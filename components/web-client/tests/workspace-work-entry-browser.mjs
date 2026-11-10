import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-work-entry';
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(output,{recursive:true});
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],calls=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[],chats=[];
 for(const name of ['Active project','Other project','Empty project']){
  const folder=path.join(home,name);await mkdir(folder);await action('workspace.add',{path:folder,name});
  workspaces.push((await action('workspace.list',{query:name})).result.items.find(row=>row.path===folder));
  if(name!=='Empty project')chats.push((await action('session.create',{workspace:folder,title:name+' chat'})).result.sessionId);
 }
 const [a,b,empty]=workspaces,[aChat,bChat]=chats;
 await action('session.select',{id:aChat});await composer.fill('Private draft stays with the active project');
 await action('attachment.add',{sessionId:aChat,name:'draft.txt',base64:'cHJpdmF0ZQ=='});
 const header=page.locator('[data-part="header"]');await expect(header.getByRole('button',{name:'Workspace work',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 const overview=page.getByRole('region',{name:'Chat overview',exact:true});await expect(overview.getByRole('button',{name:'Chat coordination review',exact:true})).toHaveCount(1);
 await overview.getByRole('button',{name:'Chat coordination review',exact:true}).click();await expect(page.getByRole('dialog',{name:'Chat coordination review',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();assert.equal((await state()).selectedSessionId,aChat);await expect(composer).toHaveDraft('Private draft stays with the active project');
 await action('canvas.visibility',{open:false});
 const browse=workspace=>action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'details',navExpanded:false}});
 const body=page.locator('.a-work-browser'),entry=body.locator('.a-workspace-work-entry');
 await browse(b);await expect(body.getByRole('heading',{level:1,name:b.name,exact:true})).toBeVisible();
 const before=calls.length;await entry.locator(':scope > summary').click();const inspection=page.getByRole('region',{name:'Inspect workspace work'});
 await expect(inspection.getByRole('combobox',{name:'Review from chat'})).toContainText('Other project chat');
 await expect(inspection.getByRole('combobox',{name:'Review from chat'})).toHaveValue('');
 await inspection.getByRole('combobox',{name:'Review from chat'}).selectOption(bChat);
 await expect(inspection.getByText('Reviewing from Other project chat.',{exact:true})).toBeVisible();
 await expect(inspection.getByRole('region',{name:'Workspace work overview'})).toBeVisible();
 assert.equal((await state()).selectedSessionId,aChat);assert.equal((await state()).selectedWorkspaceId,a.id);
 assert.equal(calls.slice(before).some(row=>['session.select','conversation.send','worker.spawn','coordination.followup'].includes(row.action)),false);
 assert.ok(calls.slice(before).some(row=>row.action==='coordination.context'&&row.args.sessionId===bChat));
 assert.ok(calls.slice(before).filter(row=>row.action==='coordination.list').every(row=>row.args.workspace===b.path&&row.args.limit===20&&!row.args.includeWorkers));
 await page.screenshot({path:output+'/workspace-inspection-desktop.png'});
 for(const width of [390,320]){
  await page.setViewportSize({width,height:900});await expect(inspection.getByRole('combobox',{name:'Review from chat'})).toBeVisible();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width);
  await page.screenshot({path:output+'/workspace-inspection-'+width+'.png'});
 }
 await page.setViewportSize({width:1440,height:1000});
 await browse(empty);await body.locator('.a-workspace-work-entry > summary').click();await expect(body.getByText('No saved chats in this workspace yet.',{exact:true})).toBeVisible();
 assert.equal((await state()).selectedSessionId,aChat);
 await browse(b);await body.locator('.a-workspace-work-entry > summary').click();await page.getByRole('combobox',{name:'Review from chat'}).selectOption(bChat);
 const related=page.getByRole('region',{name:'Workspace work overview'});await related.getByText('Workspace chat list',{exact:true}).click();await related.getByRole('button',{name:'Open chat: Other project chat',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(bChat);await expect(composer).toBeVisible();
 await action('session.select',{id:aChat});await expect(composer).toHaveDraft('Private draft stays with the active project');await expect(page.getByRole('button',{name:'Remove draft.txt',exact:true})).toBeVisible();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'passed',scenarios:8,screenshots:output}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
