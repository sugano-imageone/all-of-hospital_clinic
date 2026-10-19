-- Reconcile the existing production schema; no table or account is removed.
begin;
create table if not exists public.notification_current (bureau text not null,snapshot_id uuid not null,adopted_at timestamp with time zone default now() not null);
create table if not exists public.notification_facilities (snapshot_id uuid not null,code text not null,prefecture_code integer not null,reference_date date not null);
create table if not exists public.notification_filings (snapshot_id uuid not null,code text not null,item_key text not null,official_item_name text not null,date_basis text not null,receipt_date date,calculation_start date,payload jsonb not null);
create table if not exists public.notification_items (snapshot_id uuid not null,item_key text not null,official_item_name text not null);
create table if not exists public.notification_jobs (month date not null,bureau text not null,status text not null,attempted_at timestamp with time zone default now() not null,details jsonb default '{}'::jsonb not null);
create table if not exists public.notification_snapshots (id uuid default gen_random_uuid() not null,bureau text not null,reference_dates jsonb not null,source jsonb not null,expected_filings integer not null,expected_facilities integer not null,expected_prefectures integer[] not null,state text default 'staging'::text not null,created_at timestamp with time zone default now() not null);
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_snapshots'::regclass and conname='notification_snapshots_expected_filings_check') then alter table public.notification_snapshots add constraint notification_snapshots_expected_filings_check CHECK ((expected_filings > 0)); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_snapshots'::regclass and conname='notification_snapshots_expected_facilities_check') then alter table public.notification_snapshots add constraint notification_snapshots_expected_facilities_check CHECK ((expected_facilities > 0)); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_snapshots'::regclass and conname='notification_snapshots_state_check') then alter table public.notification_snapshots add constraint notification_snapshots_state_check CHECK ((state = ANY (ARRAY['staging'::text, 'validated'::text]))); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_snapshots'::regclass and conname='notification_snapshots_pkey') then alter table public.notification_snapshots add constraint notification_snapshots_pkey PRIMARY KEY (id); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_current'::regclass and conname='notification_current_pkey') then alter table public.notification_current add constraint notification_current_pkey PRIMARY KEY (bureau); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_facilities'::regclass and conname='notification_facilities_code_check') then alter table public.notification_facilities add constraint notification_facilities_code_check CHECK ((code ~ '^[0-9]{2}1[0-9]{7}$'::text)); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_facilities'::regclass and conname='notification_facilities_prefecture_code_check') then alter table public.notification_facilities add constraint notification_facilities_prefecture_code_check CHECK (((prefecture_code >= 1) AND (prefecture_code <= 47))); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_facilities'::regclass and conname='notification_facilities_check') then alter table public.notification_facilities add constraint notification_facilities_check CHECK ((("left"(code, 2))::integer = prefecture_code)); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_facilities'::regclass and conname='notification_facilities_pkey') then alter table public.notification_facilities add constraint notification_facilities_pkey PRIMARY KEY (snapshot_id, code); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_filings'::regclass and conname='notification_filings_item_key_check') then alter table public.notification_filings add constraint notification_filings_item_key_check CHECK ((length(item_key) > 0)); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_filings'::regclass and conname='notification_filings_official_item_name_check') then alter table public.notification_filings add constraint notification_filings_official_item_name_check CHECK ((length(official_item_name) > 0)); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_filings'::regclass and conname='notification_filings_date_basis_check') then alter table public.notification_filings add constraint notification_filings_date_basis_check CHECK ((date_basis = ANY (ARRAY['receipt_date'::text, 'calculation_start'::text]))); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_filings'::regclass and conname='notification_filings_pkey') then alter table public.notification_filings add constraint notification_filings_pkey PRIMARY KEY (snapshot_id, code, item_key); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_items'::regclass and conname='notification_items_pkey') then alter table public.notification_items add constraint notification_items_pkey PRIMARY KEY (snapshot_id, item_key); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_jobs'::regclass and conname='notification_jobs_status_check') then alter table public.notification_jobs add constraint notification_jobs_status_check CHECK ((status = ANY (ARRAY['failed'::text, 'success'::text]))); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_jobs'::regclass and conname='notification_jobs_pkey') then alter table public.notification_jobs add constraint notification_jobs_pkey PRIMARY KEY (month, bureau); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_current'::regclass and conname='notification_current_snapshot_id_fkey') then alter table public.notification_current add constraint notification_current_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES notification_snapshots(id); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_facilities'::regclass and conname='notification_facilities_snapshot_id_fkey') then alter table public.notification_facilities add constraint notification_facilities_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES notification_snapshots(id) ON DELETE CASCADE; end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_filings'::regclass and conname='notification_filings_snapshot_id_fkey') then alter table public.notification_filings add constraint notification_filings_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES notification_snapshots(id) ON DELETE CASCADE; end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_filings'::regclass and conname='notification_filings_snapshot_id_code_fkey') then alter table public.notification_filings add constraint notification_filings_snapshot_id_code_fkey FOREIGN KEY (snapshot_id, code) REFERENCES notification_facilities(snapshot_id, code); end if;end $$;
do $$begin if not exists(select 1 from pg_constraint where conrelid='public.notification_items'::regclass and conname='notification_items_snapshot_id_fkey') then alter table public.notification_items add constraint notification_items_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES notification_snapshots(id) ON DELETE CASCADE; end if;end $$;
CREATE INDEX IF NOT EXISTS notification_item_code ON public.notification_filings USING btree (snapshot_id, item_key, code);
alter table public.notification_current enable row level security;
revoke all on public.notification_current from public,anon,authenticated;
grant all on public.notification_current to service_role;
alter table public.notification_facilities enable row level security;
revoke all on public.notification_facilities from public,anon,authenticated;
grant all on public.notification_facilities to service_role;
alter table public.notification_filings enable row level security;
revoke all on public.notification_filings from public,anon,authenticated;
grant all on public.notification_filings to service_role;
alter table public.notification_items enable row level security;
revoke all on public.notification_items from public,anon,authenticated;
grant all on public.notification_items to service_role;
alter table public.notification_jobs enable row level security;
revoke all on public.notification_jobs from public,anon,authenticated;
grant all on public.notification_jobs to service_role;
alter table public.notification_snapshots enable row level security;
revoke all on public.notification_snapshots from public,anon,authenticated;
grant all on public.notification_snapshots to service_role;
grant select on public.notification_current to authenticated;
drop policy if exists notification_current_read on public.notification_current;
create policy notification_current_read on public.notification_current for select to authenticated using(( SELECT is_approved() AS is_approved));
grant select on public.notification_facilities to authenticated;
drop policy if exists notification_facilities_read on public.notification_facilities;
create policy notification_facilities_read on public.notification_facilities for select to authenticated using((( SELECT is_approved() AS is_approved) AND (snapshot_id IN ( SELECT notification_current.snapshot_id
   FROM notification_current))));
grant select on public.notification_filings to authenticated;
drop policy if exists notification_filings_read on public.notification_filings;
create policy notification_filings_read on public.notification_filings for select to authenticated using((( SELECT is_approved() AS is_approved) AND (snapshot_id IN ( SELECT notification_current.snapshot_id
   FROM notification_current))));
grant select on public.notification_items to authenticated;
drop policy if exists notification_items_read on public.notification_items;
create policy notification_items_read on public.notification_items for select to authenticated using((( SELECT is_approved() AS is_approved) AND (snapshot_id IN ( SELECT notification_current.snapshot_id
   FROM notification_current))));
grant select on public.notification_jobs to authenticated;
drop policy if exists notification_jobs_master on public.notification_jobs;
create policy notification_jobs_master on public.notification_jobs for select to authenticated using(( SELECT is_master() AS is_master));
CREATE OR REPLACE FUNCTION public.begin_notification_snapshot(p_metadata jsonb)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
end $function$
;
revoke all on function public.begin_notification_snapshot(p_metadata jsonb) from public,anon,authenticated;
grant execute on function public.begin_notification_snapshot(p_metadata jsonb) to service_role;
CREATE OR REPLACE FUNCTION public.finish_notification_snapshot(p_snapshot uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
 if extract(day from now() at time zone 'Asia/Tokyo') in (20,25) then
 update public.notification_jobs set status='success',attempted_at=now(),details=jsonb_build_object('snapshot_id',p_snapshot,'filings',s.expected_filings) where month=date_trunc('month',now() at time zone 'Asia/Tokyo')::date and bureau=s.bureau;
 end if;
 return jsonb_build_object('status','success','filings',s.expected_filings,'facilities',s.expected_facilities,'reference_dates',s.reference_dates);
end $function$
;
revoke all on function public.finish_notification_snapshot(p_snapshot uuid) from public,anon,authenticated;
grant execute on function public.finish_notification_snapshot(p_snapshot uuid) to service_role;
CREATE OR REPLACE FUNCTION public.notification_job_result(p_bureau text, p_success boolean, p_details jsonb)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 update public.notification_jobs set status=case when p_success then 'success' else 'failed' end,attempted_at=now(),details=p_details
 where month=date_trunc('month',now() at time zone 'Asia/Tokyo')::date and bureau=p_bureau and status<>'success';

end $function$
;
revoke all on function public.notification_job_result(p_bureau text, p_success boolean, p_details jsonb) from public,anon,authenticated;
grant execute on function public.notification_job_result(p_bureau text, p_success boolean, p_details jsonb) to service_role;
CREATE OR REPLACE FUNCTION public.notification_metadata()
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 return jsonb_build_object(
 'items',(select coalesce(jsonb_agg(name order by name),'[]') from (select min(i.official_item_name) name from public.notification_items i join public.notification_current c on c.snapshot_id=i.snapshot_id group by i.item_key) x),
 'bureaus',(select coalesce(jsonb_agg(jsonb_build_object('bureau',c.bureau,'reference_dates',s.reference_dates) order by c.bureau),'[]') from public.notification_current c join public.notification_snapshots s on s.id=c.snapshot_id)
 );
end $function$
;
revoke all on function public.notification_metadata() from public,anon,authenticated;
grant execute on function public.notification_metadata() to authenticated;
CREATE OR REPLACE FUNCTION public.notification_monthly_targets()
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
end $function$
;
revoke all on function public.notification_monthly_targets() from public,anon,authenticated;
grant execute on function public.notification_monthly_targets() to service_role;
CREATE OR REPLACE FUNCTION public.notification_search(p_item text, p_status text)
 RETURNS SETOF medical_institutions
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
 select m.* from public.medical_institutions m
 where p_status in ('filed','unfiled') and m.status='現存'
 and exists(
  select 1 from public.notification_current c
  join public.notification_facilities f on f.snapshot_id=c.snapshot_id and f.code=m.code
  join public.notification_items i on i.snapshot_id=c.snapshot_id and i.item_key=regexp_replace(normalize(p_item,NFKC),'\s','','g')
  where c.bureau=m.bureau
  and (exists(select 1 from public.notification_filings n where n.snapshot_id=c.snapshot_id and n.code=m.code and n.item_key=i.item_key))=(p_status='filed')
 )
$function$
;
revoke all on function public.notification_search(p_item text, p_status text) from public,anon,authenticated;
grant execute on function public.notification_search(p_item text, p_status text) to authenticated;
CREATE OR REPLACE FUNCTION public.upload_notification_batch(p_snapshot uuid, p_facilities jsonb DEFAULT '[]'::jsonb, p_filings jsonb DEFAULT '[]'::jsonb)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
end $function$
;
revoke all on function public.upload_notification_batch(p_snapshot uuid, p_facilities jsonb, p_filings jsonb) from public,anon,authenticated;
grant execute on function public.upload_notification_batch(p_snapshot uuid, p_facilities jsonb, p_filings jsonb) to service_role;
create or replace function public.notification_item_names() returns jsonb language sql stable security invoker set search_path='' as $$select public.notification_metadata()->'items'$$;
revoke all on function public.notification_item_names() from public,anon;grant execute on function public.notification_item_names() to authenticated;

create or replace function public.search_reference_dates() returns jsonb language plpgsql stable security definer set search_path='' as $$
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 return jsonb_build_object('master',(select jsonb_agg(x) from (select prefecture_code,prefecture,min(reference_date) reference_date,max(reference_date) latest_reference_date from public.medical_institutions where status='現存' and bureau in ('北海道厚生局','東北厚生局','関東信越厚生局','東海北陸厚生局','近畿厚生局','中国四国厚生局','四国厚生支局','九州厚生局') group by prefecture_code,prefecture order by prefecture_code)x),'notifications',(select jsonb_object_agg(case c.bureau when '四国厚生支局' then '四国' else replace(c.bureau,'厚生局','') end,(select jsonb_object_agg(lpad(x.key,2,'0'),jsonb_build_array(x.value)) from jsonb_each(s.reference_dates) x)) from public.notification_current c join public.notification_snapshots s on s.id=c.snapshot_id));
end $$;

revoke all on function public.search_reference_dates() from public,anon;grant execute on function public.search_reference_dates() to authenticated;
insert into public.directory_migrations(version) values('notifications-v1') on conflict do nothing;notify pgrst,'reload schema';commit;