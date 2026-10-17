"""MMA CPU rendering on the team's existing AutoDL connection."""
import hashlib
import json
from pathlib import Path
import shlex
import time
import uuid

import numpy as np
import soundfile as sf

from audio_engine import source_track
from mma_runtime import build_plan
from providers import connect_yue2, settings


def render_mma(samples, sample_rate, analysis, duration, directory, progress,
               bpm=96, groove='Ballad', chords=''):
    rate = 44100
    source, placements, length = source_track(samples, sample_rate, duration, rate)
    request = dict(notes=analysis['notes'], placements=[p/rate for p in placements],
        duration=length/rate, bpm=bpm, bpm_source='user', groove=groove, chords=chords)
    build_plan(request)
    directory = Path(directory)
    remote = '/srv/sonic/outputs/sonic-mvp2-mma-' + uuid.uuid4().hex
    script = Path(__file__).with_name('mma_runtime.py')
    config = settings()
    progress('rendering', 40, 'AutoDL 正在生成伴奏并合成旋律。')
    with connect_yue2(config) as client, client.open_sftp() as sftp:
        sftp.get_channel().settimeout(20)
        sftp.mkdir(remote)
        sftp.put(str(script), remote+'/mma_runtime.py')
        with sftp.file(remote+'/mma-request.json','wb') as stream:
            stream.write(json.dumps(request,allow_nan=False).encode())
        python = config.get('basic_pitch_python','/srv/sonic/BasicPitch/.conda/bin/python')
        command = ' '.join(shlex.quote(v) for v in ['timeout','180',python,'-B',remote+'/mma_runtime.py',remote])
        _,out,_ = client.exec_command(command,timeout=200)
        channel, log, started = out.channel, bytearray(), time.monotonic()
        while True:
            for ready,receive in ((channel.recv_ready,channel.recv),(channel.recv_stderr_ready,channel.recv_stderr)):
                if ready():
                    log.extend(receive(8192))
            if len(log)>262144:
                del log[:-131072]
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic()-started>195:
                raise TimeoutError('MMA 服务器任务超时，请检查服务器任务目录。')
            time.sleep(.1)
        (directory/'mma.log').write_bytes(log)
        if channel.recv_exit_status()!=0:
            raise RuntimeError('MMA 渲染未完成：'+log.decode(errors='replace')[-900:])
        progress('downloading',75,'正在取回旋律、伴奏与 MIDI。')
        for name in ('melody.wav','accompaniment.wav','melody.mid','accompaniment.mid','mma-plan.json','accompaniment.mma'):
            if sftp.stat(remote+'/'+name).st_size>40*1024*1024:
                raise ValueError('MMA 返回文件过大。')
            sftp.get(remote+'/'+name,str(directory/name))
    plan=json.loads((directory/'mma-plan.json').read_text(encoding='utf-8'))
    plan.update(remote_directory=remote,runtime_version=hashlib.sha256(script.read_bytes()).hexdigest()[:16])
    (directory/'mma-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    if source.ndim==1:
        source=np.repeat(source[:,None],2,axis=1)
    tracks={'source':source}
    for name in ('melody','accompaniment'):
        audio,sr=sf.read(directory/(name+'.wav'),dtype='float32',always_2d=True)
        if sr!=rate or audio.shape!=(length,2) or not np.isfinite(audio).all():
            raise ValueError('MMA 音轨长度或格式不符合工程。')
        tracks[name]=audio
    return tracks,rate,plan
