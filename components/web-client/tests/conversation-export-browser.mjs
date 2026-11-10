import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/conversation_export_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,vite;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Export fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Export fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 const target=url,root=fileURLToPath(new URL('../',import.meta.url));
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 const inspect=async()=>{const response=await page.request.get(new URL('/api/fixture/nativeWindow',page.url()).href);assert.ok(response.ok());return response.json()};
 const beforeExport=await inspect();
 // The legacy fixture's conversation-settings route now opens Chat info.
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(page.locator('[data-part="overlay"]')).toHaveCount(0);
 // Modern navigation can initially be Home even with a selected conversation.
 // Select the fixture's existing chat through the same supported action used
 // by the sidebar; the actual Share/menu/export controls below remain clicks.
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),beforeExport.sessionId);
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('button',{name:'Share or save',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Share or save',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Preview Markdown',exact:true}).click();
 await expect(page.getByLabel('Reviewed Markdown')).toBeVisible();
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'Copied conversation Markdown'})).toBeVisible();
 const copied=await page.evaluate(()=>navigator.clipboard.readText());
 assert.match(copied,/The earliest question/);assert.match(copied,/## User \(voice\)\n\nSpoken follow-up/);
 assert.ok(copied.includes(beforeExport.sessionId),'Default export identifies its exact source');
 assert.ok(copied.includes('```python\nprint("exact α")  \n```\n'));assert.match(copied,/saved-diagram/);
 const preview=page.getByLabel('Reviewed Markdown');assert.equal(await preview.inputValue(),copied);
 assert.equal((copied.match(/^## (User|Assistant)/gm)||[]).length,141,'All native pages and the voice bubble are included');
 assert.ok(await preview.evaluate(el=>el.scrollHeight>el.clientHeight),'Long preview has bounded scrollable viewport');
 await preview.focus();await preview.press('Control+End');await expect.poll(()=>preview.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
 assert.deepEqual(await inspect(),beforeExport,'Full preview and clipboard do not mutate resident/native history or activate runtime');
 const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Download Markdown',exact:true}).click();
 const download=await pending;assert.match(download.suggestedFilename(),/\.md$/);
 assert.equal(await readFile(await download.path(),'utf8'),copied);
 await expect(page.getByRole('status').filter({hasText:'Markdown download started'})).toBeVisible();
 // The exact existing UI action is also available through the shared bridge.
 const snapshot=await page.evaluate(async()=>{const state=window.amplifier.getState();return window.amplifier.dispatch('session.export',{id:state.selectedSessionId,format:'markdown',destination:'none'})});
 const stored=await page.request.get(new URL(snapshot.result.url,page.url()).href);assert.equal((await stored.json()).content,copied);
 // Existing normal Share -> More formats -> Export JSON must deliver the
 // closed diagnostic without Network/console/storage/header inspection.
 await page.getByText('More formats',{exact:true}).click();
 if(process.env.AMPLIFIER_TEST_WORK_DIAGNOSTIC==='1'){
  const refresh=await page.request.post(new URL('/api/fixture/workDiagnostic',page.url()).href);
  assert.ok(refresh.ok());
 }
 const jsonPending=page.waitForEvent('download');
 await page.getByRole('button',{name:'Export JSON',exact:true}).click();
 const jsonDownload=await jsonPending;
 assert.equal(jsonDownload.suggestedFilename(),'amplifier-export.json');
 const exported=JSON.parse(await readFile(await jsonDownload.path(),'utf8'));
 const diagnostic=exported.workDiagnostics;
 assert.deepEqual(Object.keys(diagnostic).sort(),['version','sessionId','attempt','current','status','stage','reason','exceptionCategory','errno','projectionStatus','projectionCurrent'].sort());
 assert.equal(diagnostic.sessionId,beforeExport.sessionId);
 assert.equal(diagnostic.version,1);
 assert.ok(!JSON.stringify(diagnostic).includes('SECRET-DIAGNOSTIC-CANARY'));
 if(process.env.AMPLIFIER_TEST_WORK_DIAGNOSTIC==='1'){
  assert.equal(diagnostic.status,'retrying');assert.equal(diagnostic.stage,'projector');
  assert.equal(diagnostic.exceptionCategory,'permission');assert.equal(diagnostic.errno,13);
  assert.equal(diagnostic.current,true);assert.equal(diagnostic.projectionStatus,'retrying');
  await writeFile('/artifacts/normal-export-diagnostic.json',JSON.stringify(diagnostic,null,2)+'\n');
 }
 // A selected range is reviewed separately; changing controls revokes the old preview.
 await page.getByLabel('Export scope').selectOption('range');
 await expect(page.getByLabel('Reviewed Markdown')).toHaveCount(0);
 const boundaries=await page.getByLabel('Starting message').locator('option').evaluateAll(rows=>rows.map(row=>row.value));
 await page.getByLabel('Starting message').selectOption(boundaries.at(-2));
 await page.getByLabel('Ending message').selectOption(boundaries.at(-1));
 await page.getByRole('checkbox',{name:'Minimal context: omit conversation identifiers'}).check();
 await page.getByRole('button',{name:'Preview Markdown',exact:true}).click();
 await expect(page.getByLabel('Reviewed Markdown')).toBeVisible();
 const range=await page.getByLabel('Reviewed Markdown').inputValue();
 assert.ok(!range.includes(beforeExport.sessionId));assert.ok(!range.includes('Conversation: `'));
 assert.ok(!range.includes('The earliest question'));assert.ok(range.includes('exact α'));assert.ok(range.includes('Spoken follow-up'));assert.ok(range.includes('saved-diagram'));
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(range);
 // New chat activity cannot change already-reviewed bytes.
 await page.request.post(new URL('/api/fixture/exportAppend',page.url()).href);
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(range);
 const beforeFailure=await inspect();
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Fixture clipboard denied')}}}));
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect(page.getByRole('alert').filter({hasText:'Fixture clipboard denied'})).toBeVisible();
 assert.deepEqual(await inspect(),beforeFailure,'Denied delivery cannot change canonical or resident messages or activate runtime');
 assert.equal(await preview.inputValue(),range,'Failed delivery preserves the exact reviewed bytes');
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Download Markdown',exact:true}).scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-conversation-export-mobile.png'});
 // A late preview response must not populate another conversation's review.
 let releasePreview,startedPreview=false,finishedPreview=false;
 const held=new Promise(resolve=>{releasePreview=resolve});
 await page.route('**/api/conversation/exports/**',async route=>{startedPreview=true;await held;await route.continue();finishedPreview=true});
 await page.getByRole('button',{name:'Refresh preview',exact:true}).click();await expect.poll(()=>startedPreview).toBe(true);
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Different export chat'}));
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-share'}}));
 releasePreview();await expect.poll(()=>finishedPreview).toBe(true);await page.unroute('**/api/conversation/exports/**');
 await expect(page.getByLabel('Reviewed Markdown')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Preview Markdown',exact:true})).toBeVisible();
 assert.deepEqual(errors,[]);
 console.log('Conversation export browser passed: complete paged native history, exact code, voice and artifact refs, byte-identical copy/download, shared action snapshot, selected ranges, immutable delivery after later activity, stale preview navigation, clipboard failure, mobile layout.');
}finally{try{await browser?.close()}finally{try{await vite?.close()}finally{fixture.kill()}}}
