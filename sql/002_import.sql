begin;
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
 insert into public.source_imports(prefecture_code,prefecture,bureau,filename,source_url,sha256,archive_sha256,parser_version,reference_date,file_created_date,retrieved_at,status,record_count)
 values((p_source->>'prefecture_code')::integer,p_source->>'prefecture',p_source->>'bureau',p_source->>'filename',p_source->>'source_url',p_source->>'sha256',p_source->>'archive_sha256',p_source->>'parser_version',
 (p_source->>'reference_date')::date,(p_source->>'file_created_date')::date,(p_source->>'retrieved_at')::timestamptz,'success',jsonb_array_length(p_records)) returning * into s;
 for item in select value from jsonb_array_elements(p_records) loop
  if (item->>'prefecture_code')::integer<>s.prefecture_code or (item->>'reference_date')::date<>s.reference_date
  then raise exception 'snapshot region/date mismatch'; end if;
  select * into old from public.medical_institutions where code=item->>'code';
  new_id:=coalesce(old.id,gen_random_uuid());
  newrow:=jsonb_populate_record(null::public.medical_institutions,item||jsonb_build_object('id',new_id,'source_import_id',s.id,'updated_at',now()));
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
   insert into public.institution_beds values(new_id,s.id,k,(item->>(k||'_beds'))::integer);
  end loop;
  event:=null;
  if old.id is null then
   -- First observation is baseline; new code alone does NOT prove a new designation/opening.
   event:=case when had_prior and nullif(item->>'change_reason','') is not null
     and (item->>'designation_date')::date>coalesce((select max(reference_date) from public.source_imports where prefecture_code=s.prefecture_code and status='success' and id<>s.id),'0001-01-01')
     and (item->>'designation_date')::date<=s.reference_date
     then 'new_designation' else 'baseline' end;
  elsif (to_jsonb(old)-array['reference_date','file_created_date','retrieved_at','source_import_id','source_file','raw','updated_at'])
       is distinct from (to_jsonb(newrow)-array['reference_date','file_created_date','retrieved_at','source_import_id','source_file','raw','updated_at']) then
   event:=case when exists(select 1 from public.source_imports prev where prev.id=old.source_import_id and prev.sha256=s.sha256 and prev.parser_version<>s.parser_version)
    then 'reclassified' when newrow.status in ('廃止','辞退','取消') and old.status not in ('廃止','辞退','取消') then 'closure_confirmed' else 'changed' end;
  end if;
  if event is not null then
   insert into public.institution_changes(institution_id,source_import_id,event_type,event_date,reason,before_data,after_data,opening_classification)
   values(new_id,s.id,event,s.reference_date,newrow.change_reason,case when old.id is not null then to_jsonb(old) end,to_jsonb(newrow),newrow.opening_classification);
  end if;
  imported:=imported+1;
 end loop;
 -- Missing is only a candidate. Keep last good master, never silently close/delete it.
 insert into public.institution_changes(institution_id,source_import_id,event_type,event_date,reason,before_data)
 select id,s.id,'missing_candidate',s.reference_date,'公表一覧から消失。廃止の裏付け資料待ち',to_jsonb(m)
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
 update public.institution_changes set opening_classification=p_classification where institution_id=p_new and event_type='new_designation';
 if p_old is not null then
  insert into public.institution_links(old_institution_id,new_institution_id,relation,evidence,confirmed_by)
  values(p_old,p_new,p_classification,p_evidence,auth.uid());
 end if;
 insert into public.access_logs(user_id,action,details) values(auth.uid(),'review_link',jsonb_build_object('old',p_old,'new',p_new,'classification',p_classification,'evidence',p_evidence));
end $$;
revoke all on function public.review_link(uuid,uuid,text,text) from public;
grant execute on function public.review_link(uuid,uuid,text,text) to authenticated;
commit;
