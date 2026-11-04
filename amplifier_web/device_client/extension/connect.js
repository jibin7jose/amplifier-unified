const $=selector=>document.querySelector(selector),params=new URLSearchParams(location.search);
const prefix='/api/connections/v1';
let host,connection;
const display=text=>{$('#status').textContent=text};
async function api(path,payload,token){
 return connectionRequest(host,path,payload,token);
}
async function done(){
 $('#qr-wrap').hidden=true;
 $('#controls').hidden=true;$('#code').textContent='';$('#approval').hidden=true;$('#return').hidden=false;
 $('#task-upgrade').hidden=connection.grants.includes('browser.task');$('#upgrade').hidden=connection.grants.includes('browser.act');$('#rotate').hidden=false;
 $('#retry').hidden=true;$('#network-help').hidden=true;
 display('Approved. Checking the background connection…');
 const result=await chrome.runtime.sendMessage({type:'check-connection'});
 if(result.error){
  display('Access is saved, but the background connection is not ready. '+result.error);
  $('#retry').hidden=false;$('#network-help').hidden=result.code!=='connection_transport';return;
 }
 display('Connected. Open the extension on the browser tab you want to use, then choose Use this tab.');
}
async function begin(upgrade=false,alreadyGranted=false,taskAccess=false){
 $('#connect').disabled=true;$('#upgrade').disabled=true;$('#task-upgrade').disabled=true;
 try{
  const pattern=new URL(host).origin+'/*';
  // Request while the actual button's user gesture is active. An asynchronous
  // contains() check first can lose that gesture before the browser prompt.
  if(!alreadyGranted&&!await chrome.permissions.request({origins:[pattern]}))throw Error('Permission to reach this service was not granted.');
  const bytes=crypto.getRandomValues(new Uint8Array(32));
  const encode=bytes=>btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  const verifier=encode(bytes),challenge=encode(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
  const grants=['connection.ping',...(upgrade||$('#browser').checked?['browser.observe','browser.act']:[]),...(taskAccess?['browser.task']:[])];
  const pending=await api(upgrade?'/self/upgrade':'/enroll',{name:'Browser on '+(navigator.userAgentData?.platform||navigator.platform),
   appId:'amplifier-connect-browser-preview',grants,challenge,...(params.get('setup')?{setupId:params.get('setup')}:{})},upgrade?connection.token:undefined);
  if(!pending.verificationPath.startsWith('/connect/verify?id=')||pending.verificationPath.includes('://'))throw Error('Unexpected approval address.');
  $('#code').textContent=pending.code;
  $('#approval').href=host+pending.verificationPath;$('#approval').hidden=false;
  if(pending.qrPath?.startsWith(prefix+'/qr?id=')){
   try{
    const qr=await fetch(host+pending.qrPath,{credentials:'omit',redirect:'error',signal:AbortSignal.timeout(10000)});
    if(qr.ok){$('#qr').src=URL.createObjectURL(await qr.blob());$('#qr-wrap').hidden=false}
   }catch{/* The direct approval link and code remain available. */}
  }
  display('Compare this code with the approval page and allow the connection.');
  await chrome.tabs.create({url:host+pending.verificationPath});
  const deadline=Date.now()+Math.min(pending.expiresIn,600)*1000;
  while(Date.now()<deadline){
   const value=await api('/token',{id:pending.id,verifier});
   if(value.status==='approved'){
    if(value.scope!=='device-v1'||!/^amd_[a-f0-9]{32}\.[A-Za-z0-9_-]{64}$/.test(value.token))throw Error('Unexpected connection credential.');
    connection={host,token:value.token,deviceID:value.deviceID,grants:value.grants,stopped:true,setupId:params.get('setup')};
    await chrome.storage.local.set({connection});
    await done();return;
   }
   if(value.status!=='pending')throw Error('Connection '+value.status+'. No new access was granted.');
   await new Promise(resolve=>setTimeout(resolve,Math.max(5,Math.min(pending.interval,30))*1000));
  }
  throw Error('The approval expired. Start again.');
 }catch(error){display(error.message);$('#code').textContent='';$('#network-help').hidden=error.code!=='connection_transport'}
 finally{$('#connect').disabled=false;$('#upgrade').disabled=false;$('#task-upgrade').disabled=false}
}
async function initialize(){
 try{
  const url=new URL(params.get('host'));
  if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||
     !(url.protocol==='https:'||(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname))))throw Error('Open this page from Devices & apps on your Amplifier service.');
  host=url.origin;$('#host').textContent='Service: '+host;$('#return').href=host+'/connect';$('#network-help').href=host+'/setup';
  await chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
  connection=(await chrome.storage.local.get('connection')).connection;
  if(connection?.host===host&&connection.token&&!connection.revoked){
   connection={...connection,setupId:params.get('setup')};await chrome.storage.local.set({connection});
   await done();
  }else if(connection?.token&&connection.host!==host){
   $('#controls').hidden=true;display('This preview is connected to another service. Remove that connection before using a different service.');
  }else{
   display('Connect to this service and review its request in your browser.');
   if(params.get('auto')==='1'&&await chrome.permissions.contains({origins:[host+'/*']}))await begin(false,true);
  }
 }catch(error){display(error.message);$('#controls').hidden=true}
}
$('#task-upgrade').onclick=()=>begin(true,false,true);
$('#connect').onclick=()=>begin();$('#upgrade').onclick=()=>begin(true);
$('#retry').onclick=async()=>{try{await done()}catch(error){display(error.message);$('#retry').hidden=false}};
$('#rotate').onclick=async()=>{
 try{
  const fresh=(await chrome.storage.local.get('connection')).connection;
  if(!fresh?.token)throw Error('Connect first.');
  await chrome.storage.local.set({connection:{...fresh,rotationId:crypto.randomUUID()}});
  await chrome.runtime.sendMessage({type:'resume'});display('Credential refresh requested. The connection will recover without pairing again.');
 }catch(error){display(error.message)}
};
initialize();
