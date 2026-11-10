import {useEffect,useRef,useState} from 'react';

export function isFileDrag(data){return Array.from(data?.types||[]).includes('Files')||!!data?.files?.length}
export function dropFiles(data){
 if(!isFileDrag(data))return [];
 const items=Array.from(data.items||[]).filter(item=>item.kind==='file');
 if(!items.length)return Array.from(data.files||[]);
 return items.map(item=>{
  if(item.webkitGetAsEntry?.()?.isDirectory)throw Error('Choose files, not folders. Folders are not uploaded.');
  const file=item.getAsFile();if(!file)throw Error('Could not read a dropped file. Choose it again with Attach files.');
  return file;
 });
}
export function useChatFileDrop({ownerKey,enabled,onFiles,onError}){
 const [active,setActive]=useState(false),depth=useRef(0);
 const clear=()=>{depth.current=0;setActive(false)};
 useEffect(()=>{clear()},[ownerKey,enabled]);
 useEffect(()=>{
  const escape=e=>{if(e.key==='Escape')clear()};
  window.addEventListener('blur',clear);window.addEventListener('dragend',clear);window.addEventListener('keydown',escape);
  return()=>{window.removeEventListener('blur',clear);window.removeEventListener('dragend',clear);window.removeEventListener('keydown',escape)};
 },[]);
 const blocked=()=>!enabled||!!document.querySelector('dialog[open],.a-overlay [role="dialog"]');
 // React portals retain their logical ancestry but are not chat drop targets.
 const inside=e=>e.currentTarget.contains(e.target);
 const enter=e=>{if(!inside(e)||!isFileDrag(e.dataTransfer))return;e.preventDefault();if(blocked()){clear();return;}depth.current++;setActive(true)};
 const over=e=>{if(!inside(e)||!isFileDrag(e.dataTransfer))return;e.preventDefault();e.dataTransfer.dropEffect=blocked()?'none':'copy';if(!blocked())setActive(true);else clear()};
 const leave=e=>{if(!inside(e)||!isFileDrag(e.dataTransfer)&&!depth.current)return;depth.current=Math.max(0,depth.current-1);if(!depth.current||e.relatedTarget&&!e.currentTarget.contains(e.relatedTarget))clear()};
 const drop=e=>{
  if(!inside(e)||!isFileDrag(e.dataTransfer))return;
  e.preventDefault();e.stopPropagation();clear();
  if(blocked()){onError('Attachments are unavailable in this view. Close the dialog or select an editable chat.');return;}
  try{const files=dropFiles(e.dataTransfer);if(!files.length)throw Error('No readable files were dropped.');onFiles(files)}catch(error){onError(error.message)}
 };
 return {active,handlers:{onDragEnterCapture:enter,onDragOverCapture:over,onDragLeaveCapture:leave,onDropCapture:drop}};
}
