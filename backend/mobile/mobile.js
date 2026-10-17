const $=id=>document.getElementById(id),root=$('app');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=n=>`<i data-lucide="${n}" aria-hidden="true"></i>`;
const state={view:'library',sessions:[],jobs:[],selected:new Set(),current:null,busy:false};
let player=window.SonicPreview.create(updateSourcePlayback),pollTimer;
let preparedMelody='',promptEdited=false,resultVersion=0,authEpoch=0;
const selectedGenre=()=>document.querySelector('input[name="genre"]:checked').value;
const time=s=>`${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
function notice(text){$('notice').textContent=text;$('notice').style.display='block';clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('notice').style.display='none',7000);}
const request=(path,options)=>window.SonicTransport.fetch(path,options);
async function api(path,options){const epoch=authEpoch;const r=await request(path,options);if(epoch!==authEpoch)throw Error('账号已切换，请重新操作。');if(!r.ok){if(r.status===401&&state.accountsRequired)window.dispatchEvent(new Event('sonic-auth-expired'));let j;try{j=await r.json();}catch{}throw Error(r.status===401?'连接已失效，请点顶部“重新连接”。':j?.detail||`服务错误 ${r.status}`);}const data=await r.json();if(epoch!==authEpoch)throw Error('账号已切换，请重新操作。');return data;}
function renameName(title,value){
 const dialog=$('renameDialog'),form=$('renameForm'),input=$('renameInput');
 if(dialog.open)return Promise.resolve(null);
 const previous=document.activeElement;$('renameTitle').textContent=title;input.value=value;
 return new Promise(resolve=>{
  let result=null;
  const finish=()=>{form.onsubmit=null;$('renameCancel').onclick=null;dialog.removeEventListener('close',finish);if(previous?.isConnected)previous.focus();resolve(result);};
  form.onsubmit=e=>{e.preventDefault();if(e.isComposing)return;const name=input.value.trim();if(!name){input.focus();return;}result=name;dialog.close();};
  $('renameCancel').onclick=()=>dialog.close();dialog.addEventListener('close',finish,{once:true});
  dialog.showModal();window.lucide?.createIcons();input.focus();input.select();
 });
}
function updateSourcePlayback(active){
 let changed=false;
 root.querySelectorAll('[data-preview]').forEach(button=>{
  const selected=button.dataset.preview===active.id,mode=selected&&active.loading?'loading':selected&&active.playing?'playing':'paused';
  if(button.dataset.mode!==mode){button.dataset.mode=mode;button.innerHTML=icon(mode==='loading'?'loader-circle':mode==='playing'?'pause':'play');button.setAttribute('aria-label',mode==='playing'?'暂停原声':mode==='loading'?'取消加载':'播放原声');changed=true;}
 });
 if(changed)window.lucide?.createIcons();
 const seek=root.querySelector(`[data-source-seek="${active.id}"]`),label=root.querySelector(`[data-source-time="${active.id}"]`);
 if(seek){seek.max=active.duration||seek.max;seek.value=active.currentTime;}
 if(label)label.textContent=active.loading?'加载原声…':`${time(active.currentTime)} / ${time(active.duration)}`;
}
window.SonicAuthUserId=()=>JSON.parse(localStorage.getItem('sonic-account')||'null')?.id||'local';
const post=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
async function refresh(){const epoch=authEpoch;const [a,b]=await Promise.all([api('/api/sessions'),api('/api/jobs')]);if(epoch!==authEpoch)return;state.sessions=a.sessions;state.jobs=b.jobs;render();}
function go(view){if(state.accountsRequired&&!state.authReady)view='account';if(state.busy)return;++resultVersion;clearTimeout(pollTimer);root.querySelectorAll('audio').forEach(audio=>audio.pause());if(view!=='library')player.pause();state.view=view;render();}
function render(){
 document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===state.view));
 if(state.view==='library')library();else if(state.view==='works')works();else if(state.view==='hardware')window.SonicIntegrated?.hardware();else if(state.view==='account')window.SonicIntegrated?.account();else if(state.view==='generating')progress('准备生成…');
 window.lucide?.createIcons();
}
function library(){
 root.innerHTML=`<div class="title-row"><div><h1>我的声音</h1><p class="subtitle">${state.sessions.length} 条声音 · 最多组合 4 条</p></div></div><div class="library-actions"><button id="sample">${icon('music-2')}添加测试旋律</button><button id="uploadInline">${icon('plus')}上传音频</button><button id="deleteSelected" title="删除选中" aria-label="删除选中">${icon('trash-2')}</button></div><div id="sounds">${state.sessions.map(s=>`<article class="sound ${state.selected.has(s.id)?'selected':''}"><input type="checkbox" data-id="${s.id}" ${state.selected.has(s.id)?'checked':''} aria-label="选择 ${esc(s.name)}"><div><strong data-rename="${s.id}" title="双击重命名">${esc(s.name)}</strong><div class="meta">${s.source==='soundcore'?'录音豆':s.source==='sample'?'测试素材':s.source==='microphone'?'麦克风':'上传'} · ${time(s.duration)}${s.markers?.length?` · ${s.markers.length}个重点`:''}${s.analysis?.gemini_used?' · 已分析':''}</div><canvas data-peaks="${s.id}" width="280" height="30" aria-label="录音波形"></canvas></div><button class="preview" data-preview="${s.id}" aria-label="试听 ${esc(s.name)}">${icon('play')}</button></article>`).join('')||`<div class="empty">${icon('audio-lines')}<p>还没有声音素材</p><p>导入生活声音，或哼一段旋律。</p></div>`}</div>${state.selected.size?`<div class="selection"><span>已选 ${state.selected.size} 条</span><button id="clear">取消</button><button id="create" class="primary">开始创作 ${icon('sparkles')}</button></div>`:''}`;
 $('uploadInline').onclick=()=>$('files').click();$('sample').onclick=async()=>{try{const r=await request('/api/sample');await upload(new File([await r.blob()],'测试旋律.wav'), 'sample');}catch(e){notice(e.message);}};
 const connect=document.createElement('button');connect.className='connect-recorder';connect.innerHTML=icon('bluetooth')+'<span>录音豆<small>连接设备，收集此刻的声音</small></span>'+icon('chevron-right');connect.onclick=()=>go('hardware');root.querySelector('.library-actions').before(connect);
 root.querySelectorAll('[data-id]').forEach(el=>el.onchange=()=>{if(el.checked&&state.selected.size>=4){el.checked=false;notice('最多选择 4 条声音');return;}el.checked?state.selected.add(el.dataset.id):state.selected.delete(el.dataset.id);render();});
 root.querySelectorAll('[data-rename]').forEach(el=>el.ondblclick=async()=>{const name=await renameName('修改声音名称',el.textContent.trim());if(name?.trim()){try{await api(`/api/sessions/${el.dataset.rename}/name`,{...post({name:name.trim()}),method:'PATCH'});await refresh();}catch(error){notice(error.message);}}});
 root.querySelectorAll('[data-preview]').forEach(el=>{
  const s=state.sessions.find(x=>x.id===el.dataset.preview),controls=document.createElement('div');controls.className='source-transport';
  if(s.source==='soundcore')el.previousElementSibling.querySelector('.meta').textContent=`录音豆 · ${time(s.duration)} · ${s.markers?.length||0} 个重点`;
  controls.innerHTML=`<input type="range" data-source-seek="${s.id}" min="0" max="${s.duration}" step="0.05" value="0" aria-label="${esc(s.name)}播放位置"><small data-source-time="${s.id}">0:00 / ${time(s.duration)}</small>`;
  el.previousElementSibling.append(controls);
  if(s.markers?.length){const markers=document.createElement('div');markers.className='source-marks';markers.innerHTML=s.markers.map((at,index)=>`<button data-marker="${at}" aria-label="从重点${index+1}开始播放">${icon('flag')}${time(at)}</button>`).join('');markers.querySelectorAll('[data-marker]').forEach(button=>button.onclick=async()=>{try{if(player.id!==s.id)await player.play(s.id,s.audio_url);player.seek(Number(button.dataset.marker));if(!player.playing)await player.play(s.id,s.audio_url);}catch(error){notice(error.message);}});el.previousElementSibling.append(markers);}
  const cover=document.createElement('img');cover.className='source-cover';cover.alt='';cover.src='/mobile/assets/'+(s.source==='soundcore'?'5-10070_imgSoundImage2.png':s.source==='sample'?'5-10070_imgSoundImage1.png':'5-10070_imgSoundImage.png');el.parentElement.insertBefore(cover,el.previousElementSibling);
  controls.querySelector('input').oninput=e=>{if(player.id===s.id)player.seek(e.target.value);};
  el.onclick=()=>player.play(s.id,s.audio_url).catch(e=>notice(e.message||'原声播放失败，请重新连接后重试。'));
 });
 updateSourcePlayback(player);
 if($('clear'))$('clear').onclick=()=>{state.selected.clear();render();};if($('create'))$('create').onclick=openCreate;
 $('deleteSelected').onclick=async()=>{const ids=[...state.selected];if(!ids.length)return notice('先选择要删除的音频');if(!confirm(`删除选中的 ${ids.length} 条素材？`))return;await api('/api/sessions/bulk-delete',post({ids}));state.selected.clear();await refresh();};
 for(const canvas of root.querySelectorAll('[data-peaks]'))api(`/api/sessions/${canvas.dataset.peaks}/peaks`).then(({peaks})=>{if(!canvas.isConnected)return;const ctx=canvas.getContext('2d'),max=Math.max(.02,...peaks);ctx.fillStyle='#a38cbd';peaks.forEach((p,i)=>{const h=Math.max(1,p/max*28);ctx.fillRect(i/peaks.length*280,15-h/2,Math.max(1,280/peaks.length-1),h);});}).catch(()=>{});
}
async function upload(file,source='upload'){
 if(state.busy)return;const epoch=authEpoch;state.busy=true;notice('正在导入 '+file.name);
 try{
  const form=new FormData();form.append('file',file);form.append('source',source);form.append('name',file.name);
  let response=await request('/api/sessions',{method:'POST',body:form});
  if(epoch!==authEpoch)throw Error('账号已切换，请重新操作。');
  if(!response.ok&&response.status!==401&&response.status!==403){
   const ctx=new AudioContext();try{const buf=await ctx.decodeAudioData(await file.arrayBuffer());if(epoch!==authEpoch)throw Error('账号已切换，请重新操作。');const converted=AudioEncode.wav(buf);const f=new FormData();f.append('file',converted,'converted.wav');f.append('source',source);f.append('name',file.name);response=await request('/api/sessions',{method:'POST',body:f});}finally{await ctx.close();}
  }
  const data=await response.json();if(epoch!==authEpoch)throw Error('账号已切换，请重新操作。');if(!response.ok)throw Error(data.detail||'无法读取音频');
  if(state.selected.size<4)state.selected.add(data.id);await refresh();
 }finally{if(epoch===authEpoch)state.busy=false;}
}
$('files').onchange=async e=>{for(const file of e.target.files){try{await upload(file);}catch(error){notice(error.message);}}e.target.value='';};
function openCreate(){
 player.pause();
 if(!state.selected.size){notice('先选择声音素材');return;}
 $('selectionNames').textContent=state.sessions.filter(s=>state.selected.has(s.id)).map(s=>s.name).join('、');
 $('melody').innerHTML='<option value="">无哼唱 · AI 创作旋律</option>'+state.sessions.filter(s=>state.selected.has(s.id)).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
 preparedMelody='';$('mobileAbc').value='';$('abcPanel').hidden=true;
 $('recommendStatus').textContent='';$('style').value='';$('promptOrigin').textContent='可手动填写';promptEdited=false;
 $('createDialog').showModal();
}
$('cancelCreate').onclick=()=>$('createDialog').close();
$('style').oninput=()=>{promptEdited=true;$('promptOrigin').textContent='已编辑';};
$('melody').onchange=()=>{preparedMelody='';$('mobileAbc').value='';$('abcPanel').hidden=true;$('recommendStatus').textContent=$('melody').value?'请分析选中的哼唱以提取 ABC。':'';};
document.querySelectorAll('input[name="genre"]').forEach(el=>el.onchange=()=>{$('recommendStatus').textContent='风格已切换；可重新分析推荐，或保留当前提示词。';});
$('analyzeSources').onclick=async()=>{
 if(state.busy)return;
 if(!$('consent').checked){notice('请先同意使用 AI 分析素材。');return;}
 if(promptEdited&&!confirm('重新分析会替换当前编辑的提示词，是否继续？'))return;
 const ids=[...state.selected],melody=$('melody').value,genre=selectedGenre();
 state.busy=true;$('analyzeSources').disabled=true;$('confirmGenerate').disabled=true;$('cancelCreate').disabled=true;
 document.querySelectorAll('input[name="genre"],#melody').forEach(el=>el.disabled=true);
 try{
  const prompts=[];let score='';
  for(let i=0;i<ids.length;i++){
   $('recommendStatus').textContent=`Gemini 正在分析 ${i+1}/${ids.length} 条声音…`;
   const analysis=await api(`/api/sessions/${ids[i]}/analyze`,post({use_gemini:true,pitch_provider:ids[i]===melody?'pyin':'none',style:'',genre}));
   if(!analysis.gemini_used)throw Error(analysis.gemini_error||'AI 分析未完成');
   const chinese=analysis.style_prompt_zh||(await api('/api/prompts/translate',post({text:analysis.style_prompt,target:'zh'}))).text;
   prompts.push(chinese);const record=state.sessions.find(s=>s.id===ids[i]);if(record)record.analysis=analysis;
   if(ids[i]===melody){if(!analysis.notes?.length)throw Error('没有识别到可靠旋律，请选择无哼唱生成。');score=analysis.abc;}
  }
  $('style').value=prompts.map(p=>p.slice(0,Math.floor(1200/ids.length))).join('; ');
  $('promptOrigin').textContent='Gemini 推荐 · 中文';promptEdited=false;
  preparedMelody=melody;$('mobileAbc').value=score;$('abcPanel').hidden=!score;
  $('recommendStatus').textContent='推荐已就绪，请编辑确认后再生成。';
 }catch(e){$('recommendStatus').textContent=e.message;notice(e.message);}
 finally{state.busy=false;$('analyzeSources').disabled=false;$('confirmGenerate').disabled=false;$('cancelCreate').disabled=false;document.querySelectorAll('input[name="genre"],#melody').forEach(el=>el.disabled=false);}
};
$('createForm').onsubmit=async e=>{
 e.preventDefault();if(state.busy)return;
 const ids=[...state.selected],melody=$('melody').value;let style=$('style').value.trim();const markers=ids.flatMap(id=>(state.sessions.find(item=>item.id===id)?.markers||[]).map((at,index)=>`重点${index+1}（${Number(at).toFixed(2)}秒）`));if(markers.length)style=markers.join('；')+'；'+style;
 if(!style){notice('请先分析获得推荐词，或手动填写提示词。');return;}
 if(melody&&(preparedMelody!==melody||!$('mobileAbc').value.trim())){notice('请先分析选中的哼唱并确认 ABC 乐谱。');return;}
 $('createDialog').close();state.busy=true;state.view='generating';player?.pause();
 try{
  progress('正在提交已确认的提示词与乐谱…');
  const job=await api('/api/compositions',post({session_ids:ids,melody_id:melody||null,final_prompt:style,
   genre:selectedGenre(),abc:melody?$('mobileAbc').value:null,duration:Number($('duration').value),seed:Number($('seed').value)}));
  localStorage.setItem('sonic-mobile-job:'+window.SonicAuthUserId(),job.id);state.current=job;state.busy=false;poll(job.id);
 }catch(error){state.busy=false;notice(error.message);state.view='library';await refresh();}
};
function progress(message,job){root.innerHTML=`<section class="stage generation-stage">${icon('sparkles')}<h1>正在把声音写成音乐</h1><p>AI 正在为你编排旋律与声音</p><p class="wait-tip">生成通常需要2分钟，请耐心等待～</p><progress ${typeof job?.progress==='number'?`value="${job.progress}" max="100"`:''}></progress><p class="note">可以切换页面或去做别的事情，生成会在服务器继续。</p><button id="backLibrary">返回声音库</button></section>`;window.lucide?.createIcons();$('backLibrary').onclick=()=>{state.busy=false;go('library');};}
async function poll(id){
 const epoch=authEpoch;clearTimeout(pollTimer);
 try{const job=await api('/api/jobs/'+id);state.current=job;
  if(job.status==='completed'){localStorage.removeItem('sonic-mobile-job:'+window.SonicAuthUserId());await result(job);return;}
  if(job.status==='failed'){localStorage.removeItem('sonic-mobile-job:'+window.SonicAuthUserId());notice(job.error||'生成失败');go('works');await refresh();return;}
  state.view='generating';progress(job.message||job.stage,job);pollTimer=setTimeout(()=>poll(id),2500);
 }catch(e){if(epoch!==authEpoch||state.accountsRequired&&!state.authReady)return;notice(e.message);pollTimer=setTimeout(()=>poll(id),5000);}
}
function workCover(id){
 let hash=2166136261;for(const char of String(id))hash=Math.imul(hash^char.charCodeAt(0),16777619);
 return '/mobile/assets/work-cover-'+((hash>>>0)%12)+'.png';
}
function works(){
 root.innerHTML=`<div class="title-row"><h1>我的作品</h1><div class="title-tools"><button id="deleteWorks" aria-label="删除选中作品">${icon('trash-2')}</button><button id="reloadWorks" aria-label="刷新">${icon('refresh-cw')}</button></div></div><p class="note">长按作品进入选择；双击名称重命名。</p><div class="works-list">${state.jobs.map(j=>{const cover=workCover(j.id);return `<article class="work ${state.selected.has(j.id)?'selected':''}" data-job="${j.id}"><input aria-label="选择作品 ${esc(j.name||'声音创作')}" type="checkbox" data-work-select="${j.id}" ${state.selected.has(j.id)?'checked':''}><img class="work-cover" src="${cover}" alt=""><span data-job-rename="${j.id}"><strong>${esc(j.name||'声音创作')}</strong><small>${esc(j.status==='completed'?time(j.duration||0):j.message||j.status)}</small></span><button class="work-open" aria-label="打开作品">${icon('chevron-right')}</button></article>`;}).join('')||'<p class="empty">还没有作品，先选择声音开始创作。</p>'}</div>`;
 root.querySelectorAll('[data-work-select]').forEach(input=>input.onclick=e=>{e.stopPropagation();input.checked?state.selected.add(input.dataset.workSelect):state.selected.delete(input.dataset.workSelect);render();});
 $('deleteWorks').onclick=async()=>{const ids=[...state.selected].filter(id=>state.jobs.some(j=>j.id===id));if(!ids.length)return notice('先长按或勾选作品');if(!confirm(`删除选中的 ${ids.length} 个作品？`))return;await api('/api/jobs/bulk-delete',post({ids}));state.selected.clear();await refresh();};
 $('reloadWorks').onclick=()=>refresh().catch(e=>notice(e.message));
 root.querySelectorAll('[data-job]').forEach(b=>{
  let timer,clickTimer,pressed=false,startX=0,startY=0;
  const open=async()=>{try{const j=await api('/api/jobs/'+b.dataset.job);j.status==='completed'?result(j):poll(j.id);}catch(e){notice(e.message);}};
  b.onclick=e=>{if(pressed){pressed=false;return;}if(e.target.closest('[data-job-rename]')){clearTimeout(clickTimer);clickTimer=setTimeout(open,300);return;}open();};
  b.querySelector('[data-job-rename]').ondblclick=async e=>{e.stopPropagation();clearTimeout(clickTimer);const name=await renameName('修改作品名称',b.querySelector('strong').textContent);if(name?.trim()){try{await api(`/api/jobs/${b.dataset.job}/name`,{...post({name:name.trim()}),method:'PATCH'});await refresh();}catch(error){notice(error.message);}}};
  b.onpointerdown=e=>{if(e.target.closest('button,input'))return;pressed=false;startX=e.clientX;startY=e.clientY;timer=setTimeout(()=>{pressed=true;state.selected.add(b.dataset.job);render();notice('已选中作品');},550);};
  b.onpointermove=e=>{if(Math.hypot(e.clientX-startX,e.clientY-startY)>10)clearTimeout(timer);};
  b.onpointerup=b.onpointercancel=()=>clearTimeout(timer);
 });}


async function result(job){
 const version=++resultVersion,active=()=>version===resultVersion&&state.view==='result'&&state.current?.id===job.id;
 player.pause();state.view='result';state.current=job;const project=await api(job.project_url);if(!active())return;
 const sources=project.sources?.length?project.sources:[{id:project.session_id,name:'原始素材',audio_url:state.sessions.find(s=>s.id===project.session_id)?.audio_url}];
 let seed=project.seed;
 if(seed==null){try{seed=(await api(`/results/${job.id}/yue2-request.json`)).seed;}catch{seed=null;}}
 if(!active())return;
 let chinese=project.prompt_zh||'';
 root.innerHTML=`<section class="result"><button id="backWorks" class="back">${icon('arrow-left')}我的作品</button><div class="art"><img src="${workCover(job.id)}" alt="作品装饰封面"></div><h1 class="result-title">${esc(job.name||'声音创作')}</h1><p class="subtitle" style="text-align:center">YuE2 · ${time(job.duration)} · ${project.creation_mode==='melody_conditioned'?'旋律参考':'声音灵感'}</p><audio id="song" controls src="${esc(job.audio_url)}"></audio><div class="sources">${(project.sources||[]).map(s=>`<span class="chip">${esc(s.name)}</span>`).join('')}</div><button id="separate" class="primary">${icon('sliders-horizontal')}进入六轨编辑</button><p id="splitStatus" class="pending"></p><a id="editLink" class="edit-link" hidden>打开编辑器</a><a class="edit-link" href="${esc(job.audio_url)}" download>下载 WAV</a><details class="originals"><summary>原声素材 · 默认关闭</summary>${project.tracks.filter(t=>t.kind==='recorded').map(t=>`<p class="note">${esc(t.name)}</p><audio controls preload="none" src="${esc(t.url)}"></audio>`).join('')}</details></section>`;
 $('backWorks').onclick=()=>{go('works');refresh();};$('song').onplay=()=>player?.pause();
 const originals=root.querySelector('.originals');originals.open=true;
 originals.innerHTML='<summary>本次使用的原声 · 默认不播放</summary>'+sources.map(s=>`<p class="note">${esc(s.name)}</p>${s.audio_url?`<audio controls preload="none" src="${esc(s.audio_url)}"></audio>`:'<p class="note">素材已不可用</p>'}`).join('');
 const creation=document.createElement('section');creation.className='creation-record';
 creation.innerHTML=`<h2>创作记录与再创作</h2><label>中文提示词<textarea id="workPrompt" rows="5" maxlength="1250">${esc(chinese)}</textarea></label><p id="workTranslation" class="note"></p><details><summary>提交给 YuE2 的英文提示词</summary><p class="note">${esc(project.prompt_en||project.style||'历史记录未保存')}</p></details><label>Seed<input id="workSeed" type="number" min="0" max="2147483647" value="${seed??831002}"></label><label>目标时长<select id="workDuration"><option value="60">60 秒</option><option value="120" selected>120 秒</option><option value="180">180 秒</option></select></label><button id="regenerateSong" class="primary">用同一组素材生成新作品</button><p class="note">新作品独立保存，不覆盖当前作品。</p>`;
 root.querySelector('.result').append(creation);
 const archive=document.createElement('button');archive.className='archive-work';archive.innerHTML=icon('cloud-upload')+'保存到我的项目';archive.onclick=()=>window.SonicIntegrated.archiveWork(job,project).catch(e=>notice(e.message));creation.append(archive);
 if(!chinese){$('workTranslation').textContent='正在翻译历史提示词…';try{chinese=(await api('/api/prompts/translate',post({text:project.style||'instrumental music',target:'zh'}))).text;if(active()&&$('workPrompt')){$('workPrompt').value=chinese;$('workTranslation').textContent='历史提示词中文翻译';}}catch{if(active()&&$('workTranslation'))$('workTranslation').textContent='历史提示词翻译失败，可直接填写中文再创作。';}}
 if(!active())return;
 root.querySelectorAll('.originals audio').forEach(el=>el.onplay=()=>{player?.pause();$('song').pause();root.querySelectorAll('.originals audio').forEach(other=>{if(other!==el)other.pause();});});
 $('song').onplay=()=>{player?.pause();root.querySelectorAll('.originals audio').forEach(el=>el.pause());};
 $('regenerateSong').onclick=async()=>{
  const prompt=$('workPrompt').value.trim();if(!prompt){notice('请输入中文提示词。');return;}
  $('regenerateSong').disabled=true;
  try{const next=await api(`/api/jobs/${job.id}/regenerate`,post({prompt_zh:prompt,seed:Number($('workSeed').value),duration:Number($('workDuration').value)}));localStorage.setItem('sonic-mobile-job:'+window.SonicAuthUserId(),next.id);poll(next.id);}
  catch(e){notice(e.message);$('regenerateSong').disabled=false;}
 };
 $('separate').onclick=async()=>{try{$('separate').disabled=true;$('splitStatus').classList.add('is-waiting');$('splitStatus').textContent='正在准备六轨编辑，通常需要2分钟，请耐心等待～';await api(`/api/jobs/${job.id}/separate`,post({}));checkSplit(job.id);}catch(e){notice(e.message);$('separate').disabled=false;}};
 window.lucide?.createIcons();checkSplit(job.id);
}
async function checkSplit(id){
 if(state.view!=='result'||state.current?.id!==id)return;
 try{const info=await api(`/api/jobs/${id}/separate`);if(state.view!=='result'||state.current?.id!==id)return;
  const waiting=['queued','processing'].includes(info.status);$('splitStatus').classList.toggle('is-waiting',waiting);$('splitStatus').textContent=waiting?'正在准备六轨编辑，通常需要2分钟，请耐心等待～ 可切换页面，处理会继续。':info.error||info.message||'';
  if(info.status==='completed'){$('editLink').hidden=false;$('editLink').href=info.editor_url;$('separate').hidden=true;}
  else if(['queued','processing'].includes(info.status)){ $('separate').disabled=true;setTimeout(()=>checkSplit(id),2500);}
  else $('separate').disabled=false;
 }catch(e){if($('splitStatus')){$('splitStatus').classList.remove('is-waiting');$('splitStatus').textContent=e.message;}}
}
document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>{if(b.dataset.nav==='create')openCreate();else{go(b.dataset.nav);if(['library','works'].includes(b.dataset.nav))refresh().catch(e=>notice(e.message));}});
api('/api/status').then(s=>{if(s.capabilities?.public_app)document.querySelector('header>a[title="模型配置"]').hidden=true;}).catch(()=>{});
root.innerHTML='<p class="note">正在打开你的声音空间…</p>';
