-- Apply only when the unreleased login-history client is ready. No production data included.
begin;
create table if not exists public.app_login_history_settings (
 singleton boolean primary key default true check(singleton),started_at timestamptz not null default now()
);
insert into public.app_login_history_settings(singleton) values(true) on conflict do nothing;
alter table public.app_login_history_settings enable row level security;
revoke all on public.app_login_history_settings from public,anon,authenticated;
grant all on public.app_login_history_settings to service_role;
create table if not exists public.app_login_history (
 session_id uuid primary key,
 user_id uuid not null references auth.users(id) on delete cascade,
 email text not null,
 actor_role text not null check(actor_role in ('user','admin','master')),
 created_at timestamptz not null,
 recorded_at timestamptz not null default now()
);
create index if not exists app_login_history_time on public.app_login_history(created_at desc,session_id desc);
alter table public.app_login_history enable row level security;
revoke all on public.app_login_history from public,anon,authenticated;
grant select on public.app_login_history to authenticated;
grant all on public.app_login_history to service_role;
drop policy if exists admin_viewer_logins on public.app_login_history;
create policy admin_viewer_logins on public.app_login_history for select to authenticated
 using((select public.is_admin()) and actor_role='user');

create or replace function public.record_login() returns void
language plpgsql security definer set search_path='' as $$
declare v_role text; v_session uuid; v_created timestamptz; v_email text;
begin
 select m.role into v_role from public.app_members m where m.user_id=auth.uid() and m.approved;
 if not public.is_approved() or v_role is null then raise exception 'approval required'; end if;
 v_session:=nullif(auth.jwt()->>'session_id','')::uuid;
 select s.created_at,u.email into v_created,v_email
 from auth.sessions s join auth.users u on u.id=s.user_id
 where s.id=v_session and s.user_id=auth.uid();
 if not found or v_created is null then raise exception 'verified session required'; end if;
 if v_created < (select started_at from public.app_login_history_settings where singleton) then return; end if;
 -- The session and login time come from Auth, never browser-supplied identity/time.
 insert into public.app_login_history(session_id,user_id,email,actor_role,created_at)
 values(v_session,auth.uid(),coalesce(v_email,''),v_role,v_created)
 on conflict(session_id) do nothing;
end $$;
revoke all on function public.record_login() from public,anon;
grant execute on function public.record_login() to authenticated;

create or replace function public.viewer_login_history(
 p_email text default '',p_from date default null,p_to date default null,p_page integer default 1
) returns jsonb language plpgsql stable security definer set search_path='' as $$
declare v_result jsonb;
begin
 if not public.is_admin() then raise exception 'admin required'; end if;
 if p_page is null or p_page<1 or p_page>100000 or length(coalesce(p_email,''))>254
 or (p_from is not null and p_to is not null and p_from>p_to) then raise exception 'invalid history filters'; end if;
 with matched as (
  select session_id,user_id,email,created_at from public.app_login_history
  where actor_role='user'
  and position(lower(coalesce(p_email,'')) in lower(email))>0
  and (p_from is null or created_at >= (p_from::timestamp at time zone 'Asia/Tokyo'))
  and (p_to is null or created_at < ((p_to+1)::timestamp at time zone 'Asia/Tokyo'))
 ), page as (
  select * from matched order by created_at desc,session_id desc offset (p_page-1)*50 limit 50
 ) select jsonb_build_object('items',coalesce((select jsonb_agg(to_jsonb(p) order by p.created_at desc,p.session_id desc) from page p),'[]'::jsonb),
  'total',(select count(*) from matched),'page',p_page,'page_size',50) into v_result;
 return v_result;
end $$;
revoke all on function public.viewer_login_history(text,date,date,integer) from public,anon;
grant execute on function public.viewer_login_history(text,date,date,integer) to authenticated;
insert into public.directory_migrations(version) values('login-history-v1') on conflict do nothing;
notify pgrst,'reload schema';
commit;
