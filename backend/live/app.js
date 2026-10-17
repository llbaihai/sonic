(() => {
  'use strict';
  const TRACKS = ['vocals','drums','bass','piano','guitar','other'];
  const NAMES = {vocals:'人声',drums:'鼓组',bass:'贝斯',piano:'钢琴',guitar:'吉他',other:'其他'};
  const COLORS = {vocals:'#92bca9',drums:'#d8b48f',bass:'#9abbd6',piano:'#b2a1d0',guitar:'#d3a2ae',other:'#94bdbd'};
  const icons=()=>window.lucide?.createIcons();
  function command(id,icon,text){const el=document.getElementById(id);el.innerHTML=`<i data-lucide="${icon}" aria-hidden="true"></i><span>${text}</span>`;icons();}
  const $ = id => document.getElementById(id);
  const clamp = (v,a,b) => Math.min(b,Math.max(a,v));
  const fmt = v => Number.isFinite(v) ? v.toFixed(2).replace(/\.00$/,'') : '—';
  const timeLabel = t => `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;
  const s = {id:null, duration:0, zoom:52, selected:'drums', start:0,end:1,target:2,
    edits:[], waveforms:{}, selecting:false, revision:{}, loading:false, exported:null};
  const status = (msg,type='') => { $('status').textContent=msg; $('status').className='status '+type; };
  const hint = (msg,type='') => { $('hint').textContent=msg; $('hint').className='note '+type; };

  async function api(path,opts) {
    const r = await fetch(path,opts);
    if (!r.ok) { let reason = await r.text();try{const j=JSON.parse(reason);reason=JSON.stringify(j.detail||j).slice(0,350);}catch{}
      throw new Error(`${r.status}: ${reason}`); }
    return r.json();
  }
  const encodedId = () => encodeURIComponent(s.id);
  const pospx = t => t*s.zoom;
  function readSelection(needsTarget=false) {
    const a=Number($('start').value), b=Number($('end').value), t=Number($('target').value);
    if (![a,b,t].every(Number.isFinite)||a<0||b<=a||b>s.duration+1e-3)throw new Error('请设置正确的片段开始、结束时间');
    if(needsTarget&&(t<0||t+b-a>s.duration+1e-3))throw new Error('目标片段必须完全位于当前歌曲时长内');
    return {start:a,end:b,target:t};
  }
  function refreshInputs(){ $('trackName').value=NAMES[s.selected]||s.selected; $('start').value=fmt(s.start);$('end').value=fmt(s.end);$('target').value=fmt(s.target); }
  function selectTrack(t){s.selected=t;refreshInputs();updateHighlights();hint(`已选择${NAMES[t]}。可框选片段、调音量，或复制鼓点。`);}

  // ---- Browser-side sample-accurate mixing: X + (edited stem - original stem) ----
  const engine = {
    ctx:null, original:null, stemCache:new Map(), active:new Map(), configs:new Map(),
    playing:false, anchor:0, offset:0, main:null, raf:0, loadingOriginal:null, baseProject:null,
    async context() {
      if (!this.ctx) this.ctx=new (window.AudioContext||window.webkitAudioContext)();
      return this.ctx;
    },
    async loadAudio(track) {
      const ctx=await this.context();
      const requestedProject=s.id;
      const key=requestedProject+'/'+track;
      if(track==='original'&&this.original&&this.baseProject===s.id)return this.original;
      if(track!=='original'&&this.stemCache.has(key))return this.stemCache.get(key);
      const task=(async()=>{
        const url=`/live-api/projects/${encodedId()}/audio/${encodeURIComponent(track)}`;
        const r=await fetch(url);if(!r.ok)throw new Error(`下载 ${track} 音频失败：HTTP ${r.status}`);
        return await ctx.decodeAudioData(await r.arrayBuffer());
      })();
      if(track!=='original') this.stemCache.set(key,task);
      try{
        const buffer=await task;
        if(track==='original'&&requestedProject===s.id){this.original=buffer;this.baseProject=requestedProject;}
        return buffer;
      } catch(e){if(track!=='original')this.stemCache.delete(key);throw e;}
    },
    time(){return this.playing?clamp(this.offset+this.ctx.currentTime-this.anchor,0,s.duration):this.offset;},
    stopNode(node,when=0){try{node.source.stop(when||undefined);}catch{} try{node.source.disconnect();}catch{} try{node.gain.disconnect();}catch{}},
    clearPlaying(){
      if(this.main){this.stopNode(this.main);this.main=null;}
      for(const node of this.active.values())this.stopNode(node);
      this.active.clear();
    },
    nodeFor(buffer,position,when,gainValue) {
      if(!buffer||position>=buffer.duration)return null;
      const source=this.ctx.createBufferSource(),gain=this.ctx.createGain();
      source.buffer=buffer;gain.gain.setValueAtTime(gainValue,when);
      source.connect(gain);gain.connect(this.ctx.destination);
      source.start(when,Math.min(position,Math.max(0,buffer.duration-1/this.ctx.sampleRate)));
      return {source,gain};
    },
    async play(){
      if(!s.id)return;
      await this.context();
      // Must be requested within the user gesture on mobile browsers.
      const resumed=this.ctx.resume();
      if(!this.original||this.baseProject!==s.id){
        $('engineStatus').textContent='加载原曲…';
        await this.loadAudio('original');
      }
      await resumed;
      if(this.playing)return;
      if(this.offset>=s.duration-.03)this.offset=0;
      this.clearPlaying();
      const now=this.ctx.currentTime+.045;
      this.anchor=now;this.playing=true;
      this.main=this.nodeFor(this.original,this.offset,now,1);
      for(const [name,cfg] of this.configs) {
        const node=this.nodeFor(cfg.buffer,this.offset,now,cfg.gain);
        if(node)this.active.set(name,node);
      }
      command('play','pause','暂停');
      $('engineStatus').textContent='浏览器实时混音中';
      this.drawClock();
    },
    pause(){
      if(!this.playing)return;
      this.offset=this.time();this.playing=false;this.clearPlaying();
      cancelAnimationFrame(this.raf);this.raf=0;
      command('play','play','播放');this.updateClock();
    },
    seek(to){
      const wasPlaying=this.playing;this.pause();this.offset=clamp(to,0,s.duration);this.updateClock();
      if(wasPlaying)void this.play().catch(e=>status(`播放失败：${e.message}`,'error'));
    },
    reset(){this.pause();this.offset=0;this.original=null;this.baseProject=null;this.configs.clear();this.stemCache.clear();this.updateClock();},
    updateClock(){const t=this.time();$('seek').value=String(t);$('clock').textContent=`${timeLabel(t)} / ${timeLabel(s.duration)}`;
      document.querySelectorAll('.playhead').forEach(el=>{el.style.left=`${pospx(t)}px`;});},
    drawClock(){this.updateClock();if(this.playing){
      if(this.time()>=s.duration-.025){this.pause();this.offset=s.duration;this.updateClock();return;}
      this.raf=requestAnimationFrame(()=>this.drawClock());}},
    install(name,buffer,gainValue=1){
      this.configs.set(name,{buffer,gain:gainValue});
      const old=this.active.get(name);
      if(!this.playing){if(old){this.stopNode(old);this.active.delete(name);}return;}
      const now=this.ctx.currentTime, when=now+.01, position=this.time();
      const fresh=this.nodeFor(buffer,position,when,0);
      if(fresh){fresh.gain.gain.setValueAtTime(0,when);fresh.gain.gain.linearRampToValueAtTime(gainValue,when+.03);this.active.set(name,fresh);}else this.active.delete(name);
      if(old){old.gain.gain.cancelScheduledValues(now);old.gain.gain.setValueAtTime(old.gain.gain.value,now);old.gain.gain.linearRampToValueAtTime(0,when+.03);
        setTimeout(()=>this.stopNode(old),100);}
    },
    remove(name){this.configs.delete(name);const old=this.active.get(name);this.active.delete(name);
      if(old){const now=this.ctx.currentTime;old.gain.gain.setTargetAtTime(0,now,.01);setTimeout(()=>this.stopNode(old),100);}},
    directGain(name,source,db){
      // dB to linear amplitude. 0 dB must remain unity; subtracting 1 muted the track.
      this.install(name,source,Math.pow(10,db/20));
    },
    adjustDirect(name,db){
      const g=Math.pow(10,db/20);const cfg=this.configs.get(name);
      if(!cfg)return;
      cfg.gain=g;const active=this.active.get(name);
      if(active)active.gain.gain.setTargetAtTime(g,this.ctx.currentTime,.013);
    }
  };

  function operations(track){return s.edits.filter(e=>e.track===track);}
  function currentTrackGain(track){const es=operations(track).filter(e=>e.operation==='gain'&&e.start===undefined);return es.length?es[es.length-1].gain_db:0;}
  function hasNonGain(track){return operations(track).some(e=>e.operation!=='gain'||e.start!==undefined);}

  function buildLocalDelta(raw,edits){
    const sr=engine.ctx.sampleRate, n=engine.original.length;
    const channels=engine.original.numberOfChannels;
    const out=engine.ctx.createBuffer(channels,n,sr);
    for(let c=0;c<channels;c++) {
      const origin=raw.getChannelData(Math.min(c,raw.numberOfChannels-1));
      const working=new Float32Array(n);working.set(origin.subarray(0,Math.min(n,origin.length)));
      for(const edit of edits){
        const a=clamp(Math.round((edit.start??0)*sr),0,n),b=clamp(Math.round((edit.end??s.duration)*sr),0,n);
        if(b<=a)throw new Error('音轨片段为空');
        const count=b-a, fade=Math.min(Math.round(.015*sr),Math.floor(count/2));
        const envelope = i => fade>=2 ? Math.min(1,i/(fade-1),(count-1-i)/(fade-1)) : 1;
        if(edit.operation==='gain'){
          const amp=Math.pow(10,edit.gain_db/20);
          if(a===0&&b===n){for(let i=0;i<n;i++)working[i]*=amp;}
          else {for(let i=0;i<count;i++){const idx=a+i;working[idx]*=1+(amp-1)*envelope(i);}}
        } else if(edit.operation==='mute'){
          if(a===0&&b===n)working.fill(0);
          else for(let i=0;i<count;i++)working[a+i]*=1-envelope(i);
        } else if(edit.operation==='duplicate'||edit.operation==='move'){
          const target=Math.round(edit.target*sr);
          if(target<0||target+count>n)throw new Error('目标片段超出歌曲末尾');
          const clip=working.slice(a,b); // important: source is the track's CURRENT state
          if(edit.operation==='move')for(let i=0;i<count;i++)working[a+i]*=1-envelope(i);
          for(let i=0;i<count;i++)working[target+i]+=clip[i]*envelope(i);
        } else if(edit.operation==='pitch')throw new Error('音高处理应由服务器完成');
      }
      const dest=out.getChannelData(c);
      for(let i=0;i<n;i++)dest[i]=working[i]-(i<origin.length?origin[i]:0);
    }
    return out;
  }

  async function updateTrack(track,strict=false){
    const seq=s.revision[track]=(s.revision[track]||0)+1, project=s.id;
    const edits=operations(track);
    if(!edits.length){engine.remove(track);return;}
    try {
      await engine.loadAudio('original');
      if(seq!==s.revision[track]||project!==s.id)return;
      const simpleGain=edits.length===1&&edits[0].operation==='gain'&&edits[0].start===undefined;
      const raw=await engine.loadAudio(track);
      if(seq!==s.revision[track]||project!==s.id)return;
      if(simpleGain){engine.directGain(track,raw,edits[0].gain_db);return;}
      let delta;
      if(edits.some(e=>e.operation==='pitch')){
        $('engineStatus').textContent='正在自动处理变调…';
        const resp=await fetch(`/live-api/projects/${encodedId()}/delta/${track}`,{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({edits})
        });
        if(!resp.ok)throw new Error(`变调失败（${resp.status}）：${(await resp.text()).slice(0,160)}`);
        delta=await engine.ctx.decodeAudioData(await resp.arrayBuffer());
      }else delta=buildLocalDelta(raw,edits);
      if(seq!==s.revision[track]||project!==s.id)return;
      engine.install(track,delta,1);
      $('engineStatus').textContent='浏览器实时混音就绪';
    }catch(e){if(seq===s.revision[track]){status(`音轨 ${NAMES[track]} 更新失败：${e.message}`,'error');$('engineStatus').textContent='预听更新失败';}if(strict)throw e;}
  }
  function activeRefresh(tracks){for(const t of tracks)void updateTrack(t);}

  // ---- Timeline / controls ----
  function drawWave(peaks,color){
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 1400 84');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('aria-hidden','true');
    const n=peaks.length||1, max=Math.max(.025,...peaks),up=[],down=[];
    peaks.forEach((p,i)=>{const x=(i+.5)*1400/n,a=Math.max(1,Math.min(36,(p/max)*35));up.push(`${x.toFixed(1)},${(42-a).toFixed(1)}`);down.push(`${x.toFixed(1)},${(42+a).toFixed(1)}`);});
    const poly=document.createElementNS(ns,'polygon');poly.setAttribute('points',up.concat(down.reverse()).join(' '));poly.setAttribute('fill',color);poly.setAttribute('fill-opacity','.8');svg.append(poly);return svg;
  }
  function ruler(el,labels=false){const step=s.zoom<35?10:s.zoom>=70?2:5;for(let t=0;t<=s.duration;t+=step){const tick=document.createElement(labels?'span':'i');tick.style.left=pospx(t)+'px';if(labels)tick.textContent=`${t}s`;else tick.className='tick';el.append(tick);}}
  function updateHighlights(){
    document.querySelectorAll('.row[data-track]').forEach(row=>{
      row.querySelector('.meta').classList.toggle('selected',row.dataset.track===s.selected);
      const box=row.querySelector('.selectedregion');box.style.display=(row.dataset.track===s.selected?'block':'none');box.style.left=pospx(s.start)+'px';box.style.width=Math.max(2,pospx(s.end-s.start))+'px';
    });
  }
  function drawClips(){
    document.querySelectorAll('.clip').forEach(el=>el.remove());
    s.edits.forEach((edit,index)=>{
      if(!['duplicate','move'].includes(edit.operation))return;
      const lane=document.querySelector(`.lane[data-track="${edit.track}"]`);if(!lane)return;
      const clip=document.createElement('div');clip.className='clip '+(edit.operation==='move'?'moved':'');clip.style.left=pospx(edit.target)+'px';clip.style.width=Math.max(10,pospx(edit.end-edit.start))+'px';clip.title='拖动此片段可改变目标位置；松手立即更新播放';
      const inner=document.createElement('span');inner.textContent=edit.operation==='move'?'移动':'复制';clip.append(inner);lane.append(clip);
      clip.addEventListener('pointerdown',e=>{
        if(e.button>0)return;
        e.preventDefault();e.stopPropagation();clip.setPointerCapture(e.pointerId);
        const begin=e.clientX,initial=edit.target;
        function drag(ev){const next=clamp(initial+(ev.clientX-begin)/s.zoom,0,s.duration-(edit.end-edit.start));clip.style.left=pospx(next)+'px';clip.dataset.next=String(next);}
        function done(){clip.removeEventListener('pointermove',drag);clip.removeEventListener('pointerup',done);clip.removeEventListener('pointercancel',done);
          const next=Number(clip.dataset.next||initial);if(Math.abs(next-edit.target)>.002){s.edits[index]={...edit,target:Number(next.toFixed(3))};changed([edit.track]);}}
        clip.addEventListener('pointermove',drag);clip.addEventListener('pointerup',done);clip.addEventListener('pointercancel',done);
      });
    });
  }
  function makeTimeline(){
    const holder=$('tracks');holder.replaceChildren();holder.style.setProperty('--width',Math.max(300,Math.round(s.duration*s.zoom))+'px');
    const rulerow=document.createElement('div');rulerow.className='row rulerrow';const meta=document.createElement('div');meta.className='meta';meta.textContent='音轨 / 时间';const rail=document.createElement('div');rail.className='ruler';ruler(rail,true);rulerow.append(meta,rail);holder.append(rulerow);
    for(const t of TRACKS){
      const row=document.createElement('div');row.className='row';row.dataset.track=t;
      const m=document.createElement('div');m.className='meta';const name=document.createElement('strong');name.textContent=NAMES[t];m.append(name);
      const sub=document.createElement('div');sub.className='sub';sub.textContent=t;m.append(sub);
      const area=document.createElement('div');area.className='gain';const slide=document.createElement('input');slide.type='range';slide.min='-24';slide.max='12';slide.step='1';slide.value=currentTrackGain(t);slide.setAttribute('aria-label',NAMES[t]+'音量 dB');
      const val=document.createElement('small');val.textContent=slide.value+'dB';
      slide.addEventListener('input',()=>{
        val.textContent=slide.value+'dB';
        if(!hasNonGain(t)){
          const db=Number(slide.value);
          // Update an existing Web Audio GainNode directly, without rebuilding the song.
          if(engine.configs.has(t))engine.adjustDirect(t,db);
          else void engine.loadAudio('original').then(()=>engine.loadAudio(t)).then(buf=>engine.directGain(t,buf,db)).catch(e=>status(e.message,'error'));
        }
      });
      slide.addEventListener('change',()=>{
        const gain=Number(slide.value);
        s.edits=s.edits.filter(e=>!(e.track===t&&e.operation==='gain'&&e.start===undefined));
        if(gain!==0)s.edits.push({track:t,operation:'gain',gain_db:gain});
        changed([t]);
      });
      area.append(slide,val);m.append(area);m.addEventListener('click',e=>{if(e.target!==slide)selectTrack(t);});
      row.append(m);
      const lane=document.createElement('div');lane.className='lane';lane.dataset.track=t;
      ruler(lane,false);lane.append(drawWave(s.waveforms[t]||[0],COLORS[t]));
      const box=document.createElement('div');box.className='selectedregion';lane.append(box);
      const ph=document.createElement('div');ph.className='playhead';lane.append(ph);
      let startX=null;
      const eventTime=e=>clamp((e.clientX-lane.getBoundingClientRect().left)/s.zoom,0,s.duration);
      lane.addEventListener('pointerdown',e=>{
        if(!s.selecting||e.button>0)return;
        e.preventDefault();s.selected=t;startX=eventTime(e);s.start=startX;s.end=Math.min(s.duration,startX+.1);lane.setPointerCapture(e.pointerId);updateHighlights();
      });
      lane.addEventListener('pointermove',e=>{if(startX===null)return;const next=eventTime(e);
        s.start=Math.min(startX,next);s.end=Math.min(s.duration,Math.max(Math.max(startX,next),s.start+.05));updateHighlights();refreshInputs();});
      const selectionDone=()=>{if(startX===null)return;startX=null;s.selecting=false;$('selectMode').classList.remove('active');setSelectMode(false);
        s.target=clamp(s.end+.5,0,Math.max(0,s.duration-(s.end-s.start)));refreshInputs();updateHighlights();hint(`已选中 ${NAMES[t]} 的 ${fmt(s.start)}–${fmt(s.end)} 秒。`);};
      lane.addEventListener('pointerup',selectionDone);lane.addEventListener('pointercancel',selectionDone);
      lane.addEventListener('click',()=>{if(!s.selecting)selectTrack(t);});
      row.append(lane);holder.append(row);
    }
    updateHighlights();drawClips();engine.updateClock();
  }
  function setSelectMode(yes){s.selecting=yes;$('selectMode').classList.toggle('active',yes);command('selectMode',yes?'check':'scan',yes?'选择中':'框选区间');document.querySelectorAll('.lane').forEach(el=>el.style.touchAction=yes?'none':'pan-x pan-y');}
  function summary(e){
    const clip=e.start===undefined?'整轨':`${fmt(e.start)}–${fmt(e.end)}s`;
    if(e.operation==='gain')return `${clip} 音量 ${e.gain_db} dB`;
    if(e.operation==='mute')return `${clip} 静音`;
    if(e.operation==='pitch')return `${clip} 变调 ${e.semitones>0?'+':''}${e.semitones} 半音`;
    return `${clip} ${e.operation==='duplicate'?'复制':'移动'} → ${fmt(e.target)}s`;
  }
  function drawHistory(){
    const list=$('history');list.replaceChildren();
    if(!s.edits.length){list.innerHTML='<li>暂无操作</li>';return;}
    s.edits.forEach((e,i)=>{const li=document.createElement('li'),label=document.createElement('span'),del=document.createElement('button');label.textContent=`${i+1}. ${NAMES[e.track]} · ${summary(e)}`;del.textContent='删除';del.addEventListener('click',()=>{s.edits.splice(i,1);changed([e.track]);});li.append(label,del);list.append(li);});
  }
  function changed(affected){
    s.exported=null;$('download').hidden=true;
    $('undo').disabled=!s.edits.length;$('clear').disabled=!s.edits.length;
    $('count').textContent=`${s.edits.length} 条`;drawHistory();makeTimeline();
    status('编辑已记录；播放时直接试听当前效果。只有最终下载时才需要服务器导出。','success');
    activeRefresh([...new Set(affected)]);
  }
  function addOperation(op){
    try{
      if(!s.id)throw new Error('请先加载工程');
      const {start,end,target}=readSelection(op==='duplicate'||op==='move');
      const edit={track:s.selected,operation:op,start,end};
      if(['duplicate','move'].includes(op))edit.target=target;
      if(op==='pitch'){
        const v=Number($('semitones').value);
        if(!Number.isFinite(v)||Math.abs(v)>12||end-start<.2)throw new Error('变调范围为 ±12 半音，选区不能短于 0.2 秒');
        edit.semitones=v;
      }
      s.edits.push(edit);changed([s.selected]);
      hint(op==='pitch'?'音高正在后台自动计算，完成后直接加入当前歌曲。':'已更新实时混音。无需重新生成，直接按播放试听。','success');
    }catch(e){hint(e.message,'error');}
  }
  async function exportSong(){if(!s.id||s.loading)return;s.loading=true;$('export').disabled=true;status('正在通过 AutoDL 导出最终完整 WAV…');
    try{const result=await api(`/projects/${encodedId()}/render`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({edits:s.edits})});
      s.exported=result;const a=$('download');a.href=result.download_url;a.hidden=false;status(`已完成导出：${result.duration}s、${result.operations_applied} 条指令。你可以下载最终 WAV。`,'success');
    }catch(e){status(`最终导出失败：${e.message}`,'error');}finally{s.loading=false;$('export').disabled=false;}
  }
  function enable(yes){for(const id of ['play','seek','selectMode','duplicate','move','mute','pitch','undo','clear','export']) $(id).disabled=!yes;}
  async function loadProject(id){if(!id)return;enable(false);engine.reset();s.id=id;s.edits=[];s.revision={};s.waveforms={};$('download').hidden=true;status('正在加载歌曲信息和六条波形…');
    try{
      const m=await api(`/projects/${encodedId()}/tracks`);
      s.duration=m.original.duration;if(!Number.isFinite(s.duration)||s.duration<=0)throw new Error('无效的歌曲时长');
      const data=await Promise.all(TRACKS.map(async t=>[t,(await api(`/studio-api/projects/${encodedId()}/waveforms/${t}`)).peaks]));
      for(const [t,peaks] of data)s.waveforms[t]=peaks;
      s.selected='drums';s.start=0;s.end=Math.min(1,s.duration);s.target=Math.min(2,Math.max(0,s.duration-(s.end-s.start)));
      $('seek').max=s.duration;$('seek').value=0;enable(true);$('undo').disabled=true;$('clear').disabled=true;
      drawHistory();$('count').textContent='0 条';refreshInputs();makeTimeline();$('engineStatus').textContent='点击播放加载原曲';
      status(`工程 ${id} 已加载；点击「▶ 播放」，编辑将直接作用于正在播放的混音。`,'success');
    }catch(e){s.id=null;status(`加载失败：${e.message}`,'error');$('tracks').innerHTML='<div class="placeholder">工程加载失败，请检查是否注册了歌曲。</div>';}
  }
  async function start(){try{const projects=await api('/studio-api/projects');const sel=$('project');sel.replaceChildren();if(!projects.projects.length){sel.innerHTML='<option value="">没有歌曲工程</option>';status('请先在服务器执行 sonic_mvp.py register 注册歌曲。','error');return;}
      for(const p of projects.projects){const el=document.createElement('option');el.value=p.id;el.textContent=`${p.id} (${fmt(p.duration)}s)`;sel.append(el);}
      const requested=new URLSearchParams(location.search).get('project');
      if(requested){
        if(!projects.projects.some(p=>p.id===requested))throw new Error('请求的歌曲工程不存在，请返回工作区重试。');
        sel.value=requested;
      }else if(projects.projects.some(p=>p.id==='song001'))sel.value='song001';await loadProject(sel.value);
    }catch(e){status(`服务连接失败：${e.message}`,'error');}
  }
  $('load').addEventListener('click',()=>void loadProject($('project').value));
  $('play').addEventListener('click',async()=>{try{if(engine.playing)engine.pause();else await engine.play();}catch(e){status(`不能播放：${e.message}`,'error');$('engineStatus').textContent='播放不可用';}});
  $('seek').addEventListener('change',()=>engine.seek(Number($('seek').value)));
  $('selectMode').addEventListener('click',()=>setSelectMode(!s.selecting));
  $('zoom').addEventListener('input',()=>{const scroll=$('scroll'),p=scroll.scrollLeft/Math.max(1,s.zoom);s.zoom=Number($('zoom').value);$('zoomNum').textContent=s.zoom+' px/s';if(s.id){makeTimeline();scroll.scrollLeft=p*s.zoom;}});
  for(const id of ['start','end','target'])$(id).addEventListener('change',()=>{try{const e=readSelection();s.start=e.start;s.end=e.end;s.target=e.target;updateHighlights();}catch(e){hint(e.message,'error');}});
  for(const op of ['duplicate','move','mute','pitch'])$(op).addEventListener('click',()=>addOperation(op));
  $('undo').addEventListener('click',()=>{if(s.edits.length){const last=s.edits.pop();changed([last.track]);}});
  $('clear').addEventListener('click',()=>{const touched=new Set(s.edits.map(e=>e.track));s.edits=[];changed([...touched]);});
  $('export').addEventListener('click',()=>void exportSong());
  window.addEventListener('beforeunload',()=>{engine.pause();if(engine.ctx)void engine.ctx.close();});
  icons();void start();
})();
