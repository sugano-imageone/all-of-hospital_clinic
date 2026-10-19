import pytest
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup
from app.main import app

@pytest.mark.parametrize('path',['/auth/login','/auth/refresh','/api/search','/api/admin/members'])
def test_maintenance_blocks_login_and_data(monkeypatch,path):
    monkeypatch.setenv('APP_MAINTENANCE','true')
    with TestClient(app) as c:
        r=c.post(path,json={})
        assert r.status_code==503 and r.json()['detail']=='メンテナンス中'

def test_maintenance_login_page_and_reopening(monkeypatch):
    with TestClient(app) as c:
        monkeypatch.setenv('APP_MAINTENANCE','true')
        s=BeautifulSoup(c.get('/').text,'html.parser')
        assert s.select_one('#login-heading').get_text()=='メンテナンス中'
        assert s.select_one('#login').has_attr('hidden')
        monkeypatch.setenv('APP_MAINTENANCE','false')
        s=BeautifulSoup(c.get('/').text,'html.parser')
        assert s.select_one('#login-heading').get_text()=='ログイン'
        assert not s.select_one('#login').has_attr('hidden')
