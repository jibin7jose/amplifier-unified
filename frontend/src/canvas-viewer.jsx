import {selectedReference} from './canvas-reference';
import {CanvasControl,CanvasPrimaryAction,usePrimaryCanvas} from './canvas-controls';
import {ImageActions} from './image-actions';
import {clientUrl} from './api';
import React,{lazy,Suspense,useEffect,useId,useMemo,useRef,useState} from 'react';
import DOMPurify from 'dompurify';
import {Code,Eye,Copy,ZoomIn,ZoomOut,Maximize,Check,AlertCircle} from 'lucide-react';
import {Markdown} from './markdown';
import {FormattedCopy} from './formatted-copy';
import {BlockCopy,CopyControl} from './block-copy.js';
import {MediaFigure,validateMediaMetadata} from './inline-media';
import {useDiagramNavigation} from './diagram-navigation';
import {SavedFileDownload} from './artifact-file-details';

const PdfPreview=lazy(()=>import('./pdf-preview'));
const viewAction=(canvas,act,patch)=>act('canvas.view',{id:canvas.id,patch});
function useReport(canvas,act,part='preview'){
 const ref=useRef(act);ref.current=act;
 return useMemo(()=> (status,message='')=>{if(canvas.id)return ref.current('canvas.report',{id:canvas.id,part,status,message:String(message).slice(0,2000)})},[canvas.id,part]);
}

export function GifCanvasViewer({canvas,act,reportStatus}){
 const report=useReport(canvas,act);
 const reference=useMemo(()=>{
  try{
   const value=canvas.mediaReference;
   if(!value||typeof value.messageId!=='string'||!value.messageId||value.messageId.length>200)
    throw Error('The retained GIF reference is unavailable.');
   validateMediaMetadata({items:[value.item],overflow:false,mediaRevision:0},canvas.sessionId);
   if(value.item.media.mimeType!=='image/gif')throw Error('This artifact is not a retained GIF.');
   return value;
  }catch(error){return {error:error.message}}
 },[canvas.mediaReference,canvas.sessionId]);
 const statusRef=useRef(reportStatus);statusRef.current=reportStatus;
 const reportMedia=useMemo(()=>payload=>{
  const status=payload.renderState==='loading'?'pending':payload.renderState;
  const message=payload.detail||`${payload.renderState} · ${payload.playback} · ${payload.presentedFrames} frames`;
  // Both client-local reports belong to one notification. Even a rejection
  // must wait for the other transport before MediaReporter can flush again.
  return Promise.allSettled([report(status,message),statusRef.current?.(status==='pending'?'loading':status,message)])
   .then(results=>{const failed=results.find(row=>row.status==='rejected');if(failed)throw failed.reason;return results.map(row=>row.value)});
 },[report]);
 useEffect(()=>{
  if(reference.error){report('error',reference.error);reportStatus?.('error',reference.error)}
 },[reference.error,report,reportStatus]);
 return reference.error?<p role="alert">{reference.error}</p>:<div className="a-canvas-viewer a-inline-media">
  <MediaFigure item={reference.item} messageId={reference.messageId} reportMedia={reportMedia}/>
 </div>;
}
function cleanSvg(svg){
 return DOMPurify.sanitize(svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:['foreignObject','script','image','a','use','animate','set'],FORBID_ATTR:['href','xlink:href']});
}
let mermaidPromise;
export function Diagram({kind,source,canvas,act,part='preview',embedded=false}){
 return <DiagramView key={[canvas.id,canvas.selectedVersion,canvas.resourceRevision,canvas.generation,kind,part].join(':')} {...{kind,source,canvas,act,part,embedded}}/>;
}
function DiagramView({kind,source,canvas,act,part,embedded}){
 const id=useId().replace(/[^a-zA-Z0-9]/g,''),report=useReport(canvas,act,part),[result,setResult]=useState({}),[natural,setNatural]=useState(null);
 const view=canvas.view||{},navigation=useDiagramNavigation(view,patch=>viewAction(canvas,act,patch),!embedded),{zoom,panX,panY}=navigation.value;
 useEffect(()=>{
  let active=true,worker,timer;setResult({});report('pending');
  const complete=value=>{if(!active)return;clearTimeout(timer);worker?.terminate();setResult(value);report(value.error?'error':'ready',value.error||'Diagram rendered')};
  if(source.length>50000){complete({error:'Diagrams must be 50,000 characters or smaller.'});return}
  if(kind==='dot'){
   worker=new Worker(new URL('./graphviz.worker.js',import.meta.url),{type:'module'});
   timer=setTimeout(()=>complete({error:'Diagram layout took too long. Simplify the graph and try again.'}),15000);
   worker.onmessage=e=>complete(e.data);worker.onerror=e=>complete({error:e.message||'Graph renderer failed'});
   worker.postMessage({source,engine:view.engine||'dot'});
  }else{
   mermaidPromise ||= import('mermaid').then(({default:m})=>{m.initialize({startOnLoad:false,securityLevel:'strict',maxTextSize:50000,maxEdges:500,suppressErrorRendering:true,theme:'base',themeVariables:{primaryColor:'#f3f2ff',primaryTextColor:'#24213e',primaryBorderColor:'#aaa1df',lineColor:'#8981b5',secondaryColor:'#eff6ff',tertiaryColor:'#f3f4f6',fontFamily:'Arial, sans-serif',fontSize:'14px'},flowchart:{htmlLabels:false},htmlLabels:false});return m});
   mermaidPromise.then(m=>m.render(`canvas${id}`,source)).then(({svg})=>complete({svg})).catch(e=>complete({error:e.message||String(e)}));
  }
  return()=>{active=false;clearTimeout(timer);worker?.terminate()};
 },[kind,source,view.engine,id,report]);
 const uri=useMemo(()=>result.svg?'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(cleanSvg(result.svg)):null,[result.svg]);
 const set=patch=>viewAction(canvas,act,patch),selected=result.nodes?.find(n=>n.name===view.node);
 const fit=natural&&navigation.size.width?Math.min(navigation.size.width/natural.width,navigation.size.height/natural.height):null;
 return <div className={`a-diagram ${embedded?'embedded':''}`}>
  {!embedded&&<div className="a-canvas-toolbar a-diagram-toolbar" role="group" aria-label="Diagram view">
   <button type="button" className="a-icon" aria-label="Zoom out" data-action="canvas.view" onClick={()=>navigation.zoom(1/1.2)}><ZoomOut/></button><span>{Math.round(zoom*100)}%</span>
   <button type="button" className="a-icon" aria-label="Zoom in" data-action="canvas.view" onClick={()=>navigation.zoom(1.2)}><ZoomIn/></button>
   <button type="button" className="a-soft" data-action="canvas.view" onClick={navigation.fit}><Maximize/>Fit</button>
   {kind==='dot'&&<select aria-label="Graph layout" data-action="canvas.view" value={view.engine||'dot'} onChange={e=>set({engine:e.target.value,node:''})}>{[['dot','Flow'],['neato','Network'],['fdp','Spread'],['sfdp','Large network'],['circo','Circular'],['twopi','Radial']].map(([engine,label])=><option key={engine} value={engine}>{label}</option>)}</select>}
   <span className="a-diagram-hint">Drag to pan · Scroll to zoom</span>
  </div>}
  {result.error?<div className="a-canvas-result error" role="alert"><AlertCircle/><span>{result.error}</span></div>:uri?<div ref={navigation.ref} className="a-diagram-stage" tabIndex={0} aria-label={embedded?'Diagram. Scroll to explore; arrow keys scroll.':'Diagram. Drag to pan; scroll to zoom. Arrow keys move; plus and minus zoom; zero fits.'} {...navigation.events}><img alt={canvas.title||'Diagram'} draggable="false" src={uri} onLoad={event=>setNatural({width:event.currentTarget.naturalWidth,height:event.currentTarget.naturalHeight})} style={embedded?undefined:{width:fit?natural.width*fit:undefined,height:fit?natural.height*fit:undefined,visibility:fit?'visible':'hidden',transform:`translate(${panX}px,${panY}px) scale(${zoom})`}}/></div>:<p role="status">Drawing diagram…</p>}
  {navigation.error&&<p role="status">{navigation.error}</p>}
  {!embedded&&result.nodes?.length>0&&<details className="a-node-inspector"><summary>Node details · {result.nodes.length}</summary><label>Inspect a node<select aria-label="Inspect graph node" data-action="canvas.view" value={view.node||''} onChange={e=>set({node:e.target.value})}><option value="">Choose a node…</option>{result.nodes.map(n=><option key={n.name} value={n.name}>{n.label&&n.label!=='\\N'?n.label+' ('+n.name+')':n.name}</option>)}</select></label>{selected&&<dl>{Object.entries(selected).map(([key,value])=><React.Fragment key={key}><dt>{key}</dt><dd>{typeof value==='object'?JSON.stringify(value):String(value)}</dd></React.Fragment>)}</dl>}</details>}
 </div>;
}
function CanvasMarkdown({canvas,act}){
 const overrides=useMemo(()=>({pre:({children})=><div className="a-canvas-code-block">{children}</div>,code:({className,children,node,...props})=>{
  const language=/language-(\w+)/.exec(className||'')?.[1],source=String(children).replace(/\n$/,'');
  return ['mermaid','dot','graphviz'].includes(language)?<Diagram kind={language==='mermaid'?'mermaid':'dot'} source={source} canvas={canvas} act={act} embedded part={`fence-${node?.position?.start?.offset||0}`}/>:<code className={className} {...props}>{children}</code>;
 }}),[canvas.id,act]);
 return <Markdown text={canvas.content} overrides={overrides} mapSource/>;
}
function CodePreview({text}){
 const [html,setHtml]=useState('');
 useEffect(()=>{let current=true;setHtml('');if(text.length<100000)import('highlight.js').then(({default:h})=>{const result=h.highlightAuto(text).value;if(current)setHtml(result)});return()=>{current=false}},[text]);
 return html?<pre className="a-canvas-code"><code dangerouslySetInnerHTML={{__html:html}}/></pre>:<pre className="a-canvas-code">{text}</pre>;
}
export function CanvasViewer({canvas,act,dispatch}){
 const primary=usePrimaryCanvas();
 const report=useReport(canvas,act),view=canvas.view||{},source=canvas.content||'',rich=!['text','image','pdf'].includes(canvas.kind);
 const reportCopy=useReport(canvas,act,'clipboard');
 const copyIdentity=[canvas.id,canvas.selectedVersion,canvas.revision,canvas.resourceRevision,canvas.generation].join(':');
 const inlineSourceCopy=canvas.kind!=='pdf'&&!canvas.contentResource&&(view.source||['text','code','mermaid','dot'].includes(canvas.kind));
 const textBody=useRef(null),quoteControl=useRef(null),quotePending=useRef(false),[selection,setSelection]=useState(null),[quoting,setQuoting]=useState(false),[quoteNotice,setQuoteNotice]=useState('');
 const referenceable=['markdown','text'].includes(canvas.kind)&&!view.source;
 useEffect(()=>{
  setSelection(null);setQuoteNotice('');
  if(!referenceable)return;
  const changed=()=>{if(quotePending.current||quoteControl.current?.contains(document.activeElement))return;setSelection(selectedReference(textBody.current,window.getSelection(),source));setQuoteNotice('')};
  document.addEventListener('selectionchange',changed);return()=>document.removeEventListener('selectionchange',changed);
 },[source,referenceable,canvas.id,canvas.resourceRevision]);
 async function referenceText(){
  if(!selection?.excerpt||quotePending.current)return;
  quotePending.current=true;setQuoting(true);setQuoteNotice('');
  try{const result=await act('canvas.reference',{id:canvas.id,sessionId:canvas.sessionId,version:canvas.selectedVersion||canvas.revision||canvas.latestVersion,...selection});if(result?.result?.status==='referenced'){setQuoteNotice('Reference added to your draft. Nothing was sent.');setSelection(null);window.getSelection()?.removeAllRanges()}}
  catch(error){setQuoteNotice(error.message||'The reference could not be added.')}
  finally{quotePending.current=false;setQuoting(false)}
 }
 const parsed=useMemo(()=>{if(!['json','jsonl'].includes(canvas.kind))return{};try{return{value:canvas.kind==='json'?JSON.parse(source):source.split('\n').filter(l=>l.trim()).map(l=>JSON.parse(l))}}catch(e){return{error:e.message}}},[canvas.kind,source]);
 useEffect(()=>{if(!['html','babylon','image','pdf','dot','mermaid'].includes(canvas.kind))report(parsed.error?'error':'ready',parsed.error||'Preview ready')},[canvas.id,canvas.kind,parsed.error,report]);
 const reports=Object.values(canvas.renderReports||{}),error=reports.find(r=>r.status==='error'),pending=reports.some(r=>r.status==='pending');
 return <div className="a-canvas-viewer">
  <CanvasControl><div className="a-canvas-toolbar">
   {rich&&<><button type="button" className="a-soft" aria-pressed={!view.source} data-action="canvas.view" onClick={()=>viewAction(canvas,act,{source:false})}><Eye/>Preview</button><button type="button" className="a-soft" aria-pressed={!!view.source} data-action="canvas.view" onClick={()=>viewAction(canvas,act,{source:true})}><Code/>Source</button></>}
   <span className="a-canvas-format">{canvas.kind}</span>
   {canvas.kind==='markdown'&&<FormattedCopy source={source} identity={copyIdentity} sourceUrl={canvas.contentResource?clientUrl(`/api/canvas/${canvas.id}/source?version=${canvas.selectedVersion??canvas.revision??1}`):undefined}/>}
  </div></CanvasControl>
  <CanvasPrimaryAction><div className="a-canvas-common-actions">
   {!['image','pdf'].includes(canvas.kind)&&(primary||!inlineSourceCopy&&!(view.source&&canvas.contentResource))&&<button type="button" className="a-icon" aria-label="Copy canvas source" data-action="canvas.copy" onClick={()=>act('canvas.copy',{id:canvas.id})}><Copy/><span>Copy</span></button>}
   {!['image','pdf'].includes(canvas.kind)&&dispatch&&<SavedFileDownload target={{id:canvas.id,version:canvas.selectedVersion??canvas.revision??canvas.latestVersion,sessionId:canvas.sessionId}} dispatch={dispatch} label="Download canvas source" compact/>}
  </div></CanvasPrimaryAction>
  {canvas.kind==='image'&&dispatch&&<ImageActions canvas={canvas} dispatch={dispatch}/>}
  {referenceable&&<div className="a-canvas-reference" ref={quoteControl}><button type="button" className="a-soft" data-action="canvas.reference" disabled={quoting||!selection?.excerpt} onMouseDown={event=>event.preventDefault()} onClick={referenceText}>{quoting?'Adding reference…':'Reference in chat'}</button><span role="status" className="a-caption">{quoteNotice||selection?.error||(selection?.excerpt?`${[...selection.excerpt].length} characters selected`:'Select document text to quote in your draft.')}</span></div>}
  <div className="a-canvas-preview" ref={textBody} tabIndex={referenceable?0:undefined} aria-label={referenceable?'Document text':undefined}>
   {inlineSourceCopy&&<CopyControl text={source} label="Copy canvas source" identity={copyIdentity} onResult={reportCopy}/>}
   {canvas.kind==='pdf'?<Suspense fallback={<p role="status">Loading PDF viewer…</p>}><PdfPreview canvas={canvas} report={report} act={act}/></Suspense>:view.source?(canvas.contentResource?<StoredSource key={copyIdentity} canvas={canvas}/>:<CodePreview text={source}/>):['html','babylon'].includes(canvas.kind)?<HtmlPreview canvas={canvas} act={act}/>:['mermaid','dot'].includes(canvas.kind)?<Diagram kind={canvas.kind} source={source} canvas={canvas} act={act}/>:canvas.kind==='markdown'?<CanvasMarkdown canvas={canvas} act={act}/>:canvas.kind==='image'?<img className="a-canvas-image" src={source} alt={canvas.title||'Workspace image'} onLoad={()=>report('ready','Image loaded')} onError={()=>report('error','This image could not be decoded')}/>:['json','jsonl'].includes(canvas.kind)?parsed.error?<BlockCopy text={parsed.error} label="Copy preview error" identity={copyIdentity}><div className="a-canvas-result error" role="alert">{parsed.error}</div></BlockCopy>:<StructuredData value={parsed.value} canvas={canvas} act={act}/>:canvas.kind==='code'?<CodePreview text={source}/>:<pre className="a-canvas-plain"><span data-canvas-source-start="0" data-canvas-source-end={source.length} data-canvas-literal="true">{source}</span></pre>}
  </div>
  <CanvasControl inline={!!error||pending}><div className={`a-canvas-result ${error?'error':pending?'':'success'}`} role="status">{error?<AlertCircle/>:pending?null:<Check/>}<span>{error?error.message||'Preview needs attention':pending?'Rendering…':canvas.renderReports?.clipboard?.message|| (['html','babylon'].includes(canvas.kind)?'Isolated HTML preview':'Ready')}</span></div></CanvasControl>
 </div>;
}
function StructuredData({value,canvas,act}){
 const query=canvas.view?.query||'',rows=Array.isArray(value)?value:[value],filtered=rows.map((v,i)=>({v,i})).filter(({v})=>!query||JSON.stringify(v).toLowerCase().includes(query.toLowerCase()));
 return <div className="a-canvas-data"><input aria-label="Filter data records" type="search" placeholder="Filter records…" value={query} data-action="canvas.view" onChange={e=>viewAction(canvas,act,{query:e.target.value})}/><small>{filtered.length} of {rows.length} records</small>{filtered.slice(0,200).map(({v,i})=>{const text=JSON.stringify(v,null,2);return <BlockCopy key={i} text={text} label={`Copy record ${i+1} as JSON`} identity={[canvas.id,canvas.selectedVersion,canvas.resourceRevision].join(':')}><pre>{text}</pre></BlockCopy>})}{filtered.length>200&&<p>Showing the first 200 matches. Narrow the filter to see more.</p>}</div>;
}

function HtmlPreview({canvas,act}){
 const frame=useRef(null),report=useReport(canvas,act),actRef=useRef(act),sent=useRef(null);actRef.current=act;
 useEffect(()=>{
  report('pending','Loading HTML document');
  const receive=e=>{
   if(e.source!==frame.current?.contentWindow||e.data?.id!==canvas.id)return;
   if(e.data.type==='canvas-render'&&['ready','error'].includes(e.data.status))report(e.data.status,String(e.data.message||'').slice(0,2000));
   if(e.data.type==='canvas-snapshot'){
    const data=e.data.document;if(!data||typeof data.text!=='string'||!Array.isArray(data.controls))return;
    const document={text:data.text.slice(0,16000),controls:data.controls.slice(0,100).map(c=>({id:String(c.id||'').slice(0,100),tag:String(c.tag||'').slice(0,30),type:String(c.type||'').slice(0,30),label:String(c.label||'').slice(0,200),value:String(c.value||'').slice(0,4000),disabled:!!c.disabled}))};
    actRef.current('canvas.snapshot',{id:canvas.id,document});
   }
  };
  window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
 },[canvas.id,report]);
 useEffect(()=>{const request=canvas.interaction;if(request&&sent.current!==request.requestId){sent.current=request.requestId;frame.current?.contentWindow?.postMessage({type:'canvas-interact',...request},'*')}},[canvas.interaction]);
 return <iframe ref={frame} title={canvas.title||'Interactive canvas'} className="a-canvas-html" sandbox="allow-scripts" referrerPolicy="no-referrer" src={clientUrl(`/api/canvas/${canvas.id}/document`+(canvas.viewId?'?'+new URLSearchParams({viewId:canvas.viewId,resourceId:canvas.id,resourceRevision:canvas.resourceRevision,generation:canvas.generation}):''))}/>;
}


function StoredSource({canvas}){
 const [result,setResult]=useState({});
 useEffect(()=>{const controller=new AbortController();setResult({});fetch(clientUrl(`/api/canvas/${canvas.id}/source?version=${canvas.selectedVersion??canvas.revision??1}`),{signal:controller.signal}).then(async response=>{if(!response.ok)throw Error('The saved source could not be loaded.');return response.text()}).then(text=>setResult({text})).catch(error=>{if(error.name!=='AbortError')setResult({error:error.message})});return()=>controller.abort()},[canvas.id,canvas.selectedVersion,canvas.revision]);
 return result.error?<p role="alert">{result.error}</p>:result.text===undefined?<p role="status">Loading saved source…</p>:<BlockCopy text={result.text} label="Copy canvas source"><CodePreview text={result.text}/></BlockCopy>;
}
