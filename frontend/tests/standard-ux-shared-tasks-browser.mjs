import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/coordination_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('error',reject);fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[],writes=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')writes.push(request.postDataJSON())});
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Private task board draft');const initial=await inspect();
 await openChatAdvancedPage(page,'Work & automation');await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 const assignment=page.locator('.a-assignment');await assignment.getByText('Shared tasks and assignment',{exact:true}).click();await assignment.getByRole('button',{name:'Start assignment',exact:true}).click();await expect(assignment).toContainText('Assignment active');
 await assignment.getByText('Shared task list',{exact:true}).click();const board=assignment.locator('.a-assignment-tasks');await expect(board).toContainText('No active tasks.');
 const add=async(title,dependency)=>{const summary=board.getByText('Add shared task',{exact:true});if(!await summary.locator('..').evaluate(el=>el.open))await summary.click();await board.getByLabel('Task title',{exact:true}).fill(title);await board.getByRole('textbox',{name:'Details',exact:true}).fill('A synthetic readiness task');if(dependency)await board.getByRole('checkbox',{name:dependency,exact:true}).check();await board.getByRole('button',{name:'Add task',exact:true}).click();await expect(board.locator('[data-task-id]').filter({has:page.getByText(title,{exact:true})})).toHaveCount(1)};
 await add('Inspect candidate');await add('Review reported result','Inspect candidate');
 const first=board.locator('[data-task-id]').filter({has:page.getByText('Inspect candidate',{exact:true})}),second=board.locator('[data-task-id]').filter({has:page.getByText('Review reported result',{exact:true})});
 await expect(second).toContainText('Waiting for 1 prerequisite');await expect(second.getByRole('button',{name:'Claim for this chat',exact:true})).toHaveCount(0);
 let dropped=false;await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='assignment.tasks.claim'&&!dropped){dropped=true;await route.fetch();await route.abort('connectionreset');return}await route.continue()});
 await first.getByRole('button',{name:'Claim for this chat',exact:true}).click();await expect(board.getByRole('button',{name:'Check the same task change',exact:true})).toBeVisible();
 await board.getByRole('button',{name:'Check the same task change',exact:true}).click();await expect(first).toContainText('Responsibility: This chat');
 const claims=writes.filter(row=>row.action==='assignment.tasks.claim');assert.equal(claims.length,2);assert.equal(claims[0].id,claims[1].id);assert.deepEqual(claims[0].args,claims[1].args);
 const taskId=await first.getAttribute('data-task-id'),current=(await action('assignment.tasks.read',{sessionId:initial.selected,taskId})).result.task;
 // A direct hostile/stale claim is a service negative control; ordinary report
 // completion below still travels through the actual browser form.
 await assert.rejects(()=>action('assignment.tasks.complete',{sessionId:initial.selected,taskId,expectedRevision:current.revision,claimId:'wrong-claim',report:'Must not complete'}));
 await expect(first).not.toContainText('Reported complete');await first.getByText('Report completion',{exact:true}).click();await first.getByLabel('Completion report',{exact:true}).fill('Source review reported; independent validation remains.');await first.getByRole('button',{name:'Mark reported complete',exact:true}).click();
 await expect(first).toHaveCount(0);await board.getByRole('button',{name:'Task history',exact:true}).click();await expect(first).toContainText('Reported complete');await first.getByRole('button',{name:'View task details',exact:true}).click();await expect(board.getByRole('region',{name:'Task details'})).toContainText('Artifact verification and integration remain separate.');await board.getByRole('button',{name:'Active tasks',exact:true}).click();await expect(second.getByRole('button',{name:'Claim for this chat',exact:true})).toBeEnabled();
 await second.getByRole('button',{name:'Claim for this chat',exact:true}).click();await expect(second).toContainText('Responsibility: This chat');await second.getByRole('button',{name:'Release claim',exact:true}).click();await expect(second.getByRole('button',{name:'Claim for this chat',exact:true})).toBeEnabled();
 await second.getByText('Manage task',{exact:true}).click();await second.getByRole('button',{name:'Edit task',exact:true}).click();await board.getByLabel('Task title',{exact:true}).fill('Review report later');await board.getByRole('button',{name:'Save task',exact:true}).click();
 const edited=board.locator('[data-task-id]').filter({has:page.getByText('Review report later',{exact:true})});await expect(edited).toHaveCount(1);assert.deepEqual((await action('assignment.tasks.read',{sessionId:initial.selected,taskId:await edited.getAttribute('data-task-id')})).result.task.dependsOn,[taskId]);const manage=edited.getByText('Manage task',{exact:true});if(!await manage.locator('..').evaluate(el=>el.open))await manage.click();await edited.getByRole('button',{name:'Cancel task',exact:true}).click();await expect(edited).toHaveCount(0);await board.getByRole('button',{name:'Task history',exact:true}).click();await expect(edited).toContainText('Cancelled');
 // Seed enough work through the public service to exercise two bounded pages.
 for(let index=0;index<10;index++){
  const listing=(await action('assignment.tasks.list',{sessionId:initial.selected})).result;
  await action('assignment.tasks.create',{sessionId:initial.selected,expectedAssignmentRevision:listing.assignmentRevision,title:'Paged task '+index,description:'Paged exact details '+index,dependsOn:[],blockedReason:''});
 }
 await board.getByRole('button',{name:'Active tasks',exact:true}).click();await expect(board.locator('[data-task-id]')).toHaveCount(8);
 const firstPageIds=await board.locator('[data-task-id]').evaluateAll(rows=>rows.map(row=>row.dataset.taskId));
 const addSummary=board.getByText('Add shared task',{exact:true});if(!await addSummary.locator('..').evaluate(el=>el.open))await addSummary.click();
 await board.getByLabel('Task title',{exact:true}).fill('PRIVATE PAGED TASK DRAFT');await board.getByRole('textbox',{name:'Details',exact:true}).fill('Private details survive navigation.');
 await board.getByRole('button',{name:'Prerequisite history',exact:true}).click();await board.getByRole('checkbox',{name:'Inspect candidate',exact:true}).check();
 await board.getByRole('button',{name:'Active prerequisites',exact:true}).click();await expect(board.getByRole('button',{name:'Remove prerequisite Inspect candidate',exact:true})).toBeVisible();
 await board.getByRole('button',{name:'Next prerequisites',exact:true}).click();await expect(board.getByRole('checkbox')).toHaveCount(2);
 await board.getByRole('button',{name:'Next tasks',exact:true}).click();await expect(board.locator('[data-task-id]')).toHaveCount(2);
 const secondPageIds=await board.locator('[data-task-id]').evaluateAll(rows=>rows.map(row=>row.dataset.taskId));assert.ok(secondPageIds.every(id=>!firstPageIds.includes(id)));
 await expect(board.getByLabel('Task title',{exact:true})).toHaveValue('PRIVATE PAGED TASK DRAFT');await expect(board.getByRole('textbox',{name:'Details',exact:true})).toHaveValue('Private details survive navigation.');await expect(board.getByRole('button',{name:'Remove prerequisite Inspect candidate',exact:true})).toBeVisible();
 const saved=await page.evaluate(sid=>JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:'+sid)),initial.selected);assert.deepEqual(saved.draft.dependsOn,[taskId]);
 await board.getByRole('button',{name:'Previous tasks',exact:true}).click();await expect(board.locator('[data-task-id]')).toHaveCount(8);await expect(board.getByLabel('Task title',{exact:true})).toHaveValue('PRIVATE PAGED TASK DRAFT');
 // Exact historical detail stays available after browsing active pages.
 await board.getByRole('button',{name:'Discard task draft',exact:true}).click();await board.getByRole('button',{name:'Task history',exact:true}).click();await first.getByRole('button',{name:'View task details',exact:true}).click();await expect(board.getByRole('region',{name:'Task details'})).toContainText('Source review reported');
 const observed=await inspect();assert.deepEqual(observed.sent,initial.sent,'Task declarations, claims and reported completion must not start work');assert.deepEqual(observed.messages,initial.messages);assert.deepEqual(observed.stops,initial.stops);assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),initial.selected);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(composer).toHaveDraft('Private task board draft');assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['public task board','create dependency','blocked claim','lost acknowledgment same request','exact holder and revision','wrong claim rejected','reported completion separate from verification','dependency ready without execution','release edit cancel','private draft retained','active/history page replacement','independent prerequisite pages','off-page historical dependency retained'],limits:['scripted runtime; no real model or task execution']}));
}finally{await browser?.close();fixture.kill()}
