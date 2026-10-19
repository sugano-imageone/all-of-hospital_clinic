"""Publish validated medical-only snapshots; never write facility masters or Auth.
Each bureau switches atomically only after complete upload and DB validation.
Previous snapshots remain available for rollback via notification_current.
"""
import argparse,datetime,json,os,sqlite3,time,urllib.request,urllib.error
from pathlib import Path
from build_index import REGIONS

FULL_NAMES={b:b+('厚生支局' if b=='四国' else '厚生局') for b in REGIONS}
SHORT_NAMES={v:k for k,v in FULL_NAMES.items()}
PROJECT='jnoukmkrtmpgchdpcskc'
def call(name,payload,management=False):
 if management:
  args=','.join(k+'=>'+("NULL" if v is None else "'"+json.dumps(v,ensure_ascii=False).replace("'","''")+"'::jsonb" if isinstance(v,(dict,list)) else ('true' if v else 'false') if isinstance(v,bool) else str(v) if isinstance(v,int) else "'"+str(v).replace("'","''")+"'") for k,v in payload.items())
  query="select set_config('request.jwt.claim.role','service_role',true); select public."+name+'('+args+') as result;'
  url=f'https://api.supabase.com/v1/projects/{PROJECT}/database/query'
  data={'query':query,'read_only':False};headers={'Authorization':'Bearer '+os.environ['SUPABASE_ACCESS_TOKEN']}
 else:
  base=os.environ['SUPABASE_URL'].rstrip('/')
  if base!=f'https://{PROJECT}.supabase.co':raise ValueError('Unexpected Supabase project')
  key=os.environ['SUPABASE_SECRET_KEY'];url=base+'/rest/v1/rpc/'+name;data=payload;headers={'apikey':key,**({'Authorization':'Bearer '+key} if key.startswith('eyJ') else {})}
 headers['Content-Type']='application/json'
 req=urllib.request.Request(url,data=json.dumps(data,ensure_ascii=False).encode(),headers=headers)
 for attempt in range(3):
  try:
   with urllib.request.urlopen(req,timeout=120) as r:result=json.loads(r.read() or b'null')
   return result[0]['result'] if management else result
  except urllib.error.HTTPError as e:
   # Do not log headers, request contents, or secrets.
   if e.code not in (429,500,502,503,504):raise RuntimeError(f'{name}: HTTP {e.code}: '+e.read().decode()[:500]) from None
   if attempt==2:raise RuntimeError(f'{name}: HTTP {e.code}') from None
   time.sleep(2*(attempt+1))

def publish_bureau(db,bureau,source,management=False):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 dates={str(int(pref)):date for pref,date in c.execute('select distinct prefecture_code,reference_date from source_rows where bureau=?',(bureau,))}
 if set(dates)!={str(n) for n in REGIONS[bureau]}:raise ValueError('Incomplete prefecture coverage')
 if c.execute("select count(*) from latest_rows where bureau=? and json_extract(payload_json,'$.latest_selection_resolved') is not 1",(bureau,)).fetchone()[0]:raise ValueError('Unresolved latest filing dates')
 count=c.execute('select count(*) from latest_rows where bureau=?',(bureau,)).fetchone()[0]
 facilities=c.execute('select count(distinct medical_code) from source_rows where bureau=?',(bureau,)).fetchone()[0]
 ident=call('begin_notification_snapshot',{'p_metadata':{'bureau':FULL_NAMES[bureau],'reference_dates':dates,'source':source,'filings':count,'facilities':facilities}},management)
 cursor=c.execute('select distinct medical_code code,cast(prefecture_code as integer) prefecture_code,reference_date from source_rows where bureau=?',(bureau,))
 while rows:=cursor.fetchmany(4000):
  call('upload_notification_batch',{'p_snapshot':ident,'p_facilities':[dict(r) for r in rows]},management)
 cursor=c.execute('select medical_code code,item_name_search item_key,official_item_name,calculation_start_iso calculation_start,payload_json from latest_rows where bureau=?',(bureau,))
 uploaded=0;batch=0
 while rows:=cursor.fetchmany(1500):
  data=[]
  for row in rows:
   r=dict(row);payload=json.loads(r.pop('payload_json'));r['receipt_date']=payload.get('receipt_date_iso') or None;r['calculation_start']=r['calculation_start'] or None;r['date_basis']=payload['date_basis']
   # Full details and original source rows stay in the verified source artifact.
   # Keep multiple tied receipts explicit; never choose an arbitrary receipt.
   r['payload']={k:payload[k] for k in ['latest_selection_resolved','selection_status','same_date_multiple_receipts','source_detail_rows','older_source_rows_removed']}
   if payload['same_date_multiple_receipts']:
    r['payload']['receipts']=[{k:v['fields'].get(k) for k in ['受理記号','受理番号','算定開始年月日']} for v in payload['versions']]
   data.append(r)
  call('upload_notification_batch',{'p_snapshot':ident,'p_filings':data},management);uploaded+=len(data);batch+=1
  if batch%20==0:print(json.dumps({'bureau':bureau,'uploaded':uploaded,'expected':count}),flush=True)
 result=call('finish_notification_snapshot',{'p_snapshot':ident},management);c.close();return {'bureau':FULL_NAMES[bureau],'snapshot_id':ident,**result}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--management',action='store_true');a=p.parse_args();root=Path(a.root)
 # Initial acquisition has one national database: split sources without changing them.
 import concurrent.futures
 manifest=json.loads((root/'manifest.json').read_text())['files']
 def worker(bureau):
  sources=[r for r in manifest if r['bureau']==bureau and r['role']=='full']
  result=publish_bureau(root/'medical-notifications.sqlite',bureau,sources,a.management)
  print(json.dumps(result,ensure_ascii=False),flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:reports=list(pool.map(worker,REGIONS))
 (root/'production-publication.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
