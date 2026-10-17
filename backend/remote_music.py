import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import time
import uuid

import pretty_midi

from audio_engine import encode_wav, notes_to_abc, validate_markers
from providers import connect_yue2, redact_model_text, settings


ROOT = Path(__file__).resolve().parent
REMOTE_ROOT = '/srv/sonic/outputs'
CACHE = '/srv/sonic/huggingface/hub'
MODEL = CACHE + '/models--m-a-p--YuE2-3B/snapshots/8f24312f187763b9854adeec87e437e2b03bbff1'
VAE = CACHE + '/models--m-a-p--YuE2-Vae/snapshots/152733a19ad43aa67e367f9b5503ef8075bb5126'
STAGE_MESSAGES = {'loading': 'YuE2 正在加载并校验模型，首次可能需要数分钟。',
    'planning': 'YuE2 正在创作乐谱。', 'preparing_score': '正在按官方流程转换并校验器乐声部。',
    'rendering': 'YuE2 正在生成真实音乐。', 'transcribing': 'BasicPitch 正在转录音符。'}


def _download(sftp, remote, local, maximum=1024 * 1024):
    if sftp.stat(remote).st_size > maximum:
        raise RuntimeError('远端产物超过允许大小。')
    sftp.get(remote, str(local))


def _run(mode, identity, directory, files, config, progress=None):
    if not re.fullmatch('[a-f0-9]{32}', identity):
        raise ValueError('远端任务编号无效。')
    project = config.get('yue2_project', '/srv/sonic/YuE')
    remote = REMOTE_ROOT + '/sonic-mvp-' + identity
    script = ROOT / 'remote_runtime.py'
    version = hashlib.sha256(script.read_bytes()).hexdigest()[:16]
    python = (config.get('basic_pitch_python', '/srv/sonic/BasicPitch/.conda/bin/python')
              if mode == 'transcribe' else project + '/.venv/bin/python')
    arguments = [python, '-u', remote + '/runtime-' + version + '.py', mode, '--directory', remote]
    if mode == 'generate':
        arguments.extend(['--project', project, '--model', config.get('yue2_model', MODEL),
                          '--vae', config.get('yue2_vae', VAE)])
    deadline = 180 if mode == 'transcribe' else 1800
    with connect_yue2(config) as client, client.open_sftp() as sftp:
        sftp.get_channel().settimeout(15)
        sftp.mkdir(remote)
        sftp.put(str(script), remote + '/runtime-' + version + '.py')
        for name, content in files.items():
            with sftp.file(remote + '/' + name, 'wb') as output:
                output.write(content)
        command = ('cd ' + shlex.quote(project) + ' && env HF_HUB_OFFLINE=1 '
            'HF_HOME=/srv/sonic/huggingface OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=8 '
            + 'timeout --signal=TERM --kill-after=15 ' + str(deadline) + ' '
            + ' '.join(shlex.quote(value) for value in arguments))
        _, stdout, _ = client.exec_command(command, timeout=deadline + 30)
        channel, log = stdout.channel, bytearray()
        started, next_status, previous = time.monotonic(), 0, None
        status = {}
        while True:
            for ready, receive in ((channel.recv_ready, channel.recv),
                                   (channel.recv_stderr_ready, channel.recv_stderr)):
                if ready():
                    log.extend(receive(8192))
            if len(log) > 512 * 1024:
                del log[:-256 * 1024]
            if time.monotonic() >= next_status:
                try:
                    with sftp.file(remote + '/status.json', 'rb') as stream:
                        status = json.loads(stream.read(8192))
                    if progress and status.get('stage') != previous:
                        previous = status.get('stage')
                        if previous in STAGE_MESSAGES:
                            progress(previous, None, STAGE_MESSAGES[previous])
                except FileNotFoundError:
                    pass
                next_status = time.monotonic() + 2
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() - started > deadline + 25:
                channel.close()
                raise RuntimeError('远端处理超过等待时限；请检查服务器任务目录。')
            time.sleep(0.15)
        (directory / (mode + '.log')).write_bytes(log)
        try:
            with sftp.file(remote + '/status.json', 'rb') as stream:
                status = json.loads(stream.read(8192))
        except FileNotFoundError:
            status = {}
        if channel.recv_exit_status() != 0 or status.get('stage') == 'failed':
            error = status.get('error') or log.decode(errors='replace')[-900:]
            raise RuntimeError('远端处理失败：' + redact_model_text(str(error), config)[:1000])
        if status.get('stage') != 'complete':
            raise RuntimeError('远端进程结束但没有有效完成记录。')
        if mode == 'transcribe':
            for name in ('notes.json', 'notes.csv', 'transcription.mid'):
                _download(sftp, remote + '/' + name, directory / name, 4 * 1024 * 1024)
            try:
                _download(sftp, remote + '/midi2abc.abc', directory / 'midi2abc.abc')
            except FileNotFoundError:
                pass
        else:
            if progress:
                progress('downloading', 78, '正在下载 YuE2 的真实生成结果。')
            _download(sftp, remote + '/generated/audio.flac', directory / 'generated.flac', 100 * 1024 * 1024)
            for remote_name, local_name in (('score.abc', 'yue2-score.abc'),
                    ('transfer.json', 'yue2-transfer.json'), ('effective-request.json', 'yue2-request.json'),
                    ('generated/result.json', 'yue2-result.json'), ('prompt.txt', 'yue2-prompt.txt')):
                _download(sftp, remote + '/' + remote_name, directory / local_name)
        metadata = {'directory': remote, 'runtime_version': version, **status}
        (directory / 'remote.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return metadata


def monophonic_notes(events, offset, duration):
    if not isinstance(events, list) or len(events) > 4000:
        raise ValueError('BasicPitch 音符事件数量无效。')
    valid = []
    for event in events:
        start, end = float(event['start']), float(event['end'])
        pitch, amplitude = event['midi'], float(event['amplitude'])
        if (type(pitch) is not int or not 0 <= pitch <= 127 or
                not all(math.isfinite(value) for value in (start, end, amplitude))):
            raise ValueError('BasicPitch 返回了无效音符。')
        start, end = max(0, start), min(duration, end)
        if end > start:
            valid.append((start, end, pitch, amplitude))
    edges = sorted({value for start, end, _, _ in valid for value in (start, end)})
    notes = []
    # Short recordings only: choose the strongest note on each overlapping interval.
    for left, right in zip(edges, edges[1:]):
        active = [event for event in valid if event[0] <= left and event[1] >= right]
        if not active:
            continue
        pitch = max(active, key=lambda event: event[3])[2]
        start, end = round(left + offset, 4), round(right + offset, 4)
        if notes and notes[-1]['midi'] == pitch and abs(notes[-1]['end'] - start) < 0.0002:
            notes[-1]['end'] = end
        else:
            notes.append({'midi': pitch, 'name': pretty_midi.note_number_to_name(pitch), 'start': start, 'end': end})
    return [note for note in notes if note['end'] - note['start'] >= 0.1]


def transcribe_basic_pitch(samples, sample_rate, markers, output_dir):
    markers = validate_markers(markers, len(samples) / sample_rate)
    start = markers[0] if markers else 0.0
    end = min(len(samples) / sample_rate, start + 12)
    crop = samples[round(start * sample_rate):round(end * sample_rate)]
    identity = uuid.uuid4().hex
    directory = Path(output_dir) / ('basic-pitch-' + identity)
    directory.mkdir()
    _run('transcribe', identity, directory, {'source.wav': encode_wav(crop, sample_rate)}, settings())
    events = json.loads((directory / 'notes.json').read_text(encoding='utf-8'))
    notes = monophonic_notes(events, start, end - start)
    warnings = ['速度 96 BPM 为编配默认值，未测得稳定节拍。',
                'BasicPitch 原始 MIDI 和 CSV 已保留；重叠音符按强度提取单旋律，ABC 经量化需回听核对。']
    if not notes:
        warnings.append('BasicPitch 未取得可靠音符；未替换为本地模型结果。')
    abc = notes_to_abc(notes, start)
    (directory / 'native.abc').write_text(abc, encoding='utf-8')
    return {'notes': notes, 'abc': abc, 'pitch_provider': 'basic_pitch',
        'pitch_artifact_id': directory.name, 'source_range': {'start': start, 'end': end},
        'voiced_ratio': round(min(1, sum(note['end'] - note['start'] for note in notes) / (end - start)), 3),
        'tempo': 96, 'tempo_source': 'default', 'description': 'BasicPitch 已完成音高转录。',
        'sound_role': 'melody' if notes else 'unknown', 'mood': '', 'gemini_used': False,
        'style_prompt': 'gentle instrumental music, warm piano, soft arrangement, no vocals', 'warnings': warnings}


def render_yue2(job_id, analysis, style, duration, keep_melody, output_dir, progress):
    request = {'id': 'sonic-' + job_id, 'style': style[:1500] +
        f', instrumental, no vocals, no singing, no choir, approximately {duration} seconds',
        'seed': analysis.get('generation_seed',831002), 'score_origin': analysis.get('pitch_provider', 'pyin'),
        'melody_only': bool(keep_melody), 'requested_duration': duration}
    if keep_melody:
        if not analysis.get('notes') or not analysis.get('abc', '').strip():
            raise ValueError('保留旋律需要可靠音符和乐谱，请先分析哼唱或关闭保留旋律。')
        request['abc'] = analysis['abc']
    _run('generate', job_id, Path(output_dir),
         {'request.json': json.dumps(request, ensure_ascii=False).encode()}, settings(), progress)
    return Path(output_dir) / 'generated.flac'
