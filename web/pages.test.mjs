import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function client(){
 const calls=[];
 const config={url:'https://example.supabase.co',key:'sb_publishable_test',columns:[['code','コード']],prefectureCodes:{'福岡県':40}};
 const native=async(url)=>{
  calls.push(new URL(url));
  if(url.endsWith('/auth/v1/user'))return Response.json({id:'viewer-id'});
  if(url.includes('/rest/v1/app_members?'))return Response.json([{role:'user',approved:true}]);
  if(url.includes('/rest/v1/medical_institutions?'))return Response.json([],{headers:{'Content-Range':'*/0'}});
  if(url.endsWith('/rpc/source_metadata'))return Response.json([]);
  if(url.endsWith('/rpc/monthly_change_counts'))return Response.json({});
  if(url.endsWith('/rpc/record_access'))return new Response(null,{status:204});
  throw Error('Unexpected request');
 };
 const window={fetch:native};
 vm.runInNewContext(fs.readFileSync(new URL('./pages.js',import.meta.url),'utf8'),{
  window,document:{getElementById:id=>id==='cloud-config'?{textContent:JSON.stringify(config)}:null},
  sessionStorage:{getItem:()=>JSON.stringify({access_token:'test-token'}),setItem(){},removeItem(){}},
  Response,URL,URLSearchParams,TextEncoder,
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
