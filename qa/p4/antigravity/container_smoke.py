"""Isolated Docker API/worker smoke test. Never prints the ephemeral test key."""
from __future__ import annotations

import io
import json
import math
import os
import re
import secrets
import struct
import subprocess
import time
import uuid
import wave

import httpx

IMAGE = 'aurora-binaural:codex-p4'
suffix = uuid.uuid4().hex[:10]
volume = f'aurora-p4-qa-{suffix}'
api_name = f'{volume}-api'
worker_name = f'{volume}-worker'
api_key = secrets.token_urlsafe(24)
env = os.environ | {'AURORA_API_KEY': api_key}


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(['docker', *args], env=env, capture_output=True, text=True, check=check)
    assert api_key not in result.stdout + result.stderr
    return result.stdout.strip()


def wait_job(client: httpx.Client, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f'/v1/jobs/{job_id}')
        response.raise_for_status()
        job: dict[str, object] = response.json()
        if job['status'] in ('succeeded', 'failed', 'canceled'):
            assert job['status'] == 'succeeded', str(job)
            return job
        time.sleep(0.05)
    raise AssertionError('Container job timeout')


try:
    docker('volume', 'create', volume)
    docker('run', '-d', '--name', api_name, '-p', '127.0.0.1::8000', '-v', f'{volume}:/data/runtime',
           '-e', 'AURORA_API_KEY', IMAGE, 'api', '--host', '0.0.0.0', '--port', '8000')
    docker('run', '-d', '--name', worker_name, '-v', f'{volume}:/data/runtime', IMAGE, 'worker')
    port = docker('port', api_name, '8000').split(':')[-1]
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'Authorization': f'Bearer {api_key}'}, timeout=10) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                ready = client.get('/ready')
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError('Container readiness timeout')
        html = client.get('/')
        assert html.status_code == 200 and 'Aurora Binaural' in html.text
        script = re.search(r'src="(/assets/[^\"]+\.js)"', html.text)
        assert script is not None
        assert client.get(script.group(1)).status_code == 200
        assert 'Permission is hereby granted' in client.get('/licenses/Preact-MIT.txt').text
        assert client.get('/gui/session', headers={'X-Aurora-GUI': '1'}).status_code == 404
        assert httpx.get(f'http://127.0.0.1:{port}/v1/presets').status_code == 401
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(48000)
            out.writeframes(b''.join(struct.pack('<hh', value, -value)
                                    for i in range(48000)
                                    for value in [round(7000 * math.sin(2 * math.pi * 440 * i / 48000))]))
        response = client.post('/v1/assets', content=buffer.getvalue(), headers={'X-Asset-Filename': 'container.wav'})
        assert response.status_code == 201
        asset = response.json()['asset_id']
        response = client.post('/v1/previews', json={'asset_id': asset, 'start_seconds': 0.25, 'duration_seconds': 0.5})
        assert response.status_code == 202
        preview_id = response.json()['job_id']
        wait_job(client, preview_id)
        report = client.get(f'/v1/jobs/{preview_id}/report').json()
        assert report['preview']['samples'] == 24000
        assert report['preview']['loudness_diff_lu'] <= 0.2
        assert client.get(f'/v1/jobs/{preview_id}/original').content[:4] == b'RIFF'
        assert client.get(f'/v1/jobs/{preview_id}/result').content[:4] == b'RIFF'
        response = client.post('/v1/jobs', json={'input': {'asset_id': asset}})
        assert response.status_code == 202
        job_id = response.json()['job_id']
        job = wait_job(client, job_id)
        assert isinstance(job['result_metadata'], dict)
        assert job['result_metadata']['samples'] == 48000
        assert client.get(f'/v1/jobs/{job_id}/result').content[:4] == b'RIFF'
        for name in (api_name, worker_name):
            docker('logs', name)
        files = docker('exec', api_name, 'python', '-c',
                       "from pathlib import Path; import hashlib,json; p=Path('/app/data/hrtf'); "
                       "f=list(p.rglob('*.sofa')); assert len(f)==1 and 'sadie' in str(f[0]); "
                       "assert hashlib.sha256(f[0].read_bytes()).hexdigest()=='e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31'; "
                       "assert Path('/app/licenses/SADIE-II-Apache-2.0.txt').is_file(); "
                       "assert Path('/app/licenses/Preact-MIT.txt').is_file(); "
                       "print(json.dumps({'sofa_files':[str(x.relative_to(p)) for x in f]}))")
        print(json.dumps({'ok': True, 'image': IMAGE, 'checks': ['static-js', 'ready', 'auth', 'preview-pair',
                          'matched-loudness', 'full-export', 'key-not-in-logs', 'sadie-only-and-licenses'],
                          'hrtf': json.loads(files)}, indent=2))
finally:
    docker('rm', '-f', api_name, worker_name, check=False)
    docker('volume', 'rm', volume, check=False)
