"""Local editor routes tunnel only to the existing loopback AutoDL service."""
import http.client
import json
from pathlib import Path
import re
import shlex
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse,JSONResponse
from providers import connect_yue2, settings

router=APIRouter()
PROJECT=r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}'
TRACK=r'(?:vocals|drums|bass|piano|guitar|other)'


def allowed_live_path(method,path):
    patterns = ([r'/studio-api/projects',rf'/projects/{PROJECT}/tracks',
        rf'/studio-api/projects/{PROJECT}/waveforms/(?:original|{TRACK})',
        rf'/live-api/projects/{PROJECT}/audio/(?:original|{TRACK})',r'/exports/[a-f0-9]{32}']
        if method=='GET' else [rf'/projects/{PROJECT}/render',rf'/live-api/projects/{PROJECT}/delta/{TRACK}']
        if method=='POST' else [])
    return any(re.fullmatch(p,path) for p in patterns)


def tunnel_response(method,path,body=None):
    if not allowed_live_path(method,path):
        raise HTTPException(404,'没有对应的编辑接口。')
    client=None
    connection=None
    try:
        client=connect_yue2()
        channel=client.get_transport().open_channel('direct-tcpip',('127.0.0.1',8001),('127.0.0.1',0),timeout=15)
        channel.settimeout(180)
        connection=http.client.HTTPConnection('127.0.0.1',8001,timeout=180)
        connection.sock=channel
        connection.request(method,path,body=body,headers={'Content-Type':'application/json'})
        response=connection.getresponse()
    except Exception:
        if connection:connection.close()
        if client:client.close()
        raise HTTPException(502,'音轨编辑服务连接失败，请检查 AutoDL 的 8001 服务。')
    def chunks():
        try:
            while True:
                chunk=response.read(65536)
                if not chunk:break
                yield chunk
        finally:
            connection.close();client.close()
    headers={'Cache-Control':'no-store'}
    disposition=response.getheader('Content-Disposition')
    if disposition:headers['Content-Disposition']=disposition
    return StreamingResponse(chunks(),status_code=response.status,
        media_type=response.getheader('Content-Type','application/octet-stream'),headers=headers)


@router.get('/studio-api/projects')
@router.get('/studio-api/projects/{project_id}/waveforms/{track_id}')
@router.get('/projects/{project_id}/tracks')
@router.get('/live-api/projects/{project_id}/audio/{track_id}')
@router.get('/exports/{file_id}')
def editor_get(request:Request):
    from user_access import enabled,projects
    if enabled() and request.url.path=='/studio-api/projects':return JSONResponse({'projects':projects()})
    return tunnel_response('GET',request.url.path)


@router.post('/projects/{project_id}/render')
@router.post('/live-api/projects/{project_id}/delta/{track_id}')
async def editor_post(request:Request):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>65536:raise HTTPException(413,'编辑指令过大。')
    try:json.loads(body)
    except ValueError:raise HTTPException(422,'编辑指令必须是 JSON。')
    from starlette.concurrency import run_in_threadpool
    response=await run_in_threadpool(tunnel_response,'POST',request.url.path,bytes(body))
    from user_access import enabled,remember_export
    if enabled() and request.url.path.endswith('/render') and response.status_code==200:
        data=b''.join([part if isinstance(part,bytes) else part.encode() async for part in response.body_iterator])
        result=json.loads(data);match=re.fullmatch(r'/exports/([a-f0-9]{32})',result.get('download_url',''))
        if not match:raise HTTPException(502,'导出结果无效。')
        remember_export(match[1],request.path_params['project_id'])
        return JSONResponse(result)
    return response


def separate_yue_file(identity,directory,progress):
    """Upload the unmodified YuE2 output, then create an isolated remote project."""
    if not re.fullmatch('[a-f0-9]{32}',identity):raise ValueError('Invalid job')
    directory=Path(directory)
    source=directory/'generated.flac'
    if not source.is_file():raise ValueError('YuE2 原始生成文件不存在，不能分轨。')
    import soundfile as sf
    info=sf.info(source)
    if not 0<info.duration<=300:raise ValueError('编辑器支持最多 300 秒音频。')
    project='mvp2_'+uuid.uuid4().hex
    remote='/srv/sonic/outputs/live-import-'+project
    script=Path(__file__).with_name('live_separate_runtime.py')
    progress('uploading','正在将 YuE2 原曲交给音轨编辑模块。')
    with connect_yue2() as client,client.open_sftp() as sftp:
        sftp.get_channel().settimeout(30);sftp.mkdir(remote)
        sftp.put(str(source),remote+'/input.flac');sftp.put(str(script),remote+'/separate.py')
        command=' '.join(shlex.quote(v) for v in ['timeout','870',
            '/srv/sonic/AudioBackend/.venv/bin/python','-u',remote+'/separate.py',remote,project])
        progress('separating','Demucs 正在分离人声、鼓、贝斯、钢琴、吉他和其他音轨。')
        _,out,_=client.exec_command(command,timeout=900)
        ch,log,started=out.channel,bytearray(),time.monotonic()
        while True:
            for ready,receive in ((ch.recv_ready,ch.recv),(ch.recv_stderr_ready,ch.recv_stderr)):
                if ready():log.extend(receive(8192))
            if len(log)>524288:del log[:-262144]
            if ch.exit_status_ready() and not ch.recv_ready() and not ch.recv_stderr_ready():break
            if time.monotonic()-started>890:raise TimeoutError('分轨超时，已保留原曲与日志。')
            time.sleep(.2)
        (directory/'separation.log').write_bytes(log)
        if ch.recv_exit_status()!=0:
            # Do not expose teammate paths or server logs through the browser.
            raise RuntimeError('分轨未完成，请查看本地 separation.log；原歌曲保持可用。')
        manifest='/srv/sonic/AudioBackend/mvp_projects/'+project+'.json'
        sftp.stat(manifest)
    return {'project_id':project,'editor_url':'/live/?project='+project,
        'source':'yue2_generated_original','duration':info.duration,'remote_directory':remote}
