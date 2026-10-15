"""Monthly worker with persistent bureau status. Missing/unsupported coverage is failure.

sources.json is an approved manifest (never blindly select a new-designation-only file).
Each entry: bureau, prefecture_code, url, adapter=kyushu-code-content-v1, scope=full-medical.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from app.domain import REGIONS,PREFECTURES
from app.importers.discover import fetch,current_links
from app.importers.kyushu import parse_archive,VERSION
from app.importers.national import parse_region
from app.importers.cli import apply_snapshot
from app.store import Supabase

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',default='config/sources.json')
    parser.add_argument('--apply',action='store_true');parser.add_argument('--scheduled',action='store_true')
    parser.add_argument('--output',default='artifacts/monthly.json');args=parser.parse_args()
    now=datetime.now(ZoneInfo('Asia/Tokyo'));month=now.date().replace(day=1).isoformat()
    if args.scheduled and now.day not in range(20,26):
        print('JST20〜25日以外のため実行しません');return
    manifest=json.loads(Path(args.manifest).read_text())['sources']
    db=Supabase(service=True) if args.apply else None
    results=[]
    try:
        done=set()
        if db:
            jobs=db.all('update_jobs',[('month','eq.'+month),('status','eq.success'),('order','bureau.asc')])
            done={j['bureau'] for j in jobs}
        for bureau,prefs in REGIONS.items():
            if bureau in done:
                results.append({'bureau':bureau,'status':'previously_succeeded'});continue
            entries=[s for s in manifest if s['bureau']==bureau]
            errors=[];completed=[]
            if len(entries)!=1 or entries[0].get('resolver')!='official-current-v1' or entries[0].get('scope')!='full-medical':
                errors.append('検証済みの全件配布元resolverが未登録')
            else:
                with TemporaryDirectory() as tmp:
                    try:
                        links=current_links(bureau);records=[];sources=[]
                        for i,url in enumerate(links):
                            from urllib.parse import urlsplit
                            path=Path(tmp)/('source'+str(i)+Path(urlsplit(url).path).suffix)
                            path.write_bytes(fetch(url))
                            if bureau=='九州厚生局':
                                data,meta=parse_archive(path,prefs[i],now.isoformat())
                                for src in meta:src.update(prefecture=PREFECTURES[prefs[i]],source_url=url)
                            else:
                                data,meta,layout_errors=parse_region(path,bureau,now.isoformat(),url)
                                if layout_errors:raise ValueError('未解決のファイル構造。既存データを維持')
                            records.extend(data);sources.extend(meta)
                        if sorted({r['prefecture_code'] for r in records})!=sorted(prefs):
                            raise ValueError('担当県の収集が不完全')
                        if any(src['reference_date'][:7]!=now.strftime('%Y-%m') for src in sources):
                            raise ValueError('公表基準日が対象月ではありません')
                        # Validate the whole bureau before the first prefectural transaction.
                        for src in sources:
                            completed.append(apply_snapshot(db,records,src) if db else {'validated':src['record_count']})
                    except Exception as e:
                        # Do not emit HTTP bodies/headers or credential values.
                        errors.append(f'{type(e).__name__}: 取得・解析・公表月・県の検証に失敗。直前データを維持')
            result={'bureau':bureau,'status':'failed' if errors else 'success','errors':errors,'completed':completed}
            results.append(result)
            if db:
                db.request('POST','/rest/v1/update_jobs',params={'on_conflict':'month,bureau'},
                           headers={'Prefer':'resolution=merge-duplicates'},json={'month':month,'bureau':bureau,
                           'status':result['status'],'attempted_at':now.isoformat(),'details':result})
    finally:
        if db: db.close()
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'month':month,'apply':args.apply,'results':results},ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False))
    if any(r['status']=='failed' for r in results): raise SystemExit(1)
if __name__=='__main__': main()
