import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app,authorized
from app.store import Supabase

@pytest.fixture
def client():
    with TestClient(app) as c:yield c
    app.dependency_overrides.clear()

@pytest.mark.parametrize('role',['user','admin','master'])
def test_history_permissions_and_filters(client,role):
    class DB:
        calls=[]
        def rpc(self,name,args):
            self.calls.append((name,args));return {'items':[],'total':0,'page':2,'page_size':50}
    db=DB();app.dependency_overrides[authorized]=lambda:(db,{'role':role})
    r=client.post('/api/admin/logins',json={'email':'viewer','from_date':'2026-10-01','to_date':'2026-10-21','page':2})
    if role=='user':assert r.status_code==403 and not db.calls
    else:
        assert r.status_code==200
        assert db.calls==[('viewer_login_history',{'p_email':'viewer','p_from':'2026-10-01','p_to':'2026-10-21','p_page':2})]

def test_history_requires_login(client):assert client.post('/api/admin/logins',json={}).status_code==401

@pytest.mark.parametrize('filters',[{'page':0},{'email':'x'*255},{'from_date':'2026-02-30'},{'from_date':'2026-10-21','to_date':'2026-10-01'}])
def test_invalid_filters_do_not_read_history(client,filters):
    app.dependency_overrides[authorized]=lambda:(None,{'role':'admin'})
    assert client.post('/api/admin/logins',json=filters).status_code==422

@pytest.mark.parametrize('approved',[True,False])
def test_login_records_only_approved_session(client,monkeypatch,approved):
    monkeypatch.setenv('SUPABASE_URL','https://project.supabase.co');monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY','test')
    calls=[]
    def request(self,method,path,**kwargs):
        if path.startswith('/auth'):
            return httpx.Response(200,json={'access_token':'test-access','refresh_token':'test-refresh','user':{'id':'viewer-id'}})
        return httpx.Response(200,json=[{'approved':approved,'role':'user'}])
    monkeypatch.setattr(Supabase,'request',request)
    monkeypatch.setattr(Supabase,'rpc',lambda self,name,args:calls.append((name,args)))
    r=client.post('/auth/login',data={'email':'viewer@example.invalid','password':'fixture-password'})
    assert r.status_code==(200 if approved else 403)
    assert calls==([('record_login',{})] if approved else [])
    assert ('access_token' in client.cookies)==approved
