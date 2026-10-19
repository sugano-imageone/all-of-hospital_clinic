-- Additive, versioned bureau snapshots. Existing facility and membership rows are untouched.
begin;
create table public.notification_snapshots (
 id uuid primary key default gen_random_uuid(), bureau text not null,
 reference_dates jsonb not null, source jsonb not null,
 expected_filings integer not null check(expected_filings>0),
 expected_facilities integer not null check(expected_facilities>0),
 expected_prefectures integer[] not null,
 state text not null default 'staging' check(state in ('staging','validated')),
 created_at timestamptz not null default now()
);
create table public.notification_current (
 bureau text primary key, snapshot_id uuid not null references public.notification_snapshots(id),
 adopted_at timestamptz not null default now()
);
create table public.notification_facilities (
 snapshot_id uuid not null references public.notification_snapshots(id) on delete cascade,
 code text not null check(code ~ '^[0-9]{2}1[0-9]{7}$'),
 prefecture_code integer not null check(prefecture_code between 1 and 47),
 reference_date date not null, primary key(snapshot_id,code),
 check(left(code,2)::integer=prefecture_code)
);
create table public.notification_filings (
 snapshot_id uuid not null references public.notification_snapshots(id) on delete cascade,
 code text not null, item_key text not null check(length(item_key)>0),
 official_item_name text not null check(length(official_item_name)>0),
 date_basis text not null check(date_basis in ('receipt_date','calculation_start')),
 receipt_date date, calculation_start date, payload jsonb not null,
 primary key(snapshot_id,code,item_key),
 foreign key(snapshot_id,code) references public.notification_facilities(snapshot_id,code)
);
create index notification_item_code on public.notification_filings(snapshot_id,item_key,code);
create table public.notification_items (
 snapshot_id uuid not null references public.notification_snapshots(id) on delete cascade,
 item_key text not null, official_item_name text not null, primary key(snapshot_id,item_key)
);
create table public.notification_jobs (
 month date not null, bureau text not null, status text not null check(status in ('failed','success')),
 attempted_at timestamptz not null default now(), details jsonb not null default '{}',primary key(month,bureau)
);
-- Restrict direct reads as well as functions. Current pointer never exposes staging rows.
do $$declare t text;begin
 foreach t in array array['notification_snapshots','notification_current','notification_facilities','notification_filings','notification_items','notification_jobs'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('revoke all on public.%I from public,anon,authenticated',t);
  execute format('grant all on public.%I to service_role',t);
 end loop;
 foreach t in array array['notification_current','notification_facilities','notification_filings','notification_items'] loop
  execute format('grant select on public.%I to authenticated',t);
 end loop;
end $$;
create policy notification_current_read on public.notification_current for select to authenticated using((select public.is_approved()));
create policy notification_facilities_read on public.notification_facilities for select to authenticated using((select public.is_approved()) and snapshot_id in(select snapshot_id from public.notification_current));
create policy notification_filings_read on public.notification_filings for select to authenticated using((select public.is_approved()) and snapshot_id in(select snapshot_id from public.notification_current));
create policy notification_items_read on public.notification_items for select to authenticated using((select public.is_approved()) and snapshot_id in(select snapshot_id from public.notification_current));
grant select on public.notification_jobs to authenticated;
create policy notification_jobs_master on public.notification_jobs for select to authenticated using((select public.is_master()));

create function public.notification_search(p_item text,p_status text)
returns setof public.medical_institutions language sql stable security invoker set search_path='' as $$
 select m.* from public.medical_institutions m
 where p_status in ('filed','unfiled') and m.status='現存'
 and exists(
  select 1 from public.notification_current c
  join public.notification_facilities f on f.snapshot_id=c.snapshot_id and f.code=m.code
  join public.notification_items i on i.snapshot_id=c.snapshot_id and i.item_key=regexp_replace(normalize(p_item,NFKC),'\s','','g')
  where c.bureau=m.bureau
  and (exists(select 1 from public.notification_filings n where n.snapshot_id=c.snapshot_id and n.code=m.code and n.item_key=i.item_key))=(p_status='filed')
 )
$$;
revoke all on function public.notification_search(text,text) from public,anon;
grant execute on function public.notification_search(text,text) to authenticated;

create function public.notification_metadata() returns jsonb language plpgsql stable security definer set search_path='' as $$
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 return jsonb_build_object(
 'items',(select coalesce(jsonb_agg(name order by name),'[]') from (select min(i.official_item_name) name from public.notification_items i join public.notification_current c on c.snapshot_id=i.snapshot_id group by i.item_key) x),
 'bureaus',(select coalesce(jsonb_agg(jsonb_build_object('bureau',c.bureau,'reference_dates',s.reference_dates) order by c.bureau),'[]') from public.notification_current c join public.notification_snapshots s on s.id=c.snapshot_id)
 );
end $$;
revoke all on function public.notification_metadata() from public,anon;
grant execute on function public.notification_metadata() to authenticated;

create function public.begin_notification_snapshot(p_metadata jsonb) returns uuid language plpgsql security definer set search_path='' as $$
declare v_id uuid;v_expected integer[];begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 v_expected:=case p_metadata->>'bureau'
 when '北海道厚生局' then array[1] when '東北厚生局' then array[2,3,4,5,6,7]
 when '関東信越厚生局' then array[8,9,10,11,12,13,14,15,19,20]
 when '東海北陸厚生局' then array[16,17,21,22,23,24]
 when '近畿厚生局' then array[18,25,26,27,28,29,30]
 when '中国四国厚生局' then array[31,32,33,34,35] when '四国厚生支局' then array[36,37,38,39]
 when '九州厚生局' then array[40,41,42,43,44,45,46,47] end;
 if v_expected is null or jsonb_typeof(p_metadata->'reference_dates')<>'object' then raise exception 'invalid bureau metadata'; end if;
 if (select array_agg(k::integer order by k::integer) from jsonb_object_keys(p_metadata->'reference_dates') k) is distinct from v_expected then raise exception 'incomplete reference dates'; end if;
 if exists(select 1 from jsonb_each_text(p_metadata->'reference_dates') d where d.value::date>current_date) then raise exception 'future reference date'; end if;
 insert into public.notification_snapshots(bureau,reference_dates,source,expected_filings,expected_facilities,expected_prefectures)
 values(p_metadata->>'bureau',p_metadata->'reference_dates',p_metadata->'source',(p_metadata->>'filings')::integer,(p_metadata->>'facilities')::integer,v_expected) returning id into v_id;
 return v_id;
end $$;
create function public.upload_notification_batch(p_snapshot uuid,p_facilities jsonb default '[]',p_filings jsonb default '[]') returns void language plpgsql security definer set search_path='' as $$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 perform 1 from public.notification_snapshots where id=p_snapshot and state='staging' for update;
 if not found then raise exception 'staging snapshot required'; end if;
 if jsonb_array_length(p_facilities)>5000 or jsonb_array_length(p_filings)>2000 then raise exception 'batch too large'; end if;
 insert into public.notification_facilities select p_snapshot,x.code,x.prefecture_code,x.reference_date
 from jsonb_to_recordset(p_facilities) x(code text,prefecture_code integer,reference_date date)
 on conflict(snapshot_id,code) do update set reference_date=excluded.reference_date;
 if exists(select 1 from jsonb_array_elements(p_filings) x where (x->'payload'->>'latest_selection_resolved')::boolean is distinct from true) then raise exception 'unresolved latest filing'; end if;
 insert into public.notification_filings select p_snapshot,x.code,x.item_key,x.official_item_name,x.date_basis,x.receipt_date,x.calculation_start,x.payload
 from jsonb_to_recordset(p_filings) x(code text,item_key text,official_item_name text,date_basis text,receipt_date date,calculation_start date,payload jsonb)
 on conflict(snapshot_id,code,item_key) do update set official_item_name=excluded.official_item_name,date_basis=excluded.date_basis,receipt_date=excluded.receipt_date,calculation_start=excluded.calculation_start,payload=excluded.payload;
end $$;
create function public.finish_notification_snapshot(p_snapshot uuid) returns jsonb language plpgsql security definer set search_path='' as $$
declare s public.notification_snapshots;v_old uuid;v_count integer;begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into s from public.notification_snapshots where id=p_snapshot for update;
 if not found then raise exception 'snapshot missing'; end if;
 perform pg_advisory_xact_lock(hashtext('notifications:'||s.bureau));
 select snapshot_id into v_old from public.notification_current where bureau=s.bureau for update;
 if v_old=p_snapshot then return jsonb_build_object('status','unchanged'); end if;
 if (select count(*) from public.notification_filings where snapshot_id=p_snapshot)<>s.expected_filings or (select count(*) from public.notification_facilities where snapshot_id=p_snapshot)<>s.expected_facilities then raise exception 'incomplete snapshot'; end if;
 if (select array_agg(distinct prefecture_code order by prefecture_code) from public.notification_facilities where snapshot_id=p_snapshot) is distinct from s.expected_prefectures then raise exception 'prefecture mismatch'; end if;
 if exists(select 1 from public.notification_facilities f where f.snapshot_id=p_snapshot and f.reference_date is distinct from (s.reference_dates->>f.prefecture_code::text)::date) then raise exception 'reference date mismatch'; end if;
 if exists(select 1 from public.notification_snapshots old,jsonb_each_text(s.reference_dates) d where old.id=v_old and d.value::date<(old.reference_dates->>d.key)::date) then raise exception 'older reference date'; end if;
 if exists(select 1 from public.notification_snapshots old where old.id=v_old and (s.expected_filings<old.expected_filings*0.8 or s.expected_facilities<old.expected_facilities*0.8)) then raise exception 'unexpected count drop'; end if;
 delete from public.notification_items where snapshot_id=p_snapshot;
 insert into public.notification_items select p_snapshot,item_key,min(official_item_name) from public.notification_filings where snapshot_id=p_snapshot group by item_key;
 update public.notification_snapshots set state='validated' where id=p_snapshot;
 insert into public.notification_current values(s.bureau,p_snapshot,now()) on conflict(bureau) do update set snapshot_id=excluded.snapshot_id,adopted_at=excluded.adopted_at;
 return jsonb_build_object('status','success','filings',s.expected_filings,'facilities',s.expected_facilities,'reference_dates',s.reference_dates);
end $$;
revoke all on function public.begin_notification_snapshot(jsonb),public.upload_notification_batch(uuid,jsonb,jsonb),public.finish_notification_snapshot(uuid) from public,anon,authenticated;
grant execute on function public.begin_notification_snapshot(jsonb),public.upload_notification_batch(uuid,jsonb,jsonb),public.finish_notification_snapshot(uuid) to service_role;
notify pgrst,'reload schema';
commit;
-- Durable retry state is independent of short-lived Actions artifacts.
begin;
create function public.notification_monthly_targets() returns jsonb language plpgsql security definer set search_path='' as $$
declare v_today date:=(now() at time zone 'Asia/Tokyo')::date;v_month date;v_bureau text;begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 v_month:=date_trunc('month',v_today)::date;
 if extract(day from v_today) not in (20,25) then raise exception 'JST 20th or 25th required'; end if;
 if extract(day from v_today)=20 then
  foreach v_bureau in array array['北海道厚生局','東北厚生局','関東信越厚生局','東海北陸厚生局','近畿厚生局','中国四国厚生局','四国厚生支局','九州厚生局'] loop
   insert into public.notification_jobs(month,bureau,status,details) values(v_month,v_bureau,'failed','{"reason":"pending"}') on conflict do nothing;
  end loop;
 elsif (select count(*) from public.notification_jobs where month=v_month)<>8 then raise exception '20th state missing';
 end if;
 return (select coalesce(jsonb_agg(bureau order by bureau),'[]') from public.notification_jobs where month=v_month and status='failed');
end $$;
create function public.notification_job_result(p_bureau text,p_success boolean,p_details jsonb) returns void language plpgsql security definer set search_path='' as $$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 update public.notification_jobs set status=case when p_success then 'success' else 'failed' end,attempted_at=now(),details=p_details
 where month=date_trunc('month',now() at time zone 'Asia/Tokyo')::date and bureau=p_bureau;
 if not found then raise exception 'monthly job missing'; end if;
end $$;
revoke all on function public.notification_monthly_targets(),public.notification_job_result(text,boolean,jsonb) from public,anon,authenticated;
grant execute on function public.notification_monthly_targets(),public.notification_job_result(text,boolean,jsonb) to service_role;
notify pgrst,'reload schema';
commit;
