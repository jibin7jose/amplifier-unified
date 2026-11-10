// Failure handling for the delegate stream fixture only. No browser or app imports.
export function delegateStreamDiagnostics() {
  const errors=[],pending=[];
  let omitted=0;
  const note=(stage,error)=>{
    if(errors.length>=20){omitted++;return}
    let message;
    try{message=String(error?.stack||error).slice(0,2000)}catch{message='Unprintable diagnostic error'}
    errors.push({stage,message});
  };
  const attempt=async(stage,action)=>{
    try{return await action()}catch(error){note(stage,error)}
  };
  // EventEmitter listeners must neither throw nor leave a rejected read promise
  // waiting for a later Promise.all to attach its first rejection handler.
  const listen=(stage,action)=>(...args)=>{
    try{action(...args)}catch(error){note(stage,error)}
  };
  const track=(stage,action)=>{
    const read=attempt(stage,action);
    pending.push(read);
    return read;
  };
  const collect=async()=>{
    await Promise.all(pending.splice(0));
  };
  const snapshot=()=>({errors:[...errors],omitted});
  const finish=async(primary,{writes=[],cleanup=[],report=()=>{}}={})=>{
    // Close first so pending response reads settle even on a failed journey.
    // Cleanup actions are independent; a close failure cannot skip fixture stop.
    for(const [stage,action] of cleanup)await attempt(stage,action);
    await attempt('collect closing responses',collect);
    for(const [stage,action] of writes)await attempt(stage,action);
    await attempt('diagnostic error report',()=>report(snapshot()));
    const secondary=snapshot();
    if(errors.length) {
      // stderr is the fallback when the evidence destination itself is unwritable.
      try{console.error('Delegate stream secondary failures:',JSON.stringify(secondary))}catch{}
      if(primary&&primary.error instanceof Error) {
        try{Object.defineProperty(primary.error,'diagnosticFailures',{value:secondary})}catch{}
      }
    }
    // Preserve the very same assertion/error, including its original stack.
    if(primary)throw primary.error;
    if(errors.length)throw new AggregateError(
      errors.map(({stage,message})=>new Error(`${stage}: ${message}`)),
      `Required delegate stream diagnostics or cleanup failed (${errors.length} recorded, ${omitted} omitted)`);
  };
  return {listen,track,collect,attempt,finish};
}