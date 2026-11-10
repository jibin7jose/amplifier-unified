import {showFixtureConversation} from './browser-chat-pages.mjs';
// Synthetic saved artifacts, real public viewers and controls; no authoring/model claim.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory required');await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;const checks=[],errors=[];
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await action('session.create');await composer.fill('Preserve the private format-review draft');
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();const panel=page.locator('#workspace-canvas');
 const examples=[
  {kind:'text',content:'Exact plain text <tag> & \u2603',selector:'.a-canvas-plain'},
  {kind:'code',content:'const answer = 42; // exact code bytes',selector:'.a-canvas-code'},
  {kind:'markdown',content:'# Saved format review\n\nExact **bold** content.',heading:'Saved format review'},
  {kind:'json',content:'{"label":"Exact structured value","count":7}',text:'Exact structured value'},
  {kind:'html',content:'<!doctype html><style>body{margin:0}pre{width:2400px}</style><h1>Exact isolated HTML</h1><pre>Long content stays inside its iframe</pre>',html:true},
 ];
 for(const example of examples){
  await action('canvas.show',{kind:example.kind,title:'Format '+example.kind,content:example.content});
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.kind)).toBe(example.kind);
  if(example.selector)await expect(panel.locator(example.selector)).toHaveText(example.content);
  if(example.heading)await expect(panel.getByRole('heading',{name:example.heading,exact:true})).toBeVisible();
  if(example.text)await expect(panel.getByText(example.text,{exact:false})).toBeVisible();
  if(example.html){await expect.poll(()=>page.frames().some(f=>f.url().includes('/document'))).toBe(true);const frame=page.frames().find(f=>f.url().includes('/document'));await expect(frame.getByRole('heading',{name:'Exact isolated HTML'})).toBeVisible();assert.equal(await frame.evaluate(()=>{try{void parent.document.body;return false}catch{return true}}),true);}
  const canvas=await page.evaluate(()=>window.amplifier.getState().canvas);assert.equal(canvas.content,example.content);
  for(const width of [1400,390]){await page.setViewportSize({width,height:950});await expect(panel).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await panel.evaluate(el=>{const p=el.getBoundingClientRect();return [...el.querySelectorAll('iframe')].every(f=>{const b=f.getBoundingClientRect();return b.left>=p.left-1&&b.right<=p.right+1})}));}
  checks.push({kind:example.kind,exactSavedSource:true,display:true,outerContainment:true});await page.setViewportSize({width:1400,height:950});
 }
 await action('canvas.show',{kind:'json',title:'Invalid saved JSON',content:'{invalid json'});await expect(panel.getByRole('alert')).toBeVisible();assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.content),'{invalid json');
 await panel.getByRole('button',{name:'Canvas options',exact:true}).click();await panel.getByRole('button',{name:'Source',exact:true}).click();await expect(panel.locator('.a-canvas-code')).toHaveText('{invalid json');checks.push({malformedJson:'Visible error retains exact source and usable Source control'});
 const prior=await page.evaluate(()=>window.amplifier.getState().canvas.id);await assert.rejects(()=>action('canvas.show',{kind:'unsupported-fixture-kind',content:'Do not replace'}));assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),prior);
 await expect(composer).toHaveDraft('Preserve the private format-review draft');const response=await page.request.get(url+'/fixture');assert.deepEqual((await response.json()).sent,[]);assert.deepEqual(errors,[]);
 await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks,unsupportedKindRefusedWithoutReplacement:true,noModelCalls:true,limits:['Saved sources supplied through public API as fixture setup; no source authoring UI claim.','PDF/browser/3D are separate renderers and not covered by these format probes.']},null,2));
}finally{try{await browser?.close()}finally{fixture.kill('SIGTERM');if(fixture.exitCode===null)await new Promise(resolve=>{const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});}}
