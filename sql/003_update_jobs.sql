begin;
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
commit;
