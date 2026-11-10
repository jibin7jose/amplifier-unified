import React,{useEffect,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,ZoomIn,ZoomOut,Maximize,Download} from 'lucide-react';
import {getDocument,GlobalWorkerOptions,TextLayer} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {clientUrl} from './api';
import './pdf-preview.css';

GlobalWorkerOptions.workerSrc=workerUrl;
const fallback='Download the original PDF to open it in another viewer.';
export default function PdfPreview({canvas,report,act}){
 const stage=useRef(null),pageHost=useRef(null),[document,setDocument]=useState(null),[page,setPage]=useState(1),[zoom,setZoom]=useState(null),[width,setWidth]=useState(400),[busy,setBusy]=useState(true),[error,setError]=useState('');
 const version=canvas.selectedVersion??canvas.revision??1;
 useEffect(()=>{
  const element=stage.current,observer=new ResizeObserver(()=>setWidth(Math.max(100,Math.floor(element.clientWidth-24))));
  observer.observe(element);return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  let active=true;setDocument(null);setPage(1);setZoom(null);setError('');setBusy(true);report('pending','Loading PDF');
  const task=getDocument({url:clientUrl(`/api/canvas/${canvas.id}/download?version=${version}`),cMapUrl:'/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/vendor/pdfjs/standard_fonts/',wasmUrl:'/vendor/pdfjs/wasm/',isEvalSupported:false,enableXfa:false});
  const failed=message=>{if(active){setError(message);setBusy(false);report('error',message)}};
  task.onPassword=()=>{failed('This PDF is password-protected. '+fallback);task.destroy()};
  const timer=setTimeout(()=>{failed('This PDF took too long to load. '+fallback);task.destroy()},30000);
  task.promise.then(pdf=>{if(active)setDocument(pdf)}).catch(reason=>{if(active&&reason.name!=='PasswordException')failed('This PDF could not be opened. It may be damaged or unsupported. '+fallback)}).finally(()=>clearTimeout(timer));
  return()=>{active=false;clearTimeout(timer);task.destroy()};
 },[canvas.id,version,report]);
 useEffect(()=>{
  if(!document)return;
  let active=true,render,text;setBusy(true);setError('');pageHost.current.replaceChildren();report('pending','Rendering PDF page');
  document.getPage(page).then(async pdfPage=>{
   if(!active)return;
   const original=pdfPage.getViewport({scale:1}),scale=zoom??Math.min(2,width/original.width),viewport=pdfPage.getViewport({scale});
   // Bound the raster allocation even for unusually large PDF page sizes.
   const ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(16_000_000/(viewport.width*viewport.height)));
   const element=window.document.createElement('div');element.className='a-pdf-page';
   element.style.width=`${viewport.width}px`;element.style.height=`${viewport.height}px`;element.style.setProperty('--total-scale-factor',scale*pdfPage.userUnit);element.style.setProperty('--scale-round-x','1px');element.style.setProperty('--scale-round-y','1px');
   const bitmap=window.document.createElement('canvas');bitmap.width=Math.ceil(viewport.width*ratio);bitmap.height=Math.ceil(viewport.height*ratio);bitmap.style.width='100%';bitmap.style.height='100%';bitmap.setAttribute('aria-hidden','true');
   const layer=window.document.createElement('div');layer.className='a-pdf-text';element.append(bitmap,layer);pageHost.current.replaceChildren(element);
   render=pdfPage.render({canvasContext:bitmap.getContext('2d'),viewport,transform:ratio===1?null:[ratio,0,0,ratio,0,0]});
   text=new TextLayer({textContentSource:pdfPage.streamTextContent(),container:layer,viewport});
   await Promise.all([render.promise,text.render()]);
   if(active){setBusy(false);report('ready',`PDF page ${page} of ${document.numPages}`)}
  }).catch(reason=>{if(active&&reason.name!=='RenderingCancelledException'){const message='This PDF page could not be rendered. '+fallback;setError(message);setBusy(false);report('error',message)}});
  return()=>{active=false;render?.cancel();text?.cancel()};
 },[document,page,zoom,width,report]);
 const changePage=value=>{setPage(Math.max(1,Math.min(document.numPages,value)));stage.current.scrollTo(0,0)};
 return <div className="a-pdf-viewer">
  <div className="a-pdf-toolbar" role="group" aria-label="PDF controls">
   <button type="button" className="a-soft" data-action="canvas.download" onClick={()=>act('canvas.download',{id:canvas.id})}><Download/>Download original PDF</button>
   {document&&<>
   <div className="a-pdf-control-group">
   <button type="button" className="a-icon" aria-label="Previous PDF page" disabled={page===1} onClick={()=>changePage(page-1)}><ChevronLeft/></button>
   <label>Page <input aria-label="PDF page" type="number" min="1" max={document.numPages} value={page} onChange={event=>{const number=Number(event.target.value);if(Number.isInteger(number)&&number>0)changePage(number)}}/></label><span>of {document.numPages}</span>
   <button type="button" className="a-icon" aria-label="Next PDF page" disabled={page===document.numPages} onClick={()=>changePage(page+1)}><ChevronRight/></button>
   </div><div className="a-pdf-control-group">
   <button type="button" className="a-icon" aria-label="Zoom PDF out" disabled={zoom!==null&&zoom<=.25} onClick={()=>setZoom(Math.max(.25,(zoom??1)-.25))}><ZoomOut/></button>
   <span>{zoom===null?'Fit width':`${Math.round(zoom*100)}%`}</span>
   <button type="button" className="a-icon" aria-label="Zoom PDF in" disabled={zoom>=3} onClick={()=>setZoom(Math.min(3,(zoom??1)+.25))}><ZoomIn/></button>
   <button type="button" className="a-soft" onClick={()=>setZoom(null)}><Maximize/>Fit width</button>
   </div>
  </>}
  </div>
  {error?<p role="alert">{error}</p>:busy?<p role="status">Loading PDF page…</p>:null}
  <div ref={stage} className="a-pdf-stage" tabIndex={0} aria-label="PDF document" aria-busy={busy}><div ref={pageHost}/></div>
 </div>;
}
