// Generic async/test-display helpers shared by client UI and Unified acceptance.
export const unknownCleanupValue=/^(?:unknown|not recorded)(?:\s*[/\u00b7\u2014-]\s*(?:unknown|not recorded))?\.?$/i;

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
