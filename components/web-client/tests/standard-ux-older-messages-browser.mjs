import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
// Private canonical JSONL history, real browser/host; no external provider.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('Owned artifact path required');await mkdir(output,{recursive:true});
const evidence=new StandardUxEvidence(output,{baseline:'bc98c0ad8',data:'Synthetic canonical 305-row native history',runtime:'Production HTTP/SSE host; synthetic inert runtime',timings:'Shared-load functional only'});
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});let browser,peerBrowser,page,releaseRead,log='',failed=false;fixture.stderr.on('data',c=>log+=c);
try{
 const port=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});if(!response.ok)throw Error(await response.text());return response.json()};
 const initial=await control({op:'native-history'});browser=await chromium.launch({headless:true,args:['--single-process','--no-zygote','--disable-gpu']});page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});page.setDefaultTimeout(7000);evidence.page=page;
 const annotationReads=[];page.on('request',r=>{if(new URL(r.url()).pathname==='/api/conversation/annotations')annotationReads.push(r.postDataJSON())});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));const state=()=>page.evaluate(()=>window.amplifier.getState());
 const click=(label,target)=>evidence.action(label,()=>target.click());
 const probe=async(id,flows,fn)=>{const result=await evidence.probe(id,flows,async()=>{await fn();return {variant:'desktop-pointer',...await evidence.snapshot(page,id,'#amp-one')}});if(result.status!=='passed')throw Error('Dependent flow stopped after '+id)};
 const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
 let targetId,sourceId,detached,canonicalBeforeReactions;
 await probe('native-older-page',['messages.rail'],async()=>{
  await page.goto(base);await showFixtureConversation(page,{navPinned:true});await expect(page.locator('[data-message-id]')).toHaveCount(100);sourceId=(await state()).selectedSessionId;
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this old-page draft');
  await click('Load earlier saved messages',page.getByRole('button',{name:'Load earlier conversation',exact:true}));
  const target=page.locator('[data-message-id]').filter({hasText:/^Saved message 150\b/});await expect(target).toHaveCount(1);targetId=await target.getAttribute('data-message-id');
  evidence.assertion('Target outside the resident latest page',initial.state.sessions.find(s=>s.id===sourceId).messages.some(m=>m.id===targetId),false);
  evidence.assertion('Older paging never activates runtime',(await control({op:'inspect'})).runtimeStarts,initial.runtimeStarts);
 });
 const target=()=>page.locator(`[data-message-id="${targetId}"]`);
 await probe('native-older-action-boundary',['messages.rail','messages.fork'],async()=>{
  await expect(target().getByRole('button',{name:'Reply to message',exact:true})).toHaveCount(0);
  await expect(target().getByRole('button',{name:'React to message',exact:true})).toHaveCount(0);
  await expect(target().getByRole('button',{name:'Edit message',exact:true})).toHaveCount(0);
  await expect(target().getByRole('button',{name:/^Fork conversation/})).toHaveCount(0);
  evidence.assertion('Native display-only target remains readable',(await target().innerText()).includes('Saved message 150'));
 });
 await probe('canonical-older-page',['messages.rail'],async()=>{
  const prepared=await control({op:'heavy',messages:305,otherMessages:4,chars:80});
  const canonical=prepared.state.sessions.find(s=>s.id===prepared.alpha);await control({op:'patch',view:{}});
  await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),canonical.id);sourceId=canonical.id;
  await expect(page.locator('[data-message-id]')).toHaveCount(100);
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this old-page draft');
  await click('Load earlier canonical web messages',page.getByRole('button',{name:'Load earlier conversation',exact:true}));
  targetId=sourceId+'-m-150';await expect(target()).toHaveCount(1);
  detached=await page.evaluate(id=>{const row=window.amplifier.getState().sessions.find(s=>s.id===id);window.auditDetached=structuredClone(row);return JSON.stringify(window.auditDetached)},sourceId);
 });
 await probe('canonical-older-quote',['messages.reply'],async()=>{
  await click('Reply to exact older canonical message',target().getByRole('button',{name:'Reply to message',exact:true}));
  await expect(page.getByLabel('Quoted reply',{exact:true})).toContainText('Retained message 150');
  const quote=(await state()).view.draftDocument.segments.find(row=>row.type==='quote').quote;
  evidence.assertion('Quote targets exact native message',quote.messageId,targetId);
  evidence.assertion('Quote targets exact chat',quote.sessionId,sourceId);
  evidence.assertion('Older-page commentary retained',(await state()).view.draftDocument.segments.filter(row=>row.type==='text').map(row=>row.text).filter(Boolean),['Keep this old-page draft']);
  await click('Send one explicitly quoted message',page.getByRole('button',{name:'Send message',exact:true}));await expect(page.locator('.a-reply-quote')).toHaveCount(1);
  await click('Reveal exact older quote',page.getByTitle('Go to original message',{exact:true}));
  evidence.assertion('Reveal does not select unrelated conversation',(await state()).selectedSessionId,sourceId);await expect(target()).toBeInViewport();
  await expect(page.getByLabel('Quoted reply',{exact:true})).toHaveCount(0);
 });
 await probe('canonical-older-reaction',['messages.reaction'],async()=>{
  canonicalBeforeReactions=hash((await control({op:'inspect'})).state.sessions.find(s=>s.id===sourceId).messages);
  await click('Open reaction on older canonical message',target().getByRole('button',{name:'React to message',exact:true}));
  const receipt=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/actions'&&JSON.stringify(r.request().postDataJSON()).includes('message.reaction'));
  await click('Add exact Heart reaction',target().getByRole('button',{name:'Heart',exact:true}));
  const admitted=await(await receipt).json();const observed=await control({op:'inspect'});
  await writeFile(output+'/reaction-proof.json',JSON.stringify({accepted:admitted.accepted,result:admitted.result,owner:sourceId,target:targetId,canonical:observed.state.sessions.find(s=>s.id===sourceId).messageAnnotations?.[targetId],clientAnnotations:(await state()).sessions.find(s=>s.id===sourceId).messageAnnotations},null,2));
  await expect(target().getByRole('button',{name:'Remove Heart reaction',exact:true})).toBeVisible();
  evidence.assertion('Reaction never submits input',(await control({op:'inspect'})).runtimeSends,initial.runtimeSends+1);
  await click('Remove exact older Heart reaction',target().getByRole('button',{name:'Remove Heart reaction',exact:true}));await expect(target().getByRole('button',{name:'Remove Heart reaction',exact:true})).toHaveCount(0);
  evidence.assertion('Detached earlier snapshot stays byte-identical',hash(await page.evaluate(()=>JSON.stringify(window.auditDetached))),hash(detached));evidence.assertion('No page errors',errors,[]);
 });
 await probe('cross-client-older-reaction',['messages.reaction'],async()=>{
  peerBrowser=await chromium.launch({headless:true,args:['--single-process','--no-zygote','--disable-gpu']});const peer=await peerBrowser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});await peer.goto(base);await peer.waitForFunction(()=>window.amplifier?.getState());await peer.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sourceId);
  await peer.getByRole('button',{name:'Load earlier conversation',exact:true}).click();const other=peer.locator(`[data-message-id="${targetId}"]`);await expect(other).toHaveCount(1);
  await other.getByRole('button',{name:'React to message',exact:true}).click();await other.getByRole('button',{name:'Heart',exact:true}).click();await expect(target().getByRole('button',{name:'Remove Heart reaction',exact:true})).toBeVisible();
  await other.getByRole('button',{name:'Remove Heart reaction',exact:true}).click();await expect(target().getByRole('button',{name:'Remove Heart reaction',exact:true})).toHaveCount(0);
  evidence.assertion('Every annotation request is bounded to explicit IDs',annotationReads.every(row=>row.sessionId===sourceId&&row.messageIds.length<=100&&row.messageIds.length>0));
  const count=annotationReads.length;await page.waitForTimeout(150);evidence.assertion('Idle older reader does not poll',annotationReads.length,count);
  // Hold an older read that already captured its response, then navigate away.
  let entered;const gate=new Promise(resolve=>entered=resolve);await page.route('**/api/conversation/annotations',async route=>{const response=await route.fetch();await new Promise(resolve=>{releaseRead=resolve;entered()});try{await route.fulfill({response})}catch{}});
  await other.getByRole('button',{name:'React to message',exact:true}).click();await other.getByRole('button',{name:'Heart',exact:true}).click();await gate;
  if(await page.getByRole('button',{name:'Open navigation',exact:true}).isVisible())await click('Open navigation',page.getByRole('button',{name:'Open navigation',exact:true}));await click('Leave old reader for a new draft',page.getByRole('button',{name:'New chat',exact:true}));await expect.poll(async()=>(await state()).selectedSessionId).toBe(null);releaseRead();releaseRead=null;await page.unrouteAll({behavior:'wait'});await expect(page.locator('[data-message-id]')).toHaveCount(0);await expect(page.locator('.a-reaction-chip')).toHaveCount(0);
  const recent=page.getByRole('region',{name:'Recent chats',exact:true});await click('Return to original exact chat',recent.getByRole('button',{name:'Alpha conversation',exact:true}));await expect.poll(async()=>(await state()).selectedSessionId).toBe(sourceId);await expect(target()).toHaveCount(1);await target().scrollIntoViewIfNeeded();await expect(target().getByRole('button',{name:'Remove Heart reaction',exact:true})).toBeVisible();await page.reload();await expect(target()).toHaveCount(1);await target().scrollIntoViewIfNeeded();await expect(target().getByRole('button',{name:'Remove Heart reaction',exact:true})).toBeVisible();
  evidence.assertion('Reactions leave canonical transcript unchanged',hash((await control({op:'inspect'})).state.sessions.find(s=>s.id===sourceId).messages),canonicalBeforeReactions);
  evidence.assertion('Navigation and reactions never replay input',(await control({op:'inspect'})).runtimeSends,initial.runtimeSends+1);
  // Ordinary latest rows use their authoritative scalar revision, not a sticky
  // local receipt. A later peer removal must replace this client's own add.
  await page.getByRole('button',{name:'Jump to latest messages',exact:true}).click();
  await peer.getByRole('button',{name:'Jump to latest messages',exact:true}).click();
  const latestId=sourceId+'-m-304',ownLatest=page.locator(`[data-message-id="${latestId}"]`),peerLatest=peer.locator(`[data-message-id="${latestId}"]`);
  await expect(ownLatest).toBeVisible();await expect(peerLatest).toBeVisible();
  await ownLatest.getByRole('button',{name:'React to message',exact:true}).click();await ownLatest.getByRole('button',{name:'Heart',exact:true}).click();
  await expect(peerLatest.getByRole('button',{name:'Remove Heart reaction',exact:true})).toBeVisible();
  await peerLatest.getByRole('button',{name:'Remove Heart reaction',exact:true}).click();
  await expect(ownLatest.getByRole('button',{name:'Remove Heart reaction',exact:true})).toHaveCount(0);
  evidence.assertion('Latest reactions preserve canonical transcript',hash((await control({op:'inspect'})).state.sessions.find(s=>s.id===sourceId).messages),canonicalBeforeReactions);
  await writeFile(output+'/annotation-requests.json',JSON.stringify(annotationReads,null,2));
 });
}catch(error){failed=true;console.error(error.stack);if(!evidence.probes.some(r=>r.status==='failed'))evidence.probes.push({id:'runner',status:'failed',error:error.message,coverage:'none'})}
finally{await evidence.save();await writeFile(output+'/fixture.log',log);releaseRead?.();await peerBrowser?.close();await browser?.close();if(fixture.exitCode===null&&fixture.signalCode===null){const ended=once(fixture,'exit');fixture.kill();const timer=setTimeout(()=>fixture.kill('SIGKILL'),6000);try{await ended}finally{clearTimeout(timer)}}if(failed)process.exitCode=1}
