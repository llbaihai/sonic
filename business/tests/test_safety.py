import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select
from sonic.config import Settings
from sonic.main import create_app
from sonic.models import Asset, FileDeletion
from test_api import PREFIX, account, auth, client, draft, project, upload, wav_bytes


def test_body_limit_applies_before_multipart_parsing(client, auth):
    p = project(client, auth)
    settings = Settings(max_upload_bytes=1024)
    with TestClient(create_app(settings)) as small:
        r = upload(small, auth, p, b'x' * 70000)
        assert r.status_code == 413
        r = small.post(f'{PREFIX}/projects/{p}/assets', headers=auth | {'Content-Type': 'multipart/form-data; boundary=x'},
                       content=iter([b'x' * 40000, b'y' * 40000]))
        assert r.status_code == 413
        assert r.json()['error']['code'] == 'UPLOAD_TOO_LARGE'
        assert r.headers['X-Request-ID']


def test_truncated_wav_is_rejected(client, auth):
    p = project(client, auth)
    r = upload(client, auth, p, wav_bytes()[:-500])
    assert r.status_code == 415
    assert client.get(f'{PREFIX}/projects/{p}/assets', headers=auth).json()['data'] == []


def test_password_spaces_are_significant(client):
    username = 'space_' + uuid.uuid4().hex
    password = '  significant-password!  '
    assert client.post(PREFIX + '/auth/register', json={'username': username, 'password': password}).status_code == 201
    r = client.post(PREFIX + '/auth/login', json={'username': username, 'password': password.strip()})
    assert r.status_code == 401
    assert client.post(PREFIX + '/auth/login', json={'username': username, 'password': password}).status_code == 200


def test_concurrent_project_retries_create_one_record(client, auth):
    h = auth | {'Idempotency-Key': str(uuid.uuid4())}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post(PREFIX + '/projects', headers=h, json={'name': 'once'}), range(4)))
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()['data']['id'] for r in results}) == 1
    assert len(client.get(PREFIX + '/projects', headers=auth).json()['data']) == 1


def test_draft_retry_returns_original_version(client, auth):
    p = project(client, auth)
    asset = upload(client, auth, p).json()['data']['id']
    h = auth | {'Idempotency-Key': str(uuid.uuid4())}
    url = f'{PREFIX}/projects/{p}/draft'
    first = client.put(url, headers=h, json=draft(asset))
    second = client.put(url, headers=h, json=draft(asset))
    assert first.status_code == 200
    assert second.json() == first.json()
    assert client.get(url, headers=auth).json()['data']['version'] == 1


def test_expired_token_and_validation_never_expose_secrets(client, auth):
    user = client.get(PREFIX + '/users/me', headers=auth).json()['data']['id']
    now = datetime.now(timezone.utc)
    token = jwt.encode({'sub': user, 'iat': now - timedelta(hours=2), 'exp': now - timedelta(hours=1),
                        'aud': 'sonic-android', 'iss': 'sonic'}, Settings().jwt_secret.get_secret_value(), algorithm='HS256')
    assert client.get(PREFIX + '/users/me', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    secret = 'sensitive-input-value'
    r = client.post(PREFIX + '/auth/login', json={'username': 'abc', 'password': secret * 100})
    assert r.status_code == 422
    assert secret not in r.text
    assert r.json()['error']['request_id'] == r.headers['X-Request-ID']


def test_rate_limit_persists_across_app_instances():
    settings = Settings(auth_limit=2)
    username = 'missing_' + uuid.uuid4().hex
    for attempt in range(3):
        with TestClient(create_app(settings), client=('192.0.2.44', 4000)) as c:
            r = c.post(PREFIX + '/auth/login', json={'username': username, 'password': 'incorrect'})
            assert r.status_code == (401 if attempt < 2 else 429)
    assert r.headers['Retry-After']


def test_project_delete_removes_file_and_pending_queue(client, auth):
    p = project(client, auth)
    asset = upload(client, auth, p).json()['data']['id']
    with client.app.state.sessions() as session:
        row = session.get(Asset, uuid.UUID(asset))
        from sonic.storage import LocalStorage
        path = LocalStorage(Settings()).require(row.storage_key)
    assert client.delete(f'{PREFIX}/projects/{p}', headers=auth).status_code == 204
    assert not path.exists()
    with client.app.state.sessions() as session:
        assert session.scalar(select(FileDeletion).where(FileDeletion.storage_key == path.name)) is None


def test_work_cannot_reference_other_users_audio(client, auth):
    p = project(client, auth)
    other, _ = account(client)
    q = project(client, other)
    asset = upload(client, other, q).json()['data']['id']
    r = client.post(f'{PREFIX}/projects/{p}/works', headers=auth, json={'title': 'stolen', 'asset_id': asset})
    assert r.status_code == 422


def test_registration_can_be_disabled():
    with TestClient(create_app(Settings(allow_registration=False))) as c:
        r = c.post(PREFIX + '/auth/register', json={'username': 'disabled', 'password': 'a-long-password-123'})
        assert r.status_code == 403
