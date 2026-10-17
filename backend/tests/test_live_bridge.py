import pytest
from live_bridge import allowed_live_path


@pytest.mark.parametrize('method,path', [('GET','/studio-api/projects'),
    ('GET','/projects/mvp2_abc/tracks'), ('GET','/live-api/projects/song001/audio/original'),
    ('POST','/live-api/projects/song001/delta/drums'), ('POST','/projects/song001/render'),
    ('GET','/exports/'+'a'*32)])
def test_editor_api_allowlist(method,path):
    assert allowed_live_path(method,path)


@pytest.mark.parametrize('method,path', [('GET','/etc/passwd'),('DELETE','/projects/song001/render'),
    ('GET','/live-api/projects/../audio/vocals'),('GET','/live-api/projects/song001/audio/secret'),
    ('GET','http://evil.example/'),('POST','/studio-api/projects')])
def test_proxy_rejects_arbitrary_targets(method,path):
    assert not allowed_live_path(method,path)
