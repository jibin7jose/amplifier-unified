import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {fixturePython} from './fixture-python.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const outputBase=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(outputBase,{recursive:true});
const outputDir=await mkdtemp(join(outputBase,'operation-output-retention-'));
const {fixture,base,stop}=await startFixture({command:fixturePython(join(root,'.venv/bin/python')),
 args:['-u',join(root,'tests/fixtures/computation_ui_server.py'),'--output-retention'],env:process.env,
 tag:'computation-ui',name:'Operation output retention'});
let browser,failure,stderr='';fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
try{
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 const context=await browser.newContext({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const observe=async()=>{const response=await page.request.get(base+'/fixture/output-retention');assert.equal(response.ok(),true);return response.json()};
 const call=async(action,args)=>{const response=await page.request.post(base+'/api/actions',{data:{action,args}});assert.equal(response.ok(),true,await response.text());return (await response.json()).result};
 await page.goto(base);await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 const initial=await observe(),sessionId=initial.sessionId;
 // These are public host actions; the bundled app intentionally has no
 // Operations/Computation panels. Do not simulate a removed UI to claim coverage.
 const kernel=await call('kernels.create',{sessionId,language:'python'});
 const accepted=await call('kernels.execute',{sessionId,kernelId:kernel.id,generation:kernel.generation,code:"print('temporary output evidence'); 42"});
 const read=()=>call('operations.read',{sessionId,id:accepted.id,cursor:0});
 await expect.poll(async()=>(await read()).state,{timeout:15000}).toBe('completed');
 const captured=await read();assert.match(captured.chunks.map(row=>row.text).join(''),/temporary output evidence/);
 assert.equal(captured.evidence.result,'42');assert.equal(captured.outputComplete,true);
 const before=await observe();assert.equal(before.cells.length,1);
 const cell=before.cells[0];assert.equal(cell.outputRetentionDays,30);assert.ok(cell.retainedOutputBytes>0);
 assert.equal(cell.id,accepted.id);
 const expiry=await page.request.post(base+'/fixture/expire-output',{data:{id:cell.id}});
 assert.equal(expiry.ok(),true);assert.deepEqual(await expiry.json(),{status:'expired',removedOutputBytes:cell.retainedOutputBytes});
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 const detail=await read();assert.equal(detail.outputAvailability,'expired');assert.equal(detail.outputComplete,false);
 assert.equal(detail.outputExpiredAt,cell.outputExpiresAt);assert.equal(detail.state,'completed');
 assert.equal(detail.cursorGap,true);assert.deepEqual(detail.chunks,[]);assert.equal(detail.evidence.result,'42');
 assert.deepEqual(await read(),detail);
 const after=await observe();assert.equal(after.runtimeCalls,before.runtimeCalls);assert.equal(after.cells.length,1);
 assert.equal(after.cells[0].state,'completed');assert.equal(after.cells[0].retainedChunks,0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),before.sessionId);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep computation draft');
 assert.deepEqual(errors,[]);
 const result={passed:true,productionBuiltShell:true,publicHttpActions:true,operationPanelCoverage:false,realKernel:true,realJournalExpiry:true,syntheticDueClock:true,providerCalls:false,
  checks:['opt-in captured before real cell execution','expired stdout with result retained','explicit expiry and cursor gap after browser reload',
   'exact repeated reads without runtime dispatch or replay','selection and draft preserved']};
 await writeFile(join(outputDir,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({outputDir,...result}));
}catch(error){failure=error;await writeFile(join(outputDir,'fixture-stderr.log'),stderr);console.error(outputDir);throw error}
finally{await finishFixture({label:'Operation output retention fixture',close:[['Output retention browser',()=>browser?.close()]],stop,originalError:failure})}
