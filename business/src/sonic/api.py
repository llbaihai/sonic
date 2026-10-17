from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID
import jwt
from fastapi import APIRouter, Depends, Header, Query, Request, Response, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .errors import BusinessError
from .models import User, Project, Asset, Work, LoginSession
from .repositories import Repository
from .services import AuthService, ProjectService, AssetService, DraftService, WorkService
from .storage import LocalStorage
from .schemas import Data, Page, UserOut, TokenOut, Register, Login, PasswordChange, ProjectInput, ProjectOut, ErrorResponse
from .schemas import AssetOut, DraftInput, DraftOut, WorkInput, WorkOut

router = APIRouter(prefix="/api/v1", responses={code: {"model": ErrorResponse} for code in [401, 403, 404, 409, 413, 415, 422, 429, 500, 503]})
bearer = HTTPBearer(auto_error=False)


def session_dep(request: Request):
    with request.app.state.sessions() as session:
        yield session


Session = Annotated[object, Depends(session_dep)]


def user_dep(request: Request, session: Session,
             credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    try:
        if credentials is None:
            raise ValueError()
        claims = jwt.decode(credentials.credentials, request.app.state.settings.jwt_secret.get_secret_value(),
                            algorithms=["HS256"], audience="sonic-android", issuer="sonic",
                            options={"require": ["sub", "iat", "exp", "aud", "iss"]})
        user = session.get(User, UUID(claims["sub"]))
        login=session.get(LoginSession,UUID(claims.get('sid','')))
        if user is None or login is None or login.user_id!=user.id or login.revoked or login.expires_at<=datetime.now(timezone.utc):
            raise ValueError()
        request.state.login_session=login
        return user
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise BusinessError(401, "UNAUTHORIZED", "A valid access token is required", headers={"WWW-Authenticate": "Bearer"})


CurrentUser = Annotated[User, Depends(user_dep)]
Key = Annotated[str | None, Header(alias="Idempotency-Key")]


def auth_client(request):
    host=request.client.host if request.client else 'unknown'
    # This header is accepted only from our loopback application gateway.
    if host in ['127.0.0.1','::1']:
        forwarded=request.headers.get('x-sonic-auth-client','')
        if forwarded and len(forwarded)<=160:return forwarded
    return host


@router.post("/auth/register", response_model=Data[UserOut], status_code=201, tags=["auth"])
def register(body: Register, request: Request, response: Response, session: Session):
    service = AuthService(session, request.app.state.settings)
    service.throttle("register:" + auth_client(request))
    data = service.register(body)
    session.commit()
    response.headers["Location"] = "/api/v1/users/me"
    return {"data": data}


@router.post("/auth/login", response_model=Data[TokenOut], tags=["auth"])
def login(body: Login, request: Request, session: Session):
    service = AuthService(session, request.app.state.settings)
    service.throttle("login-ip:" + auth_client(request))
    service.throttle("login-user:" + body.username)
    return {"data": service.login(body)}


@router.get("/users/me", response_model=Data[UserOut], tags=["auth"])
def me(user: CurrentUser):
    return {"data": user}


@router.post("/projects", response_model=Data[ProjectOut], status_code=201, tags=["projects"])
def create_project(body: ProjectInput, response: Response, session: Session, user: CurrentUser, idempotency_key: Key = None):
    data = ProjectService(session).create(user.id, body, idempotency_key)
    session.commit()
    response.headers["Location"] = f"/api/v1/projects/{data['id']}"
    return {"data": data}


@router.get("/projects", response_model=Page[ProjectOut], tags=["projects"])
def projects(session: Session, user: CurrentUser, cursor: UUID | None = None, limit: int = Query(20, ge=1, le=100)):
    data, cursor = Repository(session).page(Project, Project.owner_id == user.id, cursor=cursor, limit=limit)
    return {"data": data, "next_cursor": cursor}


@router.get("/projects/{project_id}", response_model=Data[ProjectOut], tags=["projects"])
def get_project(project_id: UUID, session: Session, user: CurrentUser):
    return {"data": Repository(session).project(user.id, project_id)}


@router.patch("/projects/{project_id}", response_model=Data[ProjectOut], tags=["projects"])
def rename_project(project_id: UUID, body: ProjectInput, session: Session, user: CurrentUser):
    data = ProjectService(session).rename(user.id, project_id, body)
    session.commit()
    return {"data": data}


@router.delete("/projects/{project_id}", status_code=204, tags=["projects"])
def delete_project(project_id: UUID, request: Request, session: Session, user: CurrentUser):
    ProjectService(session).delete(user.id, project_id)
    session.commit()
    AssetService(session, LocalStorage(request.app.state.settings)).clean_deleted()
    return Response(status_code=204)


@router.post('/projects/{project_id}/assets', status_code=201, response_model=Data[AssetOut], tags=['assets'])
def upload_asset(project_id: UUID, request: Request, response: Response, session: Session, user: CurrentUser,
                 file: Annotated[UploadFile, File()], idempotency_key: Key = None):
    data = AssetService(session, LocalStorage(request.app.state.settings)).upload(user.id, project_id, file, idempotency_key)
    response.headers['Location'] = f"/api/v1/assets/{data['id']}"
    return {'data': data}


@router.get('/projects/{project_id}/assets', response_model=Page[AssetOut], tags=['assets'])
def assets(project_id: UUID, session: Session, user: CurrentUser, cursor: UUID | None = None, limit: int = Query(20, ge=1, le=100)):
    repo = Repository(session)
    repo.project(user.id, project_id)
    data, cursor = repo.page(Asset, Asset.project_id == project_id, cursor=cursor, limit=limit)
    return {'data': data, 'next_cursor': cursor}


@router.get('/assets/{asset_id}', response_model=Data[AssetOut], tags=['assets'])
def get_asset(asset_id: UUID, session: Session, user: CurrentUser):
    return {'data': Repository(session).asset(user.id, asset_id)}


@router.get('/assets/{asset_id}/download', response_class=FileResponse, tags=['assets'],
            responses={200: {'content': {'application/octet-stream': {}}}, 206: {'description': 'Partial content'}})
def download_asset(asset_id: UUID, request: Request, session: Session, user: CurrentUser):
    asset = Repository(session).asset(user.id, asset_id)
    path = LocalStorage(request.app.state.settings).require(asset.storage_key)
    return FileResponse(path, media_type=asset.media_type, filename=asset.filename,
                        headers={'X-Content-SHA256': asset.sha256})


@router.get('/projects/{project_id}/draft', response_model=Data[DraftOut], tags=['drafts'])
def get_draft(project_id: UUID, session: Session, user: CurrentUser):
    return {'data': Repository(session).draft(user.id, project_id)}


@router.put('/projects/{project_id}/draft', response_model=Data[DraftOut], tags=['drafts'])
def save_draft(project_id: UUID, body: DraftInput, session: Session, user: CurrentUser, idempotency_key: Key = None):
    data = DraftService(session).save(user.id, project_id, body, idempotency_key)
    session.commit()
    return {'data': data}


@router.post('/projects/{project_id}/works', response_model=Data[WorkOut], status_code=201, tags=['works'])
def create_work(project_id: UUID, body: WorkInput, request: Request, response: Response, session: Session, user: CurrentUser,
                idempotency_key: Key = None):
    data = WorkService(session, LocalStorage(request.app.state.settings)).create(user.id, project_id, body, idempotency_key)
    session.commit()
    response.headers['Location'] = f"/api/v1/works/{data['id']}"
    return {'data': data}


@router.get('/projects/{project_id}/works', response_model=Page[WorkOut], tags=['works'])
def works(project_id: UUID, session: Session, user: CurrentUser, cursor: UUID | None = None, limit: int = Query(20, ge=1, le=100)):
    repo = Repository(session)
    repo.project(user.id, project_id)
    data, cursor = repo.page(Work, Work.project_id == project_id, cursor=cursor, limit=limit)
    return {'data': data, 'next_cursor': cursor}


@router.get('/works/{work_id}', response_model=Data[WorkOut], tags=['works'])
def get_work(work_id: UUID, session: Session, user: CurrentUser):
    return {'data': Repository(session).work(user.id, work_id)}


@router.get('/works/{work_id}/download', response_class=FileResponse, tags=['works'],
            responses={200: {'content': {'application/octet-stream': {}}}, 206: {'description': 'Partial content'}})
def download_work(work_id: UUID, request: Request, session: Session, user: CurrentUser):
    work = Repository(session).work(user.id, work_id)
    return download_asset(work.asset_id, request, session, user)


@router.delete('/works/{work_id}', status_code=204, tags=['works'])
def delete_work(work_id: UUID, request: Request, session: Session, user: CurrentUser):
    WorkService(session, LocalStorage(request.app.state.settings)).delete(user.id, work_id)
    session.commit()
    return Response(status_code=204)


@router.post('/auth/logout',tags=['auth'])
def logout(request:Request,session:Session,user:CurrentUser):
    request.state.login_session.revoked=True
    session.commit()
    return {'data':{'logged_out':True}}

@router.post('/auth/password',tags=['auth'])
def password(body:PasswordChange,request:Request,session:Session,user:CurrentUser):
    service=AuthService(session,request.app.state.settings)
    service.throttle('password-user:'+str(user.id))
    service.change_password(user,body)
    return {'data':{'password_changed':True,'reauthenticate':True}}
