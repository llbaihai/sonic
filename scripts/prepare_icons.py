"""Fetch the pinned public icon dependency for local development."""
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://unpkg.com/lucide@0.468.0/dist/umd/lucide.js'


def main():
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if not data or len(data) > 2 * 1024 * 1024 or b'lucide' not in data.lower():
        raise SystemExit('Unexpected icon library response.')
    for directory in ['backend/web/vendor', 'gpu-audio/vendor']:
        path = ROOT / directory / 'lucide.js'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print('Lucide 0.468.0 ready; SHA256:', hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
