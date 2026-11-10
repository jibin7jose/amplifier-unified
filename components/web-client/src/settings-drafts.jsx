import React,{createContext,useCallback,useContext,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';

const DraftContext=createContext(null);
export function useSettingsDraft(id,entry){
 const registry=useContext(DraftContext);
 // Navigation can run as soon as the edited field is committed. Publish its
 // guard before paint, rather than leaving a gap until passive effects run.
 useLayoutEffect(()=>{registry?.register(id,entry)});
 useLayoutEffect(()=>()=>registry?.remove(id),[registry,id]);
}

export function useSettingsDrafts(){
 const entries=useRef(new Map()),waiting=useRef(null);
 const[count,setCount]=useState(0),[prompt,setPrompt]=useState(null);
 const recount=useCallback(()=>setCount([...entries.current.values()].filter(entry=>entry.dirty).length),[]);
 const register=useCallback((id,entry)=>{const changed=!!entries.current.get(id)?.dirty!==!!entry.dirty;entries.current.set(id,entry);if(changed)recount()},[recount]);
 const remove=useCallback(id=>{if(entries.current.delete(id))recount()},[recount]);
 const hasUnsaved=useCallback(()=>[...entries.current.values()].some(entry=>entry.dirty),[]);
 const confirmClose=useCallback(()=>{
  const dirty=[...entries.current.values()].filter(entry=>entry.dirty);
  if(!dirty.length)return Promise.resolve(true);
  if(waiting.current)return waiting.current.promise;
  let resolve;const promise=new Promise(done=>{resolve=done});waiting.current={promise,resolve};setPrompt(dirty);return promise;
 },[]);
 const context=useMemo(()=>({register,remove,hasUnsaved,confirmClose}),[register,remove,hasUnsaved,confirmClose]);
 const settle=accepted=>{const pending=waiting.current;waiting.current=null;setPrompt(null);pending?.resolve(accepted)};
 useEffect(()=>{
  if(!count)return;
  const unload=event=>{event.preventDefault();event.returnValue=''};
  window.addEventListener('beforeunload',unload);return()=>window.removeEventListener('beforeunload',unload);
 },[count]);
 useEffect(()=>()=>{waiting.current?.resolve(false)},[]);
 const dialog=prompt&&<div className="a-settings-draft-overlay"><div className="a-settings-draft-confirm" role="alertdialog" aria-modal="true" aria-labelledby="settings-unsaved-title" onKeyDown={event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();settle(false)}
  if(event.key==='Tab'){const buttons=[...event.currentTarget.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);event.preventDefault();event.stopPropagation();buttons[(index+(event.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus()}
 }}>
  <h3 id="settings-unsaved-title">Unsaved settings changes</h3>
  <p>Your changes have not been saved. Review them before closing Settings, or discard them.</p>
  <ul>{prompt.map(entry=><li key={entry.label}>{entry.label}</li>)}</ul>
  <div className="a-dialog-actions">
   <button type="button" className="a-primary" autoFocus onClick={()=>{prompt[0].review();settle(false)}}>Review and save</button>
   <button type="button" className="a-soft a-danger" onClick={()=>{prompt.forEach(entry=>entry.discard());settle(true)}}>Discard changes</button>
   <button type="button" className="a-soft" onClick={()=>settle(false)}>Keep settings open</button>
  </div>
 </div></div>;
 return {context,confirmClose,hasUnsaved,count,dialog};
}
export function SettingsDraftProvider({value,children}){return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>}

// Keep private editor memory when a shared action selects another connection.
// The same Settings dialog owns the user's discard/review decision.
export function useGuardedSettingsDraft(requested,identity,restore){
 const registry=useContext(DraftContext),[accepted,setAccepted]=useState(identity),[restoring,setRestoring]=useState(null),visible=useRef(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 if(identity===accepted)visible.current=requested;
 useEffect(()=>{
  if(restoring||identity===accepted)return;
  if(!registry?.hasUnsaved()){setAccepted(identity);return;}
  let current=true;
  registry.confirmClose().then(discard=>{
   if(!current)return;
   if(discard){
    // Discard callbacks may first reset the old shared editor. Hold the
    // approved target through that intermediate frame and its restore receipt.
    setAccepted(identity);setRestoring({value:requested});visible.current=requested;
    Promise.resolve(latestRestore.current(requested)).catch(()=>{}).finally(()=>setRestoring(null));
   }else latestRestore.current(visible.current);
  });
  return()=>{current=false};
 },[identity,accepted,registry,restoring]);
 return restoring?.value??(identity===accepted?requested:visible.current);
}

// Shared view updates (including agent actions) must not unmount the only copy
// of a private field before the user chooses whether to discard it.
export function useGuardedSettingsPanel(requested,navigationRef,restore){
 const [visible,setVisible]=useState(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 useEffect(()=>{
  if(requested===visible)return;
  const settings=panel=>panel==='settings'||panel==='appearance'||panel==='settings-canvas',navigation=navigationRef.current;
  if(!settings(visible)||!navigation?.hasUnsaved()) {setVisible(requested);return;}
  let current=true;
  navigation.confirmClose().then(accepted=>{if(!current)return;if(accepted)setVisible(requested);else latestRestore.current(visible)});
  return()=>{current=false};
 },[requested,visible,navigationRef]);
 return visible;
}

// Navigation from an agent or another client also preserves private editor
// memory until the user decides. Artifact panes still reset per conversation.
export function useGuardedSettingsSession(requested,navigationRef,restore){
 const [visible,setVisible]=useState(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 useEffect(()=>{
  if(requested?.id===visible?.id)return;
  const navigation=navigationRef.current;
  if(!navigation?.hasUnsaved()){setVisible(requested);return;}
  let current=true;
  navigation.confirmClose().then(accepted=>{if(!current)return;if(accepted)setVisible(requested);else latestRestore.current(visible?.id)});
  return()=>{current=false};
 },[requested?.id,visible?.id,navigationRef]);
 return requested?.id===visible?.id?requested:visible;
}
