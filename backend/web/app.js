'use strict';

const $ = (id) => document.getElementById(id);
const sourceLabels = { upload: '上传音频', microphone: '麦克风录音', sample: '测试旋律' };
const providerLabels = { local: '本地音符合成', yue2: 'YuE2', mma: 'MMA＋FluidSynth' };
const state = { sessions: [], jobs: [], session: null, sourceBuffer: null, status: null, busy: false, recording: null, analyzing: false, generating: false, job: null, pollTimer: null, selection: 0, resultSelection: 0, tracks: [], playing: false, playToken: 0, offset: 0, startedAt: 0, duration: 0, audioContext: null, master: null, nodes: [], frame: 0 };

function icons() { window.lucide?.createIcons(); }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]); }
function time(seconds) { const value = Math.max(0, Math.floor(Number(seconds) || 0)); return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`; }
function error(message) { $('errorText').textContent = String(message); $('errorBanner').hidden = false; }
function clearError() { $('errorBanner').hidden = true; }
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
  return body;
}
function jsonOptions(data, method = 'POST') { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }; }
function audioContext() {
  if (!state.audioContext) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) throw new Error('此浏览器不支持音频处理，请使用最新版 Chrome 或 Edge。');
    state.audioContext = new AudioCtx();
    state.master = state.audioContext.createGain();
    state.master.gain.value = Number($('masterVolume').value);
    state.master.connect(state.audioContext.destination);
  }
  return state.audioContext;
}
async function decode(urlOrBuffer) {
  let data = urlOrBuffer;
  if (typeof data === 'string') {
    const response = await fetch(data);
    if (!response.ok) throw new Error('音频读取失败，请刷新后重试。');
    data = await response.arrayBuffer();
  }
  return audioContext().decodeAudioData(data.slice(0));
}
function wavBlob(channels, sampleRate) {
  const count = channels.length, frames = channels[0].length;
  const data = new ArrayBuffer(44 + frames * count * 2), view = new DataView(data);
  const string = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  string(0, 'RIFF'); view.setUint32(4, data.byteLength - 8, true); string(8, 'WAVE'); string(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, count, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * count * 2, true);
  view.setUint16(32, count * 2, true); view.setUint16(34, 16, true); string(36, 'data'); view.setUint32(40, frames * count * 2, true);
  for (let frame = 0, offset = 44; frame < frames; frame++) for (let channel = 0; channel < count; channel++, offset += 2) {
    const sample = Math.max(-1, Math.min(1, channels[channel][frame]));
    view.setInt16(offset, sample < 0 ? sample * 32768 : sample * 32767, true);
  }
  return new Blob([data], { type: 'audio/wav' });
}
function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function updateControls() {
  const importing = state.busy || !!state.recording;
  $('uploadButton').disabled = importing;
  $('sampleButton').disabled = importing;
  $('recordButton').disabled = state.busy;
  $('analyzeButton').disabled = !state.session || state.analyzing || importing;
  $('generateButton').disabled = !state.session?.analysis || state.generating || state.analyzing || importing;
  $('addMarker').disabled = !state.session || state.busy || state.analyzing || state.generating;
  $('abcEditor').disabled = !state.session?.analysis || state.generating;
  $('downloadAbc').disabled = !state.session?.analysis;
  $('providerInput').disabled = state.generating;
  ['mmaBpm', 'mmaGroove', 'mmaChords'].forEach(id => { $(id).disabled = state.generating; });
  $('jobSelect').disabled = state.generating;
  $('useGemini').disabled = !state.status?.gemini?.configured || state.analyzing;
  $('pitchProvider').disabled = state.analyzing || state.generating;
  $('probeRemote').disabled = state.probingRemote || state.analyzing || state.generating;
  if ($('pitchProvider').value === 'basic_pitch' && !state.status?.yue2?.basic_pitch_available) $('analyzeButton').disabled = true;
  const loaded = state.tracks.length > 0;
  ['playButton', 'rewindButton', 'seekInput', 'downloadWav'].forEach((id) => { $(id).disabled = !loaded; });
}
function canvasContext(canvas) {
  const rect = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  const context = canvas.getContext('2d'); context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width: rect.width, height: rect.height };
}
function drawWave(canvas, buffer, color = '#a48ac5', fullDuration = buffer?.duration) {
  const { context, width, height } = canvasContext(canvas);
  context.clearRect(0, 0, width, height);
  if (!buffer || width < 1) return;
  const samples = buffer.getChannelData(0), audioWidth = width * buffer.duration / fullDuration;
  context.fillStyle = color;
  for (let x = 2; x < audioWidth - 2; x += 3) {
    const start = Math.floor(x / audioWidth * samples.length), end = Math.max(start + 1, Math.floor((x + 3) / audioWidth * samples.length));
    let peak = 0;
    for (let i = start; i < Math.min(end, samples.length); i++) peak = Math.max(peak, Math.abs(samples[i]));
    const bar = Math.max(1, peak * (height - 16));
    context.fillRect(x, (height - bar) / 2, 1.7, bar);
  }
  if (canvas.id === 'sourceWaveform') {
    context.fillStyle = '#a7544a';
    for (const marker of state.session?.markers || []) { const x = marker / buffer.duration * width; context.fillRect(x, 0, 1, height); context.fillRect(x - 3, 0, 7, 5); }
    const x = $('sourceAudio').currentTime / buffer.duration * width;
    context.fillStyle = '#ef8074'; context.fillRect(x, 0, 1.5, height);
  }
}
function drawNotes() {
  const { context, width, height } = canvasContext($('notesCanvas'));
  context.clearRect(0, 0, width, height);
  const notes = state.session?.analysis?.notes || [];
  $('notesEmpty').hidden = !!notes.length;
  if (!notes.length) { $('notesEmpty').textContent = state.session?.analysis ? '未检测到可辨识的旋律' : '等待旋律分析'; return; }
  const min = Math.min(...notes.map((note) => note.midi)) - 2, max = Math.max(...notes.map((note) => note.midi)) + 2;
  const duration = Math.max(state.session.duration, ...notes.map((note) => note.end)), pad = 24;
  context.font = '9px "Segoe UI", sans-serif'; context.textBaseline = 'middle';
  for (let midi = min; midi <= max; midi++) {
    const y = (max - midi + 0.5) / (max - min + 1) * (height - 12) + 6;
    context.strokeStyle = '#ecebee'; context.beginPath(); context.moveTo(pad, y); context.lineTo(width, y); context.stroke();
  }
  for (const note of notes) {
    const x = pad + note.start / duration * (width - pad), y = (max - note.midi + 0.5) / (max - min + 1) * (height - 12) + 6;
    const w = Math.max(3, (note.end - note.start) / duration * (width - pad));
    context.fillStyle = '#9180b8'; context.fillRect(x, y - 3, w, 6);
    if (w > 27) { context.fillStyle = '#fff'; context.fillText(note.name || String(note.midi), x + 3, y); }
  }
}
function drawAll() {
  drawWave($('sourceWaveform'), state.sourceBuffer); drawNotes();
  for (const track of state.tracks) drawWave($(`wave-${track.id}`), track.buffer, '#ffffff', state.duration);
}
function renderSessions() {
  $('sourceCount').textContent = state.sessions.length;
  $('sessionList').innerHTML = state.sessions.map((session) => `<button class="session-item ${session.id === state.session?.id ? 'selected' : ''}" data-session="${escapeHtml(session.id)}" aria-pressed="${session.id === state.session?.id}"><i data-lucide="${session.source === 'microphone' ? 'mic' : 'audio-lines'}" aria-hidden="true"></i><span class="session-content"><strong>${escapeHtml(session.name)}</strong><small>${escapeHtml(sourceLabels[session.source] || session.source)} · ${time(session.duration)}</small></span></button>`).join('');
  if (!state.sessions.length) $('sessionList').innerHTML = '<span class="muted">暂无声音素材</span>';
  icons();
}
function renderMarkers() {
  $('markerList').innerHTML = (state.session?.markers || []).map((marker, index) => `<button class="marker-button" data-marker="${index}" title="跳转到标记 ${index + 1}">${index + 1} · ${time(marker)}</button>`).join('');
  drawWave($('sourceWaveform'), state.sourceBuffer);
}
function renderGeminiStatus() {
  const gemini = state.status?.gemini;
  const compatible = ['openai_compatible', 'google_compatible'].includes(gemini?.mode);
  const protocolLabel = gemini?.mode === 'google_compatible' ? 'API + URL（Gemini）' : compatible ? 'API + URL（OpenAI兼容）' : '官方接口';
  const connectionLabel = { success: '最近调用成功', failed: '最近调用失败，尚未接通', unverified: '密钥已配置，尚未验证调用' }[gemini?.connection?.state || 'unverified'];
  $('geminiStatus').textContent = gemini?.configured ? `${gemini.name || 'AI'} · ${gemini.model || '已配置'} · ${protocolLabel} · ${connectionLabel}` : 'AI 未配置 · 可使用本地 pYIN';
  $('geminiStatus').title = gemini?.connection?.message || '';
  $('geminiConsentLabel').textContent = `同意将此段音频发送至 ${gemini?.endpoint_host || '模型接口'}，由 ${gemini?.model || '所选模型'} 分析`;
}
function renderRemoteStatus() {
  const remote = state.status?.yue2 || {};
  const option = $('providerInput').querySelector('[value="yue2"]');
  option.disabled = !remote.available;
  option.textContent = remote.available ? 'YuE2 · 服务器可用' : 'YuE2 · 尚未连接';
  $('pitchProvider').querySelector('[value="basic_pitch"]').disabled = !remote.basic_pitch_available;
  $('remoteStatus').textContent = remote.message || '尚未检查';
  $('remoteStatus').title = remote.gpu || '';
  $('pitchConsent').hidden = $('pitchProvider').value !== 'basic_pitch';
  $('durationLabel').textContent = $('providerInput').value === 'yue2' ? '目标时长（参考）' : '时长';
  const mma = $('providerInput').value === 'mma';
  $('mmaControls').hidden = !mma;
  $('styleInput').disabled = mma;
  const hasMelody = !!state.session?.analysis?.notes?.length;
  $('keepMelody').disabled = mma || !hasMelody;
  if (!hasMelody) $('keepMelody').checked = false;
  if (mma) $('keepMelody').checked = hasMelody;
  $('abcEditor').readOnly = mma;
}
function renderAnalysis() {
  const analysis = state.session?.analysis;
  $('abcEditor').value = analysis?.abc || '';
  const unconfirmedAudio = analysis?.gemini_used && analysis.sound_role === 'unknown';
  const geminiFailed = unconfirmedAudio || (analysis && !analysis.gemini_used && (analysis.gemini_attempted || (analysis.warnings || []).some(warning => typeof warning === 'string' && /^(Gemini|模型) 调用未完成/.test(warning))));
  const pitchName = analysis?.pitch_provider === 'none' ? '生活素材' : analysis?.pitch_provider === 'basic_pitch' ? 'BasicPitch' : '本地 pYIN';
  $('pitchDownloads').hidden = !analysis?.transcription_urls;
  for (const [id, key] of [['pitchMidi', 'midi'], ['pitchCsv', 'csv']]) {
    setDownloadLink(id, analysis?.transcription_urls?.[key]);
  }
  $('analysisStatus').textContent = analysis ? (geminiFailed ? `${pitchName} · AI 音频未确认` : analysis.gemini_used ? `${pitchName} + ${analysis.ai_name || analysis.gemini_model || 'Gemini'}` : pitchName) : '等待分析';
  $('analysisSummary').innerHTML = analysis ? `<span>${analysis.notes?.length || 0} 个音符</span><span>有声比例 ${Math.round((analysis.voiced_ratio || 0) * 100)}%</span><span>${escapeHtml(analysis.tempo)} BPM${analysis.tempo_source === 'default' ? ' · 默认' : ''}</span>` : '—';
  $('analysisDescription').hidden = !analysis?.description;
  $('analysisDescription').textContent = [analysis?.description, analysis?.sound_role, analysis?.mood].filter(Boolean).join(' · ');
  $('analysisWarnings').textContent = [...(analysis?.warnings || []), ...(unconfirmedAudio ? ['这条历史 AI 回复未识别出声音，不能视为音频分析成功。请换用已测试的音频模型后重新分析。'] : [])].join('；');
  drawNotes(); renderRemoteStatus(); updateControls();
}
async function selectSession(id) {
  const session = state.sessions.find((item) => item.id === id);
  if (!session) return;
  const selection = ++state.selection;
  $('sourceAudio').pause(); state.session = session; state.sourceBuffer = null;
  $('useGemini').checked = false;
  $('sourceTitle').textContent = session.name;
  $('sourceTag').textContent = sourceLabels[session.source] || session.source;
  $('sourceMeta').textContent = `${session.duration.toFixed(1)} 秒 · ${(session.sample_rate / 1000).toFixed(1)} kHz`;
  $('sourceAudio').src = session.audio_url;
  $('waveformEmpty').hidden = false; $('waveformEmpty').querySelector('span').textContent = '读取音频中';
  renderSessions(); renderMarkers(); renderAnalysis();
  try {
    const buffer = await decode(session.audio_url);
    if (selection !== state.selection) return;
    state.sourceBuffer = buffer; $('waveformEmpty').hidden = true; drawWave($('sourceWaveform'), buffer);
  } catch (failure) { if (selection === state.selection) { $('waveformEmpty').querySelector('span').textContent = '音频读取失败'; error(failure.message); } }
}
async function saveAudio(blob, source, name, markers = [], filename = 'source.wav') {
  if (blob.size > 12 * 1024 * 1024) throw new Error('音频文件超过 12 MiB，请缩短录音后重试。');
  const form = new FormData(); form.append('file', blob, filename); form.append('source', source); form.append('name', name); form.append('markers', JSON.stringify(markers));
  const session = await api('/api/sessions', { method: 'POST', body: form });
  state.sessions.unshift(session); await selectSession(session.id); $('uploadState').textContent = '素材已保存';
}
async function importAudio(file, source = 'upload', name = file.name) {
  state.busy = true; updateControls(); clearError(); $('uploadState').textContent = '处理音频中…';
  try {
    if (file.size > 12 * 1024 * 1024) throw new Error('文件超过 12 MiB，请选择较短的音频。');
    if (/\.(wav|wave|flac|aiff|aif|aifc|mp3|ogg|oga|opus)$/i.test(file.name || '')) {
      await saveAudio(file, source, name.replace(/\.[^.]+$/, ''), [], file.name);
      return;
    }
    const buffer = await decode(await file.arrayBuffer());
    if (buffer.duration < 2 || buffer.duration > 30.05) throw new Error('请选择 2–30 秒的音频。');
    const channels = Array.from({ length: Math.min(2, buffer.numberOfChannels) }, (_, i) => buffer.getChannelData(i));
    await saveAudio(wavBlob(channels, buffer.sampleRate), source, name.replace(/\.[^.]+$/, ''));
  } catch (failure) { $('uploadState').textContent = '导入失败'; error(failure.message || '无法解码音频，请尝试 WAV、FLAC 或 AIFF 文件。'); }
  finally { state.busy = false; $('fileInput').value = ''; updateControls(); }
}
async function startRecording() {
  if (state.recording) { await stopRecording(); return; }
  clearError(); $('recordButton').disabled = true;
  let stream;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('麦克风不可用，请从 localhost 打开页面并允许录音权限。');
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    const context = audioContext(); await context.resume();
    const source = context.createMediaStreamSource(stream);
    // A short mono capture keeps PCM data independent of browser recording codecs.
    const processor = context.createScriptProcessor(4096, 1, 1), silent = context.createGain(); silent.gain.value = 0;
    const recording = { stream, source, processor, silent, chunks: [], frames: 0, sampleRate: context.sampleRate, markers: [], started: performance.now(), timer: null };
    state.recording = recording;
    processor.onaudioprocess = (event) => {
      if (state.recording !== recording) return;
      const remaining = Math.round(30 * recording.sampleRate) - recording.frames;
      const chunk = event.inputBuffer.getChannelData(0).slice(0, Math.max(0, remaining));
      recording.chunks.push(chunk); recording.frames += chunk.length;
      if (recording.frames >= 30 * recording.sampleRate) stopRecording();
    };
    source.connect(processor); processor.connect(silent); silent.connect(context.destination);
    recording.timer = setInterval(() => { $('recordingTime').textContent = time(recording.frames / recording.sampleRate); }, 150);
    $('recordButton').innerHTML = '<i data-lucide="square" aria-hidden="true"></i><span>结束录音</span>';
    $('recordingState').hidden = false; $('recordingMarkers').textContent = '0 个标记'; $('recordingTime').textContent = '0:00'; $('uploadState').textContent = '录音中'; icons();
  } catch (failure) { stream?.getTracks().forEach((track) => track.stop()); error(failure.message || '录音失败，请检查麦克风权限。'); }
  finally { updateControls(); }
}
async function stopRecording() {
  const recording = state.recording;
  if (!recording) return;
  state.recording = null; state.busy = true;
  clearInterval(recording.timer); recording.processor.onaudioprocess = null;
  recording.source.disconnect(); recording.processor.disconnect(); recording.silent.disconnect(); recording.stream.getTracks().forEach((track) => track.stop());
  $('recordingState').hidden = true; $('recordButton').innerHTML = '<i data-lucide="mic" aria-hidden="true"></i><span>麦克风录音</span>'; icons(); updateControls();
  try {
    if (recording.frames < recording.sampleRate * 2) throw new Error('录音不足 2 秒，请重新录制。');
    const samples = new Float32Array(recording.frames); let offset = 0;
    for (const chunk of recording.chunks) { samples.set(chunk, offset); offset += chunk.length; }
    $('uploadState').textContent = '保存录音中…';
    await saveAudio(wavBlob([samples], recording.sampleRate), 'microphone', `录音 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`, recording.markers);
  } catch (failure) { $('uploadState').textContent = '录音未保存'; error(failure.message); }
  finally { state.busy = false; updateControls(); }
}
async function analyze() {
  const session = state.session;
  if (!session || state.analyzing) return;
  state.analyzing = true; updateControls(); clearError(); $('analysisStatus').textContent = '分析中…'; $('analyzeButton').querySelector('span').textContent = '分析中…';
  try {
    const analysis = await api(`/api/sessions/${encodeURIComponent(session.id)}/analyze`, jsonOptions({ use_gemini: $('useGemini').checked, style: $('styleInput').value.trim(), pitch_provider: $('pitchProvider').value, genre: $('genreInput').value }));
    session.analysis = analysis;
    if (analysis.gemini_connection && state.status?.gemini) state.status.gemini.connection = analysis.gemini_connection;
    renderGeminiStatus();
    if (state.session?.id === session.id) { renderAnalysis(); if (analysis.gemini_used && analysis.style_prompt) { $('styleInput').value = analysis.style_prompt; $('recommendationPanel').hidden=false;$('recommendationText').textContent=analysis.style_prompt; } }
    if (analysis.gemini_attempted && !analysis.gemini_used) error(`音高分析已完成，但所选 AI 模型调用未完成。${analysis.gemini_error || ''}`);
  } catch (failure) { error(failure.message); if (state.session?.id === session.id) $('analysisStatus').textContent = '分析失败'; }
  finally { state.analyzing = false; $('analyzeButton').querySelector('span').textContent = '分析声音'; updateControls(); }
}
function currentPosition() { return state.playing ? Math.max(0, Math.min(state.duration, state.offset + audioContext().currentTime - state.startedAt)) : state.offset; }
function updateTransport() {
  const position = currentPosition(); $('currentTime').textContent = time(position); $('seekInput').value = position;
  for (const head of document.querySelectorAll('.playhead')) head.style.left = `${state.duration ? position / state.duration * 100 : 0}%`;
  for (const wave of document.querySelectorAll('[data-wave]')) wave.setAttribute('aria-valuenow', position.toFixed(2));
}
function stopPlayback(reset = false) {
  state.playToken++;
  if (state.playing) state.offset = currentPosition(); state.playing = false;
  for (const node of state.nodes) { try { node.source.stop(); } catch (_) { /* Already ended. */ } node.source.disconnect(); node.gain.disconnect(); }
  state.nodes = []; cancelAnimationFrame(state.frame);
  if (reset) state.offset = 0;
  $('playButton').innerHTML = '<i data-lucide="play" aria-hidden="true"></i>'; $('playButton').title = '播放'; $('playButton').setAttribute('aria-label', '播放'); icons(); updateTransport();
}
function effectiveGain(track) { const solo = state.tracks.some((item) => item.solo); return track.mute || (solo && !track.solo) ? 0 : track.gain; }
function updateGains() {
  for (const track of state.tracks) {
    const node = state.nodes.find((item) => item.id === track.id);
    if (node) node.gain.gain.setTargetAtTime(effectiveGain(track), audioContext().currentTime, 0.015);
    const row = $(`track-${track.id}`); row?.classList.toggle('is-muted', effectiveGain(track) === 0);
  }
}
async function play() {
  if (!state.tracks.length) return;
  if (state.playing) { stopPlayback(); return; }
  const token = ++state.playToken;
  try {
    $('sourceAudio').pause(); const context = audioContext(); await context.resume();
    if (token !== state.playToken) return;
    if (state.offset >= state.duration - 0.01) state.offset = 0;
    state.startedAt = context.currentTime + 0.035;
    for (const track of state.tracks) {
      if (state.offset >= track.buffer.duration) continue;
      const source = context.createBufferSource(), gain = context.createGain(); source.buffer = track.buffer; gain.gain.value = effectiveGain(track);
      source.connect(gain); gain.connect(state.master); source.start(state.startedAt, state.offset);
      state.nodes.push({ id: track.id, source, gain });
    }
    state.playing = true; $('playButton').innerHTML = '<i data-lucide="pause" aria-hidden="true"></i>'; $('playButton').title = '暂停'; $('playButton').setAttribute('aria-label', '暂停'); icons();
    const tick = () => { updateTransport(); if (currentPosition() >= state.duration) { stopPlayback(true); return; } state.frame = requestAnimationFrame(tick); }; tick();
  } catch (failure) { stopPlayback(); error(failure.message || '播放失败，请重试。'); }
}
async function seek(position) { const playing = state.playing; stopPlayback(); state.offset = Math.max(0, Math.min(state.duration, position)); updateTransport(); if (playing) await play(); }
function renderTracks() {
  $('trackCount').textContent = state.tracks.length;
  $('trackList').innerHTML = state.tracks.map((track) => `<div class="track-row" id="track-${escapeHtml(track.id)}"><div class="track-control"><div class="track-title"><i data-lucide="${track.kind === 'recorded' ? 'mic' : 'music-2'}" aria-hidden="true"></i><strong title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</strong><span class="tag">${track.kind === 'recorded' ? '原声' : track.kind === 'local_synthesis' ? '合成' : 'AI 生成'}</span></div><div class="track-tools"><button class="track-toggle" data-track="${escapeHtml(track.id)}" data-action="mute" aria-pressed="false" title="静音 ${escapeHtml(track.name)}" aria-label="静音 ${escapeHtml(track.name)}"><i data-lucide="volume-x" aria-hidden="true"></i></button><button class="track-toggle" data-track="${escapeHtml(track.id)}" data-action="solo" aria-pressed="false" title="独奏 ${escapeHtml(track.name)}" aria-label="独奏 ${escapeHtml(track.name)}"><i data-lucide="headphones" aria-hidden="true"></i></button><input type="range" min="0" max="1.5" step="0.01" value="${track.gain}" data-gain="${escapeHtml(track.id)}" aria-label="${escapeHtml(track.name)} 音量"><output id="gain-${escapeHtml(track.id)}">${Math.round(track.gain * 100)}%</output></div></div><div class="track-waveform" data-wave="${escapeHtml(track.id)}" role="slider" tabindex="0" aria-label="${escapeHtml(track.name)} 播放位置" aria-valuemin="0" aria-valuemax="${state.duration}" aria-valuenow="0"><canvas id="wave-${escapeHtml(track.id)}" aria-label="${escapeHtml(track.name)} 波形"></canvas><span class="playhead"></span></div></div>`).join('');
  state.tracks.forEach(track => {
    if (['mma_accompaniment', 'fluidsynth_melody'].includes(track.kind)) {
      $(`track-${track.id}`).querySelector('.tag').textContent = track.kind === 'mma_accompaniment' ? '规则伴奏' : '旋律合成';
    }
  });
  if (!state.tracks.length) $('trackList').innerHTML = '<div class="editor-empty"><i data-lucide="audio-lines" aria-hidden="true"></i><span>暂无生成轨道</span></div>';
  $('totalTime').textContent = time(state.duration); $('seekInput').max = state.duration; icons(); drawAll(); updateGains(); updateControls(); updateTransport();
}
function setDownloadLink(id, url) { const link = $(id); link.classList.toggle('disabled', !url); link.setAttribute('aria-disabled', String(!url)); if (url) { link.href = url; link.setAttribute('download', ''); } else link.removeAttribute('href'); }
async function loadResult(job) {
  $('separateButton').hidden = job.provider !== 'yue2';
  $('liveEditorLink').hidden = true;
  $('separationStatus').textContent = '';
  if (job.provider === 'yue2') void refreshSeparation(job.id);
  const selection = ++state.resultSelection; stopPlayback(true); state.tracks = []; state.duration = 0; state.job = job; renderTracks();
  $('resultProvider').hidden = false; $('resultProvider').textContent = providerLabels[job.provider] || job.provider;
  $('resultState').textContent = '读取生成音频中…'; setDownloadLink('resultAbc', job.abc_url); setDownloadLink('resultProject', job.project_url);
  for (const [id, file] of [['mmaMidi', 'accompaniment.mid'], ['mmaMelody', 'melody.mid']]) {
    $(id).hidden = job.provider !== 'mma';
    if (job.provider === 'mma') setDownloadLink(id, `/results/${job.id}/${file}`);
  }
  try {
    const tracks = job.tracks?.length ? job.tracks : (job.audio_url ? [{ id: 'mix', name: '生成混音', url: job.audio_url, gain: 1, kind: 'generated' }] : []);
    const decoded = await Promise.all(tracks.map(async (track, index) => ({ ...track, id: `t${index}`, originalId: track.id, buffer: await decode(track.url), gain: track.kind === 'recorded' && Number(track.gain) === 0 ? 1 : Number.isFinite(Number(track.gain)) ? Number(track.gain) : 1, mute: track.kind === 'recorded', solo: false })));
    if (selection !== state.resultSelection) return;
    if (!decoded.length) throw new Error('作品已完成，但未返回可播放的音轨。');
    const peakScan = decoded.map(track => Array.from({ length: track.buffer.numberOfChannels }, (_, ch) => track.buffer.getChannelData(ch)));
    let mixPeak = 0, stemPeak = 0;
    for (let ch = 0; ch < 2; ch++) {
      const channels = peakScan.map((parts, i) => ({ data: parts[Math.min(ch, parts.length - 1)], gain: decoded[i].gain }));
      const length = Math.max(...channels.map(part => part.data.length));
      for (let i = 0; i < length; i++) {
        let sum = 0;
        for (const part of channels) { const value = (part.data[i] || 0) * part.gain; sum += value; stemPeak = Math.max(stemPeak, Math.abs(value)); }
        mixPeak = Math.max(mixPeak, Math.abs(sum));
      }
    }
    const boost = mixPeak > 0.00001 ? Math.min(4, Math.pow(10, -1 / 20) / Math.max(mixPeak, stemPeak)) : 1;
    for (const track of decoded) for (let ch = 0; ch < track.buffer.numberOfChannels; ch++) {
      const data = track.buffer.getChannelData(ch);
      for (let i = 0; i < data.length; i++) data[i] *= boost;
    }
    state.tracks = decoded; state.duration = Math.max(...decoded.map((track) => track.buffer.duration)); renderTracks();
    $('resultState').textContent = `${providerLabels[job.provider] || job.provider} · ${state.duration.toFixed(1)} 秒 · ${decoded.length} 条真实音轨`;
  } catch (failure) { if (selection === state.resultSelection) { $('resultState').textContent = '音轨读取失败'; error(failure.message); } }
}
let separationTimer;
async function refreshSeparation(id) {
  clearTimeout(separationTimer);
  try {
    const info = await api(`/api/jobs/${id}/separate`);
    if (state.job?.id !== id) return;
    const busy = ['queued', 'processing'].includes(info.status);
    $('separateButton').disabled = busy;
    $('separationStatus').textContent = info.error || info.message || (info.status === 'completed' ? '六轨已就绪' : '');
    $('liveEditorLink').hidden = info.status !== 'completed';
    if (info.status === 'completed') $('liveEditorLink').href = info.editor_url;
    if (busy) separationTimer = setTimeout(() => refreshSeparation(id), 2000);
  } catch (failure) { if (state.job?.id === id) $('separationStatus').textContent = failure.message; }
}
$('separateButton').addEventListener('click', async () => {
  const job = state.job;
  if (!job || job.provider !== 'yue2') return;
  $('separateButton').disabled = true;
  try { await api(`/api/jobs/${job.id}/separate`, jsonOptions({})); await refreshSeparation(job.id); }
  catch (failure) { error(failure.message); $('separateButton').disabled = false; }
});
function renderJobs() {
  const selected = state.job?.id || $('jobSelect').value;
  const completed = state.jobs.filter((job) => job.status === 'completed' || job.status === 'succeeded');
  $('jobSelect').innerHTML = completed.length ? completed.map((job) => `<option value="${escapeHtml(job.id)}">${escapeHtml(providerLabels[job.provider] || job.provider)} · ${escapeHtml(job.id.slice(0, 8))}</option>`).join('') : '<option value="">暂无作品</option>';
  if (completed.some((job) => job.id === selected)) $('jobSelect').value = selected;
}
function renderJobStatus(job) {
  $('jobStage').textContent = job.error ? `生成失败：${job.error}` : (job.message || job.stage || job.status);
  $('jobProvider').textContent = providerLabels[job.provider] || job.provider;
  $('jobProgress').hidden = false;
  if (typeof job.progress === 'number' && Number.isFinite(job.progress)) $('jobProgress').value = Math.max(0, Math.min(100, job.progress));
  else $('jobProgress').removeAttribute('value');
}
async function pollJob(id) {
  clearTimeout(state.pollTimer);
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`); renderJobStatus(job);
    const index = state.jobs.findIndex((item) => item.id === id); if (index >= 0) state.jobs[index] = job; else state.jobs.unshift(job);
    if (job.status === 'completed' || job.status === 'succeeded') {
      state.generating = false; renderJobs(); $('jobSelect').value = job.id; await loadResult(job); updateControls(); return;
    }
    if (['failed', 'error', 'cancelled'].includes(job.status)) { state.generating = false; error(job.error || '生成失败，请检查生成器配置后重试。'); updateControls(); return; }
    state.pollTimer = setTimeout(() => pollJob(id), 1500);
  } catch (failure) {
    $('jobStage').textContent = '状态连接中断，正在重试'; error(failure.message); state.pollTimer = setTimeout(() => pollJob(id), 4000);
  }
}
async function generate() {
  if (!state.session?.analysis || state.generating) return;
  state.generating = true; updateControls(); clearError(); $('jobStage').textContent = '提交生成任务…';
  try {
    const job = await api('/api/jobs', jsonOptions({ session_id: state.session.id, provider: $('providerInput').value, style: $('styleInput').value.trim(), genre: $('genreInput').value, duration: Number($('durationInput').value), keep_melody: $('keepMelody').checked, source_gain: Number($('sourceGain').value), abc: $('abcEditor').value, mma_bpm: Number($('mmaBpm').value), mma_groove: $('mmaGroove').value, mma_chords: $('mmaChords').value.trim() }));
    await pollJob(job.id);
  } catch (failure) { state.generating = false; $('jobStage').textContent = '提交失败'; error(failure.message); updateControls(); }
}
async function exportMix() {
  if (!state.tracks.length) return;
  $('downloadWav').disabled = true;
  try {
    const rate = 44100, context = new OfflineAudioContext(2, Math.ceil(state.duration * rate), rate);
    for (const track of state.tracks) { const source = context.createBufferSource(), gain = context.createGain(); source.buffer = track.buffer; gain.gain.value = effectiveGain(track); source.connect(gain); gain.connect(context.destination); source.start(0); }
    const buffer = await context.startRendering();
    let peak = 0;
    for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
      for (const value of buffer.getChannelData(ch)) peak = Math.max(peak, Math.abs(value));
    }
    const scale = Math.min(1, Math.pow(10, -1 / 20) / (peak || 1));
    for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
      const data = buffer.getChannelData(ch);
      for (let i = 0; i < data.length; i++) data[i] *= scale;
    }
    downloadBlob(wavBlob([buffer.getChannelData(0), buffer.getChannelData(1)], rate), `SONIC-${state.job?.id.slice(0, 8) || 'mix'}.wav`);
  } catch (failure) { error(failure.message || '混音导出失败。'); }
  finally { updateControls(); }
}
async function refresh() {
  $('connectionStatus').textContent = '连接中';
  const results = await Promise.allSettled([api('/api/status'), api('/api/sessions'), api('/api/jobs')]);
  if (results[0].status === 'fulfilled') {
    state.status = results[0].value;
    $('hardwareStatus').textContent = state.status.hardware === 'connected' ? '设备已连接' : '设备未连接';
    renderGeminiStatus();
    renderRemoteStatus();
    $('connectionStatus').textContent = '本地服务已连接';
  } else { $('connectionStatus').textContent = '本地服务连接失败'; error(results[0].reason.message); }
  if (results[1].status === 'fulfilled') {
    state.sessions = results[1].value.sessions;
    if (state.session) state.session = state.sessions.find((item) => item.id === state.session.id) || state.session;
    renderSessions();
    if (!state.session && state.sessions.length) await selectSession(state.sessions[0].id);
  } else error(results[1].reason.message);
  if (results[2].status === 'fulfilled') {
    state.jobs = results[2].value.jobs; renderJobs();
    const active = state.jobs.find((job) => ['pending', 'queued', 'running', 'processing'].includes(job.status));
    if (active && !state.generating) { state.generating = true; pollJob(active.id); }
    else if (!state.job && !state.generating) { const completed = state.jobs.find((job) => ['completed', 'succeeded'].includes(job.status)); if (completed) await loadResult(completed); }
  } else error(results[2].reason.message);
  updateControls();
}

$('dismissError').addEventListener('click', clearError);
$('uploadButton').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', () => { if ($('fileInput').files[0]) importAudio($('fileInput').files[0]); });
$('sampleButton').addEventListener('click', async () => {
  state.busy = true; updateControls(); clearError(); $('uploadState').textContent = '读取测试旋律…';
  try { const response = await fetch('/api/sample'); if (!response.ok) throw new Error('测试旋律读取失败'); await importAudio(await response.blob(), 'sample', '测试旋律'); }
  catch (failure) { state.busy = false; $('uploadState').textContent = '载入失败'; error(failure.message); updateControls(); }
});
$('recordButton').addEventListener('click', startRecording);
$('recordMarker').addEventListener('click', () => { if (!state.recording) return; state.recording.markers.push(state.recording.frames / state.recording.sampleRate); $('recordingMarkers').textContent = `${state.recording.markers.length} 个标记`; });
$('refreshButton').addEventListener('click', refresh);
$('sessionList').addEventListener('click', (event) => { const button = event.target.closest('[data-session]'); if (button && !state.analyzing && !state.generating) selectSession(button.dataset.session); });
$('sourceAudio').addEventListener('timeupdate', () => drawWave($('sourceWaveform'), state.sourceBuffer));
$('sourceAudio').addEventListener('play', () => stopPlayback());
$('sourceWaveform').addEventListener('click', (event) => { if (state.sourceBuffer) $('sourceAudio').currentTime = event.offsetX / event.currentTarget.clientWidth * state.sourceBuffer.duration; });
$('markerList').addEventListener('click', (event) => { const button = event.target.closest('[data-marker]'); if (button) $('sourceAudio').currentTime = state.session.markers[Number(button.dataset.marker)]; });
$('addMarker').addEventListener('click', async () => {
  if (!state.session) return;
  const session = state.session, markers = [...(session.markers || []), Number($('sourceAudio').currentTime.toFixed(3))].sort((a, b) => a - b);
  $('addMarker').disabled = true;
  try { const updated = await api(`/api/sessions/${encodeURIComponent(session.id)}`, jsonOptions({ markers }, 'PATCH')); Object.assign(session, updated); renderMarkers(); renderAnalysis(); }
  catch (failure) { error(failure.message); } finally { updateControls(); }
});
$('analyzeButton').addEventListener('click', analyze);
$('pitchProvider').addEventListener('change', () => { renderRemoteStatus(); updateControls(); });
$('providerInput').addEventListener('change', renderRemoteStatus);
$('probeRemote').addEventListener('click', async () => {
  state.probingRemote = true; updateControls(); clearError(); $('remoteStatus').textContent = '正在检查服务器与模型…';
  try {
    const remote = await api('/api/remote/probe', jsonOptions({}));
    state.status = { ...state.status, yue2: remote }; renderRemoteStatus();
    if (!remote.available) error(remote.message || '服务器暂不可用。');
  } catch (failure) { $('remoteStatus').textContent = '连接失败'; error(failure.message); }
  finally { state.probingRemote = false; updateControls(); }
});
for (const name of ['notes', 'abc']) $(name + 'Tab').addEventListener('click', () => { for (const tab of ['notes', 'abc']) { $(tab + 'Tab').setAttribute('aria-selected', String(tab === name)); $(tab + 'Panel').hidden = tab !== name; } if (name === 'notes') drawNotes(); });
$('downloadAbc').addEventListener('click', () => downloadBlob(new Blob([$('abcEditor').value], { type: 'text/vnd.abc;charset=utf-8' }), 'SONIC-melody.abc'));
$('sourceGain').addEventListener('input', () => { $('sourceGainValue').textContent = `${Math.round(Number($('sourceGain').value) * 100)}%`; });
$('generateButton').addEventListener('click', generate);
$('playButton').addEventListener('click', play);
$('rewindButton').addEventListener('click', () => seek(0));
$('seekInput').addEventListener('input', () => seek(Number($('seekInput').value)));
$('masterVolume').addEventListener('input', () => { if (state.master) state.master.gain.setTargetAtTime(Number($('masterVolume').value), audioContext().currentTime, 0.015); });
$('trackList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-action]');
  if (button) { const track = state.tracks.find((item) => item.id === button.dataset.track); track[button.dataset.action] = !track[button.dataset.action]; button.setAttribute('aria-pressed', String(track[button.dataset.action])); updateGains(); return; }
  const wave = event.target.closest('[data-wave]'); if (wave) { const rect = wave.getBoundingClientRect(); seek((event.clientX - rect.left) / rect.width * state.duration); }
});
$('trackList').addEventListener('keydown', (event) => { const wave = event.target.closest('[data-wave]'); if (!wave) return; const offsets = { ArrowLeft: -1, ArrowRight: 1 }; if (event.key in offsets) { event.preventDefault(); seek(currentPosition() + offsets[event.key]); } if (event.key === 'Home') { event.preventDefault(); seek(0); } if (event.key === 'End') { event.preventDefault(); seek(state.duration); } });
$('trackList').addEventListener('input', (event) => { if (!event.target.dataset.gain) return; const track = state.tracks.find((item) => item.id === event.target.dataset.gain); track.gain = Number(event.target.value); $(`gain-${track.id}`).textContent = `${Math.round(track.gain * 100)}%`; updateGains(); });
$('jobSelect').addEventListener('change', async () => { try { const job = await api(`/api/jobs/${encodeURIComponent($('jobSelect').value)}`); renderJobStatus(job); await loadResult(job); } catch (failure) { error(failure.message); } });
$('downloadWav').addEventListener('click', exportMix);
window.addEventListener('beforeunload', () => { state.recording?.stream.getTracks().forEach((track) => track.stop()); });
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawAll, 100); });
icons(); updateControls(); drawAll(); refresh();
async function refreshModelStatus() {
  try { state.status = await api('/api/status'); renderGeminiStatus(); renderRemoteStatus(); updateControls(); }
  catch (failure) { error(failure.message); }
}
window.addEventListener('focus', refreshModelStatus);
window.addEventListener('storage', (event) => { if (event.key === 'sonic-model-change') refreshModelStatus(); });
