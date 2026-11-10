import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/file_links_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('File-link fixture did not start')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  let output='';fixture.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/FIXTURE_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(match){clearTimeout(timer);resolve(match[1])}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Show file links');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const plan=page.getByRole('button',{name:'Plan',exact:true});await plan.waitFor();
 await composer.fill('Keep this draft');
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const before=await state(),session=before.sessions.find(row=>row.id===before.selectedSessionId);
 await plan.click();
 await page.waitForFunction(()=>window.amplifier.getState().canvas?.content==='# File opened in Canvas');
 assert.equal(await readComposerDraft(composer),'Keep this draft');
 const opened=await state();assert.equal(opened.selectedSessionId,before.selectedSessionId);
 assert.deepEqual(opened.sessions.find(row=>row.id===session.id).messages,session.messages);
 await page.getByRole('button',{name:'Missing',exact:true}).click();
 await page.getByRole('status').filter({hasText:'This file is unavailable'}).waitFor();
 assert.equal((await state()).canvas.id,opened.canvas.id);
 assert.equal(await readComposerDraft(composer),'Keep this draft');
 assert.equal(await page.getByRole('link',{name:'Web',exact:true}).getAttribute('href'),'https://example.com/a.md');
 assert.equal(await page.locator('.a-assistant pre [data-action="canvas.openFile"]').count(),0);
 assert.deepEqual(errors,[]);
 console.log('File links: real browser click opens Canvas; missing file stays local; draft, history, external links and fenced code preserved.');
}finally{
 await browser?.close();fixture.kill();
}
