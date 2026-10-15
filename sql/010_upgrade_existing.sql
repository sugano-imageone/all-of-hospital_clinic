-- Upgrade the inspected existing Supabase schema (2026-10-15).
-- Run the WHOLE file in SQL Editor. Transactional; no table deletion/recreation.
-- Preserves UUIDs, bigint identity IDs, all rows and current bed table.
-- Do not run 001_initial.sql / 002_import.sql on the existing project.
begin;
select pg_advisory_xact_lock(8914,0);
create table if not exists public.directory_migrations(version text primary key, applied_at timestamptz not null default now());
alter table public.directory_migrations enable row level security;
revoke all on public.directory_migrations from public,anon,authenticated;
do $$ begin
 if exists(select 1 from public.directory_migrations where version='existing-v1-to-directory-v2') then
  raise exception 'Migration already applied; no changes made';
 end if;
 if not exists(select 1 from information_schema.columns where table_schema='public' and table_name='medical_institutions' and column_name='insurance_medical_code')
 or not exists(select 1 from information_schema.columns where table_schema='public' and table_name='app_members' and column_name='enabled')
 or not exists(select 1 from information_schema.columns where table_schema='public' and table_name='source_imports' and column_name='id' and data_type='bigint') then
  raise exception 'Unexpected existing schema; inspect before migration';
 end if;
 if exists(select 1 from pg_catalog.pg_trigger t join pg_catalog.pg_class c on c.oid=t.tgrelid
 join pg_catalog.pg_namespace n on n.oid=c.relnamespace where n.nspname='public'
 and c.relname in ('medical_institutions','institution_beds','source_imports','institution_changes','app_members') and not t.tgisinternal) then
  raise exception 'Unexpected existing trigger; inspect before migration';
 end if;
 if exists(select 1 from pg_catalog.pg_trigger t join pg_catalog.pg_class c on c.oid=t.tgrelid
 join pg_catalog.pg_namespace n on n.oid=c.relnamespace join pg_catalog.pg_proc p on p.oid=t.tgfoid
 join pg_catalog.pg_namespace fn on fn.oid=p.pronamespace where n.nspname='auth' and c.relname='users'
 and fn.nspname='public' and not t.tgisinternal) then
  raise exception 'Existing custom Auth trigger requires review before renaming membership columns';
 end if;
end $$;

alter table public.app_members rename column enabled to approved;
alter table public.app_members drop constraint app_members_role_check;
update public.app_members set role='user' where role='viewer';
alter table public.app_members add constraint app_members_role_check check(role in ('admin','user'));
alter table public.app_members alter column role set default 'user';
alter table public.app_members alter column approved set default false;

alter table public.source_imports rename column publication_reference_date to reference_date;
alter table public.source_imports rename column import_status to status;
alter table public.source_imports rename column error_message to error_summary;
alter table public.source_imports drop constraint source_imports_import_status_check;
update public.source_imports set status='success' where status='completed';
alter table public.source_imports add constraint source_imports_status_check check(status in ('pending','success','failed'));
alter table public.source_imports add column prefecture_code integer check(prefecture_code between 1 and 47),
 add column source_url text, add column archive_sha256 text,
 add column parser_version text not null default 'legacy-v1', add column file_created_date date,
 add column retrieved_at timestamptz;
-- Existing date-only retrieval fields remain intact; never fabricate an exact timestamp.
create unique index directory_source_identity on public.source_imports(prefecture_code,sha256,parser_version)
 where prefecture_code is not null and sha256 is not null;

alter table public.medical_institutions rename column insurance_medical_code to code;
alter table public.medical_institutions rename column institution_name to name;
alter table public.medical_institutions rename column telephone to phone;
alter table public.medical_institutions rename column operator_name to owner;
alter table public.medical_institutions rename column operator_type to owner_type;
alter table public.medical_institutions rename column administrator_name to manager;
alter table public.medical_institutions rename column institution_type to kind;
alter table public.medical_institutions rename column publication_reference_date to reference_date;
alter table public.medical_institutions drop constraint medical_institutions_institution_type_check;
alter table public.medical_institutions drop constraint medical_institutions_status_check;
update public.medical_institutions set kind=case kind when 'hospital' then '病院' when 'clinic' then '診療所' else kind end,
 status=case status when 'active' then '現存' when 'abolished' then '廃止' when 'surrendered' then '辞退' when 'cancelled' then '取消' when 'review' then '確認中' else status end;
alter table public.medical_institutions add constraint medical_institutions_kind_check check(kind in ('病院','診療所')),
 add constraint medical_institutions_status_check check(status in ('現存','休止','廃止','辞退','取消','確認中'));
alter table public.medical_institutions alter column status set default '現存';
alter table public.medical_institutions alter column departments type jsonb using
 case when departments is null then '[]'::jsonb else jsonb_build_array(departments) end;
alter table public.medical_institutions alter column departments set default '[]'::jsonb;
alter table public.medical_institutions add column prefecture_code integer,
 add column total_beds integer check(total_beds>=0), add column general_beds integer check(general_beds>=0),
 add column medical_beds integer check(medical_beds>=0), add column psychiatric_beds integer check(psychiatric_beds>=0),
 add column tuberculosis_beds integer check(tuberculosis_beds>=0), add column infectious_beds integer check(infectious_beds>=0),
 add column clinic_beds text check(clinic_beds in ('有床','無床')),
 add column psychiatric_class text not null default '不明', add column needs_review boolean not null default false,
 add column renewal_date date, add column change_reason text,
 add column opening_classification text not null default '判定保留' check(opening_classification in ('純新規開業','法人化に伴う新規指定','事業承継等','その他の新規指定','判定保留')),
 add column file_created_date date, add column retrieved_at timestamptz,
 add column source_file text, add column raw jsonb not null default '{}';
update public.medical_institutions set prefecture_code=left(code,2)::integer;
alter table public.medical_institutions alter column prefecture_code set not null;
alter table public.medical_institutions add constraint directory_prefecture_valid check(prefecture_code between 1 and 47 and left(code,2)::integer=prefecture_code);
create index directory_location on public.medical_institutions(prefecture_code,kind,status);
create index directory_total_beds on public.medical_institutions(total_beds);

-- Retain original bed table (one current row per institution) and add history.
create table public.institution_bed_history (
 institution_id uuid not null references public.medical_institutions(id),
 source_import_id bigint not null references public.source_imports(id),
 bed_type text not null check(bed_type in ('general','medical','psychiatric','tuberculosis','infectious')),
 bed_count integer check(bed_count>=0), primary key(institution_id,source_import_id,bed_type)
);
create function public.directory_sync_beds() returns trigger language plpgsql security definer set search_path='' as $$
begin
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
 where m.id=new.institution_id;
 return new;
end $$;
revoke all on function public.directory_sync_beds() from public;
create trigger directory_sync_beds after insert or update or delete on public.institution_beds
 for each row execute function public.directory_sync_beds();
-- Exercise the trigger for existing current rows without changing their values.
update public.institution_beds set updated_at=updated_at;

-- Retain all original change columns, CHECKs, bigint IDs and manually reviewed evidence.
alter table public.institution_changes add column institution_id uuid references public.medical_institutions(id),
 add column event_type text check(event_type in ('baseline','new_designation','changed','missing_candidate','closure_confirmed','reclassified','legacy_history')),
 add column event_date date, add column opening_classification text not null default '判定保留',
 add column reason text, add column before_data jsonb, add column after_data jsonb;
update public.institution_changes set event_date=effective_date,
 event_type=case when review_status<>'confirmed' then 'legacy_history'
  when change_type in ('pure_new','incorporation','succession','other_designation') then 'new_designation'
  when change_type='abolition' then 'closure_confirmed' when change_type in ('bed_change','information_change') then 'changed' else 'legacy_history' end,
 opening_classification=case when review_status<>'confirmed' then '判定保留' when change_type='pure_new' then '純新規開業'
  when change_type='incorporation' then '法人化に伴う新規指定' when change_type='succession' then '事業承継等'
  when change_type='other_designation' then 'その他の新規指定' else '判定保留' end;
update public.institution_changes c set institution_id=m.id from public.medical_institutions m
 where m.code=coalesce(c.new_medical_code,c.old_medical_code);
create unique index directory_change_identity on public.institution_changes(institution_id,source_import_id,event_type);

create table public.institution_links (
 old_institution_id uuid not null references public.medical_institutions(id),
 new_institution_id uuid not null references public.medical_institutions(id),
 relation text not null, evidence text not null check(length(evidence)>=10),
 confirmed_by uuid not null references auth.users(id), created_at timestamptz not null default now(),
 primary key(old_institution_id,new_institution_id),check(old_institution_id<>new_institution_id)
);
create table public.access_logs (
 id bigint generated always as identity primary key, user_id uuid not null references auth.users(id),
 action text not null, details jsonb not null default '{}',created_at timestamptz not null default now()
);

-- SECURITY DEFINER helpers avoid recursive RLS on app_members.
create function public.is_approved() returns boolean language sql stable security definer set search_path=''
as $$ select exists(select 1 from public.app_members where user_id=auth.uid() and approved) $$;
create function public.is_admin() returns boolean language sql stable security definer set search_path=''
as $$ select exists(select 1 from public.app_members where user_id=auth.uid() and approved and role='admin') $$;
revoke all on function public.is_approved(),public.is_admin() from public;
grant execute on function public.is_approved(),public.is_admin() to authenticated;
create policy directory_admin_members on public.app_members for select to authenticated using((select public.is_admin()));
-- Membership changes occur through a checked RPC, preventing self-promotion.
create function public.set_member(p_user uuid,p_role text,p_approved boolean) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not public.is_admin() then raise exception 'admin required'; end if;
 if p_user=auth.uid() then raise exception 'self membership changes are not allowed'; end if;
 insert into public.app_members(user_id,role,approved) values(p_user,p_role,p_approved)
 on conflict(user_id) do update set role=excluded.role,approved=excluded.approved;
 insert into public.access_logs(user_id,action,details) values(auth.uid(),'member_update',jsonb_build_object('user_id',p_user,'role',p_role,'approved',p_approved));
end $$;
revoke all on function public.set_member(uuid,text,boolean) from public;
grant execute on function public.set_member(uuid,text,boolean) to authenticated;
create function public.register_member() returns trigger language plpgsql security definer set search_path='' as $$
begin insert into public.app_members(user_id) values(new.id) on conflict do nothing; return new; end $$;
revoke all on function public.register_member() from public;
create trigger register_medical_member after insert on auth.users for each row execute function public.register_member();
-- Preserve existing Auth accounts, pending approval.
insert into public.app_members(user_id) select id from auth.users on conflict do nothing;
alter table public.institution_links enable row level security;
alter table public.access_logs enable row level security;
alter table public.institution_bed_history enable row level security;
create policy directory_links_read on public.institution_links for select to authenticated using((select public.is_approved()));
create policy directory_bed_history_read on public.institution_bed_history for select to authenticated using((select public.is_approved()));
create policy directory_admin_logs on public.access_logs for select to authenticated using((select public.is_admin()));
-- Preserve the scope of original policies; translate the renamed status/approval fields.
alter policy medical_read_authorized on public.medical_institutions using
 ((select public.is_approved()) and (status in ('現存','休止') or (select public.is_admin())));
do $$ declare t text; begin
 foreach t in array array['medical_institutions','institution_beds','institution_changes','source_imports','app_members','institution_links','access_logs','institution_bed_history'] loop
  execute format('revoke all on public.%I from anon,authenticated',t);
  execute format('grant select on public.%I to authenticated',t);
  execute format('grant all on public.%I to service_role',t);
 end loop;
end $$;
create function public.record_access(p_action text,p_details jsonb default '{}') returns void
language plpgsql security definer set search_path='' as $$
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 if length(p_action)>100 or pg_column_size(p_details)>10000 then raise exception 'invalid audit event'; end if;
 insert into public.access_logs(user_id,action,details) values(auth.uid(),p_action,p_details);
end $$;
revoke all on function public.record_access(text,jsonb) from public;
grant execute on function public.record_access(text,jsonb) to authenticated;

-- One validated prefectural snapshot = one transaction; failures roll everything back.
create or replace function public.import_snapshot(p_source jsonb,p_records jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare
 s public.source_imports%rowtype; old public.medical_institutions%rowtype;
 item jsonb; newrow public.medical_institutions%rowtype; prior_count integer;
 had_prior boolean; imported integer:=0; k text; event text; new_id uuid;
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
 for item in select value from jsonb_array_elements(p_records) loop
  if (item->>'prefecture_code')::integer<>s.prefecture_code or (item->>'reference_date')::date<>s.reference_date
  then raise exception 'snapshot region/date mismatch'; end if;
  select * into old from public.medical_institutions where code=item->>'code';
  new_id:=coalesce(old.id,gen_random_uuid());
  newrow:=jsonb_populate_record(null::public.medical_institutions,item||jsonb_build_object('id',new_id,'source_import_id',s.id,'updated_at',now(),'created_at',coalesce(old.created_at,now()),'retrieved_date',((item->>'retrieved_at')::timestamptz at time zone 'Asia/Tokyo')::date));
  -- Preserve human adjudication when a new snapshot arrives.
  if old.id is not null then newrow.opening_classification:=old.opening_classification; end if;
  insert into public.medical_institutions select newrow.*
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
  foreach k in array array['general','medical','psychiatric','tuberculosis','infectious'] loop
   insert into public.institution_bed_history values(new_id,s.id,k,(item->>(k||'_beds'))::integer);
  end loop;
  insert into public.institution_beds(institution_id,total_beds,general_beds,long_term_care_beds,psychiatric_beds,tuberculosis_beds,infectious_disease_beds,clinic_bed_type,psychiatric_category,updated_at)
  values(new_id,newrow.total_beds,newrow.general_beds,newrow.medical_beds,newrow.psychiatric_beds,newrow.tuberculosis_beds,newrow.infectious_beds,newrow.clinic_beds,
   case newrow.psychiatric_class when '精神病床のみ' then 'psychiatric_only' when '精神病床80%以上' then 'psychiatric_80_percent' when '混合型' then 'mixed' else 'unconfirmed' end,now())
  on conflict(institution_id) do update set total_beds=excluded.total_beds,general_beds=excluded.general_beds,long_term_care_beds=excluded.long_term_care_beds,
   psychiatric_beds=excluded.psychiatric_beds,tuberculosis_beds=excluded.tuberculosis_beds,infectious_disease_beds=excluded.infectious_disease_beds,
   clinic_bed_type=excluded.clinic_bed_type,psychiatric_category=excluded.psychiatric_category,updated_at=excluded.updated_at;
  event:=null;
  if old.id is null then
   -- First observation is baseline; new code alone does NOT prove a new designation/opening.
   event:=case when had_prior and nullif(item->>'change_reason','') is not null
     and (item->>'designation_date')::date>coalesce((select max(reference_date) from public.source_imports where prefecture_code=s.prefecture_code and status='success' and id<>s.id),'0001-01-01')
     and (item->>'designation_date')::date<=s.reference_date
     then 'new_designation' else 'baseline' end;
  elsif (to_jsonb(old)-array['reference_date','file_created_date','retrieved_at','source_import_id','source_file','raw','updated_at','retrieved_date','created_at'])
       is distinct from (to_jsonb(newrow)-array['reference_date','file_created_date','retrieved_at','source_import_id','source_file','raw','updated_at','retrieved_date','created_at']) then
   event:=case when exists(select 1 from public.source_imports prev where prev.id=old.source_import_id and prev.sha256=s.sha256 and prev.parser_version<>s.parser_version)
    then 'reclassified' when newrow.status in ('廃止','辞退','取消') and old.status not in ('廃止','辞退','取消') then 'closure_confirmed' else 'changed' end;
  end if;
  if event is not null then
   insert into public.institution_changes(institution_id,source_import_id,event_type,event_date,reason,before_data,after_data,opening_classification,change_type,new_medical_code)
   values(new_id,s.id,event,s.reference_date,newrow.change_reason,case when old.id is not null then to_jsonb(old) end,to_jsonb(newrow),newrow.opening_classification,case when event='closure_confirmed' then 'abolition' when event in ('changed','reclassified') then 'information_change' else 'undetermined' end,newrow.code);
  end if;
  imported:=imported+1;
 end loop;
 -- Missing is only a candidate. Keep last good master, never silently close/delete it.
 insert into public.institution_changes(institution_id,source_import_id,event_type,event_date,reason,before_data,change_type,old_medical_code)
 select id,s.id,'missing_candidate',s.reference_date,'公表一覧から消失。廃止の裏付け資料待ち',to_jsonb(m),'undetermined',m.code
 from public.medical_institutions m where m.prefecture_code=s.prefecture_code and m.status in ('現存','休止')
 and m.source_import_id<>s.id;
 return jsonb_build_object('status','success','count',imported,'source_import_id',s.id);
end $$;
revoke all on function public.import_snapshot(jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.import_snapshot(jsonb,jsonb) to service_role;

create or replace function public.review_link(p_old uuid,p_new uuid,p_classification text,p_evidence text) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not public.is_admin() then raise exception 'admin required'; end if;
 if length(p_evidence)<10 then raise exception 'evidence required'; end if;
 update public.medical_institutions set opening_classification=p_classification where id=p_new;
 if not found then raise exception 'institution not found'; end if;
 update public.institution_changes set opening_classification=p_classification,review_status='confirmed',
 change_type=case p_classification when '純新規開業' then 'pure_new' when '法人化に伴う新規指定' then 'incorporation' when '事業承継等' then 'succession' when 'その他の新規指定' then 'other_designation' else 'undetermined' end,
 evidence=p_evidence where institution_id=p_new and event_type='new_designation';
 if p_old is not null then
  insert into public.institution_links(old_institution_id,new_institution_id,relation,evidence,confirmed_by)
  values(p_old,p_new,p_classification,p_evidence,auth.uid());
 end if;
 insert into public.access_logs(user_id,action,details) values(auth.uid(),'review_link',jsonb_build_object('old',p_old,'new',p_new,'classification',p_classification,'evidence',p_evidence));
end $$;
revoke all on function public.review_link(uuid,uuid,text,text) from public;
grant execute on function public.review_link(uuid,uuid,text,text) to authenticated;

create table public.update_jobs (
 month date not null, bureau text not null, status text not null check(status in ('success','failed')),
 attempted_at timestamptz not null default now(), details jsonb not null default '{}',
 primary key(month,bureau)
);
alter table public.update_jobs enable row level security;
revoke all on public.update_jobs from anon,authenticated;
grant all on public.update_jobs to service_role;
grant select on public.update_jobs to authenticated;
create policy admin_jobs on public.update_jobs for select to authenticated using(public.is_admin());

create or replace function public.source_metadata() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb;
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 select coalesce(jsonb_agg(to_jsonb(s) order by s.reference_date desc nulls last,s.id),'[]'::jsonb) into result
 from (select id,prefecture_code,prefecture,bureau,filename,source_url,sha256,archive_sha256,
 parser_version,reference_date,file_created_date,retrieved_at,status,record_count
 from public.source_imports) s;
 return result;
end $$;
revoke all on function public.source_metadata() from public,anon;
grant execute on function public.source_metadata() to authenticated;

create or replace function public.monthly_change_counts() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb;
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 with events as (
  select to_char(event_date,'YYYY-MM') as month,
   case event_type when 'new_designation' then '新規指定総数' when 'changed' then '変更数'
    when 'missing_candidate' then '廃止候補（未確定）' when 'closure_confirmed' then '廃止等確定数' end as category
  from public.institution_changes where event_type in ('new_designation','changed','missing_candidate','closure_confirmed') and event_date is not null
  union all
  select to_char(event_date,'YYYY-MM'),'純新規開業数' from public.institution_changes
  where event_type='new_designation' and opening_classification='純新規開業' and event_date is not null
 ), counts as (select month,category,count(*) as n from events group by month,category),
 months as (select month,jsonb_object_agg(category,n) as totals from counts group by month)
 select coalesce(jsonb_object_agg(month,totals),'{}'::jsonb) into result from months;
 return result;
end $$;
revoke all on function public.monthly_change_counts() from public,anon;
grant execute on function public.monthly_change_counts() to authenticated;

insert into public.directory_migrations(version) values('existing-v1-to-directory-v2');
create function public.directory_schema_version() returns text language sql stable security definer set search_path='' as $$
 select version from public.directory_migrations where version='existing-v1-to-directory-v2'
$$;
revoke all on function public.directory_schema_version() from public,anon;
grant execute on function public.directory_schema_version() to authenticated,service_role;
notify pgrst, 'reload schema';
commit;
-- Expected single-row result after success. Safe to share this result.
select 'existing-v1-to-directory-v2' as migration,
 (select count(*) from public.medical_institutions) as institutions,
 (select count(*) from public.source_imports) as imports,
 (select count(*) from public.app_members) as members;
