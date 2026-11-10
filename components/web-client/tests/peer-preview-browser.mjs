// Real bundled browser + host fixture. No real account/history/model access.
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const installed=process.env.AMPLIFIER_TEST_INSTALLED_STATIC==='1';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/peer_preview_ui_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','inherit'],env:{...process.env,PEER_PREVIEW_DUPLICATE_TITLES:'1'}});
let browser,page;const results=[];
try{
 const target=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-preview'}});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 page=await context.newPage();const errors=[],commands=[],actions=[];
 const observe=client=>{
  client.on('pageerror',e=>errors.push(e.message));
  client.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions'){try{const action=r.postDataJSON();commands.push(action.action);actions.push(action)}catch{}}});
 };
 observe(page);
 await page.goto(target);await showFixtureConversation(page,{navPinned:false});
 const facts=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
 assert.equal(facts.installed,installed);
 assert.notEqual(facts.sourceWorkspace,facts.recipientWorkspace);
 assert.equal(facts.sourceWorkspaceRegistered,true);assert.equal(facts.independentRoots,true);
 assert.equal(facts.sourceWorkspaceLabel,'readiness-review');
 assert.equal(facts.sourceBTitle,'Navigation design');
 assert.notEqual(facts.sourceA,facts.sourceB);
 assert.equal(facts.sourceBWorkspace,facts.recipientWorkspace);
 assert.notEqual(facts.sourceWorkspaceLabel,facts.sourceBWorkspaceLabel);
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toHaveDraft('Unsent synthetic draft α');
 await expect(composer).toHaveText('Unsent synthetic draft α');
 // Request-linked reads can reach the exact foreign counterpart without
 // replacing the workspace list with a global discovery query.
 async function inspectRequest(client,caption,absentPeer){
  const before=await client.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft}});
  await client.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
  await client.getByRole('button',{name:'Tasks and workers',exact:true}).click();
  const related=client.locator('.a-coordination-related');
  await related.getByText('Exact requests, messages and saved results',{exact:true}).click();
  const request=related.locator(`[data-request-id="${facts.requests[0]}"]`);
  await expect(request.locator(':scope > small')).toContainText(caption);
  await expect(related.locator(`[data-workspace-chat-id="${absentPeer}"]`)).toHaveCount(0);
  await request.getByRole('button',{name:'Open submitted message',exact:true}).click();
  await expect(related.getByRole('article',{name:'Original message'})).toContainText('Navigation review');
  assert.deepEqual(await client.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft}}),before);
  await client.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
  await expect(related).toHaveCount(0);
 }
 await inspectRequest(page,'Received from Navigation design · readiness-review',facts.sourceA);
 const otherContext=await browser.newContext({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-preview'}});
 const otherPage=await otherContext.newPage();observe(otherPage);
 await otherPage.goto(target);await showFixtureConversation(otherPage,{navPinned:false});
 await otherPage.evaluate(id=>window.amplifier.dispatch('session.select',{id}),facts.sourceA);
 await expect.poll(()=>otherPage.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(facts.sourceA);
 const otherComposer=otherPage.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const otherDraft='Private readiness draft β — never sent';
 await otherComposer.fill(otherDraft);await expect(otherComposer).toHaveDraft(otherDraft);
 await inspectRequest(otherPage,`Sent to Release planning · ${facts.recipientWorkspace.split('/').at(-1)}`,facts.receiving);
 const linkedReads=actions.filter(row=>row.action==='coordination.read');
 assert.equal(linkedReads.length,2);
 assert.ok(linkedReads.every(row=>row.args.scope==='all'&&row.args.sessionId===facts.receiving&&row.args.messageId===facts.originals[0]));
 const discovery=actions.filter(row=>row.action==='coordination.list');
 assert.ok(discovery.length>=2);
 assert.ok(discovery.every(row=>row.args.scope!=='all'&&[facts.sourceWorkspace,facts.recipientWorkspace].includes(row.args.workspace)));
 await otherPage.evaluate(async()=>{
  const s=window.amplifier.getState(),result=await window.amplifier.dispatch('attachment.add',{sessionId:s.selectedSessionId,name:'private-readiness-draft.txt',base64:btoa('Other browser attachment, never sent.')});
  if(!result.accepted)throw Error('Other browser attachment was not accepted');
  window.peerDocument=crypto.randomUUID();
 });
 const draftState=client=>client.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerDocument}});
 const otherBefore=await draftState(otherPage);assert.equal(otherBefore.attachments.length,1);
 const assertOtherDraft=async()=>{
  assert.deepEqual(await draftState(otherPage),otherBefore);
  await expect(otherComposer).toHaveDraft(otherDraft);await expect(otherComposer).toHaveText(otherDraft);
 };
 await page.bringToFront();
 await expect(composer).toHaveDraft('Unsent synthetic draft α');
 const change=async kind=>{const response=await page.request.post(new URL('/api/fixture/change',target).href,{data:{kind},headers:{Origin:target}});assert.equal(response.ok(),true,await response.text())};
 const article=id=>page.locator(`[data-message-id="${id}"]`),panel=()=>page.getByRole('region',{name:'Read-only source chat preview'}).or(page.getByRole('dialog',{name:'Read-only source chat preview'}));
 const run=page.locator('.a-peer-run'),summary=run.locator('.a-peer-run-summary');
 await expect(summary).toContainText('3 messages');await expect(summary).toHaveAttribute('aria-expanded','false');
 await expect(run.locator(':scope > .a-peer-sources').first()).toContainText('Navigation design — readiness-review');
 await expect(run.locator(':scope > .a-peer-sources').first()).toContainText(`Navigation design — ${facts.sourceBWorkspaceLabel}`);
 await summary.press('Enter');
 assert.deepEqual(await run.locator('article[data-message-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId)),facts.originals);
 await expect(run.getByRole('button',{name:'Preview source chat: Navigation design',exact:true})).toHaveCount(3);
 const original=article(facts.originals[0]);await original.locator('.a-peer-summary').press('Enter');
 await expect(original.locator('.a-peer-workspace')).toHaveText('readiness-review');
 assert.deepEqual(await original.evaluate(n=>{
  const s=window.amplifier.getState(),peer=s.sessions.find(row=>row.id===s.selectedSessionId).messages.find(row=>row.id===n.dataset.messageId).attribution.peer;
  return {sourceId:peer.sourceId,title:peer.title,location:peer.location,workspaceLabel:peer.workspaceLabel};
 }),{sourceId:facts.sourceA,title:'Navigation design',location:{kind:'workspace',path:facts.sourceWorkspace},workspaceLabel:facts.sourceWorkspaceLabel});
 const otherOriginal=article(facts.originals[1]);
 await expect(otherOriginal.locator('.a-peer-workspace')).toHaveText(facts.sourceBWorkspaceLabel);
 assert.deepEqual(await otherOriginal.evaluate(n=>{
  const s=window.amplifier.getState(),peer=s.sessions.find(row=>row.id===s.selectedSessionId).messages.find(row=>row.id===n.dataset.messageId).attribution.peer;
  return {sourceId:peer.sourceId,title:peer.title,location:peer.location,workspaceLabel:peer.workspaceLabel};
 }),{sourceId:facts.sourceB,title:'Navigation design',location:{kind:'workspace',path:facts.sourceBWorkspace},workspaceLabel:facts.sourceBWorkspaceLabel});
 async function formatted(root){
  await expect(root.locator('h1')).toHaveText('Navigation review');await expect(root.locator('h2').first()).toHaveText('Ordered checks');
  await expect(root.locator('ol>li')).toHaveCount(2);await expect(root.locator('ul>li')).toHaveCount(2);
  await expect(root.locator('strong')).toContainText('strong');await expect(root.locator('em')).toContainText('emphasis');
  await expect(root.locator('blockquote')).toContainText('A peer report is not a human approval.');await expect(root.locator('table')).toHaveCount(1);
  assert.equal(await root.locator('pre code.language-python').textContent(),'def sample():\n    return "café α"\n\nprint(sample())\n');
  const paragraph=root.locator('p').filter({hasText:'First paragraph with'});
  assert.match(await paragraph.textContent(),/inline_code\.\nThis is a real soft line break\./);
  assert.equal(await paragraph.evaluate(n=>getComputedStyle(n).whiteSpace),'pre-wrap');
  await expect(root.locator('p').filter({hasText:'Literal backslash-n'})).toContainText('\\n, not a newline.');
  assert.equal(await root.locator('script,img[onerror],a[href^="javascript:"]').count(),0);
  assert.equal(await page.evaluate(()=>globalThis.peerXss),undefined);
 }
 await formatted(original.locator('.a-markdown'));
 // Discriminator: deliberately flatten DOM text; semantic line-break assertion must fail.
 const flattened=await original.locator('pre code').textContent();assert.notEqual(flattened.replace(/\s+/g,' '),flattened);
 if(process.env.PEER_NEGATIVE_FLATTEN==='1'){
  await original.locator('.a-markdown').evaluate(n=>{
   const walker=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);let text;
   while(text=walker.nextNode())text.nodeValue=text.nodeValue.replace(/\s+/g,' ');
  });
  let detected;try{await formatted(original.locator('.a-markdown'))}catch(e){detected=e}
  assert.ok(detected,'Negative control incorrectly passed');
  throw Error('PEER_NEGATIVE_CONTROL_CONFIRMED: '+detected.message);
 }
 await original.getByRole('button',{name:'Copy peer message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.text);
 assert.equal(createHash('sha256').update(await page.evaluate(()=>navigator.clipboard.readText())).digest('hex'),facts.textSha256);
 const receivedLong=article(facts.originals[2]);await receivedLong.locator('.a-peer-summary').click();
 await receivedLong.getByRole('button',{name:'Show full text',exact:true}).click();
 await expect(receivedLong.locator('h2').last()).toHaveText('Beyond compact boundary');
 await receivedLong.getByRole('button',{name:'Copy peer message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.long);
 await receivedLong.locator('.a-peer-summary').click();
 await page.evaluate(async()=>{
  const s=window.amplifier.getState(),result=await window.amplifier.dispatch('attachment.add',{
   sessionId:s.selectedSessionId,name:'draft-proof.txt',base64:btoa('Synthetic attachment retained without sending.')});
  if(result.accepted!==true)throw Error('Synthetic draft attachment was not accepted');
 });
 const before=await page.evaluate(()=>{const s=window.amplifier.getState();window.peerDocument=crypto.randomUUID();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerDocument}});
 assert.equal(before.attachments.length,1);
 const link=original.getByRole('button',{name:'Preview source chat: Navigation design'});
 await link.click();await expect(panel()).toBeVisible();
 await expect(panel().locator('[data-part="peer-workspace"]')).toHaveText('readiness-review');
 await expect(panel()).not.toContainText(otherDraft);
 await expect(panel()).not.toContainText('private-readiness-draft.txt');
 await assertOtherDraft();
 assert.equal(await original.locator('.a-peer-summary').getAttribute('aria-expanded'),'true');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),facts.receiving);
 const sourceOriginal=panel().locator(`[data-preview-message-id="${facts.sourceText}"]`);
 await formatted(sourceOriginal.locator('.a-markdown'));
 await sourceOriginal.getByRole('button',{name:'Copy preview original as Markdown'}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.text);
 const sourceLong=panel().locator(`[data-preview-message-id="${facts.sourceLong}"]`);await sourceLong.getByRole('button',{name:'Show full text',exact:true}).click();
 await expect(sourceLong.locator('h2').last()).toHaveText('Beyond compact boundary');
 await sourceLong.getByRole('button',{name:'Copy preview original as Markdown'}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.long);
 const previewIds=()=>panel().locator('[data-preview-message-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.previewMessageId));
 assert.equal(facts.sourceIds.length,80);
 assert.deepEqual(await previewIds(),facts.sourceIds.slice(-20));
 for(const length of [40,60]){
  await panel().getByRole('button',{name:'Load earlier messages',exact:true}).click();
  await expect.poll(previewIds).toEqual(facts.sourceIds.slice(-length));
 }
 await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await expect(panel().getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
 assert.deepEqual(await previewIds(),facts.sourceIds.slice(-60));
 await panel().getByRole('button',{name:'Load earlier messages',exact:true}).click();
 await expect.poll(previewIds).toEqual(facts.sourceIds);
 await formatted(panel().locator(`[data-preview-message-id="${facts.older}"] .a-markdown`));
 const longNode=await sourceLong.elementHandle();
 const readAnchor=()=>panel().locator('.a-peer-preview-scroll').evaluate(pane=>{
  const top=pane.getBoundingClientRect().top,node=[...pane.querySelectorAll('[data-preview-message-id]')].find(n=>n.getBoundingClientRect().bottom>top);
  return {id:node.dataset.previewMessageId,offset:node.getBoundingClientRect().top-top};
 });
 await panel().locator('.a-peer-preview-scroll').evaluate(n=>n.scrollTop=700);
 const anchor=await readAnchor();await change('source-arrive');
 await expect(panel().getByText('New captured note',{exact:true})).toHaveCount(0);
 await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await expect(panel().getByText('New captured note',{exact:true})).toBeVisible();
 assert.equal(await sourceLong.evaluate((node,old)=>node===old,longNode),true);
 const afterAnchor=await readAnchor();assert.equal(afterAnchor.id,anchor.id);assert.ok(Math.abs(afterAnchor.offset-anchor.offset)<2);
 await expect(sourceLong.locator('h2').last()).toHaveText('Beyond compact boundary');
 const arrived=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
 assert.deepEqual(await previewIds(),arrived.sourceIds);
 // Confirm no reload/current draft mutation through preview and an adjacent close/reopen cycle.
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerDocument}}),before);
 await panel().getByRole('button',{name:'Close source preview'}).click();await expect(link).toBeFocused();
 await change('arrive');await expect(summary).toContainText('4 messages');await expect(original.locator('.a-peer-summary')).toHaveAttribute('aria-expanded','true');
 await expect(composer).toHaveDraft(before.draft);await assertOtherDraft();
 for(const width of [390,320]){
  await page.setViewportSize({width,height:900});await link.click();await expect(panel()).toBeVisible();
  await expect(panel()).toHaveAttribute('aria-modal','true');
  await expect(panel().locator('[data-part="peer-workspace"]')).toHaveText('readiness-review');
  const geometry=await panel().evaluate(n=>({width:n.getBoundingClientRect().width,overflow:n.scrollWidth>n.clientWidth}));assert.equal(geometry.overflow,false);assert.ok(geometry.width<=width);
  if(process.env.AMPLIFIER_PROOF_DIR){mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,`peers-narrow-${width}.png`)});}
  await panel().getByRole('button',{name:'Close source preview'}).press('Escape');await expect(link).toBeFocused();
 }
 // Resolve the second identical title from its exact original, never by a
 // page-wide title lookup. Its one canonical message differs from source A.
 const otherSourceLink=otherOriginal.getByRole('button',{name:'Preview source chat: Navigation design',exact:true});
 await otherSourceLink.click();await expect(panel().locator('.a-canvas-head strong')).toHaveText('Navigation design');
 await expect(panel().locator('[data-part="peer-workspace"]')).toHaveText(facts.sourceBWorkspaceLabel);
 await expect(panel().locator('[data-preview-message-id]')).toHaveCount(1);
 await expect(panel().locator('[data-preview-message-id]')).toHaveAttribute('data-preview-message-id',facts.sourceBText);
 await expect(panel().locator(`[data-preview-message-id="${facts.sourceText}"]`)).toHaveCount(0);
 await panel().getByRole('button',{name:'Copy preview original as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.crlf);
 assert.ok(await panel().locator('.a-canvas-head').evaluate(n=>n.scrollWidth<=n.clientWidth));
 await panel().getByRole('button',{name:'Close source preview'}).press('Shift+Tab');
 assert.equal(await panel().evaluate(n=>n.contains(document.activeElement)),true);
 await page.keyboard.press('Escape');await expect(otherSourceLink).toBeFocused();
 await page.setViewportSize({width:1280,height:1000});
 // Existing Settings cancellation must prevent the exact preview request.
 await openSettingsPage(page,'workspaces');
 await page.locator('#default-workspace-root').fill('/synthetic-unsaved-folder');
 await expect(page.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toBeVisible();
 await page.evaluate(mid=>{
  const s=window.amplifier.getState(),peer=s.sessions.find(x=>x.id===s.selectedSessionId).messages.find(m=>m.id===mid).attribution.peer;
  window.peerGuardAttempt=window.amplifier.dispatch('peer.preview',{proof:peer.proof,sourceSessionId:s.selectedSessionId});
 },facts.originals[0]);
 const settingsGuard=page.getByRole('alertdialog',{name:'Unsaved settings changes'});
 await expect(settingsGuard).toBeVisible();await settingsGuard.getByRole('button',{name:'Keep settings open'}).click();
 assert.equal((await page.evaluate(()=>window.peerGuardAttempt)).accepted,false);
 await expect(page.locator('#default-workspace-root')).toHaveValue('/synthetic-unsaved-folder');
 await expect(panel()).toHaveCount(0);
 await page.getByRole('button',{name:/^Close (settings|panel)$/}).click();await settingsGuard.getByRole('button',{name:'Discard changes',exact:true}).click();
 // Exact existing Canvas dirty guard. The feature must not replace it.
 await page.evaluate(async()=>{await window.amplifier.dispatch('canvas.show',{kind:'html',content:'<input value="unfinished">'});const state=window.amplifier.getState(),v=state.canvasWorkspace.views.find(x=>x.viewId==='primary');await window.amplifier.dispatch('canvas.views.dirty',{viewId:v.viewId,resourceId:v.resourceId,resourceRevision:v.resourceRevision,generation:v.generation,dirty:true})});
 await link.click();await expect(panel()).toHaveCount(0);await expect(original.getByRole('alert')).toContainText('edit');
 await page.evaluate(async()=>{const v=window.amplifier.getState().canvasWorkspace.views.find(x=>x.viewId==='primary');await window.amplifier.dispatch('canvas.views.dirty',{viewId:v.viewId,resourceId:v.resourceId,resourceRevision:v.resourceRevision,generation:v.generation,dirty:false})});
 // A real renderer-local edit can finish after preview has replaced its visible
 // surface. Arm it while visible, then release its event only after preview opens.
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const view=async()=>(await action('canvas.views.inspect')).result.views.find(v=>v.viewId==='primary');
 const viewTarget=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,v[key]]));
 const assertDraft=async()=>assert.deepEqual(await page.evaluate(()=>{
  const s=window.amplifier.getState();
  return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerDocument};
 }),before);
 await action('canvas.show',{kind:'markdown',title:'Retained local editor',content:'# Canonical source remains unchanged'});
 const manifest={id:'example.peer-preservation-editor',label:'Peer preservation editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const rendererSource=`export default ({React})=>function Editor({host}){
  const [text,setText]=React.useState(""),[armed,setArmed]=React.useState(false),[mount]=React.useState(()=>crypto.randomUUID());
  React.useEffect(()=>{const release=()=>{if(armed){setText("LATE UNSAVED EDIT");host.setDirty(true)}};document.addEventListener("peer-fixture-release-edit",release);return()=>document.removeEventListener("peer-fixture-release-edit",release)},[armed,host]);
  return React.createElement("div",null,
   React.createElement("label",null,"Retained local edit",React.createElement("input",{"data-mount":mount,"data-armed":armed,value:text,readOnly:true})),
   React.createElement("button",{onClick:()=>setArmed(true)},"Queue late editor change"),
   React.createElement("button",{onClick:()=>{setText("");setArmed(false);host.setDirty(false)}},"Cancel local edit"));
 }`;
 const renderer=(await action('shell.packages.stage',{manifest,source:rendererSource})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest:renderer})).result.status,'passed');
 await action('canvas.views.renderer',{...viewTarget(await view()),renderer});
 const editor=page.locator('[data-canvas-view="primary"]').getByLabel('Retained local edit');
 await expect(editor).toBeVisible();await page.getByRole('button',{name:'Queue late editor change',exact:true}).click();
 await expect(editor).toHaveAttribute('data-armed','true');
 const lease=viewTarget(await view()),editorMount=await editor.getAttribute('data-mount');
 await link.click();await expect(panel()).toBeVisible();
 await expect(editor).toBeHidden();
 const previewMount=await panel().elementHandle();
 await page.evaluate(()=>document.dispatchEvent(new Event('peer-fixture-release-edit')));
 await expect(editor).toHaveValue('LATE UNSAVED EDIT');await expect.poll(async()=>(await view()).dirty).toBe(true);
 await panel().getByRole('button',{name:'Open chat',exact:true}).click();
 await expect(panel().getByRole('alert')).toContainText('primary viewer edit');
 await assertDraft();assert.deepEqual(viewTarget(await view()),lease);
 await expect(editor).toHaveValue('LATE UNSAVED EDIT');await expect(editor).toHaveAttribute('data-mount',editorMount);
 assert.equal(await panel().evaluate((node,old)=>node===old,previewMount),true);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.content),'# Canonical source remains unchanged');
 // Cancel using that same renderer's real control, not force-clear/recovery.
 await panel().getByRole('button',{name:'Close source preview'}).click();await expect(editor).toBeVisible();
 await page.getByRole('button',{name:'Cancel local edit',exact:true}).click();
 await expect.poll(async()=>(await view()).dirty).toBe(false);await expect(editor).toHaveValue('');
 assert.deepEqual(viewTarget(await view()),lease);await expect(editor).toHaveAttribute('data-mount',editorMount);
 await link.click();await expect(panel()).toBeVisible();
 // Settings edits now begin AFTER preview. A stale activation of the already
 // mounted Open chat control must still run its real Settings guard. Dispatching
 // its click models that late activation; it does not dismiss the modal overlay.
 await openSettingsPage(page,'workspaces');
 const lateSettings=page.locator('#default-workspace-root'),lateSettingsNode=await lateSettings.elementHandle();
 await lateSettings.fill('/synthetic-late-unsaved-folder');
 await expect(page.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toBeVisible();
 await panel().getByRole('button',{name:'Open chat',exact:true}).dispatchEvent('click');
 await expect(settingsGuard).toBeVisible();await settingsGuard.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(lateSettings).toHaveValue('/synthetic-late-unsaved-folder');
 assert.equal(await lateSettings.evaluate((node,old)=>node===old,lateSettingsNode),true);
 await assertDraft();assert.deepEqual(viewTarget(await view()),lease);
 await expect(editor).toHaveAttribute('data-mount',editorMount);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.content),'# Canonical source remains unchanged');
 await page.getByRole('button',{name:/^Close (settings|panel)$/}).click();
 await settingsGuard.getByRole('button',{name:'Discard changes',exact:true}).click();
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);await expect(panel()).toBeVisible();
 await expect(panel().getByRole('button',{name:'Open chat',exact:true})).toBeEnabled();
 await page.route('**/api/peer/preview?**',async route=>{if(!new URL(route.request().url()).searchParams.has('metadata'))await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic read failure'})});else await route.continue()});
 await panel().getByRole('button',{name:'Refresh',exact:true}).click();await expect(panel().getByRole('alert')).toContainText('Previous snapshot retained');
 await page.unroute('**/api/peer/preview?**');await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await change('archive');await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await change('cold-receiver');
 await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await expect(panel().getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
 assert.equal((await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json()).receiverCold,true);
 await expect(panel().getByRole('alert')).toHaveCount(0);
 if(process.env.AMPLIFIER_PROOF_DIR){mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,'peers-desktop.png')});}
 await panel().getByRole('button',{name:'Open chat',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(facts.sourceA);assert.equal(await page.evaluate(()=>window.peerDocument),before.document);
 // Both clients now view the source, but its other browser's unfinished input
 // and attachment must never become this client's composer.
 await expect(composer).toHaveDraft('');await expect(composer).toHaveText('');
 assert.deepEqual((await draftState(page)).attachments,[]);
 await assertOtherDraft();
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),facts.receiving);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(facts.receiving);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),before.draft);
 await expect(composer).toHaveText(before.draft);await assertOtherDraft();
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[]}),before.attachments);
 await expect(summary).toBeVisible();if(await summary.getAttribute('aria-expanded')==='false')await summary.click();
 await link.click();await expect(panel()).toBeVisible();await change('revoke');
 await expect(panel().getByText('Source chat unavailable',{exact:true})).toBeVisible();await expect(panel().locator('[data-preview-message-id]')).toHaveCount(0);
 await expect(panel().getByRole('button',{name:'Open chat',exact:true})).toBeDisabled();
 await expect(panel().locator('[data-part="peer-workspace"]')).toHaveCount(0);
 await expect(original.locator('.a-peer-workspace')).toHaveCount(0);
 await assertOtherDraft();
 const final=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();assert.deepEqual(final.effects,[]);assert.deepEqual(final.subprocesses,[]);assert.deepEqual(errors,[]);
 assert.equal(final.rendererValidations.length,1);
 assert.ok(!commands.some(x=>['conversation.send','worker.spawn','session.warm','approval.respond'].includes(x)));
 results.push({mode:installed?'installed-static':'built-static',crossWorkspaceIndependentRoots:true,workspaceLabels:{collapsed:true,sourceRow:true,preview:true},requestDescriptorFallback:true,requestLinkedAllScopeReads:true,workspaceDiscoveryRetained:true,twoClientPrivateDrafts:true,semanticMarkdown:true,exactCopy:true,refreshEarlierOrder:true,draftTextAfterReturn:true,draftAttachmentsRetained:before.attachments.length,lateDirtyPreservation:{canvasRefused:true,rendererMountAndLeaseRetained:true,settingsCancelled:true,settingsEditorRetained:true,canonicalSourceRetained:true,cleanOpenAccepted:true},narrow:[320,390],noReload:true,noRuntime:true,negativeControl:'separate PEER_NEGATIVE_FLATTEN=1 run required',backend:final.backend});
 if(process.env.AMPLIFIER_PROOF_DIR)writeFileSync(join(process.env.AMPLIFIER_PROOF_DIR,'peer-browser-result.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
}catch(error){
 if(page&&process.env.AMPLIFIER_PROOF_DIR){
  mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});
  await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,'failure.png')}).catch(()=>{});
  writeFileSync(join(process.env.AMPLIFIER_PROOF_DIR,'failure.json'),JSON.stringify({error:String(error),body:await page.locator('body').innerText().catch(()=>''),url:page.url()},null,2));
 }
 throw error;
}finally{await browser?.close();fixture.kill();}
