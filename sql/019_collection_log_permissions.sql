-- Collection execution logs are master-only. Public source metadata stays available
-- through the existing approved-user source_metadata RPC, which omits error details.
begin;
drop policy if exists imports_admin_read on public.source_imports;
create policy imports_admin_read on public.source_imports for select to authenticated using((select public.is_master()));
drop policy if exists admin_jobs on public.update_jobs;
create policy admin_jobs on public.update_jobs for select to authenticated using((select public.is_master()));
notify pgrst,'reload schema';
commit;
