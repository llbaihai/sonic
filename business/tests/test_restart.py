"""Real TCP requests and termination/restart of a separate Uvicorn process."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from test_api import PREFIX, account, draft, project, upload, wav_bytes

ROOT = Path(__file__).resolve().parents[1]


def start_server(port, log):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'sonic.main:create_app', '--factory',
        '--host', '127.0.0.1', '--port', str(port), '--no-access-log'], cwd=ROOT, env=env,
        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=30, trust_env=False)
    for _ in range(100):
        if process.poll() is not None:
            raise AssertionError('API process exited; inspect .runtime/restart-test.log')
        try:
            if client.get('/openapi.json').status_code == 200:
                return process, client
        except httpx.ConnectError:
            pass
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    client.close()
    raise AssertionError('API did not become ready')


def stop_server(process, client):
    client.close()
    process.terminate()
    process.wait(timeout=10)


def test_data_and_token_survive_actual_api_process_restart():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (ROOT / '.runtime/restart-test.log').open('w') as log:
        process, client = start_server(port, log)
        try:
            auth, _ = account(client)
            p = project(client, auth)
            r = upload(client, auth, p)
            assert r.status_code == 201
            asset = r.json()['data']['id']
            assert client.put(f'{PREFIX}/projects/{p}/draft', headers=auth, json=draft(asset)).status_code == 200
            r = client.post(f'{PREFIX}/projects/{p}/works', headers=auth, json={'title': 'persistent music', 'asset_id': asset})
            assert r.status_code == 201
            work = r.json()['data']['id']
        finally:
            stop_server(process, client)
        if os.environ.get('SONIC_TEST_RESTART_DB') == '1':
            # Only opt into restarting this project's known local development cluster.
            subprocess.run([sys.executable, 'scripts/local_db.py', 'stop'], cwd=ROOT, check=True)
            subprocess.run([sys.executable, 'scripts/local_db.py', 'start'], cwd=ROOT, check=True)
        process, client = start_server(port, log)
        try:
            assert client.get(f'{PREFIX}/projects/{p}', headers=auth).status_code == 200
            assert client.get(f'{PREFIX}/projects/{p}/draft', headers=auth).json()['data']['version'] == 1
            assert client.get(f'{PREFIX}/projects/{p}/assets', headers=auth).json()['data'][0]['id'] == asset
            assert client.get(f'{PREFIX}/projects/{p}/works', headers=auth).json()['data'][0]['id'] == work
            assert client.get(f'{PREFIX}/works/{work}/download', headers=auth).content == wav_bytes()
            other, _ = account(client)
            assert client.get(f'{PREFIX}/works/{work}/download', headers=other).status_code == 404
        finally:
            stop_server(process, client)
