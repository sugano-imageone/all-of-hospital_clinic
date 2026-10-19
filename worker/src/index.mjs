import {verifyIdToken,requireMember} from './auth.mjs';
import {execute} from './db.mjs';
import {buildSearch} from './search.mjs';

export function handler(db=execute,verify=verifyIdToken) {
 return async function(request,env) {
  const origin=request.headers.get('Origin');
  const headers={'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Vary':'Origin'};
  if(origin && origin===env.ALLOWED_ORIGIN) Object.assign(headers,{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Allow-Methods':'GET, POST, OPTIONS'});
  const send=(data,status=200)=>new Response(JSON.stringify(data),{status,headers});
  try {
   if(origin && origin!==env.ALLOWED_ORIGIN)return send({detail:'Origin denied'},403);
   if(request.method==='OPTIONS')return new Response(null,{status:204,headers});
   const path=new URL(request.url).pathname;
   if(path==='/health' && request.method==='GET')return send({status:'migration-validation',maintenance:env.MAINTENANCE!=='false'});
   if(env.MAINTENANCE!=='false')return send({detail:'メンテナンス中'},503);
   const bearer=request.headers.get('Authorization')?.match(/^Bearer ([^\s]+)$/)?.[1];
   const claims=await verify(bearer,env.FIREBASE_PROJECT_ID);
   const stop=await db(env,'SELECT active FROM application_maintenance WHERE singleton=1');
   if(stop.length!==1||Number(stop[0].active)!==0)return send({detail:'メンテナンス中'},503);
   const rows=await db(env,'SELECT * FROM app_members WHERE firebase_uid=?',[claims.sub]);
   const me=requireMember(claims,rows[0]);
   if(path==='/api/me'&&request.method==='GET')return send({user_id:me.firebase_uid,email:me.email,role:me.role});
   if(path==='/api/admin/members') {
    requireMember(claims,me,'admin');
    if(request.method==='GET')return send(await db(env,'SELECT firebase_uid AS user_id,email,role,approved,disabled FROM app_members ORDER BY email,firebase_uid LIMIT 1000'));
    if(request.method==='POST') {
     const text=await request.text();if(text.length>4096)return send({detail:'Request too large'},413);
     let q;try{q=JSON.parse(text);}catch{return send({detail:'Invalid JSON'},400);}
     if(!q||typeof q.user_id!=='string'||q.user_id.length>128||q.user_id===me.firebase_uid||!['user','admin'].includes(q.role)||typeof q.approved!=='boolean'||typeof q.disabled!=='boolean')return send({detail:'Invalid membership'},400);
     const target=await db(env,'SELECT role FROM app_members WHERE firebase_uid=?',[q.user_id]);
     if(target.length!==1||target[0].role==='master')return send({detail:'Protected or unknown member'},403);
     await db(env,'INSERT INTO member_actions(actor_uid,target_uid,role,approved,disabled) VALUES(?,?,?,?,?)',[me.firebase_uid,q.user_id,q.role,Number(q.approved),Number(q.disabled)]);
     return send({ok:true});
    }
   }
   if(path==='/api/master/collection-jobs'&&request.method==='GET') {
    requireMember(claims,me,'master');
    return send(await db(env,'SELECT month,bureau,status,attempted_at,details_json FROM notification_jobs ORDER BY month DESC,bureau LIMIT 100'));
   }
   if(path==='/api/login'&&request.method==='POST') {
    // Firebase auth_time survives token refresh. Identity/time never come from the request body.
    await db(env,'INSERT OR IGNORE INTO app_login_history(session_key,firebase_uid,email,actor_role,auth_time) VALUES(?,?,?,?,?)',
      [claims.sub+':'+claims.auth_time,claims.sub,me.email,me.role,claims.auth_time]);
    return send({recorded:true});
   }
   if(path==='/api/admin/logins'&&request.method==='GET') {
    requireMember(claims,me,'admin');
    const params=new URL(request.url).searchParams;
    const page=Number(params.get('page')||1),email=params.get('email')||'';
    if(!Number.isInteger(page)||page<1||page>100000||email.length>254)return send({detail:'Invalid filters'},400);
    const data=await db(env,"SELECT session_key,firebase_uid,email,auth_time FROM app_login_history WHERE actor_role='user' AND instr(lower(email),lower(?))>0 ORDER BY auth_time DESC,session_key LIMIT 50 OFFSET ?",[email,(page-1)*50]);
    return send({items:data,page,page_size:50});
   }
   if(path==='/api/notifications/metadata'&&request.method==='GET') {
    const items=await db(env,"SELECT min(i.official_item_name) AS name FROM notification_items i JOIN notification_current c ON c.snapshot_id=i.snapshot_id JOIN notification_snapshots s ON s.id=c.snapshot_id AND s.state='validated' GROUP BY i.item_key ORDER BY name");
    const bureaus=await db(env,"SELECT c.bureau,s.reference_dates FROM notification_current c JOIN notification_snapshots s ON s.id=c.snapshot_id AND s.state='validated' ORDER BY c.bureau");
    return send({items:items.map(r=>r.name),bureaus:bureaus.map(r=>({...r,reference_dates:JSON.parse(r.reference_dates)}))});
   }
   if(['/api/search','/api/export/page'].includes(path)&&request.method==='POST') {
    if(Number(request.headers.get('content-length'))>16384)return send({detail:'Request too large'},413);
    const text=await request.text();if(text.length>16384)return send({detail:'Request too large'},413);
    let q;try{q=JSON.parse(text);}catch{return send({detail:'Invalid JSON'},400);}
    const search=buildSearch(q);
    const revisionRows=await db(env,'SELECT revision FROM dataset_revision WHERE singleton=1');
    if(revisionRows.length!==1)throw Error('Missing dataset revision');
    const revision=revisionRows[0].revision;
    if(path==='/api/export/page' && search.page>1 && q.revision!==revision)return send({detail:'データが更新されました。出力を最初からやり直してください'},409);
    const items=await db(env,search.sql,search.args);
    const total=q.include_total===false?null:await db(env,search.countSql,search.countArgs);
    const after=await db(env,'SELECT revision FROM dataset_revision WHERE singleton=1');
    if(after[0]?.revision!==revision)return send({detail:'データが更新されました。再試行してください'},409);
    if(search.page===1)await db(env,'INSERT INTO access_logs(firebase_uid,action,details_json) VALUES(?,?,?)',
      [me.firebase_uid,path==='/api/search'?'search':'export_search',JSON.stringify({count:total?.[0].total??null,revision})]);
    return send({items,total:total?.[0].total??null,page:search.page,page_size:search.page_size,revision});
   }
   return send({detail:'Not found'},404);
  } catch(e) {
   // Provider responses, SQL and tokens must never appear in an HTTP error or log.
   return send({detail:e.status===401?'ログインを確認してください':e.status===403?'利用承認・権限が必要です':e.status===400?'検索条件を確認してください':'一時的に利用できません'},e.status||503);
  }
 };
}
export default {fetch:handler()};
