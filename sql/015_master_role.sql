-- Protect the owner's master role. Existing user accounts/data remain intact.
begin;
do $$
declare c record;
begin
 if not exists(select 1 from auth.users where id='2530b4ab-17b3-4f22-829c-c6b21ef5a13c'::uuid and lower(email)='sugano@imageone.co.jp') then
  raise exception 'Expected owner account is missing; verify the target project';
 end if;
 for c in select conname from pg_constraint where conrelid='public.app_members'::regclass and contype='c' and pg_get_constraintdef(oid) like '%role%' loop
  execute format('alter table public.app_members drop constraint %I',c.conname);
 end loop;
end $$;
alter table public.app_members add constraint directory_member_role check(role in ('master','admin','user'));
alter table public.app_members drop constraint if exists directory_master_identity;
alter table public.app_members add constraint directory_master_identity check(role<>'master' or user_id='2530b4ab-17b3-4f22-829c-c6b21ef5a13c'::uuid);
update public.app_members set role='master',approved=true where user_id='2530b4ab-17b3-4f22-829c-c6b21ef5a13c'::uuid;
create or replace function public.is_master() returns boolean language sql stable security definer set search_path=''
as $$select exists(select 1 from public.app_members where user_id=auth.uid() and approved and role='master')$$;
create or replace function public.is_admin() returns boolean language sql stable security definer set search_path=''
as $$select exists(select 1 from public.app_members where user_id=auth.uid() and approved and role in ('master','admin'))$$;
revoke all on function public.is_master() from public,anon;
grant execute on function public.is_master() to authenticated;
create or replace function public.set_member(p_user uuid,p_role text,p_approved boolean) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not public.is_admin() then raise exception 'admin required';end if;
 if p_user='2530b4ab-17b3-4f22-829c-c6b21ef5a13c'::uuid then raise exception 'master membership is protected';end if;
 if p_user=auth.uid() then raise exception 'self membership changes are not allowed';end if;
 if p_role not in ('admin','user') then raise exception 'only the designated owner may be master';end if;
 insert into public.app_members(user_id,role,approved) values(p_user,p_role,p_approved)
 on conflict(user_id) do update set role=excluded.role,approved=excluded.approved;
 insert into public.access_logs(user_id,action,details) values(auth.uid(),'member_update',jsonb_build_object('user_id',p_user,'role',p_role,'approved',p_approved));
end $$;
insert into public.directory_migrations(version) values('existing-v1-master-role-v1') on conflict do nothing;
notify pgrst,'reload schema';
commit;
select 'existing-v1-master-role-v1' as migration,role,approved from public.app_members where user_id='2530b4ab-17b3-4f22-829c-c6b21ef5a13c'::uuid;
