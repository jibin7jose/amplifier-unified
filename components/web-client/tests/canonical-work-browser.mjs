import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,writeFile,mkdir,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';
const outputDir=process.env.AMPLIFIER_TEST_OUTPUT_DIR||(process.env.AMPLIFIER_TEST_ARTIFACTS?join(process.env.AMPLIFIER_TEST_ARTIFACTS,'canonical-work'):null)||await mkdtemp(join(tmpdir(),'amplifier-canonical-work-'));
await mkdir(outputDir,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let log='',fixtureCookie;fixture.stderr.on('data',chunk=>log+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);fixtureCookie=JSON.parse(line).cookie;resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});if(!response.ok)throw Error(await response.text()+' '+log);return response.json()};
await control({op:'canonical-work'});
let page;
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});
try{
 page=await browser.newPage({viewport:{width:1280,height:1050},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:headers});
 const errors=[],requestReads=[],responseReads=[];
 page.on('request',r=>{if(r.url().includes('/api/conversation/detail?')){const field=new URL(r.url()).searchParams.get('field');if(field==='request')requestReads.push(r.url());if(field==='response')responseReads.push(r.url())}});
 page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 // Browser-managed downloads use the real signed session cookie; Chromium
 // does not forward Playwright's extra Authorization header to downloads.
 await page.context().addCookies([{name:'amplifier_unified_session',value:fixtureCookie,url:base,httpOnly:true,sameSite:'Strict'}]);
 await page.goto(base);await showFixtureConversation(page);
 const groups=page.locator('[data-group-id]'),before=page.locator('[data-group-id$="@before"]'),interim=page.locator('[data-group-id$="@interim"]');
 // The bounded saved-event window settles independently of live host groups.
 // Wait for that real receipt rather than racing its arrival with UI clicks.
 const saved=page.locator('[data-group-id^="event-window:"]');
 const checkGroups=async expected=>{
  await page.waitForFunction(()=>{try{const receipt=JSON.parse(document.querySelector('.a-messages')?.dataset.workProjection);return receipt.status==='ready'&&receipt.current===true&&receipt.receipt?.scope==='recent-events'}catch{return false}});
  await expect(saved).toHaveCount(1);
  await expect(saved).toContainText('Recent saved work');
  await expect(groups).toHaveCount(expected.length+1);
  assert.deepEqual(await groups.evaluateAll(rows=>rows.map(n=>n.dataset.groupId).filter(id=>!id.startsWith('event-window:'))),expected);
  assert.equal(await saved.locator('.a-execution-roots').count(),0,'The saved window stays collapsed until requested');
 };
 await checkGroups(['canonical@before','canonical@interim']);
 const order=()=>page.locator('.a-messages').evaluate(el=>[...el.children].map(n=>n.dataset.messageId||(n.dataset.groupId?.startsWith('canonical@')?n.dataset.groupId.split('@').at(-1):null)).filter(Boolean));
 assert.deepEqual(await order(),['before','before','interim','interim','final']);
 const beforeToggle=before.locator('button.a-execution-turn-line'),interimToggle=interim.locator('button.a-execution-turn-line');
 assert.equal(await beforeToggle.count(),1,'Every work section must have a collapse control, even with only two actions');
 await expect(beforeToggle).toHaveAttribute('aria-expanded','false');
 await expect(before.locator('.a-execution-roots')).toHaveCount(0);
 await beforeToggle.focus();await page.keyboard.press('Enter');
 await expect(beforeToggle).toHaveAttribute('aria-expanded','true');
 await expect(before).toContainText('git diff --check');
 assert.match(await before.locator('.a-execution-turn-line').innerText(),/Worked for 3s[\s\S]*12 tokens.*\$0.001/);
 await expect(interimToggle).toHaveAttribute('aria-expanded','false');await interimToggle.click();
 const action=id=>page.locator(`[data-node-id$=":${id}"]`),read15=action('read15'),read16=action('read16');
 assert.equal(await page.locator('.a-execution-body').count(),0);
 const brief=action('brief'),briefLine=brief.locator(':scope > button.a-execution-action-line');
 await expect(briefLine.locator('.a-execution-label > span')).toHaveText('Ran · bash git diff --check');
 await expect(briefLine.locator('code')).toHaveText('git diff --check');
 await briefLine.click();
 const command=brief.locator('.a-execution-payload').filter({has:page.locator('header > span:first-child',{hasText:/^Command$/})});
 await expect(command.locator('pre')).toHaveText('git diff --check');
 await brief.getByRole('button',{name:'Copy command',exact:true}).click();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'git diff --check');
 await briefLine.click();
 await read15.locator('button.a-execution-action-line').click();await read16.locator('button.a-execution-action-line').click();
 assert.equal(await read15.locator('pre').innerText(),Array.from({length:15},(_,i)=>'Line '+(i+1)).join('\n'));
 assert.equal(await read15.getByRole('button',{name:/Show all/}).count(),0);
 const read16Full=Array.from({length:16},(_,i)=>'Line '+(i+1)).join('\n');
 assert.equal(await read16.locator('pre').innerText(),Array.from({length:10},(_,i)=>'Line '+(i+1)).join('\n')+'\n…');
 await expect(read16.getByText('Showing a preview.',{exact:true})).toBeVisible();
 await read16.getByRole('button',{name:'Copy file content',exact:true}).click();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),read16Full,'Copy retains every saved line while the display is shortened');
 await read16.getByRole('button',{name:'Show all 16 lines'}).click();
 assert.equal(await read16.locator('pre').innerText(),read16Full);
 await expect(read16.getByText('Showing a preview.',{exact:true})).toHaveCount(0);
 assert.equal(await read15.locator('.a-execution-payload details').count(),0);
 assert.equal(await read16.locator('.a-execution-payload details').count(),0);
 await action('owned').locator('button.a-execution-action-line').click();
 assert.match(await action('owned').innerText(),/Bearer owner-recorded-value/);
 assert.equal((await action('owned').innerText()).split('The full result is already here').length-1,1);
 const model=before.locator('[data-kind="llm"]');
 assert.equal(await model.locator('.a-execution-body').count(),0);assert.equal(requestReads.length,0);
 await model.locator('button.a-execution-action-line').click();
 assert.match(await model.innerText(),/Messages\s+100/);assert.match(await model.innerText(),/Tools\s+2/);assert.match(await model.innerText(),/Reasoning effort\s+high/);
 assert.equal(requestReads.length,0);assert.equal(await model.locator('pre').count(),0);
 const fullDownload=page.waitForEvent('download');
 await model.getByRole('link',{name:'Download full recorded request (JSON)',exact:true}).click();
 const fullFile=await fullDownload,fullRecord=JSON.parse(await readFile(await fullFile.path(),'utf8'));
 assert.equal(fullRecord.payload.end,'FULL-RECORDING-END');
 assert.equal(fullRecord.payload.largeDiagnostic.length,1_100_000);
 assert.equal(fullRecord.capture.truncated,false);assert.equal(requestReads.length,0);
 let failRequest=true;await page.route('**/api/conversation/detail?**',route=>{if(failRequest&&new URL(route.request().url()).searchParams.get('field')==='request'){failRequest=false;return route.fulfill({status:503,json:{error:'Request temporarily unavailable'}})}return route.continue()});
 await model.getByRole('button',{name:/^View request/}).click();await model.getByRole('button',{name:'Retry request',exact:true}).click();
 await expect(model.getByRole('button',{name:'Copy raw request',exact:true})).toBeEnabled();
 const requestTree=model.getByRole('group',{name:'Request payload'});
 await expect(requestTree.getByRole('button',{name:/^Request Object/})).toHaveAttribute('aria-expanded','false');
 assert.equal(await requestTree.locator('.a-json-value').count(),0);assert.equal(responseReads.length,0);
 await requestTree.getByRole('button',{name:/^Request Object/}).click();
 await requestTree.getByRole('button',{name:/^input Array/}).click();
 await expect(requestTree.getByRole('button',{name:/^\[49\] Object/})).toBeVisible();
 assert.equal(await requestTree.getByRole('button',{name:/^\[50\] Object/}).count(),0);
 await requestTree.getByRole('button',{name:'Show next 50 of 50 remaining',exact:true}).click();
 await requestTree.getByRole('button',{name:/^\[99\] Object/}).click();await expect(requestTree).toContainText('Recorded message 99');
 await requestTree.getByRole('button',{name:'Show more text (580 characters left)',exact:true}).click();
 await model.getByRole('button',{name:/^View response/}).click();
 const responseTree=model.getByRole('group',{name:'Response payload'});
 await expect(responseTree.getByRole('button',{name:/^Response Object/})).toHaveAttribute('aria-expanded','false');
 assert.equal(responseReads.length,1);assert.equal(requestReads.length,2);
 await responseTree.getByRole('button',{name:/^Response Object/}).click();
 await responseTree.getByRole('button',{name:/^output Array/}).click();
 await responseTree.getByRole('button',{name:/^\[0\] Object/}).click();
 await responseTree.getByRole('button',{name:/^content Array/}).click();
 await responseTree.getByRole('button',{name:/^\[0\] Object/}).last().click();
 await expect(responseTree).toContainText('Recorded response content');
 await model.getByRole('button',{name:'Copy raw response',exact:true}).click();
 assert.equal(JSON.parse(await page.evaluate(()=>navigator.clipboard.readText())).output[0].content[0].text,'Recorded response content');
 await model.getByRole('button',{name:'Hide response',exact:true}).click();await model.getByRole('button',{name:/^View response/}).click();assert.equal(responseReads.length,1);

 await model.getByRole('button',{name:'Copy raw request',exact:true}).click();const copied=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));
 assert.equal(copied.input.length,100);assert.equal(copied.instructions,'Owner-recorded instructions');assert.equal(requestReads.length,2);
 const exportRecord=async(container,field)=>{
  const pending=page.waitForEvent('download');
  await container.getByRole('button',{name:`Download recorded ${field} (JSON)`,exact:true}).click();
  const saved=await pending;return {filename:saved.suggestedFilename(),record:JSON.parse(await readFile(await saved.path(),'utf8'))};
 };
 const requestExport=await exportRecord(model,'request');
 assert.deepEqual(requestExport.record.payload,copied);assert.equal(requestExport.record.kind,'provider.request');
 const responseExport=await exportRecord(model,'response');
 assert.equal(responseExport.record.payload.output[0].content[0].text,'Recorded response content');
 assert.equal(requestReads.length,2);assert.equal(responseReads.length,1);
 await model.locator('button.a-execution-action-line').click();await model.locator('button.a-execution-action-line').click();assert.equal(requestReads.length,2);
 await expect(model.getByRole('button',{name:/^View request/})).toBeVisible();assert.equal(responseReads.length,1);
 const noRequest=interim.locator('[data-kind="llm"]');await noRequest.locator('button.a-execution-action-line').click();assert.match(await noRequest.innerText(),/This call’s request recording isn’t available here/);
 const priorWindow=await saved.getAttribute('data-group-id');
 await control({op:'canonical-append'});
 await expect(saved).not.toHaveAttribute('data-group-id',priorWindow,{timeout:15000});
 await checkGroups(['canonical@before','canonical@interim','canonical@final']);
 assert.deepEqual(await order(),['before','before','interim','interim','final','final']);
 const finalGroup=page.locator('[data-group-id$="@final"]'),finalToggle=finalGroup.locator('button.a-execution-turn-line');
 await expect(finalToggle).toHaveAttribute('aria-expanded','false');await finalToggle.click();
 await expect(finalToggle).toHaveAttribute('aria-expanded','true');
 await finalToggle.focus();await page.keyboard.press('Space');
 await expect(finalToggle).toHaveAttribute('aria-expanded','false');
 await expect(finalGroup.locator('.a-execution-roots')).toHaveCount(0);
 await finalToggle.click();
 const small=page.locator('[data-group-id$="@final"] [data-kind="llm"]');await small.locator('button.a-execution-action-line').click();
 assert.equal(requestReads.length,2);assert.equal(responseReads.length,1);assert.equal(await small.locator('.a-json-viewer').count(),0);
 await small.getByRole('button',{name:/^View request/}).click();
 await small.getByRole('button',{name:/^Request Object/}).click();await expect(small).toContainText('Small recorded request');assert.equal(requestReads.length,3);
 await small.getByRole('button',{name:/^View response/}).click();await expect(small).toContainText('This recording is text rather than complete JSON');await expect(small).toContainText('Small recorded response');await expect(small).toContainText('part of the response is omitted');assert.equal(responseReads.length,2);
 const partialExport=await exportRecord(small,'response');
 assert.equal(partialExport.record.capture.truncated,true);assert.match(partialExport.filename,/-partial.json$/);assert.match(partialExport.record.payload,/Small recorded response/);
 await openSettingsPage(page,'appearance');
 await expect(page.getByRole('radio',{name:/^Balanced /})).toBeChecked();
 await page.getByRole('radio',{name:/^Focused /}).check();await expect(page.locator('#amp-one')).toHaveAttribute('data-execution-detail','minimal');
 assert.equal(await before.locator('.a-execution-turn-line .a-execution-usage').isVisible(),false);
 await page.getByRole('radio',{name:/^Everything /}).check();await expect(page.locator('#amp-one')).toHaveAttribute('data-execution-detail','detailed');
 assert.match(await before.locator('.a-execution-call-counts').innerText(),/1 tool call · 1 model call/);
 assert.match(await interim.locator('.a-execution-call-counts').innerText(),/3 tool calls · 1 model call/);
 assert.equal(await interim.locator('.a-execution-call-counts').isVisible(),true);
 await page.getByRole('button',{name:'Close panel'}).click();
 // Collapsing a section preserves its child expansion and doesn't move interim replies.
 await interimToggle.click();await expect(interimToggle).toHaveAttribute('aria-expanded','false');
 await expect(read15).toHaveCount(0);
 await page.reload();await expect(interimToggle).toHaveAttribute('aria-expanded','false');
 await expect(read15).toHaveCount(0);
 const interimId=await interim.getAttribute('data-group-id');
 await page.evaluate(id=>{const expanded=window.amplifier.getState().view.executionExpanded;return window.amplifier.dispatch('view.update',{patch:{executionExpanded:[...expanded,`turn:${id}`]}})},interimId);
 await expect(interimToggle).toHaveAttribute('aria-expanded','true');
 await page.reload();await expect(page.locator('#amp-one')).toHaveAttribute('data-execution-detail','detailed');
 await checkGroups(['canonical@before','canonical@interim','canonical@final']);
 assert.deepEqual(await order(),['before','before','interim','interim','final','final']);
 await read15.locator('pre').waitFor();assert.equal(requestReads.length,3);assert.equal(responseReads.length,2);assert.equal(await page.locator('.a-json-viewer').count(),0,'Reloaded expanded calls must not fetch raw payloads');
 await model.getByRole('button',{name:/^View request/}).click();await model.getByRole('button',{name:/^Request Object/}).click();
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navPinned:false,navExpanded:false}}));
 await before.scrollIntoViewIfNeeded();await page.screenshot({path:join(outputDir,'desktop.png')});
 await page.setViewportSize({width:390,height:844});await model.locator('.a-json-viewer').scrollIntoViewIfNeeded();await page.mouse.move(380,830);await page.waitForTimeout(400);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(outputDir,'mobile.png')});
 assert.deepEqual(errors,[]);
 await writeFile(join(outputDir,'work-groups.json'),JSON.stringify({canonicalGroups:await page.locator('[data-group-id^="canonical@"]').evaluateAll(rows=>rows.map(n=>n.dataset.groupId)),savedWindows:await saved.count(),workProjection:JSON.parse(await page.locator('.a-messages').getAttribute('data-work-projection')),errors},null,2));
 console.log('Collapsed one-line actions and richer grouped model details pass; raw requests and responses are click-only, collapsible, paged, retryable and copyable; 15/10 previews, ordering, shared expansion, reload and mobile pass.');
}catch(error){
 if(page){
  await writeFile(join(outputDir,'failure-groups.json'),JSON.stringify(await page.evaluate(()=>{
   const state=window.amplifier?.getState(),session=state?.sessions.find(row=>row.id===state.selectedSessionId);
   return {groups:[...document.querySelectorAll('[data-group-id]')].map(n=>n.dataset.groupId),
    workProjection:document.querySelector('.a-messages')?.dataset.workProjection,
    selectedSessionId:state?.selectedSessionId,hostInstanceId:state?.client?.hostInstanceId,
    publicWorkProjection:session?.workProjection,historyLoading:session?.historyLoading,
    readingControls:[...document.querySelectorAll('.a-history-window-controls button')].map(node=>node.textContent),
    workStatus:document.querySelector('[data-part="work-projection-status"]')?.textContent};
  }),null,2)).catch(()=>{});
  await page.screenshot({path:join(outputDir,'failure.png')}).catch(()=>{});
 }
 console.error(log);throw error
}finally{await browser.close();fixture.kill()}
