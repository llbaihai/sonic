"""Scan tracked files when Git is initialized; otherwise scan candidate files."""
from pathlib import Path
import ast
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'.local', '.runtime', '.venv', '__pycache__', '.git',
                   'node_modules', 'data', 'build', '.gradle', '.kotlin'}
FORBIDDEN_EXTENSIONS = {'.apk', '.aab', '.aar', '.pem', '.key', '.jks',
                        '.keystore', '.safetensors', '.ckpt', '.pt', '.pth', '.so'}
TEXT_EXTENSIONS = {'.md', '.py', '.java', '.kt', '.js', '.css', '.html', '.json',
                   '.xml', '.gradle', '.kts', '.ini', '.properties', '.txt', '.yml', '.yaml'}
PATTERNS = [
    re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----'),
    re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(r'\bAIza[A-Za-z0-9_-]{25,}'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{32,}'),
]


def candidate_files():
    if (ROOT / '.git').exists():
        result = subprocess.run(['git', '-c', 'safe.directory=' + str(ROOT),
                                 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
        return [ROOT / name.decode('utf-8') for name in result.stdout.split(b'\0') if name]
    return [path for path in ROOT.rglob('*') if path.is_file()
            and not any(part in FORBIDDEN_PARTS for part in path.relative_to(ROOT).parts)]


def main():
    errors = []
    files = candidate_files()
    if not files:
        raise SystemExit('No candidate files found.')
    for path in files:
        relative = path.relative_to(ROOT)
        if (set(relative.parts) & FORBIDDEN_PARTS or path.suffix in FORBIDDEN_EXTENSIONS
                or path.name in {'.env', '.private-connection.json', 'local.properties'}):
            errors.append(str(relative) + ': forbidden private/generated file')
            continue
        if path.stat().st_size > 50 * 1024 * 1024:
            errors.append(str(relative) + ': file exceeds 50 MiB')
        if path.suffix not in TEXT_EXTENSIONS and path.name not in {'.gitignore', '.gitattributes'}:
            continue
        text = path.read_text(encoding='utf-8-sig')
        if any(pattern.search(text) for pattern in PATTERNS):
            errors.append(str(relative) + ': credential pattern')
        if path.suffix == '.py':
            try:
                ast.parse(text, filename=str(relative))
            except SyntaxError:
                errors.append(str(relative) + ': Python syntax error')
        if path.suffix == '.json':
            try:
                json.loads(text)
            except ValueError:
                errors.append(str(relative) + ': JSON parse error')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'PASS: {len(files)} files checked; private paths, common credential patterns, size and syntax.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
