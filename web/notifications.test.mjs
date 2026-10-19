import {PGlite} from '@electric-sql/pglite';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const db=new PGlite();
await db.exec(`create role anon;create role authenticated;create role service_role;
create schema auth;grant usage on schema auth to authenticated,service_role;
create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.uid',true),'')::uuid$$;
create function auth.role() returns text language sql stable as $$select current_setting('request.role',true)$$;
create table app_members(user_id uuid primary key,role text,approved boolean);
insert into app_members values('11111111-1111-1111-1111-111111111111','user',true),('22222222-2222-2222-2222-222222222222','master',true),('33333333-3333-3333-3333-333333333333','user',false);
create table medical_institutions(code text primary key,bureau text,status text);
insert into medical_institutions values('0110000001','北海道厚生局','現存'),('0110000002','北海道厚生局','現存'),('0110000003','北海道厚生局','現存'),('0110000004','北海道厚生局','廃止'),('0210000001','東北厚生局','現存');
alter table medical_institutions enable row level security;grant select on medical_institutions to authenticated;
`);
await db.exec(fs.readFileSync(new URL('../sql/017_maintenance.sql',import.meta.url),'utf8'));
await db.exec('create policy approved_read on medical_institutions for select to authenticated using((select is_approved()));');
await db.exec(fs.readFileSync(new URL('../sql/018_notifications.sql',import.meta.url),'utf8'));
await db.exec(`create table source_imports(id int);create table update_jobs(id int);insert into source_imports values(1);insert into update_jobs values(1);alter table source_imports enable row level security;alter table update_jobs enable row level security;grant select on source_imports,update_jobs to authenticated;create policy imports_admin_read on source_imports for select to authenticated using(is_admin());create policy admin_jobs on update_jobs for select to authenticated using(is_admin());insert into app_members values('44444444-4444-4444-4444-444444444444','admin',true);`);
await db.exec(fs.readFileSync(new URL('../sql/019_collection_log_permissions.sql',import.meta.url),'utf8'));

async function role(name,uid='11111111-1111-1111-1111-111111111111'){await db.exec('reset role');await db.query("select set_config('request.uid',$1,false),set_config('request.role',$2,false)",[uid,name]);await db.exec('set role '+name);}
await role('service_role');
const metadata={bureau:'北海道厚生局',reference_dates:{1:'2026-10-01'},source:{url:'https://official.invalid/source'},filings:1,facilities:3};
async function begin(m=metadata){return (await db.query('select begin_notification_snapshot($1) id',[m])).rows[0].id;}
const id=await begin();
await assert.rejects(db.query('select finish_notification_snapshot($1)',[id]),/incomplete snapshot/);
const facilities=['0110000001','0110000002','0110000004'].map(code=>({code,prefecture_code:1,reference_date:'2026-10-01'}));
const filing={code:'0110000001',item_key:'医療安全対策加算1',official_item_name:'医療安全対策加算１',date_basis:'calculation_start',receipt_date:null,calculation_start:'2026-09-01',payload:{latest_selection_resolved:true,date_basis:'calculation_start'}};
await db.query('select upload_notification_batch($1,$2,$3)',[id,facilities,[filing]]);
await db.query('select upload_notification_batch($1,$2,$3)',[id,facilities,[filing]]); // retry idempotence
await role('authenticated');assert.deepEqual((await db.query('select * from notification_search($1,$2)',['医療安全対策加算１','filed'])).rows,[]);
await assert.rejects(db.query('select begin_notification_snapshot($1)',[metadata]),/permission denied/);
await role('service_role');await db.query('select finish_notification_snapshot($1)',[id]);
await role('authenticated');
const codes=async(name,status)=>(await db.query('select code from notification_search($1,$2) order by code',[name,status])).rows.map(r=>r.code);
assert.deepEqual(await codes('医療安全対策加算１','filed'),['0110000001']);
assert.deepEqual(await codes('医療安全対策加算１','unfiled'),['0110000002']); // absent facility, missing bureau and closed excluded
assert.deepEqual(await codes('資料にない項目','unfiled'),[]);
assert.deepEqual(await codes('医療安全対策加算１','invalid'),[]);
assert.equal((await db.query('select notification_metadata() m')).rows[0].m.bureaus[0].reference_dates['1'],'2026-10-01');
assert.equal((await db.query('select count(*)::int n from notification_jobs')).rows[0].n,0);
await role('authenticated','33333333-3333-3333-3333-333333333333');assert.deepEqual(await codes('医療安全対策加算１','filed'),[]);
await assert.rejects(db.query('select notification_metadata()'),/approval required/);
await role('anon');await assert.rejects(db.query('select * from notification_search($1,$2)',['医療安全対策加算１','filed']),/permission denied/);
await role('authenticated','44444444-4444-4444-4444-444444444444');
assert.equal((await db.query('select count(*)::int n from source_imports')).rows[0].n,0);
assert.equal((await db.query('select count(*)::int n from update_jobs')).rows[0].n,0);
await role('authenticated','22222222-2222-2222-2222-222222222222');
assert.equal((await db.query('select count(*)::int n from source_imports')).rows[0].n,1);
assert.equal((await db.query('select count(*)::int n from update_jobs')).rows[0].n,1);
await role('service_role');
const bad=await begin({...metadata,reference_dates:{1:'2026-09-01'}});
await db.query('select upload_notification_batch($1,$2,$3)',[bad,facilities.map(f=>({...f,reference_date:'2026-09-01'})),[filing]]);
await assert.rejects(db.query('select finish_notification_snapshot($1)',[bad]),/older reference date/);
await db.query('select set_maintenance(true)');await role('authenticated');assert.deepEqual(await codes('医療安全対策加算１','filed'),[]);
await db.exec('reset role');assert.equal((await db.query('select snapshot_id from notification_current')).rows[0].snapshot_id,id);
assert.equal((await db.query('select count(*)::int n from medical_institutions')).rows[0].n,5);
await db.close();console.log('Notification SQL: permissions, staging, atomic replacement, date rollback guard, missing coverage and maintenance checks passed');
