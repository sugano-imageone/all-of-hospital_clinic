import test from 'node:test';
import assert from 'node:assert/strict';
import {verifyIdToken,requireMember} from './src/auth.mjs';
import {handler} from './src/index.mjs';
const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const jwk={...await crypto.subtle.exportKey('jwk',pair.publicKey),kid:'test'};
const b64=v=>Buffer.from(typeof v==='string'?v:JSON.stringify(v)).toString('base64url');
const base={aud:'imageone-test',iss:'https://securetoken.google.com/imageone-test',sub:'uid',exp:2000,iat:1000,auth_time:900,email:'test@example.invalid',email_verified:true,firebase:{sign_in_provider:'password'}};
async function token(claims=base,header={alg:'RS256',kid:'test'}) {
 const body=b64(header)+'.'+b64(claims);
 return body+'.'+Buffer.from(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',pair.privateKey,new TextEncoder().encode(body))).toString('base64url');
}
test('accepts signed project-bound email/password ID token',async()=>{
 assert.equal((await verifyIdToken(await token(),'imageone-test',async()=>[jwk],1100)).sub,'uid');
});
for(const [name,changes] of Object.entries({expired:{exp:1100},otherProject:{aud:'other'},otherIssuer:{iss:'https://example.invalid'},future:{iat:1200},unverified:{email_verified:false},custom:{firebase:{sign_in_provider:'custom'}},invalidAuthTime:{auth_time:1001},emptySubject:{sub:''}})) {
 test('rejects '+name,async()=>assert.rejects(()=>token({...base,...changes}).then(t=>verifyIdToken(t,'imageone-test',async()=>[jwk],1100)),{status:401}));
}
test('rejects forged signatures, unknown keys and algorithm confusion',async()=>{
 const t=await token();
 await assert.rejects(()=>verifyIdToken(t.slice(0,-10)+'AAAAAAAAAA','imageone-test',async()=>[jwk],1100),{status:401});
 await assert.rejects(()=>verifyIdToken(t,'imageone-test',async()=>[],1100),{status:401});
 await assert.rejects(()=>token(base,{alg:'none',kid:'test'}).then(t=>verifyIdToken(t,'imageone-test',async()=>[jwk],1100)),{status:401});
});
const member={firebase_uid:'uid',email:base.email,role:'user',approved:1,disabled:0,tokens_valid_after:0};
test('approval, role and revocation are read from server membership',()=>{
 assert.equal(requireMember(base,member).role,'user');
 for(const changes of [{approved:0},{disabled:1},{role:'owner'},{firebase_uid:'other'},{tokens_valid_after:901},{email:'other@example.invalid'}])assert.throws(()=>requireMember(base,{...member,...changes}),{status:403});
 assert.throws(()=>requireMember({...base,role:'master'},member,'admin'),{status:403});
 assert.equal(requireMember(base,{...member,role:'master'},'admin').role,'master');
});
test('API fails closed before a database is configured',async()=>{
 const fetch=handler(()=>{throw Error('must not connect');});
 const r=await fetch(new Request('https://api.invalid/api/search',{method:'POST',body:'{}'}),{});
 assert.equal(r.status,503);
});
test('API denies unapproved or disabled accounts and viewer admin access',async()=>{
 for(const m of [{...member,approved:0},{...member,disabled:1},member]) {
  const db=async(_,sql)=>sql.includes('SELECT active')?[{active:0}]:[m];
  const r=await handler(db,async()=>base)(new Request('https://api.invalid/api/admin/logins'),{MAINTENANCE:'false'});
  assert.equal(r.status,403);
 }
});
test('DB maintenance blocks an otherwise valid admin and missing gate fails closed',async()=>{
 for(const stop of [[],[{active:1}]]) {
  const r=await handler(async()=>stop,async()=>base)(new Request('https://api.invalid/api/me'),{MAINTENANCE:'false'});
  assert.equal(r.status,503);
 }
});
test('login history uses verified claims, never client identity or time',async()=>{
 let insert;
 const db=async(_,sql,args)=>{if(sql.includes('SELECT active'))return [{active:0}];if(sql.includes('SELECT *'))return [member];insert=args;return [];};
 const r=await handler(db,async()=>base)(new Request('https://api.invalid/api/login',{method:'POST',body:JSON.stringify({sub:'attacker',auth_time:1,role:'master'})}),{MAINTENANCE:'false'});
 assert.equal(r.status,200);assert.deepEqual(insert,['uid:900','uid',base.email,'user',900]);
});
test('export rejects a changed revision before querying medical data',async()=>{
 const db=async(_,sql)=>{
  if(sql.includes('SELECT active'))return [{active:0}];
  if(sql.includes('SELECT * FROM app_members'))return [member];
  if(sql.includes('SELECT revision'))return [{revision:8}];
  throw Error('medical data must not be queried');
 };
 const r=await handler(db,async()=>base)(new Request('https://api.invalid/api/export/page',{method:'POST',body:JSON.stringify({page:2,page_size:1000,revision:7})}),{MAINTENANCE:'false'});
 assert.equal(r.status,409);
});
test('collection logs remain master-only even when requested directly',async()=>{
 for(const role of ['user','admin']) {
  const db=async(_,sql)=>sql.includes('SELECT active')?[{active:0}]:[{...member,role}];
  const r=await handler(db,async()=>base)(new Request('https://api.invalid/api/master/collection-jobs'),{MAINTENANCE:'false'});
  assert.equal(r.status,403);
 }
});
