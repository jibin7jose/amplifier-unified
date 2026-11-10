import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)),python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python';
const directory=await mkdtemp(join(tmpdir(),'canvas-presentation-')),html=join(directory,'app.html'),calls=join(directory,'calls.jsonl');
const built=await build({stdin:{contents:`import {App} from '@modelcontextprotocol/ext-apps';const app=new App({name:'Entity review',version:'1.0.0'},{});app.ontoolresult=result=>document.querySelector('#value').textContent=result.structuredContent.entity+':'+result.structuredContent.value;await app.connect();document.body.dataset.booted='true';`,resolveDir:process.cwd(),sourcefile:'presentation.js'},bundle:true,write:false,format:'esm'});
await writeFile(html,`<!doctype html><h1>Entity dashboard</h1><output id="value"></output><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
const fixture=spawn(python,[root+'tests/fixtures/canvas_restart_ui_server.py',directory,'0'],{stdio:['ignore','pipe','inherit']});let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});const page=await browser.newPage({viewport:{width:1450,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const act=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const operation=async(name,args)=>{const receipt=await act(name,args);if(!receipt.operationId)return receipt.result;for(let i=0;i<200;i++){const row=await(await page.request.get(url+'/api/smart-tools/operations/'+receipt.operationId)).json();if(row.status==='completed')return {id:receipt.operationId,...row.result};if(row.status==='failed')throw Error(row.error);await page.waitForTimeout(50)}throw Error('Timed out')};
 await act('session.create');await act('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,draft:'Keep my unfinished question'}});
 await operation('smartTools.configure',{id:'fixture',name:'Entity dashboard',command:python,args:[root+'tests/fixtures/canvas_presentation_mcp_server.py',html,calls]});await operation('smartTools.connect',{id:'fixture'});
 const present=async(tool,entity,value)=>{const call=await operation('smartTools.call',{id:'fixture',name:tool,arguments:{entity_id:entity,value}});return operation('smartTools.open',{id:'fixture',tool,operationId:call.id})};
 const first=await present('dashboard_read','alpha',1),frame=page.frameLocator('.a-mcp-app-viewer iframe');await expect(frame.locator('#value')).toHaveText('alpha:1');
 const firstLabel='['+first.canvasId.slice(0,8)+']';
 await expect(page.getByRole('tab').filter({hasText:firstLabel})).toBeVisible();
 const second=await present('dashboard_detail','alpha',2);assert.equal(second.canvasId,first.canvasId);assert.equal(second.revision,2);await expect(frame.locator('#value')).toHaveText('alpha:2');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvasArtifacts.length),1);
 await expect(page.getByRole('tab').filter({hasText:firstLabel})).toHaveCount(1);
 const other=await present('dashboard_read','beta',3);assert.notEqual(other.canvasId,first.canvasId);assert.equal(await page.evaluate(()=>window.amplifier.getState().canvasArtifacts.length),2);
 const labels=[firstLabel,'['+other.canvasId.slice(0,8)+']'];
 for(const label of labels){
  const tab=page.getByRole('tab').filter({hasText:label});await expect(tab).toBeVisible();
  assert.ok(await tab.locator('span').evaluate((span,label)=>{const node=span.firstChild,range=document.createRange();range.setStart(node,0);range.setEnd(node,label.length);const text=range.getBoundingClientRect(),box=span.getBoundingClientRect();return text.left>=box.left&&text.right<=box.right},label),'The distinguishing prefix is visible before ellipsis');
 }
 await page.screenshot({path:'/tmp/canvas-presentation-labels.png'});
 await act('canvas.select',{id:first.canvasId,version:1});await expect(frame.locator('#value')).toHaveText('alpha:1');
 await page.getByRole('combobox',{name:'Artifact version'}).selectOption('latest');await expect(frame.locator('#value')).toHaveText('alpha:2');
 await page.reload();await expect(frame.locator('#value')).toHaveText('alpha:2');await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished question');
 for(const label of labels)await expect(page.getByRole('tab').filter({hasText:label})).toBeVisible();
 assert.equal((await readFile(calls,'utf8')).trim().split('\n').length,3);assert.deepEqual(errors,[]);
 console.log('Explicit MCP dashboard identity passed: two methods one tab, independent entity separate, immutable old version and reload, retained draft, exactly three explicit reads and no replay.');
}finally{await browser?.close();if(fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');await exited}await rm(directory,{recursive:true,force:true})}
