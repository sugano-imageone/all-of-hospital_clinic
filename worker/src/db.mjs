// The browser never receives the database URL/token or an arbitrary SQL endpoint.
export async function execute(env, sql, args=[]) {
  const base=env.TURSO_DATABASE_URL?.replace(/^libsql:/,'https:');
  const url=new URL(base);
  if(url.protocol!=='https:' || !url.hostname.endsWith('.turso.io') || url.username || url.password || url.port || url.search || url.hash || url.pathname!=='/') throw new Error('Invalid database endpoint');
  if(!env.TURSO_AUTH_TOKEN) throw new Error('Missing database secret');
  const typed=args.map(v=>v===null?{type:'null'}:typeof v==='number'?{type:'integer',value:String(v)}:{type:'text',value:String(v)});
  const response=await fetch(new URL('/v2/pipeline',url),{
    method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),
    headers:{Authorization:'Bearer '+env.TURSO_AUTH_TOKEN,'Content-Type':'application/json'},
    body:JSON.stringify({requests:[{type:'execute',stmt:{sql,args:typed}},{type:'close'}]})
  });
  if(!response.ok) throw new Error('Database unavailable');
  const body=await response.json();
  const result=body.results?.[0];
  if(result?.type!=='ok' || !result.response?.result) throw new Error('Database request failed');
  const data=result.response.result;
  return data.rows.map(row=>Object.fromEntries(data.cols.map((col,i)=>[col.name,row[i].type==='null'?null:row[i].type==='integer'?Number(row[i].value):row[i].value])));
}
