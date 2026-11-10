// Presentation-only red regression for the approved interleaved Work layout.
// This deliberately uses body-bearing fixture nodes. It does NOT qualify capture,
// canonical detail resolution or cold reopen; test_delegate_work_stream.py owns
// that independent boundary. Run against built assets in the owned test instance:
// node tests/delegate-work-stream-browser.mjs (from frontend/).
// --mobile-only selects 390x844 for all scenes; it does not skip any assertions.
// --appearance-scope-only runs just the maintained aside paint regression at all
// three widths; it does not claim the unrelated observer/attribution scenarios.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft,clearComposer} from './composer-test-helpers.mjs';
import {delegateStreamDiagnostics} from './delegate-stream-diagnostics.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'test-results');
mkdirSync(output,{recursive:true});
const artifact=name=>join(output,name);
const mobileOnly=process.argv.includes('--mobile-only');
const appearanceScopeOnly=process.argv.includes('--appearance-scope-only');
assert.ok(!(mobileOnly&&appearanceScopeOnly),'The appearance regression requires all three widths');
const desktop={width:1280,height:1000},mobile={width:390,height:844};
const viewports=mobileOnly?[['mobile',mobile]]:[['desktop',desktop],['mobile',mobile],['small',{width:320,height:844}]];
const navigationEntries=[];
const layoutChecks=[],apiFailures=[],transitionChecks=[];
const appearanceScopeChecks=[];
const diagnostics=delegateStreamDiagnostics();
const lookupEvents=[],lookupRequests=new Map();
const nodeEvidence=node=>node?{
  id:node.id,kind:node.kind,toolCallId:node.toolCallId,toolObservationId:node.toolObservationId,
  detailVersion:node.detailVersion,toolDetail:node.toolDetail,contentAvailability:node.contentAvailability,
  delegateRequestDetail:node.delegateRequestDetail,delegateResponseDetail:node.delegateResponseDetail,
  requestChars:node.delegateRequest?.length,responseChars:node.delegateResponse?.length,
  exchange:node.exchange?nodeEvidence({...node.exchange,exchange:undefined}):undefined
}:null;
const saveLookups=()=>writeFileSync(artifact('delegate-work-stream-lookups.json'),JSON.stringify(lookupEvents,null,2));
let scene='stream';
function observeApi(page,origin) {
  page.on('request',diagnostics.listen('lookup request metadata',request=>{
    const url=new URL(request.url());
    if(scene!=='observer'||url.origin!==origin||!['/api/conversation/detail','/api/conversation/work-sync'].includes(url.pathname)||lookupEvents.length>=300)return;
    const row={sequence:lookupEvents.length,at:Date.now(),scene,method:request.method(),
      path:url.pathname,query:Object.fromEntries(url.searchParams),client:request.headers()['x-amplifier-client']};
    if(url.pathname.endsWith('work-sync'))row.input=request.postDataJSON();
    lookupRequests.set(request,row);lookupEvents.push(row);
  }));
  page.on('requestfailed',diagnostics.listen('lookup request failure',request=>{
    const row=lookupRequests.get(request);
    if(row){row.cancelledOrFailed=request.failure();saveLookups()}
  }));
  page.on('response',diagnostics.listen('lookup response metadata',response=>{
    const tracked=lookupRequests.get(response.request());
    if(tracked) {
      tracked.status=response.status();
      diagnostics.track('lookup response body/evidence',async()=>{
        let body;
        try{body=await response.json()}catch(error){tracked.readError=String(error);throw error}
        tracked.result={lookupKey:body.lookupKey,before:body.before,error:body.error,coverage:body.coverage,
          revision:body.revision,group:body.group,order:body.order,item:nodeEvidence(body.item),
          items:body.items?.filter(n=>n.toolCallId==='observer-delegate'||n.exchange).map(nodeEvidence),
          valueChars:typeof body.value==='string'?body.value.length:undefined,nextOffset:body.nextOffset};
        saveLookups();
      });
    }
    const url=new URL(response.url());
    if(url.origin!==origin||!url.pathname.startsWith('/api/')||response.status()<400)return;
    const record={scene,method:response.request().method(),path:url.pathname+url.search,status:response.status()};
    apiFailures.push(record);
    diagnostics.track('failed API response body',async()=>{
      try{record.body=(await response.text()).slice(0,2000)}
      catch(error){record.bodyError=String(error);throw error}
    });
  }));
}
async function cleanScene(page) {
  await diagnostics.collect();
  await diagnostics.attempt('API failure report',()=>writeFileSync(
    artifact('delegate-work-stream-api-failures.json'),JSON.stringify(apiFailures,null,2)));
  await expect(page.getByText(/Invalid request: This message is no longer available/)).toHaveCount(0);
  assert.deepEqual(apiFailures,[],'Normal scene must not issue rejected API requests');
}
// Measure the CURRENT scroll position; unlike the centered owner oracle, this
// catches a floating control covering any visible reply, header or owner glyph.
function visibleWorkGeometry(work) {
  const pane=work.closest('[data-part="messages"]'),p=pane.getBoundingClientRect();
  const clip={left:Math.max(0,p.left+pane.clientLeft),top:Math.max(0,p.top+pane.clientTop),
    right:Math.min(innerWidth,p.left+pane.clientLeft+pane.clientWidth),bottom:Math.min(innerHeight,p.top+pane.clientTop+pane.clientHeight)};
  const hits=[],problems=[];
  for(const region of work.querySelectorAll('.a-delegate-message header,.a-delegate-markdown,.a-work-actor')) {
    const walker=document.createTreeWalker(region,NodeFilter.SHOW_TEXT);
    for(let text=walker.nextNode();text;text=walker.nextNode())for(const match of text.textContent.matchAll(/\S+/gu)) {
      const range=document.createRange();range.setStart(text,match.index);range.setEnd(text,match.index+match[0].length);
      for(const rect of range.getClientRects()) {
        const left=Math.max(rect.left,clip.left),right=Math.min(rect.right,clip.right);
        const top=Math.max(rect.top,clip.top),bottom=Math.min(rect.bottom,clip.bottom);
        if(right-left<2||bottom-top<2)continue;
        for(const fraction of [.2,.5,.8]) {
          const x=left+(right-left)*fraction,y=(top+bottom)/2,hit=document.elementFromPoint(x,y);
          const inside=!!hit&&text.parentElement.contains(hit);
          hits.push({word:match[0],x,y,inside,hit:hit?{tag:hit.tagName,class:hit.getAttribute('class'),id:hit.id}:null});
          if(!inside)problems.push(`Obscured visible text: ${match[0]}`);
        }
      }
    }
  }
  const jump=work.closest('[data-part="conversation"]').querySelector('.a-chat-jump button');
  const composer=work.closest('[data-part="conversation"]').querySelector('[data-part="composer"]');
  return {hits,problems,pane:p.toJSON(),jump:jump?.getBoundingClientRect().toJSON()??null,
    composer:composer.getBoundingClientRect().toJSON(),scrollTop:pane.scrollTop};
}
async function checkWorkLayout(page,work,label) {
  const measured=await work.evaluate(visibleWorkGeometry);
  layoutChecks.push({label,viewport:page.viewportSize(),...measured});
  writeFileSync(artifact('delegate-work-stream-layout.json'),JSON.stringify(layoutChecks,null,2));
  assert.ok(measured.hits.length,'Visible Work glyphs must actually be sampled');
  assert.deepEqual(measured.problems,[],label+': visible Work text is readable');
  if(measured.jump) {
    assert.ok(measured.pane.bottom<=measured.jump.top+1,label+': jump control must sit outside message pane');
    assert.ok(measured.jump.bottom<=measured.composer.top+1,label+': jump control must not cover composer');
  }
  assert.ok(measured.composer.bottom<=page.viewportSize().height+1,label+': composer remains in viewport');
}
async function checkAsideAppearanceScope(page,work,viewport,setWorkerPhase) {
  const action=async(action,args={})=>{
    const receipt=await page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
    assert.equal(receipt.accepted,true,action);return receipt.result;
  };
  const priorTheme=await page.evaluate(()=>window.amplifier.getState().theme);
  const priorPresentation=await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition);
  const present=async composition=>{
    const applied=await page.evaluate(async composition=>{
      const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
      const prepared=await window.amplifier.dispatch('shell.changes.prepare',{
        clientId,expectedRevision:shell.revision,composition});
      if(!prepared.accepted)throw Error('Appearance-scope presentation prepare failed');
      const receipt=await window.amplifier.dispatch('shell.changes.apply',{
        clientId,expectedRevision:shell.revision,changeId:prepared.result.id});
      if(!receipt.accepted)throw Error('Appearance-scope presentation apply failed');
      if(!Number.isInteger(receipt.result.revision))throw Error('Appearance-scope apply omitted its revision');
      return receipt.result.revision;
    },composition);
    await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().revision)).toBe(applied);
  };
  // This is the production DelegateStreamStatus <aside>, not injected markup.
  const status=work.getByRole('complementary',{name:'Review status',exact:true});
  await expect(status).toBeVisible();
  const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
  const editor=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  const draft=await readComposerDraft(editor);
  const probeDraft=`Unsent appearance check ${viewport}`;
  await editor.fill(probeDraft);await expect(editor).toHaveDraft(probeDraft);
  // Exercise the native multi-control status so narrow layouts must really wrap.
  // Fixture metadata changes do not start a worker or activate Stop.
  await setWorkerPhase('running');
  await expect(status.getByRole('button',{name:'Stop worker',exact:true})).toBeVisible();
  const listed=await action('theme.list');
  assert.deepEqual(listed.errors,[]);
  const builtins=listed.items.filter(row=>row.source==='built-in');
  assert.ok(builtins.length>0,'The built-in appearance set must not be empty');
  assert.ok(builtins.some(row=>row.id==='builtin:digital-rain'),'The affected built-in must be exercised');
  for(const entry of builtins){
    const theme=await action('theme.read',{id:entry.id});
    await action('theme.apply',{name:theme.name,css:theme.css});
    await expect.poll(()=>page.locator('#amp-one > style').evaluateAll((nodes,css)=>
      nodes.some(node=>node.textContent===css),theme.css)).toBe(true);
    for(const scheme of ['light','dark']){
      await present({...priorPresentation,presentation:{...priorPresentation.presentation,scheme}});
      await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
      await status.scrollIntoViewIfNeeded();
      const details=status.getByRole('button',{name:'Review delegation details',exact:true});
      await details.focus();await details.press('Tab');await expect(details).not.toBeFocused();
      await page.keyboard.press('Shift+Tab');await expect(details).toBeFocused();
      const measured=await status.evaluate(el=>{
        const style=getComputedStyle(el),box=el.getBoundingClientRect();
        const first=el.querySelector(':scope > .a-caption'),range=document.createRange();
        range.selectNodeContents(first);
        const text=range.getBoundingClientRect(),button=el.querySelector(':scope > button[aria-label="Review delegation details"]');
        const focus=getComputedStyle(button),control=button.getBoundingClientRect();
        const children=[...el.children].map(child=>{
          const rect=child.getBoundingClientRect();
          return {left:rect.left-box.left,right:rect.right-box.left,
            top:rect.top-box.top,bottom:rect.bottom-box.top};
        });
        return {shadow:style.boxShadow,background:style.backgroundColor,radius:style.borderRadius,
          border:[style.borderLeftWidth,style.borderRightWidth],width:box.width,scrollWidth:el.scrollWidth,
          height:box.height,clientWidth:el.clientWidth,textInset:text.left-box.left,children,
          wrapped:control.top>=text.bottom,
          captionTopMargin:style.marginTop,
          labelMargin:getComputedStyle(first).marginTop,
          sameLineCenterDelta:Math.abs((text.top+text.bottom)/2-(control.top+control.bottom)/2),
          control:{width:control.width,height:control.height,outline:focus.outlineStyle,
            outlineWidth:parseFloat(focus.outlineWidth)}};
      });
      // The outer navigation surface owns the accent. The host deliberately
      // resets the inner rail's shadow, including on narrow screens.
      const navigationShadow=await page.locator('[data-part="navigation"]')
        .evaluate(el=>getComputedStyle(el).boxShadow);
      appearanceScopeChecks.push({theme:entry.id,scheme,viewport,navigationShadow,...measured});
      writeFileSync(artifact('delegate-aside-appearance-scope.json'),JSON.stringify(appearanceScopeChecks,null,2));
      assert.equal(measured.shadow,'none','Navigation accent must not paint the worker status aside');
      assert.match(measured.background,/rgba\(0, 0, 0, 0\)|transparent/,'Status keeps its native unboxed surface');
      assert.equal(measured.radius,'0px');assert.deepEqual(measured.border,['0px','0px']);
      assert.ok(measured.width>0&&measured.textInset>=0);
      assert.equal(measured.labelMargin,'0px','Native inline captions must not inherit block-caption top spacing');
      if(!measured.wrapped)assert.ok(measured.sameLineCenterDelta<=3,'Unwrapped label and Details control align vertically');
      assert.ok(measured.scrollWidth<=measured.clientWidth+1,'Worker controls wrap without horizontal overflow');
      assert.ok(measured.children.every(child=>child.left>=-1&&child.right<=measured.width+1&&
        child.top>=-1&&child.bottom<=measured.height+1),'Native status children stay inside their surface');
      if(viewport==='small')assert.ok(measured.wrapped,'320px native status must exercise actual wrapping');
      assert.ok(measured.control.width>0&&measured.control.height>0);
      assert.notEqual(measured.control.outline,'none');assert.ok(measured.control.outlineWidth>0);
      assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
      assert.equal(await readComposerDraft(editor),probeDraft,'Each appearance preserves nonempty draft text');
      if(entry.id==='builtin:digital-rain')
        assert.match(navigationShadow,/inset/,'The actual navigation retains its inset accent');
      if(entry.id==='builtin:digital-rain'&&scheme==='dark'&&viewport!=='small')
        await page.screenshot({path:artifact(`delegate-aside-digital-rain-${viewport}.png`)});
    }
  }
  // Restore the exact composition, not a merge that leaves temporary keys.
  await present(priorPresentation);
  await action('theme.apply',{name:priorTheme.name,css:priorTheme.css});
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().theme)).toEqual(priorTheme);
  await expect.poll(()=>page.locator('#amp-one > style').evaluateAll((nodes,css)=>
    nodes.some(node=>node.textContent===css),priorTheme.css)).toBe(true);
  assert.deepEqual(await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition),priorPresentation);
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
  assert.equal(await readComposerDraft(editor),probeDraft,'Restoration preserves the nonempty draft');
  await setWorkerPhase('completed');
  await expect(status.getByRole('button',{name:'Stop worker',exact:true})).toHaveCount(0);
  // The Markdown editor commits deletion through its keyboard transaction.
  // fill('') can clear DOM text without updating the canonical local draft.
  await clearComposer(editor);
  if(draft)await editor.fill(draft);
  await expect(editor).toHaveDraft(draft);
}
async function checkHeader(page,region,label) {
  const heading=region.locator('header>span').first();
  await heading.scrollIntoViewIfNeeded();
  const measured=await heading.evaluate(element=>{
    const range=document.createRange();range.selectNodeContents(element);
    return {text:element.textContent,lines:[...new Set([...range.getClientRects()].map(r=>Math.round(r.top)))]};
  });
  assert.equal(measured.lines.length,1,label+': heading must not split mid-word');
  const copy=region.getByRole('button',{name:new RegExp('^Copy '+label.toLowerCase()+'$')});
  await expect(copy).toBeEnabled();await expect(copy).toBeInViewport();
}
async function checkLatest(page,work,label) {
  const jump=page.getByRole('button',{name:'Jump to latest messages',exact:true});
  const pane=page.locator('[data-part="messages"]');
  if(await pane.evaluate(el=>el.scrollHeight-el.clientHeight<=80)) {
    await expect(jump).toHaveCount(0);return;
  }
  if(!await jump.isVisible()) {
    await pane.hover();await page.mouse.wheel(0,-240);
    await expect.poll(()=>pane.evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop)).toBeGreaterThan(80);
  }
  await expect(jump).toBeVisible();
  await checkWorkLayout(page,work,label);
  await jump.focus();await expect(jump).toBeFocused();await jump.press('Enter');
  await expect.poll(()=>pane.evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop)).toBeLessThanOrEqual(2);
  await expect(jump).toHaveCount(0);
}
async function checkReadingAnchor(page,operation) {
  const button=operation.locator(':scope > button');
  await button.scrollIntoViewIfNeeded();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const pane=page.locator('[data-part="messages"]');
  const anchor=await pane.evaluate(el=>{
    const top=el.getBoundingClientRect().top;
    const row=[...el.querySelectorAll('[data-message-id]')].find(row=>row.getBoundingClientRect().bottom>top);
    return row?{id:row.dataset.messageId,offset:row.getBoundingClientRect().top-top}:null;
  });
  assert.ok(anchor,'Reading-position check needs an actual message anchor');
  const offset=()=>pane.evaluate((el,id)=>{
    const row=[...el.querySelectorAll('[data-message-id]')].find(row=>row.dataset.messageId===id);
    return row?row.getBoundingClientRect().top-el.getBoundingClientRect().top:null;
  },anchor.id);
  await button.click();
  await expect.poll(async()=>Math.abs((await offset())-anchor.offset)).toBeLessThanOrEqual(1);
  await button.click();
  await expect.poll(async()=>Math.abs((await offset())-anchor.offset)).toBeLessThanOrEqual(1);
  const viewport=page.viewportSize();
  await page.setViewportSize({...viewport,width:viewport.width+12});
  await expect.poll(async()=>Math.abs((await offset())-anchor.offset)).toBeLessThanOrEqual(1);
  await page.setViewportSize(viewport);
  await expect.poll(async()=>Math.abs((await offset())-anchor.offset)).toBeLessThanOrEqual(1);
}
async function enterStreamConversation(page,expectedSessionId,stage) {
  // Keep the shared Home-to-chat setup and desktop pinned preference intact.
  await showFixtureConversation(page);
  const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
  assert.equal(selected,expectedSessionId,'Fixture must retain its selected chat');
  const narrow=await page.evaluate(()=>matchMedia('(max-width: 900px)').matches);
  const navigation=page.locator('#workspace-navigation');
  let closed=false;
  if(narrow) {
    await expect(navigation).toHaveAttribute('role','dialog');
    if(await navigation.getAttribute('aria-modal')==='true') {
      await page.getByRole('button',{name:'Close navigation',exact:true}).click();
      closed=true;
    }
    await expect(navigation).toBeHidden();
    await expect(page.locator('#navigation-toggle')).toHaveAttribute('aria-expanded','false');
  }
  const pane=page.locator('[data-part="messages"]');
  await expect(pane).toBeVisible();
  await expect(pane).toHaveAttribute('data-session-id',selected);
  const after=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
  assert.equal(after,selected,'Closing navigation must not select another chat');
  navigationEntries.push({stage,viewport:page.viewportSize(),selectedBefore:selected,
    selectedAfter:after,narrow,closed,navigationHidden:await navigation.isHidden()});
  writeFileSync(artifact('delegate-work-stream-navigation-entry.json'),JSON.stringify(navigationEntries,null,2));
}
// Word ranges detect the one-letter-per-line regression that an element box
// inside the viewport cannot detect. Hit tests also reject floating overlays.
function measureOwner(element,probe) {
  if(probe) {
    // Preserve the existing recenter, then sample in that same browser task.
    // Two bounded animation frames expose movement; they do NOT prove settlement.
    if(probe.recenter)element.scrollIntoView({block:'center',inline:'nearest'});
    const samples=[],sample=phase=>samples.push({stage:probe.stage,phase,
      expectedRestoredStyle:probe.restoredStyle,...measureOwner(element)});
    sample('immediate');
    if(!probe.frames)return samples;
    return new Promise(resolve=>{
      let frame,done=false;
      const finish=error=>{
        if(done)return;done=true;clearTimeout(timer);cancelAnimationFrame(frame);
        if(error)samples.push({stage:probe.stage,diagnosticError:String(error).slice(0,500)});
        resolve(samples);
      };
      const timer=setTimeout(()=>finish('Animation-frame diagnostic deadline exceeded'),2000);
      frame=requestAnimationFrame(()=>{
        try {
          sample('animation-frame-1');
          frame=requestAnimationFrame(()=>{
            try{sample('animation-frame-2');finish()}catch(error){finish(error)}
          });
        }catch(error){finish(error)}
      });
    });
  }
  // Only geometry and bounded element identifiers from this synthetic fixture:
  // never page HTML, message bodies, storage, or app/private runtime state.
  const attribute=(node,key)=>node?.getAttribute(key)?.slice(0,256)??null;
  const identity=node=>node?{tag:node.tagName,id:attribute(node,'id'),class:attribute(node,'class'),
    part:attribute(node,'data-part'),role:attribute(node,'role'),
    operationId:attribute(node.closest('[data-node-id]'),'data-node-id'),
    workEntry:attribute(node.closest('[data-work-entry]'),'data-work-entry')}:null;
  const geometry=node=>{
    if(!node)return null;
    const css=getComputedStyle(node);
    return {identity:identity(node),rect:node.getBoundingClientRect().toJSON(),
      inlineStyle:node.getAttribute('style'),computed:{display:css.display,
        visibility:css.visibility,position:css.position,zIndex:css.zIndex,
        pointerEvents:css.pointerEvents,overflowX:css.overflowX,overflowY:css.overflowY,
        overflowAnchor:css.overflowAnchor,maxWidth:css.maxWidth,overflowWrap:css.overflowWrap}};
  };
  const button=element.closest('button'),bounds=button.getBoundingClientRect();
  const pane=element.closest('[data-part="messages"]'),paneRect=pane?.getBoundingClientRect();
  const paneClient=pane?{left:paneRect.left+pane.clientLeft,top:paneRect.top+pane.clientTop,
    right:paneRect.left+pane.clientLeft+pane.clientWidth,
    bottom:paneRect.top+pane.clientTop+pane.clientHeight}:null;
  const jump=element.closest('[data-part="conversation"]')?.querySelector('.a-chat-jump');
  const hitSamples=[];
  const words=[],problems=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
  for(let text=walker.nextNode();text;text=walker.nextNode()) {
    for(const match of text.textContent.matchAll(/[\p{L}\p{N}]+/gu)) {
      const range=document.createRange();
      range.setStart(text,match.index);range.setEnd(text,match.index+match[0].length);
      const rects=[...range.getClientRects()].filter(r=>r.width&&r.height);
      const lines=new Set(rects.map(r=>Math.round(r.top)));
      if(lines.size!==1)problems.push(`split word: ${match[0]}`);
      for(const rect of rects) {
        if(rect.left<Math.max(0,bounds.left)-1||rect.right>Math.min(innerWidth,bounds.right)+1||
           rect.top<Math.max(0,bounds.top)-1||rect.bottom>Math.min(innerHeight,bounds.bottom)+1)
          problems.push(`clipped word: ${match[0]}`);
        for(const fraction of [0.2,0.5,0.8]) {
          const x=rect.left+rect.width*fraction,y=rect.top+rect.height/2;
          const hit=document.elementFromPoint(x,y);
          if(!hit||!button.contains(hit))problems.push(`covered word: ${match[0]}`);
          hitSamples.push({word:match[0],wordOffset:match.index,fraction,x,y,
            hit:identity(hit),hitRect:hit?.getBoundingClientRect().toJSON()??null,
            nearestButton:identity(hit?.closest('button')),
            insideJumpControl:!!hit&&!!jump&&jump.contains(hit),
            insideButton:!!hit&&button.contains(hit),
            insidePane:paneClient?x>=paneClient.left&&x<=paneClient.right&&y>=paneClient.top&&y<=paneClient.bottom:null});
        }
      }
      words.push({text:match[0],lines:lines.size,rects:rects.map(r=>({x:r.x,y:r.y,width:r.width,height:r.height}))});
    }
  }
  if(!words.length)problems.push('No owner words measured');
  return {text:element.textContent,words,problems,hitSamples,at:performance.now(),
    connected:element.isConnected,viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY},
    owner:geometry(element),button:geometry(button),pane:pane?{...geometry(pane),
      clientRect:paneClient,scrollTop:pane.scrollTop,scrollLeft:pane.scrollLeft,
      scrollHeight:pane.scrollHeight,clientHeight:pane.clientHeight}:null,
    jumpContainer:geometry(jump),jumpControl:geometry(jump?.querySelector('button'))};
}
const fixture = spawn(process.env.AMPLIFIER_TEST_PYTHON || root + '.venv/bin/python', ['-u', root + 'tests/fixtures/browser_detail_server.py', root],
  {stdio: ['ignore', 'pipe', 'inherit']});
const stopFixture=ownFixture(fixture,{label:'Delegate work stream fixture'});
let browser,context,page,diagnosticOwner,restoredOwnerStyle,lastOwnerMeasurement,primaryFailure;
const ownerDiagnostics=[];
const saveOwnerDiagnostics=()=>writeFileSync(artifact('delegate-work-stream-owner-diagnostics.json'),
  JSON.stringify({scope:'synthetic fixture only; frame samples are not proof of settled layout',
    samples:ownerDiagnostics,lastPoll:lastOwnerMeasurement??null},null,2));
const recordOwnerDiagnostics=async(stage,{recenter=false,frames=false}={})=>{
  const samples=await diagnosticOwner.evaluate(measureOwner,
    {stage,recenter,frames,restoredStyle:restoredOwnerStyle});
  ownerDiagnostics.push(...samples);
  saveOwnerDiagnostics();
  const failed=samples.find(sample=>sample.diagnosticError);
  if(failed)throw Error(failed.diagnosticError);
};
try {
  const ready = await new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(Error('Fixture timeout')), 20000);
    fixture.once('error', error => {clearTimeout(timer); reject(error)});
    fixture.once('exit', code => {clearTimeout(timer); reject(Error(`Fixture exited: ${code}`))});
    fixture.stdout.on('data', chunk => {
      text += chunk;
      for (const line of text.split('\n')) {
        try {const value = JSON.parse(line); if (value.port) {clearTimeout(timer); resolve(value)}} catch {}
      }
    });
  });
  const url = `http://127.0.0.1:${ready.port}`;
  const headers = {Authorization: 'Bearer fixture-detail-token', 'content-type': 'application/json'};
  const control = async body => {
    const result = await fetch(url + '/api/fixture/control', {method: 'POST', headers, body: JSON.stringify(body)});
    assert.equal(result.status, 200, await result.clone().text());
    return result.json();
  };
  const {alpha} = await control({op: 'heavy', messages: 2, otherMessages: 2, chars: 50, nodes: 0});
  const start = 1791409080, turnId = 'stream-turn';
  const worker = {id: 'stream-worker', kind: 'worker', sessionId: 'review', parentId: 'stream-request',
    turnId, label: 'Review', phase: 'completed', startedAt: start + 1, endedAt: start + 36,
    summary: 'SUMMARY-DECOY must not replace actual messages'};
  const common = {turnId, rootSessionId: alpha, phase: 'completed'};
  const request = {...common, id: 'stream-request', sessionId: alpha, kind: 'tool', label: 'delegate',
    startedAt: start + 1, endedAt: start + 20,
    input: JSON.stringify({agent: 'review', instruction: 'STREAM REQUEST café'}),
    output: JSON.stringify({success: true, output: {response: 'RECEIPT-DECOY', session_id: 'review'}})};
  const read = {...common, id: 'stream-read', parentId: worker.id, sessionId: 'review', kind: 'tool',
    label: 'read_file', startedAt: start + 12, endedAt: start + 13,
    input: JSON.stringify({file_path: '/fixture/checklist.txt'}), output: 'CHILD READ RESULT'};
  const search = {...common, id: 'stream-search', parentId: worker.id, sessionId: 'review', kind: 'tool',
    label: 'grep', startedAt: start + 14, endedAt: start + 15,
    input: JSON.stringify({pattern: 'check', path: '/fixture'}), output: 'CHILD SEARCH RESULT'};
  const mainModel = {...common, id: 'stream-main-model', sessionId: alpha, kind: 'llm', label: 'Model call',
    provider: 'fixture', model: 'main-model', startedAt: start + 24, endedAt: start + 26};
  const followup = {...common, id: 'stream-followup', sessionId: alpha, kind: 'tool', label: 'live_worker',
    exchangeOwnerId: worker.id, startedAt: start + 28, endedAt: start + 29,
    input: JSON.stringify({action: 'message', session_id: 'review', text: 'STREAM FOLLOW-UP'}),
    output: JSON.stringify({accepted: true, completed: false})};
  const childModel = {...common, id: 'stream-child-model', parentId: worker.id, sessionId: 'review',
    kind: 'llm', label: 'Model call', provider: 'fixture', model: 'review-model',
    startedAt: start + 32, endedAt: start + 34};
  const report = (id, at, text) => ({...common, id, parentId: worker.id, sessionId: 'review',
    kind: 'exchange', startedAt: start + at, exchange: {id, delegateResponse: text, report: true}});
  // Scrambled storage order is intentional; presentation follows recorded order.
  const nodes = [request, worker, followup, mainModel, childModel,
    report('stream-reply-two', 36, 'STREAM SECOND REPLY'), search, read,
    report('stream-reply-one', 20, 'STREAM FIRST REPLY')];
  const execution={turns:[{id:turnId,anchorMessageId:'question',phase:'completed',
    startedAt:start,endedAt:start+40}],nodes};
  const setWorkerPhase=phase=>control({op:'patch',sessions:{[alpha]:{execution:{
    ...execution,nodes:nodes.map(node=>node.id===worker.id?{...node,phase}:node)
  }}}});
  await control({op: 'patch', sessions: {[alpha]: {
    messages: [{id: 'question', role: 'user', text: 'Review this checklist.', createdAt: start},
      {id: 'answer', role: 'assistant', text: 'Review finished.', createdAt: start + 40}],
    execution
  }}});
  browser = await chromium.launch({headless: true, args: ['--no-zygote', '--single-process', '--disable-gpu']});
  // Keep one explicit context: closing an implicitly owned context between
  // scenes terminates this ARM64 single-process Chromium. Tabs still have
  // independent sessionStorage, so replaced transcripts get fresh anchors.
  context=await browser.newContext({extraHTTPHeaders:headers});
  page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  observeApi(page,url);
  for (const [name, viewport] of viewports) {
    await page.setViewportSize(viewport);
    await page.goto(url);
    await enterStreamConversation(page,alpha,`stream-${name}`);
    await page.getByRole('textbox', {name: 'Message Amplifier'}).waitFor();
    const work = page.locator(`[data-group-id="${turnId}@question"]`);
    const toggle = work.locator(':scope > button');
    await expect(toggle).toHaveCount(1);
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await expect(work.locator('[data-node-id="stream-main-model"]')).toBeVisible();
    await checkAsideAppearanceScope(page,work,name,setWorkerPhase);
    if(appearanceScopeOnly){await cleanScene(page);continue}
    // No intervening Delegate or Worker activity drawer is needed to read it.
    await expect(work.getByRole('button', {name: /Worker activity/})).toHaveCount(0);
    await expect(work.locator('[data-kind="worker"] > button')).toHaveCount(0);
    const rows = [work.getByText('STREAM REQUEST café', {exact: true}),
      work.locator('[data-node-id="stream-read"]'), work.locator('[data-node-id="stream-search"]'),
      work.getByText('STREAM FIRST REPLY', {exact: true}), work.locator('[data-node-id="stream-main-model"]'),
      work.getByText('STREAM FOLLOW-UP', {exact: true}), work.locator('[data-node-id="stream-child-model"]'),
      work.getByText('STREAM SECOND REPLY', {exact: true})];
    for (const row of rows) {
      await expect(row).toHaveCount(1);
      await expect(row).toBeVisible();
    }
    // Compare one layout frame: chat scroll restoration can move viewport
    // coordinates between separate boundingBox requests without any overlap.
    const handles = await Promise.all(rows.map(row => row.elementHandle()));
    const boxes = await page.evaluate(elements => elements.map(element =>
      element?.isConnected ? element.getBoundingClientRect().toJSON() : null), handles);
    await Promise.all(handles.map(handle => handle?.dispose()));
    let bottom = -Infinity;
    for (const box of boxes) {
      assert.ok(box && box.y >= bottom - 1, 'Exchange and operations must be vertically ordered without overlap');
      bottom = box.y + box.height;
    }
    await expect(work).toContainText('Main chat → Delegate');
    await expect(work).toContainText('Delegate → Main chat');
    await expect(work).not.toContainText('SUMMARY-DECOY');
    await expect(work).not.toContainText('RECEIPT-DECOY');
    const operations = [rows[1], rows[2], rows[4], rows[6]];
    await expect(operations[2].locator(':scope > button')).toContainText('Main chat');
    for (const operation of [operations[0], operations[1], operations[3]])
      await expect(operation.locator(':scope > button')).toContainText('Delegate');
    for (const operation of operations)
      await expect(operation.locator(':scope > button')).toHaveAttribute('aria-expanded', 'false');
    await operations[0].locator(':scope > button').click();
    await expect(operations[0]).toContainText('CHILD READ RESULT');
    for (const operation of operations.slice(1))
      await expect(operation.locator(':scope > button')).toHaveAttribute('aria-expanded', 'false');
    await operations[0].locator(':scope > button').click();
    await toggle.click();
    await expect(rows[0]).toBeHidden();
    await toggle.click();
    await expect(rows[0]).toBeVisible();
    await expect(operations[0].locator(':scope > button')).toHaveAttribute('aria-expanded', 'false');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow');
    await checkReadingAnchor(page,operations[0]);
    await checkWorkLayout(page,work,`stream-${name}-opened`);
    const readability=[];
    for(const operation of operations) {
      const owner=operation.locator(':scope > button > .a-work-actor');
      await diagnosticOwner?.dispose();
      diagnosticOwner=await owner.elementHandle();
      restoredOwnerStyle=await owner.getAttribute('style');
      lastOwnerMeasurement=null;
      await owner.evaluate(element=>element.scrollIntoView({block:'center',inline:'nearest'}));
      // Wait for layout/scroll settlement without relying on a fixed sleep.
      await expect.poll(async()=> (await owner.evaluate(measureOwner)).problems).toEqual([]);
      readability.push(await owner.evaluate(measureOwner));
    }
    if(name==='mobile') {
      const owner=operations[0].locator(':scope > button > .a-work-actor');
      await diagnosticOwner?.dispose();
      diagnosticOwner=await owner.elementHandle();
      restoredOwnerStyle=await owner.getAttribute('style');
      lastOwnerMeasurement=null;
      await owner.evaluate(element=>element.scrollIntoView({block:'center',inline:'nearest'}));
      await expect.poll(async()=> (await owner.evaluate(measureOwner)).problems).toEqual([]);
      // Negative controls: the same oracle must reject both the old narrow
      // column symptom and text covered by another UI element.
      const style=await owner.getAttribute('style');
      restoredOwnerStyle=style;
      await owner.evaluate(element=>{element.style.maxWidth='8px';element.style.overflowWrap='anywhere'});
      const split=await owner.evaluate(measureOwner);
      assert.ok(split.problems.some(value=>value.startsWith('split word:')),'Readability check must detect split owner words');
      await owner.evaluate((element,style)=>style===null?element.removeAttribute('style'):element.setAttribute('style',style),style);
      await recordOwnerDiagnostics('width-restored-and-recentered',{recenter:true,frames:true});
      await expect.poll(async()=>{
        lastOwnerMeasurement=await owner.evaluate(measureOwner);
        return lastOwnerMeasurement.problems;
      }).toEqual([]);
      await owner.evaluate(element=>{
        const rect=element.getBoundingClientRect(),cover=document.createElement('div');
        cover.id='readability-negative-cover';
        Object.assign(cover.style,{position:'fixed',left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px',zIndex:'2147483647',background:'white'});
        document.body.append(cover);
      });
      const covered=await owner.evaluate(measureOwner);
      assert.ok(covered.problems.some(value=>value.startsWith('covered word:')),'Readability check must detect obscured owner text');
      await page.locator('#readability-negative-cover').evaluate(element=>element.remove());
      // Restore the inspected position after the artificial reflow/overlay.
      // The app may have applied its reading anchor while this control ran.
      await recordOwnerDiagnostics('cover-removed-and-recentered',{recenter:true,frames:true});
      await expect.poll(async()=>{
        lastOwnerMeasurement=await owner.evaluate(measureOwner);
        return lastOwnerMeasurement.problems;
      }).toEqual([]);
      saveOwnerDiagnostics();
      writeFileSync(artifact('delegate-work-stream-negative-controls.json'),JSON.stringify({split,covered},null,2));
      await page.screenshot({path:artifact('delegate-work-stream-mobile-owner.png')});
    }
    writeFileSync(artifact(`delegate-work-stream-${name}-readability.json`),JSON.stringify(readability,null,2));
    await work.scrollIntoViewIfNeeded();
    await checkWorkLayout(page,work,`stream-${name}-saved-position`);
    await page.screenshot({path:artifact(`delegate-work-stream-${name}.png`), fullPage: true});
    await checkLatest(page,work,`stream-${name}-latest`);
    await cleanScene(page);
  }
  if(!appearanceScopeOnly){
  // The separate live scenario drives actual metadata-only observer hooks and
  // UUID-stamped canonical records through the registered-browser UI. The
  // earlier scene alone is only presentation proof; this is not cold placement.
  const instruction='OBSERVER REQUEST café\n'+('request body 日本語\n'.repeat(1200))+'REQUEST-END';
  const reply='OBSERVER REPLY\n'+('reply body café\n'.repeat(1500))+'REPLY-END';
  const fieldRequests=[];
  // A new tab for new synthetic message identities; never clear app errors or
  // reuse another scene's saved reading anchor after replacing its transcript.
  await diagnosticOwner?.dispose();diagnosticOwner=null;await page.close();
  scene='observer';
  page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));observeApi(page,url);
  page.on('request',request=>{
    if(request.url().includes('/api/conversation/detail?'))fieldRequests.push({
      query:new URL(request.url()).searchParams,client:request.headers()['x-amplifier-client']});
  });
  // Install the request observer before first navigation and canonical reads.
  const observerSetup=await control({op:'delegate-observer',instruction,reply,olderRecords:true});
  writeFileSync(artifact('delegate-work-stream-capture.json'),JSON.stringify(observerSetup.delegateCapture,null,2));
  await page.setViewportSize(mobileOnly?mobile:desktop);
  await page.goto(url);
  await enterStreamConversation(page,alpha,'observer');
  const observed=page.locator('[data-group-id="observer-turn@observer-question"]');
  const observedToggle=observed.locator(':scope > button');
  await expect(observedToggle).toBeVisible();
  if(await observedToggle.getAttribute('aria-expanded')!=='true')await observedToggle.click();
  // This fixture deliberately places the stamped exchange beyond the recent
  // window. Follow its explicit bounded lookup before copying full fields.
  const earlier=observed.getByRole('button',{name:'Search older saved details',exact:true});
  await expect(earlier).toHaveCount(1);
  await earlier.click();
  await expect(observed.getByRole('region',{name:'Request',exact:true})).toContainText('OBSERVER REQUEST café');
  await expect(observed.getByRole('region',{name:'Reply',exact:true})).toContainText('OBSERVER REPLY');
  await diagnostics.collect();
  const continued=lookupEvents.find(row=>row.query.part==='tool'&&row.query.before);
  assert.ok(continued,'Ordinary older click must send a bounded continuation');
  assert.ok(lookupEvents.some(row=>row.sequence<continued.sequence&&
    row.result?.lookupKey===continued.query.lookupKey&&row.result?.before===continued.query.before),
    'Older lookup must use the exact accepted lookupKey and before pair');
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
    writeText:async text=>{window.copied=text}
  }}));
  await observed.getByRole('button',{name:'Copy request',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.copied)).toBe(instruction);
  await observed.getByRole('button',{name:'Copy reply',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.copied)).toBe(reply);
  assert.ok(fieldRequests.some(item=>item.query.get('part')==='tool'&&item.client),'Real browser must follow saved-tool lookup');
  assert.ok(fieldRequests.some(item=>item.query.get('field')==='delegateResponse'&&Number(item.query.get('offset'))>0),'Long reply must use more than one field chunk');
  const observerInspection=await control({op:'inspect-work-source'});
  const source=observerInspection.sessions.find(row=>row.id===alpha);
  assert.ok(source.execution.nodes.every(row=>!Object.hasOwn(row,'input')&&!Object.hasOwn(row,'output')),
    'UI inspection must not inject canonical bodies into live observer metadata');
  await cleanScene(page);
  await page.close();
  scene='nested';
  // The original V3 requires real actor labels, not a Main chat guess when
  // only a nested recipient is loaded. These are synthetic projection inputs,
  // independent of the actual observer/copy case above.
  const orphanRequest={...common,id:'orphan-request',kind:'tool',label:'delegate',
    sessionId:'outside-loaded-parent',startedAt:start+2,endedAt:start+7,
    input:JSON.stringify({instruction:'NESTED REQUEST'}),output:JSON.stringify({success:true,output:{response:'NESTED REPLY'}})};
  const orphanWorker={...worker,id:'orphan-worker',sessionId:'inner-review',parentId:orphanRequest.id,
    label:'Inner review',startedAt:start+2,endedAt:start+7};
  const orphanOperation={...read,id:'orphan-read',sessionId:orphanWorker.sessionId,parentId:orphanWorker.id,
    startedAt:start+3,endedAt:start+4};
  await control({op:'patch',sessions:{[alpha]:{
    messages:[{id:'question',role:'user',text:'Inspect a nested delegate.',createdAt:start}],
    execution:{turns:[{id:turnId,anchorMessageId:'question',phase:'completed',startedAt:start,endedAt:start+8}],
      nodes:[orphanRequest,orphanWorker,orphanOperation]}
  }}});
  // Negative evidence for the former banner: this fixture transition removes
  // observer-question under the same synthetic session. Probe that exact guard,
  // separately from the clean browser scene; do not waive any browser failure.
  const removed=await fetch(url+`/api/conversation/navigation?sessionId=${alpha}&messageId=observer-question&window=true`,{headers});
  const removedBody=await removed.json();
  transitionChecks.push({from:'observer',to:'nested',removedMessageId:'observer-question',
    request:removed.url,status:removed.status,body:removedBody});
  writeFileSync(artifact('delegate-work-stream-transitions.json'),JSON.stringify(transitionChecks,null,2));
  assert.equal(removed.status,400);
  assert.match(removedBody.error,/This message is no longer available/);
  page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));observeApi(page,url);
  for(const [name,viewport] of viewports) {
    await page.setViewportSize(viewport);
    await page.goto(url);await enterStreamConversation(page,alpha,`nested-${name}`);
    const work=page.locator(`[data-group-id="${turnId}@question"]`),toggle=work.locator(':scope > button');
    await expect(toggle).toBeVisible();
    if(await toggle.getAttribute('aria-expanded')!=='true')await toggle.click();
    const request=work.getByRole('region',{name:'Request',exact:true});
    const reply=work.getByRole('region',{name:'Reply',exact:true});
    await expect(request).toContainText('NESTED REQUEST');
    await expect(reply).toContainText('NESTED REPLY');
    await request.scrollIntoViewIfNeeded();
    await page.screenshot({path:artifact(`delegate-work-stream-attribution-${name}.png`)});
    await expect(request.locator('header')).toContainText('Worker (owner unavailable) → Delegate');
    await expect(reply.locator('header')).toContainText('Delegate → Worker (owner unavailable)');
    await expect(request.locator('header')).not.toContainText('Main chat');
    await expect(reply.locator('header')).not.toContainText('Main chat');
    const operation=work.locator('[data-node-id="orphan-read"]');
    await expect(operation.locator(':scope > button')).toHaveAttribute('aria-expanded','false');
    await expect(operation.locator('.a-work-actor')).toContainText('Delegate · Inner review');
    await checkHeader(page,request,'Request');
    await checkHeader(page,reply,'Reply');
    await request.scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Nested attribution must not overflow');
    await checkWorkLayout(page,work,`nested-${name}-saved-position`);
    await page.screenshot({path:artifact(`delegate-work-stream-attribution-${name}.png`)});
    await checkLatest(page,work,`nested-${name}-latest`);
    await toggle.click();
    await expect(request).toBeHidden();await expect(reply).toBeHidden();
    await cleanScene(page);
  }
  }
  assert.deepEqual(errors, []);
  const inspection = await control({op: 'inspect-work-source'});
  assert.equal(inspection.runtimeStarts, 0);
  assert.equal(inspection.runtimeSends, 0);
  writeFileSync(artifact('delegate-aside-preservation.json'),JSON.stringify({
    runtimeStarts:inspection.runtimeStarts,runtimeSends:inspection.runtimeSends,
    viewports:viewports.map(([name,size])=>({name,...size})),
    appearanceRows:appearanceScopeChecks.length,
    restoredComposition:true,restoredThemeAndMountedCss:true,restoredSelection:true,
    nonemptyDraftPreserved:true,originalDraftRestored:true
  },null,2));
} catch(error) {
  primaryFailure={error};
  await diagnostics.attempt('failure owner capture',async()=>{
    // Do not recenter, hide overlays, or change styles to obtain failure evidence.
    if(page&&!page.isClosed()&&diagnosticOwner)await recordOwnerDiagnostics('failure');
  });
  await diagnostics.attempt('failure screenshot',async()=>{
    if(page&&!page.isClosed())await page.screenshot({path:artifact('delegate-work-stream-failure.png')});
  });
} finally {
  await diagnostics.finish(primaryFailure,{
    cleanup:[['fixture lifecycle',()=>finishFixture({label:'Delegate work stream fixture',
      close:[['Delegate work stream browser',()=>browser?.close()]],stop:stopFixture})]],
    writes:[
      ['lookup report',saveLookups],
      ['API failure report',()=>writeFileSync(artifact('delegate-work-stream-api-failures.json'),JSON.stringify(apiFailures,null,2))],
      ['owner report',saveOwnerDiagnostics]
    ],
    report:value=>writeFileSync(artifact('delegate-work-stream-diagnostic-errors.json'),JSON.stringify(value,null,2))
  });
}
console.log(appearanceScopeOnly?
  'PASS: real worker-status aside paint across bundled themes, both modes and three widths; NOT full delegate, provider or pixel-review acceptance.':
  'PASS: synthetic interleaving/layout/actor attribution plus registered-browser stamped-observer long exchange reads and aside appearance isolation; NOT cold-turn association or real-provider acceptance.');
