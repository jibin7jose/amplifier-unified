import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {X,Download,Minus,Plus,Maximize} from 'lucide-react';
import {imageLayout,imageAttachment} from './attachment-view.js';
import {isFileDrag} from './chat-file-drop.js';
import './attachment-preview.css';

// Browser-local transient inspection. Never navigates Canvas or calls a model.
export function AttachmentPreview({file,privateDraft,opener,onClose}){
 const dialog=useRef(null),stage=useRef(null),close=useRef(null),pointer=useRef(null),previous=useRef(null),epoch=useRef(0);
 const [natural,setNatural]=useState(null),[viewport,setViewport]=useState([0,0]),[zoom,setZoom]=useState(null),[phase,setPhase]=useState('loading'),[attempt,setAttempt]=useState(0),[notice,setNotice]=useState('');
 const valid=imageAttachment(file),layout=natural&&imageLayout(natural,viewport,zoom);
 useLayoutEffect(()=>{
  const node=dialog.current;
  node.showModal();close.current?.focus({preventScroll:true});
  // Native dialog makes the rest of the document inert. Do not mutate Canvas.
  const key=event=>{
   if(!node.open)return;
   if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();onClose();return;}
   if(event.key==='Tab'){
    const items=[...node.querySelectorAll('button:not(:disabled),a[href],[tabindex="0"]')].filter(x=>x.getClientRects().length);
    const first=items[0],last=items.at(-1);
    if(!items.includes(document.activeElement)||event.shiftKey&&document.activeElement===first){event.preventDefault();(event.shiftKey?last:first)?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
   }
  };
  document.addEventListener('keydown',key,true);
  return()=>{epoch.current++;document.removeEventListener('keydown',key,true);node.close();if(opener?.isConnected)opener.focus({preventScroll:true});else document.querySelector('.a-markdown-composer')?.focus({preventScroll:true});};
 },[]);
 useLayoutEffect(()=>{
  const node=stage.current;const measure=()=>setViewport([node.clientWidth,node.clientHeight]);
  measure();const observer=new ResizeObserver(measure);observer.observe(node);
  window.visualViewport?.addEventListener('resize',measure);
  return()=>{observer.disconnect();window.visualViewport?.removeEventListener('resize',measure)};
 },[]);
 useEffect(()=>{
  const ticket=++epoch.current;setNatural(null);setZoom(null);previous.current=null;setPhase(valid?'loading':'unavailable');
  if(!valid)return;
  const image=new Image();let alive=true;
  image.onload=async()=>{
   try{await image.decode();if(!alive||ticket!==epoch.current)return;
    if(!image.naturalWidth||!image.naturalHeight)throw Error('Invalid image');
    setNatural([image.naturalWidth,image.naturalHeight]);setPhase('ready');
   }catch{if(alive&&ticket===epoch.current)setPhase('error')}
  };
  image.onerror=()=>{if(alive&&ticket===epoch.current)setPhase('error')};image.src=file.url;
  return()=>{alive=false;image.onload=null;image.onerror=null;image.removeAttribute('src')};
 },[file.id,file.url,attempt,valid]);
 useLayoutEffect(()=>{
  if(!layout)return;
  const node=stage.current,old=previous.current;
  if(zoom===null||!old){node.scrollLeft=(layout.planeWidth-viewport[0])/2;node.scrollTop=(layout.planeHeight-viewport[1])/2;}
  else{
   node.scrollLeft=(old.left+old.vw/2)/old.width*layout.planeWidth-viewport[0]/2;
   node.scrollTop=(old.top+old.vh/2)/old.height*layout.planeHeight-viewport[1]/2;
  }
  previous.current={left:node.scrollLeft,top:node.scrollTop,vw:viewport[0],vh:viewport[1],width:layout.planeWidth,height:layout.planeHeight};
 },[layout?.scale,viewport[0],viewport[1]]);
 const change=value=>{setZoom(value);setNotice(value===null?'Image fitted without upscaling.':`${Math.round(value*100)}% image scale.`)};
 const stopPan=event=>{const p=pointer.current;pointer.current=null;if(p&&stage.current.hasPointerCapture(p.id))stage.current.releasePointerCapture(p.id)};
 const blockFile=event=>{if(isFileDrag(event.dataTransfer)){event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect='none';setNotice('Close the image preview before attaching files.')}};
 const keyboard=event=>{
  if(event.ctrlKey||event.metaKey||event.altKey||!layout)return;
  if(['+','=','-','0','1'].includes(event.key)){event.preventDefault();change(event.key==='0'?null:event.key==='1'?1:Math.max(layout.fit,Math.min(8,layout.scale*(event.key==='-'?1/1.25:1.25))))}
 };
 const backdrop=useRef(false);
 return createPortal(<dialog ref={dialog} className="a-image-preview" data-part="attachment-preview" data-private={privateDraft||undefined} aria-label="Image preview" aria-describedby="attachment-preview-help" onKeyDown={e=>e.stopPropagation()} onCancel={e=>{e.preventDefault();onClose()}}
  onDragEnterCapture={blockFile} onDragOverCapture={blockFile} onDropCapture={blockFile}
  onPointerDown={e=>{backdrop.current=e.target===e.currentTarget}} onClick={e=>{if(backdrop.current&&e.target===e.currentTarget)onClose();backdrop.current=false}}>
  <div className="a-image-preview-card">
   <header><div><strong title={file.name}>{file.name}</strong><small>{natural?`${natural[0]} × ${natural[1]} · original unchanged`:'Original attachment'}</small></div><button ref={close} type="button" className="a-icon" data-tooltip="off" aria-label="Close image preview" onClick={onClose}><X/></button></header>
   <div className="a-image-preview-tools" role="group" aria-label="Image zoom controls">
    <button type="button" disabled={!layout} aria-pressed={zoom===null} onClick={()=>change(null)}><Maximize size={16}/> Fit</button>
    <button type="button" disabled={!layout} onClick={()=>change(1)}>100%</button>
    <button type="button" disabled={!layout||layout.scale<=layout.fit} aria-label="Zoom out" onClick={()=>change(Math.max(layout.fit,layout.scale/1.25))}><Minus size={16}/></button>
    <output aria-label="Image scale">{layout?`${Math.round(layout.scale*1000)/10}%`:'—'}</output>
    <button type="button" disabled={!layout||layout.scale>=8} aria-label="Zoom in" onClick={()=>change(Math.min(8,layout.scale*1.25))}><Plus size={16}/></button>
    {valid&&<a href={file.url} download={file.name} className="a-image-download" aria-label="Download original"><Download size={16}/><span>Download original</span></a>}
   </div>
   <div ref={stage} className="a-image-preview-stage" tabIndex={0} aria-label="Image viewport" aria-busy={phase==='loading'} onKeyDown={keyboard}
    onScroll={()=>{if(previous.current){previous.current.left=stage.current.scrollLeft;previous.current.top=stage.current.scrollTop}}}
    onPointerDown={e=>{if(e.pointerType!=='mouse'||e.button!==0||!layout||(layout.width<=viewport[0]&&layout.height<=viewport[1]))return;e.preventDefault();stage.current.focus({preventScroll:true});pointer.current={id:e.pointerId,x:e.clientX,y:e.clientY,left:stage.current.scrollLeft,top:stage.current.scrollTop};stage.current.setPointerCapture(e.pointerId)}}
    onPointerMove={e=>{const p=pointer.current;if(!p||p.id!==e.pointerId)return;stage.current.scrollLeft=p.left+p.x-e.clientX;stage.current.scrollTop=p.top+p.y-e.clientY}}
    onPointerUp={stopPan} onPointerCancel={stopPan} onLostPointerCapture={()=>{pointer.current=null}}>
    {phase==='ready'&&layout?<div className="a-image-preview-plane" style={{width:layout.planeWidth,height:layout.planeHeight}}><img key={file.id+':'+attempt} src={file.url} alt={file.name} draggable="false" style={{width:layout.width,height:layout.height}}/></div>:
     <div className="a-image-preview-status" role={phase==='loading'?'status':'alert'}><p>{phase==='loading'?'Loading image…':'The original is unavailable or could not be decoded. Your draft and original bytes are unchanged.'}</p>{phase==='error'&&<button type="button" onClick={()=>setAttempt(x=>x+1)}>Retry preview</button>}</div>}
   </div>
   <footer><span id="attachment-preview-help">Scroll or drag to pan. Touch pan and browser pinch stay native. 100% uses natural CSS pixels.</span><span role="status" aria-live="polite">{notice}</span></footer>
  </div>
 </dialog>,document.getElementById('amp-one')||document.body);
}
