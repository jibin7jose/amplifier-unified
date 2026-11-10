import {showFixtureConversation} from './browser-chat-pages.mjs';
// Packaged UI and disposable history; no live tools, providers or user data.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 ['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],
 {stdio:['ignore','pipe','pipe']});
let logs='',browser;
fixture.stderr.on('data',chunk=>logs+=chunk);
try{
 const {port,alpha}=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+logs)),30000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs))});
 });
 const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const now=Date.now()/1000;
 const failed=[{id:'not-found',turnId:'turn',kind:'tool',label:'web_fetch',phase:'error',startedAt:now-5,endedAt:now-4,
   input:JSON.stringify({url:'https://example.com/old-page'}),output:JSON.stringify({success:false,error:{message:'HTTP 404: Not Found'}})},
  {id:'script-error',turnId:'turn',kind:'tool',label:'bash',phase:'error',startedAt:now-3,endedAt:now-2,
   input:JSON.stringify({command:'python generated-script.py'}),output:JSON.stringify({success:false,output:{stdout:'',stderr:'TypeError: string indices must be integers',returncode:1}})}];
 const execution={currentTurnId:'turn',turns:[{id:'turn',phase:'running',startedAt:now-6,anchorMessageId:'input'}],nodes:failed};
 await control({op:'patch',sessions:{[alpha]:{status:'working',messages:[{id:'input',role:'user',via:'chat',text:'Explore these sources.',createdAt:now-7}],execution}}});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({extraHTTPHeaders:headers,viewport:{width:1280,height:950}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base);await showFixtureConversation(page,{navPinned:true});
 const chat=page.locator(`.a-nav-chat[data-session-id="${alpha}"]`).first(),group=page.locator('[data-group-id="turn@input"]');
 const header=group.locator('.a-execution-turn-line');
 await expect(chat.locator('.a-navigation-status')).toHaveAttribute('data-kind','working');
 await expect(header).toContainText('Working');
 await expect(header.locator('.a-execution-warning')).toHaveCount(0);
 await header.click();
 await expect(group.locator('[data-node-id] .a-execution-warning')).toHaveCount(2);
 await group.locator('[data-node-id="not-found"] .a-execution-action-line').click();
 await expect(group.locator('[data-node-id="not-found"]')).toContainText('HTTP 404: Not Found');
 // Successful completion does not require guessing whether a later tool fixed
 // the problem. The manager outcome is authoritative, even with errors last.
 execution.turns[0]={...execution.turns[0],phase:'completed',endedAt:now};
 await control({op:'patch',sessions:{[alpha]:{status:'idle',execution}}});
 await expect(header).toContainText('Worked for');
 await expect(header.locator('.a-execution-complete')).toHaveCount(1);
 await expect(chat.locator('.a-navigation-status')).toHaveAttribute('data-kind','idle');
 await expect(group.locator('[data-node-id] .a-execution-warning')).toHaveCount(2);
 await page.reload();
 await expect(header.locator('.a-execution-complete')).toHaveCount(1);
 await expect(chat.locator('.a-navigation-status')).toHaveAttribute('data-kind','idle');
 // A real manager stop still marks the same summary and chat as failed.
 execution.turns[0]={...execution.turns[0],phase:'error'};
 await control({op:'patch',sessions:{[alpha]:{status:'error',error:'The manager turn failed.',execution}}});
 await expect(header.locator('.a-execution-warning')).toHaveCount(1);
 await expect(chat.locator('.a-navigation-status')).toHaveAttribute('data-kind','attention');
 // Pending permissions take precedence over an active, otherwise healthy turn.
 execution.turns[0]={...execution.turns[0],phase:'running',endedAt:undefined};
 await control({op:'patch',sessions:{[alpha]:{status:'working',error:null,execution,approvals:[{id:'approval',status:'pending',title:'Approve this action'}]}}});
 await expect(header).toContainText('Working');
 await expect(header.locator('.a-execution-warning')).toHaveCount(0);
 await expect(chat.locator('.a-navigation-status')).toHaveAttribute('title','Approval requested');
 assert.deepEqual(errors,[]);
 console.log('PASS: tool errors remain visible, active work stays pending, success and reload stay healthy, and manager failures/approvals retain attention.');
}catch(error){console.error(logs);throw error}finally{
 await browser?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
