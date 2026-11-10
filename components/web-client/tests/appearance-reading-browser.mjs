// Public appearance changes must preserve the message the user is reading.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('An owned artifact directory is required');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='';fixture.stderr.on('data',chunk=>logs+=chunk);
const checks=[],errors=[],commands=[];
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),20000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row)}}catch{}})});
 const url='http://127.0.0.1:'+ready.port,headers={Authorization:'Bearer fixture-detail-token'};
 const control=async body=>{const response=await fetch(url+'/api/fixture/control',{method:'POST',headers:{...headers,'content-type':'application/json'},body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 await control({op:'heavy',messages:40,otherMessages:2,chars:600});
 const before=await control({op:'inspect-work-source'});
 assert.ok(Array.isArray(before.sessions),'Read-only canonical oracle is present');
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const body=request.postDataJSON();commands.push(body?.action==='shell.command'?(body.args.command||body.args):body)}});
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();
 await composer.fill('Keep my appearance reading draft');
 const pane=page.locator('.a-messages');await expect(pane).toBeVisible();
 // One real wheel gesture expresses scrollback intent; the anchor is measured
 // from the actual rendered message list, not set through the scroll controller.
 await pane.hover();await page.mouse.wheel(0,-2200);
 await expect.poll(()=>pane.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeGreaterThan(1000);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const anchor=await pane.evaluate(el=>{const top=el.getBoundingClientRect().top;const node=[...el.querySelectorAll('[data-message-id]')].find(row=>row.getBoundingClientRect().bottom>top+20);return {id:node?.dataset.messageId,offset:node.getBoundingClientRect().top-top}});
 assert.ok(anchor.id);const anchorElement=page.locator(`[data-message-id="${anchor.id}"]`);
 for(const value of ['conversation','work','balanced']){
  await openSettingsPage(page,'appearance');
  const layout=page.getByLabel('Layout',{exact:true});if(!await layout.isVisible())await page.getByText('Layout & background',{exact:true}).click();
  await layout.selectOption(value);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState()?.effectiveComposition.presentation.layout)).toBe(value);
  await expect(layout).toBeEnabled();
  await page.locator('button[aria-label="Close settings"]:visible,button[aria-label="Close panel"]:visible').last().click();
  await expect(page.locator('.a-settings-experience')).toHaveCount(0);await expect(page.locator('#amp-one')).toHaveAttribute('data-layout',value);
  await expect(composer).toHaveDraft('Keep my appearance reading draft');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(ready.alpha);
  await expect.poll(()=>anchorElement.evaluate(node=>{const pane=node.closest('.a-messages');return node.getBoundingClientRect().top-pane.getBoundingClientRect().top})).toBeCloseTo(anchor.offset,0);
  checks.push({layout:value,messageId:anchor.id,offset:await anchorElement.evaluate(node=>node.getBoundingClientRect().top-node.closest('.a-messages').getBoundingClientRect().top)});
 }
 assert.deepEqual(await control({op:'inspect-work-source'}),before,'Appearance changes leave canonical messages, usage, execution and runtime counts unchanged');
 assert.deepEqual(commands.filter(row=>['conversation.send','session.send'].includes(row.action)),[]);
 assert.deepEqual(errors,[]);
 await page.screenshot({path:output+'/appearance-reading.png'});
 await writeFile(output+'/observations.json',JSON.stringify({passed:true,anchor,checks,limits:['Desktop pointer, three declared layouts, one saved reading anchor and private draft.','Synthetic history/runtime; no provider call, narrow-layout or universal scroll-position claim.']},null,2)+'\n');
}catch(error){await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();if(fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{const [code,signal]=await exited;assert.equal(code,0);assert.equal(signal,null)}finally{clearTimeout(timer)}}await writeFile(output+'/fixture.log',logs)}
