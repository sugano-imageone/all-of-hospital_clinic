-- Includes corrected 012 bulk import and private bounded upload staging.
-- Apply after 010. Replace row-by-row SQL with batched SQL while retaining
-- a single transaction, old/new evidence, manual adjudication, and all RLS.
begin;
do $$ begin
 if public.directory_schema_version() <> 'existing-v1-to-directory-v2' then
  raise exception 'Apply 010_upgrade_existing.sql first';
 end if;
end $$;
-- Avoid rewriting indexed master rows when their derived bed values already agree.
create or replace function public.directory_sync_beds() returns trigger language plpgsql security definer set search_path='' as $$
begin
 -- Bulk import normalizes the same derived values once before inserting masters.
 if tg_op<>'DELETE' and current_setting('directory.bulk_beds_normalized',true)='on' then
  return new;
 end if;
 if tg_op='DELETE' then
  update public.medical_institutions set total_beds=null,general_beds=null,medical_beds=null,psychiatric_beds=null,
   tuberculosis_beds=null,infectious_beds=null,clinic_beds=null,psychiatric_class='不明' where id=old.institution_id;
  return old;
 end if;
 update public.medical_institutions m set total_beds=new.total_beds,general_beds=new.general_beds,
  medical_beds=new.long_term_care_beds,psychiatric_beds=new.psychiatric_beds,tuberculosis_beds=new.tuberculosis_beds,
  infectious_beds=new.infectious_disease_beds,
  clinic_beds=case when m.kind='診療所' and new.total_beds between 0 and 19 then case when new.total_beds=0 then '無床' else '有床' end end,
  psychiatric_class=case when m.kind is distinct from '病院' or new.total_beds is null or new.total_beds=0 or new.psychiatric_beds is null or new.psychiatric_beds>new.total_beds then '不明'
   when new.psychiatric_beds=0 then '精神病床なし' when new.psychiatric_beds=new.total_beds then '精神病床のみ'
   when new.psychiatric_beds::numeric/new.total_beds>=0.8 then '精神病床80%以上' else '混合型' end,
  needs_review=m.needs_review or coalesce((new.total_beds is not null and ((m.kind='病院' and new.total_beds<20) or (m.kind='診療所' and new.total_beds>=20))),false)
   or coalesce((new.psychiatric_beds>new.total_beds),false),
  status=case when m.status in ('現存','休止') and new.total_beds is not null and
    ((m.kind='病院' and new.total_beds<20) or (m.kind='診療所' and new.total_beds>=20) or new.psychiatric_beds>new.total_beds)
    then '確認中' else m.status end
 where m.id=new.institution_id
 and row(m.total_beds,m.general_beds,m.medical_beds,m.psychiatric_beds,m.tuberculosis_beds,m.infectious_beds,m.clinic_beds,m.psychiatric_class,m.needs_review,m.status)
 is distinct from row(new.total_beds,
  new.general_beds,
  new.long_term_care_beds,
  new.psychiatric_beds,
  new.tuberculosis_beds,
  new.infectious_disease_beds,
  case when m.kind='診療所' and new.total_beds between 0 and 19 then case when new.total_beds=0 then '無床' else '有床' end end,
  case when m.kind is distinct from '病院' or new.total_beds is null or new.total_beds=0 or new.psychiatric_beds is null or new.psychiatric_beds>new.total_beds then '不明'
   when new.psychiatric_beds=0 then '精神病床なし' when new.psychiatric_beds=new.total_beds then '精神病床のみ'
   when new.psychiatric_beds::numeric/new.total_beds>=0.8 then '精神病床80%以上' else '混合型' end,
  m.needs_review or coalesce((new.total_beds is not null and ((m.kind='病院' and new.total_beds<20) or (m.kind='診療所' and new.total_beds>=20))),false)
   or coalesce((new.psychiatric_beds>new.total_beds),false),
  case when m.status in ('現存','休止') and new.total_beds is not null and
    ((m.kind='病院' and new.total_beds<20) or (m.kind='診療所' and new.total_beds>=20) or new.psychiatric_beds>new.total_beds)
    then '確認中' else m.status end);
 return new;
end $$;
revoke all on function public.directory_sync_beds() from public;

create or replace function public.import_snapshot(p_source jsonb,p_records jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare
 s public.source_imports%rowtype; prior_count integer;
 had_prior boolean; imported integer:=0; previous_sync_setting text;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 perform pg_advisory_xact_lock(8914,(p_source->>'prefecture_code')::integer);
 if jsonb_array_length(p_records)=0 or jsonb_array_length(p_records)<>(p_source->>'record_count')::integer
 then raise exception 'snapshot count mismatch'; end if;
 if exists(select 1 from public.source_imports where prefecture_code=(p_source->>'prefecture_code')::integer
 and status='success' and reference_date>(p_source->>'reference_date')::date) then raise exception 'stale snapshot'; end if;
 select * into s from public.source_imports where prefecture_code=(p_source->>'prefecture_code')::integer
 and sha256=p_source->>'sha256' and parser_version=p_source->>'parser_version' and status='success';
 if found then return jsonb_build_object('status','unchanged','count',s.record_count); end if;
 select count(*) into prior_count from public.medical_institutions where prefecture_code=(p_source->>'prefecture_code')::integer and status in ('現存','休止');
 if prior_count>0 and jsonb_array_length(p_records)<prior_count*0.8 then raise exception 'count dropped over 20 percent; manual investigation required'; end if;
 had_prior:=exists(select 1 from public.source_imports where prefecture_code=(p_source->>'prefecture_code')::integer and status='success');
 insert into public.source_imports(prefecture_code,prefecture,bureau,filename,source_url,sha256,archive_sha256,parser_version,reference_date,file_created_date,retrieved_at,status,record_count,retrieved_date)
 values((p_source->>'prefecture_code')::integer,p_source->>'prefecture',p_source->>'bureau',p_source->>'filename',p_source->>'source_url',p_source->>'sha256',p_source->>'archive_sha256',p_source->>'parser_version',
 (p_source->>'reference_date')::date,(p_source->>'file_created_date')::date,(p_source->>'retrieved_at')::timestamptz,'success',jsonb_array_length(p_records),((p_source->>'retrieved_at')::timestamptz at time zone 'Asia/Tokyo')::date) returning * into s;

 -- Materialize old/new rows once. Every prefecture still commits as one snapshot.
 -- A duplicate temporary name fails safely; no untrusted temp object is reused.
 create temporary table directory_import_work (
  old_row public.medical_institutions,
  new_row public.medical_institutions
 ) on commit drop;
 insert into pg_temp.directory_import_work(old_row,new_row)
 select m, jsonb_populate_record(null::public.medical_institutions,
  v.item || jsonb_build_object('id',coalesce(m.id,gen_random_uuid()),
   'source_import_id',s.id,'updated_at',now(),'created_at',coalesce(m.created_at,now()),
   'opening_classification',coalesce(m.opening_classification,v.item->>'opening_classification'),
   'retrieved_date',((v.item->>'retrieved_at')::timestamptz at time zone 'Asia/Tokyo')::date))
 from jsonb_array_elements(p_records) as v(item)
 left join public.medical_institutions m on m.code=v.item->>'code';
 if exists(select 1 from pg_temp.directory_import_work w
  where (w.new_row).prefecture_code is distinct from s.prefecture_code
     or (w.new_row).reference_date is distinct from s.reference_date)
 then raise exception 'snapshot region/date mismatch'; end if;

 -- Apply the current-bed invariants before writing any master or event rows.
 update pg_temp.directory_import_work w set
 new_row.clinic_beds=case when (w.new_row).kind='診療所' and (w.new_row).total_beds between 0 and 19 then case when (w.new_row).total_beds=0 then '無床' else '有床' end end,
 new_row.psychiatric_class=case when (w.new_row).kind is distinct from '病院' or (w.new_row).total_beds is null or (w.new_row).total_beds=0 or (w.new_row).psychiatric_beds is null or (w.new_row).psychiatric_beds>(w.new_row).total_beds then '不明'
   when (w.new_row).psychiatric_beds=0 then '精神病床なし' when (w.new_row).psychiatric_beds=(w.new_row).total_beds then '精神病床のみ'
   when (w.new_row).psychiatric_beds::numeric/(w.new_row).total_beds>=0.8 then '精神病床80%以上' else '混合型' end,
 new_row.needs_review=(w.new_row).needs_review or coalesce(((w.new_row).total_beds is not null and (((w.new_row).kind='病院' and (w.new_row).total_beds<20) or ((w.new_row).kind='診療所' and (w.new_row).total_beds>=20))),false)
   or coalesce(((w.new_row).psychiatric_beds>(w.new_row).total_beds),false),
 new_row.status=case when (w.new_row).status in ('現存','休止') and (w.new_row).total_beds is not null and
    (((w.new_row).kind='病院' and (w.new_row).total_beds<20) or ((w.new_row).kind='診療所' and (w.new_row).total_beds>=20) or (w.new_row).psychiatric_beds>(w.new_row).total_beds)
    then '確認中' else (w.new_row).status end
 where (w.new_row).source_import_id=s.id;

 insert into public.medical_institutions select (w.new_row).* from pg_temp.directory_import_work w
 on conflict(code) do update set
   prefecture=excluded.prefecture,bureau=excluded.bureau,municipality=excluded.municipality,name=excluded.name,
   postal_code=excluded.postal_code,address=excluded.address,phone=excluded.phone,owner=excluded.owner,
   owner_type=excluded.owner_type,manager=excluded.manager,departments=excluded.departments,
   kind=excluded.kind,clinic_beds=excluded.clinic_beds,total_beds=excluded.total_beds,
   general_beds=excluded.general_beds,medical_beds=excluded.medical_beds,psychiatric_beds=excluded.psychiatric_beds,
   tuberculosis_beds=excluded.tuberculosis_beds,infectious_beds=excluded.infectious_beds,
   psychiatric_class=excluded.psychiatric_class,needs_review=excluded.needs_review,status=excluded.status,
   designation_date=excluded.designation_date,renewal_date=excluded.renewal_date,change_reason=excluded.change_reason,
   reference_date=excluded.reference_date,file_created_date=excluded.file_created_date,retrieved_at=excluded.retrieved_at,
   source_file=excluded.source_file,source_import_id=excluded.source_import_id,raw=excluded.raw,updated_at=excluded.updated_at;
 get diagnostics imported = row_count;

 insert into public.institution_bed_history(institution_id,source_import_id,bed_type,bed_count)
 select (w.new_row).id,s.id,b.bed_type,b.bed_count
 from pg_temp.directory_import_work w
 cross join lateral (values
  ('general',(w.new_row).general_beds),('medical',(w.new_row).medical_beds),
  ('psychiatric',(w.new_row).psychiatric_beds),('tuberculosis',(w.new_row).tuberculosis_beds),
  ('infectious',(w.new_row).infectious_beds)) b(bed_type,bed_count);

 previous_sync_setting:=current_setting('directory.bulk_beds_normalized',true);
 perform set_config('directory.bulk_beds_normalized','on',true);
 insert into public.institution_beds(institution_id,total_beds,general_beds,long_term_care_beds,
  psychiatric_beds,tuberculosis_beds,infectious_disease_beds,clinic_bed_type,psychiatric_category,updated_at)
 select (w.new_row).id,(w.new_row).total_beds,(w.new_row).general_beds,(w.new_row).medical_beds,
  (w.new_row).psychiatric_beds,(w.new_row).tuberculosis_beds,(w.new_row).infectious_beds,(w.new_row).clinic_beds,
  case (w.new_row).psychiatric_class when '精神病床のみ' then 'psychiatric_only'
   when '精神病床80%以上' then 'psychiatric_80_percent' when '混合型' then 'mixed' else 'unconfirmed' end,now()
 from pg_temp.directory_import_work w
 on conflict(institution_id) do update set total_beds=excluded.total_beds,general_beds=excluded.general_beds,
  long_term_care_beds=excluded.long_term_care_beds,psychiatric_beds=excluded.psychiatric_beds,
  tuberculosis_beds=excluded.tuberculosis_beds,infectious_disease_beds=excluded.infectious_disease_beds,
  clinic_bed_type=excluded.clinic_bed_type,psychiatric_category=excluded.psychiatric_category,updated_at=excluded.updated_at;

 perform set_config('directory.bulk_beds_normalized',coalesce(previous_sync_setting,'off'),true);

 with previous as (
  select coalesce(max(reference_date),'0001-01-01') as reference_date
  from public.source_imports where prefecture_code=s.prefecture_code and status='success' and id<>s.id
 ), events as (
  select w.old_row,w.new_row,
   case when (w.old_row).id is null then
    case when had_prior and nullif((w.new_row).change_reason,'') is not null
     and (w.new_row).designation_date>previous.reference_date
     and (w.new_row).designation_date<=s.reference_date then 'new_designation' else 'baseline' end
   when (to_jsonb(w.old_row)-array['reference_date','file_created_date','retrieved_at','source_import_id','source_file','raw','updated_at','retrieved_date','created_at'])
    is distinct from (to_jsonb(w.new_row)-array['reference_date','file_created_date','retrieved_at','source_import_id','source_file','raw','updated_at','retrieved_date','created_at']) then
    case when prev.sha256=s.sha256 and prev.parser_version<>s.parser_version then 'reclassified'
     when (w.new_row).status in ('廃止','辞退','取消') and (w.old_row).status not in ('廃止','辞退','取消')
      then 'closure_confirmed' else 'changed' end
   end as event
  from pg_temp.directory_import_work w cross join previous
  left join public.source_imports prev on prev.id=(w.old_row).source_import_id
 )
 insert into public.institution_changes(institution_id,source_import_id,event_type,event_date,reason,
  before_data,after_data,opening_classification,change_type,new_medical_code)
 select (e.new_row).id,s.id,e.event,s.reference_date,(e.new_row).change_reason,
  case when (e.old_row).id is not null then to_jsonb(e.old_row) end,to_jsonb(e.new_row),
  (e.new_row).opening_classification,
  case when e.event='closure_confirmed' then 'abolition'
   when e.event in ('changed','reclassified') then 'information_change' else 'undetermined' end,(e.new_row).code
 from events e where e.event is not null;
 -- Missing is only a candidate. Keep last good master, never silently close/delete it.
 insert into public.institution_changes(institution_id,source_import_id,event_type,event_date,reason,before_data,change_type,old_medical_code)
 select id,s.id,'missing_candidate',s.reference_date,'公表一覧から消失。廃止の裏付け資料待ち',to_jsonb(m),'undetermined',m.code
 from public.medical_institutions m where m.prefecture_code=s.prefecture_code and m.status in ('現存','休止')
 and m.source_import_id<>s.id;
 drop table pg_temp.directory_import_work;
 return jsonb_build_object('status','success','count',imported,'source_import_id',s.id,'strategy','set-based-v1');
end $$;
revoke all on function public.import_snapshot(jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.import_snapshot(jsonb,jsonb) to service_role;

insert into public.directory_migrations(version) values('existing-v1-import-bulk-v1') on conflict do nothing;
notify pgrst, 'reload schema';



-- Upload staging is private to the service role. No master changes before finalize.
create table if not exists public.snapshot_uploads (
 id uuid primary key default gen_random_uuid(),
 prefecture_code integer not null,
 sha256 text not null, parser_version text not null, payload_sha256 text not null,
 source jsonb not null, expected_records integer not null check(expected_records>0),
 expected_batches integer not null check(expected_batches>0),
 created_at timestamptz not null default now(),
 unique(prefecture_code,sha256,parser_version,payload_sha256)
);
create table if not exists public.snapshot_upload_batches (
 upload_id uuid not null references public.snapshot_uploads(id) on delete cascade,
 batch_number integer not null check(batch_number>=0),
 records jsonb not null check(jsonb_typeof(records)='array'),
 primary key(upload_id,batch_number)
);
alter table public.snapshot_uploads enable row level security;
alter table public.snapshot_upload_batches enable row level security;
revoke all on public.snapshot_uploads,public.snapshot_upload_batches from public,anon,authenticated;
grant all on public.snapshot_uploads,public.snapshot_upload_batches to service_role;

create or replace function public.begin_snapshot_upload(p_source jsonb,p_payload_sha256 text,p_batches integer) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u public.snapshot_uploads%rowtype; existing_count integer; n integer;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 n:=(p_source->>'record_count')::integer;
 if n is null or n<1 or n>200000 or p_batches is null or p_batches<1 or p_batches>n
 or p_payload_sha256 is null or p_payload_sha256 !~ '^[0-9a-f]{64}$'
 then raise exception 'invalid upload metadata'; end if;
 select record_count into existing_count from public.source_imports
 where prefecture_code=(p_source->>'prefecture_code')::integer and sha256=p_source->>'sha256'
 and parser_version=p_source->>'parser_version' and status='success';
 if found then return jsonb_build_object('status','unchanged','count',existing_count); end if;
 delete from public.snapshot_uploads where created_at<now()-interval '1 day';
 insert into public.snapshot_uploads(prefecture_code,sha256,parser_version,payload_sha256,source,expected_records,expected_batches)
 values((p_source->>'prefecture_code')::integer,p_source->>'sha256',p_source->>'parser_version',p_payload_sha256,p_source,n,p_batches)
 on conflict(prefecture_code,sha256,parser_version,payload_sha256) do nothing;
 select * into u from public.snapshot_uploads where prefecture_code=(p_source->>'prefecture_code')::integer
 and sha256=p_source->>'sha256' and parser_version=p_source->>'parser_version' and payload_sha256=p_payload_sha256;
 if u.expected_records<>n or u.expected_batches<>p_batches then raise exception 'upload manifest conflict'; end if;
 return jsonb_build_object('status','uploading','upload_id',u.id,
  'uploaded_batches',coalesce((select jsonb_agg(batch_number order by batch_number) from public.snapshot_upload_batches where upload_id=u.id),'[]'::jsonb));
end $$;

create or replace function public.upload_snapshot_batch(p_upload_id uuid,p_batch_number integer,p_records jsonb) returns void
language plpgsql security definer set search_path='' as $$
declare u public.snapshot_uploads%rowtype; previous jsonb;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into u from public.snapshot_uploads where id=p_upload_id for update;
 if not found then raise exception 'upload not found'; end if;
 if p_batch_number is null or p_batch_number<0 or p_batch_number>=u.expected_batches
 or jsonb_typeof(p_records) is distinct from 'array' or jsonb_array_length(p_records)<1
 or jsonb_array_length(p_records)>500 then raise exception 'invalid batch'; end if;
 if exists(select 1 from jsonb_array_elements(p_records) r
 where (r->>'prefecture_code')::integer is distinct from u.prefecture_code
 or r->>'reference_date' is distinct from u.source->>'reference_date'
 or r->>'source_file' is distinct from u.source->>'filename') then raise exception 'batch region/date/source mismatch'; end if;
 select records into previous from public.snapshot_upload_batches where upload_id=u.id and batch_number=p_batch_number;
 if found and previous is distinct from p_records then raise exception 'conflicting batch'; end if;
 insert into public.snapshot_upload_batches(upload_id,batch_number,records) values(u.id,p_batch_number,p_records)
 on conflict(upload_id,batch_number) do nothing;
end $$;

create or replace function public.finish_snapshot_upload(p_upload_id uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u public.snapshot_uploads%rowtype; data jsonb; result jsonb; batches integer; n bigint;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into u from public.snapshot_uploads where id=p_upload_id for update;
 if not found then raise exception 'upload not found'; end if;
 select count(*),sum(jsonb_array_length(records)) into batches,n
 from public.snapshot_upload_batches where upload_id=u.id;
 if batches<>u.expected_batches or n is distinct from u.expected_records::bigint then raise exception 'incomplete upload'; end if;
 select jsonb_agg(r.item order by b.batch_number,r.ordinality) into data
 from public.snapshot_upload_batches b cross join lateral jsonb_array_elements(b.records) with ordinality r(item,ordinality)
 where b.upload_id=u.id;
 -- Assembly stays inside PostgreSQL; the final master update is one transaction.
 result:=public.import_snapshot(u.source,data);
 delete from public.snapshot_uploads where id=u.id;
 return result||jsonb_build_object('transport','staged-v1');
end $$;
revoke all on function public.begin_snapshot_upload(jsonb,text,integer),public.upload_snapshot_batch(uuid,integer,jsonb),public.finish_snapshot_upload(uuid) from public,anon,authenticated;
grant execute on function public.begin_snapshot_upload(jsonb,text,integer),public.upload_snapshot_batch(uuid,integer,jsonb),public.finish_snapshot_upload(uuid) to service_role;
insert into public.directory_migrations(version) values('existing-v1-staged-upload-v1') on conflict do nothing;

notify pgrst,'reload schema';
commit;
select 'existing-v1-staged-upload-v1' as migration;
