import test from 'node:test';
import assert from 'node:assert/strict';
import JSZip from 'jszip';
import {createFirebaseClient,csv,xlsx} from './firebase-client.mjs';
const config={apiUrl:'https://imageone-api.example.invalid',firebaseApiKey:'AIza_PUBLIC_TEST_ONLY'};
const reply=(v,status=200)=>new Response(JSON.stringify(v),{status});
test('unapproved login cannot retain tokens or search',async()=>{
 const seen=[];
 const c=createFirebaseClient(config,async(url)=>{seen.push(String(url));return String(url).includes('googleapis')?reply({idToken:'token',refreshToken:'refresh'}):reply({},403);});
 await assert.rejects(()=>c.login('x@example.invalid','password'),{status:403});
 await assert.rejects(()=>c.request('/api/search',{}),{status:401});
 assert.equal(seen.length,2);
});
test('export keeps filters and revision across pages and never returns tokens',async()=>{
 const calls=[];
 const c=createFirebaseClient(config,async(url,options)=>{
  if(String(url).includes('googleapis'))return reply({idToken:'id-token',refreshToken:'refresh'});
  if(new URL(url).pathname==='/api/me')return reply({role:'user'});
  if(new URL(url).pathname==='/api/login')return reply({recorded:true});
  const q=JSON.parse(options.body);calls.push(q);
  return reply({items:q.page===1?Array.from({length:1000},(_,i)=>({code:String(i).padStart(10,'0')})): [{code:'0000001000'}],total:q.page===1?1001:null,revision:7});
 });
 assert.deepEqual(await c.login('x@example.invalid','pw'),{role:'user'});
 const filters={kind:'診療所',use_notifications:true,notification_item:'検査',notification_status:'unfiled'};
 assert.equal((await c.allRows(filters)).length,1001);
 assert.deepEqual(calls[1],{...filters,after_code:'0000000999',page:2,page_size:1000,revision:7,include_total:false});
 c.logout();await assert.rejects(()=>c.request('/api/me'),{status:401});
});
test('export aborts if the adopted revision changes',async()=>{
 const c=createFirebaseClient(config,async(url,options)=>{
  if(String(url).includes('googleapis'))return reply({idToken:'token',refreshToken:'refresh'});
  if(new URL(url).pathname==='/api/me')return reply({role:'user'});
  if(new URL(url).pathname==='/api/login')return reply({});
  return JSON.parse(options.body).page===1?reply({items:[{code:1}],total:2,revision:7}):reply({},409);
 });
 await c.login('x@example.invalid','pw');await assert.rejects(()=>c.allRows({}),{status:409});
});
test('CSV neutralizes formulas and Excel writes both institution kinds as strings',async()=>{
 const rows=[{kind:'病院',name:'=1+1'},{kind:'診療所',name:'<医療>&診療所'}],columns=[['kind','種別'],['name','名称']];
 assert.ok(csv(rows,columns).includes("'=1+1"));
 const bytes=await xlsx(rows,columns),zip=await JSZip.loadAsync(bytes,{checkCRC32:true});
 assert.ok((await zip.file('xl/worksheets/sheet1.xml').async('string')).includes('t="inlineStr"'));
 assert.ok((await zip.file('xl/worksheets/sheet2.xml').async('string')).includes('&lt;医療&gt;&amp;診療所'));
 assert.ok((await zip.file('xl/workbook.xml').async('string')).includes('診療所'));
});
