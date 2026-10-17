import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
from datetime import datetime, timezone

import httpx
import paramiko
from google import genai

from audio_engine import encode_wav
from model_profiles import active_config


ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / '.local' / 'settings.json'
GEMINI_ENDPOINTS_PATH = ROOT / '.local' / 'gemini-endpoints.json'
_gemini_last_call = None


def settings(use_profile=True):
    config = json.loads(SETTINGS_PATH.read_text(encoding='utf-8')) if SETTINGS_PATH.exists() else {}
    if GEMINI_ENDPOINTS_PATH.exists():
        config.update(json.loads(GEMINI_ENDPOINTS_PATH.read_text(encoding='utf-8')))
    config.setdefault('gemini_model', 'gemini-3.8-flash')
    config.setdefault('gemini_mode', 'official')
    if use_profile:
        config.update(active_config())
    return config


def official_audio_settings():
    config=settings(use_profile=False)
    config.update(gemini_mode='official',model_label='Google 官方 Gemini',model_profile_id='official-audio')
    config.pop('model_profile_key',None)
    return config


def gemini_key(config):
    if 'model_profile_key' in config:
        return config['model_profile_key']
    mode = config.get('gemini_mode', 'official')
    if mode in {'openai_compatible', 'google_compatible'}:
        return os.environ.get('GEMINI_COMPATIBLE_API_KEY', '') or config.get('gemini_compatible_api_key', '')
    if mode != 'official':
        raise ValueError('Gemini 接口模式无效，请选择 official、google_compatible 或 openai_compatible。')
    key = os.environ.get('GEMINI_API_KEY', '') or config.get('gemini_official_api_key', '')
    if key:
        return key
    path = config.get('gemini_key_file', '')
    if path and Path(path).is_file():
        content = Path(path).read_text(encoding='utf-8')
        found = re.search(r'(?:AQ\.[A-Za-z0-9_-]+|AIza[A-Za-z0-9_-]+)', content)
        if found:
            return found.group(0)
    return ''


def redact_model_text(text, config):
    for secret in (gemini_key(config), config.get('gemini_compatible_api_key', ''),
                   config.get('gemini_official_api_key', ''), os.environ.get('GEMINI_API_KEY', ''),
                   os.environ.get('GEMINI_COMPATIBLE_API_KEY', '')):
        if secret:
            text = text.replace(secret, '[redacted]')
    return text


def _gemini_connection_id(config):
    mode = config.get('gemini_mode', 'official')
    endpoint = (config.get('gemini_native_base_url', '') if mode == 'google_compatible' else
                config.get('gemini_base_url', '') if mode == 'openai_compatible' else 'official')
    key_hash = hashlib.sha256(gemini_key(config).encode()).hexdigest()
    return mode, config.get('gemini_model'), endpoint.rstrip('/'), key_hash


def gemini_call_status(config):
    last = _gemini_last_call
    if last is not None and last[0] == _gemini_connection_id(config):
        return dict(last[1])
    return {'state': 'unverified', 'message': '尚未完成调用验证。', 'checked_at': None}


def _record_gemini_call(config, state, message):
    global _gemini_last_call
    _gemini_last_call = (_gemini_connection_id(config),
                        {'state': state, 'message': message,
                         'checked_at': datetime.now(timezone.utc).isoformat()})


def _official_audio(config, key, prompt, audio):
    options = {'timeout': 45000}
    if config.get('proxy'):
        options['client_args'] = {'proxy': config['proxy']}
    client = genai.Client(api_key=key, http_options=options)
    try:
        response = client.interactions.create(model=config['gemini_model'],
            input=[{'type': 'audio', 'data': audio, 'mime_type': 'audio/wav'},
                   {'type': 'text', 'text': prompt}], generation_config={'max_output_tokens': 2048})
        text = getattr(response, 'output_text', '') or ''
        if not text:
            outputs = getattr(response, 'outputs', None) or getattr(response, 'output', None) or []
            text = ''.join(getattr(item, 'text', '') or '' for item in outputs)
        return text
    finally:
        client.close()


def _gateway_json(base_url, path, headers, payload=None, method='POST', proxy=None):
    base_url = base_url.strip().rstrip('/')
    parsed = urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API 地址需要是不含密钥参数的 HTTPS 地址，例如 https://example.com/v1。')
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False, proxy=proxy) as client:
        response = (client.get(base_url + path, headers=headers) if method == 'GET' else
                    client.post(base_url + path, headers=headers, json=payload))
        try:
            body = response.json()
        except ValueError as error:
            raise ValueError(f'兼容接口没有返回 JSON（HTTP {response.status_code}）。') from error
        if response.status_code != 200:
            detail = body.get('error', {}) if isinstance(body, dict) else {}
            message = detail.get('message', '') if isinstance(detail, dict) else str(detail)
            raise RuntimeError(f'兼容接口 HTTP {response.status_code}：{message}')
    return body


def _compatible_audio(config, key, prompt, audio):
    payload = {'model': config['gemini_model'], 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt},
        {'type': 'input_audio', 'input_audio': {'data': audio, 'format': 'wav'}}]}],
        'max_tokens': 2048, 'stream': False}
    body = _gateway_json(config.get('gemini_base_url', ''), '/chat/completions',
                         {'Authorization': 'Bearer ' + key}, payload)
    choices = body.get('choices') if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError('兼容接口没有返回有效的模型回复。')
    message = choices[0].get('message', {})
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, list):
        content = ''.join(item['text'] for item in content
                          if isinstance(item, dict) and isinstance(item.get('text'), str))
    if not isinstance(content, str) or not content.strip():
        raise ValueError('兼容接口返回了空回复，请检查音频输入和模型输出限制。')
    return content


def _google_compatible_audio(config, key, prompt, audio):
    base_url = config.get('gemini_native_base_url', '').strip().rstrip('/')
    prefix = '' if urlsplit(base_url).path.rstrip('/').endswith('/v1beta') else '/v1beta'
    path = prefix + '/models/' + quote(config['gemini_model'], safe='') + ':generateContent'
    payload = {'contents': [{'role': 'user', 'parts': [
        {'text': prompt}, {'inline_data': {'mime_type': 'audio/wav', 'data': audio}}]}],
        'generationConfig': {'maxOutputTokens': 2048}}
    body = _gateway_json(base_url, path, {'x-goog-api-key': key}, payload)
    candidates = body.get('candidates') if isinstance(body, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise ValueError('Gemini 原生兼容接口没有返回有效的模型回复。')
    parts = candidates[0].get('content', {}).get('parts', [])
    text = ''.join(part['text'] for part in parts
                   if isinstance(part, dict) and isinstance(part.get('text'), str) and not part.get('thought'))
    if not text.strip():
        raise ValueError('Gemini 原生兼容接口没有返回可用文字。')
    return text


def describe_audio(samples, sample_rate, style, config=None):
    config = settings() if config is None else config
    key = gemini_key(config)
    if not key:
        raise ValueError('尚未配置当前模型的 API 密钥。')
    prompt = (
        'Listen to this short original recording. Describe the actual sounds, do not invent a song title. '
        'Return ONLY a JSON object with sound_role (melody/rhythm/texture/voice/unknown), '
        'description (concise Chinese), mood (concise Chinese), style_prompt (English music generation prompt), '
        'and style_prompt_zh (equivalent Simplified Chinese music generation prompt for the user to edit). '
        'Use uncertain language when the source is ambiguous. Do not invent exact notes, tempo or key. '
        'This is an instrumental music project. The style prompt must request no vocals, warm production, '
        'and explicitly reference identifiable source events such as cat meows, ocean waves or footsteps, '
        'asking for those sound textures in the composition where appropriate, not only a generic mood. '
        'Do not claim that a downstream music model is guaranteed to reproduce these sounds. '
        'and retaining the provided melody when a score is supplied. User direction: ' + style[:500]
        + '. Follow the chosen primary genre in the user direction; do not default to piano pop when rock, classical or electronic is requested.'
    )
    audio = base64.b64encode(encode_wav(samples, sample_rate)).decode('ascii')
    try:
        if config.get('gemini_mode', 'official') == 'openai_compatible':
            text = _compatible_audio(config, key, prompt, audio)
        elif config.get('gemini_mode') == 'google_compatible':
            text = _google_compatible_audio(config, key, prompt, audio)
        else:
            text = _official_audio(config, key, prompt, audio)
        text = text.strip()
        if text.startswith('```'):
            lines = text.splitlines()
            text = '\n'.join(lines[1:-1]) if lines[-1].strip() == '```' else text
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError('模型没有返回有效的创作计划。')
        if not all(isinstance(result.get(field), str) and result[field].strip()
                   for field in ('description', 'style_prompt')):
            raise ValueError('模型回复缺少声音描述或创作提示词。')
        role = result.get('sound_role', 'unknown')
        if role not in {'melody', 'rhythm', 'texture', 'voice', 'unknown'}:
            role = 'unknown'
        if role == 'unknown':
            detail = redact_model_text(str(result.get('description', '')), config)[:200]
            raise ValueError('音频理解未确认：模型未识别出可分析的声音。' + detail +
                             ' 请在模型配置中测试支持音频的模型，或选择 Gemini 原生接口。')
        _record_gemini_call(config, 'success', '最近一次调用成功，已取得有效创作计划。')
        return {'sound_role': role, 'description': redact_model_text(str(result.get('description', '')), config)[:600],
                'mood': redact_model_text(str(result.get('mood', '')), config)[:120],
                'style_prompt': redact_model_text(str(result.get('style_prompt', '')), config)[:1500], 'gemini_used': True,
                'style_prompt_zh': redact_model_text(str(result.get('style_prompt_zh','')),config)[:1500],
                'ai_name': config.get('model_label', config['gemini_model']),
                'gemini_mode': config.get('gemini_mode', 'official'), 'gemini_model': config['gemini_model']}
    except Exception as error:
        message = ('接口在 60 秒内没有返回，请检查平台上游状态或网络。'
                   if isinstance(error, httpx.ReadTimeout) else str(error))
        message = redact_model_text(message, config)
        _record_gemini_call(config, 'failed', message[:450])
        raise RuntimeError('模型调用未完成：' + message[:450]) from error


def list_models(config):
    key = gemini_key(config)
    if config['gemini_mode'] == 'openai_compatible':
        body = _gateway_json(config['gemini_base_url'], '/models',
                             {'Authorization': 'Bearer ' + key}, method='GET')
        entries = body.get('data', []) if isinstance(body, dict) else []
        models = [item['id'] for item in entries if isinstance(item, dict) and isinstance(item.get('id'), str)]
    else:
        official = config['gemini_mode'] == 'official'
        base = 'https://generativelanguage.googleapis.com' if official else config['gemini_native_base_url']
        prefix = '' if urlsplit(base).path.rstrip('/').endswith('/v1beta') else '/v1beta'
        models, page_token = [], ''
        for _ in range(10):
            path = prefix + '/models?pageSize=100' + ('&pageToken=' + quote(page_token, safe='') if page_token else '')
            body = _gateway_json(base, path, {'x-goog-api-key': key}, method='GET',
                                 proxy=config.get('proxy') if official else None)
            if not isinstance(body, dict):
                raise ValueError('接口没有返回有效的模型列表，请手动填写模型名称。')
            models.extend(item['name'].removeprefix('models/') for item in body.get('models', [])
                          if isinstance(item, dict) and isinstance(item.get('name'), str)
                          and 'generateContent' in item.get('supportedGenerationMethods', ['generateContent']))
            page_token = body.get('nextPageToken', '')
            if not page_token:
                break
    models = [model for model in models if redact_model_text(model, config) == model]
    if not models:
        raise ValueError('接口未返回模型列表，可直接手动填写平台提供的模型名称。')
    return sorted(set(models))


def probe_model_text(config):
    key = gemini_key(config)
    if config['gemini_mode'] == 'openai_compatible':
        body = _gateway_json(config['gemini_base_url'], '/chat/completions',
            {'Authorization': 'Bearer ' + key}, {'model': config['gemini_model'],
            'messages': [{'role': 'user', 'content': 'Reply with the single word OK.'}], 'stream': False})
        choices = body.get('choices', []) if isinstance(body, dict) else []
        message = choices[0].get('message', {}) if choices and isinstance(choices[0], dict) else {}
        text = message.get('content', '')
        if isinstance(text, list):
            text = ''.join(item.get('text', '') for item in text if isinstance(item, dict))
    else:
        official = config['gemini_mode'] == 'official'
        base = 'https://generativelanguage.googleapis.com' if official else config['gemini_native_base_url']
        prefix = '' if urlsplit(base).path.rstrip('/').endswith('/v1beta') else '/v1beta'
        body = _gateway_json(base, prefix + '/models/' + quote(config['gemini_model'], safe='') + ':generateContent',
            {'x-goog-api-key': key}, {'contents': [{'parts': [{'text': 'Reply with the single word OK.'}]}],
            'generationConfig': {'maxOutputTokens': 128}}, proxy=config.get('proxy') if official else None)
        candidates = body.get('candidates', []) if isinstance(body, dict) else []
        parts = candidates[0].get('content', {}).get('parts', []) if candidates else []
        text = ''.join(item.get('text', '') for item in parts if isinstance(item, dict) and not item.get('thought'))
    if not isinstance(text, str) or not text.strip():
        raise ValueError('接口没有返回可用文字，请检查模型名称、余额和平台上游状态。')
    return redact_model_text(text.strip(), config)[:600]


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint

    def missing_host_key(self, client, hostname, key):
        actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if not self.fingerprint or not hmac.compare_digest(actual, self.fingerprint):
            raise paramiko.SSHException('服务器指纹与已有记录不一致，需要先核对当前实例。')


def connect_yue2(config=None):
    config = config or settings()
    if not config.get('ssh_host') or not config.get('ssh_password'):
        raise ValueError('尚未配置 YuE2 服务器连接。')
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHostKey(config.get('ssh_fingerprint', '')))
    try:
        client.connect(config['ssh_host'], port=int(config.get('ssh_port', 22)),
                       username=config.get('ssh_user', 'root'), password=config['ssh_password'],
                       look_for_keys=False, allow_agent=False, timeout=8, auth_timeout=8,
                       banner_timeout=8, channel_timeout=10)
        client.get_transport().set_keepalive(15)
        return client
    except Exception:
        client.close()
        raise


def probe_yue2():
    config = settings()
    try:
        with connect_yue2(config) as client:
            from remote_music import MODEL, VAE
            project = config.get('yue2_project', '/srv/sonic/YuE')
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(15)
                sftp.stat(project + '/.venv/bin/python')
                sftp.stat(config.get('yue2_model', MODEL) + '/model.safetensors')
                sftp.stat(config.get('yue2_vae', VAE) + '/model.safetensors')
                sftp.stat(project + '/skills/yue2-music/instrumental/scripts/instrumentalize.py')
                try:
                    sftp.stat(config.get('basic_pitch_python', '/srv/sonic/BasicPitch/.conda/bin/python'))
                    basic_pitch_available = True
                except FileNotFoundError:
                    basic_pitch_available = False
            _, output, _ = client.exec_command('nvidia-smi --query-gpu=name,memory.total,memory.used '
                                               '--format=csv,noheader,nounits', timeout=10)
            gpu = output.read(4096).decode('utf-8', errors='replace').strip()
            available = output.channel.recv_exit_status() == 0 and bool(gpu)
            return {'available': available, 'gpu': gpu, 'basic_pitch_available': basic_pitch_available,
                    'message': '已连接 GPU，并找到模型和器乐转换工具。' if available else '未取得 GPU。'}
    except Exception as error:
        return {'available': False, 'gpu': '', 'basic_pitch_available': False,
                'message': '服务器尚不可用：' + redact_model_text(str(error), config)[:200]}


def render_yue2(job_id, analysis, style, duration, keep_melody, output_dir, progress):
    from remote_music import render_yue2 as render
    return render(job_id, analysis, style, duration, keep_melody, output_dir, progress)
