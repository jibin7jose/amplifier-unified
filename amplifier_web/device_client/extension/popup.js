const $=selector=>document.querySelector(selector);
async function update(){
 const state=await chrome.runtime.sendMessage({type:'state'});
 const online=state.host&&!state.revoked&&!state.error&&Date.now()-(state.lastSeen||0)<45000;
 $('#status').textContent=state.revoked?'Access removed. Reconnect from your service.':state.host?(online?(state.target&&!state.stopped?'Selected: '+state.target:'Connected to '+state.host+'. Choose a tab to begin.'):'Access is saved, but the background connection is not ready.'):'Open Devices & apps on your Amplifier service to connect.';
 $('#use-task').disabled=!state.grants?.includes('browser.task')||state.revoked;$('#use').disabled=!state.host||state.revoked;$('#stop').disabled=!state.host||state.stopped;
 $('#retry').hidden=!state.host||state.revoked||online;
 $('#network-help').hidden=state.errorCode!=='connection_transport';
 if(state.host)$('#network-help').href=state.host+'/setup';
 if(state.host){$('#manage').href=state.host+'/connect';$('#manage').hidden=false}
 if(state.error)$('#status').textContent+=' '+state.error;
}
$('#use').onclick=async()=>{
 try{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  if(!tab)throw Error('Open the extension from the tab you want to use.');
  const result=await chrome.runtime.sendMessage({type:'use-tab',tabId:tab.id});await update();if(result.error)$('#status').textContent=result.error;
 }catch(error){$('#status').textContent=error.message}
};
$('#retry').onclick=async()=>{$('#retry').disabled=true;try{const result=await chrome.runtime.sendMessage({type:'check-connection'});await update();if(result.error)$('#status').textContent=result.error}finally{$('#retry').disabled=false}};
$('#stop').onclick=async()=>{await chrome.runtime.sendMessage({type:'stop'});await update()};
update();

$('#use-task').onclick=async()=>{try{const [tab]=await chrome.tabs.query({active:true,currentWindow:true});const result=await chrome.runtime.sendMessage({type:'use-task-tab',tabId:tab.id});await update();if(result.error)$('#status').textContent=result.error}catch(e){$('#status').textContent=e.message}};
