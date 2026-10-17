import io
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

import librosa
import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from audio_engine import (AUDIO_FORMATS, MAX_BYTES, analyze_audio, encode_wav, mix_tracks, read_audio,
                          render_local, source_track, test_sample, validate_markers)
from providers import describe_audio, gemini_call_status, gemini_key, probe_yue2, render_yue2, settings, official_audio_settings
from model_routes import router as model_router
from remote_music import transcribe_basic_pitch
from mma_provider import render_mma
from live_bridge import router as live_router, separate_yue_file
from music_styles import Genre, STYLES, with_style
from prompt_translation import translate_prompt
from public_access import install as install_access,enabled as public_mode
from hardware_integration import router as hardware_router
from account_integration import router as account_router
from user_access import install as install_users, visible, stamp


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SONIC_DATA_DIR', str(ROOT / 'data')))
SESSIONS_DIR = DATA / 'sessions'
JOBS_DIR = DATA / 'jobs'
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='SONIC Technical MVP')
app.include_router(model_router)
app.include_router(live_router)
app.include_router(hardware_router)
app.include_router(account_router)
app.mount('/live', StaticFiles(directory=str(ROOT / 'live'), html=True), name='live-editor')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '10.0.2.2', 'testserver'])
install_access(app)
install_users(app)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sonic-render')
remote_state = {'available': False, 'gpu': '', 'message': '尚未验证服务器连接。'}
json_io_lock = threading.RLock()
separation_lock = threading.Lock()


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    if request.url.path.startswith('/api/model-profiles'):
        return JSONResponse({'detail': '配置字段格式或长度不正确，请检查后重试。'}, status_code=422)
    return await request_validation_exception_handler(request, error)


def save_json(path, value):
    # Windows cannot replace a file while another request holds a read handle.
    with json_io_lock:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)


def read_json(path):
    with json_io_lock:
        return json.loads(path.read_text(encoding='utf-8'))


def folder(root, identity):
    if not re.fullmatch('[a-f0-9]{32}', identity):
        raise HTTPException(404, '没有找到对应记录。')
    path = root / identity
    if not path.is_dir():
        raise HTTPException(404, '没有找到对应记录。')
    return path


def load_session(identity):
    return read_json(folder(SESSIONS_DIR, identity) / 'session.json')


@app.middleware('http')
async def local_requests(request: Request, call_next):
    if request.method in {'POST', 'PATCH', 'PUT', 'DELETE'}:
        content_length = request.headers.get('content-length', '')
        if content_length and (not content_length.isdigit() or int(content_length) > MAX_BYTES + 65536):
            return JSONResponse({'detail': '请求超过 12 MB，请选择较短的音频。'}, status_code=413)
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            valid_origin=(origin.rstrip('/')==os.environ.get('SONIC_PUBLIC_ORIGIN','').rstrip('/')
                          if public_mode() else parsed.hostname in {'localhost','127.0.0.1','testserver'}
                          and parsed.netloc==request.headers.get('host'))
            if not valid_origin:
                return JSONResponse({'detail': '请从 SONIC App 发起请求。'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    if request.url.path.startswith('/api/model-profiles') or request.url.path == '/api/status':
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/status')
def status(probe: bool = False):
    global remote_state
    config = official_audio_settings()
    if probe:
        remote_state = probe_yue2()
    return {'hardware': 'not_connected',
            'gemini': {'configured': bool(gemini_key(config)), 'model': config['gemini_model'],
                       'name': config.get('model_label', '原有 Gemini 配置'),
                       'profile_id': config.get('model_profile_id', 'legacy'),
                       'mode': config.get('gemini_mode', 'official'),
                       'connection': gemini_call_status(config),
                       'endpoint_host': (urlsplit(config.get('gemini_native_base_url', '')).hostname
                                         if config.get('gemini_mode') == 'google_compatible' else
                                         urlsplit(config.get('gemini_base_url', '')).hostname
                                         if config.get('gemini_mode') == 'openai_compatible' else
                                         'generativelanguage.googleapis.com')},
            'yue2': {'configured': bool(config.get('ssh_host') and config.get('ssh_password')),
                     'host': config.get('ssh_host', ''), **remote_state},
            'capabilities': {'local_render': True,'user_accounts':__import__('user_access').enabled(),'public_app':public_mode()}, 'version': '0.1.0'}


@app.get('/api/sample')
def sample():
    return Response(test_sample(), media_type='audio/wav',
                    headers={'Content-Disposition': 'attachment; filename="sonic-test-melody.wav"'})


@app.post('/api/remote/probe')
def remote_probe():
    global remote_state
    remote_state = probe_yue2()
    return remote_state


@app.get('/api/sessions')
def sessions():
    records = []
    for path in SESSIONS_DIR.glob('*/session.json'):
        try:
            record=read_json(path)
            if visible(record):records.append(record)
        except (OSError, json.JSONDecodeError):
            continue
    return {'sessions': sorted(records, key=lambda item: item['created_at'], reverse=True)}


@app.post('/api/sessions')
async def upload(file: UploadFile = File(...), source: str = Form('upload'), name: str = Form(''),
                 markers: str = Form('[]')):
    if source not in {'upload', 'microphone', 'sample'}:
        raise HTTPException(400, '当前只接受上传、电脑录音或测试样例来源。')
    blob = await file.read(MAX_BYTES + 1)
    try:
        samples, sample_rate = read_audio(blob)
        duration = len(samples) / sample_rate
        marker_times = validate_markers(json.loads(markers), duration)
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(400, str(error)) from error
    identity = uuid.uuid4().hex
    path = SESSIONS_DIR / identity
    path.mkdir()
    extension = AUDIO_FORMATS[sf.info(io.BytesIO(blob)).format][0]
    (path / ('original.' + extension)).write_bytes(blob)
    (path / 'audio.wav').write_bytes(encode_wav(samples, sample_rate))
    record = {'id': identity, 'name': (name or file.filename or '新的声音')[:100],
              'source': source, 'duration': round(duration, 4), 'sample_rate': sample_rate,
              'created_at': datetime.now(timezone.utc).isoformat(),
              'audio_url': f'/media/{identity}/audio.wav',
              'original_url': f'/media/{identity}/original.{extension}',
              'markers': marker_times, 'markers_origin': 'manual', 'analysis': None}
    save_json(path / 'session.json', stamp(record))
    return record


@app.get('/api/sessions/{identity}')
def session(identity: str):
    return load_session(identity)

class NameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

@app.patch('/api/sessions/{identity}/name')
def rename_session(identity: str, request: NameRequest):
    record=load_session(identity);record['name']=request.name.strip();save_json(folder(SESSIONS_DIR,identity)/'session.json',record);return record

@app.delete('/api/sessions/{identity}')
def delete_session(identity: str):
    import shutil
    shutil.rmtree(folder(SESSIONS_DIR,identity));return {'deleted':identity}

class BulkRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)

@app.post('/api/sessions/bulk-delete')
def delete_sessions_bulk(request: BulkRequest):
    import shutil
    for identity in request.ids: shutil.rmtree(folder(SESSIONS_DIR,identity))
    return {'deleted':request.ids}


class MarkerRequest(BaseModel):
    markers: list[float]


@app.patch('/api/sessions/{identity}')
def update_markers(identity: str, request: MarkerRequest):
    record = load_session(identity)
    try:
        marker_times = validate_markers(request.markers, record['duration'])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if marker_times != record['markers']:
        record.update(markers=marker_times, analysis=None)
        save_json(folder(SESSIONS_DIR, identity) / 'session.json', record)
    return record


class AnalyzeRequest(BaseModel):
    use_gemini: bool = False
    style: str = Field(default='', max_length=1500)
    pitch_provider: Literal['pyin', 'basic_pitch', 'none'] = 'pyin'
    genre: Genre | None = None


@app.post('/api/sessions/{identity}/analyze')
def analyze(identity: str, request: AnalyzeRequest):
    record = load_session(identity)
    path = folder(SESSIONS_DIR, identity)
    samples, sample_rate = sf.read(path / 'audio.wav', dtype='float32')
    if request.pitch_provider == 'none':
        result = {'notes': [], 'abc': '', 'pitch_provider': 'none', 'voiced_ratio': 0,
            'tempo': 96, 'tempo_source': 'default', 'description': '生活声音素材',
            'sound_role': 'unknown', 'mood': '', 'gemini_used': False,
            'style_prompt': request.style.strip(), 'warnings': ['未提取旋律；YuE2 将按创作提示词生成。']}
    elif request.pitch_provider == 'basic_pitch':
        try:
            result = transcribe_basic_pitch(samples, sample_rate, record['markers'], path)
        except Exception as error:
            raise HTTPException(502, 'BasicPitch 转录未完成：' + str(error)[:700]) from error
    else:
        result = analyze_audio(samples, sample_rate, record['markers'])
    if result.get('pitch_artifact_id'):
        result['transcription_urls'] = {name: f'/api/sessions/{identity}/transcription/{filename}'
            for name, filename in (('midi', 'transcription.mid'), ('csv', 'notes.csv'), ('abc', 'native.abc'))}
    if request.style.strip():
        result['style_prompt'] = request.style.strip()
    result['gemini_attempted'] = request.use_gemini
    if request.use_gemini:
        config = official_audio_settings()
        result.update(gemini_mode=config.get('gemini_mode', 'official'), gemini_model=config['gemini_model'],
                      ai_name=config.get('model_label', config['gemini_model']))
        try:
            result.update(describe_audio(samples, sample_rate, with_style(request.style,request.genre), config=config))
        except Exception as error:
            result['gemini_error'] = str(error)
            result['warnings'].append(str(error))
        result['gemini_connection'] = gemini_call_status(config)
    record['analysis'] = result
    result['genre']=request.genre
    save_json(path / 'session.json', record)
    save_json(path / 'analysis.json', result)
    return result


@app.get('/api/sessions/{identity}/transcription/{filename}')
def transcription_artifact(identity: str, filename: str):
    if filename not in {'transcription.mid', 'notes.csv', 'native.abc', 'midi2abc.abc'}:
        raise HTTPException(404, '没有找到对应转录产物。')
    record = load_session(identity)
    artifact_id = (record.get('analysis') or {}).get('pitch_artifact_id', '')
    if not re.fullmatch('basic-pitch-[a-f0-9]{32}', artifact_id):
        raise HTTPException(404, '当前分析没有 BasicPitch 转录产物。')
    path = folder(SESSIONS_DIR, identity) / artifact_id / filename
    if not path.is_file():
        raise HTTPException(404, '没有找到对应转录产物。')
    return FileResponse(path, filename=filename)


class GenerateRequest(BaseModel):
    session_id: str
    provider: str = 'local'
    style: str = Field(default='', max_length=1500)
    duration: int = Field(default=120, ge=16, le=240)
    seed:int=Field(default=831002,ge=0,le=2147483647)
    prompt_zh:str=Field(default='',max_length=1500)
    parent_job_id:str|None=None
    melody_id:str|None=None
    keep_melody: bool = True
    source_gain: float = Field(default=0, ge=0, le=1)
    abc: str | None = Field(default=None, max_length=20000)
    mma_bpm: int = Field(default=96, ge=60, le=140)
    mma_groove: Literal['Ballad', 'Ballad1', 'BalladSus'] = 'Ballad'
    mma_chords: str = Field(default='', max_length=512)
    selected_session_ids: list[str] = Field(default_factory=list,max_length=4)
    genre: Genre | None = None


def process_job(identity, request, record):
    path = folder(JOBS_DIR, identity)
    job = read_json(path / 'job.json')
    started = time.monotonic()

    def progress(stage, percent, message):
        job.update(status='processing', stage=stage, progress=percent, message=message,
                   elapsed=round(time.monotonic() - started, 1))
        save_json(path / 'job.json', job)

    try:
        progress('analyzing', 12, '正在取得真实音符与原声。')
        session_path = folder(SESSIONS_DIR, request.session_id)
        samples, sample_rate = sf.read(session_path / 'audio.wav', dtype='float32')
        analysis = record['analysis'] or analyze_audio(samples, sample_rate, record['markers'])
        if request.abc:
            analysis = {**analysis, 'abc': request.abc}
        (path / 'score.abc').write_text(analysis['abc'], encoding='utf-8')
        style = request.style.strip() or analysis['style_prompt']
        if request.genre and not request.selected_session_ids:
            style=with_style(style,request.genre)
        if request.provider == 'mma':
            tracks, output_rate, mma_plan = render_mma(samples, sample_rate, analysis,
                request.duration, path, progress, request.mma_bpm, request.mma_groove, request.mma_chords)
            gains = {'source': request.source_gain, 'melody': 0.65, 'accompaniment': 0.65}
            names = {'source': '原声', 'melody': '保留旋律', 'accompaniment': 'MMA 伴奏'}
            kinds = {'source': 'recorded', 'melody': 'fluidsynth_melody', 'accompaniment': 'mma_accompaniment'}
        elif request.provider == 'local':
            progress('rendering', 45, '正在合成音符预览与轻伴奏。')
            tracks, output_rate = render_local(samples, sample_rate, analysis, request.duration)
            gains = {'source': request.source_gain, 'melody': 0.42, 'accompaniment': 0.65}
            names = {'source': '原声', 'melody': '旋律合成', 'accompaniment': '轻伴奏'}
            kinds = {'source': 'recorded', 'melody': 'local_synthesis', 'accompaniment': 'local_synthesis'}
        else:
            use_melody = bool(request.keep_melody and analysis.get('notes') and analysis.get('abc', '').strip())
            if not style.strip():
                raise ValueError('请先用 Gemini 分析生活素材或填写创作提示词。')
            analysis={**analysis,'generation_seed':request.seed}
            generated_path = render_yue2(identity, analysis, style, request.duration, use_melody, path, progress)
            generated, output_rate = sf.read(generated_path, dtype='float32', always_2d=True)
            if not np.isfinite(generated).all() or len(generated) == 0 or len(generated) > output_rate * 600:
                raise ValueError('YuE2 返回了无效或过长的音频。')
            generated_duration = len(generated) / output_rate
            original, _, length = source_track(samples, sample_rate, generated_duration, output_rate)
            if original.ndim == 1:
                original = np.repeat(original[:, None], generated.shape[1], axis=1)
            generated = np.pad(generated, ((0, length - len(generated)), (0, 0)))
            tracks = {'source': original, 'generated': generated}
            gains = {'source': request.source_gain, 'generated': 0.75}
            names = {'source': '原声', 'generated': 'YuE2 生成混音'}
            kinds = {'source': 'recorded', 'generated': 'yue2_mix'}
            if (path / 'yue2-score.abc').is_file():
                (path / 'score.abc').write_text((path / 'yue2-score.abc').read_text(encoding='utf-8'), encoding='utf-8')
        progress('mixing', 88, '正在保存独立原声与播放结果。')
        mixed, components = mix_tracks(tracks, gains)
        sf.write(path / 'mix.wav', mixed, output_rate, subtype='PCM_16')
        manifest = []
        for track_id, audio in components.items():
            track_gain = 1.0
            if track_id == 'source' and request.source_gain == 0:
                audio = tracks['source'].astype(np.float32)
                peak = float(np.max(np.abs(audio), initial=0))
                audio = audio * min(1.0, .89 / max(peak, 1e-12))
                track_gain = 0.0
            sf.write(path / (track_id + '.wav'), audio, output_rate, subtype='PCM_16')
            manifest.append({'id': track_id, 'name': names[track_id], 'url': f'/results/{identity}/{track_id}.wav',
                             'gain': track_gain, 'kind': kinds[track_id]})
        sources=[]
        for source_id in request.selected_session_ids:
            source_record=load_session(source_id)
            sources.append({'id':source_id,'name':source_record['name'],'audio_url':source_record['audio_url'],
                'markers':source_record.get('markers',[]),'markers_origin':source_record.get('markers_origin','manual'),
                'hardware':source_record.get('hardware')})
            if source_id!=request.session_id:
                manifest.append({'id':'source_'+source_id,'name':source_record['name'],
                    'url':source_record['audio_url'],'gain':0,'kind':'recorded'})
        project = {'owner_id':job.get('owner_id'), 'id': identity, 'session_id': request.session_id, 'source': record['source'],
                   'markers': record['markers'], 'markers_origin': record.get('markers_origin','manual'), 'analysis': analysis,
                   'provider': request.provider, 'style': style, 'tracks': manifest,
                   'duration': round(len(mixed) / output_rate, 3), 'sample_rate': output_rate,
                   'source_gain': request.source_gain,
                   'sources': sources,
                   'genre': request.genre,
                   'seed':request.seed,'prompt_zh':request.prompt_zh,'prompt_en':style,
                   'requested_duration':request.duration,'parent_job_id':request.parent_job_id,
                   'melody_id':request.melody_id,'input_abc':analysis.get('abc','') if request.keep_melody else '',
                   'limitations': ['生成乐谱与音频需要试听核对；独立乐器分离未运行。']}
        if request.provider == 'mma':
            project.update(mma=mma_plan, limitations=mma_plan['warnings'],
                artifact_urls={name: f'/results/{identity}/{name}' for name in
                               ('melody.mid','accompaniment.mid','mma-plan.json','accompaniment.mma')})
        if request.provider == 'yue2':
            project.update(creation_mode='melody_conditioned' if use_melody else 'text_conditioned',
                remote=read_json(path / 'remote.json') if (path / 'remote.json').is_file() else {},
                requested_duration=request.duration, duration_is_hint=True,
                generated_duration=round(generated_duration, 3),
                prompt_url=f'/results/{identity}/yue2-prompt.txt')
            project['limitations'].append('YuE2 时长为创作参考；无人声和旋律保真需试听验收。')
        save_json(path / 'project.json', project)
        job.update(status='completed', stage='completed', progress=100, error=None,
                   message='音乐已就绪。', elapsed=round(time.monotonic() - started, 1),
                   audio_url=f'/results/{identity}/mix.wav', abc_url=f'/results/{identity}/score.abc',
                   project_url=f'/results/{identity}/project.json', tracks=manifest, duration=project['duration'])
    except Exception as error:
        config = settings()
        message = str(error)
        for secret in (config.get('ssh_password', ''), gemini_key(config)):
            if secret:
                message = message.replace(secret, '[redacted]')
        job.update(status='failed', stage='failed', error=message[:900], message='本次任务未完成。',
                   elapsed=round(time.monotonic() - started, 1))
    save_json(path / 'job.json', job)


@app.post('/api/jobs')
def generate(request: GenerateRequest):
    if request.provider not in {'local', 'yue2', 'mma'}:
        raise HTTPException(400, '请选择本地音符合成或 YuE2。')
    if request.provider in {'yue2','mma'} and not settings().get('ssh_host'):
        raise HTTPException(400, '请先配置并检查 YuE2 服务器。')
    record = load_session(request.session_id)
    identity = uuid.uuid4().hex
    path = JOBS_DIR / identity
    path.mkdir()
    job = {'id': identity, 'session_id': request.session_id, 'name': record['name'],
           'status': 'queued', 'stage': 'queued', 'progress': 0, 'error': None,
           'message': '等待处理。', 'created_at': datetime.now(timezone.utc).isoformat(),
           'provider': request.provider, 'audio_url': None, 'abc_url': None, 'project_url': None,
           'tracks': [], 'duration': None}
    save_json(path / 'job.json', stamp(job))
    executor.submit(process_job, identity, request, record)
    return {'id': identity, 'status': 'queued'}


@app.get('/api/jobs')
def jobs():
    records = []
    for path in JOBS_DIR.glob('*/job.json'):
        try:
            record=read_json(path)
            if visible(record):records.append(record)
        except (OSError, json.JSONDecodeError):
            continue
    return {'jobs': sorted(records, key=lambda item: item['created_at'], reverse=True)}

@app.patch('/api/jobs/{identity}/name')
def rename_job(identity: str, request: NameRequest):
    path=folder(JOBS_DIR,identity);record=read_json(path/'job.json');record['name']=request.name.strip();save_json(path/'job.json',record)
    if (path/'project.json').exists():
        project=read_json(path/'project.json');project['name']=request.name.strip();save_json(path/'project.json',project)
    return record

@app.delete('/api/jobs/{identity}')
def delete_job(identity: str):
    import shutil
    shutil.rmtree(folder(JOBS_DIR,identity));return {'deleted':identity}

@app.post('/api/jobs/bulk-delete')
def delete_jobs_bulk(request: BulkRequest):
    import shutil
    for identity in request.ids: shutil.rmtree(folder(JOBS_DIR,identity))
    return {'deleted':request.ids}


class CompositionRequest(BaseModel):
    session_ids:list[str]=Field(min_length=1,max_length=4)
    melody_id:str|None=None
    style:str=Field(default='',max_length=700)
    duration:int=Field(default=120,ge=16,le=240)
    seed:int=Field(default=831002,ge=0,le=2147483647)
    parent_job_id:str|None=None
    genre: Genre | None = None
    final_prompt:str|None=Field(default=None,max_length=1500)
    abc:str|None=Field(default=None,max_length=20000)


@app.post('/api/compositions')
def composition(request:CompositionRequest):
    return submit_composition(request)


def submit_composition(request:CompositionRequest,melody_snapshot=None):
    ids=list(dict.fromkeys(request.session_ids))
    records=[load_session(value) for value in ids]
    if request.melody_id and request.melody_id not in ids:
        raise HTTPException(400,'主旋律必须来自已选素材。')
    primary=next((r for r in records if r['id']==request.melody_id),records[0])
    melody=((melody_snapshot if melody_snapshot is not None else primary.get('analysis')) or {}) if request.melody_id else {}
    if request.melody_id and not melody.get('notes'):
        raise HTTPException(400,'所选哼唱尚未识别出旋律，可选择无哼唱生成。')
    parts=[request.style.strip()]
    for item in records:
        analysis=item.get('analysis') or {}
        prompt=analysis.get('style_prompt','').strip()
        if prompt:parts.append(prompt[:300])
    style=request.final_prompt.strip() if request.final_prompt is not None else '; '.join(p for p in parts if p)[:1300]
    if not style:
        raise HTTPException(400,'请先分析素材或填写创作描述。')
    user_prompt=style
    try:style=translate_prompt(style,'en')
    except Exception as error:raise HTTPException(502,'提示词翻译失败，尚未生成音乐，请检查模型配置或重试。') from error
    style=with_style(style,request.genre)
    if len(style)>1500:raise HTTPException(400,'最终提示词过长，请缩短后生成。')
    if request.melody_id and request.abc is not None:
        if not request.abc.strip():raise HTTPException(400,'所选哼唱的 ABC 不能为空，或改为无哼唱。')
        melody={**melody,'abc':request.abc}
    combined={**melody,'notes':melody.get('notes',[]),'abc':melody.get('abc',''),
        'style_prompt':style,'warnings':[], 'pitch_provider':melody.get('pitch_provider','none')}
    record={**primary,'analysis':combined,'name':'多素材创作 · '+str(len(records))+' 条声音'}
    job_request=GenerateRequest(session_id=primary['id'],provider='yue2',style=style,
        duration=request.duration,keep_melody=bool(request.melody_id),source_gain=0,selected_session_ids=ids,genre=request.genre,
        seed=request.seed,prompt_zh=user_prompt,parent_job_id=request.parent_job_id,melody_id=request.melody_id)
    identity=uuid.uuid4().hex;path=JOBS_DIR/identity;path.mkdir()
    save_json(path/'job.json',{'id':identity,'session_id':primary['id'],'name':record['name'],
        'status':'queued','stage':'queued','progress':0,'error':None,'message':'等待 YuE2 创作。',
        'created_at':datetime.now(timezone.utc).isoformat(),'provider':'yue2','tracks':[],
        'audio_url':None,'abc_url':None,'project_url':None,'duration':None,'source_ids':ids,**({'owner_id':primary['owner_id']} if primary.get('owner_id') else {})})
    executor.submit(process_job,identity,job_request,record)
    return {'id':identity,'status':'queued'}


@app.get('/api/sessions/{identity}/peaks')
def session_peaks(identity:str):
    path=folder(SESSIONS_DIR,identity)/'audio.wav'
    data,rate=sf.read(path,dtype='float32',always_2d=True)
    step=max(1,int(np.ceil(len(data)/60)))
    peaks=[float(np.max(np.abs(data[i:i+step]))) for i in range(0,len(data),step)]
    return {'peaks':peaks}


@app.get('/api/music-styles')
def music_styles():
    return {'styles':[{'id':key,**value} for key,value in STYLES.items()]}


class TranslateRequest(BaseModel):
    text:str=Field(min_length=1,max_length=1500)
    target:Literal['zh','en']='en'


@app.post('/api/prompts/translate')
def translate_music_prompt(request:TranslateRequest):
    try:return {'text':translate_prompt(request.text,request.target),'target':request.target}
    except Exception as error:raise HTTPException(502,'翻译失败，请检查文字模型配置或重试。') from error


class RegenerateRequest(BaseModel):
    prompt_zh:str=Field(min_length=1,max_length=1250)
    seed:int=Field(ge=0,le=2147483647)
    duration:int=Field(default=120,ge=16,le=240)


@app.post('/api/jobs/{identity}/regenerate')
def regenerate(identity:str,request:RegenerateRequest):
    path=folder(JOBS_DIR,identity);project=read_json(path/'project.json')
    ids=[s['id'] for s in project.get('sources',[])] or [project['session_id']]
    melody_id=project.get('melody_id') or (project['session_id'] if project.get('creation_mode')=='melody_conditioned' else None)
    return submit_composition(CompositionRequest(session_ids=ids,melody_id=melody_id,final_prompt=request.prompt_zh,
        genre=project.get('genre'),abc=project.get('input_abc') or (project.get('analysis') or {}).get('abc'),seed=request.seed,
        duration=request.duration,parent_job_id=identity),melody_snapshot=project.get('analysis') if melody_id else None)


@app.get('/api/jobs/{identity}')
def job(identity: str):
    return read_json(folder(JOBS_DIR, identity) / 'job.json')


def process_separation(identity):
    path=folder(JOBS_DIR,identity)
    def progress(stage,message):
        save_json(path/'separation.json',{'status':'processing','stage':stage,'message':message})
    try:
        result=separate_yue_file(identity,path,progress)
        save_json(path/'separation.json',{'status':'completed',**result})
    except Exception as error:
        message=str(error)
        password=settings().get('ssh_password','')
        if password:message=message.replace(password,'[redacted]')
        save_json(path/'separation.json',{'status':'failed','error':message[:500]})


@app.post('/api/jobs/{identity}/separate')
def start_separation(identity:str):
    path=folder(JOBS_DIR,identity)
    record=read_json(path/'job.json')
    if record['status']!='completed' or record['provider']!='yue2':
        raise HTTPException(400,'请选择已生成完成的 YuE2 歌曲。')
    with separation_lock:
        state=read_json(path/'separation.json') if (path/'separation.json').exists() else {}
        if state.get('status') in {'queued','processing','completed'}:return state
        state={'status':'queued','message':'等待分轨；原歌曲仍可播放。'}
        save_json(path/'separation.json',state)
        executor.submit(process_separation,identity)
        return state


@app.get('/api/jobs/{identity}/separate')
def separation_status(identity:str):
    path=folder(JOBS_DIR,identity)/'separation.json'
    return read_json(path) if path.exists() else {'status':'not_started'}


@app.get('/media/{identity}/{filename}')
def media(identity: str, filename: str):
    media_types = {'audio.wav': 'audio/wav',
                   **{'original.' + extension: mime for extension, mime in AUDIO_FORMATS.values()}}
    if filename not in media_types:
        raise HTTPException(404, '没有找到对应音频。')
    path = folder(SESSIONS_DIR, identity) / filename
    if not path.is_file():
        raise HTTPException(404, '没有找到对应音频。')
    return FileResponse(path, media_type=media_types[filename])


@app.get('/results/{identity}/{filename}')
def result(identity: str, filename: str):
    if filename not in {'mix.wav', 'source.wav', 'melody.wav', 'accompaniment.wav', 'generated.wav',
                        'score.abc', 'project.json', 'yue2-prompt.txt', 'yue2-request.json',
                        'yue2-result.json', 'yue2-transfer.json', 'melody.mid', 'accompaniment.mid',
                        'mma-plan.json', 'accompaniment.mma'}:
        raise HTTPException(404, '没有找到对应结果。')
    path = folder(JOBS_DIR, identity) / filename
    if not path.is_file():
        raise HTTPException(404, '结果文件尚未就绪。')
    return FileResponse(path, filename=filename)


app.mount('/mobile',StaticFiles(directory=ROOT/'mobile',html=True),name='mobile')
if (ROOT / 'web').exists():
    app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
