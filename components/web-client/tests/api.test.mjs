import test from 'node:test';
import assert from 'node:assert/strict';
import {request,visibleView} from '../src/api.js';

test('commands send structured JSON to same origin and return server receipt',async()=>{
 const original=globalThis.fetch;
 try {globalThis.fetch=async(path,options)=>{assert.equal(path,'/api/actions');assert.equal(options.credentials,'same-origin');assert.equal(options.headers['Content-Type'],'application/json');assert.deepEqual(JSON.parse(options.body),{action:'conversation.send',args:{text:'Hello'}});return new Response(JSON.stringify({accepted:true,revision:4}))};assert.deepEqual(await request('/api/actions',{method:'POST',body:{action:'conversation.send',args:{text:'Hello'}}}),{accepted:true,revision:4});}finally{globalThis.fetch=original}
});
test('server validation errors remain visible and do not report success',async()=>{
 const original=globalThis.fetch;try{globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,error:'Select a conversation first.'}),{status:400});await assert.rejects(request('/api/actions',{method:'POST',body:{}}),/Select a conversation first/);}finally{globalThis.fetch=original}
});
test('rejection identifies its request endpoint without retaining private query context',async()=>{
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({error:'Permission denied'}),{status:403});
  for(const path of ['/api/actions','/api/state'])await assert.rejects(request(path+'?private=do-not-retain'),error=>{
   assert.equal(error.requestPath,path);assert.equal(error.status,403);assert.ok(!JSON.stringify(error).includes('do-not-retain'));return true;
  });
 }finally{globalThis.fetch=original}
});
test('unexpected HTML response gives useful connection error',async()=>{
 const original=globalThis.fetch;try{globalThis.fetch=async()=>new Response('<html>proxy failure</html>',{status:502});await assert.rejects(request('/api/state'),/unexpected response \(502\)/);}finally{globalThis.fetch=original}
});
test('unreadable acknowledgements keep mutations unconfirmed and never replay',async()=>{
 const original=globalThis.fetch;let calls=0;
 try{
  for(const [status,body] of [[502,'<html>proxy failure</html>'],[200,'{"accepted":'],[200,''],[200,'null'],[200,'{}'],[200,'[]'],[200,'{"accepted":"true"}'],[502,''],[502,'{"error":{}}'],[502,'{"message":true}']]){
   globalThis.fetch=async()=>{calls++;return new Response(body,{status})};
   await assert.rejects(request('/api/actions',{method:'POST',body:{action:'canvas.views.loadVersion'}}),error=>{
    assert.equal(error.unconfirmed,true,`Unreadable ${status} acknowledgement is not a refusal`);
    assert.equal(error.code,'invalid_response');assert.equal(error.status,status);return true;
   });
  }
  assert.equal(calls,10);
  globalThis.fetch=async()=>new Response('<html>proxy failure</html>',{status:502});
  await assert.rejects(request('/api/state'),error=>error.code==='invalid_response'&&error.unconfirmed===false);
  for(const status of [409,200]){
   globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,status:409,error:'Unfinished edits',code:'dirty_view'}),{status});
   await assert.rejects(request('/api/actions',{method:'POST',body:{action:'canvas.views.loadVersion'}}),error=>error.status===409&&!error.unconfirmed&&error.code==='dirty_view');
  }
 }finally{globalThis.fetch=original}
});
test('live timers anchor to the host response clock instead of a skewed device clock',async()=>{
 const {hostNow}=await import('../src/api.js'),original=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response('{}',{headers:{date:'Sun, 20 Sep 2026 12:00:00 GMT'}});
  await request('/api/state');const anchored=Date.parse('2026-09-20T12:00:00Z')/1000;
  assert.ok(Math.abs(hostNow()-anchored)<1);
 }finally{globalThis.fetch=original}
});

test('an older host date arriving after a newer response cannot rewind elapsed time',async()=>{
 const {hostNow}=await import('../src/api.js'),{elapsedLabel}=await import('../src/timeline-data.js'),original=globalThis.fetch;
 let finishSlow;
 try{
  globalThis.fetch=path=>path==='/api/slow'?new Promise(resolve=>finishSlow=resolve):Promise.resolve(new Response('{}',{headers:{date:'Thu, 20 Jan 2050 12:00:10 GMT'}}));
  const slow=request('/api/slow');await request('/api/fast');
  const before=hostNow(),record={phase:'running',startedAt:Date.parse('2050-01-20T12:00:00Z')/1000},elapsed=elapsedLabel(record,before);
  finishSlow(new Response('{}',{headers:{date:'Thu, 20 Jan 2050 12:00:05 GMT'}}));await slow;
  assert.ok(hostNow()>=before);assert.equal(elapsedLabel(record,hostNow()),elapsed);
 }finally{globalThis.fetch=original}
});

test('publishing rejection preserves authoritative unknown receipt even on HTTP409',async()=>{
 const original=globalThis.fetch,receipt={requestId:'admitted-import',state:'unknown',reconciliationError:{code:'invalid_response'}};
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,error:'The remote receipt is malformed',code:'invalid_response',receipt}),{status:409});
  await assert.rejects(request('/api/actions',{method:'POST',body:{}}),error=>{
   assert.equal(error.status,409);assert.equal(error.code,'invalid_response');assert.deepEqual(error.receipt,receipt);return true;
  });
 }finally{globalThis.fetch=original}
});

test('visible view recognizes the WCO display mode as installed',()=>{
 const values={window:{matchMedia:query=>({matches:query==='(display-mode: window-controls-overlay)'}),getSelection:()=>null},navigator:{onLine:true},document:{createTreeWalker:()=>({nextNode:()=>null})},NodeFilter:{SHOW_ELEMENT:1,SHOW_TEXT:4},innerWidth:1000,innerHeight:800,scrollX:0,scrollY:0,location:{pathname:'/'}};
 const descriptors=Object.fromEntries(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 try{
  for(const [key,value] of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
  const root={closest:()=>null,querySelectorAll:()=>[],querySelector:()=>null,innerText:''};
  assert.equal(visibleView(root,'test-client').webApp.standalone,true);
  window.matchMedia=()=>({matches:false});
  assert.equal(visibleView(root,'test-client').webApp.standalone,false);
 }finally{
  for(const [key,descriptor] of Object.entries(descriptors))if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
 }
});

test('transport failures distinguish interrupted reads from unconfirmed writes without replay',async()=>{
 const original=globalThis.fetch;let calls=0;
 try{
  globalThis.fetch=async()=>{calls++;throw new TypeError('Failed to fetch')};
  await assert.rejects(request('/api/state'),error=>error.code==='transport_unavailable'&&!error.unconfirmed);
  await assert.rejects(request('/api/actions',{method:'POST',body:{action:'session.pin'}}),error=>error.code==='transport_unavailable'&&error.unconfirmed);
  assert.equal(calls,2);
 }finally{globalThis.fetch=original}
});
test('disconnect while reading an acknowledgement is still an unconfirmed write',async()=>{
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async()=>({headers:new Headers(),text:async()=>{throw new TypeError('Load failed')}});
  await assert.rejects(request('/api/actions',{method:'POST',body:{}}),error=>error.code==='transport_unavailable'&&error.unconfirmed);
 }finally{globalThis.fetch=original}
});
test('intentional aborts and server rejections retain their original meaning',async()=>{
 const original=globalThis.fetch,abort=new DOMException('Cancelled','AbortError');
 try{
  globalThis.fetch=async()=>{throw abort};
  await assert.rejects(request('/api/state'),error=>error===abort);
  globalThis.fetch=async()=>new Response(JSON.stringify({error:'Permission denied'}),{status:403});
  await assert.rejects(request('/api/actions',{method:'POST'}),error=>error.status===403&&error.code!=='transport_unavailable');
 }finally{globalThis.fetch=original}
});

test('support timings retain correlation and exclude query strings and bodies',async()=>{
 const {recentRequestTimings}=await import('../src/api.js');
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response('{"accepted":true}',{headers:{'Server-Timing':'app;dur=12.3','X-Amplifier-Request-Id':'request-1'}});
  await request('/api/actions?private=secret',{method:'POST',body:{action:'session.create',args:{text:'private prompt'}}});
  const row=recentRequestTimings().at(-1);
  assert.equal(row.serverMs,12.3);assert.equal(row.requestId,'request-1');assert.equal(row.action,'session.create');
  assert.equal(row.route,'/api/actions');assert.ok(row.elapsedMs>=0);
  assert.ok(!JSON.stringify(row).includes('private'));
 }finally{globalThis.fetch=original}
});

test('history authentication changes invalidate once and ignore older in-flight responses',async()=>{
 const api=await import('../src/api.js?history-auth-test'),original=globalThis.fetch;
 const changes=[],stop=api.onAuthenticationChange(scope=>changes.push(scope));
 const response=scope=>new Response(JSON.stringify({accepted:true,state:{client:{kind:'web'}}}),{headers:{'X-Amplifier-History-Scope':scope}});
 let release;
 try{
  globalThis.fetch=async()=>response('scope-a');
  assert.equal((await api.request('/api/actions')).state.client.historyScope,'scope-a');
  assert.equal(changes.length,0);
  globalThis.fetch=()=>new Promise(resolve=>{release=resolve});
  const old=api.request('/api/actions');
  globalThis.fetch=async()=>response('scope-b');
  assert.equal((await api.request('/api/actions')).state.client.historyScope,'scope-b');
  assert.deepEqual(changes,['scope-b']);
  release(response('scope-a'));
  assert.equal((await old).state.client.historyScope,undefined);
  await api.request('/api/actions');assert.equal(changes.length,1);
  globalThis.fetch=async()=>new Response('{"error":"Authentication required."}',{status:401});
  await assert.rejects(api.request('/api/state'),error=>error.status===401);
  await assert.rejects(api.request('/api/state'),error=>error.status===401);
  assert.deepEqual(changes,['scope-b',null]);
 }finally{stop();globalThis.fetch=original}
});
