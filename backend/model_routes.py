import io
import time

import httpx
import soundfile as sf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import model_profiles as profiles
from audio_engine import test_sample
from providers import describe_audio, gemini_key, list_models, probe_model_text, settings


router = APIRouter(prefix='/api/model-profiles')


class ProfileDraft(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=100)
    protocol: str | None = Field(default=None, max_length=40)
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, repr=False)
    kind: str = 'text'


def failure(error, config=None):
    message = ('接口请求超时，请检查地址、网络和平台上游状态。' if isinstance(error, httpx.TimeoutException)
               else '无法连接接口，请检查地址、网络或代理。' if isinstance(error, httpx.RequestError)
               else str(error))
    secrets = [item.get('api_key', '') for item in profiles.read_store()['profiles']]
    if config:
        secrets.append(gemini_key(config))
    for secret in secrets:
        if secret:
            message = message.replace(secret, '[redacted]')
    return HTTPException(400, message[:600])


def draft_config(request, require_model=True):
    if request.id == 'legacy':
        config = settings(use_profile=False)
    else:
        config = profiles.runtime_config(profiles.resolve_profile(request.model_dump(exclude_unset=True), require_model))
    if not gemini_key(config):
        raise ValueError('请先填写 API Key。')
    return config


@router.get('')
def get_profiles():
    store = profiles.read_store()
    config = settings(use_profile=False)
    mode = config.get('gemini_mode', 'official')
    legacy = {'id': 'legacy', 'name': '原有 Gemini 配置', 'protocol': mode,
              'base_url': config.get('gemini_native_base_url', '') if mode == 'google_compatible' else
                          config.get('gemini_base_url', '') if mode == 'openai_compatible' else
                          'https://generativelanguage.googleapis.com',
              'model': config['gemini_model'], 'has_key': bool(gemini_key(config))}
    return {'active_id': store['active_id'], 'profiles': [legacy] + [profiles.public_profile(item) for item in store['profiles']]}


@router.post('')
def save_profile(request: ProfileDraft):
    try:
        return {'profile': profiles.save_profile(request.model_dump(exclude_unset=True))}
    except ValueError as error:
        raise failure(error) from error


@router.post('/models')
def get_models(request: ProfileDraft):
    config = None
    try:
        config = draft_config(request, require_model=False)
        return {'models': list_models(config)}
    except Exception as error:
        raise failure(error, config) from error


@router.post('/test')
def test_profile(request: ProfileDraft):
    config = None
    started = time.monotonic()
    sound_role = None
    warning = ''
    try:
        config = draft_config(request)
        if request.kind == 'audio':
            samples, rate = sf.read(io.BytesIO(test_sample()), dtype='float32')
            result = describe_audio(samples, rate, 'Instrumental piano', config=config)
            reply = result['description']
            sound_role = result.get('sound_role', 'unknown')
            if sound_role not in {'melody', 'texture'}:
                warning = '接口已返回回复，但未将测试旋律识别为旋律或声音纹理，音频理解能力尚未确认。'
        elif request.kind == 'text':
            reply = probe_model_text(config)
        else:
            raise ValueError('请选择文字或音频测试。')
        # An upstream may echo credentials; never send them back to the browser.
        reply = reply.replace(gemini_key(config), '[redacted]')
        return {'ok': not warning, 'kind': request.kind, 'reply': reply,
                'sound_role': sound_role, 'warning': warning, 'elapsed': round(time.monotonic() - started, 2),
                'model': config['gemini_model']}
    except Exception as error:
        raise failure(error, config) from error


@router.post('/{identity}/activate')
def activate_profile(identity: str):
    try:
        profiles.activate_profile(identity)
        return {'active_id': identity}
    except ValueError as error:
        raise failure(error) from error


@router.delete('/{identity}')
def delete_profile(identity: str):
    try:
        profiles.delete_profile(identity)
        return {'deleted': identity}
    except ValueError as error:
        raise failure(error) from error
