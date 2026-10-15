-- Baseline for an inspected EMPTY schema only. Existing tables deliberately cause an
-- atomic rollback: reconcile their real definitions before writing an additive migration.
-- No DROP/TRUNCATE/physical deletion of institutions. Run once in SQL Editor.
begin;
create extension if not exists pgcrypto;
create table public.app_members (
 user_id uuid primary key references auth.users(id),
 role text not null default 'user' check (role in ('admin','user')),
 approved boolean not null default false,
 created_at timestamptz not null default now()
);
create table public.source_imports (
 id uuid primary key default gen_random_uuid(),
 prefecture_code integer not null check(prefecture_code between 1 and 47),
 prefecture text not null, bureau text not null,
 filename text not null, source_url text, sha256 text not null check(sha256 ~ '^[a-f0-9]{64}$'),
 archive_sha256 text, parser_version text not null,
 reference_date date not null, file_created_date date, retrieved_at timestamptz not null,
 status text not null check(status in ('success','failed')), record_count integer not null default 0,
 error_summary text, created_at timestamptz not null default now(),
 unique(prefecture_code,sha256,parser_version)
);
create table public.medical_institutions (
 id uuid primary key default gen_random_uuid(), code text not null unique check(code ~ '^[0-9]{2}1[0-9]{7}$'),
 prefecture_code integer not null check(prefecture_code between 1 and 47), prefecture text not null,
 bureau text not null, municipality text, name text not null, postal_code text, address text not null,
 phone text, owner text, owner_type text, manager text, departments jsonb not null default '[]',
 kind text check(kind in ('病院','診療所')), clinic_beds text check(clinic_beds in ('有床','無床')),
 total_beds integer check(total_beds>=0), general_beds integer check(general_beds>=0),
 medical_beds integer check(medical_beds>=0), psychiatric_beds integer check(psychiatric_beds>=0),
 tuberculosis_beds integer check(tuberculosis_beds>=0), infectious_beds integer check(infectious_beds>=0),
 psychiatric_class text not null default '不明', needs_review boolean not null default false,
 designation_date date, renewal_date date, change_reason text,
 opening_classification text not null default '判定保留' check(opening_classification in
 ('純新規開業','法人化に伴う新規指定','事業承継等','その他の新規指定','判定保留')),
 status text not null check(status in ('現存','休止','廃止','辞退','取消','確認中')),
 reference_date date not null, file_created_date date, retrieved_at timestamptz not null,
 source_file text not null, source_import_id uuid not null references public.source_imports(id),
 raw jsonb not null, updated_at timestamptz not null default now(),
 check(left(code,2)::integer=prefecture_code)
);
create index institutions_location on public.medical_institutions(prefecture_code,kind,status);
create index institutions_beds on public.medical_institutions(total_beds);
create table public.institution_beds (
 institution_id uuid not null references public.medical_institutions(id),
 source_import_id uuid not null references public.source_imports(id),
 bed_type text not null check(bed_type in ('general','medical','psychiatric','tuberculosis','infectious')),
 bed_count integer check(bed_count>=0), primary key(institution_id,source_import_id,bed_type)
);
create table public.institution_changes (
 id uuid primary key default gen_random_uuid(), institution_id uuid not null references public.medical_institutions(id),
 source_import_id uuid not null references public.source_imports(id),
 event_type text not null check(event_type in ('baseline','new_designation','changed','missing_candidate','closure_confirmed','reclassified')),
 event_date date not null, opening_classification text not null default '判定保留',
 reason text, before_data jsonb, after_data jsonb, created_at timestamptz not null default now(),
 unique(institution_id,source_import_id,event_type)
);
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
alter table public.app_members enable row level security;
create policy member_read on public.app_members for select to authenticated using(user_id=auth.uid() or public.is_admin());
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
do $$ declare t text; begin
 foreach t in array array['medical_institutions','institution_beds','institution_changes','source_imports','institution_links','access_logs'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('revoke all on public.%I from anon, authenticated',t);
  execute format('grant select on public.%I to authenticated',t);
  execute format('grant all on public.%I to service_role',t);
  execute format('create policy approved_read on public.%I for select to authenticated using ((select public.is_approved()))',t);
 end loop;
end $$;
-- Logs contain user IDs, so restrict reading to administrators.
drop policy approved_read on public.access_logs;
create policy admin_logs on public.access_logs for select to authenticated using(public.is_admin());
revoke all on public.app_members from anon,authenticated;
grant select on public.app_members to authenticated;
grant all on public.app_members to service_role;
create function public.record_access(p_action text,p_details jsonb default '{}') returns void
language plpgsql security definer set search_path='' as $$
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 if length(p_action)>100 or pg_column_size(p_details)>10000 then raise exception 'invalid audit event'; end if;
 insert into public.access_logs(user_id,action,details) values(auth.uid(),p_action,p_details);
end $$;
revoke all on function public.record_access(text,jsonb) from public;
grant execute on function public.record_access(text,jsonb) to authenticated;
commit;
