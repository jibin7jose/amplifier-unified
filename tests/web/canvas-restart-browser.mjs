import {fixturePython} from './fixture-python.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const directory=await mkdtemp(join(tmpdir(),'canvas-restart-'));
let fixture,browser;
async function start(port=0){
 fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/canvas_restart_ui_server.py',directory,String(port)],{stdio:['ignore','pipe','inherit']});
 return new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timeout);resolve(data.url)}}catch{}}});
 });
}
async function stop(){if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');await exited}fixture=null}
try{
 const url=await start();
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create');
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'Restored notes',content:'# Readable after restart\n\nThe source stays with its saved artifact.'});
 await expect(page.getByRole('heading',{name:'Readable after restart',exact:true})).toBeVisible();
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep the unsent draft too');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='Keep the unsent draft too');
 // The browser updates optimistically before its debounced autosave. Confirm
 // the host has acknowledged the draft before interrupting that host.
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 await expect.poll(async()=>{
  const response=await page.request.get(url+'/api/state?clientId='+encodeURIComponent(clientId));
  return (await response.json()).view?.draft;
 }).toBe('Keep the unsent draft too');
 const before=await page.evaluate(()=>{const s=window.amplifier.getState();return {artifact:s.canvas.id,session:s.selectedSessionId,host:s.client.hostInstanceId,source:s.canvas.content}});
 await stop();
 await start(Number(new URL(url).port));
 await page.reload();
 await expect(page.getByRole('heading',{name:'Readable after restart',exact:true})).toBeVisible();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep the unsent draft too');
 const after=await page.evaluate(()=>{const s=window.amplifier.getState();return {artifact:s.canvas.id,session:s.selectedSessionId,host:s.client.hostInstanceId,source:s.canvas.content,reference:s.canvas.contentResource}});
 assert.equal(after.artifact,before.artifact);assert.equal(after.session,before.session);
 assert.equal(after.source,before.source);assert.notEqual(after.host,before.host);assert.equal(after.reference,undefined);
 await page.getByRole('button',{name:'Source',exact:true}).click();
 await expect(page.locator('.a-canvas-code')).toContainText('# Readable after restart');
 assert.deepEqual(errors,[]);
 console.log('Canvas restart browser passed: real host restart, visible saved Markdown, source controls, artifact/session identity and unsent draft preserved.');
}finally{await browser?.close();await stop();await rm(directory,{recursive:true,force:true})}
