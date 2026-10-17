import logging
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from .api import router
from .config import Settings
from .db import make_database
from .errors import BusinessError
from .middleware import BodyLimitMiddleware

logger = logging.getLogger("sonic")


def create_app(settings=None):
    settings = settings or Settings()
    engine, sessions = make_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="SONIC Android API", version="0.1.0", lifespan=lifespan,
                  description="Non-AI business API. Bearer authentication. All resources are private to their owner.")
    app.state.settings = settings
    app.state.engine, app.state.sessions = engine, sessions

    def error_response(request, status, code, message, details=None, headers=None):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                            "details": details or [], "request_id": getattr(request.state, "request_id", "unknown")}}, headers=headers)

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("request_id=%s failure_type=%s", request.state.request_id, type(exc).__name__)
            response = error_response(request, 500, "INTERNAL_ERROR", "Internal server error")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        route = request.scope.get("route")
        logger.info("request_id=%s method=%s route=%s status=%d elapsed_ms=%.1f", request.state.request_id,
                    request.method, getattr(route, "path", "unmatched"), response.status_code,
                    (time.monotonic() - started) * 1000)
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"field": ".".join(map(str, e["loc"])), "code": e["type"], "message": "Invalid value"} for e in exc.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "HTTP_ERROR", "Request could not be processed", headers=exc.headers)

    @app.exception_handler(OperationalError)
    async def unavailable(request, exc):
        return error_response(request, 503, "DATABASE_UNAVAILABLE", "Please retry later", headers={"Retry-After": "3"})

    app.include_router(router)
    app.add_middleware(BodyLimitMiddleware, settings=settings)
    return app
