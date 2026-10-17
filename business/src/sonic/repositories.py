import hashlib
import json
from sqlalchemy import select, text
from .models import Project, Idempotency, Asset, Work, Draft
from .errors import BusinessError, not_found


class Repository:
    def __init__(self, session):
        self.session = session

    def project(self, owner_id, project_id, lock=False):
        stmt = select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        if lock:
            stmt = stmt.with_for_update()
        obj = self.session.scalar(stmt)
        if obj is None:
            raise not_found()
        return obj

    def page(self, model, *filters, cursor=None, limit=20):
        stmt = select(model).where(*filters)
        if cursor:
            stmt = stmt.where(model.id > cursor)
        rows = list(self.session.scalars(stmt.order_by(model.id).limit(limit + 1)))
        return rows[:limit], str(rows[limit - 1].id) if len(rows) > limit else None

    def asset(self, owner_id, asset_id):
        obj = self.session.scalar(select(Asset).join(Project).where(Asset.id == asset_id, Project.owner_id == owner_id))
        if obj is None:
            raise not_found()
        return obj

    def work(self, owner_id, work_id):
        obj = self.session.scalar(select(Work).join(Project).where(Work.id == work_id, Project.owner_id == owner_id))
        if obj is None:
            raise not_found()
        return obj

    def draft(self, owner_id, project_id):
        self.project(owner_id, project_id)
        obj = self.session.get(Draft, project_id)
        if obj is None:
            raise not_found()
        return obj

    def idempotent(self, owner_id, scope, key, payload, operation):
        if key is None:
            return operation()
        if not 1 <= len(key) <= 128 or not all(33 <= ord(c) <= 126 for c in key):
            raise BusinessError(422, "INVALID_IDEMPOTENCY_KEY", "Use 1-128 visible ASCII characters")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        lock_id = int.from_bytes(hashlib.sha256(f"{owner_id}:{scope}:{key}".encode()).digest()[:8], 'big', signed=True)
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        row = self.session.get(Idempotency, (owner_id, scope, key))
        if row:
            if row.fingerprint != fingerprint:
                raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "Key was already used with different content")
            return row.response
        response = operation()
        self.session.add(Idempotency(owner_id=owner_id, scope=scope, key=key,
                                     fingerprint=fingerprint, response=response))
        self.session.flush()
        return response
