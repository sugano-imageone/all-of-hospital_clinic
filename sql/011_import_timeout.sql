-- Apply after 010 on Supabase. Large prefectural snapshots exceed the default
-- Data API statement timeout. Keep each snapshot atomic instead of splitting it.
-- This changes the server-side import role only; end-user roles and RLS stay intact.
begin;
do $$
begin
 if public.directory_schema_version() <> 'existing-v1-to-directory-v2' then
  raise exception 'Apply 010_upgrade_existing.sql first';
 end if;
end $$;
alter role service_role set statement_timeout = '90s';
notify pgrst, 'reload config';
commit;
select rolname, setting
from pg_roles cross join lateral unnest(rolconfig) as setting
where rolname='service_role' and setting like 'statement_timeout=%';
