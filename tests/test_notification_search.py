import json,sqlite3
import pytest
from fastapi.testclient import TestClient
from app.notifications.devapp import app,database
from app.notifications.store import connect,import_file,audit
from app.notifications.reference import load
from tests.test_notifications import workbook

@pytest.fixture
def setup(tmp_path,monkeypatch):
    path=tmp_path/'dev.sqlite';db=connect(path)
    blank={k:'' for k in ['受理届出名称','受理記号','受理番号','算定開始年月日']}
    rows=[{'受理届出名称':'電子的診療情報連携体制整備加算１'},dict(blank,医療機関番号='0111888',医療機関名称='未届出整形外科')]
    import_file(db,workbook(tmp_path/'source.xlsx',rows),'hokkaido')
    refs=[{'code':'0110111887','name':'公式病院','departments':['整外 リハ']}, {'code':'0110111888','name':'未届出整形外科','departments':['整外']}, {'code':'0110111889','name':'未照合整形外科','departments':['整外']}, {'code':'0110111890','name':'内科','departments':['内']}]
    for r in refs:r.update(address='札幌市',kind='診療所',status='現存',prefecture_code=1,prefecture='北海道',reference_date='2026-10-01')
    (tmp_path/'master.institutions.json').write_text(json.dumps(refs,ensure_ascii=False));load(db,tmp_path);db.close()
    monkeypatch.setenv('NOTIFICATIONS_DEV_DB',str(path))
    with TestClient(app) as c:yield c,path

def test_department_and_filed_unfiled_are_disjoint_and_exclude_unknown(setup):
    c,_=setup;q={'kind':'診療所','department':'整形外科','notification':'電子的診療情報連携体制整備加算'}
    yes=c.get('/api/search',params={**q,'mode':'filed'}).json();no=c.get('/api/search',params={**q,'mode':'not_filed'}).json()
    assert [r['code'] for r in yes['items']]==['0110111887']
    assert [r['code'] for r in no['items']]==['0110111888']
    assert yes['unconfirmed_institutions']==no['unconfirmed_institutions']==1
    assert c.get('/api/search',params={'mode':'not_filed'}).status_code==400

def test_all_filing_details_keep_official_name_and_all_columns(setup):
    c,_=setup
    r=c.get('/api/institutions/0110111887/filings').json()['items'][0]
    assert r['official_name']=='電子的診療情報連携体制整備加算１'
    detail=c.get('/api/filings/'+r['id']).json()['items']
    assert len(detail)==1 and len(detail[0]['values'])==24 and detail[0]['row']==3
    assert c.get('/api/audit').json()['duplicate_codes']==0
    assert c.get('/').status_code==200
    assert '未届出の' in c.get('/').text

def test_development_page_uses_read_only_connection(setup):
    with database() as db:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):db.execute('DELETE FROM institutions')
