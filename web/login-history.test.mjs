// PostgreSQL engine in an isolated WASM instance: no network or production DB.
import {PGlite} from '@electric-sql/pglite';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const db=new PGlite();
await db.exec(`
create role anon;create role authenticated;create role service_role;
create schema auth;
create table auth.users(id uuid primary key,email text);
create table auth.sessions(id uuid primary key,user_id uuid,created_at timestamptz);
create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.uid',true),'')::uuid$$;
create function auth.jwt() returns jsonb language sql stable as $$select coalesce(nullif(current_setting('request.jwt',true),''),'{}')::jsonb$$;
grant usage on schema auth to authenticated;
create table public.app_members(user_id uuid primary key,role text,approved boolean);
create function public.is_admin() returns boolean language sql stable security definer set search_path='' as $$select exists(select 1 from public.app_members where user_id=auth.uid() and approved and role in ('admin','master'))$$;
insert into auth.users values ('11111111-1111-1111-1111-111111111111','viewer@example.invalid'),('22222222-2222-2222-2222-222222222222','admin@example.invalid'),('33333333-3333-3333-3333-333333333333','master@example.invalid');
insert into public.app_members values ('11111111-1111-1111-1111-111111111111','user',true),('22222222-2222-2222-2222-222222222222','admin',true),('33333333-3333-3333-3333-333333333333','master',true);
insert into auth.sessions values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','11111111-1111-1111-1111-111111111111','2026-10-20T15:05:00Z'),('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','22222222-2222-2222-2222-222222222222','2026-10-21T01:00:00Z');
`);
const migration=fs.readFileSync(new URL('../sql/016_login_history.sql',import.meta.url),'utf8');
await db.exec(migration);await db.exec(migration);
async function caller(uid,session){await db.exec('reset role');await db.query("select set_config('request.uid',$1,false),set_config('request.jwt',$2,false)",[uid,JSON.stringify({session_id:session})]);await db.exec('set role authenticated');}
const viewer='11111111-1111-1111-1111-111111111111',admin='22222222-2222-2222-2222-222222222222',master='33333333-3333-3333-3333-333333333333';
let checks=0;
await caller(viewer,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
await db.query('select public.record_login()');await db.query('select public.record_login()');
assert.equal((await db.query('select count(*)::int n from public.app_login_history')).rows[0].n,0);checks++; // RLS conceals logs from viewers.
await assert.rejects(db.query('select public.viewer_login_history()'),/admin required/);checks++;
await assert.rejects(db.query("insert into public.app_login_history values ('cccccccc-cccc-cccc-cccc-cccccccccccc',$1,'forged','user',now(),now())",[viewer]),/permission denied/);checks++;
await caller(viewer,'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');await assert.rejects(db.query('select public.record_login()'),/verified session required/);checks++;
await caller(admin,'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');await db.query('select public.record_login()');
let history=(await db.query('select public.viewer_login_history() d')).rows[0].d;
assert.equal(history.total,1);assert.equal(history.items[0].email,'viewer@example.invalid');checks++;
assert.equal(history.items[0].created_at,'2026-10-20T15:05:00+00:00');checks++; // Trusted Auth session time, not recording time.
assert.equal((await db.query("select public.viewer_login_history('viewer','2026-10-21','2026-10-21',1) d")).rows[0].d.total,1);checks++;
assert.equal((await db.query("select public.viewer_login_history('viewer','2026-10-20','2026-10-20',1) d")).rows[0].d.total,0);checks++;
assert.equal((await db.query("select public.viewer_login_history('%',null,null,1) d")).rows[0].d.total,0);checks++; // Literal email substring.
await assert.rejects(db.query("select public.viewer_login_history('',null,null,0)"),/invalid history filters/);checks++;
await caller(master,null);assert.equal((await db.query('select public.viewer_login_history() d')).rows[0].d.total,1);checks++;
await db.exec('reset role');await db.exec("insert into auth.sessions values ('cccccccc-cccc-cccc-cccc-cccccccccccc','11111111-1111-1111-1111-111111111111','2020-01-01T00:00:00Z')");
await caller(viewer,'cccccccc-cccc-cccc-cccc-cccccccccccc');await db.query('select record_login()');
await caller(admin,'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');assert.equal((await db.query('select viewer_login_history() d')).rows[0].d.total,1);checks++;
await db.exec('reset role');await db.query('update public.app_members set approved=false where user_id=$1',[master]);
await caller(master,null);await assert.rejects(db.query('select public.viewer_login_history()'),/admin required/);checks++;
await db.close();console.log(JSON.stringify({isolated_postgres_checks_passed:checks,production_connected:false}));
