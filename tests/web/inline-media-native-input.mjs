// Opt-in guest-native interactions; no DOM state setters or host desktop access.
import {execFileSync,spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {readFileSync,appendFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {chromium} from '@playwright/test';
export const nativeInput=[];
let binding,inputUncertain=false;
function journal(row){
 nativeInput.push(row);
 appendFileSync(join(process.env.INLINE_MEDIA_EVIDENCE_DIR,'native-input.jsonl'),JSON.stringify(row)+'\n');
}
export async function bounded(promise,label){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{
  timer=setTimeout(()=>reject(Error(`${label} exceeded 10 seconds`)),10000);
 })])}finally{clearTimeout(timer)}
}
export async function launchNativeBrowser(){
 assert.ok(process.env.AMPLIFIER_TEST_CONTRACT&&process.env.DISPLAY,'Owned managed guest display required');
 const contract=JSON.parse(readFileSync(process.env.AMPLIFIER_TEST_CONTRACT,'utf8'));
 assert.equal(contract.profileId,'browser-test');assert.equal(contract.workspace,'/workspace');
 assert.match(process.env.DISPLAY,/^:\d+(?:\.\d+)?$/,'Guest-local display only');
 const profile=await mkdtemp(join(tmpdir(),'inline-media-browser-'));
 const child=spawn(chromium.executablePath(),['--no-sandbox','--no-first-run','--no-default-browser-check',
  '--mute-audio','--force-device-scale-factor=1','--window-size=1280,1050',
  '--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',`--user-data-dir=${profile}`,'about:blank'],
 {stdio:['ignore','ignore','pipe']});
 const exited=new Promise(resolve=>{
  child.once('exit',(code,signal)=>resolve({code,signal}));
  child.once('error',error=>resolve({code:null,spawnError:error.message}));
 });
 let stderr='',browser;
 const startTime=()=>readFileSync(`/proc/${child.pid}/stat`,'utf8').split(')').at(-1).trim().split(/\s+/)[19];
 async function settleProcess(errors){
  let result;
  try{result=await bounded(exited,'Browser process exit')}
  catch(error){
   errors.push(error.message);
   try{
    assert.equal(startTime(),binding?.startTime,'Refuse signaling changed process identity');
    child.kill('SIGTERM');
    result=await bounded(exited,'Owned browser termination');
   }catch(termination){errors.push(termination.message)}
  }
  let profileRemoved=false;
  if(result)try{await rm(profile,{recursive:true});profileRemoved=true}catch(error){errors.push(error.message)}
  return {process:result||null,processExited:!!result&&!result.spawnError,
   profileRemoved,custody:profileRemoved?null:{pid:child.pid,profile},errors};
 }
 try{
  const endpoint=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('Guest Chromium endpoint unavailable')),10000);
   child.once('error',error=>{clearTimeout(timer);reject(error)});
   child.stderr.on('data',chunk=>{
    stderr=(stderr+chunk).slice(-8000);
    const match=stderr.match(/DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/devtools\/browser\/\S+)/);
    if(match){clearTimeout(timer);resolve(match[1])}
   });
  });
  // This documented option leaves the default context's native focus/visibility
  // alone. newContext() would re-enable Playwright's focus emulation.
  binding={pid:child.pid,startTime:startTime(),profile,display:process.env.DISPLAY};
  journal({route:'owned-browser-process',...binding});
  browser=await chromium.connectOverCDP(endpoint,{noDefaults:true});
  const context=browser.contexts()[0];assert.ok(context);
  journal({route:'owned-Chromium-CDP',noDefaults:true,display:process.env.DISPLAY,deviceScaleFactor:1});
  return {browser,context,keepAlive:context.pages()[0],async close(){
   const errors=[];
   try{
    const cdp=await bounded(browser.newBrowserCDPSession(),'Browser cleanup session');
    await bounded(cdp.send('Browser.close'),'Browser close request');
   }catch(error){errors.push(error.message)}
   const result=await settleProcess(errors);
   try{await bounded(browser.close(),'Browser disconnect')}catch(error){errors.push(error.message)}
   result.status=result.process?.code===0&&result.profileRemoved&&!errors.length?'PASS':'FAIL';
   journal({route:'owned-browser-cleanup',...result});
   if(result.status!=='PASS')throw Object.assign(Error('Native browser cleanup incomplete'),{cleanup:result});
   return result;
  }};
 }catch(error){
  // child is our spawn handle; do not signal any discovered or peer process.
  if(child.pid&&child.exitCode===null)child.kill('SIGTERM');
  error.cleanup=await settleProcess([]);
  throw error;
 }
}
function chord(keys,extraEnv={}){
 assert.ok(binding,'Exact browser binding required');
 assert.equal(inputUncertain,false,'Previous native input is uncertain; do not send more keys');
 journal({route:'guest-X11-XTEST',status:'attempted',keys,pid:binding.pid});
 let row;
 try{
  row=JSON.parse(execFileSync(process.env.AMPLIFIER_TEST_PYTHON,
   [fileURLToPath(new URL('./inline-media-x11.py',import.meta.url)),...keys],
   {encoding:'utf8',timeout:5000,env:{...process.env,INLINE_MEDIA_X11_BINDING:JSON.stringify(binding),...extraEnv}}));
 }catch(error){
  try{row=JSON.parse(String(error.stdout))}catch{row={route:'guest-X11-XTEST',status:'uncertain',keys,error:error.message}}
 }
 inputUncertain=row.status==='uncertain';journal(row);
 return row;
}
export function nativeKey(...keys){
 const row=chord(keys);
 if(row.status!=='completed')throw Object.assign(Error(`Native input ${row.status}: ${row.error||''}`),{input:row});
 return row;
}
export function wrongWindowControl(){
 const row=chord(['space'],{INLINE_MEDIA_EXPECT_WINDOW:'1'});
 assert.equal(row.status,'rejected');assert.deepEqual(row.pressed,[]);
 assert.match(row.error,/Expected owned X window mismatch/);return row;
}
export const zoomMetrics=page=>page.evaluate(()=>({dpr:devicePixelRatio,innerWidth,outerWidth,
 screenWidth:screen.width,screenHeight:screen.height,visualScale:visualViewport.scale,
 scrollWidth:document.documentElement.scrollWidth,hidden:document.hidden,visibilityState:document.visibilityState}));
export async function nativeTab(page){
 await page.bringToFront();
 const context=page.context(),opened=context.waitForEvent('page',{timeout:5000});
 // Observe rejection before input can throw. Keep the original promise so a
 // page-wait failure still propagates when the chord itself succeeds.
 void opened.catch(()=>{});
 nativeKey('Control_L','t');
 return opened;
}
export async function nativeControl(page,selector,role,name){
 const cdp=await page.context().newCDPSession(page);
 try{
  const {root}=await cdp.send('DOM.getDocument');
  const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector});
  assert.ok(nodeId,'The exact video must exist');
  const {node}=await cdp.send('DOM.describeNode',{nodeId,depth:-1,pierce:true});
  const owned=new Map();
  function visit(row){owned.set(row.backendNodeId,row);for(const child of [...row.children||[],...row.shadowRoots||[]])visit(child)}
  for(const shadow of node.shadowRoots||[]){assert.equal(shadow.shadowRootType,'user-agent');visit(shadow)}
  const {nodes}=await cdp.send('Accessibility.getFullAXTree');
  const matches=nodes.filter(row=>!row.ignored&&row.role?.value===role&&name.test(row.name?.value||'')&&owned.has(row.backendDOMNodeId));
  assert.equal(matches.length,1,`Need one actual UA ${role} ${name}`);
  const match=matches[0],backendNodeId=match.backendDOMNodeId;
  const attrs=owned.get(backendNodeId).attributes||[];
  const pseudo=attrs[attrs.indexOf('pseudo')+1];
  assert.match(pseudo||'',/media-controls/);
  await cdp.send('DOM.focus',{backendNodeId});
  const focused=(await cdp.send('Accessibility.getFullAXTree')).nodes.find(row=>row.backendDOMNodeId===backendNodeId);
  assert.ok(focused.properties.some(p=>p.name==='focused'&&p.value.value===true),'Actual native control must own focus');
  const result={role:match.role.value,name:match.name.value,backendNodeId,pseudo,focused:true};
  nativeInput.push({route:'CDP-UA-accessibility-focus',...result});return result;
 }finally{await cdp.detach()}
}
