import io,json
from unittest.mock import patch
import numpy as np,soundfile as sf
from fastapi.testclient import TestClient
from app import app

def test_hardware_import_preserves_markers_and_deduplicates():
    wave=io.BytesIO();sf.write(wave,np.zeros(16000*3),16000,format='WAV',subtype='PCM_16')
    def remote(method,path,body=None,content_type=None):
        if method=='POST':return 200,json.dumps({'id':'a'*32,'markersMs':[500,1500],'playbackPath':'/api/hardware/soundcore/recordings/'+'a'*32+'/wav'}).encode(),'application/json'
        return 200,wave.getvalue(),'audio/wav'
    with TestClient(app) as client,patch('hardware_integration.remote_hardware',side_effect=remote):
        payload={'deviceFileId':'123','durationMs':'3000','markersMs':'500,1500'}
        first=client.post('/api/device/import',data=payload,files={'file':('raw.opus',b'raw fixture')});assert first.status_code==200,first.text
        data=first.json();assert data['source']=='soundcore';assert data['markers']==[.5,1.5];assert data['hardware']['device_file_id']==123
        second=client.post('/api/device/import',data=payload,files={'file':('raw.opus',b'raw fixture')});assert second.json()['id']==data['id']
        assert client.get(data['audio_url']).status_code==200

def test_account_proxy_uses_user_jwt_not_app_token():
    from account_integration import account_headers
    assert account_headers({'authorization':'Bearer shared','x-sonic-user-token':'user-jwt'})['Authorization']=='Bearer user-jwt'


def test_registration_gateway_response(monkeypatch):
    import httpx
    from unittest.mock import AsyncMock
    response=httpx.Response(201,json={'data':{'id':'test','username':'new_user'}})
    with TestClient(app) as client,patch('account_integration.httpx.AsyncClient') as mocked:
        mocked.return_value.__aenter__.return_value.request=AsyncMock(return_value=response)
        r=client.post('/api/v1/auth/register',json={'username':'new_user','password':'long-password-fixture'})
        assert r.status_code==201,r.text


def test_hardware_sdk_identity_is_not_business_owner(monkeypatch):
    from hardware_integration import credentials,DeviceRequest
    from user_access import current_user
    context=current_user.set({'id':'business-account-uuid','username':'personal'})
    def remote(method,path,body=None,content_type=None):
        payload=json.loads(body)
        assert payload['userId']=='sonic-demo'
        assert payload['deviceId']=='installation-device-123'
        return 200,json.dumps({'userId':payload['userId'],'deviceId':payload['deviceId'],'token':'fixture-token','licenseJson':'{}','licenseSignature':'fixture-signature','environment':'DEVELOPMENT'}).encode(),'application/json'
    try:
        with patch('hardware_integration.remote_hardware',side_effect=remote):
            assert credentials(DeviceRequest(deviceId='installation-device-123'))['userId']=='sonic-demo'
    finally:current_user.reset(context)
