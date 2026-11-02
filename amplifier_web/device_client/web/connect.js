const $=selector=>document.querySelector(selector), prefix='/api/connections/v1';
let csrf='',snapshot={devices:[],operations:[]},setupId,activeOperation,stopped=false,running=false;
const notice=text=>{$('#notice').textContent=text};
async function api(path='',payload){
 const response=await fetch(prefix+path,{method:payload===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Connection-CSRF':csrf},...(payload===undefined?{}:{body:JSON.stringify(payload)})});
 const text=await response.text();
 if(!response.ok){let message=text;try{message=JSON.parse(text).error||text}catch{}throw Error(message||`Request failed (${response.status})`)}
 return JSON.parse(text);
}
function element(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node}
function target(){
 const [deviceId,targetId]=($('#target').value||'').split('/');
 const device=snapshot.devices.find(d=>d.id===deviceId),item=device?.targets.find(t=>t.id===targetId);
 if(!item)throw Error('Choose an available browser target.');
 return {deviceId,targetId,revision:item.revision};
}
async function operation(where,action,args={}){
 if(stopped)throw Error('Stopped before the next step.');
 const id=crypto.randomUUID();activeOperation=id;
 let receipt=await api('/operations',{id,...where,action,args});
 while(['queued','delivered'].includes(receipt.status)){
  await new Promise(resolve=>setTimeout(resolve,350));
  receipt=await api('/operations/'+id);
 }
 activeOperation=null;
 if(receipt.status!=='completed')throw Error(`${receipt.status}: ${receipt.detail||receipt.result?.message||'No confirmed result.'}`);
 return receipt.result;
}
async function refresh(){
 const data=await api('/devices');csrf=data.csrf;snapshot=data;
 const previous=$('#target').value,select=$('#target'),devices=$('#devices');select.replaceChildren();devices.replaceChildren();
 for(const device of data.devices){
  const row=element('article',undefined,'device'),heading=element('h3',device.name);
  heading.append(element('span',device.ready?'Ready':device.connected?'Connected':'Offline','badge'+(device.ready?' ready':'')));
  row.append(heading,element('p',device.grants.includes('browser.task')?'Browser access · selected tabs can navigate until Stop':device.grants.includes('browser.act')?'Browser access · only documents chosen locally':'Connection checks only','muted'));
  if(!device.connected)row.append(element('p','Open the connected app to bring it online.','muted'));
  else if(!device.targets.some(t=>t.ready&&t.capabilities.includes('browser.observe')))row.append(element('p','For browser tasks, choose Use this tab in the extension.','muted'));
  const actions=element('div',undefined,'actions'),pingTarget=device.targets.find(t=>t.ready&&t.capabilities.includes('connection.ping'));
  const ping=element('button','Check connection');ping.disabled=!pingTarget;
  ping.onclick=async()=>{try{const result=await operation({deviceId:device.id,targetId:pingTarget.id,revision:pingTarget.revision},'connection.ping');notice(result.message)}catch(error){notice(error.message)}};
  const remove=element('button','Remove access');remove.onclick=async()=>{try{await api('/devices/'+device.id+'/revoke',{});notice('Access removed.');await refresh()}catch(error){notice(error.message)}};
  actions.append(ping,remove);row.append(actions);devices.append(row);
  for(const item of device.targets.filter(t=>t.ready&&t.capabilities.includes('browser.observe'))){
   const option=element('option',`${device.name} · ${item.label}`);option.value=device.id+'/'+item.id;select.append(option);
  }
 }
 if(!data.devices.length)devices.append(element('p','No apps connected yet. Start with Open and connect above.','muted'));
 if(!select.children.length){const option=element('option','No browser target is ready');option.value='';select.append(option)}
 if([...select.options].some(option=>option.value===previous))select.value=previous;
 const full=data.devices.flatMap(d=>d.targets.map(t=>({...t,key:d.id+'/'+t.id}))).find(t=>t.key===select.value)?.capabilities.includes('browser.task');
 $('#observe').disabled=running||!select.value;$('#sample').disabled=running||!select.value||full;
 $('#task-hint').textContent=full?'This tab supports normal chat tasks across pages. Return to chat and choose it under Browser & computer.':'';
 const logs=$('#operations');logs.replaceChildren();
 for(const row of [...data.operations].reverse()){
  const details=element('details'),summary=element('summary',`${row.action} · ${row.status}`);
  details.append(summary,element('pre',JSON.stringify(row,null,2)));logs.append(details);
 }
 if(!data.operations.length)logs.append(element('p','Operations will appear here as you try a connection.'));
 if(setupId){try{const status=await api('/setups/'+setupId);$('#setup-state').textContent=({waiting_for_app:'Waiting for the app to start. If it is already connected, its status appears below.',waiting_for_approval:'Approve the matching code in the browser.',paired:'Approved. Waiting for the app to connect.',connected:'Connected. Choose a browser tab in the extension.',ready:'Ready. Your connection is available below.',denied:'Connection denied. You can start again.',revoked:'Access was removed.'})[status.status]||status.status}catch{$('#setup-state').textContent='Setup expired. Choose Open and connect to try again.';setupId=null}}
}
$('#open-extension').onclick=async()=>{
 try{
  const catalog=await(await fetch('/connect/catalog')).json();
  if(!globalThis.chrome?.runtime?.sendMessage)throw Error('Install the extension preview, then reload this page and choose Open and connect.');
  const setup=await api('/setups',{});setupId=setup.id;
  await new Promise((resolve,reject)=>chrome.runtime.sendMessage(catalog.extensionId,{type:'open-connect',setupId},response=>{
   if(chrome.runtime.lastError)reject(Error('The extension is not available. Install the preview and reload this page.'));
   else if(response?.opened)resolve();else reject(Error(response?.error||'Could not open the extension.'));
  }));
  notice('The app will guide you through connecting to this service.');await refresh();
 }catch(error){notice(error.message)}
};
async function run(sample){
 running=true;stopped=false;$('#cancel').hidden=false;$('#sample').disabled=true;$('#observe').disabled=true;$('#result').hidden=true;
 try{
  const where=target();$('#task-status').textContent='Inspecting the selected browser document…';
  const full=snapshot.devices.find(d=>d.id===where.deviceId)?.targets.find(t=>t.id===where.targetId)?.capabilities.includes('browser.task');
  let result=await operation(where,full?'browser.task.observe':'browser.observe');
  if(sample&&full)throw Error('Use normal chat for this tab, or choose document-only access to run this sample.');
  if(sample){
   const url=new URL(result.url);
   if(url.origin!==location.origin||url.pathname!=='/connect/sample')throw Error('Select the sample form in the extension before running this test.');
   for(const [action,args,description] of [
    ['browser.type',{selector:'#name',text:'Ada'},'Entering Ada…'],
    ['browser.click',{selector:'#blue'},'Choosing Team Blue…'],
    ['browser.click',{selector:'#save'},'Saving the selection…'],
   ]){$('#task-status').textContent=description;await operation(where,action,args)}
   result=await operation(where,'browser.observe');
   if(!result.text.includes('Saved Ada blue.'))throw Error('The final observation did not confirm the saved selection.');
  }
  $('#result').textContent=JSON.stringify(result,null,2);$('#result').hidden=false;
  $('#task-status').textContent=sample?'Verified: Saved Ada blue.':'Current browser observation received.';
 }catch(error){$('#task-status').textContent=error.message}
 finally{running=false;$('#cancel').hidden=true;await refresh().catch(error=>notice(error.message))}
}
$('#target').onchange=()=>refresh();
$('#observe').onclick=()=>run(false);$('#sample').onclick=()=>run(true);
$('#cancel').onclick=async()=>{stopped=true;if(activeOperation)await api('/operations/'+activeOperation+'/cancel',{}).catch(error=>notice(error.message));$('#task-status').textContent='Stopping. An already delivered action may have happened.'};
async function update(){try{await refresh()}catch(error){notice(error.message)}finally{setTimeout(update,2500)}}
update();
