const version=value=>/^v?\d+\.\d+\.\d+$/.test(value||'')?value.replace(/^v/,''):null;

// Read the update owner's target. A newer availability check must not relabel
// an in-flight installation, and component work cannot imply app success.
export function updateReleaseSummary(updates,application,overview){
 let target,stage;
 const diagnostics=updates.diagnostics||{};
 if(updates.pendingRestart||updates.pendingReplacement!=null){
  stage='during';target=version((updates.pendingRestart||updates.pendingReplacement)?.version);
 }else if(['staging','validating','activating'].includes(updates.phase)&&diagnostics.kind==='application'){
  stage='during';
  target=diagnostics.revision&&diagnostics.revision===application.revision?version(application.latest):
   diagnostics.revision&&diagnostics.revision===application.runningRevision?version(application.current):null;
 }else if(updates.pendingApp){
  stage='during';target=version(updates.pendingApp.latest);
 }else if(application.status==='update'&&application.latest&&
   !updates.pendingRelease&&overview.toolResultsIndependent&&!updates.sequence?.install){
  stage='before';target=version(application.latest);
 }else{
  stage=overview.applicationResult?'after':'installed';target=version(application.current);
 }
 const sources=[...(updates.pendingApp?.releaseNotes||[]),...(application.releaseNotes||[])];
 const notes=target?sources.find(entry=>version(entry.version)===target):null;
 return {stage,version:target,notes,
  heading:stage==='before'?'What’s new in':stage==='during'?'Included in this update':stage==='after'?'Now installed':'Installed release',
  url:target?'https://github.com/microsoft/amplifier-unified/releases/tag/v'+target:null};
}
