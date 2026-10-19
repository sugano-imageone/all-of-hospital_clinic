import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function client({approved=true,networkFailure=false,timeout=false,role='user',exportRows=[],maintenance=false,serverMaintenance=false}={}){
 const calls=[];
 const config={maintenance,url:'https://example.supabase.co',key:'sb_publishable_test',columns:[['code','コード']],prefectureCodes:{'福岡県':40}};
 const native=async(url)=>{
  if(url.endsWith('/rpc/maintenance_status'))return Response.json({active:serverMaintenance});
  calls.push(new URL(url));
  if(networkFailure)throw new TypeError('Failed to fetch');
  if(timeout)throw new DOMException('Timed out','TimeoutError');
  if(url.includes('/auth/v1/token?grant_type=password'))return Response.json({access_token:'test-token',refresh_token:'test-refresh',user:{id:'viewer-id',email:'viewer@example.invalid'}});
  if(url.endsWith('/auth/v1/health'))return Response.json({version:'test'});
  if(url.endsWith('/auth/v1/user'))return Response.json({id:'viewer-id'});
  if(url.includes('/rest/v1/app_members?'))return Response.json([{role,approved}]);
  if(url.includes('/rest/v1/medical_institutions?'))return Response.json(exportRows.slice(Number(new URL(url).searchParams.get('offset')||0),Number(new URL(url).searchParams.get('offset')||0)+1000),{headers:{'Content-Range':'0-'+Math.max(0,exportRows.length-1)+'/'+exportRows.length}});
  if(url.endsWith('/rpc/source_metadata'))return Response.json([]);
  if(url.endsWith('/rpc/monthly_change_counts'))return Response.json({});
  if(url.endsWith('/rpc/record_login'))return new Response(null,{status:204});
  if(url.includes('/rpc/notification_search?'))return Response.json(exportRows,{headers:{'Content-Range':'0-'+Math.max(0,exportRows.length-1)+'/'+exportRows.length}});
  if(url.endsWith('/rpc/record_access'))return new Response(null,{status:204});
  throw Error('Unexpected request');
 };
 const window={fetch:native};
 vm.runInNewContext(fs.readFileSync(new URL('./pages.js',import.meta.url),'utf8'),{
  window,document:{getElementById:id=>id==='cloud-config'?{textContent:JSON.stringify(config)}:id==='message'?{textContent:''}:null},
  sessionStorage:{getItem:()=>JSON.stringify({access_token:'test-token'}),setItem(){},removeItem(){}},
  Response,URL,URLSearchParams,TextEncoder,AbortSignal,performance,
 });
 return {window,calls};
}

for(const [key,value] of [['clinic_beds','無床'],['clinic_beds','有床'],['bureau','九州厚生局'],['owner_type','医療法人'],['psychiatric_class','精神病床なし'],['code','4010210047']]){
 test(`equality filter ${key}=${value} sends the scalar without literal quote characters`,async()=>{
  const {window,calls}=client();
  const response=await window.fetch('/api/search',{method:'POST',body:JSON.stringify({kind:'診療所',prefectures:['福岡県'],[key]:value})});
  assert.equal(response.status,200);
  const query=calls.find(url=>url.pathname==='/rest/v1/medical_institutions').searchParams;
  assert.equal(query.get(key),'eq.'+value);
  assert.equal(query.get('kind'),'eq.診療所');
  assert.equal(query.get('prefecture'),'in.("福岡県")');
  assert.equal(query.get('prefecture_code'),'in.(40)');
 });
}

test('search and CSV export use identical non-bedded clinic filters',async()=>{
 const {window,calls}=client();
 const options={method:'POST',body:JSON.stringify({kind:'診療所',prefectures:['福岡県'],clinic_beds:'無床'})};
 assert.equal((await window.fetch('/api/search',options)).status,200);
 assert.equal((await window.fetch('/api/export/csv',options)).status,200);
 const queries=calls.filter(url=>url.pathname==='/rest/v1/medical_institutions').map(url=>url.searchParams);
 for(const query of queries){assert.equal(query.get('clinic_beds'),'eq.無床');assert.equal(query.get('prefecture'),'in.("福岡県")');}
 assert.equal(queries.length,2);
 assert.equal(queries[0].get('total_beds'),'eq.0');
});

test('clinic bed classification preserves independent user bed bounds',async()=>{
 const {window,calls}=client();
 await window.fetch('/api/search',{method:'POST',body:JSON.stringify({kind:'診療所',clinic_beds:'有床',minimum:2,maximum:10,bed_type:'general'})});
 const query=calls.find(url=>url.pathname==='/rest/v1/medical_institutions').searchParams;
 assert.deepEqual(query.getAll('total_beds'),['gte.1','lte.19']);
 assert.deepEqual(query.getAll('general_beds'),['gte.2','lte.10']);
});

test('dashboard fetches all 47 indexed prefecture partitions without a national query',async()=>{
 const {window,calls}=client();
 const response=await window.fetch('/api/dashboard');
 assert.equal(response.status,200);
 const queries=calls.filter(url=>url.pathname==='/rest/v1/medical_institutions').map(url=>url.searchParams);
 assert.equal(queries.length,47);
 assert.deepEqual(queries.map(q=>Number(q.get('prefecture_code').slice(3))).sort((a,b)=>a-b),Array.from({length:47},(_,i)=>i+1));
 assert.ok(queries.every(q=>q.get('offset')==='0'&&q.get('limit')==='1000'));
 assert.equal((await response.json()).total,0);
});

test('password login verifies membership and records the verified login and returns no token to the UI',async()=>{
 const {window,calls}=client();const form=new FormData();form.set('email','viewer@example.invalid');form.set('password','test-password');
 const r=await window.fetch('/auth/login',{method:'POST',body:form});assert.equal(r.status,200);
 const body=await r.json();assert.equal(body.user.email,'viewer@example.invalid');assert.equal(body.user.role,'user');
 assert.equal(calls.length,3);assert.equal(calls[2].pathname,'/rest/v1/rpc/record_login');assert.equal(calls[0].pathname,'/auth/v1/token');assert.equal(calls[1].pathname,'/rest/v1/app_members');
 assert.ok(!JSON.stringify(body).includes('test-token'));assert.ok(!JSON.stringify(body).includes('test-password'));
 const search=await window.fetch('/api/search',{method:'POST',body:'{}'});assert.equal(search.status,200);
 assert.equal(calls[3].pathname,'/auth/v1/user');assert.equal(calls[4].pathname,'/rest/v1/app_members');
});

test('unapproved login clears the session and cannot search',async()=>{
 const {window}=client({approved:false});const form=new FormData();form.set('email','viewer@example.invalid');form.set('password','test-password');
 assert.equal((await window.fetch('/auth/login',{method:'POST',body:form})).status,403);
 assert.equal((await window.fetch('/api/search',{method:'POST',body:'{}'})).status,401);
});

test('connection check works without fetching memberships or medical data',async()=>{
 const {window,calls}=client();const r=await window.fetch('/api/connection-check');assert.equal(r.status,200);
 assert.equal(calls.length,1);assert.equal(calls[0].pathname,'/auth/v1/health');
});

for(const [options,status] of [[{networkFailure:true},503],[{timeout:true},504]]){
 test(`connection errors return actionable Japanese messages (${status})`,async()=>{
  const {window}=client(options);const r=await window.fetch('/api/connection-check');assert.equal(r.status,status);
  const body=await r.json();assert.match(body.detail,/接続を確認/);assert.ok(!body.detail.includes('Failed to fetch'));
 });
}

for(const role of ['user','admin','master']){
 test(`full CSV export checks ${role} permissions and ignores search filters`,async()=>{
  const {window,calls}=client({role,exportRows:[{id:'one',code:'0110000001',name:'病院',kind:'病院',status:'現存',raw:{確認:'済'}},{id:'two',code:'2710000002',kind:'診療所',status:'廃止',raw:{}}]});
  const response=await window.fetch('/api/export/full',{method:'POST',body:JSON.stringify({kind:'病院',name:'一致しない条件',prefectures:['東京都']})});
  const medical=calls.filter(url=>url.pathname==='/rest/v1/medical_institutions');
  if(role==='user'){assert.equal(response.status,403);assert.equal(medical.length,0);return;}
  assert.equal(response.status,200);const text=await response.text();
  assert.match(text,/0110000001/);assert.match(text,/診療所/);assert.match(text,/廃止/);assert.match(text,/確認/);
  const params=medical[0].searchParams;assert.equal(params.get('select'),'*');assert.equal(params.get('order'),'code.asc,id.asc');
  for(const key of ['kind','status','name','prefecture'])assert.equal(params.has(key),false);
  assert.ok(calls.some(url=>url.pathname==='/rest/v1/rpc/record_access'));
 });
}

test('full export retrieves rows beyond the first 1000',async()=>{
 const {window,calls}=client({role:'admin',exportRows:Array.from({length:1001},(_,i)=>({id:String(i),code:String(i).padStart(10,'0')}))});
 const response=await window.fetch('/api/export/full',{method:'POST',body:'{}'});
 assert.equal(response.status,200);const text=await response.text();
 assert.equal(text.split('\r\n').length,1002);assert.match(text,/0000001000/);
 assert.deepEqual(calls.filter(url=>url.pathname==='/rest/v1/medical_institutions').map(url=>url.searchParams.get('offset')),['0','1000']);
});

for(const mode of ['static','server'])test(`maintenance ${mode} blocks login, refresh and data before authentication`,async()=>{
 const {window,calls}=client({maintenance:mode==='static',serverMaintenance:mode==='server'});
 for(const url of ['/auth/login','/auth/refresh','/api/me','/api/search']){
  const r=await window.fetch(url,{method:'POST',body:'{}'});assert.equal(r.status,503);assert.equal((await r.json()).detail,'メンテナンス中');
 }
 assert.equal(calls.length,0);
});

test('notification conditions OFF ignore retained item and status',async()=>{
 const {window,calls}=client();await window.fetch('/api/search',{method:'POST',body:JSON.stringify({notifications_enabled:false,notification_item:'条件残り',notification_status:'not_filed'})});
 assert.ok(calls.some(c=>c.pathname==='/rest/v1/medical_institutions'));assert.ok(!calls.some(c=>c.pathname.includes('notification_search')));
});
test('notification search and CSV use the identical RPC filters',async()=>{
 const {window,calls}=client();const q={notifications_enabled:true,notification_item:'医療安全対策加算１',notification_status:'not_filed',minimum:20,maximum:200};
 for(const path of ['/api/search','/api/export/csv'])assert.equal((await window.fetch(path,{method:'POST',body:JSON.stringify(q)})).status,200);
 const requests=calls.filter(c=>c.pathname==='/rest/v1/rpc/notification_search');assert.equal(requests.length,2);
 for(const u of requests){assert.equal(u.searchParams.get('p_item'),q.notification_item);assert.equal(u.searchParams.get('p_status'),'unfiled');u.searchParams.delete('offset');u.searchParams.delete('limit');u.searchParams.sort();}
 assert.equal(requests[0].search,requests[1].search);
});
