/* Decode short source recordings with Web Audio and keep one active source. */
(()=>{
 function create(onUpdate){
  let context,node,offset=0,started=0,frame,ticket=0,buffer;
  const cache=new Map();
  const player={id:null,playing:false,loading:false,duration:0,
   get currentTime(){return Math.min(player.duration,offset+(player.playing?context.currentTime-started:0));},
   destroy(){player.pause();cache.clear();buffer=null;if(context)void context.close();},
   pause(){++ticket;offset=player.currentTime;player.playing=false;player.loading=false;stopNode();onUpdate(player);},
   seek(time){const resume=player.playing;offset=Math.max(0,Math.min(Number(time)||0,player.duration));stopNode();player.playing=false;if(resume&&offset<player.duration)start();else onUpdate(player);},
   async play(id,url){
    if(player.id===id&&(player.playing||player.loading)){player.pause();return;}
    const same=player.id===id;player.pause();const current=++ticket;
    player.id=id;if(!same){offset=0;buffer=null;player.duration=0;}
    context??=new (window.AudioContext||window.webkitAudioContext)();
    const unlocked=context.resume();player.loading=true;onUpdate(player);
    try{
     await unlocked;
     if(!cache.has(id)){
      const response=await window.SonicTransport.fetch(url);
      if(!response.ok)throw Error(response.status===401?'连接已失效，请点重新连接。':'原声音频读取失败，请重试。');
      const decoded=await context.decodeAudioData(await response.arrayBuffer());
      if(current!==ticket)return;
      if(cache.size>=3)cache.delete(cache.keys().next().value);cache.set(id,decoded);
     }
     if(current!==ticket)return;
     buffer=cache.get(id);player.duration=buffer.duration;player.loading=false;if(offset>=player.duration)offset=0;start();
    }catch(error){if(current===ticket){player.loading=false;player.playing=false;onUpdate(player);throw error;}}
   }
  };
  function stopNode(){cancelAnimationFrame(frame);if(node){const previous=node;node=null;previous.onended=null;try{previous.stop();}catch{}previous.disconnect();}}
  function tick(){onUpdate(player);if(player.playing)frame=requestAnimationFrame(tick);}
  function start(){
   node=context.createBufferSource();node.buffer=buffer;node.connect(context.destination);
   const current=node;node.onended=()=>{if(node!==current)return;node.disconnect();node=null;player.playing=false;offset=0;onUpdate(player);};
   started=context.currentTime;player.playing=true;node.start(0,offset);tick();
  }
  return player;
 }
 window.SonicPreview={create};
})();
