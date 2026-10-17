import uuid
import hashlib
import time
from datetime import datetime, timedelta, timezone
import jwt
from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from .models import User, Project, RateLimit, Asset, FileDeletion, Draft, Work, LoginSession
from .repositories import Repository
from .schemas import UserOut, ProjectOut, AssetOut, DraftOut, WorkOut
from .errors import BusinessError

PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash("not-a-real-account-password")


class AuthService:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    def throttle(self, identity):
        key = hashlib.sha256(identity.encode()).hexdigest()
        window = int(time.time()) // 900
        stmt = insert(RateLimit).values(key=key, window=window, count=1)
        count = self.session.scalar(stmt.on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window], set_={"count": RateLimit.count + 1}
        ).returning(RateLimit.count))
        self.session.commit()
        if count > self.settings.auth_limit:
            raise BusinessError(429, "RATE_LIMITED", "Too many authentication attempts", headers={"Retry-After": "900"})

    def register(self, body):
        if not self.settings.allow_registration:
            raise BusinessError(403, "REGISTRATION_DISABLED", "Registration is disabled")
        stmt = insert(User).values(username=body.username, password_hash=PASSWORDS.hash(body.password))
        user = self.session.scalar(stmt.on_conflict_do_nothing(index_elements=[User.username]).returning(User))
        if user is None:
            raise BusinessError(409, "USERNAME_TAKEN", "Username is unavailable")
        return UserOut.model_validate(user).model_dump(mode="json")

    def login(self, body):
        user = self.session.scalar(select(User).where(User.username == body.username))
        valid = PASSWORDS.verify(body.password, user.password_hash if user else DUMMY_HASH)
        if not user or not valid:
            raise BusinessError(401, "INVALID_CREDENTIALS", "Invalid username or password", headers={"WWW-Authenticate": "Bearer"})
        now = datetime.now(timezone.utc)
        session_id=uuid.uuid4()
        expires=now+timedelta(minutes=self.settings.token_minutes)
        self.session.add(LoginSession(id=session_id,user_id=user.id,expires_at=expires,revoked=False))
        self.session.commit()
        token = jwt.encode({"sub": str(user.id), "sid":str(session_id), "iat": now, "exp": now + timedelta(minutes=self.settings.token_minutes),
                            "iss": "sonic", "aud": "sonic-android"},
                           self.settings.jwt_secret.get_secret_value(), algorithm="HS256")
        return {"access_token": token, "token_type": "bearer", "expires_in": self.settings.token_minutes * 60}


    def change_password(self,user,body):
        if not PASSWORDS.verify(body.current_password,user.password_hash):
            raise BusinessError(400,'INVALID_CURRENT_PASSWORD','Current password is incorrect')
        user.password_hash=PASSWORDS.hash(body.new_password)
        self.session.execute(update(LoginSession).where(LoginSession.user_id==user.id).values(revoked=True))
        self.session.commit()


class ProjectService:
    def __init__(self, session):
        self.session = session
        self.repo = Repository(session)

    def create(self, owner, body, key):
        def operation():
            obj = Project(owner_id=owner, name=body.name)
            self.session.add(obj)
            self.session.flush()
            return ProjectOut.model_validate(obj).model_dump(mode="json")
        return self.repo.idempotent(owner, "projects", key, body.model_dump(mode="json"), operation)

    def rename(self, owner, project_id, body):
        obj = self.repo.project(owner, project_id, lock=True)
        obj.name = body.name
        self.session.flush()
        return obj

    def delete(self, owner, project_id):
        obj = self.repo.project(owner, project_id, lock=True)
        for key in self.session.scalars(select(Asset.storage_key).where(Asset.project_id == project_id)):
            self.session.add(FileDeletion(storage_key=key))
        self.session.delete(obj)


class AssetService:
    def __init__(self, session, storage):
        self.session, self.storage = session, storage
        self.repo = Repository(session)

    def upload(self, owner, project_id, upload, key):
        self.repo.project(owner, project_id)
        # Do not hold a transaction open while decoding untrusted audio.
        self.session.rollback()
        with self.storage.stage(upload) as (path, metadata, ext):
            self.repo.project(owner, project_id, lock=True)
            def operation():
                obj = Asset(project_id=project_id, storage_key=self.storage.publish(path, ext), **metadata)
                self.session.add(obj)
                self.session.flush()
                return AssetOut.model_validate(obj).model_dump(mode='json')
            data = self.repo.idempotent(owner, f'assets:{project_id}', key, metadata, operation)
            self.session.commit()
            return data

    def clean_deleted(self):
        # Queue persists across crashes. Remove only known committed storage keys.
        for row in self.session.scalars(select(FileDeletion).with_for_update(skip_locked=True).limit(100)):
            try:
                self.storage.delete(row.storage_key)
            except OSError:
                continue
            self.session.delete(row)
        self.session.commit()


class DraftService:
    def __init__(self, session):
        self.session = session
        self.repo = Repository(session)

    def save(self, owner, project_id, body, key):
        self.repo.project(owner, project_id, lock=True)
        def operation():
            draft = self.session.get(Draft, project_id)
            version = draft.version if draft else 0
            if body.expected_version != version:
                raise BusinessError(409, 'VERSION_CONFLICT', 'Draft has changed; reload before saving', [{'current_version': version}])
            track_ids = [track.id for track in body.tracks]
            if len(set(track_ids)) != len(track_ids):
                raise BusinessError(422, 'INVALID_DRAFT', 'Track IDs must be unique')
            assets = {a.id: a for a in self.session.scalars(select(Asset).where(Asset.project_id == project_id))}
            for track in body.tracks:
                for clip in track.clips:
                    asset = assets.get(clip.asset_id)
                    if not asset or clip.offset_ms + clip.duration_ms > asset.duration_ms:
                        raise BusinessError(422, 'INVALID_DRAFT_ASSET', 'Clip must reference audio in this project within its duration')
            tracks = [track.model_dump(mode='json') for track in body.tracks]
            if draft:
                draft.version += 1
                draft.tracks = tracks
            else:
                draft = Draft(project_id=project_id, version=1, tracks=tracks)
                self.session.add(draft)
            self.session.flush()
            return DraftOut.model_validate(draft).model_dump(mode='json')
        return self.repo.idempotent(owner, f'draft:{project_id}', key, body.model_dump(mode='json'), operation)


class WorkService:
    def __init__(self, session, storage):
        self.session, self.storage = session, storage
        self.repo = Repository(session)

    def create(self, owner, project_id, body, key):
        self.repo.project(owner, project_id, lock=True)
        def operation():
            asset = self.session.scalar(select(Asset).where(Asset.id == body.asset_id, Asset.project_id == project_id))
            if not asset:
                raise BusinessError(422, 'INVALID_WORK_ASSET', 'A real audio asset in this project is required')
            self.storage.require(asset.storage_key)
            work = Work(project_id=project_id, asset_id=asset.id, title=body.title)
            self.session.add(work)
            self.session.flush()
            return WorkOut.model_validate(work).model_dump(mode='json')
        return self.repo.idempotent(owner, f'works:{project_id}', key, body.model_dump(mode='json'), operation)

    def delete(self, owner, work_id):
        work = self.repo.work(owner, work_id)
        self.repo.project(owner, work.project_id, lock=True)
        self.session.delete(work)
