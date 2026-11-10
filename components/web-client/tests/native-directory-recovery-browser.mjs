import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 ['-u',root+'tests/fixtures/chat_library_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const port=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),30000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/\{"port":\s*(\d+)\}/);
   if(match){clearTimeout(timer);resolve(Number(match[1]))}});
 });
 const base=`http://127.0.0.1:${port}`;
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?
  ['--no-sandbox','--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const info=async()=>{const response=await page.request.get(base+'/api/fixture/directory');assert.ok(response.ok());return response.json()};
 const change=async data=>{const response=await page.request.post(base+'/api/fixture/directory',{data});assert.ok(response.ok());return response.json()};
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const selected=()=>page.evaluate(()=>{
  const state=window.amplifier.getState();return (state.sessions||[]).find(row=>row.id===state.selectedSessionId);
 });
 await page.goto(base);
 const health=await (await page.request.get(base+'/api/health')).json();
 const before=await info(),identity=before.initialSession;
 await expect.poll(async()=>(await selected())?.id).toBe(identity);
 await expect(page.getByText('Saved response',{exact:true})).toBeVisible();
 const draft=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await draft.fill('Preserve this native chat draft through recovery.');
 await action('view.update',{sessionId:identity,patch:{draft:'Preserve this native chat draft through recovery.'}});
 await action('canvas.show',{kind:'text',title:'Retained recovery Canvas',content:'Keep this selected artifact.'});
 await expect(page.getByText('Keep this selected artifact.',{exact:true})).toBeVisible();
 const canvasBefore=await page.evaluate(()=>window.amplifier.getState().canvas);
 assert.ok(canvasBefore.id,'selected Canvas has a durable identity');
 const restart=await page.request.post(base+'/api/fixture/restart',{data:{}});assert.ok(restart.ok());
 await expect.poll(async()=>{try{return (await info()).generation}catch{return 0}},{timeout:30000}).toBeGreaterThan(before.generation);
 await page.reload();
 await expect.poll(async()=>(await selected())?.id).toBe(identity);
 await expect(draft).toHaveDraft('Preserve this native chat draft through recovery.');
 await expect(page.getByText('Keep this selected artifact.',{exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),canvasBefore.id);

 const unavailable=await change({available:false});
 console.log(JSON.stringify({step:'workspace removed',...unavailable}));
 await expect.poll(async()=>(await selected())?.workspaceAvailable).toBe(false);
 await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeDisabled();
 await expect(draft).toHaveDraft('Preserve this native chat draft through recovery.');
 await change({available:true});
 await expect.poll(async()=>(await selected())?.workspaceAvailable).toBe(true);
 await expect.poll(async()=>Boolean((await selected())?.historyReadOnlyReason)).toBe(false);
 await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeEnabled();

 const title='Native title changed during warm recovery';
 await change({title});
 await expect.poll(async()=>(await selected())?.title).toBe(title);
 await expect(draft).toHaveDraft('Preserve this native chat draft through recovery.');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),canvasBefore.id);
 const after=await info();
 assert.equal(after.rootCount,before.rootCount);
 assert.ok(after.residentCount<10,'native recovery does not admit the dormant library');
 assert.deepEqual(after.runtimeStarts,[]);assert.deepEqual(after.runtimeSends,[]);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',version:health.version,generation:after.generation,rootCount:after.rootCount,
  residentCount:after.residentCount,selectedIdentity:identity,draftPreserved:true,canvasPreserved:true,
  availabilityRecovered:true,changedCanonicalTitle:true,runtimeStarts:0,runtimeSends:0}));
}catch(error){await page?.screenshot({path:'/tmp/native-directory-recovery-failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}}
