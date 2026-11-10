import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../../amplifier_web/device_client/extension/background.js',import.meta.url),'utf8')).replace(/resume\(\);\s*$/,'');

function worker(retention,status,{age=0,localTransient=false,connectionPatch={},receiptPatch={},startWithReceipt=true,deliveries=[],resultResponse}={}){
 let now=1700000000000;
 const values={connection:{host:'https://service.example',token:'private-test-token',deviceID:'device',grants:['connection.ping'],...connectionPatch}},session={};
 const pending={host:'https://service.example',deviceID:'device',id:'receipt',lease:'lease',status:'completed',result:{image:'fixture-image'},retention,
  ...(retention==='transient'?{createdAt:now-age,expiresAt:now-age+60000}:{}),...receiptPatch};
 if(startWithReceipt)(retention==='transient'&&!localTransient?session:values).pendingReceipt=pending;
 const calls=[],resultPosts=[],delays=[],alarms=new Map(),alarmListeners=[];
 const listener={addListener(){}};
 const storage=area=>({setAccessLevel:async()=>{},get:async()=>({...area}),set:async patch=>Object.assign(area,patch),remove:async key=>{delete area[key]}});
 const context=vm.createContext({URL,Date:class extends Date{static now(){return now}},Error,Number,TextEncoder,crypto:globalThis.crypto,importScripts(){},clearTimeout(){},
  setTimeout(fn,delay){delays.push(delay);return 1},detachTask:async()=>{},
  chrome:{storage:{local:storage(values),session:storage(session)},
   alarms:{create(name,options){alarms.set(name,options)},onAlarm:{addListener(fn){alarmListeners.push(fn)}}},runtime:{onMessage:listener,onMessageExternal:listener,onStartup:listener},action:{setBadgeText:async()=>{}}},
  connectionRequest:async(host,path,payload)=>{calls.push(path);if(path==='/self/result'){
    resultPosts.push(JSON.parse(JSON.stringify(payload)));
    if(resultResponse)return resultResponse(payload,resultPosts.length);
    if(status===undefined)throw new TypeError('Failed to fetch');
    const error=Error('Receipt unavailable');error.status=status;throw error;
   }
   if(path==='/self/heartbeat')return {grants:['connection.ping']};if(path==='/self/poll')return {operation:deliveries.shift()||null};if(path==='/self/stop')return {};throw Error('Unexpected request '+path)}});
 vm.runInContext(source,context);
 return {values,session,calls,resultPosts,delays,alarms,context,advance(ms){now+=ms},alarm:name=>Promise.all(alarmListeners.map(fn=>fn({name}))),run:code=>vm.runInContext(code,context)};
}

for(const status of [404,410])test(`Expired transient receipt ${status} clears and resumes without replay`,async()=>{
 const w=worker('transient',status);await w.run('resume()');
 assert.equal(w.session.pendingReceipt,undefined);assert.equal(w.values.pendingReceipt,undefined);
 assert.deepEqual(w.calls,['/self/result','/self/heartbeat','/self/poll']);
});
const retentionKinds=['transient','durable',undefined,'future-retention'];
const retainedFailures=[
 ...retentionKinds.flatMap(retention=>[403,undefined].map(status=>[retention,status])),
 ...['durable',undefined,'future-retention'].flatMap(retention=>[404,410].map(status=>[retention,status])),
 ['transient',500],
];
for(const [retention,status] of retainedFailures)test(`Retain ${retention||'legacy'} receipt on ${status??'network failure'}`,async()=>{
 const w=worker(retention,status);await w.run('resume()');
 assert.ok((retention==='transient'?w.session:w.values).pendingReceipt);
 assert.deepEqual(w.calls,['/self/result']);assert.deepEqual(w.delays,[5000]);
});

test('Offline transient result expires before another send and is never persisted locally',async()=>{
 const w=worker('transient',500);await w.run('resume()');
 assert.equal(w.values.pendingReceipt,undefined);assert.ok(w.session.pendingReceipt);
 w.advance(60000);await w.run('resume()');
 assert.equal(w.session.pendingReceipt,undefined);
 assert.deepEqual(w.calls,['/self/result','/self/heartbeat','/self/poll']);
});
test('Expiry alarm clears transient memory without networking while the worker is busy',async()=>{
 const w=worker('transient',500);w.advance(60000);w.run('busy=true');
 await w.alarm('receipt-expiry');
 assert.equal(w.session.pendingReceipt,undefined);assert.deepEqual(w.calls,[]);
});
for(const receiptPatch of [{createdAt:undefined},{expiresAt:undefined},{expiresAt:1700000060001},{createdAt:1700000000001}])
 test(`Invalid transient lifetime is discarded before network delivery ${JSON.stringify(receiptPatch)}`,async()=>{
  const w=worker('transient',500,{receiptPatch});await w.run('resume()');
  assert.equal(w.session.pendingReceipt,undefined);assert.deepEqual(w.calls,['/self/heartbeat','/self/poll']);
 });

test('Old-version transient disk result is discarded without reading it onto the wire',async()=>{
 const w=worker('transient',500,{localTransient:true});await w.run('resume()');
 assert.equal(w.values.pendingReceipt,undefined);assert.deepEqual(w.calls,['/self/heartbeat','/self/poll']);
});
for(const retention of retentionKinds)test(`Local Stop clears only transient receipts (${retention||'legacy'})`,async()=>{
 const w=worker(retention,500);await w.run('stop()');
 assert.equal(w.session.pendingReceipt,undefined);
 assert.equal(Boolean(w.values.pendingReceipt),retention!=='transient');
 assert.equal(w.values.connection.stopped,true);assert.deepEqual(w.calls,['/self/stop']);
});
for(const connectionPatch of [{token:null},{revoked:true},{stopped:true},{deviceID:'other-device'}])
 test(`Disconnect/stopped/changed enrollment clears transient result ${JSON.stringify(connectionPatch)}`,async()=>{
  const w=worker('transient',500,{connectionPatch});await w.run('resume()');
  assert.equal(w.session.pendingReceipt,undefined);assert.equal(w.calls.includes('/self/result'),false);
 });
for(const retention of ['durable',undefined,'future-retention'])test(`Different enrollment preserves ${retention||'legacy'} receipt without sending or replaying`,async()=>{
 const w=worker(retention,500,{connectionPatch:{deviceID:'new-device'}});await w.run('resume()');
 assert.ok(w.values.pendingReceipt);assert.deepEqual(w.calls,[]);assert.match(w.values.connection.error,/earlier connection/);
});
for(const retention of retentionKinds)test(`Revocation clears only transient receipts (${retention||'legacy'})`,async()=>{
 const w=worker(retention,401);await w.run('resume()');
 assert.equal(w.session.pendingReceipt,undefined);assert.equal(Boolean(w.values.pendingReceipt),retention!=='transient');
 assert.equal(w.values.connection.revoked,true);assert.deepEqual(w.calls,['/self/result']);
});

for(const retention of retentionKinds)test(`Lost acknowledgement retries only the ${retention||'legacy'} receipt before allowing more work`,async()=>{
 let acknowledge,requestStarted;
 const retryStarted=new Promise(resolve=>{requestStarted=resolve});
 const deliveries=['first','second'].map(id=>({id,lease:'lease-'+id,action:retention==='transient'?'connection.ping':'browser.click',retention,deadline:1700000015}));
 const executed=[];
 const w=worker(retention,undefined,{startWithReceipt:false,deliveries,resultResponse:(payload,attempt)=>{
  // The service accepted the first receipt, but its acknowledgement was lost.
  if(attempt===1)throw new TypeError('Failed to fetch');
  if(attempt===2)return new Promise(resolve=>{acknowledge=resolve;requestStarted()});
  return {status:'completed'};
 }});
 w.context.execute=async(connection,operation)=>{
  assert.equal(w.values.pendingReceipt,undefined);assert.equal(w.session.pendingReceipt,undefined);
  executed.push(operation.id);return {message:'Executed '+operation.id};
 };
 await w.run('resume()');
 const pendingArea=retention==='transient'?w.session:w.values;
 assert.equal(pendingArea.pendingReceipt.id,'first');
 assert.deepEqual(executed,['first']);
 assert.equal(w.calls.filter(path=>path==='/self/poll').length,1);

 const retry=w.run('resume()');await retryStarted;
 assert.equal(pendingArea.pendingReceipt.id,'first');
 assert.deepEqual(w.resultPosts[0],w.resultPosts[1]);
 assert.deepEqual(executed,['first']);
 assert.equal(w.calls.filter(path=>path==='/self/poll').length,1);

 acknowledge({status:'completed'});await retry;
 assert.deepEqual(executed,['first','second']);
 assert.deepEqual(w.resultPosts.map(receipt=>receipt.id),['first','first','second']);
 assert.equal(w.calls.filter(path=>path==='/self/poll').length,2);
 assert.equal(w.values.pendingReceipt,undefined);assert.equal(w.session.pendingReceipt,undefined);
});

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
for(const retention of ['durable','transient'])test(`Overlapping reconnects retain the next ${retention} receipt after a lost acknowledgement`,async()=>{
 const oldPost=deferred(),newPost=deferred(),oldStarted=deferred(),newStarted=deferred(),executed=[];
 const w=worker(retention,undefined,{deliveries:[{id:'new-effect',lease:'new-lease',action:'browser.click',retention,deadline:1700000015}],resultResponse:payload=>{
  if(payload.id==='receipt'){oldStarted.resolve();return oldPost.promise}
  newStarted.resolve();return newPost.promise;
 }});
 w.context.execute=async(connection,operation)=>{executed.push(operation.id);return {message:'Effect executed'}};
 const first=w.run('resume()');await oldStarted.promise;
 await w.run('resume()');await w.alarm('connect');
 assert.deepEqual(w.resultPosts.map(row=>row.id),['receipt']);
 assert.equal(w.calls.includes('/self/poll'),false);
 oldPost.resolve({status:'completed'});await newStarted.promise;
 const area=retention==='transient'?w.session:w.values;
 assert.equal(area.pendingReceipt.id,'new-effect');
 await w.run('resume()');
 newPost.reject(new TypeError('Lost acknowledgement'));await first;
 assert.deepEqual(executed,['new-effect']);
 assert.deepEqual(w.resultPosts.map(row=>row.id),['receipt','new-effect']);
 assert.equal(area.pendingReceipt.id,'new-effect');
});
for(const retention of ['durable','transient'])test(`An older acknowledgement cannot clear another ${retention} receipt`,async()=>{
 const w=worker(retention,500);const area=retention==='transient'?w.session:w.values;
 w.context.older={...area.pendingReceipt};area.pendingReceipt={...area.pendingReceipt,id:'new-effect',lease:'new-lease'};
 await w.run('clearReceipt(older)');
 assert.equal(area.pendingReceipt.id,'new-effect');
});
