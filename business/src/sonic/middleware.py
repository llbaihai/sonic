import asyncio
import tempfile
import uuid
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    """Bound chunked bodies before the multipart parser spools files.

Uses a temporary spool, a total request deadline, and a separate small JSON cap.
The spool is always closed, including disconnects and parsing failures.
"""
    def __init__(self, app, settings):
        self.app = app
        self.upload_limit = settings.max_upload_bytes + 65536

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        multipart = headers.get(b'content-type', b'').lower().startswith(b'multipart/form-data')
        limit = self.upload_limit if multipart else 1024 * 1024

        async def reject(status, code, message):
            request_id = str(uuid.uuid4())
            response = JSONResponse({'error': {'code': code, 'message': message, 'details': [], 'request_id': request_id}},
                status_code=status, headers={'X-Request-ID': request_id, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
            await response(scope, receive, send)

        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await reject(400, 'INVALID_CONTENT_LENGTH', 'Invalid Content-Length')
        if declared < 0:
            return await reject(400, 'INVALID_CONTENT_LENGTH', 'Invalid Content-Length')
        if declared > limit:
            return await reject(413, 'UPLOAD_TOO_LARGE', 'Request exceeds the size limit')
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            total = 0
            try:
                async with asyncio.timeout(90):
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            return
                        chunk = message.get('body', b'')
                        total += len(chunk)
                        if total > limit:
                            return await reject(413, 'UPLOAD_TOO_LARGE', 'Request exceeds the size limit')
                        body.write(chunk)
                        if not message.get('more_body', False):
                            break
            except TimeoutError:
                return await reject(408, 'REQUEST_TIMEOUT', 'Upload exceeded 90 seconds; retry with the same idempotency key')
            body.seek(0)
            delivered = 0
            complete = False

            async def replay():
                nonlocal delivered, complete
                if complete:
                    return await receive()
                chunk = body.read(64 * 1024)
                delivered += len(chunk)
                complete = delivered >= total
                return {'type': 'http.request', 'body': chunk, 'more_body': not complete}

            await self.app(scope, replay, send)
