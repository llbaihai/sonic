"""Run inside the existing server environments; never installs dependencies."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def transcribe(directory):
    from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
    from basic_pitch.inference import predict
    write_json(directory / 'status.json', {'stage': 'transcribing'})
    _, midi, events = predict(directory / 'source.wav',
        model_or_model_path=build_icassp_2022_model_path(FilenameSuffix.tflite),
        minimum_frequency=70, maximum_frequency=1200)
    midi.write(str(directory / 'transcription.mid'))
    notes = [{'start': float(event[0]), 'end': float(event[1]),
              'midi': int(event[2]), 'amplitude': float(event[3])} for event in events]
    write_json(directory / 'notes.json', notes)
    with (directory / 'notes.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['start', 'end', 'midi', 'amplitude'])
        writer.writeheader()
        writer.writerows(notes)
    tool = Path('/usr/bin/midi2abc')
    if tool.is_file():
        conversion = subprocess.run([str(tool), '-f', str(directory / 'transcription.mid'),
            '-o', str(directory / 'midi2abc.abc')], capture_output=True, text=True, timeout=30)
        (directory / 'midi2abc.log').write_text(conversion.stdout + conversion.stderr, encoding='utf-8')
    write_json(directory / 'status.json', {'stage': 'complete', 'notes': len(notes)})


def prepare_instrumental(directory, draft, original, origin):
    from instrumental import lyric_tags, validate_request
    from instrumentalize import convert_score
    from yue2.protocol import SongRequest
    (directory / 'source.abc').write_text(original, encoding='utf-8')
    write_json(directory / 'status.json', {'stage': 'preparing_score'})
    score, transfer = convert_score(original, keep_chords=not draft.get('melody_only', False))
    transfer['score_origin'] = origin
    write_json(directory / 'transfer.json', transfer)
    (directory / 'score.abc').write_text(score, encoding='utf-8')
    request = {'id': draft['id'], 'style': draft['style'], 'lyrics': lyric_tags(score),
        'abc': score, 'cot': 'full' if validate_score_has_chords(score) else 'melody', 'seed': draft['seed']}
    validate_request(request)
    write_json(directory / 'effective-request.json', request)
    (directory / 'prompt.txt').write_text(SongRequest(**request).text(), encoding='utf-8')
    return request


def generate(directory, project, model, vae):
    import fcntl
    lock = (directory.parent / '.sonic-mvp-gpu.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError('Another SONIC GPU task is running; retry after it completes.') from error
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.used',
        '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
    if int(gpu.stdout.splitlines()[0]) > 12000:
        raise RuntimeError('GPU is occupied by another process; no existing process was stopped.')
    write_json(directory / 'status.json', {'stage': 'loading'})
    import numpy as np
    import torch
    from yue2 import YuE2Pipeline
    from yue2.protocol import SongRequest, token_prefixes
    sys.path.insert(0, str(project / 'skills/yue2-music/instrumental/scripts'))
    torch.set_num_threads(8)
    draft = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
    origin = draft.get('score_origin', 'user_score')
    request = prepare_instrumental(directory, draft, draft['abc'], origin) if draft.get('abc') else None
    write_json(directory / 'status.json', {'stage': 'loading'})
    started = time.monotonic()
    with YuE2Pipeline.from_pretrained(str(model), vae=str(vae), device='cuda',
            memory_budget_gib=24, local_files_only=True) as pipe:
        last_update = 0.0

        def on_token(phase, token):
            nonlocal last_update
            if time.monotonic() - last_update > 2:
                write_json(directory / 'status.json',
                    {'stage': 'planning' if phase == 'abc' else 'rendering'})
                last_update = time.monotonic()

        if request is None:
            write_json(directory / 'status.json', {'stage': 'planning'})
            plan = pipe.plan(style=draft['style'], lyrics='[Verse]\n', cot='full',
                             seed=draft['seed'], on_token=on_token)
            plan.save(directory / 'planning')
            if plan.truncated:
                raise RuntimeError('YuE2 score planning was truncated; original plan retained.')
            origin = 'yue2_planner'
            request = prepare_instrumental(directory, draft, plan.abc, origin)
        score = request['abc']
        write_json(directory / 'status.json', {'stage': 'rendering'})
        song = pipe(**request, on_token=on_token)
        song.save_artifacts(directory / 'generated')
        if song.semantic.plan.prefix != token_prefixes(SongRequest(**request), pipe.tokenizer):
            raise RuntimeError('Generated token prefix differs from the requested score.')
        if song.abc != score or not np.isfinite(song.audio).all() or not np.any(song.audio):
            raise RuntimeError('Generated audio or score failed validation.')
        if any(song.truncated.values()):
            raise RuntimeError('Generated output was truncated; artifacts retained for review.')
        write_json(directory / 'status.json', {'stage': 'complete',
            'duration': len(song.audio) / song.sample_rate, 'elapsed': time.monotonic() - started,
            'score_origin': origin, 'absence_of_vocals_checked': False,
            'audio_melody_fidelity_checked': False})
    lock.close()


def validate_score_has_chords(score):
    from abc_tools import parse_abc
    return bool(parse_abc(score).voices['Vocal'].chords)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['transcribe', 'generate'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--vae', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'transcribe':
            transcribe(args.directory)
        else:
            generate(args.directory, args.project, args.model, args.vae)
    except Exception as error:
        write_json(args.directory / 'status.json', {'stage': 'failed', 'error': str(error)[:1200]})
        raise


if __name__ == '__main__':
    main()
