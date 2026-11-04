import React,{useEffect,useRef,useState} from 'react';
import {thumbnailScene} from './appearance-thumbnail-document';

export function AppearanceThumbnail({item,mode,catalog,act}){
 const box=useRef(null),api=useRef(act);api.current=act;
 const [visible,setVisible]=useState(false),[width,setWidth]=useState(0),[result,setResult]=useState(null);
 useEffect(()=>{
  const element=box.current,resize=()=>setWidth(element.clientWidth);
  resize();
  const observer=new ResizeObserver(resize);observer.observe(element);
  const intersection=new IntersectionObserver(entries=>{
   if(entries.some(entry=>entry.isIntersecting)){setVisible(true);intersection.disconnect()}
  },{rootMargin:'160px'});
  intersection.observe(element);
  return()=>{observer.disconnect();intersection.disconnect()};
 },[]);
 const key=JSON.stringify([item.id,item.fingerprint,mode,thumbnailScene.version]);
 useEffect(()=>{
  if(!visible)return;
  let current=true;
  Promise.all([catalog.read(item,(...args)=>api.current(...args)),import('./appearance-thumbnail-scene')])
   .then(([entry,scene])=>{if(current)setResult({key,html:scene.renderAppearanceThumbnail(entry.css,mode)})})
   .catch(()=>{if(current)setResult({key,error:true})});
  return()=>{current=false};
 },[visible,key,catalog]);
 const ready=result?.key===key&&!result.error;
 return <div ref={box} className="a-appearance-thumbnail" data-thumbnail-state={ready?'ready':result?.key===key?'error':'loading'} data-appearance-fingerprint={item.fingerprint} data-thumbnail-mode={mode} aria-hidden="true">
  {ready?<iframe title={`${item.name}: ${mode} sample`} sandbox="" referrerPolicy="no-referrer" tabIndex={-1} srcDoc={result.html} style={{width:thumbnailScene.width,height:thumbnailScene.height,transform:`scale(${width/thumbnailScene.width})`}}/>:
   <span className="a-thumbnail-placeholder">{result?.key===key?'Preview unavailable':'Preparing preview…'}</span>}
  <span className="a-thumbnail-mode">{mode==='dark'?'Dark':'Light'}</span>
 </div>;
}