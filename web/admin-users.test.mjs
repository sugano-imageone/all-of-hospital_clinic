import test from 'node:test';
import assert from 'node:assert/strict';
import {handle} from '../supabase/functions/admin-users/index.ts';
const base={SUPABASE_URL:'https://example.supabase.co',SUPABASE_ANON_KEY:'public-test-key',SUPABASE_SERVICE_ROLE_KEY:'server-test-secret'};
const request=(body,token='user-token')=>new Request('https://example.test/',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',Origin:'https://sugano-imageone.github.io'},body:JSON.stringify(body)});
test('viewer cannot list or create users even through direct function calls',async()=>{
 let serviceCalls=0;
 const env={...base,fetch:async(url,options)=>{if(options.headers.apikey===base.SUPABASE_SERVICE_ROLE_KEY)serviceCalls++;return new Response(JSON.stringify(url.includes('/auth/')?{id:'viewer-id'}:[{role:'user',approved:true}]),{status:200});}};
 for(const action of ['list','create'])assert.equal((await handle(request({action}),env)).status,403);
 assert.equal(serviceCalls,0);
});
test('unapproved administrator and forged token are rejected',async()=>{
 assert.equal((await handle(request({action:'list'}),{...base,fetch:async()=>new Response('{}',{status:401})})).status,401);
 const env={...base,fetch:async url=>new Response(JSON.stringify(url.includes('/auth/')?{id:'admin-id'}:[{role:'admin',approved:false}]))};
 assert.equal((await handle(request({action:'list'}),env)).status,403);
});
test('admin creates viewer with confirmed login and assigns through guarded RPC',async()=>{
 const calls=[];
 const env={...base,fetch:async(url,options)=>{calls.push([url,options]);if(url.endsWith('/auth/v1/user'))return new Response('{"id":"admin-id"}');if(url.includes('/rest/v1/app_members?'))return new Response('[{"role":"admin","approved":true}]');if(url.endsWith('/auth/v1/admin/users'))return new Response('{"id":"new-user-id"}');return new Response(null,{status:204});}};
 const r=await handle(request({action:'create',email:'viewer@example.invalid',password:'long-test-password',role:'user'}),env);
 assert.equal(r.status,201);const data=await r.json();assert.equal(data.role,'user');assert.ok(!JSON.stringify(data).includes(base.SUPABASE_SERVICE_ROLE_KEY));
 const create=JSON.parse(calls.find(([url])=>url.endsWith('/auth/v1/admin/users'))[1].body);assert.equal(create.email_confirm,true);
 const assign=calls.find(([url])=>url.endsWith('/rpc/set_member'));assert.equal(assign[1].headers.Authorization,'Bearer user-token');assert.deepEqual(JSON.parse(assign[1].body),{p_user:'new-user-id',p_role:'user',p_approved:true});
});
test('failed membership assignment removes only the newly created account',async()=>{
 const deleted=[];
 const env={...base,fetch:async(url,options)=>{if(options.method==='DELETE'){deleted.push(url);return new Response('{}');}if(url.endsWith('/auth/v1/user'))return new Response('{"id":"admin-id"}');if(url.includes('/app_members?'))return new Response('[{"role":"admin","approved":true}]');if(url.endsWith('/admin/users'))return new Response('{"id":"new-user-id"}');return new Response('{}',{status:500});}};
 assert.equal((await handle(request({action:'create',email:'viewer@example.invalid',password:'long-test-password',role:'user'}),env)).status,502);
 assert.deepEqual(deleted,['https://example.supabase.co/auth/v1/admin/users/new-user-id']);
});
test('foreign origin and invalid role rejected before creating an account',async()=>{
 const bad=new Request('https://example.test/',{method:'POST',headers:{Origin:'https://attacker.invalid'}});assert.equal((await handle(bad,base)).status,403);
 let created=false;const env={...base,fetch:async url=>{if(url.endsWith('/auth/v1/user'))return new Response('{"id":"admin-id"}');if(url.includes('/app_members?'))return new Response('[{"role":"admin","approved":true}]');created=true;return new Response('{}');}};
 assert.equal((await handle(request({action:'create',email:'viewer@example.invalid',password:'long-test-password',role:'owner'}),env)).status,400);assert.equal(created,false);
});
test('master can list members but cannot create another master',async()=>{
 const env={...base,fetch:async url=>{
  if(url.endsWith('/auth/v1/user'))return new Response('{"id":"master-id"}');
  if(url.includes('/app_members?')&&url.includes('user_id='))return new Response('[{"role":"master","approved":true}]');
  if(url.includes('/auth/v1/admin/users?'))return new Response('{"users":[{"id":"master-id","email":"master@example.invalid"}]}');
  if(url.includes('/app_members?'))return new Response('[{"user_id":"master-id","role":"master","approved":true}]');
  return new Response(null,{status:204});
 }};
 assert.equal((await handle(request({action:'list'}),env)).status,200);
 assert.equal((await handle(request({action:'create',email:'other@example.invalid',password:'long-test-password',role:'master'}),env)).status,400);
});
