"""Real PostgreSQL tests for persistent revocable login sessions."""
import uuid
from fastapi.testclient import TestClient
from sonic.main import create_app

def test_logout_and_password_revoke_sessions():
    with TestClient(create_app()) as client:
        name='session_'+uuid.uuid4().hex[:12];password='Initial-password-123'
        assert client.post('/api/v1/auth/register',json={'username':name,'password':password}).status_code==201
        def login(pw=password):
            r=client.post('/api/v1/auth/login',json={'username':name,'password':pw});assert r.status_code==200,r.text
            return {'Authorization':'Bearer '+r.json()['data']['access_token']}
        first=login();second=login()
        assert client.post('/api/v1/auth/logout',headers=first).status_code==200
        assert client.get('/api/v1/users/me',headers=first).status_code==401
        assert client.get('/api/v1/users/me',headers=second).status_code==200
        r=client.post('/api/v1/auth/password',headers=second,json={'current_password':password,'new_password':'Changed-password-456'})
        assert r.status_code==200,r.text
        assert client.get('/api/v1/users/me',headers=second).status_code==401
        assert client.post('/api/v1/auth/login',json={'username':name,'password':password}).status_code==401
        assert client.get('/api/v1/users/me',headers=login('Changed-password-456')).status_code==200


def test_gateway_clients_have_separate_auth_limits():
    from sonic.config import Settings
    with TestClient(create_app(Settings(auth_limit=2)),client=('127.0.0.1',4200)) as client:
        first='gateway_'+uuid.uuid4().hex
        for i in range(2):
            assert client.post('/api/v1/auth/login',headers={'X-Sonic-Auth-Client':first},json={'username':'missing_'+uuid.uuid4().hex,'password':'wrong'}).status_code==401
        assert client.post('/api/v1/auth/login',headers={'X-Sonic-Auth-Client':first},json={'username':'missing_'+uuid.uuid4().hex,'password':'wrong'}).status_code==429
        assert client.post('/api/v1/auth/login',headers={'X-Sonic-Auth-Client':'other_'+uuid.uuid4().hex},json={'username':'missing_'+uuid.uuid4().hex,'password':'wrong'}).status_code==401
