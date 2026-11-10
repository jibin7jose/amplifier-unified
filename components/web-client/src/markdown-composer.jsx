import React,{useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Link,Bold,Italic,Code,Strikethrough} from 'lucide-react';
import 'prosemirror-view/style/prosemirror.css';
import {EditorView} from 'prosemirror-view';
import {composerState,composerMarkdown,composerDocument,composerSendDocument,insertQuote,composerEnter,pasteMarkdown,composerClipboard,composerFormat,formatComposerBlock,setComposerLink,moveComposerBoundary,composerSelectionEditable,toggleComposerMark} from './markdown-composer.js';
import {quoteNodeView,refreshQuoteControls} from './composer-quote-view.js';
import {clipboardPaste} from './clipboard-paste';
import {resizeComposer,observeComposerLayout} from './composer';
import './composer-formatting.css';

const quoteCount=doc=>{let count=0;doc.forEach(node=>{if(node.type.name==='reference')count++;});return count;};

export function MarkdownComposer({value,document:documentValue,onChange,onSend,onFiles,onReveal,onQuoteRemoved,quoteApi,disabled=false,readOnly=false,pasteDisabled=false,editorRef}){
 const host=useRef(null),editor=useRef(null),latest=useRef(null),published=useRef(value),publishedDocument=useRef(documentValue);
 const toolbar=useRef(null),refresh=useRef(()=>{}),dismissed=useRef(null);
 const insertionMode=useRef('caret');
 const [selection,setSelection]=useState(null),[linkOpen,setLinkOpen]=useState(false),[link,setLink]=useState(''),[linkError,setLinkError]=useState('');
 const openLink=()=>{
  const view=editor.current;if(!view||view.state.selection.empty||!composerSelectionEditable(view.state)||view.state.selection.$from.parent.type.spec.code)return;
  let href='';view.state.doc.nodesBetween(view.state.selection.from,view.state.selection.to,node=>{const mark=node.marks.find(mark=>mark.type.name==='link');if(mark)href=mark.attrs.href;});
  setLink(href);setLinkError('');setLinkOpen(true);
 };
 latest.current={value,onChange,onSend,onFiles,onReveal,onQuoteRemoved,disabled,readOnly,pasteDisabled};
 useLayoutEffect(()=>{
  const editable=()=>!latest.current.disabled&&!latest.current.readOnly;
  const view=new EditorView(host.current,{
   state:composerState(latest.current.value,documentValue),editable,clipboardTextSerializer:composerClipboard,
   nodeViews:{reference:(node,view,getPos)=>quoteNodeView(node,view,getPos,()=>latest.current)},
   attributes:{role:'textbox','aria-label':'Message Amplifier','aria-multiline':'true','data-action':'view.update','data-private':'true','data-view-label':'Message Amplifier',class:'a-markdown-composer',spellcheck:'true'},
   dispatchTransaction(transaction){
    const before=view.state.doc;view.updateState(view.state.apply(transaction));
    if(transaction.docChanged){published.current=composerMarkdown(view.state.doc);publishedDocument.current=composerDocument(view.state.doc,!!publishedDocument.current);latest.current.onChange(published.current,publishedDocument.current);
     // The Remove quote control, keyboard deletion and undo all land here; report how many remain.
     const left=quoteCount(view.state.doc);if(left<quoteCount(before))latest.current.onQuoteRemoved?.(left);}
    refreshQuoteControls(view);
    view.dom.dataset.empty=String(view.state.doc.childCount===1&&view.state.doc.firstChild.type.name==='paragraph'&&view.state.doc.firstChild.content.size===0);
    resizeComposer(view.dom);
    refresh.current();
   },
   handleKeyDown(view,event){
    if(!editable()||view.composing||event.isComposing||event.keyCode===229)return false;
    if(moveComposerBoundary(view,event)){event.preventDefault();return true;}
    if(event.key==='Escape'&&!view.state.selection.empty){dismissed.current=view.state.selection.from+':'+view.state.selection.to;setSelection(null);setLinkOpen(false);return true;}
    if(event.altKey&&event.key==='F10'&&!view.state.selection.empty){
     event.preventDefault();refresh.current();requestAnimationFrame(()=>toolbar.current?.querySelector('button:not(:disabled),select')?.focus());return true;
    }
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'&&!view.state.selection.empty){event.preventDefault();openLink();return true;}
    if(event.key==='Enter'){
     event.preventDefault();
     if(!composerEnter(view,{continueWriting:event.shiftKey||event.ctrlKey,exit:event.altKey}))latest.current.onSend();
     return true;
    }
    return false;
   },
   handleDOMEvents:{
    // Let the browser/IME commit composition, but do not run later editor
    // keymaps (whose Enter binding would otherwise split a paragraph).
    // DOM handlers do not prevent the native default merely by returning true.
    keydown(view,event){return view.composing||event.isComposing||event.keyCode===229;},
    beforeinput(view,event){
     // Virtual keyboards may emit beforeinput without a usable keydown.
     if(editable()&&!view.composing&&!event.isComposing&&['insertParagraph','insertLineBreak'].includes(event.inputType)){
      event.preventDefault();if(!composerEnter(view,{continueWriting:event.inputType==='insertLineBreak'}))latest.current.onSend();return true;
     }
     return false;
    },
    contextmenu(){
     // Keep native copy/paste/spelling. Our same selection toolbar remains
     // available after the native menu closes; Alt+F10 focuses it directly.
     refresh.current();return false;
    },
    paste(view,event){
     if(!editable()||latest.current.pasteDisabled){event.preventDefault();return true;}
     const paste=clipboardPaste(event.clipboardData);
     event.preventDefault();
     if(paste.text)pasteMarkdown(view,paste.text);
     if(paste.files.length)latest.current.onFiles(paste.files);
     return true;
    },
    drop(view,event){
     if(!editable()||latest.current.pasteDisabled){event.preventDefault();return true;}
     if(event.dataTransfer?.files.length){event.preventDefault();event.stopPropagation();latest.current.onFiles([...event.dataTransfer.files]);return true;}
     // External text drops follow the same plain-text/Markdown policy as paste.
     if(!view.dragging&&event.dataTransfer?.getData('text/plain')){event.preventDefault();pasteMarkdown(view,event.dataTransfer.getData('text/plain'));return true;}
     if(!view.dragging&&event.dataTransfer?.getData('text/html')){event.preventDefault();pasteMarkdown(view,event.dataTransfer.getData('text/html'));return true;}
     return false;
    },
   },
  });
  editor.current=view;editorRef.current=view.dom;
  let alive=true,usedCaret=false;
  const remember=()=>{usedCaret=true;};
  view.dom.addEventListener('focus',remember);
  if(quoteApi)quoteApi.current={
   capture:()=>({doc:view.state.doc,mode:insertionMode.current,pos:usedCaret&&insertionMode.current==='caret'?view.state.selection.to:view.state.doc.content.size}),
   insert:(quote,target)=>{if(!alive||!editable())throw Error('The composer changed. Add the quote again.');insertQuote(view,quote,target);usedCaret=true;},
   document:()=>composerSendDocument(view.state.doc,!!publishedDocument.current),
   append:text=>{if(!alive||!editable()||view.composing)throw Error('Finish the current edit before dictating.');if(text)view.dispatch(view.state.tr.insertText(text,view.state.doc.content.size-1));},
  };
  refreshQuoteControls(view);
  let frame;
  const update=()=>{
   const {from,to,empty}=view.state.selection,key=from+':'+to;
   const focused=view.hasFocus()||toolbar.current?.contains(document.activeElement);
   if(empty||!editable()||!focused||!composerSelectionEditable(view.state)||dismissed.current===key){setSelection(null);if(empty||!focused)setLinkOpen(false);return;}
   const a=view.coordsAtPos(from),b=view.coordsAtPos(to),bounds=view.dom.getBoundingClientRect();
   if(b.bottom<bounds.top||a.top>bounds.bottom){setSelection(null);return;}
   setSelection({...composerFormat(view.state),left:Math.max(8,Math.min(Math.max(a.left,bounds.left),window.innerWidth-Math.min(330,window.innerWidth-16)-8)),top:Math.max(8,Math.min(Math.max(a.top,bounds.top)-52,window.innerHeight-160))});
  };
  const schedule=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(update);};
  refresh.current=()=>{dismissed.current=null;schedule();};
  const resizeObserver=new ResizeObserver(schedule);resizeObserver.observe(view.dom);
  document.addEventListener('selectionchange',schedule);document.addEventListener('focusin',schedule);
  window.addEventListener('resize',schedule);window.addEventListener('scroll',schedule,true);
  view.dom.dataset.empty=String(!latest.current.value);
  resizeComposer(view.dom);
  const stopLayout=observeComposerLayout(view.dom);
  return()=>{alive=false;stopLayout();view.dom.removeEventListener('focus',remember);if(quoteApi)quoteApi.current=null;resizeObserver.disconnect();cancelAnimationFrame(frame);refresh.current=()=>{};document.removeEventListener('selectionchange',schedule);document.removeEventListener('focusin',schedule);window.removeEventListener('resize',schedule);window.removeEventListener('scroll',schedule,true);view.destroy();editor.current=null;if(editorRef.current===view.dom)editorRef.current=null;};
 },[]);
 useLayoutEffect(()=>{
  const view=editor.current;if(!view)return;
  view.setProps({editable:()=>!disabled&&!readOnly});
  view.dom.setAttribute('aria-disabled',String(disabled));view.dom.setAttribute('aria-readonly',String(readOnly));view.dom.setAttribute('aria-busy',String(readOnly));
  // Do not rebuild selection or undo history for our own autosave echoes.
  if((value!==published.current||JSON.stringify(documentValue??null)!==JSON.stringify(publishedDocument.current??null))&&!view.composing){
   published.current=value;publishedDocument.current=documentValue;view.updateState(composerState(value,documentValue));refreshQuoteControls(view);
   view.dom.dataset.empty=String(!value);resizeComposer(view.dom);
  }
  refresh.current();
 },[value,documentValue,disabled,readOnly]);
 const dismiss=()=>{const view=editor.current;dismissed.current=view.state.selection.from+':'+view.state.selection.to;setSelection(null);setLinkOpen(false);view.focus();};
 const mark=name=>toggleComposerMark(editor.current,name);
 return <><div className="a-markdown-composer-host" ref={host}/>{documentValue?.segments?.some(part=>part.type==='quote')&&<label className="a-caption">Add quotes <select aria-label="Quote insertion" defaultValue="caret" onChange={event=>{insertionMode.current=event.target.value;}}><option value="caret">At remembered caret</option><option value="append">At end</option></select></label>}{selection&&!disabled&&!readOnly&&createPortal(
  <div className="a-composer-format" ref={toolbar} style={{left:selection.left,top:selection.top}} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismiss();}}}>
   <div role="toolbar" aria-label="Format selected text" className="a-composer-format-row">
    <button type="button" aria-label="Link" disabled={selection.literal} title="Link (Ctrl/⌘ K)" aria-pressed={selection.link} onMouseDown={e=>e.preventDefault()} onClick={openLink}><Link size={18}/></button>
    <button type="button" aria-label="Bold" disabled={selection.literal} title="Bold (Ctrl/⌘ B)" aria-pressed={selection.bold} onMouseDown={e=>e.preventDefault()} onClick={()=>mark('strong')}><Bold size={18}/></button>
    <button type="button" aria-label="Italic" disabled={selection.literal} title="Italic (Ctrl/⌘ I)" aria-pressed={selection.italic} onMouseDown={e=>e.preventDefault()} onClick={()=>mark('em')}><Italic size={18}/></button>
    <button type="button" aria-label="Inline code" disabled={selection.literal} title="Inline code (Ctrl/⌘ `)" aria-pressed={selection.code} onMouseDown={e=>e.preventDefault()} onClick={()=>mark('code')}><Code size={18}/></button>
    <button type="button" aria-label="Strikethrough" disabled={selection.literal} title="Strikethrough (Ctrl/⌘ Shift X)" aria-pressed={selection.strike} onMouseDown={e=>e.preventDefault()} onClick={()=>mark('strike')}><Strikethrough size={18}/></button>
    <select aria-label="Text style" value={selection.block} onChange={e=>formatComposerBlock(editor.current,e.target.value)}>
     {selection.block==='table'?<option value="table" disabled>Table</option>:<>
      <option value="text">Text</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option><option value="h4">Heading 4</option><option value="h5">Heading 5</option><option value="h6">Heading 6</option><option value="ordered_list">Numbered list</option><option value="bullet_list">Bulleted list</option><option value="task_list">Task list</option><option value="blockquote">Block quote</option><option value="code_block">Code block</option>
     </>}<option value="markdown_source">Markdown source</option>
    </select>
   </div>
   {linkOpen&&<form className="a-composer-link" onSubmit={e=>{e.preventDefault();e.stopPropagation();if(setComposerLink(editor.current,link)){setLinkOpen(false);setLinkError('');}else setLinkError('Use an http, https, mailto, or relative link.');}}>
    <label>Link address<input data-private="true" autoFocus aria-label="Link address" placeholder="https://example.com" value={link} onChange={e=>setLink(e.target.value)}/></label>
    <div><button type="submit">Apply</button><button type="button" onClick={()=>{setComposerLink(editor.current,'');setLinkOpen(false);}}>Remove link</button></div>
    {linkError&&<p role="alert">{linkError}</p>}
   </form>}
  </div>,host.current.closest('#amp-one')||document.body)}</>;
}
