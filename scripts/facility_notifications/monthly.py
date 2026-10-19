"""JST 20th all-bureau refresh, JST 25th failed-bureau retry; staging only."""
import argparse,concurrent.futures,datetime,json,sqlite3,subprocess,sys
from pathlib import Path
from zoneinfo import ZoneInfo
import collect
from build_index import REGIONS

def init(c):
 c.execute('CREATE TABLE IF NOT EXISTS monthly_jobs(month TEXT,bureau TEXT,status TEXT,attempted_at TEXT,details_json TEXT,PRIMARY KEY(month,bureau))')
 c.execute('CREATE TABLE IF NOT EXISTS bureau_current(bureau TEXT PRIMARY KEY,updated_at TEXT,reference_dates_json TEXT,row_count INTEGER,source_json TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS current_rows(bureau TEXT,reference_date TEXT,prefecture_code TEXT,prefecture TEXT,medical_code TEXT,institution_name TEXT,official_item_name TEXT,item_name_search TEXT,calculation_start_iso TEXT,individual_start_iso TEXT,same_date_multiple_receipts INTEGER,date_unknown INTEGER,payload_json TEXT,PRIMARY KEY(bureau,medical_code,item_name_search))')
 c.execute('CREATE INDEX IF NOT EXISTS idx_current_name ON current_rows(item_name_search)');c.commit()

def targets(c,month,day):
 if day not in [20,25]:raise ValueError('JST20日・25日以外は月次入替を実行しません')
 rows=c.execute('SELECT bureau,status FROM monthly_jobs WHERE month=?',(month,)).fetchall();states=dict(rows)
 if day==25:
  if set(states)!=set(REGIONS):raise ValueError('20日の処理状態がありません。25日に全局取得へフォールバックしません。')
  return [b for b in REGIONS if states[b]=='failed']
 with c:
  for b in REGIONS:c.execute("INSERT OR IGNORE INTO monthly_jobs VALUES(?,?,'failed',NULL,?)",(month,b,json.dumps({'reason':'20日の処理が未完了'},ensure_ascii=False)))
 return [b for b in REGIONS if states.get(b)!='success']

def replace_bureau(c,bureau,candidate,month,now,source):
 c.execute('ATTACH DATABASE ? AS candidate',(str(candidate),))
 try:
  expected={f'{n:02}' for n in REGIONS[bureau]};found={r[0] for r in c.execute('SELECT DISTINCT prefecture_code FROM candidate.latest_rows')}
  if found!=expected:raise ValueError('県範囲が不完全。前回分を維持します')
  other=c.execute('SELECT COUNT(*) FROM candidate.latest_rows WHERE bureau<>?',(bureau,)).fetchone()[0]
  count=c.execute('SELECT COUNT(*) FROM candidate.latest_rows').fetchone()[0]
  if other or not count:raise ValueError('異なる局または空データ')
  dates=[r[0] for r in c.execute('SELECT DISTINCT reference_date FROM candidate.latest_rows')]
  unresolved=c.execute("SELECT COUNT(*) FROM candidate.latest_rows WHERE json_extract(payload_json,'$.latest_selection_resolved') IS NOT 1").fetchone()[0]
  if unresolved:raise ValueError(f'比較できる日付が不足して最新判定が未解決の項目が{unresolved}組あります。原本と前回分を維持します')
  if any(not d for d in dates):raise ValueError('基準日がありません。前回分を維持します')
  previous_dates=[r[0] for r in c.execute('SELECT DISTINCT reference_date FROM current_rows WHERE bureau=?',(bureau,))]
  if previous_dates and min(dates)<max(previous_dates):raise ValueError('基準日が前回より古いため前回分を維持します')
  with c:
   c.execute('DELETE FROM current_rows WHERE bureau=?',(bureau,))
   c.execute('INSERT INTO current_rows SELECT * FROM candidate.latest_rows')
   c.execute('INSERT OR REPLACE INTO bureau_current VALUES(?,?,?,?,?)',(bureau,now,json.dumps(dates),count,json.dumps(source,ensure_ascii=False)))
   c.execute("INSERT OR REPLACE INTO monthly_jobs VALUES(?,?,'success',?,?)",(month,bureau,now,json.dumps({'row_count':count,'reference_dates':dates,'replace_mode':'full_bureau_snapshot'},ensure_ascii=False)))
  return count
 finally:c.execute('DETACH DATABASE candidate')

def acquire(bureau,root):
 url=collect.SEEDS[bureau];html,h,u=collect.get(url);(root/'source-pages').mkdir(parents=True,exist_ok=True);(root/'source-pages'/f'{bureau}.html').write_bytes(html)
 r=collect.resolve(bureau,url,html);results=[]
 # The complete XLSX already contains formal names. Optional abbreviation PDFs
 # must not delay or fail the monthly medical snapshot replacement.
 tasks=[(bureau,'full',url) for url in r['full_urls']]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda t:collect.download_one(t,root),tasks))
 (root/'sources.json').write_text(json.dumps([r],ensure_ascii=False,indent=2));(root/'manifest.json').write_text(json.dumps({'files':results,'production_write':False},ensure_ascii=False,indent=2))
 if any(a['status']!='downloaded_validated' for a in results):raise ValueError('取得または形式検証失敗')
 return r

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--date',help='再現試験用の日本日付 YYYY-MM-DD');p.add_argument('--bootstrap-index',help='取得済み全局索引を初回の隔離データへ入れる（スケジュール実行とは区別）');a=p.parse_args()
 root=Path(a.root);root.mkdir(parents=True,exist_ok=True);state=root/'state';state.mkdir(exist_ok=True);db=state/'current.sqlite'
 now=datetime.datetime.now(ZoneInfo('Asia/Tokyo'));date=datetime.date.fromisoformat(a.date) if a.date else now.date();month=date.strftime('%Y-%m');c=sqlite3.connect(db);init(c)
 if a.bootstrap_index:
  # Bootstrap is a local preview, and must not mark a future 20th run as completed.
  src=sqlite3.connect(Path(a.bootstrap_index).resolve().as_uri()+'?mode=ro',uri=True)
  if {r[0] for r in src.execute('SELECT DISTINCT bureau FROM latest_rows')}!=set(REGIONS):raise ValueError('初回データに8局がありません')
  count=0
  with c:
   if c.execute('SELECT COUNT(*) FROM current_rows').fetchone()[0]:raise ValueError('初回投入先が空ではありません')
   rows=src.execute('SELECT * FROM latest_rows')
   while batch:=rows.fetchmany(2000):
    c.executemany('INSERT INTO current_rows VALUES('+','.join('?'*13)+')',batch);count+=len(batch)
  src.close();print(json.dumps({'bootstrap_rows':count,'scheduled_jobs_marked_complete':False,'production_write':False}));c.close();return
 todo=targets(c,month,date.day);results=[]
 for bureau in todo:
  stamp=datetime.datetime.now(ZoneInfo('Asia/Tokyo')).isoformat();run=root/'attempts'/month/(str(date.day)+'-'+bureau);run.mkdir(parents=True,exist_ok=True)
  try:
   source=acquire(bureau,run)
   with (run/'parse.log').open('w') as log:
    subprocess.run([sys.executable,str(Path(__file__).with_name('build_index.py')),'--input',str(run),'--bureau',bureau],check=True,stdout=log,stderr=subprocess.STDOUT)
   candidate=run/'medical-notifications.sqlite'
   with (run/'latest.log').open('w') as log:subprocess.run([sys.executable,str(Path(__file__).with_name('latest.py')),'--database',str(candidate)],check=True,stdout=log,stderr=subprocess.STDOUT)
   count=replace_bureau(c,bureau,candidate,month,stamp,source);results.append({'bureau':bureau,'status':'success','rows':count})
  except Exception as e:
   with c:c.execute("INSERT OR REPLACE INTO monthly_jobs VALUES(?,?,'failed',?,?)",(month,bureau,stamp,json.dumps({'error':type(e).__name__+': '+str(e),'previous_snapshot_preserved':True},ensure_ascii=False)))
   results.append({'bureau':bureau,'status':'failed','error':str(e)})
  print(json.dumps(results[-1],ensure_ascii=False),flush=True)
 report={'date_jst':date.isoformat(),'month':month,'attempted_bureaus':todo,'results':results,'production_write':False,'storage':'isolated SQLite; full replacement per successful bureau','jobs':[dict(zip(['bureau','status','attempted_at','details_json'],r)) for r in c.execute('SELECT bureau,status,attempted_at,details_json FROM monthly_jobs WHERE month=?',(month,))]}
 (state/'last-run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));c.close()
 if any(r['status']=='failed' for r in results):raise SystemExit(1)
if __name__=='__main__':main()
