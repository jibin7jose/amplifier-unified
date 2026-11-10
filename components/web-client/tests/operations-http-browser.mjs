import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {fixturePython} from './fixture-python.mjs';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';

// Keep the real HTTP/process boundary; the bundled chat has no operation panel.
const {base,stop}=await startFixture({
 command:fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),
 args:['-u',fileURLToPath(new URL('../../tests/fixtures/operations_ui_server.py',import.meta.url))],
 env:process.env,tag:'operations-ui',name:'Operation HTTP',
});
let browser,failure;
try{
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});
 const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const call=async(action,args)=>{
  const response=await page.request.post(base+'/api/actions',{data:{action,args}});
  assert.equal(response.ok(),true,await response.text());return (await response.json()).result;
 };
 await page.goto(base);await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const read=id=>call('operations.read',{sessionId,id});
 const initial=await read('browser-operation');assert.equal(initial.state,'running');
 assert.equal(initial.chunks.map(row=>row.text).join(''),'Build started\n');
 const changed=call('operations.wait',{sessionId,id:initial.id,afterRevision:String(initial.revision),waitMs:5000});
 assert.equal((await page.request.post(base+'/fixture/finish')).ok(),true);
 assert.ok((await changed).revision>initial.revision);
 await expect.poll(async()=>(await read(initial.id)).state).toBe('completed');
 const finished=await read(initial.id);
 assert.equal(finished.chunks.map(row=>row.text).join(''),'Build started\nBuild finished\n');
 const submit={sessionId,requestId:'http-submit',command:"printf 'effect\\n' >> effect.txt; printf 'input ready\\n'; IFS= read -r value; printf 'got:%s\\n' \"$value\""};
 const accepted=await call('operations.submit',submit);assert.equal(accepted.state,'accepted');
 assert.deepEqual(await call('operations.submit',submit),accepted);
 await expect.poll(async()=>(await read(accepted.operationId)).chunks.map(row=>row.text).join('')).toContain('input ready');
 const input={sessionId,id:accepted.operationId,requestId:'http-input',stdin:'http input\n'};
 const written=await call('operations.write',input);assert.equal(written.state,'accepted');
 assert.deepEqual(await call('operations.write',input),written);
 await expect.poll(async()=>(await read(accepted.operationId)).state).toBe('completed');
 const completed=await read(accepted.operationId);
 assert.equal(completed.returncode,0);assert.equal(completed.chunks.map(row=>row.text).join(''),'input ready\ngot:http input\n');
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 assert.deepEqual(await read(accepted.operationId),completed);
 assert.deepEqual(await call('operations.submit',submit),accepted);
 assert.deepEqual(await call('operations.request',{sessionId,requestId:submit.requestId}),accepted);
 const effects=await page.request.get(base+'/fixture/effects');assert.equal(effects.ok(),true);assert.equal((await effects.json()).count,1);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),sessionId);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep this unsent draft');
 assert.deepEqual(errors,[]);
 console.log('Operation HTTP browser passed: observed wait, real submission/stdin, exact retries, one effect, durable reload and preserved draft/selection. No operation-panel UI is exercised.');
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Operation HTTP fixture',close:[['Operation HTTP browser',()=>browser?.close()]],stop,originalError:failure})}
