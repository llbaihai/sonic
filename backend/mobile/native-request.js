/* Android uses its authenticated native connection; browsers use normal fetch. */
(()=>{
 const pending=new Map();let sequence=0;
 const encode=bytes=>{let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(binary);};
 async function request(path,options={}){
  if(!window.SonicNative?.request){try{return await fetch(path,options);}catch{throw Error('无法连接服务器，请检查网络后重试；App 内可点“重新连接”。');}}
  const requestUser=window.SonicAccountToken||'';
  const url=new URL(path,location.origin);if(url.origin!==location.origin)throw Error('请求地址不属于当前服务。');
  const req=new Request(url,options),method=req.method;
  
  const body=method==='GET'?'':encode(new Uint8Array(await req.arrayBuffer()));
  if(requestUser!==(window.SonicAccountToken||''))throw Error('账号已切换，请重新操作。');
  if(window.SonicNative?.setUserToken)window.SonicNative.setUserToken(requestUser);
  const id='sonic_'+Date.now()+'_'+(++sequence);
  return new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{pending.delete(id);reject(Error('请求等待超时，请检查网络后重试。'));},175000);
   pending.set(id,{resolve,reject,timer});
   try{window.SonicNative.request(id,url.pathname+url.search,method,body,req.headers.get('Content-Type')||'');}
   catch(error){clearTimeout(timer);pending.delete(id);reject(error);}
  });
 }
 function complete(id,status,body,contentType,error){
  const item=pending.get(id);if(!item)return;pending.delete(id);clearTimeout(item.timer);
  if(error){item.reject(Error(error));return;}
  try{const binary=atob(body),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
   item.resolve(new Response(status===204?null:bytes,{status,headers:{'Content-Type':contentType}}));
  }catch{item.reject(Error('服务器响应不完整，请重新连接后重试。'));}
 }
 window.SonicTransport={fetch:request,complete};
})();
