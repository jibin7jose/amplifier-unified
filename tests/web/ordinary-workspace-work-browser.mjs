import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-ordinary-workspace-work';
await mkdir(output,{recursive:true});
await writeFile(output+'/receipt.json',JSON.stringify({passed:false,status:'running'})+'\n');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[
 '-u',root+'tests/fixtures/ordinary_workspace_work_ui_server.py'
],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);
  fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});
 page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],actions=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 const inspect=()=>page.request.get(url+'/fixture/ordinary-work').then(r=>r.json());
 const seed=path=>page.request.post(url+'/fixture/ordinary-work/'+path).then(async r=>{assert.equal(r.ok(),true);return r.json()});
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();await composer.fill('Keep the ordinary inspection draft');
 const initial=await inspect();
 // Public breadcrumb -> Details -> Workspace work. No API preopened panel or surface.
 await page.locator('[data-part="header"] .a-work-crumb:not(.a-work-home-crumb)').click();
 const workspaceView=page.getByRole('group',{name:'Workspace view',exact:true});
 await expect(workspaceView).toBeVisible();
 await workspaceView.getByRole('button',{name:'Details',exact:true}).click();
 await page.locator('.a-workspace-work-entry > summary').click();
 const picker=page.getByRole('combobox',{name:'Review from chat',exact:true});
 await expect(picker).toHaveValue(initial.selected);
 const related=page.getByRole('region',{name:'Workspace work overview',exact:true});
 await expect(related.getByRole('region',{name:'Working in this workspace',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),initial.selected);

 // File intent is an advisory service write; exact project bytes and execution
 // placement must remain unchanged even when the declared path does not exist.
 const filesBefore=await inspect(),awareness=related.getByRole('region',{name:'Working in this workspace',exact:true});
 await awareness.getByText('Declare files for this chat',{exact:true}).click();
 await awareness.getByLabel('Planned file or folder paths',{exact:true}).fill('file.txt\nnot-created/');
 await awareness.getByRole('button',{name:'Save file declaration',exact:true}).click();
 await expect(awareness).toContainText('File declaration saved. It does not lock or change files.');
 const filesAfter=await inspect();
 assert.deepEqual(filesAfter.files,filesBefore.files);assert.deepEqual(filesAfter.placement,filesBefore.placement);
 assert.deepEqual(filesAfter.runtimeCalls,filesBefore.runtimeCalls);
 const fileAction=actions.filter(row=>row.action==='coordination.intent').at(-1);
 assert.equal(fileAction.args.sessionId,initial.selected);assert.equal(fileAction.args.paths.length,2);

 // Hold the actual read response. Same-owner refresh preserves the mounted
 // editor and its private draft; changing owners rejects the held response.
 let holdNext=false,failNext=false,releaseContext;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(failNext&&body?.action==='coordination.context'&&body.args.sessionId===initial.selected){
   failNext=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture scope read unavailable'})});return;
  }
  if(holdNext&&body?.action==='coordination.context'&&body.args.sessionId===initial.selected){
   holdNext=false;const response=await route.fetch();
   releaseContext=()=>route.fulfill({response});return;
  }
  await route.continue();
 });
 const declaration=awareness.getByLabel('Planned file or folder paths',{exact:true});
 await declaration.fill('Unsubmitted private declaration');
 const mountedEditor=await declaration.elementHandle();
 holdNext=true;await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect.poll(()=>!!releaseContext).toBe(true);await expect(related).toHaveAttribute('inert','');
 assert.equal(await mountedEditor.evaluate(el=>el.isConnected),true);
 await expect(declaration).toBeVisible();await expect(declaration).toHaveValue('Unsubmitted private declaration');
 await releaseContext();releaseContext=null;
 await expect(related).not.toHaveAttribute('inert');
 await expect(declaration).toHaveValue('Unsubmitted private declaration');
 await expect(related.getByRole('button',{name:'Refresh workspace work',exact:true})).toBeFocused();
 holdNext=true;await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect.poll(()=>!!releaseContext).toBe(true);
 await picker.selectOption(initial.other);
 await expect(page.getByText('Reviewing from Settled helper.',{exact:true})).toBeVisible();
 await expect(related.getByRole('region',{name:'Working in this workspace',exact:true})).toBeVisible();
 await releaseContext();releaseContext=null;
 await expect(picker).toHaveValue(initial.other);
 assert.equal(await mountedEditor.evaluate(el=>el.isConnected),false);
 await expect(related).not.toContainText('Unsubmitted private declaration');
 await picker.selectOption(initial.selected);
 await awareness.getByText('Declare files for this chat',{exact:true}).click();
 await expect(declaration).toHaveValue('Unsubmitted private declaration');
 await awareness.getByRole('button',{name:'Discard draft and use latest',exact:true}).click();

 // The original is longer than the UI read budget. Read the real retained
 // message and verify both the bounded result and the unchanged full source.
 await related.getByText('Exact requests, messages and saved results',{exact:true}).click();
 await related.locator('[data-request-id="ordinary-long-message"]').getByRole('button',{name:'Open submitted message',exact:true}).click();
 const original=related.getByRole('article',{name:'Original message',exact:true});
 await expect(original).toContainText('Bounded excerpt; original retained.');
 assert.equal((await original.locator('p').textContent()).length,4000);
 assert.equal(await original.locator('p').textContent(),initial.originalText.slice(0,4000));
 const messageRead=actions.filter(row=>row.action==='coordination.read').at(-1);
 assert.equal(messageRead.args.textLimit,4000);assert.equal(messageRead.args.sessionId,initial.other);
 assert.equal((await inspect()).sourceMessage.text,initial.originalText);
 await original.getByRole('button',{name:'Close original message',exact:true}).click();

 // An ordinary visitor can see the exact assignment authority and revision in
 // its existing details disclosure; controls still bind that observed revision.
 let assignment=related.locator('.a-assignment');
 await assignment.getByText('Shared tasks and assignment',{exact:true}).click();
 await assignment.getByRole('button',{name:'Start assignment',exact:true}).click();
 await expect(assignment).toContainText('Assignment active');
 const member=await seed('member');
 await assignment.getByRole('button',{name:'Refresh assignment',exact:true}).click();
 await expect(assignment).toContainText('2 members');
 await expect(assignment).toContainText('Total tokens: 12 (partial)');
 await expect(assignment).toContainText('Cost (USD): Unknown');
 await assignment.getByText('Membership and accounting details',{exact:true}).click();
 await expect(assignment).toContainText('Assignment: '+member.assignmentId+' · Revision: '+member.revision);
 await expect(assignment).toContainText('Commissioning owner: This conversation · '+initial.selected);
 await expect(assignment).toContainText('provider-internal retries and external work may be unreported');
 await assignment.getByText('Assignment limits',{exact:true}).click();
 await assignment.getByLabel('Total token limit',{exact:true}).fill('1000');
 await assignment.getByRole('button',{name:'Save assignment limits',exact:true}).click();
 await expect(assignment).toContainText('Revision: '+(member.revision+1));
 const limits=actions.filter(row=>row.action==='assignment.set').at(-1);
 assert.equal(limits.args.sessionId,initial.selected);assert.equal(limits.args.expectedRevision,member.revision);
 await assignment.getByText('Shared task list',{exact:true}).click();
 await expect(assignment).toContainText('No shared tasks yet.');
 await expect(assignment.getByText('Add shared task',{exact:true})).toBeVisible();

 // Review a settled member without selecting that chat. Detachment removes its
 // authority but keeps its membership evidence and attributed incomplete usage.
 await picker.selectOption(initial.other);
 assignment=related.locator('.a-assignment');
 await assignment.getByText('Shared tasks and assignment',{exact:true}).click();
 await expect(assignment).toContainText('Assignment active');
 await expect(assignment.getByRole('button',{name:'Stop assignment',exact:true})).toHaveCount(0);
 await assignment.getByText('Membership and accounting details',{exact:true}).click();
 await assignment.getByRole('button',{name:'Detach this settled chat',exact:true}).click();
 await expect(assignment).toContainText('This chat has no assignment.');
 const detach=actions.filter(row=>row.action==='assignment.detach').at(-1);
 assert.equal(detach.args.sessionId,initial.other);assert.equal(detach.args.expectedRevision,member.revision+1);
 await picker.selectOption(initial.selected);
 assignment=related.locator('.a-assignment');
 await assignment.getByText('Shared tasks and assignment',{exact:true}).click();
 await expect(assignment).toContainText('1 members');
 await expect(assignment).toContainText('Total tokens: 12 (partial)');
 await assignment.getByText('Membership and accounting details',{exact:true}).click();
 const detached=await inspect();
 assert.equal(detached.assignment.members[initial.other].status,'detached');
 await expect(assignment.locator('pre').first()).toContainText('"status": "detached"');
 assert.equal(detached.assignmentUsage.calls,2);assert.equal(detached.assignmentUsage.metrics.grossTotalTokens.status,'partial');

 // Stop followed by explicit human resume preserves the shared work rather
 // than forcing users to detach chats or discard cumulative accounting.
 await assignment.getByRole('button',{name:'Stop assignment',exact:true}).click();
 await expect(assignment).toContainText('Assignment stopped');
 await expect(assignment).toContainText('nothing is sent automatically');
 await expect.poll(async()=>(await inspect()).runtimeCalls.length).toBe(detached.runtimeCalls.length+1);
 const stopped=await inspect();
 assert.deepEqual(stopped.runtimeCalls,[...detached.runtimeCalls,'stop']);
 await assignment.getByRole('button',{name:'Resume assignment',exact:true}).click();
 await expect(assignment).toContainText('Assignment active');
 const resumed=await inspect();
 assert.equal(resumed.assignment.id,detached.assignment.id);
 assert.deepEqual(resumed.assignment.members,detached.assignment.members);
 assert.deepEqual(resumed.assignment.limits,detached.assignment.limits);
 assert.deepEqual(resumed.assignmentUsage,detached.assignmentUsage);
 assert.deepEqual(resumed.runtimeCalls,stopped.runtimeCalls);
 const resumeAction=actions.filter(row=>row.action==='assignment.resume').at(-1);
 assert.equal(resumeAction.args.sessionId,initial.selected);
 assert.equal(resumeAction.args.expectedRevision,detached.assignment.revision+1);

 // Candidate capture/recheck uses real local Git reads. It must neither invoke
 // a validation command nor mutate source/ref bytes or execution placement.
 const candidateBefore=await inspect(),candidate=related.locator('[data-part="integration-evidence"]');
 await candidate.getByText('Combine and check work',{exact:true}).click();
 await candidate.getByRole('button',{name:'Inspect and add repository',exact:true}).click();
 await expect(candidate.locator('.a-integration-repository')).toHaveCount(1);
 await candidate.getByLabel('What are you checking?',{exact:true}).fill('Ordinary local source observation');
 await candidate.getByRole('button',{name:'Save candidate observation',exact:true}).click();
 await expect(candidate.locator('[data-part="integration-result"]')).toContainText('Candidate: matched');
 await expect(candidate).toContainText('Matching files does not prove a successful test or the exact bytes imported earlier.');
 await candidate.getByRole('button',{name:'Recheck this candidate',exact:true}).click();
 await expect(candidate.locator('[data-part="integration-result"]')).toContainText('Recheck: matched');
 await candidate.getByText('Run checks and collect results',{exact:true}).click();
 await expect(candidate).toContainText('Inspection alone runs no command.');
 const candidateAfter=await inspect();
 assert.deepEqual(candidateAfter.files,candidateBefore.files);assert.deepEqual(candidateAfter.placement,candidateBefore.placement);
 assert.deepEqual(candidateAfter.runtimeCalls,candidateBefore.runtimeCalls);
 const gitReads=candidateAfter.gitCalls.slice(candidateBefore.gitCalls.length);
 assert.ok(gitReads.length>0);
 assert.ok(gitReads.every(row=>row.path===initial.workspace&&['config','rev-parse','status','diff','ls-files','worktree','branch'].includes(row.args[0])),JSON.stringify(gitReads));
 assert.ok(gitReads.filter(row=>row.args[0]==='branch').every(row=>row.args.length===2&&row.args[1]==='--show-current'));
 assert.ok(gitReads.filter(row=>row.args[0]==='worktree').every(row=>row.args[1]==='list'));
 assert.ok(gitReads.filter(row=>row.args[0]==='config').every(row=>row.args.includes('--get-regexp')));
 assert.equal(actions.filter(row=>['integration.validate','worktree.create','worktree.handoff'].includes(row.action)).length,0);

 // Same ordinary entry reaches passive observations and the workspace's
 // Managed resources page; merely inspecting must not allocate or retire work.
 await related.getByText('Changes since your last view and diagnostics',{exact:true}).click();
 const recap=related.getByRole('region',{name:'Changes since your last view',exact:true});
 await recap.scrollIntoViewIfNeeded();await expect(recap.getByRole('button',{name:'Refresh change recap',exact:true})).toBeEnabled();
 await workspaceView.getByRole('button',{name:'Details',exact:true}).click();
 await page.getByText('Managed resources',{exact:true}).click();
 const managed=page.getByRole('region',{name:'Managed resources',exact:true});
 await expect(managed.getByRole('heading',{name:'Task storage',exact:true})).toBeVisible();
 await expect(managed).toContainText('No task storage on this page.');
 const managedRead=actions.filter(row=>row.action==='workspace.resources.managed').at(-1);
 assert.ok(managedRead);assert.equal(managedRead.args.sessionId,initial.selected);
 const workspaceId=await page.evaluate(()=>window.amplifier.getState().view.workWorkspaceId);
 assert.equal(managedRead.args.workspaceId,workspaceId);
 assert.equal(actions.filter(row=>['workspace.resources.provision','workspace.resources.retire','publishing.stop'].includes(row.action)).length,0);

 // A listed chat is rebound after discovery. Actual service context changes;
 // the public picker must reject it before showing stale/wrong workspace work.
 await workspaceView.getByRole('button',{name:'Details',exact:true}).click();
 failNext=true;await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect(related.getByRole('alert')).toHaveText('Fixture scope read unavailable');
 await expect(related.locator('.a-assignment')).toHaveCount(0);
 await expect(related).not.toHaveAttribute('inert');
 await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect(related.getByRole('alert')).toHaveCount(0);await expect(related.locator('.a-assignment')).toBeVisible();
 await seed('rebound');await picker.selectOption(initial.other);
 await expect(related.getByRole('alert')).toHaveText('This chat no longer belongs to the workspace being reviewed. Refresh the chat list.');
 await expect(related.getByRole('region',{name:'Working in this workspace',exact:true})).toHaveCount(0);
 await expect(related.locator('.a-assignment')).toHaveCount(0);
 await expect(related.locator('[data-part="integration-evidence"]')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),initial.selected);
 await page.screenshot({path:output+'/rebound-scope.png'});
 await picker.selectOption(initial.selected);
 await related.getByText('Workspace chat list',{exact:true}).click();
 await related.getByRole('button',{name:'Open chat: Settings test',exact:true}).click();
 await expect(composer).toHaveDraft('Keep the ordinary inspection draft');
 assert.deepEqual((await inspect()).runtimeCalls,[...initial.runtimeCalls,'stop']);
 assert.deepEqual(errors,[]);
 await page.screenshot({path:output+'/ordinary-workspace-work.png'});
 const receipt={passed:true,route:'Chat breadcrumb → workspace Details → Workspace work; workspace Details → Managed resources',checks:[
  'file declaration preserves project bytes and execution placement','saved acknowledgment expanded editor and focus survive scope refresh',
  'held scope response cannot overwrite another inspected owner or private draft','bounded 4000-character excerpt and full original retained',
  'explicit assignment identity owner and revision','limits use exact observed authority/revision and show partial unknown usage',
  'settled member detachment retains attributed incomplete usage','task board reachable through ordinary route',
  'stopped assignment explicitly resumes with topology and cumulative usage retained; zero new runtime work',
  'candidate observation and recheck use only local read Git commands and do not validate','public validation controls and passive recap',
  'managed resources reads selected workspace without allocation or retirement','scope read failure hides stale controls and refresh recovers','rebound workspace rejected with stale controls hidden',
  'selected conversation and exact private draft retained; zero runtime calls'
 ],gitReads,actions,limits:['Synthetic fixture, including previously commissioned settled membership and incomplete accounting. No live provider or load-performance claims. Existing tests own task lifecycle and validation execution.']};
 await writeFile(output+'/receipt.json',JSON.stringify(receipt,null,2)+'\n');
 console.log(JSON.stringify({...receipt,actions:actions.map(row=>row.action)}));
}catch(error){
 await writeFile(output+'/receipt.json',JSON.stringify({passed:false,error:error.message})+'\n');
 if(page){await page.screenshot({path:output+'/failure.png'}).catch(()=>{});await writeFile(output+'/failure.html',await page.content()).catch(()=>{})}
 throw error;
}finally{await browser?.close();fixture.kill()}
