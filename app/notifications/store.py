"""SQLite development store. Original rows survive; repeated imports do not multiply data."""
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
import zipfile
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import openpyxl
from app.notifications.guards import compare,validate_context
from app.notifications.sources import PREFECTURES
from app.importers.discover import official_url

VERSION='notifications-v2'
HEADERS=['項番','都道府県コード','都道府県名','区分','医療機関番号','併設医療機関番号','医療機関記号番号','医療機関名称','医療機関所在地（郵便番号）','医療機関所在地（住所）','電話番号','FAX番号','病床数','受理届出名称','受理記号','受理番号','算定開始年月日','個別有効開始年月日','備考（見出し）','備考（データ）','市町村コード','市町村名','種別コード','種別']
SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS institutions(code TEXT PRIMARY KEY CHECK(length(code)=10 AND code NOT GLOB '*[^0-9]*'),prefecture INTEGER NOT NULL,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,prefecture INTEGER NOT NULL,reference_date TEXT NOT NULL,created_label TEXT NOT NULL,logical_sha TEXT NOT NULL,source_url TEXT NOT NULL,file_sha TEXT NOT NULL,filename TEXT NOT NULL,sheet TEXT NOT NULL,headers_json TEXT NOT NULL,imported_at TEXT NOT NULL,row_count INTEGER NOT NULL DEFAULT 0,filing_count INTEGER NOT NULL DEFAULT 0,UNIQUE(prefecture,reference_date,logical_sha));
CREATE TABLE IF NOT EXISTS filings(id TEXT PRIMARY KEY,code TEXT NOT NULL REFERENCES institutions(code),official_name TEXT NOT NULL,signature TEXT,receipt_number TEXT,start_date TEXT,valid_from TEXT);
CREATE INDEX IF NOT EXISTS filings_code ON filings(code);
CREATE UNIQUE INDEX IF NOT EXISTS filings_identity ON filings(code,official_name,coalesce(signature,''),coalesce(receipt_number,''),coalesce(start_date,''),coalesce(valid_from,''));
CREATE TABLE IF NOT EXISTS source_rows(snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,row_number INTEGER NOT NULL,code TEXT REFERENCES institutions(code),filing_id TEXT REFERENCES filings(id),row_scope TEXT NOT NULL,raw_json TEXT NOT NULL,PRIMARY KEY(snapshot_id,row_number));
CREATE INDEX IF NOT EXISTS source_rows_filing ON source_rows(filing_id,snapshot_id);
CREATE TABLE IF NOT EXISTS source_sheets(file_sha TEXT NOT NULL,sheet TEXT NOT NULL,parser_version TEXT NOT NULL,snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),PRIMARY KEY(file_sha,sheet,parser_version));
CREATE TABLE IF NOT EXISTS snapshot_institutions(snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),code TEXT NOT NULL REFERENCES institutions(code),PRIMARY KEY(snapshot_id,code));
CREATE INDEX IF NOT EXISTS snapshot_institutions_code ON snapshot_institutions(code,snapshot_id);
CREATE TABLE IF NOT EXISTS snapshot_reviews(snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id),old_snapshot_id INTEGER REFERENCES snapshots(id),status TEXT NOT NULL CHECK(status IN ('accepted','review_required','approved','historical')),reasons_json TEXT NOT NULL,metrics_json TEXT NOT NULL,context_json TEXT NOT NULL,approval_note TEXT);
CREATE TABLE IF NOT EXISTS current_snapshots(prefecture INTEGER PRIMARY KEY,snapshot_id INTEGER NOT NULL REFERENCES snapshots(id));
'''
def dumps(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),default=str)
def connect(path):
    path=Path(path)
    if path.suffix not in ['.sqlite','.sqlite3','.db']:raise ValueError('ローカルSQLiteファイルを指定してください')
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=60);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL');db.executescript(SCHEMA)
    old=db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
    if old and old[0]!=VERSION:raise ValueError('異なる開発DBの定義です')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('schema',?)",(VERSION,));db.commit();return db

def reference_date(label):
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',label))
    m=re.search(r'令和(元|\d+)年(\d+)月(\d+)日現在',text)
    if not m:raise ValueError('帳票の基準日を確認できません')
    return date(2018+(1 if m[1]=='元' else int(m[1])),int(m[2]),int(m[3])).isoformat()
def code10(pref,value):
    text=str(value or '').strip()
    if not re.fullmatch(r'\d{7}',text):raise ValueError('医科7桁コードが不正です: '+text)
    return f'{pref:02}1'+text

def workbooks(path,bureau):
    path=Path(path)
    if path.suffix.lower()=='.xlsx':yield path.name,path.read_bytes();return
    if path.suffix.lower()!='.zip':raise ValueError('ExcelまたはZIPが必要です')
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist())>500*1024*1024:raise ValueError('ZIP展開サイズ超過')
        found=0
        for item in z.infolist():
            name=item.filename
            if not item.flag_bits&0x800:
                try:name=name.encode('cp437').decode('cp932')
                except UnicodeError:pass
            if not name.lower().endswith('.xlsx'):continue
            if bureau=='kyushu' and ('shika' in name.lower() or 'yakkyoku' in name.lower()):continue
            if bureau!='chugokushikoku' and not ('医科' in name or re.search(r'(?:^|[_（(])ika(?:[_）).]|$)',name.lower())):continue
            found+=1;yield name,z.read(item)
        if not found:raise ValueError('医科Excelが見つかりません')

def import_sheet(db,sh,*,file_sha,filename,url,bureau,revision_context=None):
    revision_context=validate_context(revision_context or [])
    db.execute('BEGIN IMMEDIATE')
    try:
        seen=db.execute('SELECT snapshot_id FROM source_sheets WHERE file_sha=? AND sheet=? AND parser_version=?',(file_sha,sh.title,VERSION)).fetchone()
        if seen:
            r=db.execute('SELECT * FROM snapshots WHERE id=?',(seen[0],)).fetchone();review=db.execute('SELECT status FROM snapshot_reviews WHERE snapshot_id=?',(seen[0],)).fetchone();db.rollback()
            return {'status':'review_required' if review and review[0]=='review_required' else 'unchanged','prefecture':r['prefecture'],'rows':r['row_count'],'filings':r['filing_count'],'snapshot_id':r['id']}
        iterator=enumerate(sh.iter_rows(values_only=True),1);labels=[];headers=None
        for rowno,row in iterator:
            if '受理届出名称' in row and '都道府県コード' in row:
                headers=[str(x or '') for x in row];break
            labels.extend(str(x) for x in row if x is not None)
            if rowno>15:break
        if headers!=HEADERS:raise ValueError('列構造が未対応です。元資料を保持して確認してください')
        label=' '.join(labels);asof=reference_date(label);digest=hashlib.sha256(dumps([headers,asof]).encode())
        snapshot=None;pref=None;row_count=0;cache=set();buffer=[];institutions={}
        for rowno,row in iterator:
            if all(x is None or x=='' for x in row):continue
            if len(row)!=len(headers):raise ValueError('列数の不一致')
            raw=dict(zip(headers,row))
            p=int(raw['都道府県コード'])
            if p not in PREFECTURES[bureau]:raise ValueError('管轄県の不一致')
            medical=raw['区分']=='医科'
            if not medical and bureau!='chugokushikoku':raise ValueError('医科区分の不一致')
            if pref is not None and p!=pref:raise ValueError('シート内に複数県')
            code=code10(p,raw['医療機関番号']) if medical else None;name=str(raw['医療機関名称'] or '')
            official=str(raw['受理届出名称'] or '')
            if not name.strip():raise ValueError('医療機関名が未記載')
            if medical and not official.strip() and any(raw[k] for k in ['受理記号','受理番号','算定開始年月日','個別有効開始年月日','備考（見出し）','備考（データ）']):raise ValueError('届出の値があるのに公式名称が未記載')
            if snapshot is None:
                pref=p;snapshot=db.execute('INSERT INTO snapshots(prefecture,reference_date,created_label,logical_sha,source_url,file_sha,filename,sheet,headers_json,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(p,asof,label,'pending-'+file_sha+'-'+sh.title,url,file_sha,filename,sh.title,dumps(headers),datetime.now(ZoneInfo('Asia/Tokyo')).isoformat())).lastrowid
            if medical and code not in institutions:
                db.execute('INSERT OR IGNORE INTO institutions VALUES(?,?,?)',(code,p,name));institutions[code]=name
            elif medical and institutions[code]!=name:raise ValueError('同一コードで医療機関名が不一致')
            values=[code,official]+[str(raw[k] or '') for k in ['受理記号','受理番号','算定開始年月日','個別有効開始年月日']]
            fid=hashlib.sha256(dumps(values).encode()).hexdigest() if medical and official.strip() else None
            if fid is not None and fid not in cache:
                db.execute('INSERT OR IGNORE INTO filings VALUES(?,?,?,?,?,?,?)',(fid,*values));cache.add(fid)
            raw_json=dumps(list(row));digest.update(raw_json.encode());digest.update(b'\n')
            buffer.append((snapshot,rowno,code,fid,'medical' if medical else 'nonmedical-original',raw_json));row_count+=1
            if len(buffer)>=1000:db.executemany('INSERT INTO source_rows VALUES(?,?,?,?,?,?)',buffer);buffer=[]
        if snapshot is None:raise ValueError('空の医科シート')
        if buffer:db.executemany('INSERT INTO source_rows VALUES(?,?,?,?,?,?)',buffer)
        sha=digest.hexdigest();old=db.execute('SELECT id FROM snapshots WHERE prefecture=? AND reference_date=? AND logical_sha=?',(pref,asof,sha)).fetchone()
        if old:
            # Repacked XLSX/ZIP with identical content must also be idempotent.
            review=db.execute('SELECT status FROM snapshot_reviews WHERE snapshot_id=?',(old[0],)).fetchone()
            db.rollback();db.execute('INSERT OR IGNORE INTO source_sheets VALUES(?,?,?,?)',(file_sha,sh.title,VERSION,old[0]));db.commit()
            return {'status':'review_required' if review and review[0]=='review_required' else 'unchanged_content','snapshot_id':old[0],'prefecture':pref,'rows':row_count,'filings':len(cache)}
        db.execute('UPDATE snapshots SET logical_sha=?,row_count=?,filing_count=? WHERE id=?',(sha,row_count,len(cache),snapshot))
        db.execute('INSERT INTO source_sheets VALUES(?,?,?,?)',(file_sha,sh.title,VERSION,snapshot))
        db.execute('INSERT INTO snapshot_institutions SELECT snapshot_id,code FROM source_rows WHERE snapshot_id=? AND code IS NOT NULL GROUP BY code',(snapshot,))
        current=db.execute('SELECT s.id,s.reference_date FROM current_snapshots c JOIN snapshots s ON s.id=c.snapshot_id WHERE c.prefecture=?',(pref,)).fetchone()
        gate=compare(db,current['id'],snapshot) if current else {'status':'accepted','reasons':[],'metrics':{'baseline':True}}
        policy_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_events'").fetchone()
        from app.notifications.policy_watch import unreviewed
        if current and policy_table and unreviewed(db):
            gate['status']='review_required';gate['reasons'].append('制度関連公表の更新確認が未了')
        if not current and asof>datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat():gate={'status':'review_required','reasons':['将来の基準日'],'metrics':{'baseline':True}}
        if current and asof<current['reference_date']:gate['status']='historical'
        activate=gate['status']=='accepted'
        db.execute('INSERT INTO snapshot_reviews VALUES(?,?,?,?,?,?,NULL)',(snapshot,current['id'] if current else None,gate['status'],dumps(gate['reasons']),dumps(gate['metrics']),dumps(revision_context)))
        if activate:
            db.execute('INSERT INTO current_snapshots VALUES(?,?) ON CONFLICT(prefecture) DO UPDATE SET snapshot_id=excluded.snapshot_id',(pref,snapshot))
            db.executemany('UPDATE institutions SET name=? WHERE code=?',[(v,k) for k,v in institutions.items()])
        actual=db.execute('SELECT count(*) FROM source_rows WHERE snapshot_id=?',(snapshot,)).fetchone()[0]
        if actual!=row_count:raise ValueError('元行数と保存行数が不一致')
        db.commit();return {'status':'review_required' if gate['status']=='review_required' else 'imported','prefecture':pref,'rows':row_count,'filings':len(cache),'active':activate,'reference_date':asof,'snapshot_id':snapshot,'comparison':gate}
    except BaseException:
        db.rollback();raise

def import_file(db,path,bureau,url='',revision_context=None):
    if url and not official_url(url):raise ValueError('公式配布元URLが不正です')
    results=[]
    for filename,data in workbooks(path,bureau):
        sha=hashlib.sha256(data).hexdigest();book=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True)
        try:
            for sh in book:results.append(import_sheet(db,sh,file_sha=sha,filename=filename,url=url,bureau=bureau,revision_context=revision_context))
        finally:book.close()
    return results

def audit(db):
    total=db.execute('SELECT count(*) FROM source_rows r JOIN current_snapshots c ON c.snapshot_id=r.snapshot_id').fetchone()[0]
    expected=db.execute('SELECT coalesce(sum(s.row_count),0) FROM snapshots s JOIN current_snapshots c ON c.snapshot_id=s.id').fetchone()[0]
    return {'pending_review':db.execute("SELECT count(*) FROM snapshot_reviews WHERE status='review_required'").fetchone()[0],
     'prefectures':db.execute('SELECT count(*) FROM current_snapshots').fetchone()[0],
     'current_rows':total,'expected_rows':expected,'row_counts_match':total==expected,
     'current_filings':db.execute('SELECT count(*) FROM (SELECT r.filing_id FROM source_rows r JOIN current_snapshots c ON c.snapshot_id=r.snapshot_id WHERE r.filing_id IS NOT NULL GROUP BY r.filing_id)').fetchone()[0],
     'current_institutions':db.execute('SELECT count(*) FROM (SELECT r.code FROM source_rows r JOIN current_snapshots c ON c.snapshot_id=r.snapshot_id WHERE r.code IS NOT NULL GROUP BY r.code)').fetchone()[0],
     'nonmedical_original_rows':db.execute("SELECT count(*) FROM source_rows r JOIN current_snapshots c ON c.snapshot_id=r.snapshot_id WHERE r.row_scope='nonmedical-original'").fetchone()[0],
     'snapshots':db.execute('SELECT count(*) FROM snapshots').fetchone()[0],
     'raw_rows':db.execute('SELECT count(*) FROM source_rows').fetchone()[0],
     'foreign_key_errors':len(db.execute('PRAGMA foreign_key_check').fetchall()),
     'duplicate_filing_keys':db.execute("SELECT count(*) FROM (SELECT 1 FROM filings GROUP BY code,official_name,coalesce(signature,''),coalesce(receipt_number,''),coalesce(start_date,''),coalesce(valid_from,'') HAVING count(*)>1)").fetchone()[0],
     'duplicate_codes':db.execute('SELECT count(*) FROM (SELECT code FROM institutions GROUP BY code HAVING count(*)>1)').fetchone()[0]}
