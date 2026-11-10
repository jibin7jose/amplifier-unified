// Small test-only guards; no product dispatch, runtime or browser is created here.
import assert from 'node:assert/strict';

export const unknownCleanupValue=/^(?:unknown|not recorded)(?:\s*[/\u00b7\u2014-]\s*(?:unknown|not recorded))?\.?$/i;
export const passiveWorkReads=new Set(['workspace.work.list','workspace.work.read','workspace.work.history','workspace.work.source',
 'workspace.work.wait','workspace.work.event','workspace.work.coverage']);
const forbiddenPassiveActions=new Set(['session.select','session.warm','conversation.send','worker.spawn','task.start','coordination.create']);

export function assertPassiveActions(calls) {
 for(const call of calls) {
  assert.ok(!forbiddenPassiveActions.has(call.action),`Passive Work dispatched ${call.action} from ${call.client}`);
  if(call.action.startsWith('workspace.work.'))assert.ok(passiveWorkReads.has(call.action),`Unexpected Work mutation ${call.action}`);
 }
}

// EventEmitter does not await request listeners. Catch locally and fail later in
// the awaited browser try block, so its finally always owns ordinary cleanup.
export function createWorkObserver({maxCalls=1000,maxErrors=16}={}) {
 assert.ok(Number.isInteger(maxCalls)&&maxCalls>0);
 assert.ok(Number.isInteger(maxErrors)&&maxErrors>0);
 const calls=[],errors=[];let errorCount=0;
 const recordError=(client,kind,message)=>{
  errorCount++;
  if(errors.length<maxErrors)errors.push({client,kind,message:String(message).slice(0,500)});
 };
 return {
  calls,errors,
  get errorCount(){return errorCount},
  request(client,request) {
   try {
    if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
    const body=request.postDataJSON();
    assert.ok(body&&typeof body==='object'&&!Array.isArray(body),'Action body must be an object');
    assert.ok(typeof body.action==='string'&&body.action.length>0&&body.action.trim()===body.action,'Action name must be a nonempty string');
    assert.ok(body.args===undefined||body.args&&typeof body.args==='object'&&!Array.isArray(body.args),'Action arguments must be an object');
    assert.ok(JSON.stringify(body).length<=65536,'Action body exceeds observation bound');
    assert.ok(calls.length<maxCalls,'Action observation capacity exceeded');
    calls.push({client,action:body.action,args:body.args??{}});
    if(body.action==='session.warm')recordError(client,'forbidden-warm','Work browser requested session.warm');
   } catch(error) {recordError(client,'request-observation',error.message)}
  },
  pageError(client,error) {recordError(client,'page-error',error.message)},
  assertHealthy() {
   assert.equal(errorCount,0,`Browser observation errors (${errorCount}; retained ${errors.length}): ${JSON.stringify(errors)}`);
  },
 };
}

export async function bounded(promise,timeoutMs,label) {
 let timer;
 try {return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),timeoutMs)})])}
 finally {clearTimeout(timer)}
}

// Observe a returned handler promise immediately, without dictating whether the
// product callback is sync or async and without an unhandled rejection window.
export const observeCompletion=value=>Promise.resolve(value).then(
 value=>({status:'fulfilled',value}),reason=>({status:'rejected',reason}));
export async function requireCompletion(completion) {
 const result=await completion;
 if(result.status==='rejected')throw result.reason;
 return result.value;
}
export function deferredRead() {
 let resolve;
 const promise=new Promise(done=>{resolve=done});
 return {promise,resolve};
}

export const workWaitProbeIds=Object.freeze({
 natural:'fixture-work-natural-deadline',shutdown:'fixture-work-shutdown-pending',
});

// Pure evidence oracle. Server monotonic times are compared only with each
// other; browser Date.now timestamps are not a substitute for a real fence.
export function assertWorkWaitTermination(termination) {
 assert.deepEqual(termination.observationErrors,[]);
 const {cleanupComplete:cleanup,workWaits:rows,shutdownSignals:signals,shutdownFences:fences,wireWaits}=termination;
 assert.equal(cleanup?.complete,true,'Real runner.cleanup must return');
 assert.deepEqual(cleanup.activeIds,[],'No pending observed dispatch after cleanup');
 assert.equal(cleanup.observationErrorCount,0);
 assert.deepEqual(cleanup.observationErrors,[]);
 assert.equal(signals?.length,1,'Exactly one real shutdown signal');
 assert.equal(signals[0].signal,'SIGTERM');
 assert.equal(fences?.length,1,'Exactly one real admission fence');
 const fence=fences[0];
 assert.equal(fence.serviceClosed,true);
 for(const value of [signals[0].at,fence.at,cleanup.at])assert.ok(Number.isFinite(value));
 assert.ok(signals[0].at<=fence.at&&fence.at<=cleanup.at,'Signal, fence and cleanup order');
 assert.ok(cleanup.at-signals[0].at<10,'Real fixture cleanup exceeds 10s');
 assert.equal(termination.fixtureClose?.code,0);assert.equal(termination.fixtureClose?.signal,null);
 assert.ok(Array.isArray(rows)&&rows.length>0);
 const entered=new Map(),terminals=new Map(),responses=new Map();
 for(const row of rows){
  assert.ok(Number.isInteger(row.id)&&row.id>0&&Number.isFinite(row.at));
  assert.ok(['entered','returned','cancelled','http-response'].includes(row.phase),'Failed/unknown wait observation: '+row.phase);
  assert.ok(row.commandId===null||typeof row.commandId==='string'&&row.commandId.length>0&&row.commandId.length<=200);
  assert.ok(Number.isSafeInteger(row.taskId)&&row.taskId>0);
  assert.equal(typeof row.serviceClosed,'boolean');
  if(row.phase==='entered'){
   assert.ok(!entered.has(row.id),'Duplicate entered wait');
   assert.equal(row.serviceClosed,false,'Wait entered after fence');
   assert.ok(row.at<fence.at,'Wait admitted after shutdown fence');
   entered.set(row.id,row);
  }else{
   const start=entered.get(row.id);assert.ok(start,'Unknown wait ID');
   for(const key of ['commandId','taskId','waitMs'])assert.equal(row[key],start[key],'Wait identity mismatch: '+key);
   assert.ok(row.at>=start.at&&row.at<=cleanup.at,'Terminal/response outside lifecycle');
   const target=row.phase==='http-response'?responses:terminals;
   assert.ok(!target.has(row.id),'Duplicate terminal/HTTP response');target.set(row.id,row);
  }
 }
 const active=new Map();
 for(const row of fence.active){
  assert.ok(!active.has(row.id)&&entered.has(row.id),'Unknown/duplicate active fence ID');
  for(const key of ['commandId','taskId','waitMs'])assert.equal(row[key],entered.get(row.id)[key]);
  active.set(row.id,row);
 }
 for(const [id,start] of entered){
  const end=terminals.get(id),response=responses.get(id);
  assert.ok(end&&response,'Missing terminal or HTTP response: '+id);
  assert.ok(response.at>=end.at,'HTTP response precedes dispatch terminal');
  assert.equal(active.has(id),start.at<=fence.at&&end.at>=fence.at,'Active set differs from lifecycle');
  if(end.phase==='cancelled'){
   assert.ok(active.has(id)&&end.at>=fence.at&&end.serviceClosed,'Cancellation before actual fence: '+id);
   assert.equal(response.serviceClosed,true);assert.equal(response.status,503);
   assert.equal(response.accepted,false);assert.equal(response.code,'host_shutdown');
   assert.ok(response.changed==null&&response.timedOut==null,'Cancelled wait disguised as ordinary result');
  }else{
   assert.equal(end.accepted,true);assert.equal(response.status,200);assert.equal(response.accepted,true);
   for(const key of ['changed','timedOut']){
    assert.equal(typeof end[key],'boolean');assert.equal(response[key],end[key]);
   }
  }
 }
 const probe=(commandId,phase,status)=>{
  const starts=[...entered.values()].filter(row=>row.commandId===commandId);
  assert.equal(starts.length,1,'Missing/duplicate known probe '+commandId);
  const start=starts[0],end=terminals.get(start.id),response=responses.get(start.id);
  const wire=wireWaits.filter(row=>row.commandId===commandId);
  assert.equal(wire.length,1,'Missing/duplicate wire receipt '+commandId);
  assert.equal(start.waitMs,30000);assert.equal(end.phase,phase);assert.equal(wire[0].status,status);
  for(const key of ['accepted','code','changed','timedOut'])assert.equal(wire[0][key]??null,response[key]??null,'Wire/server mismatch: '+key);
  return {start,end,response};
 };
 assert.equal(wireWaits.length,2,'Unknown extra wire observation');
 const natural=probe(workWaitProbeIds.natural,'returned',200);
 assert.ok(natural.end.at<signals[0].at&&natural.response.at<signals[0].at,'Natural result must precede shutdown signal');
 assert.equal(natural.end.serviceClosed,false);
 assert.equal(natural.end.changed,false);assert.equal(natural.end.timedOut,true);
 assert.ok(natural.end.at-natural.start.at>=30,'Natural 30s deadline was shortened');
 const shutdown=probe(workWaitProbeIds.shutdown,'cancelled',503);
 assert.ok(shutdown.start.at>natural.response.at&&shutdown.start.at<signals[0].at);
 return {entered:entered.size,returned:[...terminals.values()].filter(row=>row.phase==='returned').length,
  retired:[...terminals.values()].filter(row=>row.phase==='cancelled').length,
  naturalWaitId:natural.start.id,shutdownWaitId:shutdown.start.id,
  signalToCleanupSeconds:cleanup.at-signals[0].at,
  wireDelivery:'Known probes only; other HTTP rows are server-constructed responses, not client delivery',
  oldBlanketNoCancellationOracleWouldPass:[...terminals.values()].every(row=>row.phase==='returned')};
}
