// Supabase Edge Function. Service credentials stay in the function environment.
// Every action verifies the caller's JWT and current approved admin membership.
export async function handle(request,env){
 const origin=request.headers.get('origin');const allowed=env.ALLOWED_ORIGIN||'https://sugano-imageone.github.io';
 const cors={'Access-Control-Allow-Origin':allowed,'Access-Control-Allow-Headers':'authorization, apikey, content-type, x-client-info','Access-Control-Allow-Methods':'POST, OPTIONS','Vary':'Origin','Cache-Control':'no-store'};
 const reply=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{...cors,'Content-Type':'application/json'}});
 if(origin&&origin!==allowed)return reply({error:'origin_not_allowed'},403);
 if(request.method==='OPTIONS')return new Response(null,{status:204,headers:cors});
 if(request.method!=='POST')return reply({error:'method_not_allowed'},405);
 const url=env.SUPABASE_URL,key=env.SUPABASE_ANON_KEY,secret=env.SUPABASE_SERVICE_ROLE_KEY;
 if(!url||!key||!secret)return reply({error:'server_configuration_required'},503);
 const authorization=request.headers.get('authorization')||'';
 if(!/^Bearer \S+$/.test(authorization))return reply({error:'login_required'},401);
 const call=env.fetch||fetch;
 const userHeaders={apikey:key,Authorization:authorization,'Content-Type':'application/json'};
 const serviceHeaders={apikey:secret,...(secret.startsWith('eyJ')?{Authorization:'Bearer '+secret}:{}),'Content-Type':'application/json'};
 try{
  const auth=await call(url+'/auth/v1/user',{headers:userHeaders});if(!auth.ok)return reply({error:'login_required'},401);const user=await auth.json();
  const memberResponse=await call(url+'/rest/v1/app_members?'+new URLSearchParams({user_id:'eq.'+user.id,select:'role,approved'}),{headers:userHeaders});
  if(!memberResponse.ok)return reply({error:'admin_required'},403);const members=await memberResponse.json();
  if(!members[0]?.approved||!['admin','master'].includes(members[0].role))return reply({error:'admin_required'},403);
  const text=await request.text();if(text.length>16384)return reply({error:'request_too_large'},413);let body;try{body=JSON.parse(text);}catch{return reply({error:'invalid_request'},400);}
  if(body.action==='list'){
   const users=[];for(let page=1;page<=100;page++){const r=await call(url+'/auth/v1/admin/users?page='+page+'&per_page=1000',{headers:serviceHeaders});if(!r.ok)return reply({error:'user_list_failed'},502);const chunk=(await r.json()).users||[];users.push(...chunk);if(chunk.length<1000)break;}
   const membership=[];for(let offset=0;offset<100000;offset+=1000){const r=await call(url+'/rest/v1/app_members?select=user_id,role,approved&order=created_at.asc,user_id.asc&limit=1000&offset='+offset,{headers:serviceHeaders});if(!r.ok)return reply({error:'user_list_failed'},502);const chunk=await r.json();membership.push(...chunk);if(chunk.length<1000)break;}
   const emails=new Map(users.map(u=>[u.id,u.email||'']));
   const audit=await call(url+'/rest/v1/rpc/record_access',{method:'POST',headers:userHeaders,body:JSON.stringify({p_action:'members_read',p_details:{count:membership.length}})});if(!audit.ok)return reply({error:'audit_failed'},502);
   return reply({members:membership.map(m=>({...m,email:emails.get(m.user_id)||''}))});
  }
  if(body.action!=='create')return reply({error:'invalid_action'},400);
  const email=String(body.email||'').trim(),password=String(body.password||''),role=body.role||'user';
  if(email.length>254||!/^\S+@\S+\.\S+$/.test(email)||password.length<12||new TextEncoder().encode(password).length>72||!['admin','user'].includes(role))return reply({error:'invalid_user_fields'},400);
  const created=await call(url+'/auth/v1/admin/users',{method:'POST',headers:serviceHeaders,body:JSON.stringify({email,password,email_confirm:true,user_metadata:{password_change_required:true}})});
  if(!created.ok)return reply({error:created.status===422?'email_already_registered':'user_creation_failed'},created.status===422?409:502);
  const newUser=await created.json();if(!newUser.id)return reply({error:'user_creation_failed'},502);
  const assigned=await call(url+'/rest/v1/rpc/set_member',{method:'POST',headers:userHeaders,body:JSON.stringify({p_user:newUser.id,p_role:role,p_approved:true})});
  if(!assigned.ok){await call(url+'/auth/v1/admin/users/'+encodeURIComponent(newUser.id),{method:'DELETE',headers:serviceHeaders});return reply({error:'membership_assignment_failed'},502);}
  return reply({user_id:newUser.id,email,role,approved:true},201);
 }catch{return reply({error:'operation_failed'},502);}
}
if(typeof Deno!=='undefined')Deno.serve(request=>handle(request,{
 SUPABASE_URL:Deno.env.get('SUPABASE_URL'),SUPABASE_ANON_KEY:Deno.env.get('SUPABASE_ANON_KEY'),
 SUPABASE_SERVICE_ROLE_KEY:Deno.env.get('SUPABASE_SERVICE_ROLE_KEY'),ALLOWED_ORIGIN:Deno.env.get('ALLOWED_ORIGIN')
}));
