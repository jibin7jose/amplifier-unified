const shutdownTimeoutMs=10000;
const killedTimeoutMs=2000;

async function bounded(promise,timeoutMs,message) {
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(message)),timeoutMs)})])}
 finally{clearTimeout(timer)}
}

// Attach immediately after spawn so an early exit cannot be mistaken for a
// successful cleanup. A forced kill reaps our child but remains a test failure.
export function ownFixture(fixture,{label="Test fixture",timeoutMs=shutdownTimeoutMs}={}) {
 const closed=new Promise(resolve=>{
  fixture.once('close',(code,signal)=>resolve({pid:fixture.pid,code,signal}));
  fixture.once('error',error=>resolve({pid:fixture.pid,error}));
 });
 let stopping;
 return ()=>stopping??=(async()=>{
  if(fixture.exitCode===null&&fixture.signalCode===null)fixture.kill('SIGTERM');
  let result;
  try{result=await bounded(closed,timeoutMs,`${label} did not finish graceful shutdown`)}
  catch(error){
   fixture.kill('SIGKILL');
   try{await bounded(closed,killedTimeoutMs,`${label} remained alive after forced cleanup`)}
   catch(reapError){throw new AggregateError([error,reapError],`${label} cleanup failed`)}
   throw error;
  }
  if(result.error)throw result.error;
  if(result.code!==0||result.signal!==null)throw new Error(`${label} shutdown failed (${result.code??result.signal})`);
  return result;
 })();
}

// Always stop the server, even if browser cleanup fails. Preserve the original
// assertion as the thrown error while still reporting every cleanup failure.
export async function finishFixture({label="Test fixture",close=[],stop,originalError}) {
 const failures=[];
 for(const [name,release] of close){
  try{await bounded(Promise.resolve().then(release),shutdownTimeoutMs,`${name} did not close`)}
  catch(error){failures.push(error)}
 }
 try{await stop()}catch(error){failures.push(error)}
 if(failures.length){
  const cleanupError=new AggregateError(failures,`${label} cleanup failed`);
  if(originalError)console.error(cleanupError);
  else throw cleanupError;
 }
}
