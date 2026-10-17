from fastapi import FastAPI
from fastapi.testclient import TestClient
from public_access import install

def test_public_app_auth_and_admin_block(monkeypatch):
    monkeypatch.setenv('SONIC_PUBLIC_MODE','1');monkeypatch.setenv('SONIC_APP_TOKEN','x'*40)
    app=FastAPI();install(app)
    with TestClient(app) as c:
        assert c.get('/health').status_code==200
        assert c.get('/mobile/').status_code==401
        auth={'Authorization':'Bearer '+'x'*40}
        r=c.post('/api/app/login',headers=auth)
        assert r.status_code==200 and 'HttpOnly' in r.headers['set-cookie'] and 'Secure' in r.headers['set-cookie']
        assert c.get('/api/model-profiles',headers=auth).status_code==404
