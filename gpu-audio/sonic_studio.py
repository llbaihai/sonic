"""SONIC Studio minimal web UI adapter for the existing sonic_mvp.py (V0.3).

Run: python -m uvicorn sonic_studio:app --host 127.0.0.1 --port 8001
Open forwarded: http://localhost:8001/studio/

This imports the V0.3 app; all existing /projects/.../render routes are retained.
Development-only; do not expose directly to the public Internet.
"""
from functools import lru_cache
from math import ceil
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from sonic_mvp import (app, PROJECTS_DIR, TRACK_NAMES, load_project, ID_PATTERN,
                       audio_info)

STUDIO_DIR = Path(__file__).resolve().parent / 'studio'
if not (STUDIO_DIR / 'index.html').is_file():
    raise RuntimeError(f'Missing {STUDIO_DIR / "index.html"}; unzip the studio folder beside sonic_studio.py')

@app.get('/studio-api/projects')
def studio_projects():
    """List registered projects without revealing server-side file paths."""
    projects = []
    if not PROJECTS_DIR.exists():
        return {'projects': projects}
    for p in sorted(PROJECTS_DIR.glob('*.json')):
        if not ID_PATTERN.fullmatch(p.stem):
            continue
        try:
            manifest = load_project(p.stem)
            projects.append({'id': p.stem, 'duration': manifest['original_info']['duration']})
        except (OSError, KeyError, ValueError):
            continue
    return {'projects': projects}


@lru_cache(maxsize=64)
def waveform_peaks(path: str, bins: int):
    """Stream samples to extract max absolute level per bucket, avoiding WAV transfers."""
    file_path = Path(path)
    audio_info(file_path)
    with sf.SoundFile(str(file_path)) as source:
        frame_count = len(source)
        sample_rate = source.samplerate
        step = max(1, ceil(frame_count / bins))
        peaks = []
        while True:
            block = source.read(frames=step, dtype='float32', always_2d=True)
            if len(block) == 0:
                break
            peaks.append(round(float(np.max(np.abs(block))), 5))
    return {'peaks': peaks, 'duration': round(frame_count / sample_rate, 3),
            'max_peak': max(peaks, default=0.0), 'sample_rate': sample_rate}


@app.get('/studio-api/projects/{project_id}/waveforms/{track_id}')
def studio_waveform(project_id: str, track_id: str):
    if not ID_PATTERN.fullmatch(project_id):
        raise HTTPException(404, 'Unknown project')
    try:
        manifest = load_project(project_id)
        if track_id == 'original':
            path = manifest['original']
        elif track_id in TRACK_NAMES:
            path = manifest['tracks'][track_id]
        else:
            raise HTTPException(404, 'Unknown track')
        return waveform_peaks(path, 1500)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(404, f'Waveform unavailable: {exc}') from exc

app.mount('/studio', StaticFiles(directory=str(STUDIO_DIR), html=True), name='studio')
