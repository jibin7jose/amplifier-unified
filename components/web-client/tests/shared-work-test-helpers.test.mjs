// Authored observer controls. Run with the other tests in the owned guest only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {assertPassiveActions,assertWorkWaitTermination,bounded,createWorkObserver,deferredRead,observeCompletion,requireCompletion,unknownCleanupValue,workWaitProbeIds} from './shared-work-test-helpers.mjs';

const request=(body={action:'workspace.work.list',args:{workspaceId:'synthetic'}},overrides={})=>({
 method:()=> 'POST',url:()=> 'http://127.0.0.1/api/actions',postDataJSON:()=>body,...overrides,
});

test('observer retains valid passive reads from both client identities',()=>{
 const observer=createWorkObserver();
 observer.request('primary',request());
 observer.request('peer',request({action:'workspace.work.read',args:{workspaceId:'synthetic',id:'work:one'}}));
 observer.assertHealthy();assertPassiveActions(observer.calls);
 assert.deepEqual(observer.calls.map(row=>row.client),['primary','peer']);
 assert.equal(observer.calls[1].args.id,'work:one');
});

test('observer ignores unrelated requests without trying to parse an action body',()=>{
 const observer=createWorkObserver();let parses=0;
 const postDataJSON=()=>{parses++;throw Error('Not JSON')};
 observer.request('primary',request(null,{method:()=> 'GET',postDataJSON}));
 observer.request('peer',request(null,{url:()=> 'http://127.0.0.1/api/view',postDataJSON}));
 assert.equal(parses,0);assert.deepEqual(observer.calls,[]);observer.assertHealthy();
});

test('observer fails on primary session.warm even before passive-slice selection',()=>{
 const observer=createWorkObserver();
 assert.doesNotThrow(()=>observer.request('primary',request({action:'session.warm',args:{id:'cold'}})));
 assert.throws(()=>observer.assertHealthy(),/forbidden-warm/);
 assert.throws(()=>assertPassiveActions(observer.calls),/session.warm/);
});

test('observer fails on peer session.warm instead of checking only the primary client',()=>{
 const observer=createWorkObserver();observer.request('primary',request());
 assert.doesNotThrow(()=>observer.request('peer',request({action:'session.warm',args:{id:'cold'}})));
 assert.throws(()=>observer.assertHealthy(),/forbidden-warm/);
 assert.equal(observer.errors[0].client,'peer');
});

test('malformed action JSON is retained and fails at an awaited assertion, not the listener',()=>{
 const observer=createWorkObserver();
 assert.doesNotThrow(()=>observer.request('peer',request(null,{postDataJSON:()=>{throw SyntaxError('Malformed JSON control')}})));
 assert.deepEqual(observer.calls,[]);assert.equal(observer.errorCount,1);
 assert.throws(()=>observer.assertHealthy(),/Malformed JSON control/);
});

test('valid JSON with malformed action shape is never accepted as an observed command',()=>{
 for(const body of [null,[],{}, {action:''}, {action:'  '}, {action:3}, {action:'session.warm',args:null}, {action:'workspace.work.list',args:[]}]) {
  const observer=createWorkObserver();assert.doesNotThrow(()=>observer.request('primary',request(body)));
  assert.deepEqual(observer.calls,[]);assert.equal(observer.errorCount,1);
  assert.throws(()=>observer.assertHealthy(),/request-observation/);
 }
});

test('invalid URL observation is captured rather than escaping a detached callback',()=>{
 const observer=createWorkObserver();
 assert.doesNotThrow(()=>observer.request('primary',request(undefined,{url:()=> 'not a URL'})));
 assert.equal(observer.errorCount,1);assert.throws(()=>observer.assertHealthy(),/request-observation/);
});

test('oversized action observation fails closed rather than keeping arbitrary payloads',()=>{
 const observer=createWorkObserver();observer.request('primary',request({action:'workspace.work.list',args:{query:'x'.repeat(65537)}}));
 assert.deepEqual(observer.calls,[]);assert.throws(()=>observer.assertHealthy(),/exceeds observation bound/);
});

test('bounded error retention never resets the total or disguises dropped errors as healthy',()=>{
 const observer=createWorkObserver({maxErrors:2});
 for(let index=0;index<5;index++)observer.pageError(index%2?'peer':'primary',Error('x'.repeat(1000)));
 assert.equal(observer.errorCount,5);assert.equal(observer.errors.length,2);
 assert.ok(observer.errors.every(row=>row.message.length===500));
 assert.throws(()=>observer.assertHealthy(),/errors \(5; retained 2\)/);
});

test('action observation overflow fails rather than silently dropping a possible warm call',()=>{
 const observer=createWorkObserver({maxCalls:1});observer.request('primary',request());
 observer.request('peer',request({action:'session.warm',args:{id:'cold'}}));
 assert.equal(observer.calls.length,1);assert.throws(()=>observer.assertHealthy(),/capacity exceeded/);
});

test('passive guard retains the previous worker/navigation and Work mutation oracles',()=>{
 for(const action of ['session.select','session.warm','conversation.send','worker.spawn','task.start','coordination.create',
  'workspace.work.report','workspace.work.publication','workspace.work.resource','workspace.work.decision','workspace.work.reviewInput']) {
  assert.throws(()=>assertPassiveActions([{client:'peer',action,args:{}}]),/Passive Work|Unexpected Work mutation/);
 }
 assert.doesNotThrow(()=>assertPassiveActions([{client:'primary',action:'view.update',args:{}}]));
 for(const action of ['workspace.work.wait','workspace.work.event','workspace.work.coverage']) {
  assert.doesNotThrow(()=>assertPassiveActions([{client:'peer',action,args:{}}]));
 }
});

test('Cleanup value accepts explicit uncertainty but rejects None or leakage from another facet',()=>{
 for(const value of ['Unknown','Not recorded','Unknown / not recorded'])assert.match(value,unknownCleanupValue);
 for(const value of ['None','No resources','Cleanup complete','Human decision Not recorded Cleanup None','Not recorded elsewhere; Cleanup None'])assert.doesNotMatch(value,unknownCleanupValue);
});

test('handler observation supports both synchronous returns and held asynchronous completion',async()=>{
 const held=deferredRead();let secondSelected=false;
 const completion=observeCompletion((async()=>{await held.promise;assert.ok(secondSelected);return 'old result'})());
 try {
  // Collecting completion must not require resolving it before a second action.
  secondSelected=true;
  assert.equal(await requireCompletion(observeCompletion(undefined)),undefined);
  held.resolve();assert.equal(await bounded(requireCompletion(completion),1000,'Async handler control'),'old result');
 } finally {
  held.resolve();await bounded(requireCompletion(completion),1000,'Control settlement');
 }
});

test('handler rejection is observed immediately and remains a failure when joined',async()=>{
 const failure=Error('Handler control failed');
 const completion=observeCompletion(Promise.reject(failure));
 await assert.rejects(requireCompletion(completion),error=>error===failure);
});

test('finite barrier rejects a missing completion and owned deferred work can still settle',async()=>{
 const held=deferredRead();const completion=observeCompletion(held.promise);
 try {await assert.rejects(bounded(requireCompletion(completion),10,'Held control'),/Held control timed out/)}
 finally {held.resolve('released');assert.equal(await bounded(requireCompletion(completion),1000,'Cleanup control'),'released')}
});

// Fabricated traces test the oracle, never claim actual HTTP/product evidence.
function retirementTrace(){
 const natural={id:1,commandId:workWaitProbeIds.natural,taskId:101,waitMs:30000};
 const shutdown={id:2,commandId:workWaitProbeIds.shutdown,taskId:102,waitMs:30000};
 return {
  observationErrors:[],fixtureClose:{code:0,signal:null},
  shutdownSignals:[{signal:'SIGTERM',at:41}],shutdownFences:[{at:41.01,serviceClosed:true,active:[shutdown]}],
  cleanupComplete:{complete:true,at:41.1,activeIds:[],observationErrors:[],observationErrorCount:0},
  workWaits:[
   {...natural,phase:'entered',at:10,serviceClosed:false},
   {...natural,phase:'returned',at:40.01,serviceClosed:false,accepted:true,changed:false,timedOut:true},
   {...natural,phase:'http-response',at:40.02,serviceClosed:false,status:200,accepted:true,code:null,changed:false,timedOut:true},
   {...shutdown,phase:'entered',at:40.1,serviceClosed:false},
   {...shutdown,phase:'cancelled',at:41.02,serviceClosed:true},
   {...shutdown,phase:'http-response',at:41.03,serviceClosed:true,status:503,accepted:false,code:'host_shutdown',changed:null,timedOut:null},
  ],
  wireWaits:[
   {commandId:natural.commandId,status:200,accepted:true,code:null,changed:false,timedOut:true},
   {commandId:shutdown.commandId,status:503,accepted:false,code:'host_shutdown',changed:null,timedOut:null},
  ],
 };
}

test('wait oracle accepts genuine deadline then fenced retirement, not the old blanket no-cancel predicate',()=>{
 const trace=retirementTrace(),verdict=assertWorkWaitTermination(trace);
 assert.equal(verdict.entered,2);assert.equal(verdict.returned,1);assert.equal(verdict.retired,1);
 assert.equal(verdict.oldBlanketNoCancellationOracleWouldPass,false);
 // Exact former falsifier on the SAME evidence, not another live fixture run.
 assert.throws(()=>{for(const start of trace.workWaits.filter(r=>r.phase==='entered'))
  assert.ok(trace.workWaits.some(end=>end.id===start.id&&end.phase==='returned'),'Wait must drain, not be cancelled: '+start.id)},/Wait must drain/);
});

test('wait oracle accepts natural completion and HTTP response racing across the fence',()=>{
 const trace=retirementTrace(),read={id:3,commandId:null,taskId:103,waitMs:30000};
 trace.workWaits.push({...read,phase:'entered',at:40.2,serviceClosed:false},
  {...read,phase:'returned',at:41.005,serviceClosed:false,accepted:true,changed:true,timedOut:false},
  {...read,phase:'http-response',at:41.04,serviceClosed:true,status:200,accepted:true,changed:true,timedOut:false});
 assert.equal(assertWorkWaitTermination(trace).returned,2);
});

for(const [label,breakTrace] of [
 ['premature cancellation',t=>{t.workWaits[4].at=40.9;t.workWaits[4].serviceClosed=false}],
 ['missing constructed 503',t=>{t.workWaits.pop()}],
 ['missing wire 503',t=>{t.wireWaits.pop()}],
 ['accepted shutdown result',t=>{t.workWaits[5].accepted=true}],
 ['false shutdown timeout',t=>{t.workWaits[5].timedOut=true}],
 ['wrong wire status',t=>{t.wireWaits[1].status=200}],
 ['missing natural return',t=>{t.workWaits.splice(1,1)}],
 ['shortened natural deadline',t=>{t.workWaits[1].at=39.99}],
 ['changed natural probe',t=>{t.workWaits[1].changed=true}],
 ['duplicate terminal',t=>{t.workWaits.push({...t.workWaits[4]})}],
 ['unknown wait ID',t=>{t.workWaits[4].id=99}],
 ['wrong task identity',t=>{t.workWaits[4].taskId=999}],
 ['missing active-fence member',t=>{t.shutdownFences[0].active=[]}],
 ['missing actual fence',t=>{t.shutdownFences=[]}],
 ['missing signal receipt',t=>{t.shutdownSignals=[]}],
 ['failed wait',t=>{t.workWaits[4].phase='failed'}],
 ['observer overflow',t=>{t.observationErrors.push('Wait observation overflow')}],
 ['fixture observation failure',t=>{t.cleanupComplete.observationErrorCount=1}],
 ['leaked active waiter',t=>{t.cleanupComplete.activeIds=[2]}],
 ['slow cleanup',t=>{t.cleanupComplete.at=52}],
 ['forced fixture exit',t=>{t.fixtureClose={code:null,signal:'SIGKILL'}}],
]){
 test('wait oracle rejects '+label,()=>{
  const trace=retirementTrace();breakTrace(trace);
  assert.throws(()=>assertWorkWaitTermination(trace));
 });
}
