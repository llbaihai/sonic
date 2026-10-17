/* Browser recording conversion; independent of music or track effects. */
window.AudioEncode={wav(buffer){
 const n=buffer.length,ch=buffer.numberOfChannels,bytes=new ArrayBuffer(44+n*ch*2),v=new DataView(bytes);
 const str=(p,s)=>{for(let i=0;i<s.length;i++)v.setUint8(p+i,s.charCodeAt(i));};
 str(0,'RIFF');v.setUint32(4,36+n*ch*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,ch,true);v.setUint32(24,buffer.sampleRate,true);v.setUint32(28,buffer.sampleRate*ch*2,true);v.setUint16(32,ch*2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,n*ch*2,true);
 const channels=Array.from({length:ch},(_,c)=>buffer.getChannelData(c));let peak=0;
 for(const a of channels)for(const x of a)peak=Math.max(peak,Math.abs(x));const gain=Math.min(1,.89/(peak||1));
 for(let i=0;i<n;i++)for(let c=0;c<ch;c++)v.setInt16(44+(i*ch+c)*2,Math.max(-32768,Math.min(32767,Math.round(channels[c][i]*gain*32767))),true);
 return new Blob([bytes],{type:'audio/wav'});
}};
