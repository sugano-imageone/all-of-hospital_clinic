"""Validated official medical snapshots -> atomic Supabase bureau replacement.

Initial bootstrap is explicit and does not fabricate a monthly job success.
Monthly status lives in Supabase; failed transfers are retried on JST 25th.
"""
import argparse,datetime,json,os,sqlite3,subprocess,sys,time,uuid
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
import monthly
from build_index import REGIONS

NAMES={b:b+('厚生支局' if b=='四国' else '厚生局') for b in REGIONS}

class Remote:
 def __init__(self):
  self.token=os.getenv('SUPABASE_ACCESS_TOKEN')
  self.url=os.getenv('SUPABASE_URL','https://jnoukmkrtmpgchdpcskc.supabase.co').rstrip('/')
  self.key=os.getenv('SUPABASE_SECRET_KEY')
  if not self.token and not self.key:raise ValueError('管理APIまたは取込専用シークレットが必要です')
  if self.token and self.url!='https://jnoukmkrtmpgchdpcskc.supabase.co':raise ValueError('Management API project mismatch')
  self.client=httpx.Client(timeout=120)
 def rpc(self,name,args=None):
  args=args or {}
  for attempt in range(3):
   try:
    if self.token:
     # Parameter names/functions are internal constants. JSON strings are SQL quoted.
     lit=lambda v:"'"+json.dumps(v,ensure_ascii=False).replace("'","''")+"'::jsonb"
     params=','.join(k+'=>'+(lit(v) if isinstance(v,(dict,list)) else "'"+str(v).replace("'","''")+"'" if v is not None else 'null') for k,v in args.items())
     sql="select set_config('request.jwt.claim.role','service_role',true);select public."+name+'('+params+') as result;'
     r=self.client.post('https://api.supabase.com/v1/projects/jnoukmkrtmpgchdpcskc/database/query',headers={'Authorization':'Bearer '+self.token},json={'query':sql,'read_only':False})
    else:
     headers={'apikey':self.key}
     if self.key.startswith('eyJ'):headers['Authorization']='Bearer '+self.key
     r=self.client.post(self.url+'/rest/v1/rpc/'+name,headers=headers,json=args)
    if r.status_code>=500:raise httpx.HTTPStatusError('remote unavailable',request=r.request,response=r)
    if not r.is_success:
     # Only the SQL message is needed locally; never print auth headers.
     raise RuntimeError('RPC '+name+' HTTP '+str(r.status_code)+': '+r.text[:500].replace(self.token or self.key,'[REDACTED]'))
    if self.token:
     data=r.json();return data[0]['result'] if data else None
    return r.json() if r.content else None
   except (httpx.TimeoutException,httpx.NetworkError,httpx.HTTPStatusError):
    if attempt==2:raise RuntimeError('RPC '+name+' connection failed') from None
    time.sleep(1+attempt)


def validate(c,bureau):
 c.row_factory=sqlite3.Row
 expected=set(REGIONS[bureau]);found={int(r[0]) for r in c.execute('select distinct prefecture_code from source_rows where bureau=?',(bureau,))}
 if found!=expected:raise ValueError('incomplete medical facility coverage')
 if c.execute("select count(*) from latest_rows where bureau=? and json_extract(payload_json,'$.latest_selection_resolved') IS NOT 1",(bureau,)).fetchone()[0]:raise ValueError('unresolved latest filings')
 facilities=[dict(code=r[0],prefecture_code=int(r[1]),reference_date=r[2]) for r in c.execute('select medical_code,prefecture_code,min(reference_date) from source_rows where bureau=? group by medical_code,prefecture_code',(bureau,))]
 refs={}
 for r in facilities:
  k=str(r['prefecture_code']);d=r['reference_date']
  datetime.date.fromisoformat(d)
  if d>datetime.datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat():raise ValueError('future reference date')
  if k in refs and refs[k]!=d:raise ValueError('multiple reference dates within prefecture')
  refs[k]=d
 count=c.execute('select count(*) from latest_rows where bureau=?',(bureau,)).fetchone()[0]
 if not count or not facilities:raise ValueError('empty medical snapshot')
 return facilities,refs,count


def publish(remote,database,bureau,root):
 c=sqlite3.connect(database);facilities,refs,count=validate(c,bureau)
 manifests=[]
 for p in ['sources.json','manifest.json','validation.json','latest-validation.json']:
  f=root/p
  if f.exists():manifests.append({p:json.loads(f.read_text())})
 metadata={'bureau':NAMES[bureau],'reference_dates':refs,'filings':count,'facilities':len(facilities),'source':manifests}
 # Resume an interrupted initial transfer without activating a partial snapshot.
 state=root/('upload-'+bureau+'.json')
 fingerprint=json.dumps(metadata,sort_keys=True,ensure_ascii=False)
 saved=json.loads(state.read_text()) if state.exists() else {}
 if saved.get('fingerprint')==fingerprint:
  snapshot=saved['snapshot'];start=saved.get('filings_uploaded',0)
  if saved.get('complete'):return saved['result']
 else:
  snapshot=remote.rpc('begin_notification_snapshot',{'p_metadata':metadata});start=0;saved={'fingerprint':fingerprint,'snapshot':snapshot,'filings_uploaded':0};state.write_text(json.dumps(saved,ensure_ascii=False))
 for i in range(0,len(facilities),2000):remote.rpc('upload_notification_batch',{'p_snapshot':snapshot,'p_facilities':facilities[i:i+2000]})
 cursor=c.execute('select * from latest_rows where bureau=? order by medical_code,item_name_search limit -1 offset ?',(bureau,start));sent=start
 while rows:=cursor.fetchmany(1000):
  batch=[]
  for r in rows:
   payload=json.loads(r['payload_json'])
   # The institution identity comes from the existing facility master. Preserve
   # selected filing fields/details/provenance without duplicating addresses etc.
   for version in payload.get('versions',[]):
    version['fields']={k:v for k,v in version['fields'].items() if k in ['受理記号','受理番号','受理日','受理年月日','算定開始年月日','個別有効開始年月日']}
   # Store trusted source date kinds and original versions; do not invent receipt dates.
   batch.append({'code':r['medical_code'],'item_key':r['item_name_search'],'official_item_name':r['official_item_name'],'date_basis':payload['date_basis'],'receipt_date':payload['receipt_date_iso'] or None,'calculation_start':r['calculation_start_iso'] or None,'payload':payload})
  remote.rpc('upload_notification_batch',{'p_snapshot':snapshot,'p_filings':batch});sent+=len(batch)
  saved['filings_uploaded']=sent;state.write_text(json.dumps(saved,ensure_ascii=False))
  print(json.dumps({'bureau':bureau,'uploaded':sent,'total':count}),flush=True)
 result=remote.rpc('finish_notification_snapshot',{'p_snapshot':snapshot})
 saved.update(complete=True,result=result);state.write_text(json.dumps(saved,ensure_ascii=False));c.close();return result


def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--bootstrap-index');p.add_argument('--bureau',choices=list(REGIONS));a=p.parse_args()
 root=Path(a.root);root.mkdir(parents=True,exist_ok=True);remote=Remote();results=[]
 if a.bootstrap_index:
  for bureau in ([a.bureau] if a.bureau else REGIONS):results.append({'bureau':bureau,**publish(remote,a.bootstrap_index,bureau,root)})
 else:
  todo=remote.rpc('notification_monthly_targets');reverse={v:k for k,v in NAMES.items()}
  for full in todo:
   bureau=reverse[full];run=root/'attempts'/datetime.datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d')/bureau;run.mkdir(parents=True,exist_ok=True)
   try:
    monthly.acquire(bureau,run)
    with (run/'parse.log').open('w') as log:subprocess.run([sys.executable,str(Path(__file__).with_name('build_index.py')),'--input',str(run),'--bureau',bureau],check=True,stdout=log,stderr=subprocess.STDOUT)
    database=run/'medical-notifications.sqlite'
    with (run/'latest.log').open('w') as log:subprocess.run([sys.executable,str(Path(__file__).with_name('latest.py')),'--database',str(database)],check=True,stdout=log,stderr=subprocess.STDOUT)
    result=publish(remote,database,bureau,run)
    remote.rpc('notification_job_result',{'p_bureau':full,'p_success':True,'p_details':result});results.append({'bureau':bureau,'status':'success'})
   except Exception as e:
    # A failed bureau leaves the current pointer unchanged.
    detail={'error':type(e).__name__+': '+str(e),'previous_snapshot_preserved':True}
    remote.rpc('notification_job_result',{'p_bureau':full,'p_success':False,'p_details':detail});results.append({'bureau':bureau,'status':'failed'})
 (root/'production-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False))
 if any(r.get('status')=='failed' for r in results):raise SystemExit(1)
if __name__=='__main__':main()
