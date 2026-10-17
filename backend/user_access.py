"""Account identity and resource ownership for the whole music workflow."""
from contextvars import ContextVar
import json,os,re
import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse

current_user=ContextVar('sonic_user',default=None)
def enabled():return os.environ.get('SONIC_USER_ACCOUNTS')=='1'
def owner_id():
    user=current_user.get()
    return user['id'] if user else None
def token_for(request):return request.headers.get('x-sonic-user-token') or request.cookies.get('sonic_user','')
def resolve_token(token):
    if not token:return None
    try:
        with httpx.Client(timeout=8,trust_env=False) as client:
            response=client.get('http://127.0.0.1:8790/api/v1/users/me',headers={'Authorization':'Bearer '+token})
        if response.status_code==401:return None
        if response.status_code!=200:raise HTTPException(503,'账号服务暂时不可用，请稍后重试。')
        return response.json()['data']
    except httpx.RequestError as error:raise HTTPException(503,'账号服务暂时不可用，请稍后重试。') from error

def visible(record):
    return not enabled() or (owner_id() is not None and record.get('owner_id')==owner_id())
def stamp(record):
    if enabled() and owner_id():record['owner_id']=owner_id()
    return record
def require_resource(kind,identity):
    if not enabled():return
    import app
    if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{32}',identity):raise HTTPException(404,'内容不存在。')
    directory=app.SESSIONS_DIR if kind=='sessions' else app.JOBS_DIR
    try:record=json.loads((directory/identity/('session.json' if kind=='sessions' else 'job.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError):raise HTTPException(404,'内容不存在。')
    if not visible(record):raise HTTPException(404,'内容不存在。')

def projects():
    import app
    result=[]
    for path in app.JOBS_DIR.glob('*/separation.json'):
        try:
            job=json.loads((path.parent/'job.json').read_text(encoding='utf-8'))
            info=json.loads(path.read_text(encoding='utf-8'))
            if visible(job) and info.get('status')=='completed':result.append({'id':info['project_id'],'duration':info.get('duration',job.get('duration',0))})
        except (OSError,ValueError,KeyError):continue
    return result

def export_file(identity):
    import app
    if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{32}',identity):raise HTTPException(404,'内容不存在。')
    return app.DATA/'export-owners'/(identity+'.json')
def remember_export(identity,project):
    if not enabled():return
    import app
    path=export_file(identity);path.parent.mkdir(exist_ok=True)
    app.save_json(path,{'owner_id':owner_id(),'project_id':project})

def install(app):
    @app.middleware('http')
    async def private_resources(request,call_next):
        if not enabled():return await call_next(request)
        path=request.url.path
        if request.method=='POST' and request.headers.get('x-http-method-override')=='PATCH':request.scope['method']='PATCH'
        # Public here means the app shell; public_access still enforces the app gate.
        if path.startswith(('/mobile/','/vendor/')) or path in ['/','/health','/api/app/login','/api/status','/api/music-styles'] or path.startswith('/api/v1/auth/') or path=='/api/account/session':
            return await call_next(request)
        from starlette.concurrency import run_in_threadpool
        context=None
        try:
            user=await run_in_threadpool(resolve_token,token_for(request))
            if not user:raise HTTPException(401,'请先登录账号。')
            context=current_user.set(user);request.state.user=user
            match=re.match(r'^/api/(sessions|jobs)/([^/]+)',path)
            if match and match[2]!='bulk-delete':require_resource(match[1],match[2])
            media=re.match(r'^/(media|results)/([^/]+)',path)
            if media:require_resource('sessions' if media[1]=='media' else 'jobs',media[2])
            live=re.match(r'^/(?:projects|studio-api/projects|live-api/projects)/([^/]+)',path)
            if live and live[1] not in {p['id'] for p in projects()}:raise HTTPException(404,'内容不存在。')
            if path.startswith('/exports/'):
                try:record=json.loads(export_file(path.split('/')[2]).read_text(encoding='utf-8'))
                except (OSError,ValueError):raise HTTPException(404,'内容不存在。')
                if not visible(record) or record['project_id'] not in {p['id'] for p in projects()}:raise HTTPException(404,'内容不存在。')
            media_type=request.headers.get('content-type','').split(';',1)[0].lower().strip()
            json_body=not media_type or media_type=='application/json' or (media_type.startswith('application/') and media_type.endswith('+json'))
            if request.method in ['POST','PATCH','PUT'] and json_body:
                try:body=await request.json()
                except ValueError:body={}
                if isinstance(body,dict):
                    sources=[]
                    for key in ['session_ids','selected_session_ids']:
                        values=body.get(key,[])
                        if not isinstance(values,list):raise HTTPException(422,'素材编号格式无效。')
                        sources.extend(values)
                    if body.get('session_id'):sources.append(body['session_id'])
                    for identity in sources:require_resource('sessions',identity)
                    if body.get('parent_job_id'):require_resource('jobs',body['parent_job_id'])
                    if path.endswith('/bulk-delete'):
                        ids=body.get('ids',[])
                        if not isinstance(ids,list):raise HTTPException(422,'编号格式无效。')
                        for identity in ids:require_resource('sessions' if '/sessions/' in path else 'jobs',identity)
                    if path=='/api/account/archive':require_resource('jobs',body.get('job_id',''))
            response=await call_next(request)
            if not path.startswith('/live/'):
                response.headers['Cache-Control']='private, no-store'
            return response
        except HTTPException as error:return JSONResponse({'detail':error.detail},status_code=error.status_code,headers={'Cache-Control':'private, no-store'})
        finally:
            if context is not None:current_user.reset(context)
