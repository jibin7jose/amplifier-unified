// Checkpoints are bounded and never discard local edits or start model work.
const surfaces=new Set();
export function registerSurfaceCheckpoint(sessionId,checkpoint){
 const entry={sessionId,checkpoint};surfaces.add(entry);return()=>surfaces.delete(entry);
}
export async function checkpointSurfaces(sessionId,identity){
 const jobs=[...surfaces].filter(entry=>entry.sessionId===sessionId).map(entry=>Promise.resolve().then(()=>entry.checkpoint(identity)).catch(()=>{}));
 let timer;
 try{await Promise.race([Promise.all(jobs),new Promise(resolve=>{timer=setTimeout(resolve,1000)})])}
 finally{clearTimeout(timer)}
}
