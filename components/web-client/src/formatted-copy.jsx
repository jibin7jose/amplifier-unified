import React,{useEffect,useState} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import DOMPurify from 'dompurify';
import {Copy} from 'lucide-react';
import {writeClipboardText,clipboardNotice} from './clipboard-copy';

// Render the frozen Markdown source, never the live viewer DOM or its controls.
// No HTML from the document, remote images, executable SVG, or embedded apps.
export function formattedMarkdown(source){
 const html=renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
  img:({alt})=><span>{alt?`Image: ${alt}`:'Image'}</span>,
 }}>{source}</ReactMarkdown>);
 return DOMPurify.sanitize(html,{ALLOWED_TAGS:['p','br','h1','h2','h3','h4','h5','h6','strong','em','del','blockquote','pre','code','ul','ol','li','table','thead','tbody','tr','th','td','a','hr','span'],ALLOWED_ATTR:['href','title','start','align']});
}
export function FormattedCopy({source,identity,sourceUrl}){return <CopyButton key={identity} source={source} sourceUrl={sourceUrl}/>}
function CopyButton({source,sourceUrl}){
 const [stored,setStored]=useState(null),[loadError,setLoadError]=useState('');
 useEffect(()=>{
  if(!sourceUrl)return;
  const controller=new AbortController();setStored(null);setLoadError('');
  fetch(sourceUrl,{signal:controller.signal}).then(response=>{if(!response.ok)throw Error();return response.text()}).then(setStored).catch(error=>{if(error.name!=='AbortError')setLoadError('The complete document could not be loaded for copying.')});
  return()=>controller.abort();
 },[sourceUrl]);
 const text=sourceUrl?stored:source;
 const [notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 async function copy(){
  if(busy||text===null)return;
  setBusy(true);setNotice('');
  try{
   if(navigator.clipboard?.write&&globalThis.ClipboardItem){
    const item=new ClipboardItem({'text/html':new Blob([formattedMarkdown(text)],{type:'text/html'}),'text/plain':new Blob([text],{type:'text/plain'})});
    await navigator.clipboard.write([item]);setNotice('Copied formatted document.');
   }else{await writeClipboardText(text);setNotice('Formatted copying is unavailable; copied Markdown instead.')}
  }catch(error){setNotice(clipboardNotice(error))}
  finally{setBusy(false)}
 }
 return <><button type="button" className="a-soft" disabled={busy||text===null} onClick={copy}><Copy/>Copy formatted</button><span role="status" className="a-caption">{loadError||notice||(sourceUrl&&stored===null?'Loading complete document…':'')}</span></>;
}
