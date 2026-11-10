import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py','--retention'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.dispatch);
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const created=await action('session.create',{title:'Runtime inspection'});
 const sid=created.state.selectedSessionId;
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this draft');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='Keep this draft');
 await openSettingsPage(page,'runtime');
 await page.getByRole('button',{name:'Inspect document tools',exact:true}).click();
 const host=page.getByRole('region',{name:'App environment'});
 await expect(host.getByText('openpyxl',{exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'Conversation environment'})).toContainText('No ready session runtime.');
 await page.getByRole('button',{name:'Check library imports',exact:true}).click();
 await expect(host).toContainText(/Import (passed|failed|unknown)/);
 const data=await action('runtime.dependencies',{sessionId:sid,verifyImports:true});
 await expect(host.locator('code').first()).toHaveText(data.result.host.python.path);
 const inspection=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.equal(inspection.workerCount,0);assert.deepEqual(inspection.sent,[]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),sid);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep this draft');
 await page.getByRole('heading',{name:'Document and data tools'}).scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/amplifier-artifact-runtime-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await host.locator('code').first().scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/amplifier-artifact-runtime-mobile.png',animations:'disabled'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,hostPathsMatchSharedAction:true,missingWorkerExplicit:true,noWorkerStarts:true,draftPreserved:true,mobileNoOverflow:true}));
}finally{await browser?.close();fixture.kill()}
