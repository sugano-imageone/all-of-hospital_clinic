import {PGlite} from '@electric-sql/pglite';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const db=new PGlite();
await db.exec(`create role anon;create role authenticated;create role service_role bypassrls; create schema auth;
create function auth.role() returns text language sql as $$select current_setting('request.role',true)$$;
create function public.is_approved() returns boolean language sql as $$select coalesce(current_setting('request.approved',true),'')='true'$$;
create function public.is_master() returns boolean language sql as $$select coalesce(current_setting('request.master',true),'')='true'$$;
create table public.directory_migrations(version text primary key,applied_at timestamptz default now());
create table public.medical_institutions(code text primary key,prefecture_code int,prefecture text,bureau text,status text,reference_date date);
alter table public.medical_institutions enable row level security;grant select on public.medical_institutions to authenticated;
create policy approved on public.medical_institutions for select to authenticated using(public.is_approved());
insert into public.medical_institutions values('0110000001',1,'北海道','北海道厚生局','現存','2026-10-01'),('0110000002',1,'北海道','北海道厚生局','現存','2026-10-01'),('0110000003',1,'北海道','北海道厚生局','現存','2026-10-01');
grant usage on schema auth to authenticated,service_role;
`);
await db.exec(fs.readFileSync(new URL('../sql/018_notifications.sql',import.meta.url),'utf8'));
await db.query("select set_config('request.role','service_role',false)");
const clockFunctions=(await db.query("select pg_get_functiondef(oid) sql from pg_proc where pronamespace='public'::regnamespace and proname in ('notification_monthly_targets','finish_notification_snapshot','notification_job_result')")).rows.map(r=>r.sql);
async function clock(day){for(const sql of clockFunctions)await db.exec(sql.replaceAll('now()',"'2026-10-"+day+"T01:00:00Z'::timestamptz"));}
await clock('20');assert.equal((await db.query('select public.notification_monthly_targets() t')).rows[0].t.length,8);

async function upload(rows,dates={'1':'2026-10-01'}){
 const metadata={bureau:'北海道厚生局',reference_dates:dates,source:[{url:'official'}],filings:rows.length,facilities:2};
 const id=(await db.query('select public.begin_notification_snapshot($1) id',[metadata])).rows[0].id;
 await db.query('select public.upload_notification_batch($1,$2,$3)',[id,[{code:'0110000001',prefecture_code:1,reference_date:'2026-10-01'},{code:'0110000002',prefecture_code:1,reference_date:'2026-10-01'}],rows]);return id;
}
const row=(code,item)=>({code,item_key:item.normalize('NFKC'),official_item_name:item,calculation_start:'2026-09-01',date_basis:'calculation_start',payload:{latest_selection_resolved:true}});
const id=await upload([row('0110000001','医療安全対策加算１')]);
await db.query('select public.finish_notification_snapshot($1)',[id]);
assert.equal((await db.query('select public.notification_monthly_targets() t')).rows[0].t.length,7);
await db.query("select public.notification_job_result('北海道厚生局',false,'{}')");
await clock('25');assert.equal((await db.query('select public.notification_monthly_targets() t')).rows[0].t.length,7);

await db.exec("set role authenticated;select set_config('request.approved','true',false)");
assert.equal((await db.query("select * from public.notification_search('医療安全対策加算１','filed')")).rows.length,1);
assert.equal((await db.query("select * from public.notification_search('医療安全対策加算１','unfiled')")).rows[0].code,'0110000002');
assert.equal((await db.query("select * from public.notification_search('対象外','unfiled')")).rows.length,0);
assert.equal((await db.query('select * from public.notification_jobs')).rows.length,0);
await assert.rejects(db.query("select public.begin_notification_snapshot('{}')"),/permission denied/);
await db.exec("select set_config('request.approved','false',false)");
assert.equal((await db.query('select * from public.notification_filings')).rows.length,0);
assert.equal((await db.query("select * from public.notification_search('医療安全対策加算１','filed')")).rows.length,0);
await db.exec('reset role');
const bad=await upload([row('0110000001','changed')],{'1':'2026-09-01'});
await assert.rejects(db.query('select public.finish_notification_snapshot($1)',[bad]),/reference date mismatch/);
assert.equal((await db.query('select snapshot_id from public.notification_current')).rows[0].snapshot_id,id);
await db.exec('delete from public.notification_jobs');
await assert.rejects(db.query('select public.notification_monthly_targets()'),/20th state missing/);
await db.close();console.log('Notification atomic replacement, coverage, unknown scope, RLS and privilege checks passed');
