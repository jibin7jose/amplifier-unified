// ROOT-only execution: real receiving UI, NoModel fixture, no paid provider.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
// ROOT may set this only with the installed candidate interpreter. The fixture
// refuses a checkout import and this path never serves Vite source as a wheel.
const installedStatic=process.env.AMPLIFIER_TEST_INSTALLED_STATIC==='1';
const builtStatic=process.env.AMPLIFIER_TEST_BUILT_STATIC==='1',staticClient=installedStatic||builtStatic;
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[
 fileURLToPath(new URL('../../tests/fixtures/peer_attribution_ui_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))
],{stdio:['ignore','pipe','inherit']});
let browser,vite,page;
const retainBoundary=facts=>{
 const evidence=Object.fromEntries(['starts','sends','nativeSubmits','probeAttempts','expectedBoundary','boundaryCalls','notificationReceipt'].map(key=>[key,facts[key]]));
 console.log('PEER_BOUNDARY_EVIDENCE '+JSON.stringify(evidence));
 if(process.env.AMPLIFIER_PROOF_DIR){mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});writeFileSync(join(process.env.AMPLIFIER_PROOF_DIR,'peer-boundary.json'),JSON.stringify(evidence,null,2))}
};
const readFacts=async()=>{const response=await page.request.get(new URL('/api/fixture/peerFacts',page.url()).href);assert.ok(response.ok());return response.json()};
const assertBoundary=facts=>{
 assert.equal(facts.starts,0);assert.equal(facts.sends,0);assert.equal(facts.nativeSubmits,0);assert.deepEqual(facts.probeAttempts,[]);
 const expected=facts.expectedBoundary,receipt=facts.notificationReceipt;
 assert.ok(facts.boundaryCalls.length>0,'The expected watch boundary must actually be attempted');
 assert.equal(expected.recipientId,'receiving');assert.equal(expected.generationId,'fixture-observer-generation');assert.equal(expected.watchId,'visible-watch');
 assert.equal(receipt.inputId,expected.inputId);assert.equal(receipt.requestId,expected.inputId);
 assert.equal(receipt.origin,'system');assert.equal(receipt.mode,'auto');assert.equal(receipt.delivery,'queued');
 assert.equal(receipt.dependencyRequestId,expected.watchId);assert.equal(receipt.dependencyOutcomeRevision,1);assert.equal(receipt.dependencySatisfied,false);
 const waiting={accepted:false,effect:'none',waitingForBoundary:true,reason:'Waiting for a supported recipient boundary.'};
 assert.deepEqual(receipt.admission,waiting);
 for(const call of facts.boundaryCalls){
  assert.equal(call.method,'collaboration_input');assert.equal(call.fatal,undefined);
  assert.equal(call.sessionId,expected.recipientId);assert.equal(call.arguments.inputId,expected.inputId);assert.equal(call.arguments.automaticDelivery,true);
  assert.deepEqual(call.authorizations,[{guardReason:null,admitted:true,currentGenerationId:expected.generationId},{guardReason:null,admitted:true,currentGenerationId:expected.generationId}]);
  assert.equal(call.receipt.delivery,'submitting');assert.equal(call.receipt.inputId,expected.inputId);
  assert.equal(call.receipt.origin,'system');assert.equal(call.receipt.mode,'auto');assert.equal(call.receipt.dependencyRequestId,expected.watchId);
  assert.deepEqual(call.result,waiting);
 }
};
try{
 const target=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Peer fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Peer fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 if(!staticClient){
  const {createServer}=await import('vite');
  vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
   server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},
   optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
  await vite.listen();
 }
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-attribution'}});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 page=await context.newPage();const errors=[],detailReads=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/conversation/detail'&&url.searchParams.get('field')==='text')detailReads.push(request.url())});
 await page.goto(staticClient?target:vite.resolvedUrls.local[0]);await showFixtureConversation(page);await page.waitForSelector('#amp-one');
 // Wait for the host to retain a positive no-effect receipt, not merely for a
 // frame or a counter. Unknown/accepted/unexpected calls fail instead of retrying.
 await expect.poll(async()=>{
  const observed=await readFacts();
  assert.equal(observed.starts,0);assert.equal(observed.sends,0);assert.equal(observed.nativeSubmits,0);assert.deepEqual(observed.probeAttempts,[]);
  assert.ok(observed.boundaryCalls.every(call=>!call.fatal),JSON.stringify(observed.boundaryCalls));
  assert.ok(['queued','submitting'].includes(observed.notificationReceipt.delivery),JSON.stringify(observed.notificationReceipt));
  return observed.notificationReceipt.delivery==='queued'&&observed.notificationReceipt.admission?.waitingForBoundary===true;
 }).toBe(true);
 const facts=await readFacts();retainBoundary(facts);assertBoundary(facts);
 assert.equal(facts.installedStatic,installedStatic);
 const article=id=>page.locator(`article[data-message-id="${id}"]`);
 const caption='Sent by Amplifier from another chat';
 await expect(article(facts.agent)).toHaveAttribute('aria-label',caption);
 await expect(article(facts.agent).locator('.a-message-attribution')).toHaveText(caption);
 await expect(article(facts.human)).toHaveAttribute('aria-label','From another chat');
 await expect(article(facts.notification)).toHaveAttribute('aria-label','Amplifier update');
 await expect(article(facts.notification).locator('.a-message-attribution')).toHaveText('Amplifier update');
 await expect(article(facts.quote)).toHaveAttribute('aria-label','Your message');
 await expect(article(facts.quote).locator('.a-message-attribution')).toHaveCount(0);
 await expect(article(facts.forged)).toHaveAttribute('aria-label','Message');
 await expect(article(facts.forged).locator('.a-message-attribution')).toHaveCount(0);
 for(const id of [facts.agent,facts.human,facts.forged,facts.notification]){
  await expect(article(id)).not.toHaveClass(/a-user/);
  await expect(article(id).getByRole('button',{name:'Edit message',exact:true})).toHaveCount(0);
 }

 // Incoming peers are compact, exact payloads are rendered only on expansion.
 const peer=article(facts.agent),summary=peer.locator('.a-peer-summary');
 const group=page.locator('.a-peer-run').filter({has:peer}),groupSummary=group.locator('.a-peer-run-summary');
 await expect(group).toHaveCount(1);
 await expect(groupSummary).toHaveAttribute('aria-expanded','false');
 await expect(summary).toBeHidden();
 await groupSummary.press('Enter');
 await expect(groupSummary).toBeFocused();
 await expect(groupSummary).toHaveAttribute('aria-expanded','true');
 await expect(summary).toBeVisible();
 await expect(summary).toHaveAttribute('aria-expanded','false');
 await expect(peer.locator('.a-peer-content')).toHaveCount(0);
 assert.equal(detailReads.length,0,'Collapsed peer must not fetch its full payload');
 await summary.press('Enter');await expect(summary).toHaveAttribute('aria-expanded','true');
 await expect(summary).toBeFocused();
 await expect(peer.locator('.a-delegate-markdown')).toContainText('Exact α 🐈');
 assert.equal(detailReads.length,0,'Expanding a preview does not eagerly fetch long text');
 await peer.getByRole('button',{name:'Show full text',exact:true}).click();
 await expect(peer.getByRole('button',{name:'Show full text',exact:true})).toHaveCount(0);
 assert.equal(detailReads.length,1);
 await peer.getByRole('button',{name:'Copy peer message as Markdown'}).click();
 await expect(peer.getByText('Copied Markdown',{exact:true})).toBeVisible();
 const clipboard=await page.evaluate(()=>navigator.clipboard.readText());
 assert.equal(Buffer.byteLength(clipboard),facts.peerTextLength);
 assert.equal(createHash('sha256').update(clipboard).digest('hex'),facts.peerTextDigest);
 await summary.press('Enter');await expect(peer.locator('.a-peer-content')).toHaveCount(0);
 await expect(article(facts.quote).locator('.a-peer-summary')).toHaveCount(0);
 // The real agent action uses a distinct non-removable attribution. A user's
 // matching emoji remains removable without clearing the assistant's reaction.
 const react=present=>page.request.post(new URL('/api/fixture/reaction',page.url()).href,{data:{present},headers:{Origin:target}});
 assert.ok((await react(true)).ok());
 const badge=article(facts.quote).getByLabel('Amplifier reacted: Thumbs up',{exact:true});
 await expect(badge).toBeVisible();assert.equal(await badge.evaluate(node=>node.tagName),'SPAN');
 if(process.env.AMPLIFIER_PROOF_DIR){mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,'peer-reactions-mobile.png')});await page.setViewportSize({width:1280,height:900});await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,'peer-reactions-desktop.png')});await page.setViewportSize({width:390,height:844})}
 await article(facts.quote).getByRole('button',{name:'React to message',exact:true}).click();
 await article(facts.quote).getByRole('button',{name:'Thumbs up',exact:true}).click();
 await article(facts.quote).getByRole('button',{name:'Remove Thumbs up reaction',exact:true}).click();
 await expect(badge).toBeVisible();
 await page.reload();await expect(badge).toBeVisible();
 await expect(summary).toHaveAttribute('aria-expanded','false');
 await expect(groupSummary).toHaveAttribute('aria-expanded','false');
 await expect(summary).toBeHidden();
 await groupSummary.press('Space');
 await expect(groupSummary).toBeFocused();
 await expect(groupSummary).toHaveAttribute('aria-expanded','true');
 await expect(summary).toBeVisible();
 assert.ok((await react(false)).ok());await expect(badge).toHaveCount(0);
 await expect(article(facts.quote)).toHaveClass(/a-user/);
 await expect(article(facts.quote).getByRole('button',{name:'Edit message',exact:true})).toHaveCount(1);
 const preserved=await page.evaluate(()=>{
  const state=window.amplifier.getState();
  return {selectedSessionId:state.selectedSessionId,draft:state.view.draft};
 });
 assert.deepEqual(preserved,{selectedSessionId:'receiving',draft:'Untouched draft'});
 const presentation=scheme=>page.evaluate(async scheme=>{
  const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{
   clientId,expectedRevision:shell.revision,
   composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme}}
  });
  if(!prepared.accepted||!prepared.result?.id)throw Error('Theme preparation failed');
  const applied=await window.amplifier.dispatch('shell.changes.apply',{
   clientId,expectedRevision:shell.revision,changeId:prepared.result.id
  });
  if(!applied.accepted)throw Error('Theme commit failed');
 },scheme);
 for(const width of [320,390])for(const scheme of ['light','dark'])for(const zoom of [1,2]){
  await page.setViewportSize({width,height:844});
  await presentation(scheme);
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme)).toBe(scheme);
  await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom)},zoom);
  await article(facts.agent).scrollIntoViewIfNeeded();await page.mouse.move(0,0);
  const node=article(facts.agent).locator('.a-message-attribution');await expect(node).toBeVisible();
  const geometry=await node.evaluate(element=>{
   const style=getComputedStyle(element),article=element.closest('article'),box=element.getBoundingClientRect();
   const rgb=value=>value.match(/[\d.]+/g).slice(0,3).map(Number);
   const luminance=values=>values.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   let ancestor=element,bg='rgba(0, 0, 0, 0)';
   while(ancestor){bg=getComputedStyle(ancestor).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;ancestor=ancestor.parentElement}
   const fgL=luminance(rgb(style.color)),bgL=luminance(rgb(bg));
   return {overflow:element.scrollWidth>element.clientWidth,scrollWidth:element.scrollWidth,clientWidth:element.clientWidth,visible:style.visibility,opacity:style.opacity,
    contrast:(Math.max(fgL,bgL)+.05)/(Math.min(fgL,bgL)+.05),width:box.width,
    actionsContain:!!element.closest('.a-message-actions'),tabIndex:element.tabIndex,
    articleOverflow:article.scrollWidth>article.clientWidth};
  });
  assert.equal(geometry.overflow,false,JSON.stringify({width,scheme,zoom,...geometry}));assert.equal(geometry.articleOverflow,false,JSON.stringify({width,scheme,zoom,...geometry}));
  assert.equal(geometry.actionsContain,false);assert.equal(geometry.visible,'visible');assert.equal(geometry.opacity,'1');
  assert.ok(geometry.contrast>=4.5,JSON.stringify({width,scheme,zoom,...geometry}));assert.equal(geometry.tabIndex,-1);
  assert.deepEqual(await page.evaluate(()=>{const state=window.amplifier.getState();return {
   selectedSessionId:state.selectedSessionId,draft:state.view.draft
  }}),preserved);
 }
 await page.evaluate(()=>{document.documentElement.style.zoom='1'});
 await page.reload();await expect(article(facts.agent).locator('.a-message-attribution')).toHaveText(caption);
 await expect(article(facts.notification).locator('.a-message-attribution')).toHaveText('Amplifier update');
 assert.ok((await page.request.post(new URL('/api/fixture/extendHistory',page.url()).href,{headers:{Origin:target}})).ok());
 await page.reload();
 await expect(summary).toHaveAttribute('aria-expanded','false');
 const state=await page.evaluate(()=>window.amplifier.getState());
 const receiving=state.sessions.find(row=>row.id===state.selectedSessionId);
 assert.equal(receiving.messageWindow.hasEarlier,true);
 const headers={'X-Amplifier-Client':state.client.id};
 const older=await (await page.request.get(new URL(`/api/conversation/detail?sessionId=receiving&part=messages&before=${encodeURIComponent(receiving.messageWindow.before)}`,page.url()).href,{headers})).json();
 assert.ok(Array.isArray(older.items),JSON.stringify({window:receiving.messageWindow,response:older}));
 assert.ok(older.items.some(row=>row.id===facts.older));
 assert.equal(older.items.find(row=>row.id===facts.older).attribution,undefined,'A partial page cannot prove global input uniqueness');
 // The focused rail is another receiving boundary, not a compact-text lookup.
 const focused=await (await page.request.get(new URL(`/api/conversation/navigation?sessionId=receiving&messageId=${encodeURIComponent(facts.agent)}&window=true`,page.url()).href,{headers})).json();
 assert.equal(focused.messages.find(row=>row.id===facts.agent).attribution?.caption,caption);
 await page.evaluate(id=>window.amplifier.dispatch('message.reveal',{sessionId:'receiving',messageId:id}),facts.agent);
 await expect(summary).toHaveAttribute('aria-expanded','true');
 assert.deepEqual(await page.evaluate(()=>{const state=window.amplifier.getState();return {
  selectedSessionId:state.selectedSessionId,draft:state.view.draft
 }}),preserved);
 const finalFacts=await readFacts();retainBoundary(finalFacts);assertBoundary(finalFacts);assert.deepEqual(errors,[]);
 console.log(`${installedStatic?'Installed-static':builtStatic?'Built-source':'Vite-source'} peer collapse, actor-owned reactions, and peer attribution receiving fixture: visible caption, actor distinction, forgeries, reload, older/focused pages, committed 320/390px light/dark 200% zoom, draft/selection preserved; zero model/provider starts; ${finalFacts.queryWorkers} isolated shell query helpers.`);
}catch(error){
 if(page)try{retainBoundary(await readFacts())}catch(diagnosticError){console.error('Peer boundary evidence unavailable:',diagnosticError.message)}
 throw error;
}finally{await browser?.close();await vite?.close();fixture.kill()}
