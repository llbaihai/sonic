"""SONIC MVP: register Demucs stems, edit tracks, and render a whole-song mix.

Zero-destructive editing: always start from source files. Final mix = original
song + sum(edited_stem - original_stem), not a sum of all separated stems.

CLI:
    python sonic_mvp.py register --id song001 --audio audio.flac --stems /path/to/6stems
    python sonic_mvp.py render --id song001 --edits edits.json
    python sonic_mvp.py separate --id new_song --audio song.flac --demucs-python /path/to/base/python
API:
    python -m uvicorn sonic_mvp:app --host 127.0.0.1 --port 8001
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pedalboard import Pedalboard, PitchShift
from pydantic import BaseModel, ConfigDict, Field

SONIC_ROOT = Path(os.getenv("SONIC_ROOT", "/srv/sonic"))
WORKSPACE = Path(os.getenv("SONIC_MVP_ROOT", str(SONIC_ROOT / "AudioBackend")))
PROJECTS_DIR = WORKSPACE / "mvp_projects"
EXPORTS_DIR = WORKSPACE / "mvp_exports"
STEMS_ROOT = SONIC_ROOT / "stems"
MAX_DURATION_SECONDS = 300
TRACK_NAMES = ("vocals", "drums", "bass", "piano", "guitar", "other")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
HEX_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

app = FastAPI(title="SONIC Multitrack MVP", version="0.3.0")
# Local-only development API. CORS is not authentication.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: Literal["vocals", "drums", "bass", "piano", "guitar", "other"]
    operation: Literal["gain", "mute", "duplicate", "move", "pitch"]
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    gain_db: float = Field(default=0.0, ge=-60, le=12, allow_inf_nan=False)
    semitones: float = Field(default=0.0, ge=-12, le=12, allow_inf_nan=False)


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[Edit] = Field(default_factory=list, max_length=64)


def project_file(project_id: str) -> Path:
    if not ID_PATTERN.fullmatch(project_id):
        raise ValueError("project_id must be 1-64 letters, digits, _ or -")
    return PROJECTS_DIR / f"{project_id}.json"


def audio_info(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")
    info = sf.info(str(path))
    if not (0 < info.duration <= MAX_DURATION_SECONDS):
        raise ValueError(f"Audio duration must be 0-{MAX_DURATION_SECONDS}s: {path}")
    if info.channels not in (1, 2):
        raise ValueError(f"Only mono/stereo audio supported: {path}")
    return {
        "duration": round(info.duration, 3),
        "sample_rate": info.samplerate,
        "channels": info.channels,
    }


def register_project(project_id: str, audio: Path, stems: Path) -> dict:
    """Trusted CLI registration: input paths are local server file paths."""
    target = project_file(project_id)
    if target.exists():
        raise FileExistsError(f"Project already exists: {project_id}")
    audio = audio.expanduser().resolve(strict=True)
    stems = stems.expanduser().resolve(strict=True)
    source_metadata = audio_info(audio)
    track_paths = {}
    track_metadata = {}
    for name in TRACK_NAMES:
        path = stems / f"{name}.wav"
        track_metadata[name] = audio_info(path)
        track_paths[name] = str(path)
    manifest = {
        "project_id": project_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "original": str(audio),
        "tracks": track_paths,
        "original_info": source_metadata,
        "track_info": track_metadata,
    }
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    # Exclusive create; never overwrite a project via accidental CLI reuse.
    with target.open("x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def load_project(project_id: str) -> dict:
    path = project_file(project_id)
    if not path.is_file():
        raise FileNotFoundError(f"Project not found: {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_audio(path: str | Path) -> tuple[np.ndarray, int]:
    path = Path(path)
    audio_info(path)
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if not np.all(np.isfinite(data)):
        raise ValueError(f"Invalid nonfinite samples in {path}")
    return data, sr


def resample_audio(samples: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    if from_sr == to_sr:
        return samples
    try:
        from scipy.signal import resample_poly
    except ImportError as exc:
        raise RuntimeError(
            "Original and stems have different sample rates; run: pip install scipy"
        ) from exc
    factor = math.gcd(from_sr, to_sr)
    return resample_poly(samples, to_sr // factor, from_sr // factor, axis=0).astype(np.float32)


def adapt_channels(samples: np.ndarray, channels: int) -> np.ndarray:
    if samples.shape[1] == channels:
        return samples
    if samples.shape[1] == 1 and channels == 2:
        return np.repeat(samples, 2, axis=1)
    if samples.shape[1] == 2 and channels == 1:
        return samples.mean(axis=1, keepdims=True)
    raise ValueError("Unsupported channel conversion")


def fit_length(samples: np.ndarray, frame_count: int) -> np.ndarray:
    if len(samples) < frame_count:
        return np.pad(samples, ((0, frame_count - len(samples)), (0, 0)))
    return samples[:frame_count]


def fade_envelope(n: int, sr: int) -> np.ndarray:
    """15-ms fade-in/out for local clip operations (avoids hard-edge clicks)."""
    env = np.ones((n, 1), dtype=np.float32)
    fade = min(int(round(0.015 * sr)), n // 2)
    if fade >= 2:
        env[:fade, 0] = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        env[-fade:, 0] = np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return env


def selection(edit: Edit, frames: int, sr: int) -> tuple[int, int]:
    if edit.operation in ("duplicate", "move", "pitch"):
        if edit.start is None or edit.end is None:
            raise ValueError(f"{edit.operation} requires start and end")
    a = 0 if edit.start is None else round(edit.start * sr)
    b = frames if edit.end is None else round(edit.end * sr)
    if not (0 <= a < b <= frames):
        raise ValueError("Invalid start/end: must select a nonempty interval within track")
    return a, b


def apply_one(audio: np.ndarray, sr: int, edit: Edit) -> np.ndarray:
    """Edit the CURRENT track, not the saved source stem; return an edited copy."""
    if len(audio) == 0:
        raise ValueError("Cannot edit an empty track")
    a, b = selection(edit, len(audio), sr)
    result = audio.copy()
    if edit.operation == "gain":
        amp = np.float32(10.0 ** (edit.gain_db / 20))
        if a == 0 and b == len(audio):
            result *= amp
        else:
            env = fade_envelope(b - a, sr)
            result[a:b] *= 1 + (amp - 1) * env
    elif edit.operation == "mute":
        if a == 0 and b == len(audio):
            result[:] = 0
        else:
            result[a:b] *= 1 - fade_envelope(b - a, sr)
    elif edit.operation in ("duplicate", "move"):
        if edit.target is None:
            raise ValueError("duplicate/move requires target (seconds)")
        t = round(edit.target * sr)
        n = b - a
        if t + n > MAX_DURATION_SECONDS * sr:
            raise ValueError("Destination exceeds maximum project duration")
        clip = audio[a:b].copy()
        env = fade_envelope(n, sr)
        if edit.operation == "move":
            result[a:b] *= 1 - env
        if t + n > len(result):
            result = fit_length(result, t + n)
        result[t:t+n] += clip * env  # OVERLAY, not ripple insertion.
    elif edit.operation == "pitch":
        n = b - a
        if n < int(round(0.2 * sr)):
            raise ValueError("Pitch selection must be at least 0.2 seconds")
        # SoundFile: [frames, channels]; Pedalboard: [channels, frames].
        processed = Pedalboard([PitchShift(semitones=edit.semitones)])(
            audio[a:b].T.copy(), sr
        ).T
        processed = fit_length(np.asarray(processed, dtype=np.float32), n)
        env = fade_envelope(n, sr)
        result[a:b] = audio[a:b] * (1 - env) + processed * env
    if not np.all(np.isfinite(result)):
        raise ValueError("Audio operation produced invalid numerical samples")
    return result


def render_project(project_id: str, request: RenderRequest) -> dict:
    manifest = load_project(project_id)
    mix, sr = read_audio(manifest["original"])
    channels = mix.shape[1]
    orig_frames = len(mix)
    # Group edits by track without reordering a track's own edit sequence.
    edits_by_track: dict[str, list[Edit]] = {}
    for edit in request.edits:
        edits_by_track.setdefault(edit.track, []).append(edit)

    resampled_tracks = []
    for name, edits in edits_by_track.items():
        stem, stem_sr = read_audio(manifest["tracks"][name])
        stem = resample_audio(stem, stem_sr, sr)
        stem = adapt_channels(stem, channels)
        baseline = fit_length(stem, orig_frames)
        current = baseline.copy()
        for edit in edits:
            current = apply_one(current, sr, edit)
        resampled_tracks.append((baseline, current))

    out_frames = max([orig_frames] + [len(edited) for _, edited in resampled_tracks])
    if out_frames > MAX_DURATION_SECONDS * sr:
        raise ValueError("Rendered song exceeds maximum project duration")
    master = fit_length(mix, out_frames).copy()
    # Essential: untouched instruments remain from the ORIGINAL full-quality mix.
    for baseline, edited in resampled_tracks:
        master += fit_length(edited, out_frames) - fit_length(baseline, out_frames)

    peak_before = float(np.max(np.abs(master))) if master.size else 0.0
    if not np.isfinite(peak_before):
        raise ValueError("Mix has invalid samples")
    normalized = bool(request.edits and peak_before > 0.999)
    master_gain = (0.999 / peak_before) if normalized else 1.0
    if normalized:
        master *= np.float32(master_gain)

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    file_id = uuid4().hex
    out_file = EXPORTS_DIR / f"{file_id}.wav"
    sf.write(str(out_file), master, sr, subtype="PCM_24")
    return {
        "status": "ok",
        "project_id": project_id,
        "file_id": file_id,
        "download_url": f"/exports/{file_id}",
        "sample_rate": sr,
        "channels": channels,
        "duration": round(len(master) / sr, 3),
        "operations_applied": len(request.edits),
        "peak_before_master_gain": round(peak_before, 6),
        "master_gain_db": round(20 * math.log10(master_gain), 4),
        "master_normalized_to_avoid_clipping": normalized,
        "absolute_output_path": str(out_file),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "SONIC Multitrack MVP", "version": "0.3.0"}


@app.get("/projects/{project_id}/tracks")
def get_tracks(project_id: str):
    try:
        manifest = load_project(project_id)
        return {
            "project_id": project_id,
            "original": manifest["original_info"],
            "tracks": manifest["track_info"],
        }
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/projects/{project_id}/audio/{track_id}")
def get_source_audio(project_id: str, track_id: str):
    try:
        manifest = load_project(project_id)
        if track_id == "original":
            path = Path(manifest["original"])
        elif track_id in TRACK_NAMES:
            path = Path(manifest["tracks"][track_id])
        else:
            raise ValueError("Unknown track")
        if not path.is_file():
            raise FileNotFoundError("Audio source not found")
        return FileResponse(str(path), media_type="audio/flac" if path.suffix.lower() == ".flac" else "audio/wav", filename=path.name, content_disposition_type="inline")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/projects/{project_id}/render")
def render_endpoint(project_id: str, request: RenderRequest):
    try:
        return render_project(project_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/exports/{file_id}")
def get_export(file_id: str):
    if not HEX_ID_PATTERN.fullmatch(file_id):
        raise HTTPException(404, "Export not found")
    path = EXPORTS_DIR / f"{file_id}.wav"
    if not path.is_file():
        raise HTTPException(404, "Export not found")
    return FileResponse(str(path), media_type="audio/wav", filename=f"sonic-{file_id[:8]}.wav", content_disposition_type="inline")


def demucs_separate(
    audio: Path, project_id: str, model: str, python_bin: str, device: str, root: Path
) -> Path:
    """Separate into a project-unique folder, even when inputs are all audio.flac."""
    if model != "htdemucs_6s":
        raise ValueError("This six-stem MVP requires --model htdemucs_6s")
    project_file(project_id)  # Validate the id before using it as a filename.
    audio = audio.resolve(strict=True)
    # A project-specific hardlink/copy prevents all user songs named 'audio.flac'
    # from overwriting each other's Demucs output directory.
    staging = WORKSPACE / "mvp_demucs_inputs"
    staging.mkdir(parents=True, exist_ok=True)
    unique_input = staging / f"{project_id}{audio.suffix.lower()}"
    if unique_input.exists():
        raise FileExistsError(f"Staged input exists: {unique_input}; choose another id")
    try:
        os.link(audio, unique_input)
    except OSError:
        shutil.copy2(audio, unique_input)
    cmd = [python_bin, "-m", "demucs", "-n", model, "-d", device,
           "-o", str(root), str(unique_input)]
    env = os.environ.copy()
    env.setdefault("HF_HOME", str(SONIC_ROOT / "demucs-cache"))
    print("Running Demucs:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=env)
    directory = root / model / project_id
    if not directory.is_dir():
        raise FileNotFoundError(f"Demucs output missing: {directory}")
    return directory


def main():
    parser = argparse.ArgumentParser(description="SONIC audio editor MVP")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register", help="Register previously separated audio")
    register.add_argument("--id", required=True)
    register.add_argument("--audio", type=Path, required=True)
    register.add_argument("--stems", type=Path, required=True)
    separate = sub.add_parser("separate", help="Run Demucs and register a new project")
    separate.add_argument("--id", required=True)
    separate.add_argument("--audio", type=Path, required=True)
    separate.add_argument("--demucs-python", default="/opt/conda/bin/python")
    separate.add_argument("--model", default="htdemucs_6s")
    separate.add_argument("--device", default="cuda")
    render = sub.add_parser("render", help="Render JSON edit instructions to WAV")
    render.add_argument("--id", required=True)
    render.add_argument("--edits", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "register":
        result = register_project(args.id, args.audio, args.stems)
        print(json.dumps({"status": "registered", "project_id": result["project_id"], "tracks": list(result["tracks"])}, ensure_ascii=False, indent=2))
    elif args.command == "separate":
        if project_file(args.id).exists():
            raise FileExistsError(f"Project already exists: {args.id}")
        stems = demucs_separate(args.audio, args.id, args.model, args.demucs_python, args.device, STEMS_ROOT)
        result = register_project(args.id, args.audio, stems)
        print(json.dumps({"status": "separated_and_registered", "project_id": result["project_id"], "stems": str(stems)}, ensure_ascii=False, indent=2))
    elif args.command == "render":
        request = RenderRequest.model_validate_json(args.edits.read_text(encoding="utf-8"))
        print(json.dumps(render_project(args.id, request), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
