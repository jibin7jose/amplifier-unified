// Real DOM + real app actions, synthetic runtime; no provider or user history.
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {createSelectionEvidence,observeOptional,OPTIONAL_OBSERVATION_MS} from './selection-failure-evidence.mjs';
const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
const url=`http://127.0.0.1:${port}`,proof=process.env.AMPLIFIER_PROOF_DIR;
if(proof)mkdirSync(proof,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[process.env.AMPLIFIER_QUOTE_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/message_interactions_ui_server.py',import.meta.url)),String(port)],{stdio:'inherit',cwd:process.env.AMPLIFIER_FIXTURE_CWD||process.cwd()});
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
context.setDefaultTimeout(10000);
page.on('pageerror',e=>errors.push(e.message));
const documentValue=()=>page.evaluate(()=>window.amplifier.getState().view.draftDocument);
const commentary=doc=>doc.segments.filter(s=>s.type==='text').map(s=>s.text);
const references=doc=>doc.segments.filter(s=>s.type==='quote');
const observe=async()=>{const response=await context.request.get(url+'/fixture/message-source');assert.equal(response.status(),200);return response.json();};
const checkpoints=[],compactGeometry=[];
const cardObservations=[];
const selectionEvidence=createSelectionEvidence();
const {observations:selectionObservations,binding:selectionBinding,
 retain:retainSelectionObservation,observeFailure:observeSelectionFailure}=selectionEvidence;
// Installed on each document, including reloads. One current probe retains the
// actual selected node; rebinding or closing the page releases the old closure.
// Geometry reads may flush layout. No timing-equivalence claim follows.
function installSelectionObserver(){
 window.multiQuoteBindSelection=(node,binding,text,textMatchBasis)=>{
  // Capture source identity once. Later DOM removal must not turn a source-C
  // observation into an unidentified node or a newly resolved source-A locator.
  let source;
  const inspect=()=>{
   try{
    const identity=element=>{
     const value=element?.closest('[data-message-id]')?.getAttribute('data-message-id');
     return typeof value==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value)?value:null;
    };
    source??={root:node.closest('.a-detail-text')||node,article:node.closest('[data-message-id]'),
     pane:node.closest('.a-messages'),messageId:identity(node)};
    const {root,article,pane,messageId}=source;
    const current=window.getSelection(),selected=current?.rangeCount?current.getRangeAt(0):null;
    const toolbar=document.querySelector('.a-quote-selection'),active=document.activeElement;
    const coordinate=value=>Number.isFinite(value)&&Math.abs(value)<=1e7?value:null;
    const rect=target=>{
     if(!target)return null;
     const value=target.getBoundingClientRect();
     return Object.fromEntries(['x','y','width','height','top','bottom','left','right'].map(key=>[key,coordinate(value[key])]));
    };
    const rangeStart=selected?.startContainer,rangeEnd=selected?.endContainer;
    const element=target=>target?.nodeType===1?target:target?.parentElement;
    const activeCategory=!active?'none':active===document.body?'body':root.contains(active)?'source':
     active.closest('.a-composer')?'composer':active.matches('input,textarea,[contenteditable=true]')?'editor':
     active.matches('button')?'button':'other';
    return {...binding,available:true,sourceKind:'rendered-message',messageId,
     rangeStartMessageId:identity(element(rangeStart)),rangeEndMessageId:identity(element(rangeEnd)),
     nodeConnected:node.isConnected,rootConnected:root.isConnected,rangeCount:current?.rangeCount??0,collapsed:current?.isCollapsed??null,
     anchorConnected:current?.anchorNode?.isConnected??false,focusConnected:current?.focusNode?.isConnected??false,
     rangeStartConnected:rangeStart?.isConnected??false,rangeEndConnected:rangeEnd?.isConnected??false,
     rangeInsideSource:!!selected&&root.contains(rangeStart)&&root.contains(rangeEnd),
     rangeInsideNode:!!selected&&node.contains(rangeStart)&&node.contains(rangeEnd),
     exactTextMatch:!!current&&current.toString()===text,textMatchBasis,activeCategory,
     viewport:{width:coordinate(innerWidth),height:coordinate(innerHeight)},
     rects:{selection:rect(selected),node:rect(node),source:rect(root),pane:rect(pane),toolbar:rect(toolbar)},
     toolbarPresent:!!toolbar,quoteButtonPresent:!!toolbar?.querySelector('button'),
     errorStatusPresent:!!toolbar?.querySelector('.a-quote-selection-error,[role=status]'),
     sourceErrorPresent:!!article?.querySelector('.a-message-interaction-error[role=alert]')};
   }catch{return {...binding,available:false,reason:'observation_unavailable'}}
  };
  window.multiQuoteSelectionProbe=inspect;
  return inspect();
 };
}
async function observeCards(stage){
 const rendered=await page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(editor=>{
  const bounds=node=>{const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};};
  const viewport=editor.getBoundingClientRect();
  const inspect=node=>{
   const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
   return {tag:node.tagName,className:node.className,bounds:bounds(node),display:style.display,visibility:style.visibility,
    opacity:style.opacity,position:style.position,insideEditor:rect.top>=viewport.top&&rect.bottom<=viewport.bottom,
    intersectsEditor:rect.bottom>viewport.top&&rect.top<viewport.bottom};
  };
  return {editor:{...inspect(editor),scrollTop:editor.scrollTop,scrollHeight:editor.scrollHeight,clientHeight:editor.clientHeight},
   active:document.activeElement?.getAttribute('aria-label'),cards:[...editor.querySelectorAll('[data-quote-instance]')].map(card=>({
    ...inspect(card),instanceId:card.dataset.quoteInstance,contentEditable:card.contentEditable,
    header:card.querySelector('.a-composer-quote-head')?inspect(card.querySelector('.a-composer-quote-head')):null,
    buttons:[...card.querySelectorAll('button')].map(button=>({...inspect(button),label:button.getAttribute('aria-label')||button.textContent,disabled:button.disabled})),
    excerpt:card.querySelector('pre')?.textContent,caption:card.querySelector('small')?.textContent,
   })),assets:[...document.scripts].map(node=>node.src).filter(Boolean)};
 });
 cardObservations.push({stage,rendered,document:await documentValue(),canonical:await canonicalDraft()});
 if(proof){
  writeFileSync(join(proof,'card-observations.json'),JSON.stringify(cardObservations,null,2));
  await page.locator('.a-composer').screenshot({path:join(proof,`cards-${cardObservations.length}.png`)});
 }
}
async function visibleCardControls(stage,card){
 await card.evaluate(card=>{
  const editor=card.closest('[role=textbox]'),header=card.querySelector('.a-composer-quote-head'),excerpt=card.querySelector('pre');
  editor.scrollTop=0;
  // Deliberately show the excerpt after the natural header position has
  // scrolled away. A merely present or Playwright-"visible" button is not enough.
  // A short rendered-code quote has no Markdown fence lines. Do not scroll
  // its entire readable payload behind an enlarged, wrapped sticky header.
  editor.scrollTop=card.getBoundingClientRect().top-editor.getBoundingClientRect().top+Math.min(header.offsetHeight/2,excerpt.offsetHeight/2);
 });
 await page.evaluate(()=>new Promise(requestAnimationFrame));
 const actual=await card.evaluate(card=>{
  const editor=card.closest('[role=textbox]'),viewport=editor.getBoundingClientRect(),pre=card.querySelector('pre'),body=pre.getBoundingClientRect(),header=card.querySelector('.a-composer-quote-head').getBoundingClientRect();
  let readable=false;
  for(let i=0;i<Math.min(pre.firstChild?.length||0,1000);i++){
   if(/\s/.test(pre.firstChild.textContent[i]))continue;
   const range=document.createRange();range.setStart(pre.firstChild,i);range.setEnd(pre.firstChild,i+1);
   const rect=range.getBoundingClientRect();
   if(rect.top>=Math.max(header.bottom,viewport.top)&&rect.bottom<=viewport.bottom&&pre.contains(document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2))){readable=true;break;}
  }
  return {visibleExcerpt:body.bottom>viewport.top&&body.top<viewport.bottom,readable,
   controls:[...card.querySelectorAll('.a-composer-quote-head button')].map(button=>{
    const rect=button.getBoundingClientRect(),hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
    return {label:button.getAttribute('aria-label'),inside:rect.top>=viewport.top-1&&rect.bottom<=viewport.bottom+1,hit:button.contains(hit),hitTag:hit?.outerHTML.slice(0,400),disabled:button.disabled};
   })};
 });
 checkpoints.push({stage,controls:actual});
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
 assert.ok(actual.visibleExcerpt,stage+': excerpt is visible');
 assert.ok(actual.readable,stage+': excerpt has readable text outside the sticky header');
 assert.equal(actual.controls.length,4,stage+': source and three quote controls exist');
 assert.ok(actual.controls.every(row=>row.inside&&row.hit),stage+': visible quote has reachable source/move/remove controls');
}
const BEFORE='Before the references',BETWEEN='Between the passages',BEFORE_WHOLE='Before the whole message',AFTER='After all references';
const UNICODE='  café e\u0301 東京  ';
const projectDocument=doc=>doc.segments.map(part=>part.type==='text'?part.text:part);
const canonicalDraft=async()=>{
 const id=await page.evaluate(()=>window.amplifier.getState().client.id);
 const response=await context.request.get(url+'/fixture/quote-draft?'+new URLSearchParams({clientId:id}));
 assert.equal(response.status(),200);return response.json();
};
async function quoteRendered(page,article,text){
 const binding=selectionBinding(page,'quoteRendered','rendered-passage','.a-detail-text');
 try{
 const body=article.locator('.a-detail-text');
 await body.scrollIntoViewIfNeeded();
 await body.dispatchEvent('pointerdown');
 const installed=await body.evaluate((root,{text,binding})=>{
  document.activeElement?.blur();
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),nodes=[];let node,rendered='';
  while((node=walker.nextNode())){nodes.push({node,start:rendered.length});rendered+=node.textContent;}
  const start=rendered.indexOf(text),end=start+text.length;
  if(start<0||rendered.indexOf(text,start+1)!==-1)throw Error('Expected one exact rendered fixture passage');
  const first=nodes.find(row=>row.start+row.node.length>start),last=nodes.find(row=>row.start+row.node.length>=end);
  const range=document.createRange();range.setStart(first.node,start-first.start);range.setEnd(last.node,end-last.start);
  const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
  document.dispatchEvent(new Event('selectionchange'));
  return window.multiQuoteBindSelection?.(root,binding,text,'fixture-excerpt')??{...binding,available:false,reason:'probe_unavailable'};
 },{text,binding});
 retainSelectionObservation('range-installed',installed);
  await page.getByRole('button',{name:'Quote selection',exact:true}).click();
  await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 }catch(error){
  await observeSelectionFailure(page,error);
  throw error;
 }
}
async function noPassageEditor(page){
 await expect(page.getByRole('button',{name:'Choose passage',exact:true,includeHidden:true})).toHaveCount(0);
 await expect(page.locator('.a-quote-picker,.a-quote-fallback')).toHaveCount(0);
 await expect(page.getByRole('textbox',{name:'Message source for quotation',includeHidden:true})).toHaveCount(0);
}
async function enteredCommentary(text,{canonicalReady=true}={}){
 const observed=await page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(editor=>{
  const state=window.amplifier.getState(),selection=window.getSelection();
  return {paragraphs:[...editor.children].filter(node=>!node.hasAttribute('data-quote-instance')).map(node=>node.textContent),
   document:state.view.draftDocument,draft:state.view.draft,active:document.activeElement?.outerHTML.slice(0,300),
   selectionParent:selection?.anchorNode?.parentElement?.outerHTML.slice(0,300),events:window.quoteTypingEvents||[]};
 });
 const canonical=await canonicalDraft();checkpoints.push({stage:'typed '+text,expectedText:text,observed,canonical});
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
 assert.ok(observed.paragraphs.some(value=>value.includes(text)),`Typed commentary missing from DOM immediately: ${text}`);
 assert.ok(commentary(observed.document).some(value=>value.includes(text)),`Typed commentary missing from editor draft: ${text}`);
 if(canonicalReady)await expect.poll(async()=>commentary((await canonicalDraft()).document||{segments:[]}).some(value=>value.includes(text))).toBe(true);
}
async function expectLayout(stage,expected,{canonicalReady=true}={}){
 const dom=await page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(editor=>[...editor.children].map(node=>node.hasAttribute('data-quote-instance')?
  {instanceId:node.dataset.quoteInstance,excerpt:node.querySelector('pre').textContent}:node.textContent));
 const document=await documentValue();
 const expectedDom=expected.map(part=>typeof part==='string'?part:{instanceId:part.instanceId,excerpt:part.quote.excerpt});
 checkpoints.push({stage,expected,dom,document});
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
 assert.deepEqual(dom,expectedDom,stage+': exact commentary/quote DOM order');
 assert.deepEqual(projectDocument(document),expected,stage+': independently expected editor order');
 if(canonicalReady)await expect.poll(async()=>projectDocument((await canonicalDraft()).document||{segments:[]})).toEqual(expected);
 else{
  // The deliberately held prior action blocks the existing ordered save
  // queue. The journal must own these exact bytes until receipt settlement.
  const pending=await page.evaluate(()=>{
   const state=window.amplifier.getState();
   return JSON.parse(sessionStorage.getItem('amplifier.pendingDrafts.v1')).rows.find(row=>row.sessionId===state.selectedSessionId);
  });
  assert.deepEqual(projectDocument(pending.document),expected,stage+': recovery journal owns the exact newer draft');
  checkpoints.at(-1).pendingJournal=pending;
 }
 const canonical=await canonicalDraft();
 checkpoints.at(-1).canonical=canonical;
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
}
const settleComposerLayout=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
async function measureCompact(){
 const surface=page.locator('.a-compact-composer [data-part="composer-surface"]');
 await expect(surface).toHaveAttribute('data-expanded','true');
 return surface.evaluate(node=>{
  const rect=el=>{if(!el)return null;const {x,y,width,height,top,bottom}=el.getBoundingClientRect();return {x,y,width,height,top,bottom};};
  const editor=node.querySelector('[role=textbox]'),writing=node.querySelector('.a-compose-writing');
  const conversation=node.closest('.a-conversation'),composer=node.closest('.a-composer'),messages=conversation.querySelector('.a-messages');
  return {surface:rect(node),editor:rect(editor),writing:rect(writing),viewportHeight:innerHeight,
   editorScrollHeight:editor.scrollHeight,editorMaxHeight:getComputedStyle(editor).maxHeight,messages:rect(messages),
   conversation:rect(conversation),composer:rect(composer),header:rect(document.querySelector('.a-work-header')),
   latest:rect(conversation.querySelector('.a-chat-jump')),latestButton:rect(conversation.querySelector('.a-chat-jump button')),
   rows:[...conversation.children].map(row=>({className:row.className,...rect(row)})),
   quoteCount:writing.querySelectorAll('[data-quote-instance]').length,
   overflow:node.scrollWidth>node.clientWidth+1,pageOverflow:document.documentElement.scrollWidth>innerWidth+1,
   theme:document.querySelector('#amp-one').dataset.themeScheme,
   primary:rect(node.querySelector('[data-part="composer-primary-action"]'))};
 });
}
async function expectCompact(stage){
 await settleComposerLayout();
 const geometry=await measureCompact();
 compactGeometry.push({stage,...geometry});
 if(proof)writeFileSync(join(proof,'compact-quote-geometry.json'),JSON.stringify(compactGeometry,null,2));
 assert.ok(geometry.quoteCount>=2,'Real immutable quotes belong inside the compact writing wrapper');
 assert.ok(geometry.editor.width>=geometry.surface.width-40,'Quotes use the full compact writing width');
 assert.ok(geometry.editor.height<=Math.min(400,geometry.viewportHeight*.35)+1,'Long quote drafts retain a viewport-bounded editor height');
 if(geometry.editorScrollHeight>181)assert.ok(geometry.editor.height>181,'Overflowing quote drafts gain real reading space beyond the plain editor cap');
 assert.ok(geometry.messages.height>=80,'The conversation remains usable above the expanded quote editor');
 assert.equal(geometry.overflow,false,'Compact quote surface must not overflow');
 assert.equal(geometry.pageOverflow,false,'Compact quote page must not overflow');
 assert.ok(geometry.primary.width>=44&&geometry.primary.height>=44,'Primary action retains its touch target');
 if(geometry.latest){
  assert.ok(geometry.latest.top>=geometry.messages.bottom-1,'Latest has its own row, outside readable messages');
  assert.ok(geometry.latest.bottom<=geometry.composer.top+1,'Latest does not overlay the composer');
  assert.ok(geometry.latestButton.width>=44&&geometry.latestButton.height>=44,'Latest retains its touch target');
 }
 await expect(page.locator('[data-part="composer-primary-action"]')).toBeInViewport();
 return geometry;
}
try{
 await page.addInitScript(installSelectionObserver);
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 let comparisonError=null;
 if(process.env.AMPLIFIER_QUOTE_REFERENCE&&proof){
  try{await compareReference();}
  catch(error){
   comparisonError=error;
   try{writeFileSync(join(proof,'comparison-failure.json'),JSON.stringify({message:error.message},null,2))}catch{}
  }
 }
 await page.goto(url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await noPassageEditor(page);
 const editor=page.getByRole('textbox',{name:'Message Amplifier'}),quotes=editor.locator('[data-quote-instance]');
 await editor.fill(BEFORE);
 await expect(editor).toHaveText(BEFORE);
 await expect.poll(async()=>(await canonicalDraft()).draft).toBe(BEFORE);
 await page.locator('.a-file-input').setInputFiles({name:'synthetic.txt',mimeType:'text/plain',buffer:Buffer.from('Attachment kept')});
 await page.getByRole('button',{name:'Remove synthetic.txt',exact:true}).waitFor();
 const a=page.locator('.a-message').filter({hasText:'Quote fixture source A'}).first(),b=page.locator('.a-message').filter({hasText:'Quote fixture source B'}).first();
 const sourceA=await a.getAttribute('data-message-id'),sourceB=await b.getAttribute('data-message-id');
 const originalA=(await observe()).sessions.flatMap(s=>s.messages).find(m=>m.id===sourceA).text;
 async function passage(startMarker,endMarker){
  const text=originalA,start=text.indexOf(startMarker),end=text.indexOf(endMarker,start);
  assert.ok(start>=0&&end>start);
  await quoteRendered(page,a,text.slice(start,end));
  return {text:text.slice(start,end),start:[...text.slice(0,start)].length,end:[...text.slice(0,end)].length};
 }
 // Select only rendered words, not invisible paragraph indentation or code
 // fences. The code payload below still proves exact leading whitespace/tabs.
 const first=await passage('café','\n\n* first');await expect(quotes).toHaveCount(1);
 const firstQuote=references(await documentValue())[0];
 assert.equal(firstQuote.quote.excerpt,first.text);assert.equal(firstQuote.quote.messageId,sourceA);
 assert.deepEqual(firstQuote.quote.spans,[{start:first.start,end:first.end}]);
 await expectLayout('first quote',[BEFORE,firstQuote,'']);
 await observeCards('first quote after canonical autosave');
 await visibleCardControls('first quote scrolled',quotes.first());
 await page.evaluate(()=>{
  window.quoteTypingEvents=[];
  for(const name of ['keydown','beforeinput','input'])document.addEventListener(name,event=>{
   if(window.quoteTypingEvents.length<30)window.quoteTypingEvents.push({type:name,key:event.key,inputType:event.inputType,
    target:event.target.outerHTML?.slice(0,240),active:document.activeElement?.outerHTML.slice(0,240)});
  },true);
 });
 // Actual keyboard text entry after a quote, not a patched saved document.
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Between the passages');
 await enteredCommentary('Between the passages');
 await expectLayout('between typed',[BEFORE,firstQuote,BETWEEN]);
 const second=await passage('    name','```');await expect(quotes).toHaveCount(2);
 const secondQuote=references(await documentValue())[1];
 assert.equal(secondQuote.quote.excerpt,second.text);assert.equal(secondQuote.quote.messageId,sourceA);
 assert.deepEqual(secondQuote.quote.spans,[{start:second.start,end:second.end}]);
 await expectLayout('second quote',[BEFORE,firstQuote,BETWEEN,secondQuote,'']);
 await observeCards('second quote after canonical autosave');
 await expectCompact('two immutable references');
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Before the whole message');
 await enteredCommentary('Before the whole message');
 await expectLayout('before whole typed',[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE]);
 const wholeButton=b.getByRole('button',{name:'Reply to message',exact:true});
 await expect(b.locator('.a-detail-text')).toContainText('END OF EXACT LONG SOURCE');
 await wholeButton.scrollIntoViewIfNeeded();const beforeScroll=await page.locator('.a-messages').evaluate(n=>n.scrollTop);
 await wholeButton.evaluate(button=>{
  window.quoteScroll=[];window.beforeQuoteFocus=document.activeElement;
  window.quoteGeometry=()=>{const pane=document.querySelector('.a-messages');return {top:pane.scrollTop,height:pane.clientHeight,scrollHeight:pane.scrollHeight,buttonTop:button.getBoundingClientRect().top,composerHeight:document.querySelector('.a-composer').clientHeight};};
  for(const event of ['pointerdown','mousedown','focus','click'])button.addEventListener(event,()=>window.quoteScroll.push({event,...window.quoteGeometry()}),{capture:true,once:true});
 });
 await wholeButton.click();await expect(quotes).toHaveCount(3);
 const wholeQuote=references(await documentValue())[2];
 const originalWhole=(await observe()).sessions.flatMap(s=>s.messages).find(m=>m.id===sourceB).text;
 assert.equal(wholeQuote.quote.excerpt,originalWhole);assert.equal(wholeQuote.quote.messageId,sourceB);
 await expectLayout('whole quote',[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE,wholeQuote,'']);
 const afterScroll=await page.locator('.a-messages').evaluate(n=>n.scrollTop);
 const scrollEvents=await page.evaluate(()=>[...window.quoteScroll,{event:'after',...window.quoteGeometry()}]);
 if(proof)writeFileSync(join(proof,'collect-scroll.json'),JSON.stringify({beforeScroll,afterScroll,scrollEvents},null,2));
 // Lazy content above the viewport can change scrollTop while the existing
 // reader-anchor controller correctly keeps the source at the same pixel.
 // The visible anchor, not an absolute scroll counter, is the promised result.
 assert.ok(Math.abs(scrollEvents.at(-1).buttonTop-scrollEvents[0].buttonTop)<=1,`Collecting moved the visible source: ${JSON.stringify(scrollEvents)}`);
 assert.ok(await page.evaluate(()=>document.activeElement===window.beforeQuoteFocus),'Collecting must not move focus');
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('After all references');
 await enteredCommentary('After all references');
 let expected=[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE,wholeQuote,AFTER];
 await expectLayout('after typed',expected);
 await editor.pressSequentially(UNICODE);
 await enteredCommentary(AFTER+UNICODE);
 expected=[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE];
 await expectLayout('Unicode whitespace typed',expected);
 assert.equal((await observe()).sends.length,0,'Collecting must not send');
 await editor.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,keyCode:229});
 assert.equal((await observe()).sends.length,0,'IME Enter must not send');
 await expectLayout('IME key leaves exact document unchanged',expected);
 let doc=await documentValue(),initial=[firstQuote,secondQuote,wholeQuote],comments=[BEFORE,BETWEEN,BEFORE_WHOLE,AFTER+UNICODE];
 assert.equal(initial[0].quote.excerpt,first.text);assert.equal(initial[1].quote.excerpt,second.text);
 assert.deepEqual(initial[0].quote.spans,[{start:first.start,end:first.end}]);assert.deepEqual(initial[1].quote.spans,[{start:second.start,end:second.end}]);
 assert.deepEqual(initial.map(q=>q.quote.messageId),[sourceA,sourceA,sourceB]);
 assert.equal(new Set(initial.map(q=>q.instanceId)).size,3);
 assert.ok(initial[2].quote.excerpt.length>4096,'Whole-message detail must include all text');
 assert.equal(await quotes.nth(2).locator('pre').textContent(),initial[2].quote.excerpt);
 const expand=quotes.nth(2).getByRole('button',{name:/Show all/});
 await expand.click();await expect(quotes.nth(2).getByRole('button',{name:'Collapse quote'})).toHaveAttribute('aria-expanded','true');
 await quotes.nth(2).getByRole('button',{name:'Collapse quote'}).click();
 assert.equal(await page.evaluate(()=>typeof window.untrusted),'undefined');
 await page.reload();await expect(quotes).toHaveCount(3);assert.deepEqual(await documentValue(),doc);
 await noPassageEditor(page);
 await expectLayout('reload',expected);
 await observeCards('three quotes reconstructed after reload');
 await visibleCardControls('reload scrolled quote',quotes.first());
 // Same action remains focused when valid; disabled edge action gets an enabled fallback.
 await quotes.nth(1).getByRole('button',{name:'Move quote earlier',exact:true}).focus();await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement?.disabled),false);
 let moved=await documentValue();assert.deepEqual(references(moved).map(q=>q.instanceId),[initial[1].instanceId,initial[0].instanceId,initial[2].instanceId]);assert.deepEqual(commentary(moved),comments);
 expected=[BEFORE,secondQuote,BETWEEN,firstQuote,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE];
 await expectLayout('reordered',expected);
 await quotes.nth(1).getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(2);
 doc=await documentValue();assert.deepEqual(commentary(doc),comments);assert.deepEqual(references(doc).map(q=>q.quote.excerpt),[second.text,initial[2].quote.excerpt]);
 expected=[BEFORE,secondQuote,BETWEEN,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE];
 await expectLayout('removed only first quote',expected);
 await page.getByRole('button',{name:'Remove synthetic.txt',exact:true}).waitFor();
 // Held source receipt: moving the caret/mode alone must not retarget the insertion.
 let release,started;const held=new Promise(resolve=>release=resolve),waiting=new Promise(resolve=>started=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='message.reply'){const response=await route.fetch();started();await held;await route.fulfill({response});}else await route.continue();});
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('append');
 const adding=b.getByRole('button',{name:'Reply to message',exact:true}).click();await waiting;
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('caret');await editor.click();await editor.press('ControlOrMeta+Home');
 release();await adding;await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 const delayed=await documentValue(),priorIds=new Set([secondQuote.instanceId,wholeQuote.instanceId]);
 const added=references(delayed).filter(part=>!priorIds.has(part.instanceId));
 assert.equal(added.length,1,'exactly one new quote instance');
 const appendedQuote=added[0];assert.equal(appendedQuote.quote.messageId,sourceB);
 assert.deepEqual(appendedQuote.quote,wholeQuote.quote);
 expected=[BEFORE,secondQuote,BETWEEN,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE,appendedQuote,''];
 await expectLayout('held addition appended after all existing commentary',expected);
 // A changed draft rejects a late snapshot; a moved caret above did not.
 let changedRelease,changedStarted;
 const changedHeld=new Promise(r=>changedRelease=r),changedWaiting=new Promise(r=>changedStarted=r);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();changedStarted();await changedHeld;await route.fulfill({response});}else await route.continue();});
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await changedWaiting;
 await editor.focus();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Newer commentary');
 await enteredCommentary('Newer commentary',{canonicalReady:false});
 expected=[BEFORE,secondQuote,BETWEEN,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE,appendedQuote,'Newer commentary'];
 await expectLayout('new text while source receipt held',expected,{canonicalReady:false});
 changedRelease();await page.getByText('The draft changed. Your text was kept; add the quote again.',{exact:true}).first().waitFor();
 await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 await expectLayout('late changed receipt rejected',expected);
 const sourceLink=quotes.first().getByRole('button',{name:'Go to original quoted message'});
 await sourceLink.focus();await page.keyboard.press('Enter');
 await page.getByRole('button',{name:'Return to reading position'}).click();
 await expect(sourceLink).toBeFocused();
 const options=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
 await options.click();
 const optionsDialog=page.getByRole('dialog',{name:'Chat options',exact:true});
 await optionsDialog.waitFor();
 // A visible portal can paint before its focus/dismissal effect settles.
 // Exercise Escape after the ordinary dialog has actually received focus.
 await expect.poll(()=>optionsDialog.evaluate(node=>node.contains(document.activeElement))).toBe(true);
 await page.keyboard.press('Escape');await expect(options).toBeFocused();
 await expect(page.getByRole('dialog',{name:'Chat options',exact:true})).toHaveCount(0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.composerModel?.open)).toBe(false);
 await expectLayout('compact options preserve interleaving',expected);
 await expectCompact('desktop retained quote draft');
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'quotes-desktop.png')});
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  for(const colorScheme of ['light','dark']){
   await page.emulateMedia({colorScheme});
   const revision=await page.evaluate(async scheme=>{
    const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
    const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,
     composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme}}});
    if(!prepared.accepted)throw Error(prepared.error||'Fixture appearance refused');
    const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});
    if(!applied.accepted)throw Error(applied.error||'Fixture appearance refused');return applied.result.revision;
   },colorScheme);
   await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState()?.revision)).toBe(revision);
   await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',colorScheme);
   await expectCompact(`${width}px ${colorScheme}`);
   await visibleCardControls(`${width}px ${colorScheme}`,quotes.first());
   await expectLayout(`${width}px ${colorScheme} preserves interleaving`,expected);
   if(proof)await page.locator('.a-composer').screenshot({path:join(proof,`quotes-${width}-${colorScheme}.png`)});
  }
 }
 const messagesPane=page.locator('.a-messages'),latest=page.getByRole('button',{name:'Jump to latest messages',exact:true});
 const showLatest=async()=>{
  assert.ok(await messagesPane.evaluate(node=>node.scrollHeight>node.clientHeight+80),'Fixture has genuine scrollback');
  await messagesPane.evaluate(node=>{node.scrollTop=0;node.dispatchEvent(new Event('scroll'))});
  await expect(latest).toBeVisible();
 };
 await showLatest();await expectCompact('320px Latest in flow before enlargement');
 const layoutOnlyDraft=await documentValue();
 const enlargedStyle=await page.addStyleTag({content:'#amp-one{--a-text-lg:24px;--a-text-sm:20px} #amp-one .a-composer-quote small{font-size:1rem}'});
 // Layout stress only: this is the real longest peer caption, not a forged
 // canonical attribution. Restore the DOM label after measuring both cards.
 const sourceLabels=await quotes.evaluateAll(cards=>cards.map(card=>card.querySelector('.a-composer-quote-head>button').textContent));
 await quotes.evaluateAll(cards=>cards.forEach(card=>card.querySelector('.a-composer-quote-head>button').textContent='Sent by Amplifier from another chat'));
 await settleComposerLayout();
 await visibleCardControls('320px enlarged text long caption short quote',quotes.first());
 await visibleCardControls('320px enlarged text',quotes.nth(1));
 const enlargedGeometry=await expectCompact('320px enlarged text');
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'quotes-320-enlarged.png')});
 // Negative control restores the exact superseded bound, not fake quote nodes.
 const oldCap=await page.addStyleTag({content:'#amp-one .a-markdown-composer{max-height:min(400px,35dvh)!important}'});
 try{
  await settleComposerLayout();const negative=await measureCompact();
  compactGeometry.push({stage:'negative: restored viewport-only quote cap',negativeControl:true,...negative});
  if(proof)writeFileSync(join(proof,'compact-quote-geometry.json'),JSON.stringify(compactGeometry,null,2));
  assert.ok(negative.messages.height<80,'Restoring the old quote cap must fail the unchanged conversation-space oracle');
 }finally{await oldCap.evaluate(node=>node.remove())}
 await expectCompact('320px enlarged text after negative control removed');
 await enlargedStyle.evaluate(node=>node.remove());
 await quotes.evaluateAll((cards,labels)=>cards.forEach((card,index)=>card.querySelector('.a-composer-quote-head>button').textContent=labels[index]),sourceLabels);
 const recovered=await expectCompact('320px font recovery without draft edit');
 assert.ok(Number.parseFloat(recovered.editorMaxHeight)>Number.parseFloat(enlargedGeometry.editorMaxHeight),'Removing enlarged chrome returns editor reading space');
 await latest.click();await expect(latest).toHaveCount(0);
 await expectCompact('320px Latest removed without draft edit');
 await showLatest();await expectCompact('320px Latest restored without draft edit');
 const settledFrames=await page.evaluate(async()=>{
  const frames=[];for(let i=0;i<8;i++){await new Promise(requestAnimationFrame);frames.push({
   editor:document.querySelector('.a-markdown-composer').getBoundingClientRect().height,
   messages:document.querySelector('.a-messages').getBoundingClientRect().height})}
  return frames;
 });
 for(const key of ['editor','messages'])assert.ok(Math.max(...settledFrames.map(row=>row[key]))-Math.min(...settledFrames.map(row=>row[key]))<1,'Layout budget settles without height oscillation');
 checkpoints.push({stage:'layout-only budget recovery',enlargedGeometry,recovered,settledFrames});
 assert.deepEqual(await documentValue(),layoutOnlyDraft,'Sizing never changes immutable quotes or commentary');
 await quotes.nth(1).getByRole('button',{name:/Show all/}).tap();
 await expect(quotes.nth(1).getByRole('button',{name:'Collapse quote'})).toBeVisible();
 await visibleCardControls('320px expanded long quote',quotes.nth(1));
 await quotes.evaluateAll((cards,labels)=>cards.forEach((card,index)=>card.querySelector('.a-composer-quote-head>button').textContent=labels[index]),sourceLabels);
 const submitted=await documentValue();await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.a-message [data-sent-quote]').length===3);
 await expect(quotes).toHaveCount(0);
 await expect.poll(async()=>(await observe()).sends.length).toBe(1);
 const observed=await observe(),sent=observed.sessions.flatMap(s=>s.messages).find(m=>m.replyTo?.version===2);
 assert.ok(sent);assert.deepEqual(sent.replyTo.segments,submitted.segments);
 assert.deepEqual(projectDocument(submitted),expected,'submitted independently expected interleaving');
 assert.deepEqual(projectDocument(sent.replyTo),expected,'saved sent independently expected interleaving');
 for(const text of [BEFORE,BETWEEN,BEFORE_WHOLE,AFTER+UNICODE,'Newer commentary'])assert.ok(sent.text.includes(text),'saved readable message missing '+text);
 assert.equal(sent.attachments[0].name,'synthetic.txt');
 const sentArticle=page.locator('[data-message-id="'+sent.id+'"]');
 await expect(sentArticle.getByRole('button',{name:'Edit message',exact:true})).toBeDisabled();
 await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.fixtureCopied=text;}}});});
 await sentArticle.getByRole('button',{name:'Copy message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>window.fixtureCopied)).toBe(sent.text);
 assert.equal(await page.locator('.a-message [data-sent-quote] .a-composer-quote-text').first().textContent(),second.text);
 await editor.fill('Adjacent cycle');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(c=>c.id===s.selectedSessionId)?.messages.some(m=>m.text==='Adjacent cycle');});
 await page.reload();await expect(quotes).toHaveCount(0);
 await editor.fill(Array.from({length:30},(_,i)=>'Plain draft line '+i).join('\n'));
 assert.ok((await editor.boundingBox()).height<=181,'Removing every quote restores the ordinary draft height cap');
 await editor.fill('');
 // Native transcript selection also works in an empty draft.
 const nativeBinding=selectionBinding(page,'direct-range','native-empty-draft','.a-detail-text');
 await page.setViewportSize({width:1280,height:900});
 await a.locator('.a-detail-text').scrollIntoViewIfNeeded();
 const nativeSelection=await a.locator('.a-detail-text').evaluate((node,binding)=>{
  const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);let text;
  while((text=walker.nextNode())){const start=text.textContent.indexOf('second line');if(start<0)continue;
   const range=document.createRange();range.setStart(text,start);range.setEnd(text,start+'second line'.length);
   const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));
   return window.multiQuoteBindSelection?.(node,binding,'second line','fixture-excerpt')??{...binding,available:false,reason:'probe_unavailable'};
  }throw Error('Native selection fixture text is missing');
 },nativeBinding);
 retainSelectionObservation('range-installed',nativeSelection);
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();await expect(quotes).toHaveCount(1);
 assert.equal(references(await documentValue())[0].quote.excerpt,'second line');
 await observeCards('direct rendered selection');
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(0);
 const c=page.locator('.a-message').filter({hasText:'Quote fixture source C'}).first();
 const selectContents=async(selector,stage='rendered-contents',binding=selectionBinding(page,'selectContents',stage,selector))=>{
  const node=c.locator('.a-detail-text '+selector);
  await node.scrollIntoViewIfNeeded();
  const installed=await node.evaluate((node,binding)=>{
   // A real selection gesture in the transcript leaves the editor's focus.
   // Merely installing a Range while ProseMirror still owns focus lets its next
   // transaction replace the browser selection, an impossible mouse fixture.
   document.activeElement?.blur();
   const range=document.createRange();range.selectNodeContents(node);
   const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));
   // This match is against the installed Range, not an independent quote oracle.
   return window.multiQuoteBindSelection?.(node,binding,range.toString(),'installed-range')??{...binding,available:false,reason:'probe_unavailable'};
  },binding);
  retainSelectionObservation('range-installed',installed);
 };
 for(const [selector,text] of [['p:nth-of-type(2)','A bold inside and label end.'],['ul','first\nsecond'],['p:nth-of-type(3)','Third paragraph café 😀 e\u0301 東京.'],['p:nth-of-type(4)','inline code'],['pre code','  code\tvalue\n'],['p:nth-of-type(5)','Repeated same same.']]){
  await selectContents(selector);
  const quick=page.getByRole('button',{name:'Quote selection',exact:true});await expect(quick).toBeVisible();
  const geometry=await quick.evaluate(button=>{const r=button.getBoundingClientRect(),s=window.getSelection().getRangeAt(0).getBoundingClientRect();return {distance:r.top-s.bottom,selected:window.getSelection().toString()};});
  assert.ok(geometry.distance>=0&&geometry.distance<56,'Quote selection is next to the selected passage');
  await quick.click();await expect(quotes).toHaveCount(1);
  await expect(page.locator('.a-quote-selection')).toHaveCount(0);
  await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
  await page.locator('.a-messages').evaluate(node=>node.dispatchEvent(new Event('scroll')));
  await expect(page.locator('.a-quote-selection')).toHaveCount(0);
  const collected=references(await documentValue())[0];
  assert.equal(collected.quote.excerpt,text,'rendered selection keeps exact independently expected text');
  assert.ok(collected.quote.spans.length>=1);await visibleCardControls('rendered '+selector,quotes.first());
  if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'rendered-'+selector.replace(/\W+/g,'-')+'.png')});
  await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(0);
 }
 // A selected separator at either edge cannot be silently discarded.
 for(const edge of ['trailing','leading']){
  const boundaryBinding=selectionBinding(page,'direct-range','boundary-'+edge,'[data-message-quote-source]');
  await c.locator('.a-detail-text p').first().scrollIntoViewIfNeeded();
  const boundarySelection=await c.locator('[data-message-quote-source]').evaluate((root,{edge,binding})=>{
   document.activeElement?.blur();
   const first=root.querySelector('p'),gap=first.nextSibling,next=gap.nextSibling,range=document.createRange();
   if(edge==='trailing'){range.setStartBefore(first);range.setEndAfter(gap);}
   else{range.setStartBefore(gap);range.setEndAfter(next);}
   const selected=window.getSelection();selected.removeAllRanges();selected.addRange(range);document.dispatchEvent(new Event('selectionchange'));
   return window.multiQuoteBindSelection?.(root,binding,range.toString(),'installed-range')??{...binding,available:false,reason:'probe_unavailable'};
  },{edge,binding:boundaryBinding});
  retainSelectionObservation('range-installed',boundarySelection);
  await expect(page.locator('.a-quote-selection [role=status]')).toHaveText('This boundary whitespace cannot be quoted exactly. Nothing was added.');
  await expect(page.locator('.a-quote-selection button')).toHaveCount(0);
  await noPassageEditor(page);
  await expect(quotes).toHaveCount(0);await page.keyboard.press('Escape');
 }
 // Second occurrence, across formatting, with Unicode nearby: no text search
 // may accidentally quote the earlier identical phrase.
 const repeatedBinding=selectionBinding(page,'direct-range','repeated-occurrence','p:nth-of-type(5)');
 const sourceC=(await observe()).sessions.flatMap(s=>s.messages).find(m=>m.text.startsWith('Quote fixture source C')).text;
 const repeated=c.locator('.a-detail-text p:nth-of-type(5)');await repeated.scrollIntoViewIfNeeded();
 const repeatedSelection=await repeated.evaluate((node,binding)=>{
  const text=node.querySelector('strong').firstChild.firstChild,after=node.lastChild.firstChild;
  const range=document.createRange();range.setStart(text,0);range.setEnd(after,after.length);
  const selected=window.getSelection();selected.removeAllRanges();selected.addRange(range);document.dispatchEvent(new Event('selectionchange'));
  return window.multiQuoteBindSelection?.(node,binding,'same.','fixture-excerpt')??{...binding,available:false,reason:'probe_unavailable'};
 },repeatedBinding);
 retainSelectionObservation('range-installed',repeatedSelection);
 const repeatedButton=page.getByRole('button',{name:'Quote selection',exact:true});
 await repeatedButton.focus();await page.keyboard.press('Enter');await expect(quotes).toHaveCount(1);
 const repeatQuote=references(await documentValue())[0].quote,repeatAt=sourceC.lastIndexOf('same');
 assert.equal(repeatQuote.excerpt,'same.');
 assert.deepEqual(repeatQuote.spans,[{start:[...sourceC.slice(0,repeatAt)].length,end:[...sourceC.slice(0,repeatAt+4)].length},{start:[...sourceC.slice(0,repeatAt+6)].length,end:[...sourceC.slice(0,repeatAt+7)].length}]);
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();
 // A delayed A receipt must not dismiss a newer B selection in the same source.
 let selectionRelease,selectionStarted;
 const selectionHeld=new Promise(resolve=>selectionRelease=resolve),selectionWaiting=new Promise(resolve=>selectionStarted=resolve);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();selectionStarted();await selectionHeld;await route.fulfill({response});}else await route.continue();});
 await selectContents('p:nth-of-type(2)','delayed-A-held');await page.getByRole('button',{name:'Quote selection',exact:true}).click();await selectionWaiting;
 await selectContents('p:nth-of-type(3)','delayed-B-before-release');
 const selectionState=()=>page.evaluate(()=>({
  text:window.getSelection()?.toString(),active:document.activeElement?.outerHTML.slice(0,300),
  action:document.querySelector('.a-quote-selection')?.textContent,
 }));
 const selectionBefore=await selectionState();
 assert.equal(selectionBefore.text,'Third paragraph café 😀 e\u0301 東京.');
 assert.equal(selectionBefore.active.includes('a-markdown-composer'),false,'Transcript selection does not retain composer focus');
 selectionRelease();await expect(quotes).toHaveCount(1);await page.unroute('**/api/actions');
 if(proof)writeFileSync(join(proof,'selection-receipt.json'),JSON.stringify({before:selectionBefore,after:await selectionState()},null,2));
 await expect.poll(async()=>(await selectionState()).text).toBe('Third paragraph café 😀 e\u0301 東京.');
 await expect(page.getByRole('button',{name:'Quote selection',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();await expect(quotes).toHaveCount(2);
 assert.deepEqual(references(await documentValue()).map(row=>row.quote.excerpt),['A bold inside and label end.','Third paragraph café 😀 e\u0301 東京.']);
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();
 await editor.fill(Array.from({length:30},(_,i)=>'Plain after removal '+i).join('\n'));
 assert.ok((await editor.boundingBox()).height<=181,'Removing the last quote without remounting restores ordinary draft height');
 await editor.fill('');
 await selectContents('p:nth-of-type(2)','dismissed-selection');
 const dismissedText=await page.evaluate(()=>window.getSelection().toString());
 await page.keyboard.press('Escape');await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 await page.keyboard.press('Escape');
 assert.equal(await page.evaluate(()=>window.getSelection().toString()),dismissedText);
 await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
 await page.locator('.a-messages').evaluate(node=>node.dispatchEvent(new Event('scroll')));
 await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 // A fresh source gesture may quote the same range again.
 const hiddenBinding=selectionBinding(page,'selectContents','hidden-pending-selection','p:nth-of-type(2)');
 await c.locator('.a-detail-text p:nth-of-type(2)').dispatchEvent('pointerdown');
 await selectContents('p:nth-of-type(2)','hidden-pending-selection',hiddenBinding);
 let hiddenRelease,hiddenStarted,hiddenSettled;
 const hiddenHold=new Promise(resolve=>hiddenRelease=resolve),hiddenWait=new Promise(resolve=>hiddenStarted=resolve),hiddenDone=new Promise(resolve=>hiddenSettled=resolve);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();hiddenStarted();await hiddenHold;await route.fulfill({response});hiddenSettled();}else await route.continue();});
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();await hiddenWait;
 const readingTop=await page.locator('.a-messages').evaluate(node=>{const top=node.scrollTop;node.scrollTop=0;node.dispatchEvent(new Event('scroll'));return top;});
 await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>window.getSelection().toString()),dismissedText);
 hiddenRelease();await hiddenDone;await expect(quotes).toHaveCount(1);await page.unroute('**/api/actions');
 await page.locator('.a-messages').evaluate((node,top)=>{node.scrollTop=top;node.dispatchEvent(new Event('scroll'));},readingTop);
 await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();
 // A newer gesture can select the identical source interval while A is pending.
 const sameABinding=selectionBinding(page,'selectContents','same-interval-A','p:nth-of-type(2)');
 await c.locator('.a-detail-text p:nth-of-type(2)').dispatchEvent('pointerdown');
 await selectContents('p:nth-of-type(2)','same-interval-A',sameABinding);
 let sameRelease,sameStarted,sameSettled;
 const sameHold=new Promise(resolve=>sameRelease=resolve),sameWait=new Promise(resolve=>sameStarted=resolve),sameDone=new Promise(resolve=>sameSettled=resolve);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();sameStarted();await sameHold;await route.fulfill({response});sameSettled();}else await route.continue();});
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();await sameWait;
 await page.keyboard.press('Escape');await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 const sameBBinding=selectionBinding(page,'selectContents','same-interval-B','p:nth-of-type(2)');
 await c.locator('.a-detail-text p:nth-of-type(2)').dispatchEvent('pointerdown');await selectContents('p:nth-of-type(2)','same-interval-B',sameBBinding);
 await expect(page.getByRole('button',{name:'Quote selection',exact:true})).toBeVisible();
 sameRelease();await sameDone;await expect(quotes).toHaveCount(1);await page.unroute('**/api/actions');
 await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
 await page.locator('.a-messages').evaluate(node=>node.dispatchEvent(new Event('scroll')));
 await expect(page.getByRole('button',{name:'Quote selection',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();await expect(quotes).toHaveCount(2);
 assert.equal(references(await documentValue())[0].quote.id,references(await documentValue())[1].quote.id);
 for(let i=0;i<2;i++)await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();
 // Keep keyboard activation, but do not leave the action over unrelated text
 // after scrolling the selected source away.
 await selectContents('p:nth-of-type(2)','keyboard-offscreen');await page.getByRole('button',{name:'Quote selection',exact:true}).focus();
 await page.locator('.a-messages').evaluate(node=>{node.scrollTop=0;node.dispatchEvent(new Event('scroll'));});
 await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 // Unmappable text refuses in place, without a fallback editor, quote request
 // or mutation of the saved commentary, existing quote, caret or attachment.
 await editor.fill('Keep this draft');
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await expect(quotes).toHaveCount(1);
 await editor.focus();await editor.press('ControlOrMeta+End');await page.keyboard.insertText('After the saved quote');
 await enteredCommentary('After the saved quote');
 await page.locator('.a-file-input').setInputFiles({name:'refusal.txt',mimeType:'text/plain',buffer:Buffer.from('Keep this attachment')});
 await page.getByRole('button',{name:'Remove refusal.txt',exact:true}).waitFor();
 await editor.focus();await editor.press('ControlOrMeta+End');
 const refusalDocument=await documentValue(),refusalCanonical=await canonicalDraft();
 let refusedRequests=0;
 const countReply=request=>{if(request.url().endsWith('/api/actions')&&request.postDataJSON()?.action==='message.reply')refusedRequests++;};
 page.on('request',countReply);
 for(const width of [1280,320]){
  const refusalBinding=selectionBinding(page,'selectContents','unmappable-selection-'+width,'p:last-child');
  await page.setViewportSize({width,height:844});
  // Escape deliberately keeps the old selection dismissed through resize.
  // The next viewport case needs a new source gesture, not a replayed Range.
  await c.locator('.a-detail-text p:last-child').dispatchEvent('pointerdown');
  await selectContents('p:last-child','unmappable-selection-'+width,refusalBinding);
  const explanation=page.locator('.a-quote-selection [role=status]');
  await expect(explanation).toHaveText('This selection includes controls or unmapped text. Nothing was added.');
  await expect(explanation).toBeInViewport();await expect(page.locator('.a-quote-selection button')).toHaveCount(0);
  await noPassageEditor(page);
  assert.deepEqual(await documentValue(),refusalDocument);assert.deepEqual(await canonicalDraft(),refusalCanonical);
  await expect(page.getByRole('button',{name:'Remove refusal.txt',exact:true})).toBeVisible();
  if(proof)await page.screenshot({path:join(proof,`refusal-${width}.png`)});
  await page.keyboard.press('Escape');await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 }
 page.off('request',countReply);assert.equal(refusedRequests,0,'Unmappable selection must not request a quote');
 // Test the app's remembered insertion caret without refocusing the DOM
 // contenteditable (native focus may establish a new browser selection).
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await expect(quotes).toHaveCount(2);
 const caretQuote=references(await documentValue())[1];
 await expectLayout('refusal preserves remembered quote caret',[...projectDocument(refusalDocument),caretQuote,'']);
 await quotes.nth(1).getByRole('button',{name:'Remove quote',exact:true}).click();
 await editor.focus();await editor.press('ControlOrMeta+End');await page.keyboard.insertText('New text after refusal');
 await enteredCommentary('New text after refusal');
 await page.reload();await expect(quotes).toHaveCount(1);
 await expect(editor).toContainText('Keep this draft');await expect(editor).toContainText('After the saved quote');
 await expect(editor).toContainText('New text after refusal');
 await expect(page.getByRole('button',{name:'Remove refusal.txt',exact:true})).toBeVisible();await noPassageEditor(page);
 await page.getByRole('button',{name:'Remove refusal.txt',exact:true}).click();
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await editor.fill('');
 await page.setViewportSize({width:1280,height:900});await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 // Source revision changes before snapshot admission reject the captured digest.
 // This is a fresh source gesture after editor/quote removal, not a synthetic
 // selectionchange replay while the transcript's prior gesture is dismissed.
 const revisionBinding=selectionBinding(page,'selectContents','source-revision-before-admission','p:nth-of-type(2)');
 await c.locator('.a-detail-text p:nth-of-type(2)').dispatchEvent('pointerdown');
 await selectContents('p:nth-of-type(2)','source-revision-before-admission',revisionBinding);
 await page.route('**/api/actions',async route=>{
  if(route.request().postDataJSON()?.action==='message.reply'){
   await context.request.post(url+'/fixture/quote-source-change',{data:{id:await c.getAttribute('data-message-id'),text:sourceC+'\nRevised source.'}});
  }
  await route.continue();
 });
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();
 await page.getByText('The exact source changed. Select the passage again.',{exact:true}).first().waitFor();
 await expect(quotes).toHaveCount(0);await page.unroute('**/api/actions');await page.keyboard.press('Escape');
 // A held reply belongs to its captured chat, never a new chat's private draft.
 const originalSession=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Separate synthetic quote draft'}));
 const separateSession=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.notEqual(separateSession,originalSession);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),originalSession);
 await editor.fill('Retain original draft');
 let chatRelease,chatStarted,chatSettled;
 const chatHeld=new Promise(resolve=>chatRelease=resolve),chatWaiting=new Promise(resolve=>chatStarted=resolve),chatDone=new Promise(resolve=>chatSettled=resolve);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();chatStarted();await chatHeld;await route.fulfill({response});chatSettled();}else await route.continue();});
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await chatWaiting;
 // Navigation paints immediately; its ordered acknowledgement may follow the
 // deliberately held reply. Do not await it before releasing that reply.
 await page.evaluate(id=>{void window.amplifier.dispatch('session.select',{id});},separateSession);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).not.toBe(originalSession);
 await editor.fill('Separate draft');chatRelease();await chatDone;await page.unroute('**/api/actions');
 await expect(editor).toHaveText('Separate draft');await expect(quotes).toHaveCount(0);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),originalSession);
 await expect(editor).toHaveText('Retain original draft');await expect(quotes).toHaveCount(0);
 // Quote feedback stays with its originating chat (#1126). The late refusal explains the missing
 // quote in the original chat and is absent beneath the other chat's composer.
 const changedNotice=page.getByText('The conversation changed. The quote was not added.',{exact:true});
 await changedNotice.first().waitFor();
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),separateSession);
 await expect(editor).toHaveText('Separate draft');await expect(changedNotice).toHaveCount(0);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),originalSession);
 await expect(editor).toHaveText('Retain original draft');await changedNotice.first().waitFor();
 // A successful quote's confirmation does not follow the user into another chat either.
 const addedNotice=page.getByText('Quote added. Earlier quotes, commentary and attachments kept.',{exact:true});
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await expect(quotes).toHaveCount(1);await addedNotice.first().waitFor();
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),separateSession);
 await expect(editor).toHaveText('Separate draft');await expect(addedNotice).toHaveCount(0);await expect(changedNotice).toHaveCount(0);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),originalSession);
 await expect(quotes).toHaveCount(1);await addedNotice.first().waitFor();
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(0);
 // With no quote left, "Quote added" no longer describes the draft.
 await expect(addedNotice).toHaveCount(0);
 assert.equal((await observe()).sends.length,2,'Quote/remove must not execute another turn');
 // The reported path (#1126): quote, send, then another chat. Sending takes the quote with it,
 // so its feedback ends there, in this chat, another chat and a new one.
 const pendingNotice=page.getByText('Adding quote…',{exact:true});
 const expectNoQuoteNotice=async()=>{for(const notice of [addedNotice,changedNotice,pendingNotice])await expect(notice).toHaveCount(0);};
 const showChat=async id=>{await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(id);};
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await expect(quotes).toHaveCount(1);await addedNotice.first().waitFor();
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(async()=>(await observe()).sends.length).toBe(3);await expect(quotes).toHaveCount(0);
 await expectNoQuoteNotice();
 await showChat(separateSession);await expectNoQuoteNotice();
 await showChat(originalSession);await expectNoQuoteNotice();
 await page.evaluate(()=>{void window.amplifier.dispatch('session.draft',{});});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).not.toBe(originalSession);
 await expectNoQuoteNotice();
 await showChat(originalSession);
 // A quote still pending when the user leaves keeps the draft and attachment where they were,
 // shows nothing in the other chat, and explains the missing quote on return.
 await editor.fill('Keep pending draft');
 await page.locator('.a-file-input').setInputFiles({name:'pending.txt',mimeType:'text/plain',buffer:Buffer.from('Pending attachment')});
 const pendingFile=page.getByRole('button',{name:'Remove pending.txt',exact:true});await expect(pendingFile).toBeVisible();
 let lateRelease,lateStarted,lateSettled;
 const lateHeld=new Promise(resolve=>lateRelease=resolve),lateWaiting=new Promise(resolve=>lateStarted=resolve),lateDone=new Promise(resolve=>lateSettled=resolve);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();lateStarted();await lateHeld;await route.fulfill({response});lateSettled();}else await route.continue();});
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await lateWaiting;
 await page.evaluate(id=>{void window.amplifier.dispatch('session.select',{id});},separateSession);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(separateSession);
 lateRelease();await lateDone;await page.unroute('**/api/actions');
 await expect(editor).toHaveText('Separate draft');await expect(quotes).toHaveCount(0);await expect(pendingFile).toHaveCount(0);await expectNoQuoteNotice();
 await showChat(originalSession);
 await changedNotice.first().waitFor();await expect(addedNotice).toHaveCount(0);await expect(pendingNotice).toHaveCount(0);
 await expect(editor).toHaveText('Keep pending draft');await expect(quotes).toHaveCount(0);await expect(pendingFile).toBeVisible();
 await pendingFile.click();await editor.fill('');
 // Editing without removing a quote leaves the refusal in place.
 await expect(changedNotice.first()).toBeVisible();
 assert.equal((await observe()).sends.length,3,'A late quote must not execute another turn');
 assert.deepEqual(errors,[]);
 async function compareReference(){
  // Optional private design comparison. The supplied HTML is data in an opaque
  // script-only sandbox, never product source or a privileged page.
  let comparisonPage,comparisonStage='context-setup';
  try{
  const comparisonContext=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  comparisonContext.setDefaultTimeout(10000);
  const page=await comparisonContext.newPage();
  comparisonPage=page;
  selectionBinding(page,'comparison','before-selection',null);
  await page.addInitScript(installSelectionObserver);
  const comparisonStages=[];
  const stage=name=>{
   comparisonStage=['app navigation','sandbox document','reference sample','sandbox connection guard','sample sources',
    'insert text','insert quote','capture 1280','capture 390','capture 320'].includes(name)?name:'comparison-other';
   comparisonStages.push(name);writeFileSync(join(proof,'comparison-stages.json'),JSON.stringify(comparisonStages));
  };
  stage('app navigation');
  await page.goto(url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);
  const editor=page.getByRole('textbox',{name:'Message Amplifier'});
  const documentValue=()=>page.evaluate(()=>window.amplifier.getState().view.draftDocument);
  const referenceContext=await browser.newContext();
  referenceContext.setDefaultTimeout(10000);
  const referencePage=await referenceContext.newPage();
  await referencePage.route('**/*',route=>route.abort());
  const blockedSockets=[];
  // Routed sockets have no server connection unless connectToServer is called.
  // Close every attempted socket, including the controlled probe below.
  await referencePage.routeWebSocket('**/*',socket=>{blockedSockets.push(socket.url());socket.close();});
  stage('sandbox document');
  await referencePage.setContent(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src about: 'self'; base-uri 'none'; form-action 'none'"><iframe id="reference" sandbox="allow-scripts" style="border:0;width:100%;height:1200px"></iframe>`);
  await referencePage.locator('iframe').evaluate((node,html)=>{
   // Keep the supplied content/style/script unchanged; install the isolation
   // policy before its first script as well as on the embedding document.
   const policy=`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">`;
   if(!html.includes('<head>'))throw Error('Reference requires a head for its sandbox policy');
   node.srcdoc=html.replace('<head>','<head>'+policy);
  },readFileSync(process.env.AMPLIFIER_QUOTE_REFERENCE,'utf8'));
  const reference=referencePage.frameLocator('#reference');
  stage('reference sample');
  await reference.getByRole('button',{name:'Add three-quote example',exact:true}).click();
  stage('sandbox connection guard');
  await reference.locator('#editor').evaluate((node,socketUrl)=>{
   window.referenceViolations=[];
   document.addEventListener('securitypolicyviolation',event=>window.referenceViolations.push(event.violatedDirective));
   try{const socket=new WebSocket(socketUrl);socket.onerror=()=>{};}
   catch(error){window.referenceSocketError=error.message;}
  },url.replace('http:','ws:')+'/blocked-reference-check');
  // Routing can reject before CSP dispatches a violation event. Observe the
  // effective blocker, not an assumed order between two independent guards.
  const socketProbe=url.replace('http:','ws:')+'/blocked-reference-check';
  try{
   await expect.poll(async()=>blockedSockets.includes(socketProbe)||(await reference.locator('#editor').evaluate(()=>window.referenceViolations)).includes('connect-src')).toBe(true);
  }finally{
   writeFileSync(join(proof,'reference-sandbox.json'),JSON.stringify({socketProbe,blockedSockets,webSocketRoute:'closes without connectToServer; no forwarding configured',
    ...await reference.locator('#editor').evaluate(()=>({violations:window.referenceViolations,error:window.referenceSocketError,policy:document.querySelector('meta[http-equiv="Content-Security-Policy"]').content}))},null,2));
  }
  stage('sample sources');
  const specimen=await reference.locator('#editor').evaluate(()=>({sources:sources(),blocks:draft().blocks}));
  const seeded=await context.request.post(url+'/fixture/quote-comparison',{data:specimen.sources.map(source=>({role:source.role,text:source.text}))});
  assert.equal(seeded.status(),200);const ids=(await seeded.json()).ids;
  await editor.fill('');
  await page.locator('.a-file-input').setInputFiles({name:'outline.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic comparison attachment')});
  await page.getByRole('button',{name:'Remove outline.txt',exact:true}).waitFor();
  let comparedQuotes=0;
  for(const block of specimen.blocks){
   stage('insert '+block.type);
   if(block.type==='text'){
    if(block.value){await editor.focus();await editor.press('ControlOrMeta+End');await page.keyboard.insertText(block.value);}
   }else{
    const index=specimen.sources.findIndex(source=>source.id===block.source.id),article=page.locator('[data-message-id="'+ids[index]+'"]');
    if(block.kind==='whole')await article.getByRole('button',{name:'Reply to message',exact:true}).click();
    else{
     await quoteRendered(page,article,block.excerpt);
    }
    await expect(editor.locator('[data-quote-instance]')).toHaveCount(++comparedQuotes);
   }
  }
  assert.deepEqual(references(await documentValue()).map(row=>row.quote.excerpt),specimen.blocks.filter(row=>row.type==='quote').map(row=>row.excerpt));
  for(const width of [1280,390,320]){
   stage('capture '+width);
   await referencePage.setViewportSize({width,height:1200});await page.setViewportSize({width,height:900});
   await editor.evaluate(node=>node.scrollTop=0);
   await reference.locator('.composer-panel').screenshot({path:join(proof,`reference-${width}.png`)});
   await page.locator('.a-composer').screenshot({path:join(proof,`actual-matching-${width}.png`)});
  }
  writeFileSync(join(proof,'comparison-specimen.json'),JSON.stringify({specimen,actual:await documentValue(),scope:'Matching source/excerpts and commentary; prototype-only lab and simulation controls excluded from acceptance'},null,2));
  await referenceContext.close();await comparisonContext.close();
  }catch(error){
   // Capture here for failures outside quoteRendered too (scroll, Range,
   // quote-count, capture, or setup), while this exact page is still in scope.
   await observeSelectionFailure(comparisonPage,error,comparisonStage);
   throw error;
  }
 }
 if(comparisonError)throw comparisonError;
 if(proof)writeFileSync(join(proof,'observed-document.json'),JSON.stringify({sourceA,sourceB,first,second,expected,submitted,sent,initialScroll:beforeScroll,selectionObservations,omittedSelectionObservations:selectionEvidence.omitted},null,2));
 console.log('PASS exact Unicode/multiline passages + whole message, interleaved commentary, attachment, reload, keyboard reorder, removal, captured delayed target, narrow rendering, sent projection and adjacent cycle.');
}catch(error){
 await observeSelectionFailure(page,error);
 if(proof){
  const record={message:error.message,errors,selectionObservations,omittedSelectionObservations:selectionEvidence.omitted};
  try{writeFileSync(join(proof,'failure.json'),JSON.stringify(record,null,2))}catch{}
  const screenshot=await observeOptional(()=>page.screenshot({path:join(proof,'failure.png'),timeout:OPTIONAL_OBSERVATION_MS}));
  // Expiry is not cancellation or proof of a complete PNG. Preserve the original
  // failure even if screenshot settlement or this optional report write fails.
  record.failureScreenshot={status:screenshot.status,budgetMs:OPTIONAL_OBSERVATION_MS};
  try{writeFileSync(join(proof,'failure.json'),JSON.stringify(record,null,2))}catch{}
 }
 throw error;
}finally{await browser.close();fixture.kill();await new Promise(resolve=>fixture.exitCode!==null?resolve():fixture.once('exit',resolve));}
