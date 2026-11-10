import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {openSettingsPage} from './browser-settings.mjs';
const output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||await mkdtemp(join(tmpdir(),'unified-observability-'));
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
const started=Date.now();
let log='',browser;fixture.stderr.on('data',chunk=>log+=chunk);
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
 const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200,await response.clone().text());return response.json()};
 await control({op:'inspection'});await control({op:'inspection-actions'});await control({op:'inspection-finish'});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1300,height:1000},extraHTTPHeaders:headers,permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(),errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',request=>{if(request.method()==='POST'){try{const body=request.postDataJSON();if((body.name||body.action)==='capacity.read'||body.args?.name==='capacity.read')requests.push(body)}catch{}}});
 await page.goto(base);await showFixtureConversation(page);await page.locator('#amp-one').waitFor();
 await openSettingsPage(page,'appearance');
 const prefs=page.locator('.a-observability-preferences');await prefs.locator('summary').click();
 await prefs.getByLabel('Individual steps',{exact:true}).uncheck();await prefs.getByLabel('Work summaries',{exact:true}).check();await prefs.getByLabel('Compact conversation status',{exact:true}).check();
 // Save each selection through the real shared shell action; disabled state fences racing writes.
 await prefs.getByLabel('Cost',{exact:true}).uncheck();
 await expect(prefs.getByLabel('Cost',{exact:true})).not.toBeChecked();await expect(prefs.getByLabel('Cost',{exact:true})).toBeEnabled();
 await page.reload();await openSettingsPage(page,'appearance');await prefs.locator('summary').click();
 await expect(prefs.getByLabel('Individual steps',{exact:true})).not.toBeChecked();await expect(prefs.getByLabel('Work summaries',{exact:true})).toBeChecked();await expect(prefs.getByLabel('Cost',{exact:true})).not.toBeChecked();
 await prefs.getByLabel('Individual steps',{exact:true}).check();await expect(prefs.getByLabel('Individual steps',{exact:true})).toBeEnabled();
 await page.locator('[data-part=dialog]:visible').getByRole('button',{name:/^Close (panel|settings)$/i}).click();
 const status=page.getByRole('region',{name:'Conversation status',exact:true});await expect(status).toBeVisible();assert.equal(requests.length,0,'status strip cannot silently load usage/history');
 const readStarted=Date.now();await status.getByRole('button',{name:'Read usage',exact:true}).click();await status.locator('summary').first().click();
 const totals=status.getByRole('region',{name:'Conversation totals',exact:true});await expect(totals).toBeVisible();assert.equal(requests.length,1,'explicit usage request is observed');const readMs=Date.now()-readStarted;await expect(totals.getByText('Session cost (USD)',{exact:true})).toHaveCount(0);
 await expect(status).toContainText('Cumulative usage is not context size');
 const downloadEvent=page.waitForEvent('download');await totals.getByRole('button',{name:'Download selected metrics'}).click();const download=await downloadEvent;const path=join(output,'conversation-metrics.json');await download.saveAs(path);const data=JSON.parse(await readFile(path,'utf8'));
 assert.equal(data.schema,'execution-metrics-v1');assert.equal(data.scope,'conversation');assert.ok(!('costUsd' in data.metrics));assert.ok(data.metrics.grossTotalTokens.value>0);assert.ok(!JSON.stringify(data).includes('raw_response'));
 await totals.getByRole('button',{name:'Copy selected metrics'}).click();const copied=await page.evaluate(()=>navigator.clipboard.readText());assert.deepEqual(JSON.parse(copied),data);
 await status.locator('summary').first().click();
 // Optional status remains a snapshot even when another render occurs.
 const readCount=requests.length;await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{draft:'Keep this draft'}}));await expect(status.getByRole('button',{name:'Refresh usage'})).toBeVisible();assert.equal(requests.length,readCount);
 for(const id of ['inspect@question','inspect@long-answer']){const group=page.locator(`[data-group-id="${id}"] > button.a-execution-turn-line`);await group.waitFor();if(await group.getAttribute('aria-expanded')==='false')await group.click()}
 const model=page.locator('[data-kind="llm"]').first();await model.locator(':scope > button').click();await expect(model.getByRole('region',{name:'Model call usage'})).toBeVisible();await expect(model.getByText('Call cost (USD)',{exact:true})).toHaveCount(0);
 await model.scrollIntoViewIfNeeded();await page.screenshot({path:join(output,'metrics-desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await model.scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(output,'metrics-mobile.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);await writeFile(join(output,'result.json'),JSON.stringify({passed:true,source:'production UI + synthetic canonical events',elapsedMs:Date.now()-started,readMs,requests:requests.length,exportedMetrics:Object.keys(data.metrics),errors},null,2)+'\n');
 console.log('Observability passed: persisted independent preferences, selected-only copy/export, optional no-poll snapshot, actual model rows, narrow layout.');
}finally{await writeFile(join(output,'fixture.log'),log);await browser?.close();fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})})}
