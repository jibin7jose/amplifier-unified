import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/recall_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(ready.url);
 // Memory controls belong to the selected conversation, under Chat > Advanced.
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Advanced',exact:true}).click();
 await page.getByRole('navigation',{name:'Advanced chat options'}).getByRole('button',{name:'Memory & past work',exact:true}).click();
 await expect(page.locator('#panel-title')).toHaveText('Memory & past work');
 await page.getByText('Find past work',{exact:true}).click();
 const recall=page.getByRole('region',{name:'Indexed recall'}),memory=page.getByRole('region',{name:'Saved memory'});
 await recall.getByRole('button',{name:'Update search index'}).click();await expect(recall.getByRole('status')).toContainText('ready');
 await recall.getByLabel('Search conversations, tasks and outputs').fill('paint indigo');await recall.getByRole('button',{name:'Search past work'}).click();
 await expect(recall.getByText('Original design decision',{exact:true})).toBeVisible();await recall.getByRole('button',{name:'Read source',exact:true}).click();
 await expect(recall.getByLabel('Verified source')).toContainText('The approved paint decision is indigo.');
 await memory.getByLabel('Memory note').fill('Use indigo in the report.');await memory.getByRole('button',{name:'Save memory',exact:true}).click();
 await memory.getByRole('button',{name:'Inspect or correct'}).click();await expect(memory.getByRole('button',{name:'Save correction',exact:true})).toBeVisible();await memory.getByLabel('Memory note').fill('Correction: use orange in the report.');await memory.getByRole('button',{name:'Save correction',exact:true}).click();
 await expect(memory.getByText('task · revision 2',{exact:true})).toBeVisible();await expect(memory.getByText('Correction: use orange in the report.',{exact:true})).toBeVisible();await memory.getByRole('button',{name:'Inspect or correct'}).click();
 await memory.getByText('Retained revisions (2, up to 50)',{exact:true}).click();await expect(memory.getByText('Revision 1: Use indigo in the report.',{exact:true})).toBeVisible();
 await page.screenshot({path:'/tmp/amplifier-recall-desktop.png',animations:'disabled'});await page.setViewportSize({width:390,height:844});await memory.scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:'/tmp/amplifier-recall-mobile.png',animations:'disabled'});
 await memory.getByRole('button',{name:'Delete memory',exact:true}).click();await expect(memory.getByRole('button',{name:'Inspect or correct'})).toHaveCount(0);
 await page.reload();await expect(page.locator('#panel-title')).toHaveText('Memory & past work');await memory.getByRole('button',{name:'Inspect saved memory'}).click();await expect(memory.getByRole('button',{name:'Inspect or correct'})).toHaveCount(0);
 const state=await page.request.get(ready.url+'/fixture').then(r=>r.json());assert.equal(state.selected,ready.sessionId);assert.equal(state.draft,'Preserve this draft');assert.equal(state.sourceText,'The approved paint decision is indigo.');assert.deepEqual(state.sent,[]);assert.deepEqual(state.memories,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,indexedSearch:true,sourceRead:true,memoryCorrectionAndDeletion:true,reload:true,draftSelectionOriginalPreserved:true,mobileBounds:true,noModelInvocations:true}));
}finally{await browser?.close();fixture.kill()}
