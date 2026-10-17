"""Cross-user permission checks without live inference or a live identity provider."""
import uuid
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
import app as main
from test_mvp import tone_wav

@pytest.fixture
def users(monkeypatch,tmp_path):
    monkeypatch.setenv('SONIC_USER_ACCOUNTS','1')
    monkeypatch.setattr(main,'SESSIONS_DIR',tmp_path/'sessions');monkeypatch.setattr(main,'JOBS_DIR',tmp_path/'jobs')
    main.SESSIONS_DIR.mkdir();main.JOBS_DIR.mkdir()
    def identity(token):
        return {'id':token,'username':token} if token in ['alice','bob'] else None
    with patch('user_access.resolve_token',side_effect=identity), TestClient(main.app) as client:
        yield client

def headers(user):return {'X-Sonic-User-Token':user}
def upload(client,user):
    r=client.post('/api/sessions',headers=headers(user),files={'file':('test.wav',tone_wav())})
    assert r.status_code==200,r.text
    return r.json()

def test_anonymous_is_denied(users):
    assert users.get('/api/sessions').status_code==401
    assert users.get('/api/jobs').status_code==401
    assert users.post('/api/device/credentials',json={'deviceId':'test-install'}).status_code==401

def test_private_lists_media_names_and_bulk_atomicity(users):
    a=upload(users,'alice');b=upload(users,'bob')
    assert [s['id'] for s in users.get('/api/sessions',headers=headers('alice')).json()['sessions']]==[a['id']]
    assert users.get(a['audio_url'],headers=headers('bob')).status_code==404
    assert users.get(a['audio_url'],headers=headers('alice')).status_code==200
    assert users.patch('/api/sessions/'+a['id']+'/name',json={'name':'stolen'},headers=headers('bob')).status_code==404
    r=users.post('/api/sessions/bulk-delete',json={'ids':[b['id'],a['id']]},headers=headers('bob'))
    assert r.status_code==404
    assert users.get(b['audio_url'],headers=headers('bob')).status_code==200

def test_generation_cannot_reference_foreign_source(users):
    a=upload(users,'alice');b=upload(users,'bob')
    with patch('app.executor.submit'),patch('app.translate_prompt',return_value='music'):
        r=users.post('/api/compositions',headers=headers('bob'),json={'session_ids':[a['id'],b['id']],'final_prompt':'piano'})
        assert r.status_code==404
        r=users.post('/api/compositions',headers=headers('alice'),json={'session_ids':[a['id']],'final_prompt':'piano'})
        assert r.status_code==200,r.text
    job=r.json()['id']
    assert main.read_json(main.JOBS_DIR/job/'job.json')['owner_id']=='alice'
    assert users.get('/api/jobs/'+job,headers=headers('bob')).status_code==404
    assert users.post('/api/jobs/'+job+'/separate',json={},headers=headers('bob')).status_code==404
    assert users.get('/results/'+job+'/project.json',headers=headers('bob')).status_code==404

def test_live_export_denied(users):
    job=uuid.uuid4().hex;p=main.JOBS_DIR/job;p.mkdir()
    main.save_json(p/'job.json',{'id':job,'owner_id':'alice','created_at':'now'})
    main.save_json(p/'separation.json',{'status':'completed','project_id':'mvp2_private','duration':120})
    assert users.get('/projects/mvp2_private/tracks',headers=headers('bob')).status_code==404
    assert users.get('/exports/'+'f'*32,headers=headers('bob')).status_code==404
    assert users.get('/studio-api/projects',headers=headers('bob')).json()=={'projects':[]}


def test_malformed_references_return_validation_error(users):
    for body in [{'session_ids':None},{'session_ids':'wrong'},{'session_ids':[123]}]:
        assert users.post('/api/compositions',headers=headers('alice'),json=body).status_code in [404,422]

def test_export_owner_tracks_project(users):
    from user_access import remember_export,current_user
    job=uuid.uuid4().hex;p=main.JOBS_DIR/job;p.mkdir()
    main.save_json(p/'job.json',{'id':job,'owner_id':'alice','created_at':'now'})
    main.save_json(p/'separation.json',{'status':'completed','project_id':'mvp2_export','duration':120})
    token=current_user.set({'id':'alice'})
    try:remember_export('e'*32,'mvp2_export')
    finally:current_user.reset(token)
    assert users.get('/exports/'+'e'*32,headers=headers('bob')).status_code==404
    assert users.get('/live-api/projects/mvp2_export/audio/drums',headers=headers('bob')).status_code==404

def test_hardware_dedupe_does_not_cross_users(users):
    import io,numpy as np,soundfile as sf
    wave=io.BytesIO();sf.write(wave,np.zeros(16000),16000,format='WAV',subtype='PCM_16')
    def remote(method,path,body=None,content_type=None):
        if method=='POST':return 200,b'{"id":"fixture","playbackPath":"/api/hardware/soundcore/recordings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/wav"}','application/json'
        return 200,wave.getvalue(),'audio/wav'
    with patch('hardware_integration.remote_hardware',side_effect=remote):
        def run(user):return users.post('/api/device/import',headers=headers(user),data={'deviceFileId':'1','durationMs':'1000'},files={'file':('same.opus',b'opus-fixture')})
        a=run('alice');b=run('bob');assert a.status_code==b.status_code==200
        assert a.json()['id']!=b.json()['id']
        assert run('alice').json()['id']==a.json()['id']


@pytest.mark.parametrize('content_type',['application/problem+json','application/json; charset=utf-8',None])
def test_json_variants_cannot_bypass_ownership(users,content_type):
    import json
    a=upload(users,'alice')
    h=headers('bob')
    if content_type:h['Content-Type']=content_type
    r=users.post('/api/sessions/bulk-delete',headers=h,content=json.dumps({'ids':[a['id']]}))
    assert r.status_code==404
    assert users.get(a['audio_url'],headers=headers('alice')).status_code==200
