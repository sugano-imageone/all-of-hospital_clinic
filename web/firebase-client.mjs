/* Migration-only transport. Not loaded by the production Pages build. */
import {streamingZip} from './streaming-zip.mjs';
export function createFirebaseClient(config, fetcher=globalThis.fetch.bind(globalThis)) {
 const api=new URL(config.apiUrl);
 if(api.protocol!=='https:'||api.username||api.password||api.search||api.hash||api.pathname!=='/')throw Error('Invalid API URL');
 if(typeof config.firebaseApiKey!=='string'||!config.firebaseApiKey.startsWith('AIza'))throw Error('Public Firebase web API key required');
 let session=null,refreshing=null;
 const jsonHeaders={'Content-Type':'application/json'};
 const failure=r=>Object.assign(new Error(r.status===401?'ログインを確認してください':r.status===403?'管理者の利用承認が必要です':r.status===409?'データが更新されました。出力を最初からやり直してください':'接続に失敗しました'),{status:r.status});
 async function identity(method,body) {
  const r=await fetcher('https://identitytoolkit.googleapis.com/v1/accounts:'+method+'?key='+encodeURIComponent(config.firebaseApiKey),{method:'POST',headers:jsonHeaders,body:JSON.stringify(body),redirect:'error',cache:'no-store'});
  if(!r.ok)throw failure(r);
  return r.json();
 }
 async function refresh() {
  if(!session?.refreshToken)throw Object.assign(new Error('ログインが必要です'),{status:401});
  if(!refreshing)refreshing=(async()=>{
   const r=await fetcher('https://securetoken.googleapis.com/v1/token?key='+encodeURIComponent(config.firebaseApiKey),{
    method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'refresh_token',refresh_token:session.refreshToken}),redirect:'error',cache:'no-store'});
   if(!r.ok){session=null;throw failure(r);}
   const data=await r.json();session={idToken:data.id_token,refreshToken:data.refresh_token};
  })().finally(()=>{refreshing=null;});
  return refreshing;
 }
 async function request(path,body,method=body===undefined?'GET':'POST',retry=true) {
  if(!session)throw Object.assign(new Error('ログインが必要です'),{status:401});
  if(!path.startsWith('/api/'))throw Error('Invalid API path');
  const r=await fetcher(new URL(path,api),{method,headers:{...jsonHeaders,Authorization:'Bearer '+session.idToken},body:body===undefined?undefined:JSON.stringify(body),redirect:'error',cache:'no-store'});
  if(r.status===401&&retry){await refresh();return request(path,body,method,false);}
  if(!r.ok)throw failure(r);
  return r.json();
 }
 async function login(email,password) {
  session=null;
  const data=await identity('signInWithPassword',{email,password,returnSecureToken:true});
  session={idToken:data.idToken,refreshToken:data.refreshToken};
  try {
   const me=await request('/api/me');
   await request('/api/login',{});
   return me;
  } catch(e){session=null;throw e;}
 }
 async function allRows(filters={},onProgress=()=>{}) {
  // Sequential pages bound API work and let a revision change abort the whole export.
  const first=await request('/api/export/page',{...filters,page:1,page_size:1000});
  const rows=[...first.items];onProgress(rows.length,first.total);
  for(let page=2;rows.length<first.total;page++) {
   const cursor=(!filters.sort||filters.sort==='code')&&filters.direction!=='desc'?{after_code:rows.at(-1).code}:{};
   const next=await request('/api/export/page',{...filters,...cursor,page,page_size:1000,revision:first.revision,include_total:false});
   if(next.revision!==first.revision||!next.items.length)throw Error('出力対象の版を確認できません。最初からやり直してください');
   rows.push(...next.items);onProgress(rows.length,first.total);
  }
  if(rows.length!==first.total)throw Error('出力件数が一致しません');
  return rows;
 }
 return {
  login,refresh,request,allRows,
  logout(){session=null;},
  async changePassword(password) {
   if(!session)throw Error('ログインが必要です');
   if(typeof password!=='string'||password.length<12||new TextEncoder().encode(password).length>72)throw Error('パスワードは12文字以上、72バイト以下で設定してください');
   const data=await identity('update',{idToken:session.idToken,password,returnSecureToken:true});
   session={idToken:data.idToken,refreshToken:data.refreshToken};
  }
 };
}

export function csv(rows,columns) {
 const safe=v=>{
  let value=String(v??'');
  if(/^[\s]*[=+\-@\t\r]/.test(value))value="'"+value;
  return '"'+value.replaceAll('"','""')+'"';
 };
 return '\ufeff'+[columns.map(c=>safe(c[1])).join(','),...rows.map(r=>columns.map(c=>safe(r[c[0]])).join(','))].join('\r\n');
}

export async function xlsx(rows,columns) {
 const members=[];
 const file=(name,text)=>members.push({name,async *chunks(){yield text;}});
 const esc=v=>String(v??'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
 const col=n=>{let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
 const sheets=[['病院','病院'],['診療所','診療所']];
 for(let i=0;i<sheets.length;i++) {
  const kind=sheets[i][1];
  members.push({name:`xl/worksheets/sheet${i+1}.xml`,async *chunks(){
   yield '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>';
   let y=0,buffer='';
   const rowXml=values=>{y++;return '<row r="'+y+'">'+values.map((v,x)=>typeof v==='number'&&Number.isFinite(v)?'<c r="'+col(x)+y+'"><v>'+v+'</v></c>':'<c r="'+col(x)+y+'" t="inlineStr"><is><t xml:space="preserve">'+esc(v)+'</t></is></c>').join('')+'</row>';};
   buffer+=rowXml(columns.map(c=>c[1]));
   for(const r of rows)if(r.kind===kind) {
    buffer+=rowXml(columns.map(c=>r[c[0]]));
    if(y%200===0){yield buffer;buffer='';}
   }
   if(buffer)yield buffer;
   yield '</sheetData></worksheet>';
  }});
 }
 file('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')+'</Types>');
 file('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>');
 file('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+sheets.map(([name],i)=>`<sheet name="${name}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')+'</sheets></workbook>');
 file('xl/_rels/workbook.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')+'</Relationships>');
 return streamingZip(members);
}
