"""Same-origin gateway to the teammate's unchanged PostgreSQL business API."""
import httpx,subprocess,uuid,hashlib
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field
from user_access import token_for,resolve_token
router=APIRouter()
class ArchiveRequest(BaseModel):
    job_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    project_id:str

@router.post('/api/account/archive')
def archive_work(request:ArchiveRequest,incoming:Request):
    from app import folder,JOBS_DIR,read_json
    try:uuid.UUID(request.project_id)
    except ValueError:raise HTTPException(422,'项目编号无效。')
    token=token_for(incoming)
    if not token:raise HTTPException(401,'请先登录账号。')
    headers={'Authorization':'Bearer '+token}
    job_folder=folder(JOBS_DIR,request.job_id);job=read_json(job_folder/'job.json')
    if job.get('status')!='completed':raise HTTPException(400,'请等待音乐生成完成。')
    with httpx.Client(base_url='http://127.0.0.1:8790/api/v1',timeout=150,trust_env=False) as client:
        check=client.get('/projects/'+request.project_id,headers=headers)
        if check.status_code!=200:raise HTTPException(check.status_code,'无法访问该账号项目。')
        artifact=job_folder/'account-archive.m4a'
        if not artifact.exists():
            result=subprocess.run(['ffmpeg','-v','error','-i',str(job_folder/'mix.wav'),'-c:a','aac','-b:a','160k','-y',str(artifact)],capture_output=True,timeout=60)
            if result.returncode!=0:raise HTTPException(502,'作品归档转码失败，原WAV仍保留。')
        key='song-'+request.job_id
        with artifact.open('rb') as stream:
            response=client.post('/projects/'+request.project_id+'/assets',headers={**headers,'Idempotency-Key':key},files={'file':('SONIC-'+request.job_id[:8]+'.m4a',stream,'audio/mp4')})
        if response.status_code not in [200,201]:raise HTTPException(response.status_code,'账号素材归档未完成。')
        asset=response.json()['data'];response=client.post('/projects/'+request.project_id+'/works',headers={**headers,'Idempotency-Key':'work-'+request.job_id},json={'title':(job.get('name') or '声音创作')[:120],'asset_id':asset['id']})
        if response.status_code not in [200,201]:raise HTTPException(response.status_code,'账号作品归档未完成。')
        return response.json()
def account_headers(headers):
    result={}
    token=headers.get('x-sonic-user-token','')
    if token:result['Authorization']='Bearer '+token
    for name in ['content-type','idempotency-key','range']:
        if headers.get(name):result[name]=headers[name]
    return result

@router.api_route('/api/v1/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
async def account_proxy(path:str,request:Request):
    body=await request.body()
    if len(body)>12*1024*1024:raise HTTPException(413,'请求超过当前App上限。')
    try:
        async with httpx.AsyncClient(timeout=150,trust_env=False) as client:
            forwarded=account_headers(request.headers)
            client_host=request.client.host if request.client else 'unknown'
            forwarded['X-Sonic-Auth-Client']=hashlib.sha256(client_host.encode()).hexdigest()
            token=token_for(request)
            if token:forwarded['Authorization']='Bearer '+token
            response=await client.request(request.method,'http://127.0.0.1:8790/api/v1/'+path,params=request.query_params,content=body,headers=forwarded)
        headers={key:value for key,value in response.headers.items() if key in ['content-type','content-range','accept-ranges','x-content-sha256','retry-after']}
        result=Response(response.content,status_code=response.status_code,headers={**headers,'Cache-Control':'no-store'})
        if response.status_code==200 and path=='auth/login':
            data=response.json()['data']
            result.set_cookie('sonic_user',data['access_token'],httponly=True,secure=(request.url.scheme=='https' or __import__('public_access').enabled()),samesite='strict',max_age=data['expires_in'])
        if response.status_code==200 and path in ['auth/logout','auth/password']:
            result.delete_cookie('sonic_user',httponly=True,secure=(request.url.scheme=='https' or __import__('public_access').enabled()),samesite='strict')
        return result
    except httpx.RequestError as error:raise HTTPException(503,'账号服务暂时不可用，请稍后重试。') from error

@router.get('/api/account/session')
def account_session(request:Request):
    user=resolve_token(token_for(request))
    if not user:raise HTTPException(401,'请先登录账号。')
    return __import__('fastapi.responses',fromlist=['JSONResponse']).JSONResponse({'user':user},headers={'Cache-Control':'no-store'})

@router.post('/api/account/session')
def sync_session(request:Request):
    user=resolve_token(token_for(request))
    if not user:raise HTTPException(401,'请先登录账号。')
    response=Response(content='{"status":"ok"}',media_type='application/json')
    # The token's expiry is checked by the account backend on every private request.
    response.set_cookie('sonic_user',token_for(request),httponly=True,secure=(request.url.scheme=='https' or __import__('public_access').enabled()),samesite='strict',max_age=30*86400)
    return response
