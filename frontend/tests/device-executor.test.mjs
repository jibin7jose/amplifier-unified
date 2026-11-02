import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const background=(await readFile(new URL('../../amplifier_web/device_client/extension/background.js',import.meta.url),'utf8')).replace(/resume\(\);\s*$/,'');
const executor=await readFile(new URL('../../amplifier_web/device_client/extension/task-browser.js',import.meta.url),'utf8');
function worker(){
 const connection={host:'https://service.example',token:'fixture',deviceID:'device',grants:['connection.ping'],
  target:{id:'target',documentId:'document',tabId:42}};
 const values={connection},session={},calls=[],writes=[],alarms=[],listener={addListener(){}};
 const storage=(area,label)=>({setAccessLevel:async()=>{},get:async()=>({...area}),set:async patch=>{writes.push({area:label,patch});Object.assign(area,patch)},remove:async key=>{delete area[key]}});
 const context=vm.createContext({URL,Date,Error,Number,TextEncoder,crypto:globalThis.crypto,importScripts(){},clearTimeout(){},setTimeout(){return 1},detachTask:async()=>{},
  chrome:{storage:{local:storage(values,'local'),session:storage(session,'session')},
   alarms:{create(name,options){alarms.push({name,options})},onAlarm:listener},runtime:{onMessage:listener,onMessageExternal:listener,onStartup:listener},action:{setBadgeText:async()=>{}},
   scripting:{executeScript:async({func,args})=>[{documentId:'document',result:func(...args)}]}},
  connectionRequest:async(host,path,payload)=>{calls.push({path,payload});if(path==='/self/heartbeat')return {grants:connection.grants};
   if(path==='/self/poll')return {operation:{id:'operation',lease:'lease',targetId:'target',revision:'document',action:'browser.observe',deadline:Date.now()/1000+15,retention:'transient'}};
   if(path==='/self/result')return {status:'completed'};if(path==='/self/stop')return {};throw Error('Unexpected request '+path)}});
 vm.runInContext(background,context);
 return {context,connection,values,session,calls,writes,alarms};
}

test('Document observer excludes CSS-hidden, aria-hidden, inert and sensitive controls',async()=>{
 const {context,connection}=worker();
 const control=(id,patch={})=>({id,tagName:'INPUT',type:'text',value:id+'-value',innerText:'',placeholder:'',name:id,
  getBoundingClientRect:()=>({width:100,height:20}),getAttribute:()=>null,closest:()=>null,...patch});
 const visible=control('visible'),hidden=control('hidden',{display:'none'}),invisible=control('invisible',{visibility:'hidden'});
 const nodes=[visible,hidden,invisible,control('inert',{closest:()=>({})}),control('aria-hidden',{closest:()=>({})}),
  control('password',{type:'password'}),control('token',{type:'hidden'}),control('file',{type:'file'}),control('zero',{getBoundingClientRect:()=>({width:0,height:0})})];
 context.document={title:'Fixture',body:{innerText:'Visible page'},querySelectorAll:()=>nodes};
 context.location={href:'https://fixture.example/'};context.CSS={escape:value=>value};
 context.getComputedStyle=node=>({display:node.display||'block',visibility:node.visibility||'visible'});
 context.connection=connection;context.operation={targetId:'target',revision:'document',action:'browser.observe',args:{},deadline:Date.now()/1000+15};
 const result=await vm.runInContext('execute(connection,operation)',context);
 assert.deepEqual(Array.from(result.controls,c=>c.value),['visible-value']);
 assert.equal(JSON.stringify(result).includes('password-value'),false);
});

for(const [name,result,expected] of [['oversized',{text:'x'.repeat(262144)},'failed'],['UTF8',{text:'🙂'.repeat(60000)},'completed']])
 test(`Result preflight delivers a bounded ${name} receipt without repeating execution`,async()=>{
  const {context,values,session,calls,writes,alarms}=worker();let executions=0;
  context.execute=async()=>{executions++;return result};
  await vm.runInContext('resume()',context);
  const sent=calls.find(row=>row.path==='/self/result').payload;
  assert.equal(sent.status,expected);assert.equal(executions,1);assert.equal(values.pendingReceipt,undefined);
  assert.equal(session.pendingReceipt,undefined);
  assert.equal(writes.some(row=>row.area==='local'&&row.patch.pendingReceipt),false);
  const receipt=writes.find(row=>row.area==='session'&&row.patch.pendingReceipt).patch.pendingReceipt;
  assert.equal(receipt.expiresAt-receipt.createdAt,60000);
  assert.equal(alarms.find(row=>row.name==='receipt-expiry').options.when,receipt.expiresAt);
  assert.ok(new TextEncoder().encode(JSON.stringify(sent.result)).length<=262144);
  if(expected==='failed')assert.match(sent.result.message,/too large/);
 });

test('Stop during capture does not retain or deliver its transient result',async()=>{
 const {context,calls,writes}=worker();
 context.execute=async()=>{await vm.runInContext('stop()',context);return {image:'fixture-image'}};
 await vm.runInContext('resume()',context);
 assert.equal(writes.some(row=>row.patch.pendingReceipt),false);
 assert.equal(calls.some(row=>row.path==='/self/result'),false);
});

async function screenshot(sizes,{stopAfterFirst=false}={}){
 const captures=[];
 const context=vm.createContext({URL,Date,Number,Error,pausedLocally:false,stop:async()=>{},
  chrome:{debugger:{onDetach:{addListener(){}},sendCommand:async(target,method,args)=>{
   if(method==='Page.getFrameTree')return {frameTree:{frame:{id:'frame',loaderId:'document',url:'https://fixture.example/'}}};
   if(method==='Page.createIsolatedWorld')return {executionContextId:1};
   if(method==='Page.getLayoutMetrics')return {cssVisualViewport:{clientWidth:1440,clientHeight:1000,pageX:0,pageY:0}};
   if(method==='Page.captureScreenshot'){captures.push(args);if(stopAfterFirst)context.pausedLocally=true;return {data:'a'.repeat(sizes[captures.length-1])}}
   throw Error('Unexpected debugger command '+method);
  }}}});
 vm.runInContext(executor,context);vm.runInContext('attachedTab=42',context);
 context.connection={host:'https://service.example',grants:['browser.task']};
 context.operation={action:'browser.task.screenshot',deadline:Date.now()/1000+15,args:{}};
 const result=await vm.runInContext('executeTask(connection,operation,{tabId:42})',context);
 return {result,captures};
}
test('Screenshot retries with lower JPEG quality and scale under the fixed envelope',async()=>{
 const {result,captures}=await screenshot([240001,200000]);
 assert.equal(result.image.length,200000);assert.equal(result.mimeType,'image/jpeg');
 assert.deepEqual(captures.map(row=>row.quality),[55,40]);
 assert.equal(result.width,960);assert.ok(captures[1].clip.scale<captures[0].clip.scale);
});
test('Three oversized captures return explicit recovery without an oversized result',async()=>{
 const {result,captures}=await screenshot([240001,240001,240001]);
 assert.equal(captures.length,3);assert.equal(result.status,'blocked');assert.equal(result.reason,'screenshot_size_limit');
 assert.ok(result.recovery);assert.equal(result.image,undefined);
});
test('Local Stop prevents another screenshot retry',async()=>{
 await assert.rejects(screenshot([240001],{stopAfterFirst:true}),/stopped/);
});

for(const phase of ['layout','capture'])for(const change of ['excluded','other-document','same-document-excluded','reattach'])
 test(`Screenshot discards ${change} during ${phase}`,async()=>{
  let url='https://fixture.example/',loader='document';const calls=[];
  const context=vm.createContext({URL,Date,Number,Error,pausedLocally:false,stop:async()=>{},
   chrome:{debugger:{onDetach:{addListener(){}},sendCommand:async(target,method,args)=>{
    calls.push(method);
    if(method==='Page.getFrameTree')return {frameTree:{frame:{id:'frame',loaderId:loader,url}}};
    if(method==='Page.createIsolatedWorld')return {executionContextId:1};
    if(method===(phase==='layout'?'Page.getLayoutMetrics':'Page.captureScreenshot')){
     if(change==='reattach')vm.runInContext('attachmentVersion++',context);
     else{url=change==='other-document'?'https://other.example/':'https://service.example/settings';if(change!=='same-document-excluded')loader='new-document';}
    }
    if(method==='Page.getLayoutMetrics')return {cssVisualViewport:{clientWidth:1280,clientHeight:900,pageX:0,pageY:0}};
    if(method==='Page.captureScreenshot')return {data:'EXCLUDED-PIXELS'};
    throw Error('Unexpected debugger command '+method);
   }}}});
  vm.runInContext(executor,context);vm.runInContext('attachedTab=42',context);
  context.connection={host:'https://service.example',grants:['browser.task']};
  context.operation={action:'browser.task.screenshot',deadline:Date.now()/1000+15,args:{}};
  await assert.rejects(vm.runInContext('executeTask(connection,operation,{tabId:42})',context),/excluded|changed|stopped/);
  assert.equal(calls.filter(method=>method==='Page.captureScreenshot').length,phase==='layout'?0:1);
 });
