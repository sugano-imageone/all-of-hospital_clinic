-- Add reversible application maintenance controls; no memberships/accounts are changed.
begin;
create table if not exists public.application_maintenance (
 singleton boolean primary key default true check(singleton),
 active boolean not null default false,
 updated_at timestamptz not null default now()
);
insert into public.application_maintenance(singleton) values(true) on conflict do nothing;
alter table public.application_maintenance enable row level security;
revoke all on public.application_maintenance from public,anon,authenticated;
grant all on public.application_maintenance to service_role;

create or replace function public.maintenance_active() returns boolean
language sql stable security definer set search_path='' as $$
 select coalesce((select active from public.application_maintenance where singleton),false)
$$;
revoke all on function public.maintenance_active() from public,anon;
grant execute on function public.maintenance_active() to authenticated,service_role;

create or replace function public.maintenance_status() returns jsonb
language sql stable security definer set search_path='' as $$
 select jsonb_build_object('active',public.maintenance_active())
$$;
revoke all on function public.maintenance_status() from public;
grant execute on function public.maintenance_status() to anon,authenticated,service_role;

create or replace function public.set_maintenance(p_active boolean) returns jsonb
language plpgsql security definer set search_path='' as $$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 if p_active is null then raise exception 'active must not be null'; end if;
 update public.application_maintenance set active=p_active,updated_at=now() where singleton;
 return public.maintenance_status();
end $$;
revoke all on function public.set_maintenance(boolean) from public,anon,authenticated;
grant execute on function public.set_maintenance(boolean) to service_role;

create or replace function public.is_approved() returns boolean language sql stable security definer set search_path=''
as $$select not public.maintenance_active() and exists(select 1 from public.app_members where user_id=auth.uid() and approved)$$;
create or replace function public.is_admin() returns boolean language sql stable security definer set search_path=''
as $$select not public.maintenance_active() and exists(select 1 from public.app_members where user_id=auth.uid() and approved and role in ('admin','master'))$$;
create or replace function public.is_master() returns boolean language sql stable security definer set search_path=''
as $$select not public.maintenance_active() and exists(select 1 from public.app_members where user_id=auth.uid() and approved and role='master')$$;
-- Block cached clients at the existing membership check too. Existing permissive
-- policies remain in place, and normal operation resumes when active=false.
drop policy if exists maintenance_members_gate on public.app_members;
create policy maintenance_members_gate on public.app_members as restrictive for select to authenticated
 using(not public.maintenance_active());
notify pgrst,'reload schema';
commit;
