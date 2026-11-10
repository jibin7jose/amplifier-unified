// Test-only ownership for this journey's action routes. No app behavior changes.
export async function bounded(promise,milliseconds,label){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{
  timer=setTimeout(()=>reject(Error(label)),milliseconds);
 })])}finally{clearTimeout(timer)}
}

export function combinedFailure(primary,secondary=[]){
 const rest=[...new Set(secondary)].filter(error=>error&&error!==primary);
 if(!primary&&!rest.length)return null;
 if(primary&&!rest.length)return primary;
 if(!primary&&rest.length===1)return rest[0];
 return new AggregateError(primary?[primary,...rest]:rest,
  `${primary?`Primary failure: ${primary.message}; `:''}secondary failures: ${rest.map(error=>error.message).join('; ')}`,
  primary?{cause:primary}:undefined);
}

// Observe immediately: a promise started before a dialog/gate is released must
// not reject unhandled while the main test is still exercising that gate.
export const observed=promise=>Promise.resolve(promise).then(value=>({value}),error=>({error}));
export async function resultOf(outcome){
 const result=await outcome;if(result.error)throw result.error;return result.value;
}

export async function withActionRoute(page,{label,handle,release=()=>{},trace=[]},exercise){
 const pending=new Set(),failures=[];
 let sequence=0,primary;
 const record=(id,phase,error)=>trace.push({route:label,id,phase,...(error?{error:error.message}:{})});
 const handler=route=>{
  const id=++sequence;record(id,'started');
  // Do not rethrow into Playwright's unawaited page-event callback. The exact
  // rejection instead fails the awaited exercise/teardown below. Never retry
  // continue/abort on a route whose first handling outcome is uncertain.
  const task=Promise.resolve().then(()=>handle(route)).then(
   ()=>record(id,'completed'),
   error=>{failures.push(error);record(id,'failed',error)},
  );
  pending.add(task);void task.then(()=>pending.delete(task));
  return task;
 };
 await page.route('**/api/actions',handler);
 record(null,'registered');
 try{
  // The exercise already has the journey's bounded Playwright/action waits.
  // Await it to settlement rather than abandoning it when a callback fails.
  await exercise();
 }catch(error){primary=error}
 const cleanup=[];
 try{release();record(null,'gate-released')}catch(error){cleanup.push(error)}
 try{
  // Keep interception registered while the released handlers finish. New
  // callbacks arriving during a drain are included, not abandoned by unroute.
  await bounded((async()=>{while(pending.size)await Promise.all([...pending])})(),10000,
   `${label}: route handlers did not drain`);
  record(null,'drained');
 }catch(error){cleanup.push(error)}
 try{
  // This helper owns ALL page routes in the standalone APP-004 journey; no
  // peers' routes exist on this page. wait also joins a last protocol arrival.
  await bounded(page.unrouteAll({behavior:'wait'}),10000,`${label}: waiting unroute did not settle`);
  record(null,'unregistered');
 }catch(error){cleanup.push(error)}
 const failure=combinedFailure(primary,[...failures,...cleanup]);
 if(failure)throw failure;
}
