// Only complete, replaceable editor snapshots may collapse while waiting.
// Saves, chat drafts, navigation and caller-assigned command identities are
// barriers: their ordering and individual receipts must remain unchanged.
const editors=new Set(['aiConnectionEditor','imageSettingsEditor','providerEditor','composerModel','composerBundle','bundleDefaultsDraft']);
// Draft defaults are scoped reads: a stalled receipt must not serialize edits
// or a retry for another draft. They still observe preceding writes.
export const isDraftDefaultsRead=(action,meta={})=>action==='configuration.defaults'&&meta.expectedRevision===undefined;
export const isProviderCatalogRead=(action,meta={})=>meta.expectedRevision===undefined&&['providers.list','providers.models','providers.schema'].includes(action);
// Appearance metadata/CSS reads observe earlier writes without joining their
// tail. One optional read must not delay another read, Stop, or an ordered write.
export const isAppearanceRead=(action,meta={})=>meta.expectedRevision===undefined&&['theme.list','theme.read'].includes(action);
// These host-owned reads resolve choices without loading a conversation worker.
// Other runtime controls (and all configuration writes) retain ordered dispatch.
export const isConversationChoiceRead=(action,args={},meta={})=>meta.expectedRevision===undefined&&(
 ['bundles.list','bundle.preview'].includes(action)||action==='runtime.control'&&args.operation==='configuration.catalog');
export function settingsDraftKey(action,args,meta={}){
 if(action!=='view.update'||meta.id||meta.expectedRevision!==undefined||meta.signal||meta.pendingViewToken)return null;
 if(Object.keys(args).some(key=>!['patch','sessionId'].includes(key)))return null;
 const fields=Object.keys(args.patch||{}),field=fields[0],value=args.patch?.[field];
 if(fields.length!==1||!editors.has(field)||!value||typeof value!=='object'||Array.isArray(value))return null;
 return JSON.stringify([field,args.sessionId??null,value.sessionId??null]);
}

export function createSettingsActionQueue(){
 let tail=null;
 return (queue,execute,key=null,after=null)=>{
  if(key&&tail?.queue===queue&&tail.key===key&&!tail.started){
   tail.execute=execute;
   return tail.promise;
  }
  const entry={queue,key,execute,started:false};
  const run=()=>{
   entry.started=true;
   if(tail===entry)tail=null;
   const invoke=entry.execute;entry.execute=null;
   return invoke();
  };
  // A catalog read observes preceding edits, but a later save need not wait
  // for its remote lookup. The host discards results for superseded settings.
  entry.promise=(after?Promise.all([queue.current,after]):queue.current).then(run,run);
  queue.current=entry.promise.catch(()=>{});
  tail=entry;
  return entry.promise;
 };
}
