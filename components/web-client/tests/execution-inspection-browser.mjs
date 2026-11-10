import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let log='';fixture.stderr.on('data',chunk=>log+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});if(!response.ok)throw Error(await response.text()+' '+log);return response.json()};
await control({op:'inspection'});
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});
try{
 const page=await browser.newPage({viewport:{width:1300,height:950},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:headers}),errors=[],detailReads=[];
 page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/conversation/detail?'))detailReads.push(new URL(r.url()))});
 // Exercise a live selected-chat reader, including its accepted native-window
 // receipt. A fixture that closes this reader cannot recover a terminal refresh.
 const expectCurrentWork=()=>expect.poll(async()=>{
  const value=await page.locator('.a-messages').getAttribute('data-work-projection');
  return value?JSON.parse(value):null;
 }).toMatchObject({status:'ready',current:true,receipt:{scope:'recent-events'}});
 await page.goto(base);await showFixtureConversation(page);const answer=page.locator('[data-message-id="long-answer"]');
 await expectCurrentWork();
 await answer.getByText('COMPLETE-RESPONSE-END',{exact:false}).waitFor();console.log('Assistant body loaded');
 assert.equal(await answer.getByRole('button',{name:'Show full text'}).count(),0);
 assert.equal(detailReads.filter(url=>url.searchParams.get('id')==='long-answer').length,2);
 assert.equal(detailReads.filter(url=>url.searchParams.get('part')==='nodes').length,0);
 for(const group of await page.locator('button.a-execution-turn-line').all())await group.click();
 const model=page.locator('[data-group-id="inspect@question"] [data-kind="llm"]'),modelLine=model.locator('.a-execution-line');
 await expect(modelLine).toContainText('running');
 assert.equal(await model.locator('button.a-execution-action-line').count(),1);
 assert.equal(await model.locator('.a-execution-body').count(),0);
 const firstTimer=await modelLine.innerText();await expect.poll(()=>modelLine.innerText()).not.toBe(firstTimer);
 console.log('Live model timing checked');const turn=page.locator('[data-group-id="inspect@long-answer"]');
 let failOutput=true;await page.route('**/api/conversation/detail?**',route=>{if(failOutput&&new URL(route.request().url()).searchParams.get('field')==='output'){failOutput=false;return route.fulfill({status:503,json:{error:'Temporary result load failure'}})}return route.continue()});
 const tool=turn.locator('[data-kind="tool"]');
 assert.equal(await tool.locator('.a-execution-body').count(),0);
 await tool.locator('button.a-execution-action-line').click();
 await tool.getByRole('button',{name:'Retry result',exact:true}).waitFor();console.log('Retry control visible');
 assert.equal(await tool.getByRole('button',{name:'Copy file content',exact:true}).isDisabled(),true);
 await tool.getByRole('button',{name:'Retry result',exact:true}).click();
 await expect(tool.getByRole('button',{name:'Copy file content',exact:true})).toBeEnabled();
 assert.match(await tool.innerText(),/Bearer owned-log-value/);
 assert.equal(await tool.locator('details').count(),0);
 const fileContent=tool.locator('.a-execution-payload').filter({hasText:'File content'}),fullResult='Result line.\n'.repeat(2000)+'RESULT-END';
 assert.equal(await fileContent.locator('pre').innerText(),Array(10).fill('Result line.').join('\n')+'\n…');
 await expect(fileContent.getByText('Showing a preview.',{exact:true})).toBeVisible();
 await tool.getByRole('button',{name:'Copy file content',exact:true}).click();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),fullResult,'Copy includes the original complete result while its display is shortened');
 await tool.getByRole('button',{name:'Show all 2001 lines',exact:true}).click();
 assert.equal(await fileContent.locator('pre').innerText(),fullResult);
 await expect(fileContent.getByText('Showing a preview.',{exact:true})).toHaveCount(0);
 assert.equal(await tool.getByRole('link',{name:'https://example.com/artifact',exact:true}).getAttribute('href'),'https://example.com/artifact');
 await tool.getByRole('button',{name:'Copy file content',exact:true}).click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),fullResult);
 // A browser clipboard denial must retain the readable full result and offer
 // an honest fallback; it must not refetch the record or claim another copy.
 await page.evaluate(()=>{window.savedClipboardWrite=navigator.clipboard.writeText.bind(navigator.clipboard);navigator.clipboard.writeText=async()=>{throw new DOMException('Fixture clipboard denied','NotAllowedError')}});
 await tool.getByRole('button',{name:'Copy file content',exact:true}).click();
 await expect(tool.getByRole('status')).toHaveText('Select the text to copy it manually.');
 await expect(tool.getByRole('button',{name:'Copy file content',exact:true})).toHaveText('Copy');
 assert.equal(await fileContent.locator('pre').innerText(),fullResult);
 await page.evaluate(()=>{navigator.clipboard.writeText=window.savedClipboardWrite;delete window.savedClipboardWrite});
 await tool.getByRole('button',{name:'Copy file content',exact:true}).click();
 await expect(tool.getByRole('status')).toHaveText('Copied');
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),fullResult);
 assert.equal(detailReads.filter(url=>url.searchParams.get('field')==='output').length,2);
 await control({op:'inspection-finish'});await expectCurrentWork();await expect(modelLine).toContainText('120 tokens');
 assert.match(await page.locator('[data-group-id="inspect@question"] > .a-execution-turn-line').innerText(),/120 tokens · \$0.004/);
 const finalTimer=await modelLine.innerText();await new Promise(resolve=>setTimeout(resolve,1200));assert.equal(await modelLine.innerText(),finalTimer);
 let failDetail=true;await page.route('**/api/conversation/detail?**',route=>{if(failDetail&&new URL(route.request().url()).searchParams.get('id')==='long-answer'){failDetail=false;return route.fulfill({status:503,json:{error:'Fixture detail temporarily unavailable'}})}return route.continue()});
 await page.reload();await expectCurrentWork();await model.waitFor();assert.equal(await modelLine.innerText(),finalTimer);
 await answer.scrollIntoViewIfNeeded();await page.getByRole('button',{name:'Retry loading full text'}).click();await page.getByText('COMPLETE-RESPONSE-END',{exact:false}).waitFor();
 await tool.getByRole('link',{name:'https://example.com/artifact',exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('Canonical owner content loads once with retry and full-copy support; clipboard denial retains selectable full content and explicit retry succeeds without another detail read; long fences preview 10 lines plus an explicit ellipsis; model one-liners stay visible; live timing and usage settle across reload.');
}catch(error){console.error(log);throw error}finally{await browser.close();fixture.kill()}
