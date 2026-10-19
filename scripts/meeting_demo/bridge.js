// Offline meeting adapter. Accounts and login history here are demonstration data.
const review=meetingData;
const dataset=review.rows.map(values=>Object.fromEntries(review.keys.map((key,i)=>[key,values[i]])));
const config={columns:review.columns};
const demoAccounts=[{email:'viewer@example.invalid',password:'DemoViewer2026!',role:'user',user_id:'demo-viewer'},
 {email:'admin@example.invalid',password:'DemoAdmin2026!',role:'admin',user_id:'demo-admin'}];
const storage={get(k){try{return JSON.parse(localStorage.getItem('meeting-20261021.'+k)||'null')}catch{return null}},set(k,v){try{localStorage.setItem('meeting-20261021.'+k,JSON.stringify(v))}catch{}}};
let demoSession=null;
try{demoSession=demoAccounts.find(a=>a.email===sessionStorage.getItem('meeting-20261021.session'))||null}catch{}
let demoLogins=storage.get('logins')||[];
function setDemoSession(a){demoSession=a;try{if(a)sessionStorage.setItem('meeting-20261021.session',a.email);else sessionStorage.removeItem('meeting-20261021.session')}catch{}}
const norm=s=>String(s||'').normalize('NFKC').replace(/\s+/g,'');
const notificationMap=new Map(review.notifications.items.map((item,i)=>[norm(item[0]),i]));
const notificationSets=new Map();
function notificationSet(name){const i=notificationMap.get(norm(name));if(i===undefined)throw Error('正式名称の候補から届出項目を選択してください');if(!notificationSets.has(i))notificationSets.set(i,new Set(review.notifications.items[i][1].map(n=>review.notifications.codes[n])));return notificationSets.get(i);}
function filtered(q){
 const bed=(q.bed_type||'total')+'_beds',use=q.use_notifications===true;
 const filed=use?notificationSet(q.notification_name):null;
 if(use&&!['filed','unfiled'].includes(q.filing_status))throw Error('届出済の／未届出の を選択してください');
 const covered=new Set(review.notifications.bureaus.map(b=>b.bureau));
 const contains=(r,k)=>!q[k]||String(r[k]||'').toLocaleLowerCase().includes(String(q[k]).toLocaleLowerCase());
 const rows=dataset.filter(r=>r.kind===(q.kind||'病院')&&(['現存',...(q.include_suspended?['休止']:[])].includes(r.status))
  &&(!q.prefectures?.length||q.prefectures.includes(r.prefecture))
  &&['bureau','owner_type','clinic_beds','psychiatric_class','code'].every(k=>!q[k]||r[k]===q[k])
  &&['municipality','name','owner'].every(k=>contains(r,k))
  &&(q.minimum==null||(r[bed]!=null&&r[bed]>=q.minimum))
  &&(q.maximum==null||(r[bed]!=null&&r[bed]<=q.maximum))
  &&(!use||(covered.has(r.bureau)&&filed.has(r.code)===(q.filing_status==='filed'))));
 const sort=['code','name','prefecture','total_beds','reference_date'].includes(q.sort)?q.sort:'code',direction=q.direction==='desc'?-1:1;
 rows.sort((a,b)=>a[sort]==null?(b[sort]==null?0:1):b[sort]==null?-1:
  (typeof a[sort]==='number'?(a[sort]-b[sort]):String(a[sort]).localeCompare(String(b[sort]),'ja'))*direction||a.code.localeCompare(b.code));
 return rows;
}
const demoResponse=(d,status=200)=>new Response(JSON.stringify(d),{status,headers:{'Content-Type':'application/json'}});
window.fetch=async function(url,options={}){
 try{
  if(url==='/auth/login'){
   const a=demoAccounts.find(a=>a.email===options.body.get('email')&&a.password===options.body.get('password'));
   if(!a)return demoResponse({detail:'デモ用メールアドレス・パスワードを確認してください'},401);
   setDemoSession(a);demoLogins.unshift({user_id:a.user_id,email:a.email,role:a.role,created_at:new Date().toISOString()});storage.set('logins',demoLogins);
   return demoResponse({ok:true,user:{email:a.email,role:a.role,user_id:a.user_id}});
  }
  if(url==='/auth/logout'){setDemoSession(null);return demoResponse({ok:true})}
  if(!demoSession)return demoResponse({detail:'ログインが必要です'},401);
  if(url==='/api/me')return demoResponse({email:demoSession.email,role:demoSession.role,user_id:demoSession.user_id});
  const q=options.body?JSON.parse(options.body):{};
  if(url==='/api/admin/logins'){
   if(!['admin','master'].includes(demoSession.role))return demoResponse({detail:'管理者権限が必要です'},403);
   if(q.from_date&&q.to_date&&q.from_date>q.to_date)return demoResponse({detail:'期間の開始日が終了日を超えています'},422);
   const day=s=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(s));
   const rows=demoLogins.filter(r=>r.role==='user'&&r.email.toLowerCase().includes((q.email||'').toLowerCase())&&(!q.from_date||day(r.created_at)>=q.from_date)&&(!q.to_date||day(r.created_at)<=q.to_date)).sort((a,b)=>b.created_at.localeCompare(a.created_at));
   const page=q.page||1;return demoResponse({items:rows.slice((page-1)*50,page*50).map(({role,...r})=>r),total:rows.length,page,page_size:50});
  }
  if(url==='/api/admin/members'){
   if(!['admin','master'].includes(demoSession.role))return demoResponse({detail:'管理者権限が必要です'},403);
   return demoResponse([]);
  }
  if(url==='/api/dashboard')return demoResponse(review.dashboard);
  if(url==='/api/search'){
   const rows=filtered(q),page=q.page||1;return demoResponse({items:rows.slice((page-1)*50,page*50),total:rows.length,page,page_size:50});
  }
  if(url==='/api/export/csv')return new Response(csv(filtered(q)),{headers:{'Content-Type':'text/csv;charset=utf-8'}});
  if(url==='/api/export/xlsx')return new Response(await workbook(filtered(q),review.sources,'検索条件に一致'),{headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});
  return demoResponse({detail:'このデモでは医療機関検索・出力・ログイン履歴を確認できます'},403);
 }catch(e){return demoResponse({detail:e.message},422)}
};

function prepareMeetingDemo(){
 const items=document.getElementById('notification-options');
 for(const [name] of review.notifications.items){const o=document.createElement('option');o.value=name;items.append(o)}
 const check=document.getElementById('use-notifications'),fields=document.getElementById('notification-fields');
 const sync=()=>{fields.hidden=!check.checked;document.querySelector('[name=notification_name]').required=check.checked};
 check.addEventListener('change',sync);document.getElementById('search').addEventListener('reset',()=>setTimeout(sync,0));sync();
 const dates=document.getElementById('bureau-reference-dates');
 const jp=s=>s?new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'long',day:'numeric'}).format(new Date(s+'T00:00:00+09:00')):'未取得';
 for(const b of review.notifications.bureaus){const row=document.createElement('tr');for(const v of [b.bureau,b.master_dates.map(jp).join('、')||'未取得',b.notification_dates.map(jp).join('、')||'未取得']){const c=document.createElement('td');c.textContent=v;row.append(c)}dates.append(row)}
 document.getElementById('reset-meeting-demo').onclick=()=>{if(confirm('この端末のデモ用ログイン履歴を消して、最初から始めますか？')){storage.set('logins',[]);setDemoSession(null);location.reload()}};
}
