import {spawn} from 'node:child_process';
import {ownFixture} from './fixture-lifecycle.mjs';

const stderrLimit=8000;

function attachDiagnostics(error,diagnostics) {
 // A frozen/foreign rejection still wins; evidence must never replace it.
 try{Object.defineProperty(error,'fixtureDiagnostics',{value:diagnostics,enumerable:true,configurable:true})}catch{}
}

// Observe the existing pipes/close event, never extend lifecycle deadlines or
// signal a process here. Stderr is untrusted: publish structure, not raw text.
export function observeFixtureShutdown(fixture,originalStop) {
 let tail=Buffer.alloc(0),bytes=0,closed=false,code=null,signal=null,stopping;
 fixture.stderr.on('data',chunk=>{
  bytes+=chunk.length;
  tail=Buffer.concat([tail,chunk.subarray(-stderrLimit)]).subarray(-stderrLimit);
 });
 fixture.once('close',(exitCode,exitSignal)=>{closed=true;code=exitCode;signal=exitSignal});
 const diagnostics=()=>{
  const frames=[];let omittedFrames=0,exceptionType=null,traceback=false;
  // A truncated first line is not a complete structural record.
  const lines=tail.toString('utf8').split('\n');
  if(bytes>tail.length)lines.shift();
  for(const raw of lines){
   const line=raw.endsWith('\r')?raw.slice(0,-1):raw;
   if(line==='Traceback (most recent call last):'){traceback=true;continue}
   if(!traceback)continue;
   const frame=/^  File "([^"\x00-\x1f\x7f]{1,2048})", line ([1-9]\d{0,8}), in ([A-Za-z_][A-Za-z0-9_]{0,95}|<module>)$/.exec(line);
   if(frame){
    const file=frame[1].split(/[\\/]/).at(-1);
    if(!/^[A-Za-z0-9_.-]{1,96}$/.test(file))continue;
    if(frames.length===8){frames.shift();omittedFrames++}
    frames.push({file,line:Number(frame[2]),function:frame[3]});
   }else if(frames.length){
    const exception=/^([A-Za-z_][A-Za-z0-9_.]{0,95})(?::.*)?$/.exec(line);
    if(exception){exceptionType=exception[1];traceback=false}
   }
  }
  return {closed,exitCode:Number.isInteger(code)?code:null,
   signal:typeof signal==='string'&&/^SIG[A-Z0-9]{1,16}$/.test(signal)?signal:null,
   stderr:{source:'untrusted-stderr-tail',bytes,retainedBytes:tail.length,truncated:bytes>tail.length,
    ended:fixture.stderr.readableEnded===true,traceback:{frames,omittedFrames,exceptionType}}};
 };
 const stop=()=>stopping??=(async()=>{
  try{return await originalStop()}
  catch(error){
   const detail=diagnostics();attachDiagnostics(error,detail);
   // JSON is bounded and contains no exception message, source line or raw pipe.
   try{console.error('FIXTURE_DIAGNOSTICS '+JSON.stringify(detail))}catch{}
   throw error;
  }
 })();
 return {stop,diagnostics};
}

// Read readiness from our own child, never from a possibly unrelated listener.
// ownFixture remains responsible for shutdown, including failed startup.
export async function startFixture({command,args,env,tag,name,timeoutMs=30000}) {
 const fixture=spawn(command,args,{stdio:['ignore','pipe','pipe'],env});
 const {stop,diagnostics}=observeFixtureShutdown(fixture,ownFixture(fixture,{label:`${name} fixture`}));
 try {
  const base=await new Promise((resolve,reject)=>{
   let pending='';
   const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
   const failed=error=>{cleanup();reject(error)};
   const exited=(code,signal)=>failed(new Error(`${name} fixture exited before readiness (${code??signal})`));
   const data=chunk=>{
    pending+=chunk;
    for(let end;(end=pending.indexOf('\n'))>=0;){
     const line=pending.slice(0,end);pending=pending.slice(end+1);
     let ready;try{ready=JSON.parse(line)}catch{continue}
     if(ready?.fixture!==tag)continue;
     if(ready.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(ready.base)){
      failed(new Error(`Invalid ${name.toLowerCase()} fixture readiness receipt`));return;
     }
     cleanup();resolve(ready.base);return;
    }
    if(pending.length>8000)failed(new Error(`${name} fixture readiness output exceeded its bound`));
   };
   const timer=setTimeout(()=>failed(new Error(`${name} fixture did not become ready`)),timeoutMs);
   fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
  });
  return {fixture,base,stop};
 } catch(error) {
  try{await stop()}catch(cleanupError){console.error(`${name} startup cleanup failed:`,cleanupError)}
  attachDiagnostics(error,diagnostics());
  throw error;
 }
}
