"""Local read-only review page. Not imported by app.main or the production build."""
import json,os,re,sqlite3
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI,HTTPException,Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from app.notifications.store import audit

app=FastAPI(title='届出事項・開発確認',docs_url=None,redoc_url=None)
templates=Jinja2Templates(directory=Path(__file__).parent/'templates')
@contextmanager
def database():
    path=Path(os.getenv('NOTIFICATIONS_DEV_DB','data/notifications/development-v2.sqlite')).resolve()
    if not path.is_file():raise HTTPException(503,'開発用データが未作成です')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=60);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reference_institutions'").fetchone():raise HTTPException(503,'主一覧と診療科目の開発用取込を先に実行してください')
        yield db
    finally:db.close()

@app.get('/',response_class=HTMLResponse)
def index(request:Request):return templates.TemplateResponse(request=request,name='index.html',context={})

@app.get('/api/audit')
def checks():
    with database() as db:
        result=audit(db)
        result['departments_missing']=db.execute("SELECT count(*) FROM reference_institutions r WHERE status='現存' AND NOT EXISTS(SELECT 1 FROM institution_departments d WHERE d.code=r.code)").fetchone()[0]
        result['reference_institutions']=db.execute('SELECT count(*) FROM reference_institutions').fetchone()[0]
        result['production_changes']=False
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_events'").fetchone():
            from app.notifications.policy_watch import unreviewed
            result['policy_events']=[dict(r) for r in unreviewed(db)]
        else:result['policy_events']=[]
        result['reviews']=[dict(r) for r in db.execute("SELECT s.prefecture,s.reference_date,r.* FROM snapshot_reviews r JOIN snapshots s ON s.id=r.snapshot_id WHERE r.status='review_required' ORDER BY s.prefecture")]
        result['sources']=[dict(r) for r in db.execute('SELECT s.prefecture,s.reference_date,s.row_count,s.filing_count FROM snapshots s JOIN current_snapshots c ON c.snapshot_id=s.id ORDER BY s.prefecture')]
        return result

@app.get('/api/options')
def options():
    with database() as db:
        return {'departments':[r[0] for r in db.execute('SELECT DISTINCT department FROM institution_departments ORDER BY department')],
          'notifications':[r[0] for r in db.execute('SELECT DISTINCT f.official_name FROM filings f JOIN source_rows r ON r.filing_id=f.id JOIN current_snapshots c ON c.snapshot_id=r.snapshot_id ORDER BY f.official_name')],
          'prefectures':[{'code':r[0],'name':r[1]} for r in db.execute("SELECT prefecture,json_extract(raw_json,'$.prefecture') FROM reference_institutions GROUP BY prefecture ORDER BY prefecture")]}

@app.get('/api/search')
def search(code:str='',name:str='',kind:str='',prefecture:int|None=None,department:str='',notification:str='',mode:str='filed',page:int=Query(1,ge=1)):
    if code and not re.fullmatch(r'\d{10}',code):raise HTTPException(400,'医療機関コードは10桁で入力してください')
    if kind not in ['', '病院','診療所'] or mode not in ['filed','not_filed']:raise HTTPException(400,'検索条件が不正です')
    if mode=='not_filed' and not notification.strip():raise HTTPException(400,'未届出の検索では加算名・届出名を指定してください')
    clauses=["r.status='現存'"];params=[]
    for column,value in [('code',code),('kind',kind),('prefecture',prefecture)]:
        if value:clauses.append(f'r.{column}=?');params.append(value)
    if name:clauses.append('instr(r.name,?)>0');params.append(name)
    if department:
        clauses.append('EXISTS(SELECT 1 FROM institution_departments d WHERE d.code=r.code AND d.department=?)');params.append(department)
    covered='EXISTS(SELECT 1 FROM snapshot_institutions si JOIN current_snapshots cs ON cs.snapshot_id=si.snapshot_id WHERE si.code=r.code)'
    match='EXISTS(SELECT 1 FROM filings f WHERE f.code=r.code AND instr(f.official_name,?)>0 AND EXISTS(SELECT 1 FROM source_rows sr JOIN current_snapshots cs ON cs.snapshot_id=sr.snapshot_id WHERE sr.filing_id=f.id))'
    base=' AND '.join(clauses)
    with database() as db:
        unknown=db.execute(f'SELECT count(*) FROM reference_institutions r WHERE {base} AND NOT {covered}',params).fetchone()[0]
        if notification:
            clauses.append(covered);clauses.append(match if mode=='filed' else 'NOT '+match);params.append(notification)
        where=' AND '.join(clauses)
        total=db.execute(f'SELECT count(*) FROM reference_institutions r WHERE {where}',params).fetchone()[0]
        rows=db.execute(f'SELECT r.code,r.name,r.kind,r.address,r.phone,r.prefecture,r.departments_json,r.raw_json FROM reference_institutions r WHERE {where} ORDER BY r.code LIMIT 50 OFFSET ?',[*params,(page-1)*50]).fetchall()
        items=[]
        for r in rows:
            item={k:r[k] for k in ['code','name','kind','address','phone','prefecture']};raw=json.loads(r['raw_json']);item['departments_raw']=json.loads(r['departments_json']);item['master_reference_date']=raw.get('reference_date')
            item['departments']=[d[0] for d in db.execute('SELECT DISTINCT department FROM institution_departments WHERE code=? ORDER BY department',(r['code'],))]
            item['filing_count']=db.execute('SELECT count(*) FROM filings f WHERE f.code=? AND EXISTS(SELECT 1 FROM source_rows sr JOIN current_snapshots cs ON cs.snapshot_id=sr.snapshot_id WHERE sr.filing_id=f.id)',(r['code'],)).fetchone()[0]
            item['notification_reference_date']=db.execute('SELECT s.reference_date FROM snapshots s JOIN current_snapshots cs ON cs.snapshot_id=s.id WHERE s.prefecture=?',(r['prefecture'],)).fetchone()
            item['notification_reference_date']=item['notification_reference_date'][0] if item['notification_reference_date'] else None
            items.append(item)
        return {'items':items,'total':total,'page':page,'page_size':50,'unconfirmed_institutions':unknown,'mode':mode,'notification':notification}

@app.get('/api/institutions/{code}/filings')
def filings(code:str):
    if not re.fullmatch(r'\d{10}',code):raise HTTPException(400,'医療機関コードは10桁です')
    with database() as db:
        rows=db.execute('SELECT f.*,count(*) AS original_rows,s.reference_date FROM filings f JOIN source_rows sr ON sr.filing_id=f.id JOIN current_snapshots cs ON cs.snapshot_id=sr.snapshot_id JOIN snapshots s ON s.id=sr.snapshot_id WHERE f.code=? GROUP BY f.id ORDER BY f.official_name,f.receipt_number',(code,))
        return {'code':code,'items':[dict(r) for r in rows]}

@app.get('/api/filings/{filing_id}')
def detail(filing_id:str):
    if not re.fullmatch(r'[0-9a-f]{64}',filing_id):raise HTTPException(400,'届出IDが不正です')
    with database() as db:
        rows=db.execute('SELECT sr.raw_json,sr.row_number,s.headers_json,s.filename,s.sheet,s.source_url,s.reference_date FROM source_rows sr JOIN current_snapshots cs ON cs.snapshot_id=sr.snapshot_id JOIN snapshots s ON s.id=sr.snapshot_id WHERE sr.filing_id=? ORDER BY sr.row_number',(filing_id,))
        return {'items':[{'values':dict(zip(json.loads(r['headers_json']),json.loads(r['raw_json']))),'row':r['row_number'],'filename':r['filename'],'sheet':r['sheet'],'source_url':r['source_url'],'reference_date':r['reference_date']} for r in rows]}
