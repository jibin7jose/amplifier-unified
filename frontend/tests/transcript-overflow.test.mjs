import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createChatScroll} from '../src/chat-scroll.js';

const LEFT_GUTTER='--_a-transcript-gutter-left';
const RIGHT_GUTTER='--_a-transcript-gutter-right';
const railCss=await readFile(new URL('../src/conversation-rail.css',import.meta.url),'utf8');
const maskStart=railCss.indexOf('/* Shared transcript mask');
const maskEnd=railCss.indexOf('\n.a-rail-marks',maskStart);
const maskCss=railCss.slice(maskStart,maskEnd);

class InlineStyle{
 constructor(){this.values=new Map();this.priorities=new Map();this.overflowAnchor='auto'}
 getPropertyValue(name){return this.values.get(name)||''}
 getPropertyPriority(name){return this.priorities.get(name)||''}
 setProperty(name,value,priority=''){this.values.set(name,String(value));if(priority)this.priorities.set(name,priority);else this.priorities.delete(name)}
 removeProperty(name){const value=this.getPropertyValue(name);this.values.delete(name);this.priorities.delete(name);return value}
}

class TrackedEventTarget extends EventTarget{
 listeners=new Map();
 addEventListener(type,listener,options){super.addEventListener(type,listener,options);if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(listener)}
 removeEventListener(type,listener,options){super.removeEventListener(type,listener,options);this.listeners.get(type)?.delete(listener)}
 listenerCount(){return [...this.listeners.values()].reduce((count,listeners)=>count+listeners.size,0)}
}

function append(parent,child){
 remove(child);parent.children.push(child);child.parentElement=parent;return child;
}
function remove(child){
 const parent=child.parentElement;if(parent)parent.children.splice(parent.children.indexOf(child),1);child.parentElement=null;
}
function element(localName){
 const node={localName,children:[],parentElement:null,matches:()=>false};
 if(localName==='iframe')Object.defineProperties(node,{
  contentDocument:{get(){assert.fail('focus observation must not read the embedded document')}},
  contentWindow:{get(){assert.fail('focus observation must not read the embedded window')}},
 });
 return node;
}

function harness(options={}){
 const geometry={scrollHeight:options.scrollHeight??1000,clientHeight:options.clientHeight??300,
  offsetWidth:options.offsetWidth??320,clientWidth:options.clientWidth??305,clientLeft:options.clientLeft??2,
  borderLeftWidth:options.borderLeftWidth??'2px',borderRightWidth:options.borderRightWidth??'3px',
  direction:options.direction??'ltr'};
 const frames=new Map(),resizeObservers=[],mutationObservers=[],storage=new Map(),timers=new Map(),away=[],datasetWrites=[];
 const effects={frameRequests:0,timerRequests:0,storageWrites:0,scrollWrites:0,geometryReads:0,messageQueries:0};
 let frameId=0,timerId=0;
 const view=new TrackedEventTarget(),document=new TrackedEventTarget();
 Object.assign(document,{defaultView:view,activeElement:null});
 class ResizeObserver{
  constructor(callback){this.callback=callback;resizeObservers.push(this)}
  observe(){}
  unobserve(){}
  disconnect(){this.disconnected=true}
 }
 class MutationObserver{
  constructor(callback){this.callback=callback;mutationObservers.push(this)}
  observe(){}
  disconnect(){this.disconnected=true}
 }
 Object.assign(view,{
  sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>{effects.storageWrites++;storage.set(key,value)}},
  ResizeObserver,MutationObserver,
  requestAnimationFrame(callback){effects.frameRequests++;const id=++frameId;frames.set(id,callback);return id},
  cancelAnimationFrame(id){frames.delete(id)},
  setTimeout(callback){effects.timerRequests++;const id=++timerId;timers.set(id,callback);return id},
  clearTimeout(id){timers.delete(id)},
  getComputedStyle(){return {borderLeftWidth:geometry.borderLeftWidth,borderRightWidth:geometry.borderRightWidth,direction:geometry.direction}},
 });
 const pane=new TrackedEventTarget(),rows=[...(options.messages||[])],style=new InlineStyle();
 for(const [name,{value,priority=''}] of Object.entries(options.inline||{}))style.setProperty(name,value,priority);
 let scrollTop=options.scrollTop??0;
 Object.defineProperties(pane,{
  scrollTop:{get(){scrollTop=Math.min(Math.max(0,scrollTop),Math.max(0,geometry.scrollHeight-geometry.clientHeight));return scrollTop},
   set(value){effects.scrollWrites++;scrollTop=Math.min(Math.max(0,Number(value)||0),Math.max(0,geometry.scrollHeight-geometry.clientHeight))}},
  scrollHeight:{get(){return geometry.scrollHeight},set(value){geometry.scrollHeight=value}},
  clientHeight:{get(){return geometry.clientHeight},set(value){geometry.clientHeight=value}},
  offsetWidth:{get(){return geometry.offsetWidth},set(value){geometry.offsetWidth=value}},
  clientWidth:{get(){return geometry.clientWidth},set(value){geometry.clientWidth=value}},
  clientLeft:{get(){return geometry.clientLeft},set(value){geometry.clientLeft=value}},
 });
 Object.assign(pane,{
  ownerDocument:document,isConnected:true,parentElement:null,
  dataset:new Proxy({},{
   set(target,key,value){datasetWrites.push({key,value});target[key]=value;return true},
   deleteProperty(target,key){datasetWrites.push({key});delete target[key];return true},
  }),style,children:[],
  contains(node){for(let current=node;current;current=current.parentElement)if(current===pane)return true;return false},
  querySelectorAll:()=>{effects.messageQueries++;return rows},
  getBoundingClientRect:()=>{effects.geometryReads++;return {top:0,left:0,right:geometry.offsetWidth,bottom:geometry.clientHeight,
   width:geometry.offsetWidth,height:geometry.clientHeight}},
 });
 for(const row of rows){row.pane=pane;row.children=[];append(pane,row)}
 const following=options.following||{current:true};
 const controller=createChatScroll(pane,following,value=>away.push(value));
 return {pane,rows,geometry,following,controller,away,resizeObservers,mutationObservers,frames,view,document,storage,timers,effects,datasetWrites,
  settle(){controller.settleLayout()},
  flushFrames(){for(const [id,callback] of [...frames])if(frames.delete(id))callback()},
  scrollTo(value){pane.scrollTop=value;pane.dispatchEvent(new Event('scroll'))},
 };
}

function message(id,contentTop,height=80,inputId){
 const row={dataset:{messageId:id,...(inputId?{inputId}:{})},contentTop,height};
 row.getBoundingClientRect=()=>({top:row.contentTop-row.pane.scrollTop,
  bottom:row.contentTop+row.height-row.pane.scrollTop});
 return row;
}

test('reports the existing 8px, 80px and one-pixel overflow boundaries',()=>{
 const h=harness();
 h.controller.select('alpha');h.settle();
 h.scrollTo(8);
 assert.equal(h.pane.dataset.overflowAbove,'false','top fade keeps the existing strict >8px threshold');
 assert.equal(h.pane.dataset.overflowBelow,'true');
 assert.equal(h.away.at(-1),true,'Latest remains available beyond 80px');
 h.scrollTo(8.01);assert.equal(h.pane.dataset.overflowAbove,'true');
 h.scrollTo(619);assert.equal(h.away.at(-1),true,'81px still exposes Latest');
 h.scrollTo(620);assert.equal(h.away.at(-1),false,'80px does not expose Latest');
 h.scrollTo(699);assert.equal(h.pane.dataset.overflowBelow,'false','exactly 1px is not visible overflow');
 h.scrollTo(698.99);assert.equal(h.pane.dataset.overflowBelow,'true','more than 1px is visible overflow');
 h.controller.dispose();
});

test('short transcripts stay unmasked and existing updates refresh bottom overflow',()=>{
 const h=harness({scrollHeight:200,clientHeight:300});
 h.controller.select('short');h.settle();
 assert.equal(h.pane.dataset.overflowAbove,'false');
 assert.equal(h.pane.dataset.overflowBelow,'false');

 h.pane.scrollHeight=302;h.controller.update();h.settle();
 assert.equal(h.pane.dataset.overflowBelow,'true','2px of remaining content enables the bottom edge');
 h.pane.clientHeight=301;h.resizeObservers[0].callback();h.settle();
 assert.equal(h.pane.dataset.overflowBelow,'false','resize recomputes the 1px boundary');
 h.pane.scrollHeight=350;h.mutationObservers[0].callback();h.settle();
 assert.equal(h.pane.dataset.overflowBelow,'true','the existing mutation owner reports appended content');
 assert.equal(h.resizeObservers.length,1,'no additional resize observer is installed');
 assert.equal(h.mutationObservers.length,1,'no additional mutation observer is installed');
 h.controller.dispose();
});

test('session changes, unready state and disposal clear overflow flags and restore inline styles',()=>{
 const h=harness({inline:{[LEFT_GUTTER]:{value:'7px',priority:'important'},[RIGHT_GUTTER]:{value:'3px'}}});
 h.controller.select('alpha');h.settle();h.scrollTo(300);
 assert.equal(h.pane.dataset.overflowAbove,'true');
 assert.equal(h.pane.dataset.overflowBelow,'true');

 h.controller.select('beta',false);
 assert.equal('overflowAbove' in h.pane.dataset,false,'switch clears the prior top state immediately');
 assert.equal('overflowBelow' in h.pane.dataset,false,'switch clears the prior bottom state immediately');
 h.controller.reveal();
 assert.equal('overflowAbove' in h.pane.dataset,false,'unready reports cannot recreate the top state');
 assert.equal('overflowBelow' in h.pane.dataset,false,'unready reports cannot recreate the bottom state');
 h.controller.select('beta',true);h.settle();h.controller.select('beta',false);
 assert.equal('overflowAbove' in h.pane.dataset,false,'unready clears the same-session state');
 assert.equal('overflowBelow' in h.pane.dataset,false);

 h.controller.select('beta',true);h.settle();h.scrollTo(300);h.controller.dispose();
 assert.equal('overflowAbove' in h.pane.dataset,false,'dispose clears the top state');
 assert.equal('overflowBelow' in h.pane.dataset,false,'dispose clears the bottom state');
 assert.equal(h.pane.style.getPropertyValue(LEFT_GUTTER),'7px');
 assert.equal(h.pane.style.getPropertyPriority(LEFT_GUTTER),'important');
 assert.equal(h.pane.style.getPropertyValue(RIGHT_GUTTER),'3px');
 assert.equal(h.pane.style.getPropertyPriority(RIGHT_GUTTER),'');
 assert.equal(h.pane.style.overflowAnchor,'auto');
});

test('gutter insets are measured from the actual classic scrollbar side',()=>{
 const ltr=harness({offsetWidth:320,clientWidth:305,clientLeft:2,borderLeftWidth:'2px',borderRightWidth:'3px'});
 ltr.controller.select('ltr');ltr.settle();
 assert.equal(ltr.pane.style.getPropertyValue(LEFT_GUTTER),'0px');
 assert.equal(ltr.pane.style.getPropertyValue(RIGHT_GUTTER),'10px');
 ltr.pane.clientWidth=300;ltr.resizeObservers[0].callback();ltr.settle();
 assert.equal(ltr.pane.style.getPropertyValue(RIGHT_GUTTER),'15px','resize remeasures the classic gutter');
 ltr.controller.dispose();

 const rtl=harness({offsetWidth:320,clientWidth:305,clientLeft:12,borderLeftWidth:'2px',borderRightWidth:'3px',direction:'rtl'});
 rtl.controller.select('rtl');rtl.settle();
 assert.equal(rtl.pane.style.getPropertyValue(LEFT_GUTTER),'10px');
 assert.equal(rtl.pane.style.getPropertyValue(RIGHT_GUTTER),'0px');
 rtl.controller.dispose();

 const overlay=harness({offsetWidth:320,clientWidth:315,clientLeft:2,borderLeftWidth:'2px',borderRightWidth:'3px'});
 overlay.controller.select('overlay');overlay.settle();
 assert.equal(overlay.pane.style.getPropertyValue(LEFT_GUTTER),'0px');
 assert.equal(overlay.pane.style.getPropertyValue(RIGHT_GUTTER),'0px');
 overlay.controller.dispose();
});

test('overflow reporting leaves submitted anchors, call following and reading intent unchanged',()=>{
 const row=message('reply',500,80,'input-1'),h=harness({messages:[row]});
 h.controller.select('alpha');h.settle();
 h.controller.submitted('input-1');h.settle();
 assert.equal(h.following.current,true,'submitted replies retain their existing follow intent');
 assert.equal(row.getBoundingClientRect().bottom,16,'submitted reply remains anchored 16px from the pane edge');

 h.controller.setCall('call-1','alpha');h.settle();
 assert.equal(h.following.current,false,'call following retains its existing controller semantics');
 assert.equal(h.pane.scrollTop,700);
 const wheel=new Event('wheel');Object.defineProperty(wheel,'deltaY',{value:-100});h.pane.dispatchEvent(wheel);
 h.scrollTo(300);
 row.contentTop=540;h.controller.update();h.settle();
 assert.equal(h.pane.scrollTop,340,'layout growth restores the same message anchor');
 assert.equal(row.getBoundingClientRect().top,200);
 assert.equal(h.following.current,false,'reading intent does not resume the call');
 h.controller.dispose();
});

test('mounted inactive, external and disconnected iframe hosts do not suppress fades',()=>{
 const h=harness(),wrapper=append(h.pane,element('article')),iframe=append(wrapper,element('iframe'));
 h.controller.select('alpha');h.settle();h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'mounting a preview alone is not focus');
 h.document.activeElement=element('iframe');h.view.dispatchEvent(new Event('blur'));h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'an external active iframe is not inside the transcript');
 h.document.activeElement=iframe;h.pane.isConnected=false;h.view.dispatchEvent(new Event('focus'));h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'a detached pane cannot publish active focus');
 h.pane.isConnected=true;h.view.dispatchEvent(new Event('focus'));h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true','a connected nested iframe is observed through explicit child links');
 h.controller.dispose();
});

test('window blur and document focusout read the host activeElement after event dispatch',()=>{
 for(const [targetName,eventName] of [['view','blur'],['document','focusout']]){
  const h=harness(),iframe=append(h.pane,element('iframe'));
  h.document.hasFocus=()=>assert.fail('document.hasFocus is not evidence of an active iframe');
  h.controller.select('alpha');h.settle();h.flushFrames();
  assert.equal(iframe.matches(':focus'),false,'reproduce the native CSS focus mismatch');
  h[targetName].dispatchEvent(new Event(eventName));
  assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'event handlers only invalidate');
  h.document.activeElement=iframe;
  h.flushFrames();
  assert.equal(h.pane.dataset.transcriptIframeFocus,'true',`${eventName} observes the later host activeElement without inner access`);
  h.controller.dispose();
 }
});

test('focus bursts coalesce one frame, cache unchanged state and clear for composer/native controls',()=>{
 const h=harness(),iframe=append(h.pane,element('iframe')),button=append(h.pane,element('button'));
 h.controller.select('alpha');h.settle();h.flushFrames();
 const before=h.effects.frameRequests;
 for(let i=0;i<3;i++){
  h.document.dispatchEvent(new Event('focusout'));h.document.dispatchEvent(new Event('focusin'));
  h.view.dispatchEvent(new Event('blur'));h.view.dispatchEvent(new Event('focus'));
 }
 assert.equal(h.frames.size,1,'focus events do not queue a scroll frame');
 assert.equal(h.effects.frameRequests,before+1,'one focus-only frame handles the burst');
 h.document.activeElement=iframe;h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true');
 const writes=h.datasetWrites.length;
 h.view.dispatchEvent(new Event('focus'));h.flushFrames();
 assert.equal(h.datasetWrites.length,writes,'unchanged focus does not rewrite the cached attribute');
 for(const control of [button,element('textarea')]){
  h.document.activeElement=control;h.document.dispatchEvent(new Event('focusin'));h.flushFrames();
  assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'native transcript or composer focus removes the host iframe flag');
  h.document.activeElement=iframe;h.view.dispatchEvent(new Event('blur'));h.flushFrames();
  assert.equal(h.pane.dataset.transcriptIframeFocus,'true');
 }
 h.document.activeElement=null;h.view.dispatchEvent(new Event('blur'));h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'blur does not invent iframe focus');
 h.controller.dispose();
});

test('ready selection observes current focus while switch and unready cancel stale observations immediately',()=>{
 const h=harness(),iframe=append(h.pane,element('iframe'));
 h.document.activeElement=iframe;h.view.dispatchEvent(new Event('blur'));
 assert.equal(h.frames.size,0,'an unready controller does not queue observations');
 h.controller.select('alpha');h.settle();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'initial ready selection observes asynchronously');
 h.flushFrames();assert.equal(h.pane.dataset.transcriptIframeFocus,'true');

 h.document.dispatchEvent(new Event('focusout'));
 const [oldFocusFrame]=h.frames.keys();
 h.controller.select('beta');
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'a ready chat switch clears the old flag synchronously');
 assert.equal(h.frames.has(oldFocusFrame),false,'switch cancels the prior focus callback');
 h.document.activeElement=element('button');h.settle();h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'the new observation reads current host focus');

 h.document.activeElement=iframe;h.view.dispatchEvent(new Event('blur'));h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true');
 h.view.dispatchEvent(new Event('focus'));
 const [pendingFocusFrame]=h.frames.keys();
 h.controller.select('beta',false);
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'same-session unready state clears immediately');
 assert.equal(h.frames.has(pendingFocusFrame),false,'unready cancels the pending focus callback');
 h.settle();h.flushFrames();
 h.document.dispatchEvent(new Event('focusin'));h.view.dispatchEvent(new Event('blur'));
 assert.equal(h.frames.size,0,'focus events cannot recreate unready work');
 h.controller.select('beta',true);h.settle();h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true','ready selection observes native focus without a new focus event');
 h.controller.dispose();
});

test('the existing mutation observer rechecks removed and replacement iframe hosts',()=>{
 const h=harness(),wrapper=append(h.pane,element('article')),original=append(wrapper,element('iframe'));
 h.document.activeElement=original;h.controller.select('alpha');h.settle();h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true');
 remove(original);h.mutationObservers[0].callback();h.settle();h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'even a stale activeElement must still be contained');
 const replacement=append(wrapper,element('iframe'));
 h.mutationObservers[0].callback();h.settle();h.flushFrames();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'replacement presence does not imply focus');
 h.document.activeElement=replacement;h.mutationObservers[0].callback();h.settle();h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true','mutations also observe the actual replacement focus');
 assert.equal(h.mutationObservers.length,1,'focus reuses the existing mutation observer');
 assert.equal(h.resizeObservers.length,1,'focus adds no resize observer');
 h.controller.dispose();
});

test('disposal cancels focus work, removes all listeners and prevents flag resurrection',()=>{
 const h=harness(),iframe=append(h.pane,element('iframe'));
 h.document.activeElement=iframe;h.controller.select('alpha');h.settle();h.flushFrames();
 assert.equal(h.pane.dataset.transcriptIframeFocus,'true');
 h.document.dispatchEvent(new Event('focusout'));
 const [lateCallback]=h.frames.values();
 h.controller.dispose();
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false);
 assert.equal(h.frames.size,0,'both scroll and focus callbacks are canceled');
 assert.equal(h.timers.size,0);
 assert.equal(h.document.listenerCount(),0);assert.equal(h.view.listenerCount(),0);assert.equal(h.pane.listenerCount(),0);
 assert.equal(h.resizeObservers[0].disconnected,true);assert.equal(h.mutationObservers[0].disconnected,true);
 lateCallback();
 h.document.dispatchEvent(new Event('focusin'));h.document.dispatchEvent(new Event('focusout'));
 h.view.dispatchEvent(new Event('focus'));h.view.dispatchEvent(new Event('blur'));
 h.controller.select('beta');h.flushFrames();
 assert.equal(h.frames.size,0,'disposed ready selection and focus events cannot queue new work');
 assert.equal('transcriptIframeFocus' in h.pane.dataset,false,'a late callback cannot resurrect the flag');
});

test('focus-only observations leave submitted anchors, call following and reading persistence untouched',()=>{
 for(const intent of ['submitted','call','reading']){
  const row=message('reply',500,80,'input-1'),h=harness({messages:[row]});
  const iframe=append(row,element('iframe'));
  h.controller.select('alpha');h.settle();h.flushFrames();
  if(intent==='submitted')h.controller.submitted('input-1');
  if(intent==='call')h.controller.setCall('call-1','alpha');
  h.settle();h.flushFrames();
  if(intent==='reading')h.scrollTo(300);
  // A pending geometry change makes accidental anchor capture/restore detectable.
  row.contentTop+=40;h.pane.scrollHeight+=40;
  const state=()=>({
   top:h.pane.scrollTop,anchorOffset:row.contentTop-h.pane.scrollTop,following:h.following.current,
   away:[...h.away],storage:[...h.storage],timers:[...h.timers],
   overflowAbove:h.pane.dataset.overflowAbove,overflowBelow:h.pane.dataset.overflowBelow,
   effects:{...h.effects,frameRequests:0},
  });
  const before=state();
  h.view.dispatchEvent(new Event('blur'));h.document.activeElement=iframe;h.flushFrames();
  assert.equal(h.pane.dataset.transcriptIframeFocus,'true');
  assert.deepEqual(state(),before,`${intent}: focus does not read/write geometry, report Away, capture anchors, save or reschedule timers`);
  h.document.dispatchEvent(new Event('focusout'));h.document.activeElement=element('textarea');h.flushFrames();
  assert.equal('transcriptIframeFocus' in h.pane.dataset,false);
  assert.deepEqual(state(),before,`${intent}: clearing focus also leaves the scroll owner untouched`);
  h.controller.update();h.settle();
  if(intent==='submitted'){
   assert.equal(h.following.current,true);assert.equal(row.getBoundingClientRect().bottom,16,'submitted anchor intent survives focus');
  }else if(intent==='call')assert.equal(h.pane.scrollTop,740,'owned call still follows the new bottom');
  else assert.equal(h.pane.scrollTop,340,'scrollback restores the original captured anchor, not the pending geometry');
  h.controller.dispose();
 }
});

test('shared mask uses bounded fades, modality-aware focus suppression and an additive measured gutter',()=>{
 assert.notEqual(maskStart,-1,'shared transcript mask block exists');
 assert.notEqual(maskEnd,-1,'mask block ends before navigator styling');
 assert.match(maskCss,/--a-transcript-fade-top,\s*18px/);
 assert.match(maskCss,/--a-transcript-fade-bottom,\s*24px/);
 assert.match(maskCss,/min\(clamp\(0px,\s*var\(--a-transcript-fade-top,\s*18px\),\s*48px\),\s*50%\)/);
 assert.match(maskCss,/min\(clamp\(0px,\s*var\(--a-transcript-fade-bottom,\s*24px\),\s*48px\),\s*50%\)/);
 assert.match(maskCss,/data-overflow-above=true\]\[data-overflow-below=true/);
 assert.match(maskCss,/data-overflow-above=true\]:not\(\[data-overflow-below=true\]\)\{\s*--_a-transcript-edge-mask:linear-gradient\(to bottom,\s*transparent 0,\s*black var\(--_a-transcript-fade-top\),\s*black 100%\)/);
 assert.match(maskCss,/@media\(forced-colors:active\)/);
 const normalFocusStart=maskCss.indexOf('/* Keep text readable for keyboard-visible controls');
 const forcedColorsStart=maskCss.indexOf('@media(forced-colors:active)',normalFocusStart);
 const selectors=section=>section.match(/^[ \t]*#amp-one \.a-messages\[data-overflow-(?:above|below)=true\].*$/gm)?.map(row=>row.trim())||[];
 const normalFocusSelectors=selectors(maskCss.slice(normalFocusStart,forcedColorsStart));
 const forcedFocusSelectors=selectors(maskCss.slice(forcedColorsStart));
 assert.equal(normalFocusSelectors.length,2,'Both overflow edges use a focus guard');
 assert.ok(normalFocusSelectors.every(selector=>selector.includes(':not([data-transcript-iframe-focus=true])')),
  'Only an actually active transcript iframe host suppresses the shared mask');
 assert.ok(normalFocusSelectors.every(selector=>/:not\(:focus-visible\):not\(:has\(:focus-visible,\s*input:focus,\s*textarea:focus,\s*select:focus,\s*\[contenteditable\]:focus,\s*iframe:focus\)\)/.test(selector)),
  'Keyboard-visible descendants and active editors keep the transcript unmasked');
 assert.deepEqual(forcedFocusSelectors,normalFocusSelectors,'Forced-colors suppression matches the mask selector specificity');
 assert.doesNotMatch(maskCss,/:focus-within/,'Non-visible container focus alone does not suppress both fades');
 assert.match(maskCss,/mask-composite:add!important/);
 assert.match(maskCss,/linear-gradient\(to right,\s*black 0,\s*black var\(--_a-transcript-gutter-left,0px\)/);
 assert.match(maskCss,/Overlay scrollbars expose no layout gutter/);
 assert.doesNotMatch(maskCss,/animation|transition/i);
 assert.doesNotMatch(maskCss,/\.a-(?:conversation|conversation-rail|composer|history-window-controls)/);
});