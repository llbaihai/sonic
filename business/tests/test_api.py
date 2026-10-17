"""Behavior tests against the API, a real PostgreSQL database and real files.

No repository, authentication, storage or audio decoder is mocked.
"""
import io
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from sonic.main import create_app

PREFIX = "/api/v1"


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def account(c):
    username = "test_" + uuid.uuid4().hex
    password = "test-only-password-938!"
    r = c.post(PREFIX + "/auth/register", json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    r = c.post(PREFIX + "/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["data"]["access_token"]}, username


@pytest.fixture
def auth(client):
    return account(client)[0]


def project(c, h):
    r = c.post(PREFIX + "/projects", headers=h, json={"name": "我的声音"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


def upload(c, h, p, raw=None, filename="voice.wav", mime="audio/wav"):
    return c.post(f"{PREFIX}/projects/{p}/assets", headers=h,
                  files={"file": (filename, raw if raw is not None else wav_bytes(), mime)})


def test_login_identity_and_invalid_credentials(client):
    h, username = account(client)
    r = client.get(PREFIX + "/users/me", headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["username"] == username
    assert "password" not in r.text
    bad = client.post(PREFIX + "/auth/login", json={"username": username, "password": "incorrect"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert client.get(PREFIX + "/users/me").status_code == 401
    assert client.get(PREFIX + "/users/me", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_project_crud_isolated_and_idempotent(client, auth):
    key = str(uuid.uuid4())
    h = auth | {"Idempotency-Key": key}
    a = client.post(PREFIX + "/projects", headers=h, json={"name": "first"})
    assert a.status_code == 201
    p = a.json()["data"]["id"]
    b = client.post(PREFIX + "/projects", headers=h, json={"name": "first"})
    assert b.json() == a.json()
    assert client.post(PREFIX + "/projects", headers=h, json={"name": "different"}).status_code == 409
    other, _ = account(client)
    assert client.get(PREFIX + "/projects", headers=other).json()["data"] == []
    for method in ["get", "patch", "delete"]:
        kw = {"json": {"name": "stolen"}} if method == "patch" else {}
        assert client.request(method, f"{PREFIX}/projects/{p}", headers=other, **kw).status_code == 404
    r = client.patch(f"{PREFIX}/projects/{p}", headers=auth, json={"name": "renamed"})
    assert r.json()["data"]["name"] == "renamed"
    assert client.delete(f"{PREFIX}/projects/{p}", headers=auth).status_code == 204
    assert client.get(f"{PREFIX}/projects/{p}", headers=auth).status_code == 404


def test_audio_upload_download_and_ownership(client, auth):
    p = project(client, auth)
    r = upload(client, auth, p)
    assert r.status_code == 201, r.text
    asset = r.json()["data"]
    assert asset["size_bytes"] == len(wav_bytes())
    assert asset["sample_rate"] == 16000
    assert asset["channels"] == 1
    assert asset["duration_ms"] == 100
    assert "storage_key" not in asset
    endpoint = f"{PREFIX}/assets/{asset['id']}/download"
    assert client.get(endpoint, headers=auth).content == wav_bytes()
    partial = client.get(endpoint, headers=auth | {"Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert partial.content == wav_bytes()[:10]
    other, _ = account(client)
    assert client.get(endpoint, headers=other).status_code == 404
    assert client.get(endpoint).status_code == 401
    assert upload(client, other, p).status_code == 404
    assert client.get(f"{PREFIX}/projects/{p}/assets", headers=other).status_code == 404
    assert len(client.get(f"{PREFIX}/projects/{p}/assets", headers=auth).json()["data"]) == 1


@pytest.mark.parametrize("filename,mime,raw", [
    ("../escape.wav", "audio/wav", wav_bytes()),
    ("fake.wav", "audio/wav", b"not audio"),
    ("fake.m4a", "audio/mp4", b"\x00\x00\x00\x18ftypM4A " + b"broken" * 10),
    ("voice.exe", "audio/wav", wav_bytes()),
    ("voice.wav", "text/plain", wav_bytes()),
    ("voice.wav", "audio/wav", b""),
], ids=["traversal", "fake-wav", "fake-m4a", "extension", "mime", "empty"])
def test_unsafe_audio_never_creates_record(client, auth, filename, mime, raw):
    p = project(client, auth)
    r = upload(client, auth, p, raw, filename, mime)
    assert r.status_code in (415, 422), r.text
    assert "error" in r.json()
    assert client.get(f"{PREFIX}/projects/{p}/assets", headers=auth).json()["data"] == []


def test_windows_filename_is_never_used_as_storage_path(client, auth):
    p = project(client, auth)
    # The multipart parser normalizes legacy Windows absolute filenames to basename.
    r = upload(client, auth, p, filename="C:\\escape.wav")
    assert r.status_code == 201
    assert r.json()["data"]["filename"] == "escape.wav"
    from pathlib import Path
    assert not Path('C:/escape.wav').exists()


def encoded_aac(container):
    import av
    buf = io.BytesIO()
    with av.open(buf, mode="w", format=container) as out:
        stream = out.add_stream("aac", rate=44100)
        stream.layout = "mono"
        for i in range(5):
            frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
            frame.sample_rate = 44100
            frame.pts = i * 1024
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


@pytest.mark.parametrize("container,filename,mime", [("mp4", "android.m4a", "audio/mp4"), ("adts", "android.aac", "audio/aac")])
def test_real_android_aac_formats(client, auth, container, filename, mime):
    raw = encoded_aac(container)
    p = project(client, auth)
    r = upload(client, auth, p, raw, filename, mime)
    assert r.status_code == 201, r.text
    asset = r.json()["data"]
    assert asset["codec"] == "aac"
    assert asset["sample_rate"] == 44100
    assert asset["duration_ms"] > 0
    assert client.get(f"{PREFIX}/assets/{asset['id']}/download", headers=auth).content == raw


def draft(asset_id, version=0):
    return {"expected_version": version, "tracks": [{"id": "track-1", "name": "录音",
        "clips": [{"asset_id": asset_id, "start_ms": 0, "offset_ms": 0, "duration_ms": 80, "gain_db": 0}]}]}


def test_draft_persists_and_detects_stale_update(client, auth):
    p = project(client, auth)
    asset = upload(client, auth, p).json()["data"]["id"]
    url = f"{PREFIX}/projects/{p}/draft"
    r = client.put(url, headers=auth, json=draft(asset))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["version"] == 1
    assert client.get(url, headers=auth).json()["data"]["tracks"][0]["clips"][0]["asset_id"] == asset
    assert client.put(url, headers=auth, json=draft(asset)).status_code == 409
    r = client.put(url, headers=auth, json=draft(asset, 1))
    assert r.json()["data"]["version"] == 2
    other, _ = account(client)
    assert client.get(url, headers=other).status_code == 404
    assert client.put(url, headers=other, json=draft(asset, 2)).status_code == 404


def test_draft_rejects_foreign_assets_and_out_of_bounds_clip(client, auth):
    p = project(client, auth)
    q = project(client, auth)
    asset = upload(client, auth, q).json()["data"]["id"]
    assert client.put(f"{PREFIX}/projects/{p}/draft", headers=auth, json=draft(asset)).status_code == 422
    body = draft(asset)
    body["tracks"][0]["clips"][0]["duration_ms"] = 1000
    assert client.put(f"{PREFIX}/projects/{q}/draft", headers=auth, json=body).status_code == 422


def test_concurrent_draft_updates_have_one_winner(client, auth):
    p = project(client, auth)
    asset = upload(client, auth, p).json()["data"]["id"]
    url = f"{PREFIX}/projects/{p}/draft"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: client.put(url, headers=auth, json=draft(asset)).status_code, range(2)))
    assert sorted(results) == [200, 409]
    assert client.get(url, headers=auth).json()["data"]["version"] == 1


def test_real_work_record_download_delete(client, auth):
    p = project(client, auth)
    url = f"{PREFIX}/projects/{p}/works"
    assert client.get(url, headers=auth).json()["data"] == []
    invalid = client.post(url, headers=auth, json={"title": "not real", "asset_id": str(uuid.uuid4())})
    assert invalid.status_code == 422
    asset = upload(client, auth, p).json()["data"]["id"]
    h = auth | {"Idempotency-Key": str(uuid.uuid4())}
    body = {"title": "真实音频作品", "asset_id": asset}
    r = client.post(url, headers=h, json=body)
    assert r.status_code == 201, r.text
    work = r.json()["data"]["id"]
    assert client.post(url, headers=h, json=body).json() == r.json()
    assert len(client.get(url, headers=auth).json()["data"]) == 1
    assert client.get(f"{PREFIX}/works/{work}/download", headers=auth).content == wav_bytes()
    other, _ = account(client)
    for method, suffix in [("get", ""), ("get", "/download"), ("delete", "")]:
        assert client.request(method, f"{PREFIX}/works/{work}{suffix}", headers=other).status_code == 404
    assert client.delete(f"{PREFIX}/works/{work}", headers=auth).status_code == 204
    assert client.get(url, headers=auth).json()["data"] == []


def test_upload_retry_does_not_duplicate_and_project_delete_revokes_download(client, auth):
    p = project(client, auth)
    h = auth | {"Idempotency-Key": str(uuid.uuid4())}
    a = upload(client, h, p)
    b = upload(client, h, p)
    assert a.status_code == 201, a.text
    assert b.json() == a.json()
    assert len(client.get(f"{PREFIX}/projects/{p}/assets", headers=auth).json()["data"]) == 1
    asset = a.json()["data"]["id"]
    assert client.delete(f"{PREFIX}/projects/{p}", headers=auth).status_code == 204
    assert client.get(f"{PREFIX}/assets/{asset}/download", headers=auth).status_code == 404
