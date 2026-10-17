'use strict';

const $ = (id) => document.getElementById(id);
const state = { id: null, profiles: [], active: null, busy: false, dirty: false, models: [] };
const icons = () => window.lucide?.createIcons();
const selected = () => state.profiles.find((profile) => profile.id === state.id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

async function api(path, data) {
  const response = await fetch('/api/model-profiles' + path, data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `请求失败 (${response.status})`);
  return result;
}
function draft() {
  return { ...(state.id ? { id: state.id } : {}), protocol: 'openai_compatible',
    name: $('connectionName').value.trim(), base_url: $('endpoint').value.trim(),
    api_key: $('secret').value.trim(), model: $('chosenModel').value.trim() };
}
function notice(text, failure = false) {
  $('notice').textContent = text; $('notice').hidden = !text; $('notice').classList.toggle('failure', failure);
}
function resetResults() {
  for (const kind of ['text', 'audio']) { $(kind + 'Result').textContent = '尚未测试'; $(kind + 'Result').className = ''; }
}
function controls() {
  document.querySelectorAll('button,input').forEach((element) => { element.disabled = state.busy; });
  $('upstreamModels').disabled = state.busy || !state.models.length;
}
async function run(action) {
  if (state.busy) return;
  state.busy = true; controls(); notice('');
  try { await action(); } catch (error) { notice(error.message || '操作失败，请重试。', true); }
  finally { state.busy = false; controls(); icons(); }
}
function renderList() {
  $('profileCount').textContent = state.profiles.length;
  $('connections').innerHTML = state.profiles.length ? state.profiles.map((profile) => `<button class="profile-item" type="button" data-id="${profile.id}" aria-current="${profile.id === state.id}"><span class="profile-symbol"><i data-lucide="plug" aria-hidden="true"></i></span><span><strong>${escapeHtml(profile.name)}</strong><small>${escapeHtml(profile.model)}</small><small>${escapeHtml(new URL(profile.base_url).hostname)}</small>${profile.id === state.active?.id ? '<small class="enabled-label">当前启用</small>' : ''}</span></button>`).join('') : '<span class="muted">暂无第三方接口</span>';
  $('activeSummary').textContent = state.active ? `当前启用：${state.active.name} · ${state.active.model}` : '尚未启用';
  icons();
}
function choose(identity) {
  state.id = identity; state.dirty = false; state.models = [];
  const profile = selected();
  $('endpoint').value = profile?.base_url || '';
  $('secret').value = ''; $('secret').type = 'password';
  $('showSecret').setAttribute('aria-pressed', 'false');
  $('showSecret').title = '显示密钥'; $('showSecret').setAttribute('aria-label', '显示密钥');
  $('secret').placeholder = profile?.has_key ? '留空保留已保存的密钥' : '粘贴第三方 API Key';
  $('keyState').textContent = profile?.has_key ? '已保存 · 不回显' : '未填写';
  $('chosenModel').value = profile?.model || ''; $('connectionName').value = profile?.name || '';
  $('connectionTitle').textContent = profile?.name || '新建接口';
  $('syncState').textContent = ''; $('upstreamModels').innerHTML = '<option value="">尚未同步</option>';
  notice(''); resetResults(); renderList(); controls();
  if (identity) document.querySelector(`[data-id="${identity}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}
async function load() {
  const result = await api('');
  state.profiles = result.profiles.filter((profile) => profile.protocol === 'openai_compatible' && profile.id !== 'legacy');
  state.active = result.profiles.find((profile) => profile.id === result.active_id);
  renderList();
}
function leave() { return !state.dirty || window.confirm('接口尚未保存，放弃修改？'); }
$('newConnection').addEventListener('click', () => { if (leave()) { choose(null); $('endpoint').focus(); } });
$('connections').addEventListener('click', (event) => {
  const target = event.target.closest('[data-id]');
  if (target && target.dataset.id !== state.id && leave()) choose(target.dataset.id);
});
$('connectionForm').addEventListener('input', (event) => {
  state.dirty = true; resetResults();
  $('keyState').textContent = $('secret').value ? '待保存' : selected()?.has_key ? '已保存 · 不回显' : '未填写';
  if (['endpoint', 'secret'].includes(event.target.id)) {
    state.models = []; $('upstreamModels').innerHTML = '<option value="">尚未同步</option>';
    $('upstreamModels').disabled = true; $('syncState').textContent = '';
  }
});
$('syncModels').addEventListener('click', () => run(async () => {
  $('syncState').textContent = '正在同步上游模型…';
  try {
    const result = await api('/models', draft()); state.models = result.models;
    $('upstreamModels').innerHTML = '<option value="">选择上游模型</option>' + result.models.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join('');
    if (result.models.includes($('chosenModel').value)) $('upstreamModels').value = $('chosenModel').value;
    $('syncState').textContent = `已同步 ${result.models.length} 个模型 · ${new Date().toLocaleTimeString('zh-CN')}`;
  } catch (error) { $('syncState').textContent = '同步失败，可手动填写平台提供的模型 ID。'; throw error; }
}));
$('upstreamModels').addEventListener('change', () => {
  if (!$('upstreamModels').value) return;
  $('chosenModel').value = $('upstreamModels').value; state.dirty = true; resetResults();
});
$('showSecret').addEventListener('click', () => {
  const visible = $('secret').type === 'password'; $('secret').type = visible ? 'text' : 'password';
  const label = visible ? '隐藏密钥' : '显示密钥';
  $('showSecret').title = label; $('showSecret').setAttribute('aria-label', label); $('showSecret').setAttribute('aria-pressed', String(visible));
});
$('connectionForm').addEventListener('submit', (event) => {
  event.preventDefault();
  run(async () => {
    const result = await api('', draft()); state.id = result.profile.id; state.dirty = false; $('secret').value = '';
    await api('/' + state.id + '/activate', {}); await load(); choose(state.id);
    localStorage.setItem('sonic-model-change', String(Date.now()));
    notice('已保存并启用。新的声音分析将使用此模型。');
  });
});
for (const kind of ['text', 'audio']) $(kind === 'text' ? 'probeText' : 'probeAudio').addEventListener('click', () => run(async () => {
  const target = $(kind + 'Result'); target.className = ''; target.textContent = '正在测试，最多约 60 秒…';
  try {
    const result = await api('/test', { ...draft(), kind });
    target.textContent = `${result.ok ? '已返回回复' : '能力未确认'} · ${result.elapsed} 秒\n${result.reply}${result.warning ? '\n' + result.warning : ''}`;
    target.className = result.ok ? 'success' : 'failure';
    localStorage.setItem('sonic-model-change', String(Date.now()));
  } catch (error) { target.className = 'failure'; target.textContent = error.message; throw error; }
}));
window.addEventListener('beforeunload', (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
run(async () => { await load(); choose(state.profiles.some((profile) => profile.id === state.active?.id) ? state.active.id : null); });
