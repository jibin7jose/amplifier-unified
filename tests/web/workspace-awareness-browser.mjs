import './composer-test-helpers.mjs';
import {openChatAdvancedPage,showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-awareness';await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1150,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={},meta={})=>page.evaluate(([name,args,meta])=>window.amplifier.dispatch(name,args,meta),[name,args,meta]);
 const activity=(sid,status)=>page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status}});
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const workspace=(await page.evaluate(()=>window.amplifier.getState())).workspaces[0];
 const first=await action('session.create',{workspace:workspace.path,title:'Primary writer'});
 const second=await action('session.create',{workspace:workspace.path,title:'Independent writer',select:false});
 await activity(second.sessionId,'working');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Private composer content');
 await openChatAdvancedPage(page,'Work & automation');
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 const related=page.getByRole('region',{name:'Workspace work overview'}),panel=page.getByRole('region',{name:'Working in this workspace'});
 await expect(panel.locator('[data-active-peer]')).toHaveCount(1);
 await expect(panel).toContainText('Independent writer');await expect(panel).toContainText('Shares your execution folder');
 await panel.getByText('Declare files for this chat',{exact:true}).click();
 const input=panel.getByLabel('Planned file or folder paths',{exact:true});
 await input.fill('src/');await panel.getByRole('button',{name:'Save file declaration',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('File declaration saved');
 await action('coordination.intent',{sessionId:second.sessionId,expectedRevision:0,expectedExecutionRevision:0,paths:[{path:'src/shared.py',kind:'file'}]});
 await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect(panel).toContainText('Declared files overlap');
 await panel.getByRole('button',{name:'View full declaration',exact:true}).click();
 await expect(panel.getByRole('article',{name:'Shared file declaration'})).toContainText('src/shared.py');
 await panel.getByRole('button',{name:'Close declaration',exact:true}).click();
 await page.screenshot({path:output+'/desktop.png'});
 await input.fill('PRIVATE-UNSENT-PLAN.py');
 const read=()=>action('coordination.intent.read',{sessionId:first.sessionId});
 assert.equal((await read()).result.paths[0].path,'src/');
 // Another client sees only submitted declaration content.
 const other=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await other.goto(url);await showFixtureConversation(other);await other.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const observed=await other.evaluate(sid=>window.amplifier.dispatch('coordination.context',{sessionId:sid}),first.sessionId);
 assert.ok(!JSON.stringify(observed).includes('PRIVATE-UNSENT-PLAN'));
 await other.close();
 await action('session.select',{id:second.sessionId});
 await expect(panel).toContainText('Your execution folder');
 await expect(input).not.toHaveValue('PRIVATE-UNSENT-PLAN.py');
 await action('session.select',{id:first.sessionId});
 await panel.getByText('Declare files for this chat',{exact:true}).click();
 await expect(input).toHaveValue('PRIVATE-UNSENT-PLAN.py');
 await action('coordination.intent',{sessionId:first.sessionId,expectedRevision:1,expectedExecutionRevision:0,paths:[{path:'newer.py',kind:'file'}]});
 await panel.getByRole('button',{name:'Save file declaration',exact:true}).click();
 await expect(panel.getByRole('alert')).toContainText('declaration or execution folder changed');
 await expect(input).toHaveValue('PRIVATE-UNSENT-PLAN.py');
 await page.reload();await panel.getByText('Declare files for this chat',{exact:true}).click();
 await expect(input).toHaveValue('PRIVATE-UNSENT-PLAN.py');
 await panel.getByRole('button',{name:'Discard draft and use latest',exact:true}).click();
 await expect(input).toHaveValue('newer.py');
 // Lose the response after the host committed; retry the same logical ID.
 let dropped=false;const writes=[];
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='coordination.intent'){
   writes.push(body);
   if(!dropped){dropped=true;await route.fetch();await route.abort('connectionreset');return}
  }
  await route.continue();
 });
 await input.fill('src/');await panel.getByRole('button',{name:'Save file declaration',exact:true}).click();
 await expect(panel.getByRole('alert')).toBeVisible();await expect(input).toHaveValue('src/');
 await panel.getByRole('button',{name:'Save file declaration',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('File declaration saved');
 assert.equal(writes.length,2);assert.equal(writes[0].id,writes[1].id);assert.equal((await read()).result.revision,3);
 await page.unroute('**/api/actions');
 for(let i=0;i<9;i++){const peer=await action('session.create',{workspace:workspace.path,title:'Active peer '+i,select:false});await activity(peer.sessionId,'working')}
 await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect(panel.getByRole('button',{name:'More active chats',exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'More active chats',exact:true}).click();
 await expect(panel.getByRole('button',{name:'Previous active chats',exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Previous active chats',exact:true}).click();
 await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({path:output+'/mobile.png'});
 await input.fill('');await panel.getByRole('button',{name:'Save file declaration',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('File declaration saved');assert.deepEqual((await read()).result.paths,[]);
 await activity(second.sessionId,'idle');await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await expect(panel.locator('[data-active-peer="'+second.sessionId+'"]')).toHaveCount(0);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Private composer content');
 assert.deepEqual(errors,[]);
 const runtime=await page.request.get(url+'/fixture').then(response=>response.json());assert.equal(runtime.sent.length,0);
 const report={status:'passed',runtime:'scripted; no model input',checks:['active peers','declared overlap','full read','private draft','navigation','stale revision','reload','lost response stable retry','pagination','mobile','clear','idle transition']};
 await writeFile(output+'/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});console.error((await page?.locator('body').innerText())?.slice(-6000));throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
