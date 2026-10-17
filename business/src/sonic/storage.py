import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from .errors import BusinessError


class LocalStorage:
    def __init__(self, settings):
        self.root = settings.storage_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit = settings.max_upload_bytes

    def path(self, key):
        if not re.fullmatch(r'[0-9a-f]{32}\.(wav|m4a|aac|part)', key):
            raise BusinessError(500, 'STORAGE_ERROR', 'Invalid stored file reference')
        path = (self.root / key).resolve()
        if path.parent != self.root or (self.root / key).is_symlink():
            raise BusinessError(500, 'STORAGE_ERROR', 'Invalid stored file reference')
        return path

    @contextmanager
    def stage(self, upload):
        filename = upload.filename or ''
        if not filename or len(filename) > 160 or any(c in filename for c in '/\\:') or any(ord(c) < 32 for c in filename):
            raise BusinessError(422, 'INVALID_FILENAME', 'A plain audio filename is required')
        ext = Path(filename).suffix.lower()
        allowed = {'.wav': {'audio/wav', 'audio/x-wav'}, '.m4a': {'audio/mp4', 'audio/x-m4a'}, '.aac': {'audio/aac', 'audio/aac-adts'}}
        if ext not in allowed or upload.content_type not in allowed[ext]:
            raise BusinessError(415, 'UNSUPPORTED_AUDIO', 'Use WAV PCM16, M4A AAC or ADTS AAC')
        key = uuid.uuid4().hex + '.part'
        path = self.path(key)
        try:
            size, digest = 0, hashlib.sha256()
            with path.open('xb') as out:
                while block := upload.file.read(64 * 1024):
                    size += len(block)
                    if size > self.limit:
                        raise BusinessError(413, 'UPLOAD_TOO_LARGE', 'Audio exceeds the upload limit')
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            with path.open('rb') as f:
                head = f.read(16)
            signature = (ext == '.wav' and head[:4] == b'RIFF' and head[8:12] == b'WAVE') or (ext == '.m4a' and head[4:8] == b'ftyp') or (ext == '.aac' and len(head) >= 2 and head[0] == 255 and head[1] & 246 == 240)
            if not signature:
                raise BusinessError(415, 'INVALID_AUDIO', 'Audio signature is invalid')
            if ext == '.wav' and int.from_bytes(head[4:8], 'little') + 8 != size:
                raise BusinessError(415, 'INVALID_AUDIO', 'WAV length does not match its header')
            demuxer = {'.wav': 'wav', '.m4a': 'mov', '.aac': 'aac'}[ext]
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).with_name('audio_probe.py')), str(path), demuxer],
                                        capture_output=True, text=True, timeout=15,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if result.returncode:
                    raise ValueError()
                metadata = json.loads(result.stdout)
            except (ValueError, subprocess.TimeoutExpired):
                raise BusinessError(415, 'INVALID_AUDIO', 'Audio could not be decoded within supported limits')
            yield path, {'filename': filename, 'media_type': {'.wav': 'audio/wav', '.m4a': 'audio/mp4', '.aac': 'audio/aac'}[ext],
                         'size_bytes': size, 'sha256': digest.hexdigest(), **metadata}, ext
        finally:
            path.unlink(missing_ok=True)

    def publish(self, staged, ext):
        key = uuid.uuid4().hex + ext
        staged.replace(self.path(key))
        return key

    def require(self, key):
        path = self.path(key)
        if not path.is_file():
            raise BusinessError(503, 'FILE_UNAVAILABLE', 'Stored audio is unavailable', headers={'Retry-After': '5'})
        return path

    def delete(self, key):
        # Callers must supply a committed deletion queue record as ownership evidence.
        self.path(key).unlink(missing_ok=True)
