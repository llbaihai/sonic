"""Reuse the deployed Soundcore backend via the already pinned SSH transport."""
import hashlib,http.client,io,json,os,re,threading,uuid
from datetime import datetime,timezone
from pathlib import Path
import httpx,soundfile as sf
from fastapi import APIRouter,UploadFile,File,Form,HTTPException
from pydantic import BaseModel,Field
from starlette.concurrency import run_in_threadpool
from providers import connect_yue2
from user_access import stamp,visible

router=APIRouter(prefix='/api/device');lock=threading.Lock()
def remote_hardware(method,path,body=None,content_type=None):
    if not re.fullmatch(r'/api/hardware/soundcore/(?:credentials|recordings(?:/[a-f0-9]{32}/wav)?)',path):raise ValueError('Invalid hardware path')
    with connect_yue2() as client,client.open_sftp() as files:
        with files.file('/srv/sonic/hardware-service/private/environment.json','rb') as stream:config=json.loads(stream.read())
        channel=client.get_transport().open_channel('direct-tcpip',('127.0.0.1',8765),('127.0.0.1',0),timeout=15);channel.settimeout(150)
        connection=http.client.HTTPConnection('127.0.0.1',8765,timeout=150);connection.sock=channel
        try:
            headers={'Authorization':'Bearer '+config['SONIC_HARDWARE_ACCESS_TOKEN']}
            if content_type:headers['Content-Type']=content_type
            connection.request(method,path,body=body,headers=headers);response=connection.getresponse();data=response.read(64*1024*1024+1)
            if len(data)>64*1024*1024:raise ValueError('Decoded recording exceeds limit')
            return response.status,data,response.getheader('Content-Type','application/json')
        finally:connection.close()

class DeviceRequest(BaseModel):
    deviceId:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.:@-]+$')

@router.post('/credentials')
def credentials(request:DeviceRequest):
    try:
        # The deployed SDK gateway authorizes its configured hardware identity.
        # Business account ownership remains enforced on credentials access/import.
        sdk_user=os.environ.get('SONIC_HARDWARE_SDK_USER_ID','sonic-demo')
        status,data,_=remote_hardware('POST','/api/hardware/soundcore/credentials',json.dumps({'userId':sdk_user,'deviceId':request.deviceId}).encode(),'application/json')
        if status!=200:raise HTTPException(status,'录音豆授权暂未完成，请检查云端授权配置。')
        return json.loads(data)
    except HTTPException:raise
    except Exception as error:raise HTTPException(502,'录音豆服务暂时无法连接，请稍后重试。') from error

@router.post('/import')
async def import_recording(file:UploadFile=File(...),deviceFileId:int=Form(...),durationMs:int=Form(...),markersMs:str=Form('')):
    blob=await file.read(12*1024*1024+1);await file.close()
    if not blob or len(blob)>12*1024*1024:raise HTTPException(413,'请同步较短的录音，当前最多12MB。')
    if durationMs<=0 or durationMs>240000:raise HTTPException(422,'当前创作支持4分钟以内的设备录音，请选择较短片段。')
    try:marks=[int(value) for value in markersMs.split(',') if value.strip()]
    except ValueError:raise HTTPException(422,'重点标记格式无效。')
    if len(marks)>32 or any(value<0 or value>=durationMs for value in marks):raise HTTPException(422,'当前最多32个有效重点标记。')
    return await run_in_threadpool(import_bytes,blob,deviceFileId,durationMs,marks)

def import_bytes(blob,file_id,duration,marks):
    from app import SESSIONS_DIR,read_json,save_json
    digest=hashlib.sha256(blob).hexdigest()
    with lock:
        for metadata in SESSIONS_DIR.glob('*/session.json'):
            record=read_json(metadata)
            if visible(record) and record.get('hardware',{}).get('sha256')==digest:return record
        try:
            req=httpx.Request('POST','http://localhost',files={'file':('recording.opus',blob,'application/octet-stream')},data={'deviceFileId':str(file_id),'durationMs':str(duration),'markersMs':','.join(map(str,marks))})
            payload=req.read();status,data,_=remote_hardware('POST','/api/hardware/soundcore/recordings',payload,req.headers['Content-Type'])
            if status!=200:raise HTTPException(422,'录音豆音频上传或解码失败，原始设备录音仍保留。')
            result=json.loads(data);status,wave,_=remote_hardware('GET',result['playbackPath'])
            if status!=200:raise HTTPException(502,'解码录音尚未可读取，请重试。')
            info=sf.info(io.BytesIO(wave));actual=info.duration
            if actual<=0 or actual>240:raise HTTPException(422,'解码后的录音需在4分钟以内。')
            identity=uuid.uuid4().hex;folder=SESSIONS_DIR/identity;folder.mkdir()
            (folder/'audio.wav').write_bytes(wave);(folder/'original.opus').write_bytes(blob)
            record={'id':identity,'name':'录音豆 · '+datetime.now().strftime('%m月%d日 %H:%M'),'source':'soundcore','duration':round(actual,4),'sample_rate':info.samplerate,'created_at':datetime.now(timezone.utc).isoformat(),'audio_url':f'/media/{identity}/audio.wav','original_url':f'/media/{identity}/audio.wav','markers':[value/1000 for value in marks if value/1000<actual],'markers_origin':'soundcore_device','analysis':None,'hardware':{'device_file_id':file_id,'markers_ms':marks,'sha256':digest,'cloud_recording_id':result['id']}}
            save_json(folder/'session.json',stamp(record));return record
        except HTTPException:raise
        except Exception as error:raise HTTPException(502,'录音同步未完成，请重试；不会删除设备原录音。') from error
