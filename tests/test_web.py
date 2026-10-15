import pytest
import httpx
from fastapi.testclient import TestClient
from app.main import app,authorized,monthly_counts
from app.store import Supabase

@pytest.fixture
def client():
    with TestClient(app) as c: yield c
    app.dependency_overrides.clear()

def test_login_page_and_no_unauthenticated_data(client):
    r=client.get('/');assert r.status_code==200 and 'ログイン' in r.text
    for path in ['/api/dashboard','/api/admin/members']:
        assert client.get(path).status_code==401
    assert client.post('/api/search',json={}).status_code==401
    for format in ['csv','xlsx','master']: assert client.post('/api/export/'+format,json={}).status_code==401

def test_csrf_cross_origin_denied(client):
    assert client.post('/auth/logout',headers={'Origin':'https://attacker.example'}).status_code==403

def test_missing_config_reported_without_secrets(client,monkeypatch):
    monkeypatch.delenv('SUPABASE_PUBLISHABLE_KEY',raising=False)
    r=client.post('/auth/login',data={'email':'a@example.test','password':'not-a-secret'})
    assert r.status_code==503 and 'not-a-secret' not in r.text

def test_unapproved_session_denied(client,monkeypatch):
    monkeypatch.setenv('SUPABASE_URL','https://project.supabase.co');monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY','test')
    def request(self,method,path,**kwargs):
        return httpx.Response(200,json={'id':'11111111-1111-1111-1111-111111111111'} if path.startswith('/auth') else [{'approved':False,'role':'user'}])
    monkeypatch.setattr(Supabase,'request',request)
    client.cookies.set('access_token','fake')
    assert client.get('/api/dashboard').status_code==403

def test_user_cannot_admin(client):
    app.dependency_overrides[authorized]=lambda: (None,{'role':'user'})
    assert client.get('/api/admin/members').status_code==403

class FakeDB:
    def __init__(self):self.audits=[]
    def request(self,method,path,**kwargs):
        assert ('kind','eq.病院') in kwargs['params']
        return httpx.Response(200,json=[{'code':'4010210039','name':'テスト病院','raw':{'private':'raw'}}],headers={'content-range':'0-0/1'})
    def audit(self,*args):self.audits.append(args)

def test_search_count_and_raw_not_sent(client):
    db=FakeDB();app.dependency_overrides[authorized]=lambda:(db,{'role':'user'})
    r=client.post('/api/search',json={'kind':'病院','minimum':20,'maximum':200})
    assert r.status_code==200 and r.json()['total']==1
    assert 'raw' not in r.json()['items'][0] and db.audits[0][0]=='search'

def test_monthly_baseline_not_new_opening():
    result=monthly_counts([{'event_date':'2026-10-01','event_type':'baseline'},
      {'event_date':'2026-11-01','event_type':'new_designation','opening_classification':'判定保留'},
      {'event_date':'2026-11-01','event_type':'missing_candidate'}])
    assert '2026-10' not in result
    assert result['2026-11']['新規指定総数']==1 and '純新規開業数' not in result['2026-11']

def test_void_postgrest_rpc_handles_204(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL','https://project.supabase.co');monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY','test')
    monkeypatch.setattr(Supabase,'request',lambda *args,**kwargs:httpx.Response(204))
    db=Supabase()
    try:assert db.rpc('record_access',{}) is None
    finally:db.close()
