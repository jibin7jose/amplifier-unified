import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
const python=process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url));
const path=fileURLToPath(new URL('../../tests/fixtures/question_resume_ui_server.py',import.meta.url));
const reports=[];
for(const choice of ['resume','stop']){
 const child=spawn(python,[path],{stdio:['ignore','pipe','inherit']});
 const stop=ownFixture(child,{label:'Question resume fixture'});
 let browser,failure;
 try{
  const ready=await new Promise((resolve,reject)=>{
   let buffer='';
   const cleanup=()=>{clearTimeout(timer);child.stdout.off('data',data);child.off('exit',exit);child.off('error',error)};
   const error=e=>{cleanup();reject(e)},exit=(code,signal)=>error(Error(`Fixture exited: ${code??signal}`));
   const data=chunk=>{buffer+=chunk;for(let end;(end=buffer.indexOf('\n'))>=0;){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);let value;try{value=JSON.parse(line)}catch{continue}if(value.url){cleanup();resolve(value);return}}if(buffer.length>8000)error(Error('Fixture readiness overflow'))};
   const timer=setTimeout(()=>error(Error('Fixture readiness timed out')),20000);
   child.stdout.on('data',data);child.once('exit',exit);child.once('error',error);
  });
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1100,height:850},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const page=await context.newPage(),errors=[],answerRequests=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()!=='POST'||!request.url().endsWith('/api/actions'))return;const body=request.postDataJSON();if(body.action==='question.answer')answerRequests.push(body)});
  await page.goto(ready.url);await showFixtureConversation(page);
  const card=page.locator(`[data-question-id="${ready.questionId}"]`);
  await expect(card).toBeVisible();
  const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await composer.fill('Keep this private draft');
  const button=card.getByRole('button',{name:choice==='resume'?'Resume the unfinished report':'Keep it stopped',exact:true});
  await button.click();
  await expect(page.getByText(choice==='resume'?'The unfinished report continued. No second Resume message was needed.':'Work remains stopped, as requested.',{exact:true})).toBeVisible();
  await expect(composer).toHaveDraft('Keep this private draft');
  const observe=async()=>await (await page.request.get(ready.url+'/fixture/resume-proof')).json();
  const proof=await observe();
  assert.equal(proof.problem,null);assert.equal(proof.answerSends.length,1);
  assert.equal(proof.peerInputs.length,choice==='resume'?1:0);
  assert.equal(proof.resumes.length,choice==='resume'?1:0);
  assert.equal(proof.taskUnchanged,true);assert.equal(proof.unknownUnchanged,true);
  assert.equal(proof.question.status,'answered');assert.equal(proof.question.delivery.status,'accepted');
  assert.equal(answerRequests.length,1,'One rendered choice sends one answer');
  if(choice==='resume'){
   assert.equal(proof.resumes[0].humanResume.questionAnswer.id,ready.questionId);
   assert.equal(proof.targetMessages.length,1);assert.equal(proof.targetMessages[0].inputOrigin,'peer');
  }
  const duplicate=await page.request.post(ready.url+'/api/actions',{data:answerRequests[0]});
  assert.equal(duplicate.status(),200);assert.equal((await duplicate.json()).duplicate,true);
  const another=await page.request.post(ready.url+'/api/actions',{data:{...answerRequests[0],id:'second-browser-answer'}});
  assert.equal(another.status(),409);
  await page.reload();await expect(composer).toHaveDraft('Keep this private draft');
  assert.deepEqual(await observe(),proof,'Duplicate answer and reload do not repeat or relabel work');
  assert.deepEqual(errors,[]);
  reports.push({choice,ready,proof,errors});
  if(output){await mkdir(output,{recursive:true});await page.screenshot({path:join(output,`question-${choice}.png`),fullPage:true})}
 }catch(error){failure=error;throw error}
 finally{
  await finishFixture({label:'Question resume fixture',originalError:failure,
   close:[['browser',()=>browser?.close()],['evidence',async()=>{if(output){await mkdir(output,{recursive:true});await writeFile(join(output,'question-resume.json'),JSON.stringify({reports,failure:failure?.message},null,2)+'\n')}}]],
   stop:async()=>{const exit=await stop();console.log('Question resume fixture normal exit: '+JSON.stringify(exit))}});
 }
}
console.log('Question resume browser passed: one rendered answer, one guarded peer admission, duplicate/reload no replay, draft and unknown outcomes preserved; negative answer stays stopped.');
