/* Product pages use real native hardware state and the teammate's account API. */
(()=>{
 let hardwareState={authReady:false,devices:[],recordings:[],message:'连接录音豆，保存生活里的灵感。'},message='';
 let accountState=JSON.parse(localStorage.getItem('sonic-account')||'null'),projects=[],cloudAudio=null,cloudAudioUrl=null;
 window.SonicAccountToken=accountState?.token||'';
 const userHeaders=()=>({'X-Sonic-User-Token':window.SonicAccountToken});
 async function accountApi(path,options={}){
  const epoch=authEpoch;const response=await request('/api/v1'+path,{...options,headers:{...options.headers,...userHeaders()}});let body={};try{body=await response.json();}catch{}
  if(epoch!==authEpoch)throw Error('账号已切换，请重新操作。');
  if(!response.ok){if(response.status===401&&state.authReady&&path!=='/auth/login')window.dispatchEvent(new Event('sonic-auth-expired'));throw Error(errors[body.error?.code]||body.detail||'账号服务暂未完成请求，请稍后重试。');}return body;
 }
 const action=(name,value='')=>{if(!window.SonicHardware){notice('请在最新版安卓App中连接录音豆。');return;}window.SonicHardware.action(name,String(value));};
 function hardware(){
  const h=hardwareState,connected=h.connected,recording=h.recordingStatus==='录音中'||h.info?.recording===true;
  root.innerHTML=`<div class="title-row"><div><h1>我的录音豆</h1><p class="subtitle">让每个声音，都有新的可能</p></div>${icon('bluetooth')}</div><section class="recorder-hero ${recording?'is-recording':''}"><div class="recorder-shape">${icon(recording?'radio':'audio-lines')}</div><h2>${esc(connected?.name||'随身的声音灵感')}</h2><p>${recording?'● 正在录音 · '+time((h.recordingDurationMs||0)/1000):connected?'已连接'+(Number.isFinite(h.info?.batteryPercent)?' · '+h.info.batteryPercent+'% 电量':''):'靠近设备，开始捕捉你的灵感'}</p>${recording?'<div class="recording-live">请在设备上结束录音，结束后点“刷新录音”</div>':''}</section><section class="hardware-panel"><p class="hardware-message" role="status">${esc(h.error||message||h.message)}</p>${!connected?`<button class="primary" data-hw="scan" ${h.scanning?'disabled':''}>${icon(h.scanning?'loader-circle':'search')}${h.scanning?'正在寻找录音豆…':'搜索附近录音豆'}</button>${h.scanning?'<button data-hw="stopScan">停止搜索</button>':''}${h.devices.map(d=>`<div class="device-line"><span><strong>${esc(d.name)}</strong><small>${d.rssi??'—'} dBm</small></span><button data-hw="connect" data-value="${esc(d.id)}" ${h.connectingId?'disabled':''}>${h.connectingId===d.id?'连接中':'连接'}</button></div>`).join('')}`:`<div class="hardware-actions"><button data-hw="refresh">${icon('refresh-cw')}刷新录音</button><button data-hw="bind">确认绑定</button><button data-hw="disconnect">断开</button></div><p class="note">使用录音豆按键录音，结束后刷新列表并同步。</p><h2>设备里的声音</h2>${h.recordings.map(r=>`<article class="device-line"><span><strong>灵感片段 ${esc(r.id)}</strong><small>${time(r.durationMs/1000)} · ${r.markersMs?.length||0} 个重点</small></span><button data-hw="import" data-value="${r.id}" ${h.uploading||h.transferId!=null?'disabled':''}>${h.transferId===r.id?(h.transferPhase||'同步中'):h.uploading?'导入中':'同步到声音库'}</button></article>`).join('')||'<p class="note">还没有读到录音，录制后点刷新。</p>'}`}<p class="note">授权、蓝牙与传输由设备SDK执行；同步成功后才加入声音库。</p></section>`;
  root.querySelectorAll('[data-hw]').forEach(button=>button.onclick=()=>action(button.dataset.hw,button.dataset.value));window.lucide?.createIcons();
 }
 let authMode='login';
 const errors={INVALID_CREDENTIALS:'用户名或密码不正确。',USERNAME_TAKEN:'这个用户名已被使用，请换一个。',REGISTRATION_DISABLED:'注册暂未开放。',RATE_LIMITED:'操作过于频繁，请稍后重试。',INVALID_CURRENT_PASSWORD:'当前密码不正确。',VALIDATION_ERROR:'请检查输入：用户名使用小写英文、数字或下划线，密码至少12位。'};
 async function establish(username,password){
  const response=await accountApi('/auth/login',post({username,password}));
  window.SonicAccountToken=response.data.access_token;window.SonicNative?.setUserToken?.(window.SonicAccountToken);
  await api('/api/account/session',post({}));
  const me=await api('/api/account/session');accountState={username:me.user.username,id:me.user.id,token:window.SonicAccountToken};
  localStorage.setItem('sonic-account',JSON.stringify(accountState));state.authReady=true;resetPrivate();state.view='library';await refresh();
 }
 function resetPrivate(){
  ++authEpoch;++resultVersion;if(cloudAudio){cloudAudio.pause();cloudAudio=null;}if(cloudAudioUrl){URL.revokeObjectURL(cloudAudioUrl);cloudAudioUrl=null;}clearTimeout(pollTimer);player.destroy();player=window.SonicPreview.create(updateSourcePlayback);
  root.querySelectorAll('audio').forEach(a=>{a.pause();a.removeAttribute('src');a.load();});
  state.sessions=[];state.jobs=[];state.selected.clear();state.current=null;state.busy=false;
  hardwareState={authReady:false,devices:[],recordings:[],message:'连接录音豆，保存生活里的灵感。'};
  document.querySelectorAll('dialog[open]').forEach(d=>d.close());$('files').value='';window.SonicHardware?.action('resetAccount','');localStorage.removeItem('sonic-mobile-job');localStorage.removeItem('sonic-account-project');
 }
 async function signedOut(revoke=true){
  if(revoke)await accountApi('/auth/logout',post({}));
  window.SonicAccountToken='';window.SonicNative?.setUserToken?.('');accountState=null;localStorage.removeItem('sonic-account');
  resetPrivate();state.authReady=false;state.view='account';authMode='login';render();
 }
 function account(){
  root.innerHTML=`<div class="title-row"><h1>我的</h1>${icon('user-round')}</div>${!accountState?`<section class="account-intro"><h2>声音有归处，灵感有回声。</h2><p class="subtitle">登录后，你的声音与作品只属于你。</p></section><div class="auth-tabs"><button id="tabLogin" class="${authMode==='login'?'active':''}">登录</button><button id="tabRegister" class="${authMode==='register'?'active':''}">注册</button></div><form id="accountLogin"><label>用户名<input name="username" autocomplete="username" required minlength="3" maxlength="64" pattern="[a-z0-9_]+" placeholder="小写英文、数字或下划线"></label><label>密码<input name="password" type="password" autocomplete="${authMode==='register'?'new-password':'current-password'}" required minlength="${authMode==='register'?12:1}" maxlength="128" placeholder="${authMode==='register'?'至少12位，可使用多个单词组合':'请输入密码'}"></label>${authMode==='register'?'<label>确认密码<input name="confirmPassword" type="password" autocomplete="new-password" required minlength="12" maxlength="128"></label>':''}<button class="primary">${authMode==='register'?'注册并登录':'登录'}</button><p class="note" id="accountError" role="status"></p></form>`:`<section class="account-intro"><h2>${esc(accountState.username)}</h2><p>私人声音库与作品</p><div class="account-actions"><button id="changePassword">修改密码</button><button id="logout">退出登录</button></div></section><form id="passwordForm" hidden><label>当前密码<input name="current" type="password" autocomplete="current-password" required></label><label>新密码<input name="next" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><label>确认新密码<input name="confirm" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><button class="primary">保存新密码</button><p class="note" id="passwordError" role="status"></p></form><details class="account-projects"><summary>项目归档 · 可选</summary><form id="newProject" class="project-create"><input aria-label="项目名称" placeholder="给新项目起个名字" maxlength="120" required><button>新建项目</button></form><div id="accountProjects"><p class="note">正在读取项目…</p></div></details>`}`;
  if(!accountState){
   $('tabLogin').onclick=()=>{authMode='login';account();};$('tabRegister').onclick=()=>{authMode='register';account();};
   $('accountLogin').onsubmit=async event=>{event.preventDefault();const form=event.target,button=form.querySelector('button');button.disabled=true;
    try{const username=form.username.value.trim(),password=form.password.value;
     if(authMode==='register'){if(password!==form.confirmPassword.value)throw Error('两次输入的密码不一致。');await accountApi('/auth/register',post({username,password}));}
     await establish(username,password);
    }catch(error){if($('accountError'))$('accountError').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}
   };
  }else{
   $('logout').onclick=async()=>{try{await signedOut();}catch(error){notice(error.message);}};
   $('changePassword').onclick=()=>{$('passwordForm').hidden=!$('passwordForm').hidden;};
   $('passwordForm').onsubmit=async e=>{e.preventDefault();const form=e.target,button=form.querySelector('button');button.disabled=true;
    try{if(form.next.value!==form.confirm.value)throw Error('两次输入的密码不一致。');await accountApi('/auth/password',post({current_password:form.current.value,new_password:form.next.value}));await signedOut(false);notice('密码已更新，请重新登录。');}
    catch(error){if($('passwordError'))$('passwordError').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}
   };
   $('newProject').onsubmit=async event=>{event.preventDefault();try{await accountApi('/projects',{...post({name:event.target.querySelector('input').value.trim()}),headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()}});account();}catch(error){notice(error.message);}};
   loadProjects().catch(error=>{if($('accountProjects'))$('accountProjects').textContent=error.message;});
  }window.lucide?.createIcons();
 }
 async function loadProjects(){const response=await accountApi('/projects');projects=response.data;if(state.view!=='account'||!$('accountProjects'))return;$('accountProjects').innerHTML=projects.map(p=>`<button class="work" data-project-id="${p.id}"><span>${esc(p.name)}<small>查看云端素材与作品</small></span>${icon('chevron-right')}</button>`).join('')||'<p class="note">还没有项目，先创建一个。</p>';root.querySelectorAll('[data-project-id]').forEach(button=>button.onclick=()=>showProject(button.dataset.projectId).catch(error=>notice(error.message)));window.lucide?.createIcons();}
 async function showProject(id){
  const [assets,works]=await Promise.all([accountApi('/projects/'+id+'/assets'),accountApi('/projects/'+id+'/works')]);
  const title=projects.find(p=>p.id===id)?.name||'项目';root.innerHTML=`<button id="backAccount">${icon('arrow-left')}我的项目</button><h1 class="project-title">${esc(title)}</h1><h2>作品 · ${works.data.length}</h2>${works.data.map(w=>`<div class="cloud-record"><strong>${esc(w.title)}</strong><button data-cloud-audio="/works/${w.id}/download">试听</button></div>`).join('')||'<p class="note">暂未保存作品。</p>'}<h2>素材 · ${assets.data.length}</h2>${assets.data.map(a=>`<div class="cloud-record"><strong>${esc(a.filename)}</strong><button data-cloud-audio="/assets/${a.id}/download">试听</button></div>`).join('')}`;
  $('backAccount').onclick=account;root.querySelectorAll('[data-cloud-audio]').forEach(button=>button.onclick=async()=>{try{player.pause();const response=await request('/api/v1'+button.dataset.cloudAudio,{headers:userHeaders()});if(!response.ok)throw Error('音频读取未完成');if(cloudAudio)cloudAudio.pause();if(cloudAudioUrl)URL.revokeObjectURL(cloudAudioUrl);cloudAudioUrl=URL.createObjectURL(await response.blob());cloudAudio=new Audio(cloudAudioUrl);cloudAudio.onended=()=>{if(cloudAudioUrl)URL.revokeObjectURL(cloudAudioUrl);cloudAudioUrl=null;};await cloudAudio.play();}catch(error){notice(error.message);}});window.lucide?.createIcons();
 }
 async function archiveWork(job,project){
  if(!accountState){go('account');notice('先登录，再将作品保存到自己的项目。');return;}
  let selected=localStorage.getItem('sonic-account-project');const list=(await accountApi('/projects')).data;
  if(!list.some(p=>p.id===selected)){selected=list[0]?.id;if(!selected)selected=(await accountApi('/projects',{...post({name:'我的声音创作'}),headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()}})).data.id;localStorage.setItem('sonic-account-project',selected);}
  notice('正在保存作品到云端项目…');await api('/api/account/archive',{...post({job_id:job.id,project_id:selected}),headers:{'Content-Type':'application/json',...userHeaders()}});
  notice('作品已保存到我的项目。');
 }
 window.addEventListener('sonic-hardware-state',event=>{if(state.accountsRequired&&!state.authReady)return;hardwareState=event.detail;if(state.view==='hardware')hardware();});
 window.addEventListener('sonic-hardware-message',event=>{message=event.detail.message;notice(message);if(state.view==='hardware')hardware();});
 window.addEventListener('sonic-hardware-imported',async event=>{if(state.accountsRequired&&!state.authReady)return;hardwareState.recordings=(hardwareState.recordings||[]).filter(item=>String(item.id)!==String(event.detail.hardware?.device_file_id||''));state.selected.clear();state.selected.add(event.detail.id);state.view='library';await refresh();notice('真实录音和重点标记已同步。');});
 window.SonicIntegrated={hardware,account,archiveWork,signedOut};
 window.SonicAuth={async boot(){
  try{const status=await api('/api/status');state.accountsRequired=!!status.capabilities?.user_accounts;
   if(!state.accountsRequired){state.authReady=true;await refresh();return;}
   const me=await api('/api/account/session');
   if(accountState?.id&&accountState.id!==me.user.id)resetPrivate();
   accountState={id:me.user.id,username:me.user.username,token:window.SonicAccountToken};localStorage.setItem('sonic-account',JSON.stringify(accountState));state.authReady=true;
   window.SonicNative?.setUserToken?.(window.SonicAccountToken);await refresh();
   const id=localStorage.getItem('sonic-mobile-job:'+me.user.id);if(id)poll(id);
  }catch(error){state.accountsRequired=true;await signedOut(false);}
 }};
 window.addEventListener('sonic-auth-expired',()=>{if(state.authReady)void signedOut(false);});
 void window.SonicAuth.boot();
 if(['hardware','account'].includes(state.view))render();
})();
