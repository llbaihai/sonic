'use strict';

const $ = (id) => document.getElementById(id);
const state = { profiles: [], activeId: 'legacy', selectedId: null, busy: false, dirty: false };
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const icons = () => window.lucide?.createIcons();
const selected = () => state.profiles.find((item) => item.id === state.selectedId);
const protocol = () => document.querySelector('input[name="protocol"]:checked')?.value || 'openai_compatible';

async function api(path, data, method = 'POST') {
  const response = await fetch('/api/model-profiles' + path, data === undefined ? { method } : {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
  return body;
}
function notice(message, failed = false) {
  $('pageNotice').textContent = message;
  $('pageNotice').classList.toggle('failure', failed);
  $('pageNotice').hidden = !message;
}
function draft() {
  if (state.selectedId === 'legacy') return { id: 'legacy' };
  return { ...(state.selectedId ? { id: state.selectedId } : {}), name: $('profileName').value.trim(),
    protocol: protocol(), base_url: $('baseUrl').value.trim(), model: $('modelName').value.trim(), api_key: $('apiKey').value.trim() };
}
function controls() {
  const legacy = state.selectedId === 'legacy';
  document.querySelectorAll('button').forEach((button) => { button.disabled = state.busy; });
  for (const id of ['profileName', 'baseUrl', 'apiKey', 'modelName', 'discoveredModels']) $(id).disabled = state.busy || legacy;
  document.querySelectorAll('input[name="protocol"]').forEach((input) => { input.disabled = state.busy || legacy; });
  $('deleteProfile').hidden = !state.selectedId || legacy;
  $('deleteProfile').disabled = state.busy || state.selectedId === state.activeId;
  $('deleteProfile').title = state.selectedId === state.activeId ? '请先启用另一组配置' : '删除此配置';
  $('saveActivate').hidden = legacy; $('saveOnly').hidden = legacy;
  $('activateLegacy').hidden = !legacy;
  $('activateLegacy').disabled = state.busy || state.selectedId === state.activeId;
  $('activateLegacy').innerHTML = `<i data-lucide="check" aria-hidden="true"></i>${state.selectedId === state.activeId ? '当前已启用' : '启用此配置'}`;
  $('fetchModels').disabled = state.busy || (legacy && !selected()?.has_key);
  $('toggleKey').disabled = state.busy || legacy;
  icons();
}
function renderList() {
  $('profileCount').textContent = state.profiles.length;
  $('profileList').setAttribute('aria-busy', 'false');
  $('profileList').innerHTML = state.profiles.map((item) => `<button type="button" class="profile-item" data-id="${escapeHtml(item.id)}" aria-current="${item.id === state.selectedId}"><span class="profile-symbol ${item.protocol === 'openai_compatible' ? '' : 'google'}"><i data-lucide="${item.protocol === 'openai_compatible' ? 'cpu' : 'sparkles'}" aria-hidden="true"></i></span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.model)}</small><small>${escapeHtml(new URL(item.base_url || 'https://generativelanguage.googleapis.com').hostname)}</small>${item.id === state.activeId ? '<small class="enabled-label">当前启用</small>' : ''}</span></button>`).join('');
  const active = state.profiles.find((item) => item.id === state.activeId);
  $('activeSummary').textContent = active ? `当前启用：${active.name} · ${active.model}` : '尚未启用模型';
  icons();
}
function clearTests() {
  $('textTestResult').textContent = '尚未测试'; $('textTestResult').className = '';
  $('audioTestResult').textContent = '尚未测试 · 使用合成测试旋律'; $('audioTestResult').className = '';
}
function fillForm(identity) {
  state.selectedId = identity; state.dirty = false;
  const item = selected(), legacy = identity === 'legacy';
  $('profileName').value = item?.name || '';
  $('baseUrl').value = item?.base_url || '';
  $('modelName').value = item?.model || '';
  $('apiKey').value = ''; $('apiKey').type = 'password';
  $('apiKey').placeholder = item?.has_key ? '留空保留已保存的密钥' : '粘贴 API Key';
  $('toggleKey').setAttribute('aria-pressed', 'false');
  $('toggleKey').setAttribute('aria-label', '显示本次输入的密钥');
  $('toggleKey').title = '显示本次输入的密钥';
  $('toggleKey').innerHTML = '<i data-lucide="eye" aria-hidden="true"></i>';
  $('keyState').textContent = item?.has_key ? '已保存 · 不回显' : '未填写';
  const radio = document.querySelector(`input[name="protocol"][value="${item?.protocol === 'google_compatible' || item?.protocol === 'official' ? 'google_compatible' : 'openai_compatible'}"]`);
  radio.checked = true;
  $('editorHeading').textContent = item?.name || '新建配置';
  $('editorState').textContent = item ? (identity === state.activeId ? '当前启用' : '已保存') : '尚未保存';
  $('editorState').classList.toggle('active', identity === state.activeId);
  $('legacyNotice').hidden = !legacy;
  $('discoveredModels').hidden = true; $('discoveredModels').replaceChildren(); $('modelsMessage').textContent = '';
  clearTests(); renderList(); controls();
}
async function loadProfiles(keepSelection = true) {
  const result = await api('', undefined, 'GET');
  state.profiles = result.profiles; state.activeId = result.active_id;
  if (!keepSelection) fillForm(state.activeId);
  else renderList();
}
async function busy(action) {
  if (state.busy) return;
  state.busy = true; controls(); notice('');
  try { await action(); } catch (error) { notice(error.message || '操作失败，请重试。', true); }
  finally { state.busy = false; controls(); }
}
function canLeave() { return !state.dirty || window.confirm('此配置尚未保存，放弃修改？'); }

$('newProfile').addEventListener('click', () => { if (canLeave()) { fillForm(null); notice(''); $('profileName').focus(); } });
$('profileList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-id]');
  if (button && button.dataset.id !== state.selectedId && canLeave()) { fillForm(button.dataset.id); notice(''); }
});
$('profileForm').addEventListener('input', () => {
  if (state.selectedId === 'legacy') return;
  state.dirty = true; $('editorState').textContent = '有未保存的修改'; $('editorState').classList.remove('active');
  $('keyState').textContent = $('apiKey').value ? '待保存' : selected()?.has_key ? '已保存 · 不回显' : '未填写';
  $('discoveredModels').hidden = true; clearTests();
});
$('profileForm').addEventListener('submit', (event) => {
  event.preventDefault(); const activate = event.submitter?.dataset.activate === 'true';
  busy(async () => {
    const result = await api('', draft());
    const identity = result.profile.id;
    state.dirty = false; $('apiKey').value = '';
    if (activate) await api('/' + identity + '/activate');
    await loadProfiles(); fillForm(identity);
    localStorage.setItem('sonic-model-change', String(Date.now()));
    notice(activate ? '配置已保存并启用，声音分析将使用此模型。' : '配置已保存。');
  });
});
$('activateLegacy').addEventListener('click', () => busy(async () => {
  await api('/legacy/activate'); await loadProfiles(); fillForm('legacy');
  localStorage.setItem('sonic-model-change', String(Date.now())); notice('已恢复原有 Gemini 配置。');
}));
$('deleteProfile').addEventListener('click', () => {
  if (!window.confirm(`删除“${selected()?.name}”及其本机密钥？`)) return;
  busy(async () => { await api('/' + state.selectedId, undefined, 'DELETE'); state.dirty = false; await loadProfiles(false); notice('配置已删除。'); });
});
$('toggleKey').addEventListener('click', () => {
  const visible = $('apiKey').type === 'password'; $('apiKey').type = visible ? 'text' : 'password';
  $('toggleKey').setAttribute('aria-pressed', String(visible));
  const label = visible ? '隐藏本次输入的密钥' : '显示本次输入的密钥';
  $('toggleKey').title = label; $('toggleKey').setAttribute('aria-label', label);
  $('toggleKey').innerHTML = `<i data-lucide="${visible ? 'eye-off' : 'eye'}" aria-hidden="true"></i>`; icons();
});
$('fetchModels').addEventListener('click', () => busy(async () => {
  $('modelsMessage').textContent = '正在获取模型列表…';
  try {
    const result = await api('/models', draft());
    $('discoveredModels').innerHTML = '<option value="">选择模型</option>' + result.models.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    $('discoveredModels').hidden = false;
    $('modelsMessage').textContent = `已获取 ${result.models.length} 个模型`;
  } catch (error) { $('modelsMessage').textContent = '获取失败，可手动填写模型名称。'; throw error; }
}));
$('discoveredModels').addEventListener('change', () => {
  if (!$('discoveredModels').value) return;
  $('modelName').value = $('discoveredModels').value; state.dirty = true;
  $('editorState').textContent = '有未保存的修改'; $('editorState').classList.remove('active'); clearTests();
});
for (const kind of ['text', 'audio']) {
  $(kind === 'text' ? 'testText' : 'testAudio').addEventListener('click', () => busy(async () => {
    const target = $(kind + 'TestResult'); target.className = '';
    target.textContent = kind === 'audio' ? '正在分析合成测试旋律，最多约 60 秒…' : '正在请求模型，最多约 60 秒…';
    const started = Date.now();
    try {
      const result = await api('/test', { ...draft(), kind });
      const verdict = result.ok ? (kind === 'audio' ? '已返回声音描述' : '通过') : '音频能力未确认';
      target.textContent = `${verdict} · ${result.elapsed} 秒 · ${result.model}\n${result.reply}${result.warning ? '\n' + result.warning : ''}`;
      target.className = result.ok ? 'success' : 'failure';
      if (kind === 'audio') localStorage.setItem('sonic-model-change', String(Date.now()));
    } catch (error) { target.textContent = `未通过 · ${((Date.now() - started) / 1000).toFixed(1)} 秒\n${error.message}`; target.className = 'failure'; throw error; }
  }));
}
$('refreshProfiles').addEventListener('click', () => { if (canLeave()) busy(async () => { await loadProfiles(false); notice('配置已刷新。'); }); });
window.addEventListener('beforeunload', (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
busy(async () => { await loadProfiles(false); });
