"""Offline capacity/query/browser-export trial using actual national records."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.exports import COLUMNS


def run(public, notifications, output):
    output.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(public.resolve().as_uri()+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    c.execute('ATTACH DATABASE ? AS notifications',(str(notifications),))
    # Persistent production pointers are untouched; TEMP views select a local trial version.
    c.execute('CREATE TEMP TABLE notification_current(bureau TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL)')
    c.execute("INSERT INTO notification_current SELECT bureau,id FROM notifications.notification_snapshots WHERE id LIKE 'trial-0-%'")
    for table in ('notification_snapshots','notification_facilities','notification_items','notification_filings'):
        c.execute('CREATE TEMP VIEW '+table+' AS SELECT * FROM notifications.'+table)
    c.commit()
    trials=[]
    for q in ({'kind':'病院'},{'kind':'診療所'},{'kind':'診療所','use_notifications':True,'notification_item':'医療安全対策加算１','notification_status':'filed'}, {'kind':'診療所','use_notifications':True,'notification_item':'医療安全対策加算１','notification_status':'unfiled'}):
        script="import {buildSearch} from './worker/src/search.mjs';console.log(JSON.stringify(buildSearch(JSON.parse(process.argv[1]))));"
        query=json.loads(subprocess.check_output(['node','--input-type=module','-e',script,json.dumps(q,ensure_ascii=False)],cwd=ROOT))
        start=time.monotonic();count=c.execute(query['countSql'],query['countArgs']).fetchone()[0]
        items=c.execute(query['sql'],query['args']).fetchall()
        trials.append({'filters':q,'count':count,'first_page':len(items),'seconds':time.monotonic()-start,
                       'plan':[list(r) for r in c.execute('EXPLAIN QUERY PLAN '+query['countSql'],query['countArgs'])]})
    fields=[col[0] for col in COLUMNS]
    rows=[dict(r) for r in c.execute('SELECT '+','.join(fields)+" FROM medical_institutions WHERE status='現存' ORDER BY code")]
    payload=output/'export-input.json';payload.write_text(json.dumps({'rows':rows,'columns':COLUMNS},ensure_ascii=False,separators=(',',':')))
    script="""import fs from 'node:fs';
import {csv,xlsx} from './web/firebase-client.mjs';
import JSZip from './web/node_modules/jszip/lib/index.js';
const p=process.argv[1],data=JSON.parse(fs.readFileSync(p+'/export-input.json','utf8'));
const started=performance.now();
fs.writeFileSync(p+'/national.csv',csv(data.rows,data.columns));
const bytes=await xlsx(data.rows,data.columns,JSZip);fs.writeFileSync(p+'/national.xlsx',bytes);
fs.writeFileSync(p+'/export-metrics.json',JSON.stringify({rows:data.rows.length,seconds:(performance.now()-started)/1000,node_max_rss_kib:process.resourceUsage().maxRSS,csv_bytes:fs.statSync(p+'/national.csv').size,xlsx_bytes:bytes.length}));
"""
    subprocess.run(['node','--input-type=module','-e',script,str(output.resolve())],cwd=ROOT,check=True)
    import openpyxl
    workbook=openpyxl.load_workbook(output/'national.xlsx',read_only=True,data_only=False)
    sheet_counts={sheet.title:sum(1 for _ in sheet.iter_rows())-1 for sheet in workbook}
    workbook.close()
    with (output/'national.csv').open(encoding='utf-8-sig',newline='') as f:
        csv_count=sum(1 for _ in csv.reader(f))-1
    assert csv_count==len(rows)==sum(sheet_counts.values())
    report={'queries':trials,'export':json.loads((output/'export-metrics.json').read_text()),'xlsx_sheet_rows':sheet_counts,'csv_verified_rows':csv_count,
            'limits':'Local SQLite and Node trial; does not measure Turso billed reads or Worker CPU or a real browser memory limit.', 'production_write':False}
    (output/'benchmark-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    payload.unlink()
    c.close()
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--public',type=Path,required=True);p.add_argument('--notifications',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.public,a.notifications,a.output)
