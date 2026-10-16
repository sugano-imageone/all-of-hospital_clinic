/* GitHub Pages uses Supabase Auth and RLS directly. No service key or data asset. */
(function(){
'use strict';
const config=JSON.parse(document.getElementById('cloud-config').textContent);
const nativeFetch=window.fetch.bind(window),storageKey='imageone.session.v1';
let session=null,refreshing=null;
try{session=JSON.parse(sessionStorage.getItem(storageKey)||'null');}catch{sessionStorage.removeItem(storageKey);}
function save(value){session=value;if(value)sessionStorage.setItem(storageKey,JSON.stringify(value));else sessionStorage.removeItem(storageKey);}
function json(data,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});}
function fail(status,message){const e=new Error(message);e.status=status;throw e;}
async function network(path,options={}){
 try{return await nativeFetch(config.url+path,{...options,signal:options.signal||AbortSignal.timeout(20000)});}
 catch(e){if(e.name==='TimeoutError'||e.name==='AbortError')fail(504,'接続に20秒以上かかっています。時間をおいて再試行するか「接続を確認」を押してください');fail(503,'認証・データサーバーとの通信に失敗しました。「接続を確認」で通信状況を確認してください。APIの再設定は不要です');}
}
async function refresh(){
 if(!session?.refresh_token)fail(401,'ログインが必要です');
 if(!refreshing)refreshing=(async()=>{const r=await network('/auth/v1/token?grant_type=refresh_token',{method:'POST',headers:{apikey:config.key,'Content-Type':'application/json'},body:JSON.stringify({refresh_token:session.refresh_token}),cache:'no-store'});if(!r.ok){save(null);fail(401,'再ログインしてください');}save(await r.json());})().finally(()=>{refreshing=null;});
 return refreshing;
}
async function remote(path,options={},retry=true){
 const headers={apikey:config.key,...(session?{Authorization:'Bearer '+session.access_token}:{}),...options.headers};
 const r=await network(path,{...options,headers,cache:'no-store'});
 if(r.status===401&&session&&retry&&!path.startsWith('/auth/v1/token')){await refresh();return remote(path,options,false);}
 if(!r.ok)fail(r.status===401?401:r.status===403?403:502,r.status===401?'メールアドレス・パスワードを確認してください':r.status===403?'利用権限がありません':'データの取得・登録に失敗しました。時間をおいて再度お試しください');
 return r;
}
const rpc=async(name,payload={})=>{const r=await remote('/rest/v1/rpc/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});return r.status===204?null:r.json();};
async function member(authenticatedUser=null){
 if(!session)fail(401,'ログインが必要です');
 const user=authenticatedUser||await(await remote('/auth/v1/user')).json();
 const q=new URLSearchParams({user_id:'eq.'+user.id,select:'user_id,role,approved'});
 const rows=await(await remote('/rest/v1/app_members?'+q)).json();
 if(!rows[0]?.approved)fail(403,'管理者の利用承認が必要です');
 return {...user,...rows[0]};
}
const profile=me=>({email:me.email,role:me.role,user_id:me.id,password_change_required:!!me.user_metadata?.password_change_required});
function literal(value){return '"'+String(value).replaceAll('\\','\\\\').replaceAll('"','\\"')+'"';}
function searchParams(q){
 const sort=['code','name','prefecture','total_beds','reference_date'].includes(q.sort)?q.sort:'code';
 const direction=q.direction==='desc'?'desc':'asc';
 const p=new URLSearchParams({kind:'eq.'+(q.kind==='診療所'?'診療所':'病院'),status:q.include_suspended?'in.(現存,休止)':'eq.現存',order:sort+'.'+direction+'.nullslast,code.asc'});
 if(q.prefectures?.length)p.set('prefecture','in.('+q.prefectures.map(literal).join(',')+')');
 // Use the indexed numeric location as well as the displayed prefecture name.
 const prefectureCodes=(q.prefectures||[]).map(name=>Number(config.prefectureCodes?.[name]));
 if(prefectureCodes.length&&prefectureCodes.every(code=>Number.isInteger(code)&&code>=1&&code<=47))p.set('prefecture_code','in.('+prefectureCodes.join(',')+')');
 // Standalone eq values must not be wrapped in PostgREST in-list quotes.
 // URLSearchParams encodes transport punctuation; quotes here become literal data.
 for(const k of ['bureau','owner_type','clinic_beds','psychiatric_class','code'])if(q[k])p.set(k,'eq.'+q[k]);
 // Match the approved bed policy through the numeric index too. This avoids
 // authenticated counts timing out on a text-only clinic-beds predicate.
 if(q.clinic_beds==='無床')p.append('total_beds','eq.0');
 if(q.clinic_beds==='有床'){p.append('total_beds','gte.1');p.append('total_beds','lte.19');}
 for(const k of ['municipality','name','owner'])if(q[k]){const value=String(q[k]).replace(/[\\%_*]/g,c=>'\\'+c);p.set(k,'ilike.%'+value+'%');}
 const bed=['total','general','medical','psychiatric','tuberculosis','infectious'].includes(q.bed_type)?q.bed_type:'total';
 for(const [k,op] of [['minimum','gte'],['maximum','lte']])if(q[k]!=null){if(!Number.isInteger(q[k])||q[k]<0||q[k]>100000)fail(400,'病床数を確認してください');p.append(bed+'_beds',op+'.'+q[k]);}
 return p;
}
const publicFields=config.columns.map(c=>c[0]).join(',');
async function allRows(table,params,progress){
 const first=new URLSearchParams(params);first.set('offset','0');first.set('limit','1000');
 const r=await remote('/rest/v1/'+table+'?'+first,{headers:{Prefer:'count=exact'}});
 const total=Number(r.headers.get('content-range')?.split('/')[1]);if(!Number.isFinite(total)||total>200000)fail(502,'件数を確認できませんでした');
 const rows=await r.json(),pages=[];let next=1000;
 async function worker(){while(next<total){const offset=next;next+=1000;const p=new URLSearchParams(params);p.set('offset',String(offset));p.set('limit','1000');const chunk=await(await remote('/rest/v1/'+table+'?'+p)).json();pages.push([offset,chunk]);if(progress)progress(Math.min(total,offset+chunk.length),total);}}
 await Promise.all([worker(),worker(),worker()]);pages.sort((a,b)=>a[0]-b[0]);for(const [,chunk]of pages)rows.push(...chunk);
 if(rows.length!==total)fail(403,'取得中にデータまたは利用権限が変わりました。再度ログインしてお試しください');return rows;
}
function summary(records){
 const active=records.filter(r=>r.status==='現存');const result={total:active.length,kind:{},clinic:{},psychiatric:{},owner_type:{},prefectures:{},scales:{}};
 const add=(obj,key)=>{obj[key]=(obj[key]||0)+1;};
 for(const r of active){add(result.kind,r.kind||'不明');add(result.owner_type,r.owner_type||'不明');const pref=result.prefectures[r.prefecture]||(result.prefectures[r.prefecture]={'病院':0,'診療所':0,'不明':0});add(pref,r.kind||'不明');if(r.kind==='診療所')add(result.clinic,r.clinic_beds||'不明');if(r.kind==='病院'){add(result.psychiatric,r.psychiatric_class||'不明');const n=r.total_beds;add(result.scales,n==null?'不明':n<100?'20〜99床':n<200?'100〜199床':n<500?'200〜499床':'500床以上');}}
 return result;
}
function currentSources(rows){const found=new Map();for(const r of rows){const old=found.get(r.prefecture_code);if(!old||Number(r.id)>Number(old.id))found.set(r.prefecture_code,r);}return [...found.values()].sort((a,b)=>a.prefecture_code-b.prefecture_code);}
async function dashboardRows(){
 const records=[];let next=1;
 async function worker(){while(next<=47){const code=next++;const p=new URLSearchParams({prefecture_code:'eq.'+code,select:'prefecture,kind,status,clinic_beds,total_beds,psychiatric_class,owner_type,needs_review',order:'code.asc'});records.push(...await allRows('medical_institutions',p));}}
 // Indexed prefecture-sized requests avoid a national exact count and large offsets.
 await Promise.all([worker(),worker()]);return records;
}
async function audit(action,details={}){await rpc('record_access',{p_action:action,p_details:details});}
function csv(rows){const safe=v=>{let t=String(v??'');if(/^[\s]*[=+\-@\t\r]/.test(t))t="'"+t;return '"'+t.replaceAll('"','""')+'"';};return '\ufeff'+[config.columns.map(c=>safe(c[1])).join(','),...rows.map(r=>config.columns.map(c=>safe(r[c[0]])).join(','))].join('\r\n');}
function xml(value){return String(value??'').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g,'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
function col(n){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
async function workbook(records,sources,scope){
 const zip=new JSZip();const sheets=[];
 function sheet(name,rows){
  let count=0;const parts=[];for(const values of rows){count++;const cells=values.map((v,i)=>{const ref=col(i)+count;if(typeof v==='number'&&Number.isFinite(v))return '<c r="'+ref+'"><v>'+v+'</v></c>';return '<c r="'+ref+'" t="inlineStr"><is><t xml:space="preserve">'+xml(v)+'</t></is></c>';}).join('');parts.push('<row r="'+count+'">'+cells+'</row>');}
  const width=name==='病院'||name==='診療所'||name==='精神科病院'?config.columns.length:9;
  const body='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><sheetData>'+parts.join('')+'</sheetData><autoFilter ref="A1:'+col(width-1)+Math.max(count,1)+'"/></worksheet>';
  sheets.push(name);zip.file('xl/worksheets/sheet'+sheets.length+'.xml',body);
 }
 sheet('検索・抽出フォーム',[['対象範囲',scope],['病床数の未記載','0床として扱います'],['操作方法','病院・診療所シートのフィルターで絞り込めます']]);
 const sum=summary(records),stats=[['現存施設の集計','件数']];for(const group of ['kind','clinic','psychiatric','owner_type','scales'])for(const [label,n]of Object.entries(sum[group]))stats.push([group+' / '+label,n]);sheet('集計ダッシュボード',stats);
 function* dataRows(predicate){yield config.columns.map(c=>c[1]);for(const r of records)if(predicate(r))yield config.columns.map(c=>r[c[0]]);}
 sheet('病院',dataRows(r=>r.kind==='病院'));sheet('診療所',dataRows(r=>r.kind==='診療所'));sheet('精神科病院',dataRows(r=>r.kind==='病院'&&r.psychiatric_beds>0));
 sheet('取得元・更新情報',[['都道府県','厚生局','元ファイル','基準日','作成日','取得日','SHA-256','状態','件数'],...sources.map(r=>['prefecture','bureau','filename','reference_date','file_created_date','retrieved_at','sha256','status','record_count'].map(k=>r[k]))]);
 zip.file('[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+sheets.map((_,i)=>'<Override PartName="/xl/worksheets/sheet'+(i+1)+'.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>').join('')+'</Types>');
 zip.file('_rels/.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>');
 zip.file('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+sheets.map((name,i)=>'<sheet name="'+xml(name)+'" sheetId="'+(i+1)+'" r:id="rId'+(i+1)+'"/>').join('')+'</sheets></workbook>');
 zip.file('xl/_rels/workbook.xml.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+sheets.map((_,i)=>'<Relationship Id="rId'+(i+1)+'" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet'+(i+1)+'.xml"/>').join('')+'</Relationships>');
 return zip.generateAsync({type:'blob',compression:'DEFLATE',compressionOptions:{level:6}});
}
async function adminFunction(action,payload={}){try{return (await remote('/functions/v1/admin-users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...payload})})).json();}catch(e){if(e.status===401||e.status===403)throw e;fail(503,'ユーザー管理機能の接続設定が必要です。管理者にご連絡ください');}}
window.fetch=async function(url,options={}){
 if(typeof url!=='string'||(!url.startsWith('/api/')&&!url.startsWith('/auth/')))return nativeFetch(url,options);
 try{
  if(url==='/auth/login'){
   const form=options.body;save(null);
   document.getElementById('message').textContent='メールアドレス・パスワードを認証しています…';
   const r=await remote('/auth/v1/token?grant_type=password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.get('email'),password:form.get('password')})},false);const tokens=await r.json();save(tokens);
   document.getElementById('message').textContent='利用権限を確認しています…';
   try{return json({ok:true,user:profile(await member(tokens.user))});}catch(e){save(null);throw e;}
  }
  if(url==='/api/connection-check'){
   const start=performance.now();await remote('/auth/v1/health',{},false);return json({ok:true,seconds:Math.round((performance.now()-start)/10)/100});
  }
  if(url==='/auth/refresh'){await refresh();return json({ok:true});}
  if(url==='/auth/logout'){try{if(session)await remote('/auth/v1/logout',{method:'POST'});}finally{save(null);}return json({ok:true});}
  const me=await member();if(url==='/api/me')return json(profile(me));
  const q=options.body?JSON.parse(options.body):{};
  if(url==='/auth/password'){
   if(typeof q.password!=='string'||q.password.length<12||new TextEncoder().encode(q.password).length>72)fail(400,'パスワードは12文字以上、72バイト以下で設定してください');
   await remote('/auth/v1/user',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:q.password,data:{password_change_required:false}})});return json({ok:true});
  }
  if(url==='/api/search'){
   const p=searchParams(q);p.set('select',publicFields);p.set('offset',String(((q.page||1)-1)*50));p.set('limit','50');const r=await remote('/rest/v1/medical_institutions?'+p,{headers:{Prefer:'count=exact'}}),items=await r.json();const total=Number(r.headers.get('content-range')?.split('/')[1]);await audit('search',{count:total,filters:q});return json({items,total,page:q.page||1,page_size:50});
  }
  if(url==='/api/dashboard'){
   const [rows,rawSources,monthly]=await Promise.all([dashboardRows(),rpc('source_metadata'),rpc('monthly_change_counts')]);const sources=currentSources(rawSources),status_counts={};for(const r of rows)status_counts[r.status]=(status_counts[r.status]||0)+1;
   return json({...summary(rows),collected:sources.map(r=>r.prefecture),missing:[],status_counts,quality_review_count:rows.filter(r=>r.needs_review).length,sources,monthly,note:'現存のみを集計。保険医療機関の通常医科主一覧。病床数の未記載は0床として扱います。'});
  }
  if(url.startsWith('/api/export/')){
   const format=url.split('/').pop();if(!['csv','xlsx','master'].includes(format))fail(404,'出力形式を確認してください');
   const p=format==='master'?new URLSearchParams({status:'eq.現存',order:'code.asc'}):searchParams(q);p.set('select',publicFields);
   const rows=await allRows('medical_institutions',p,(done,total)=>{document.getElementById('message').textContent='データ取得中 '+done.toLocaleString()+' / '+total.toLocaleString()+' 件';});await audit('export_'+format,{count:rows.length,filters:q});
   if(format==='csv')return new Response(csv(rows),{headers:{'Content-Type':'text/csv;charset=utf-8'}});
   document.getElementById('message').textContent='Excelを生成しています…';const sources=currentSources(await rpc('source_metadata'));const blob=await workbook(rows,sources,format==='master'?'現存のみ・全47都道府県':'検索条件に一致');return new Response(blob,{headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});
  }
  if(url==='/api/admin/members'){
   if(!['admin','master'].includes(me.role))fail(403,'管理者権限が必要です');
   if(options.method==='POST'){await rpc('set_member',{p_user:q.user_id,p_role:q.role,p_approved:q.approved});return json({ok:true});}
   try{return json((await adminFunction('list')).members);}catch(e){if(e.status===401||e.status===403)throw e;const rows=await(await remote('/rest/v1/app_members?select=user_id,role,approved&order=created_at.asc,user_id.asc')).json();return json(rows);}
  }
  if(url==='/api/admin/create'){
   if(!['admin','master'].includes(me.role))fail(403,'管理者権限が必要です');await adminFunction('create',q);return json({ok:true});
  }
  fail(404,'操作が見つかりません');
 }catch(e){return json({detail:e.message||'接続に失敗しました。再度お試しください'},e.status||503);}
};
const connectionCheck=document.getElementById('connection-check');if(connectionCheck)connectionCheck.onclick=async()=>{
 connectionCheck.disabled=true;const output=document.getElementById('connection-check-result');output.textContent='認証サーバーへの接続を確認しています…';
 try{const r=await window.fetch('/api/connection-check');const result=await r.json();if(!r.ok)throw Error(result.detail);output.textContent='認証サーバーへ接続できました（'+result.seconds+'秒）。ログインを再試行してください';}catch(e){output.textContent=e.message;}finally{connectionCheck.disabled=false;}
};
const form=document.getElementById('member-create');
const memoPanel=document.getElementById('registration-memo'),memoText=document.getElementById('registration-memo-text');
const generator=document.getElementById('generate-password');if(generator)generator.onclick=()=>{
 const values=crypto.getRandomValues(new Uint8Array(18)),alphabet='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!#$%&*+-=';
 form.elements.password.value=Array.from(values,n=>alphabet[n%alphabet.length]).join('');document.getElementById('message').textContent='仮パスワードを生成しました。登録後に送付用メモに表示されます';
};
const copyMemo=document.getElementById('copy-registration-memo');if(copyMemo)copyMemo.onclick=async()=>{
 try{await navigator.clipboard.writeText(memoText.value);document.getElementById('message').textContent='送付用メモをコピーしました';}catch{memoText.focus();memoText.select();document.getElementById('message').textContent='メモを選択しました。Ctrl+C / ⌘+C でコピーしてください';}
};
const saveMemo=document.getElementById('save-registration-memo');if(saveMemo)saveMemo.onclick=()=>{
 const url=URL.createObjectURL(new Blob([memoText.value],{type:'text/plain;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='医療機関DB_ログイン案内.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
if(form)form.onsubmit=async event=>{
 event.preventDefault();const button=form.querySelector('button[type=submit]');button.disabled=true;
 memoPanel.hidden=true;memoText.value='';
 try{const data=Object.fromEntries(new FormData(form));data.email=data.email.trim();const r=await window.fetch('/api/admin/create',{method:'POST',body:JSON.stringify(data)});if(!r.ok)throw Error((await r.json()).detail);
 memoText.value=['全国医療機関データベースのアカウントを登録しました。','','ログインURL：'+new URL('./',location.href).href,'ID（メールアドレス）：'+data.email,'仮パスワード：'+data.password,'権限：'+(data.role==='admin'?'管理者':'閲覧者'),'','ログイン後、「パスワード変更」からご自身のパスワードに変更してください。','この案内にはログイン情報が含まれます。大切に保管してください。'].join('\n');memoPanel.hidden=false;
 form.reset();document.getElementById('message').textContent='ユーザーを追加しました。送付用メモをコピーして送信してください';await loadMembers();}catch(e){document.getElementById('message').textContent=e.message;}finally{button.disabled=false;}
};
const passwordForm=document.getElementById('password-change');if(passwordForm)passwordForm.onsubmit=async event=>{
 event.preventDefault();const values=Object.fromEntries(new FormData(passwordForm));if(values.password!==values.confirmation){document.getElementById('message').textContent='確認用パスワードが一致しません';return;}
 const button=passwordForm.querySelector('button');button.disabled=true;
 try{const r=await window.fetch('/auth/password',{method:'POST',body:JSON.stringify({password:values.password})});if(!r.ok)throw Error((await r.json()).detail);passwordForm.reset();document.getElementById('message').textContent='パスワードを変更しました。次回から新しいパスワードでログインしてください';}catch(e){document.getElementById('message').textContent=e.message;}finally{button.disabled=false;}
};
})();
