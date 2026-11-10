// Actual native file read refusal and explicit public retry in disposable DTU state.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row)}}catch{}})});
 const origin=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token'};
 const control=async body=>{const response=await fetch(origin+'/api/fixture/control',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 await control({op:'native-history',unreadable:true});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers}),actions=[],errors=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});
 await page.goto(origin);const pane=page.getByRole('log',{name:'Conversation messages',exact:true}),failure=pane.getByRole('alert');
 await expect(failure).toContainText(/could not|couldn't|cannot|unable|unreadable/i,{timeout:15000});await expect(failure.getByRole('button',{name:'Try again',exact:true})).toBeEnabled();
 const baseline=await control({op:'inspect'}),beforeSelect=actions.filter(row=>row.action==='session.select').length;
 // Unrelated publications must not flicker the failed read into another attempt.
 for(let i=0;i<3;i++){await control({op:'patch',sessions:{[ready.beta]:{title:'Unrelated title '+i}}});await expect(failure).toBeVisible();await expect(pane.getByText('Loading conversation…',{exact:true})).toHaveCount(0)}
 assert.equal(actions.filter(row=>row.action==='session.select').length,beforeSelect);
 const fileReceipt=await control({op:'native-readable'});assert.deepEqual(fileReceipt.current,fileReceipt.original,'Failed read preserves original transcript and metadata bytes');
 await failure.getByRole('button',{name:'Try again',exact:true}).click();
 await expect(pane.locator('[data-message-id]')).not.toHaveCount(0,{timeout:15000});await expect(failure).toHaveCount(0);await expect(pane).toContainText('Saved message 304');
 const retries=actions.filter(row=>row.action==='session.select').slice(beforeSelect);assert.equal(retries.length,1);assert.equal(retries[0].args.id,'paging-fixture');
 const after=await control({op:'inspect'});assert.equal(after.runtimeStarts,baseline.runtimeStarts);assert.equal(after.runtimeSends,0);assert.deepEqual(after.runtimeStops,[]);assert.ok(await pane.locator('[data-message-id]').count()<=100,'Initial retry loads a bounded newest page');assert.deepEqual((await control({op:'native-readable'})).current,fileReceipt.original);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,actualFileReadDenied:true,stableFailure:true,explicitOwnerRetry:true,boundedNewestPage:true,immutableFiles:true,runtimeSends:0,runtimeStarts:after.runtimeStarts-baseline.runtimeStarts}));
}catch(error){console.error(error);throw error}
finally{await browser?.close();if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');await done.catch(()=>{})}}
