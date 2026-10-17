"""Register a new six-stem project using the teammate's existing CLI."""
import fcntl
from pathlib import Path
import subprocess
import sys

directory=Path(sys.argv[1]).resolve(strict=True)
identity=sys.argv[2]
if not identity.startswith('mvp2_') or len(identity)!=37 or any(c not in '0123456789abcdef' for c in identity[5:]):
    raise ValueError('Invalid editor project id')
lock=Path('/srv/sonic/outputs/.sonic-mvp-gpu.lock').open('a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
gpu=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True,timeout=10)
if int(gpu.splitlines()[0])>12000:
    raise RuntimeError('GPU occupied; retry after other tasks complete')
subprocess.run(['/srv/sonic/AudioBackend/.venv/bin/python',
    '/srv/sonic/AudioBackend/sonic_mvp.py','separate','--id',identity,
    '--audio',str(directory/'input.flac'),'--demucs-python',
    '/opt/conda/bin/python','--model','htdemucs_6s','--device','cuda'],
    check=True,timeout=840)
