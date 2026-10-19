const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s);
let page=1,total=0,lastQuery=null,dashboardData=null,currentUserId=null,dashboardLoading=null,canExportAll=false;
const message=t=>{$('#message').textContent=t||'';};
function showMaintenance(){
 $('#login-heading').textContent='メンテナンス中';$('#maintenance-notice').hidden=false;$('#login-description').hidden=true;$('#login').hidden=true;
 for(const input of $('#login').querySelectorAll('input,button'))input.disabled=true;
 $('#login-panel').hidden=false;$('#workspace').hidden=true;message('');
}

async function api(url,options={},retry=true){
 const response=await fetch(url,options);
 if(response.status===401&&retry&&!url.startsWith('/auth/')){
  const refreshed=await fetch('/auth/refresh',{method:'POST'});
  if(refreshed.ok)return api(url,options,false);
 }
 if(!response.ok){let text='処理に失敗しました';try{const d=await response.json();text=typeof d.detail==='string'?d.detail:'入力条件を確認してください';}catch{}if(text==='メンテナンス中')showMaintenance();throw Error(text);}
 return response;
}
const post=(url,data)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function cell(row,value){const td=document.createElement('td');td.textContent=value??'不明';row.append(td);}
function query(){
 const data=new FormData($('#search')),q={};
 for(const [k,v] of data){if(k!=='prefectures')q[k]=v;}
 q.use_notifications=data.has('use_notifications');if(!q.use_notifications){delete q.notification_item;delete q.notification_status;}
 q.prefectures=data.getAll('prefectures');q.include_suspended=data.has('include_suspended');
 for(const k of ['minimum','maximum'])q[k]=q[k]===''?null:Number(q[k]);
 return {...q,page,sort:$('#sort').value,direction:$('#direction').value};
}
async function runSearch(newQuery=true){
 try{message('検索中…');if(newQuery)lastQuery=query();const q={...lastQuery,page};
 const data=await (await post('/api/search',q)).json();total=data.total;
 $('#result-count').textContent=`検索結果 ${total.toLocaleString()} 件`;
 const body=$('#results');body.replaceChildren();
 for(const r of data.items){const tr=document.createElement('tr');for(const k of ['code','name','prefecture','address','phone','owner','total_beds','general_beds','medical_beds','psychiatric_beds','tuberculosis_beds','infectious_beds','status','reference_date'])cell(tr,r[k]);body.append(tr);}
 if(!data.items.length){const tr=document.createElement('tr');cell(tr,'条件に一致する医療機関はありません');tr.firstChild.colSpan=14;body.append(tr);}
 $('#page-info').textContent=`${page} / ${Math.max(1,Math.ceil(total/50))} ページ`;
 $('#prev').disabled=page<=1;$('#next').disabled=page*50>=total;message('');
 }catch(e){message(e.message);}
}
async function loadDashboard(){
 if(dashboardData)return;
 if(dashboardLoading)return dashboardLoading;
 $('#coverage').textContent='全国47都道府県の集計を読み込んでいます…';
 $('#source-note').textContent='取得元・更新情報を読み込んでいます…';
 dashboardLoading=renderDashboard().catch(e=>{dashboardData=null;$('#coverage').textContent='集計を取得できませんでした。タブを開き直すと再試行できます。';throw e;}).finally(()=>{dashboardLoading=null;});
 return dashboardLoading;
}
async function renderDashboard(){
 const d=await (await api('/api/dashboard')).json();dashboardData=d;
 $('#coverage').textContent=`取得済み ${d.collected.length} / 47 都道府県：${d.collected.join('、')||'なし'}。通常集計から除外：確認中 ${(d.status_counts?.['確認中']||0).toLocaleString()} 件、休止 ${(d.status_counts?.['休止']||0).toLocaleString()} 件。病床等の情報要確認 ${(d.quality_review_count||0).toLocaleString()} 件（現存集計の対象を含む）。${d.note}`;
 $('#source-note').textContent=`未収集：${d.missing.join('、')||'なし'}。取得元ごとの公表基準日は下表をご確認ください。`;
 $('#stats').replaceChildren();for(const [name,n] of [['現存施設',d.total],['病院',d.kind['病院']||0],['診療所',d.kind['診療所']||0],['取得済み都道府県',d.collected.length]]){const el=document.createElement('div');el.className='stat';el.textContent=name;const num=document.createElement('strong');num.textContent=n.toLocaleString();el.append(num);$('#stats').append(el);}
 $('#charts').replaceChildren();
 const groups=[['有床・無床診療所',d.clinic],['病床規模別の病院',d.scales],['精神科病院分類',d.psychiatric],['開設主体（文字列から分類・未分類を含む）',d.owner_type],['都道府県別病院数',Object.fromEntries(Object.entries(d.prefectures).map(([p,c])=>[p,c['病院']]))],['都道府県別診療所数',Object.fromEntries(Object.entries(d.prefectures).map(([p,c])=>[p,c['診療所']]))]];
 for(const [title,counts] of groups){const card=document.createElement('section');card.className='card';const h=document.createElement('h2');h.textContent=title;card.append(h);const max=Math.max(...Object.values(counts),1);for(const [label,n]of Object.entries(counts)){const row=document.createElement('div');row.className='bar-row';const l=document.createElement('span');l.textContent=label;const track=document.createElement('div');track.className='bar-track';const bar=document.createElement('div');bar.className='bar';bar.style.width=`${n/max*100}%`;track.append(bar);const value=document.createElement('span');value.textContent=n;row.append(l,track,value);card.append(row);}$('#charts').append(card);}
 const month=document.createElement('section');month.className='card';const title=document.createElement('h2');title.textContent='月次差分（初回取込は除外）';month.append(title);const description=document.createElement('p');description.textContent=Object.entries(d.monthly).map(([m,c])=>m+'：'+Object.entries(c).map(([k,n])=>`${k} ${n}`).join(' / ')).join('\n')||'比較可能な更新履歴はまだありません。新しいコードだけでは純新規開業と判定しません。';month.append(description);$('#charts').append(month);
 $('#sources').replaceChildren();for(const s of d.sources){const row=document.createElement('tr');for(const k of ['prefecture','filename','reference_date','file_created_date','retrieved_at','record_count','status'])cell(row,s[k]);$('#sources').append(row);}
}
async function loadMembers(){
 const members=await(await api('/api/admin/members')).json();$('#members').replaceChildren();
 for(const m of members){const row=document.createElement('div');row.className='member';const id=document.createElement('code');id.textContent=m.email||m.user_id;const role=document.createElement('select');for(const r of (m.role==='master'?['master']:['user','admin'])){const o=new Option(r==='master'?'マスター':r==='admin'?'管理者':'閲覧者',r);role.add(o);}role.value=m.role;const label=document.createElement('label');label.className='inline';const check=document.createElement('input');check.type='checkbox';check.checked=m.approved;label.append(check,document.createTextNode('利用を承認'));const save=document.createElement('button');save.textContent='保存';if(m.user_id===currentUserId||m.role==='master'){role.disabled=true;check.disabled=true;save.disabled=true;}save.onclick=async()=>{try{await post('/api/admin/members',{user_id:m.user_id,role:role.value,approved:check.checked});message('承認状態を更新しました');}catch(e){message(e.message);}};row.append(id,role,label,save);$('#members').append(row);}
}
async function loadSession(knownUser=null){
 try{const user=knownUser||await(await api('/api/me')).json();currentUserId=user.user_id;canExportAll=['admin','master'].includes(user.role);$$('[data-admin-only]').forEach(b=>{b.disabled=!canExportAll;b.title=canExportAll?'検索条件に関係なく、全施設・全状態・全項目を出力します':'管理者のみ利用できます';});$('#login-panel').hidden=true;$('#workspace').hidden=false;$('#account').textContent=user.email||'';const out=document.createElement('button');out.className='secondary';out.textContent='ログアウト';out.onclick=async()=>{try{await api('/auth/logout',{method:'POST'});location.reload();}catch(e){message(e.message);}};$('#account').append(out);$('#admin-nav').hidden=!['admin','master'].includes(user.role);$('#login-history-nav').hidden=!['admin','master'].includes(user.role);await loadNotificationMetadata();const siteLink=$('#site-settings-link');if(siteLink)siteLink.hidden=user.role!=='master';message(user.password_change_required?'仮パスワードでログインしています。「パスワード変更」からご自身のパスワードに変更してください':'');}
 catch(e){$('#login-panel').hidden=false;$('#workspace').hidden=true;if(!e.message.includes('ログイン')&&e.message!=='メンテナンス中')message(e.message);}
}
$('#login').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;message('ログイン中…');try{const result=await(await api('/auth/login',{method:'POST',body:new FormData(e.target)})).json();$('#login').reset();await loadSession(result.user);}catch(e){if(e.message!=='メンテナンス中')message(e.message);}finally{if(!$('#login').hidden)button.disabled=false;}};
const notificationToggle=$('#use-notifications');
function toggleNotifications(){for(const id of ['#notification-item','#notification-status'])$(id).disabled=!notificationToggle.checked;}
notificationToggle.onchange=toggleNotifications;
$('#search').addEventListener('reset',()=>setTimeout(toggleNotifications,0));
async function loadNotificationMetadata(){
 const d=await(await api('/api/notifications/metadata')).json();
 $('#notification-items').replaceChildren();for(const name of d.items){const o=document.createElement('option');o.value=name;$('#notification-items').append(o);}
 const fmt=value=>{if(!value)return '未取得';const [y,m,d]=value.split('-').map(Number);return `${y}年${m}月${d}日`;};
 function dates(rows){if(!rows.length)return '未取得';const unique=[...new Set(rows.map(r=>r.reference_date))];return unique.length===1?fmt(unique[0]):rows.map(r=>r.prefecture+'：'+fmt(r.reference_date)).join('、');}
 $('#reference-dates').replaceChildren();for(const bureau of d.bureaus){const tr=document.createElement('tr');cell(tr,bureau.bureau);cell(tr,dates(bureau.facility));cell(tr,dates(bureau.notification));$('#reference-dates').append(tr);}
}
$('#search').onsubmit=e=>{e.preventDefault();page=1;runSearch();};
$$('[name=kind]').forEach(r=>r.onchange=()=>{const h=r.value==='病院';$('#search').elements.minimum.value=h?20:'';$('#search').elements.maximum.value=h?200:'';$('#search').elements.clinic_beds.value='';});
$('#prev').onclick=()=>{page--;runSearch(false);};$('#next').onclick=()=>{page++;runSearch(false);};
for(const sel of ['#sort','#direction'])$(sel).onchange=()=>{page=1;runSearch();};
$$('.nav').forEach(b=>b.onclick=async()=>{$$('.nav').forEach(x=>x.classList.toggle('active',x===b));$$('.tab').forEach(t=>t.hidden=t.id!==b.dataset.tab);try{if(b.dataset.tab==='login-history-panel')await loadLoginHistory();else if(b.dataset.tab==='admin-panel')await loadMembers();else if(['dashboard-panel','sources-panel'].includes(b.dataset.tab))await loadDashboard();}catch(e){message(e.message);}});
$$('[data-export]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{message('ファイルを生成中…');const response=await post('/api/export/'+b.dataset.export,b.dataset.export==='full'?{}:lastQuery||query());const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=b.dataset.export==='full'?'全医療機関データ.csv':b.dataset.export==='csv'?'検索結果.csv':b.dataset.export==='master'?'全国医療機関マスター.xlsx':'検索結果.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message(b.dataset.export==='full'?'全医療機関データを出力しました。':'出力しました。対象範囲はファイル内の更新情報をご確認ください。');}catch(e){message(e.message);}finally{b.disabled=b.hasAttribute('data-admin-only')&&!canExportAll;}});
let loginHistoryPage=1;
async function loadLoginHistory(){
 try{
  const form=new FormData($('#login-history-filter'));const filters={email:String(form.get('email')||''),from_date:form.get('from_date')||null,to_date:form.get('to_date')||null,page:loginHistoryPage};
  const d=await(await post('/api/admin/logins',filters)).json();
  $('#login-history-results').replaceChildren();
  for(const r of d.items){const tr=document.createElement('tr');cell(tr,new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(r.created_at)));cell(tr,r.email||r.user_id);$('#login-history-results').append(tr);}
  if(!d.items.length){const tr=document.createElement('tr');cell(tr,'該当するログイン履歴はありません');tr.firstChild.colSpan=2;$('#login-history-results').append(tr);}
  $('#login-history-count').textContent=d.total.toLocaleString()+' 件';$('#login-history-page').textContent=loginHistoryPage+' / '+Math.max(1,Math.ceil(d.total/50))+' ページ';$('#login-history-prev').disabled=loginHistoryPage<=1;$('#login-history-next').disabled=loginHistoryPage*50>=d.total;message('');
 }catch(e){message(e.message);}
}
$('#login-history-filter').onsubmit=e=>{e.preventDefault();loginHistoryPage=1;loadLoginHistory();};
$('#login-history-prev').onclick=()=>{loginHistoryPage--;loadLoginHistory();};
$('#login-history-next').onclick=()=>{loginHistoryPage++;loadLoginHistory();};
loadSession();
