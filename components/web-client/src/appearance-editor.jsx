import {useCallback,useEffect,useRef,useState} from 'react';

// Like other tab-private view drafts, this survives reload in this browser tab.
// Never persist a preview flag or replay a preview/apply action on restoration.
export const appearanceEditorKey='amplifier.appearance-editor.v1';
function valid(value){
 return value&&typeof value.name==='string'&&value.name.length<=100&&typeof value.css==='string'&&value.css.length<=1000000&&typeof value.description==='string'&&value.description.length<=240&&typeof value.dirty==='boolean'&&(
  value.entry===null||value.entry&&typeof value.entry.id==='string'&&typeof value.entry.name==='string'&&['saved','built-in'].includes(value.entry.source)&&/^[a-f0-9]{64}$/.test(value.entry.fingerprint||''));
}
function load(storage,fallback){
 try{const value=JSON.parse(storage()?.getItem(appearanceEditorKey)||'null');if(valid(value))return value}catch{}
 return {entry:null,name:'Amplifier Unified',css:fallback,description:'',dirty:false};
}
const browserStorage=()=>globalThis.sessionStorage;
export function useAppearanceEditor(theme,fallback,{storage=browserStorage}={}){
 const [draft,setState]=useState(()=>load(storage,fallback)),current=useRef(draft);
 const [storageError,setStorageError]=useState('');
 const setDraft=useCallback(update=>{
  const next=typeof update==='function'?update(current.current):update;
  if(next===current.current)return;
  // Entries need identity/metadata only. The editable CSS has one canonical copy.
  const {css:entryCss,...entry}=next.entry||{};
  const value={...next,entry:next.entry?entry:null};
  current.current=value;setState(value);
  try{const target=storage();if(!target)throw Error('Browser storage unavailable');target.setItem(appearanceEditorKey,JSON.stringify(value));setStorageError('')}
  catch{setStorageError('Your appearance draft stays in this tab, but could not be saved for reload. Keep this tab open.')}
 },[storage]);
 // Each published object is a revision token for the entire draft, including
 // its target/fingerprint. Compare inside the persistent owner, not a mounted
 // Settings closure. Even editing away and back invalidates an older token.
 const compareAndSetDraft=useCallback((expected,update)=>{
  if(current.current!==expected)return false;
  setDraft(update);return true;
 },[setDraft]);
 useEffect(()=>{
  if(!theme)return;
  setDraft(value=>value.entry||value.dirty?value:{...value,name:theme.name||'Amplifier Unified',css:theme.css||fallback});
 },[theme?.name,theme?.css,fallback,setDraft]);
 return {draft,setDraft,compareAndSetDraft,storageError};
}