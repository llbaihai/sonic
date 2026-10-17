import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
PROTOCOLS = {'openai_compatible', 'google_compatible'}


def store_path():
    return Path(os.environ.get('SONIC_MODEL_PROFILES_PATH', str(ROOT / '.local' / 'model-profiles.json')))


def read_store():
    with LOCK:
        path = store_path()
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'active_id': 'legacy', 'profiles': []}


def write_store(store):
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def public_profile(profile):
    return {**{key: profile[key] for key in ('id', 'name', 'protocol', 'base_url', 'model')},
            'has_key': bool(profile.get('api_key'))}


def normalize_url(value, protocol, preserve_root=False):
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        original_path = parsed.path
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment)
        parsed.port  # Validate an explicitly supplied port without restricting HTTPS deployments.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('请填写不含密钥参数的 HTTPS 接口地址。')
    if any(character.isspace() for character in value):
        raise ValueError('接口地址不能包含空格或换行。')
    for suffix in ('/chat/completions', '/responses', '/models'):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
            break
    if protocol == 'google_compatible' and value.endswith('/v1'):
        value = value[:-3]
    if protocol == 'openai_compatible' and not urlsplit(value).path:
        value += '/' if preserve_root or original_path not in ('', '/') else '/v1'
    return value


def resolve_profile(draft, require_model=True):
    identity = draft.get('id')
    previous = None
    if identity:
        previous = next((item for item in read_store()['profiles'] if item['id'] == identity), None)
        if previous is None:
            raise ValueError('没有找到这组配置，请刷新页面。')
    profile = {**(previous or {}), **{key: value for key, value in draft.items()
               if key in {'name', 'protocol', 'base_url', 'model'} and value is not None}}
    profile.setdefault('name', '新配置')
    profile.setdefault('protocol', 'openai_compatible')
    profile.setdefault('model', '')
    profile.setdefault('base_url', '')
    for field in ('name', 'protocol', 'base_url', 'model'):
        profile[field] = profile[field].strip()
    if profile['protocol'] not in PROTOCOLS:
        raise ValueError('请选择 OpenAI 兼容或 Gemini 原生协议。')
    preserve_root = bool(previous and previous['base_url'].endswith('/')
                         and not urlsplit(previous['base_url']).path.strip('/')
                         and profile['base_url'] == previous['base_url']
                         and profile['protocol'] == previous['protocol'])
    profile['base_url'] = normalize_url(profile['base_url'], profile['protocol'], preserve_root=preserve_root)
    if require_model and not profile['model']:
        raise ValueError('请选择或填写模型名称。')
    if not profile['name']:
        profile['name'] = profile['model'] or urlsplit(profile['base_url']).hostname
    key = (draft.get('api_key') or '').strip()
    if not key and previous:
        if any(previous[field] != profile[field] for field in ('base_url', 'protocol')):
            raise ValueError('接口地址或协议已改变，请重新填写 API Key。')
        key = previous['api_key']
    if not key or len(key) > 4096 or any(character.isspace() for character in key):
        raise ValueError('请填写有效的 API Key，不能包含空格或换行。')
    profile.update(id=identity or uuid.uuid4().hex, api_key=key)
    return profile


def runtime_config(profile):
    return {'gemini_mode': profile['protocol'], 'gemini_model': profile['model'],
            'gemini_base_url': profile['base_url'], 'gemini_native_base_url': profile['base_url'],
            'model_profile_key': profile['api_key'], 'model_profile_id': profile['id'],
            'model_label': profile['name']}


def active_config():
    store = read_store()
    profile = next((item for item in store['profiles'] if item['id'] == store['active_id']), None)
    return runtime_config(profile) if profile else {}


def save_profile(draft):
    with LOCK:
        profile = resolve_profile(draft)
        store = read_store()
        store['profiles'] = [item for item in store['profiles'] if item['id'] != profile['id']]
        store['profiles'].append(profile)
        write_store(store)
        return public_profile(profile)


def activate_profile(identity):
    with LOCK:
        store = read_store()
        if identity != 'legacy' and not any(item['id'] == identity for item in store['profiles']):
            raise ValueError('没有找到这组配置，请刷新页面。')
        store['active_id'] = identity
        write_store(store)


def delete_profile(identity):
    with LOCK:
        store = read_store()
        if identity == 'legacy' or identity == store['active_id']:
            raise ValueError('请先启用另一组配置，再删除这组配置。')
        if not any(item['id'] == identity for item in store['profiles']):
            raise ValueError('没有找到这组配置，请刷新页面。')
        store['profiles'] = [item for item in store['profiles'] if item['id'] != identity]
        write_store(store)
