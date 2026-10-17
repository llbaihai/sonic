"""Single-team prototype access control; never exposes model administration."""
import hmac
import os
from fastapi import Request
from fastapi.responses import JSONResponse,RedirectResponse

def enabled():return os.environ.get('SONIC_PUBLIC_MODE')=='1'

def install(app):
    @app.middleware('http')
    async def protect(request:Request,call_next):
        if not enabled():return await call_next(request)
        token=os.environ.get('SONIC_APP_TOKEN','')
        if len(token)<32:return JSONResponse({'detail':'Server access is not configured'},status_code=503)
        path=request.url.path
        if path=='/health':return JSONResponse({'status':'ok'})
        authorization=request.headers.get('authorization','')
        supplied=authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('sonic_app','')
        if not hmac.compare_digest(token,supplied):return JSONResponse({'detail':'请使用已授权的 SONIC App 登录'},status_code=401)
        if path=='/api/app/login' and request.method=='POST':
            response=JSONResponse({'status':'ok'})
            response.set_cookie('sonic_app',token,httponly=True,secure=True,samesite='strict',max_age=86400)
            return response
        if path=='/':return RedirectResponse('/mobile/')
        allowed=path.startswith(('/mobile/','/live/','/vendor/','/media/','/results/','/exports/',
            '/projects/','/studio-api/','/live-api/','/api/sessions','/api/jobs','/api/compositions','/api/device/','/api/v1/','/api/account/')) or path in {
            '/api/status','/api/sample','/api/music-styles','/api/prompts/translate'}
        if not allowed:return JSONResponse({'detail':'接口未开放'},status_code=404)
        return await call_next(request)
