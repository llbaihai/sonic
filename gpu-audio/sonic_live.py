"""SONIC V0.5: browser live editing, backed by the unchanged V0.3 renderer.

Run: python -m uvicorn sonic_live:app --host 127.0.0.1 --port 8001
Open: http://localhost:8001/live/

Requires sonic_studio.py and sonic_mvp.py in the same directory.
Development-only. Do not expose to the Internet without authentication.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from sonic_studio import app  # retains /studio, waveforms, all V0.3 APIs
from sonic_mvp import (
    PROJECTS_DIR, WORKSPACE, TRACK_NAMES, ID_PATTERN, Edit,
    load_project, audio_info, read_audio, resample_audio,
    adapt_channels, fit_length, apply_one,
)

HERE = Path(__file__).resolve().parent
LIVE_DIR = HERE / 'live'
CACHE_DIR = WORKSPACE / 'live_wav_cache'
if not (LIVE_DIR / 'index.html').is_file():
    raise RuntimeError('Missing live/index.html. Unzip the complete V0.5 package.')


class TrackDeltaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    edits: list[Edit] = Field(default_factory=list, max_length=64)


def _manifest_and_path(project_id: str, track_id: str):
    if not ID_PATTERN.fullmatch(project_id):
        raise HTTPException(404, 'Unknown project')
    try:
        manifest = load_project(project_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    if track_id == 'original':
        path = Path(manifest['original'])
    elif track_id in TRACK_NAMES:
        path = Path(manifest['tracks'][track_id])
    else:
        raise HTTPException(404, 'Unknown track')
    if not path.is_file():
        raise HTTPException(404, 'Audio file not found')
    return manifest, path


@app.get('/live-api/projects/{project_id}/audio/{track_id}')
def live_source_wav(project_id: str, track_id: str):
    """Browser-compatible WAV, cached on disk if source was FLAC.

    Waveform UI comes from /studio-api/...; only audio needed for playback is
    downloaded. The original song is loaded first; stems are lazy-loaded.
    """
    _manifest, source = _manifest_and_path(project_id, track_id)
    if source.suffix.lower() == '.wav':
        return FileResponse(str(source), media_type='audio/wav', content_disposition_type='inline')
    try:
        audio_info(source)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        stat = source.stat()
        # Truncate to server-managed identifiers, not arbitrary user-provided paths.
        target = CACHE_DIR / f'{project_id}-{track_id}-{stat.st_size}-{stat.st_mtime_ns}.wav'
        if not target.is_file():
            tmp = CACHE_DIR / f'{target.stem}-{os.getpid()}.tmp'
            try:
                with sf.SoundFile(str(source)) as src, sf.SoundFile(
                    str(tmp), 'w', samplerate=src.samplerate,
                    channels=src.channels, format='WAV', subtype='PCM_24'
                ) as dst:
                    for block in src.blocks(blocksize=65536, dtype='float32', always_2d=True):
                        dst.write(block)
                # Atomic rename avoids serving an incomplete file.
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        return FileResponse(str(target), media_type='audio/wav', content_disposition_type='inline')
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, f'WAV conversion failed: {exc}') from exc


@app.post('/live-api/projects/{project_id}/delta/{track_id}')
def live_pitch_delta(project_id: str, track_id: str, request: TrackDeltaRequest):
    """Automatically return a stem's (edited - original) aligned float WAV.

    The browser can overlay this delta on the original song without re-rendering
    the entire master. Used for DSP effects not implemented in browser (pitch).
    """
    if track_id not in TRACK_NAMES:
        raise HTTPException(404, 'Unknown track')
    manifest, _ = _manifest_and_path(project_id, track_id)
    if any(e.track != track_id for e in request.edits):
        raise HTTPException(422, 'Each delta request must edit only its specified track')
    if not request.edits:
        raise HTTPException(422, 'Delta request requires at least one edit')
    try:
        original_info = audio_info(Path(manifest['original']))
        sr = original_info['sample_rate']
        channels = original_info['channels']
        n = sf.info(str(manifest['original'])).frames  # exact frame count (avoid rounded-duration drift)
        stem, stem_sr = read_audio(manifest['tracks'][track_id])
        base = fit_length(adapt_channels(resample_audio(stem, stem_sr, sr), channels), n)
        edited = base.copy()
        for edit in request.edits:
            edited = apply_one(edited, sr, edit)
        if len(edited) > n:
            raise ValueError('Live preview cannot extend beyond the original song duration')
        delta = (fit_length(edited, n) - base).astype(np.float32)
        if not np.isfinite(delta).all():
            raise ValueError('Nonfinite result from audio DSP')
        out = io.BytesIO()
        sf.write(out, delta, sr, format='WAV', subtype='FLOAT')
        return Response(content=out.getvalue(), media_type='audio/wav', headers={
            'Cache-Control': 'no-store',
            'X-SONIC-Track': track_id,
        })
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


app.mount('/live', StaticFiles(directory=str(LIVE_DIR), html=True), name='live')
