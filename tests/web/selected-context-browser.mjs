import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Real selected-context transport with a canonical root, saved child, and 200+
// dormant chats. The fixture does not preload the child into the browser frame.
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-selected-context';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 ['-u',root+'tests/fixtures/chat_library_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,primaryError,stderr='';
const checks=[],errors=[],canvasChecks=[];
const captureCanvas=async step=>{if(!page)return;canvasChecks.push({step,...await page.evaluate(()=>{const state=window.amplifier.getState(),canvas=state.canvas||{},frame=document.querySelector('[data-canvas-view="primary"] .a-canvas-html');return {sameFrame:window.selectedContextFrame===frame,framePresent:!!frame,frameSrc:frame?.getAttribute('src'),id:canvas.id,kind:canvas.kind,preview:canvas.renderReports?.preview,views:(state.canvasWorkspace?.views||[]).map(({viewId,resourceId,resourceRevision,generation,activation})=>({viewId,resourceId,resourceRevision,generation,activation}))}}),inputValue:await page.frameLocator('[data-canvas-view="primary"] .a-canvas-html').getByRole('textbox').inputValue({timeout:1000}).catch(error=>error.message.slice(0,200))})};
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16384)});
function contextShape(state,children=false){
 assert.deepEqual(state.browserResources,children?['selectedContext','subagentNavigation']:['selectedContext']);
 for(const key of ['chatNavigation','headerChatNavigation','workspaceExplorer'])assert.equal(Object.hasOwn(state,key),false,key+' must not be built for the selected context');
 assert.equal(Object.hasOwn(state,'subagentNavigation'),children);
}
try{
 await mkdir(output,{recursive:true});
 const port=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout\n'+stderr)),30000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+'\n'+stderr))});
  fixture.stdout.on('data',chunk=>{text+=chunk;const match=text.match(/\{"port":\s*(\d+)\}/);if(match){clearTimeout(timer);resolve(Number(match[1]))}});
 });
 const url=`http://127.0.0.1:${port}`;
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const act=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const info=async()=>{const response=await page.request.get(url+'/api/fixture/directory');assert.ok(response.ok());return response.json()};
 const coverage=async children=>{
  await expect.poll(async()=>(await state()).browserResources).toEqual(children?['selectedContext','subagentNavigation']:['selectedContext']);
  const value=await state();contextShape(value,children);return value;
 };
 await page.goto(url);await showFixtureConversation(page);
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeVisible();await expect(page.getByText('Saved response',{exact:true})).toBeVisible();
 const initial=await info(),parent=initial.initialSession;
 const first=await coverage(false),selected=first.sessions.find(row=>row.id===parent);
 assert.equal(first.selectedSessionId,parent);assert.equal(Object.hasOwn(selected,'subagentCount'),false);
 assert.ok(first.sessions.length<10);assert.ok(initial.rootCount>200);
 checks.push({ordinaryContext:true,workerCountDeferred:true,rootCount:initial.rootCount,sessionCount:first.sessions.length});
 await act('view.update',{patch:{panel:'chat-work'}});
 const work=page.getByRole('dialog',{name:'Work & automation',exact:true});
 await expect(work.getByRole('button',{name:'Subagent history (1)',exact:true})).toBeVisible();
 await work.getByRole('button',{name:'Subagent history (1)',exact:true}).click();
 const history=page.getByRole('dialog',{name:'Subagent history',exact:true});
 await expect(history.getByRole('button',{name:'Hidden worker',exact:true})).toBeVisible();
 await history.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect.poll(async()=>Object.hasOwn((await state()).sessions.find(row=>row.id===parent),'subagentCount')).toBe(false);
 checks.push({visibleWorkControlsReadCount:true,historyButtonOpens:true,countRetiredOnClose:true});
 await composer.fill('Keep my selected-context draft');
 await act('canvas.show',{kind:'html',title:'Selected context canvas',content:'<input aria-label="Local canvas draft">'});
 await page.frameLocator('[data-canvas-view="primary"] .a-canvas-html').getByRole('textbox').fill('Preserved local Canvas input');
 await page.evaluate(()=>{window.selectedContextFrame=document.querySelector('[data-canvas-view="primary"] .a-canvas-html')});
 await captureCanvas('baseline');
 const open=()=>act('view.update',{patch:{panel:'subagent-history',subagentHistory:{sessionId:parent,filter:'',index:0}}});
 await open();
 const dialog=page.getByRole('dialog',{name:'Subagent history',exact:true});await expect(dialog).toBeVisible();
 let value=await coverage(true);await captureCanvas('dialog open');
 assert.deepEqual(value.subagentNavigation.scope,{sessionId:parent,filter:'',index:0});
 assert.equal(value.subagentNavigation.total,1);assert.equal(value.subagentNavigation.unfilteredTotal,1);
 await expect(dialog.getByRole('button',{name:'Hidden worker',exact:true})).toBeVisible();
 await dialog.getByRole('searchbox',{name:'Find subagent conversations',exact:true}).fill('does not exist');
 await expect(dialog.getByText('No matching subagent conversations.',{exact:true})).toBeVisible();
 value=await coverage(true);await captureCanvas('empty filter');assert.equal(value.subagentNavigation.scope.filter,'does not exist');assert.equal(value.subagentNavigation.total,0);
 await dialog.getByRole('searchbox',{name:'Find subagent conversations',exact:true}).fill('Hidden');
 await expect(dialog.getByRole('button',{name:'Hidden worker',exact:true})).toBeVisible();
 value=await coverage(true);await captureCanvas('matching filter');assert.equal(value.subagentNavigation.scope.filter,'Hidden');
 await dialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(dialog).toHaveCount(0);await coverage(false);await captureCanvas('dialog closed');
 await expect(composer).toHaveDraft('Keep my selected-context draft');
 assert.equal(await page.evaluate(()=>window.selectedContextFrame===document.querySelector('[data-canvas-view="primary"] .a-canvas-html')),true);
 await expect(page.frameLocator('[data-canvas-view="primary"] .a-canvas-html').getByRole('textbox')).toHaveValue('Preserved local Canvas input');
 checks.push({childPageOnlyInDialog:true,filterResultsAuthoritative:true,draftAndCanvasPreserved:true});
 await open();await expect(dialog.getByRole('button',{name:'Hidden worker',exact:true})).toBeVisible();
 const child=(await state()).subagentNavigation.items[0].id;
 await dialog.getByRole('button',{name:'Hidden worker',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(child);await expect(dialog).toHaveCount(0);
 value=await coverage(false);assert.ok(value.sessions.some(row=>row.id===parent),'Selected child carries exact parent metadata');
 await expect(page.getByRole('button',{name:'Open parent chat',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Open parent chat',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(parent);await coverage(false);
 await expect(composer).toHaveDraft('Keep my selected-context draft');
 const after=await info();assert.deepEqual(after.runtimeSends,[]);assert.deepEqual(errors,[]);
 checks.push({childSelectionAndParentReturn:true,noModelCalls:true});
 await page.screenshot({path:path.join(output,'selected-context.png')});
 console.log(JSON.stringify({passed:true,checks}));
}catch(error){primaryError=error;console.error(error);await page?.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});throw error}
finally{
 const cleanupErrors=[];
 try{await browser?.close()}catch(error){cleanupErrors.push(String(error));console.error('Browser cleanup failed',error)}
 try{if(fixture.exitCode===null&&fixture.signalCode===null){fixture.kill('SIGTERM');await new Promise(resolve=>{const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})})}}catch(error){cleanupErrors.push(String(error));console.error('Fixture cleanup failed',error)}
 if(cleanupErrors.length&&!primaryError)process.exitCode=1;
 await writeFile(path.join(output,'selected-context.json'),JSON.stringify({passed:!primaryError&&!cleanupErrors.length,checks,canvasChecks,pageErrors:errors,error:primaryError?.stack,cleanupErrors,fixturePid:fixture.pid,fixtureStderr:stderr},null,2)).catch(error=>{console.error('Evidence write failed',error);if(!primaryError)process.exitCode=1});
}
