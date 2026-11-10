import {spawn} from 'node:child_process';
import {showComposerOption} from './composer-options-helpers.mjs';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const evidence=process.env.AMPLIFIER_TEST_OUTPUT||join(tmpdir(),'amplifier-setup-journey');await mkdir(evidence,{recursive:true});
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser,vite;const errors=[];
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.port){clearTimeout(timer);resolve(value)}}catch{}})});
 const target=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(target+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 vite=await createServer({configFile:false,cacheDir:root+'frontend/.vite-legacy-mocks',root:root+'frontend',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1600,height:1000},extraHTTPHeaders:headers});page.on('pageerror',e=>errors.push(e.message));
 await control({op:'setup',connected:false});
 await page.goto(vite.resolvedUrls.local[0]);
 await expect(page.getByRole('region',{name:'Get started',exact:true})).toBeVisible();
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page','ai-connections');
 await control({op:'setup',connected:true,journey:'connect'});
 await expect(page.getByText('Your first connection is ready',{exact:true})).toBeVisible();
 await expect(page.locator('.a-canvas-head').getByRole('button',{name:'Settings',exact:true})).toHaveCount(0);
 // Ordinary frames carry the selected chat; membership belongs to the catalog.
 const catalog=async()=>{const client=await page.evaluate(()=>window.amplifier.getState().client.id);const response=await page.request.get(target+'/api/sessions?kind=root&limit=10',{headers:{...headers,'X-Amplifier-Client':client}});assert.equal(response.status(),200);const value=await response.json();assert.equal(value.nextOffset,null);return value.items.map(row=>row.id)};
 const before=await catalog(),originalId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.ok(before.includes(originalId));
 await page.getByRole('button',{name:'Set up with the assistant',exact:true}).click();
 await expect(page.locator('.a-settings-canvas .a-settings-experience')).toBeVisible();
 await expect(page.locator('.a-overlay')).toHaveCount(0);
 await expect(page.getByRole('tab',{name:'Settings',exact:true})).toBeVisible();
 const settingsId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await page.waitForFunction(()=>window.amplifier.getState().view.setupJourney==='assisted');
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.ok(!before.includes(sid),'The assistant receives one new conversation');
 assert.deepEqual((await catalog()).sort(),[...before,sid].sort(),'Exactly one chat added; all previous chats retained');
 await page.waitForFunction(id=>window.amplifier.getState().sessions.find(s=>s.id===id)?.messages?.some(m=>m.role==='user'&&m.text.startsWith('Help me finish setting up')),sid);
 const bounds=await page.locator('.a-conversation').boundingBox();if(bounds)assert.ok(bounds.width>=350&&bounds.width<500,JSON.stringify(bounds));
 // The compact composer keeps one model/options control below the writing
 // field, including a narrow desktop pane beside the setup assistant.
 const options=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
 await expect(options).toBeVisible();
 await expect(options).toHaveCount(1);
 await expect(page.getByRole('button',{name:'Conversation bundle',exact:true})).toHaveCount(0);
 const belowField=async()=>{
  const optionBox=await options.boundingBox(),surface=await page.locator('[data-part="composer-surface"]').boundingBox();
  assert.ok(optionBox.y>=surface.y+surface.height-1,'Model control stays below the writing field');
  await expect(page.getByRole('button',{name:'Add attachments',exact:true})).toBeVisible();
 };
 await belowField();
 await page.screenshot({path:join(evidence,'readiness107-narrow-composer.png')});
 await options.click();const menu=page.getByRole('dialog',{name:'Chat options',exact:true});
 await expect(menu).toBeVisible();
 await showComposerOption(page,'provider');
 await expect(menu.getByLabel('Provider',{exact:true})).toBeVisible();
 await showComposerOption(page,'model');
 await expect(menu.getByRole('combobox',{name:'Conversation model',exact:true})).toBeVisible();
 await expect(menu.getByRole('button',{name:'Refresh models',exact:true})).toBeVisible();
 await showComposerOption(page,'bundle');
 await expect(menu.getByRole('combobox',{name:'Registered root bundles',exact:true})).toBeVisible();
 await menu.getByRole('button',{name:'Back to chat options',exact:true}).click();
 const box=await menu.boundingBox();assert.ok(box.height>200&&box.y>=0&&box.y+box.height<=1000,JSON.stringify(box));
 await page.screenshot({path:join(evidence,'readiness107-options-open.png')});
 await menu.getByRole('button',{name:'Close chat options',exact:true}).click();await expect(menu).toHaveCount(0);
 await options.click();await menu.getByRole('button',{name:'Close chat options',exact:true}).press('Escape');
 await expect(menu).toHaveCount(0);await expect(options).toBeFocused();
 await options.click();await page.locator('.a-messages').click({position:{x:40,y:40}});await expect(menu).toHaveCount(0);
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
 for(const width of [320,390]){
  await page.setViewportSize({width,height:650});await expect(options).toBeVisible();await belowField();await options.click();await expect(menu).toBeVisible();
  await expect.poll(async()=>{const b=await menu.boundingBox();return b.x>=0&&b.y>=0&&b.x+b.width<=width&&b.y+b.height<=650}).toBe(true);
  await menu.getByRole('button',{name:'Close chat options',exact:true}).click();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 }
 await page.setViewportSize({width:1600,height:1000});
 await expect(options).toHaveCount(1);await expect(options).toBeVisible();await belowField();
 await page.evaluate(()=>window.amplifier.dispatch('canvas.show',{kind:'settings'}));
 await expect(page.locator('.a-settings-canvas')).toBeVisible();
 // Dragging must commit the new split instead of snapping back on render.
 const divider=page.getByRole('separator',{name:'Resize canvas',exact:true});
 const handle=await divider.boundingBox();
 await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
 await page.mouse.down();await page.mouse.move(handle.x+handle.width/2+230,handle.y+handle.height/2,{steps:12});await page.mouse.up();
 await expect.poll(async()=>Math.round((await page.locator('.a-conversation').boundingBox()).width)).toBeGreaterThan(550);
 const resizedWidth=await page.evaluate(()=>window.amplifier.getState().view.canvasWidth);
 // The UI applies navigation optimistically. Reload only after the real host
 // acknowledges this exact destination, not merely after its heading paints.
 const appearanceSaved=page.waitForResponse(response=>response.request().method()==='POST'&&
  new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action==='view.update'&&
  response.request().postDataJSON()?.args?.patch?.settingsExpanded?.includes('appearance'));
 await openSettingsPage(page,'appearance');
 await expect(page.locator('.a-settings-canvas [data-settings-page=appearance]')).toBeVisible();
 const savedAppearance=await appearanceSaved;
 assert.equal(savedAppearance.status(),200);
 assert.equal((await savedAppearance.json()).accepted,true);
 await page.reload();await expect(page.locator('.a-settings-canvas [data-settings-page=appearance]')).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.canvasWidth),resizedWidth);
 assert.ok((await page.locator('.a-conversation').boundingBox()).width>550);
 await divider.press('ArrowRight');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.canvasWidth)).toBe(resizedWidth-20);
 await page.screenshot({path:join(evidence,'readiness103-settings-canvas.png')});
 // Settings is an on-demand artifact with the same close/reopen lifecycle.
 await page.evaluate(()=>window.amplifier.dispatch('canvas.show',{kind:'text',title:'Notes',content:'Keep this tab'}));
 await expect(page.getByRole('tab',{name:'Notes',exact:true})).toHaveAttribute('aria-selected','true');
 await expect(page.locator('.a-settings-canvas')).toHaveCount(0);
 await page.getByRole('tab',{name:'Settings',exact:true}).click();
 await expect(page.locator('.a-settings-canvas')).toBeVisible();
 await page.getByRole('tab',{name:'Settings',exact:true}).press('Delete');
 await expect(page.getByRole('tab',{name:'Settings',exact:true})).toHaveCount(0);
 await expect(page.getByRole('tab',{name:'Notes',exact:true})).toBeVisible();
 await page.evaluate(()=>window.amplifier.dispatch('canvas.show',{kind:'settings'}));
 await expect(page.getByRole('tab',{name:'Settings',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),settingsId);
 await page.reload();await expect(page.getByRole('tab',{name:'Settings',exact:true})).toBeVisible();
 // Private fields survive both local exits and agent-driven chat navigation.
 await openSettingsPage(page,'voice');
 const privateKey=page.locator('#voice-api-key');
 if(!await privateKey.isVisible())await page.getByText('Change voice connection',{exact:true}).click();
 await privateKey.fill('synthetic-private-draft');
 await page.getByRole('tab',{name:'Settings',exact:true}).press('Delete');
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(privateKey).toHaveValue('synthetic-private-draft');
 const other=originalId;assert.ok((await catalog()).includes(other),'Original conversation remains selectable');
 const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
 const switched=await fetch(target+'/api/actions',{method:'POST',headers:{...headers,'X-Amplifier-Client':clientId},body:JSON.stringify({action:'session.select',args:{id:other}})});
 assert.equal(switched.status,200);
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,sid);
 await expect(privateKey).toHaveValue('synthetic-private-draft');
 assert.ok(!JSON.stringify(await page.evaluate(()=>window.amplifier.getState())).includes('synthetic-private-draft'));
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
 await page.getByRole('button',{name:'Discard changes',exact:true}).click();
 // A normal configured visit does not force a setup dialog.
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{settingsCanvas:false,setupJourney:'done'}}));
 await page.reload();await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 // A discovery result for the original project does not apply to the managed
 // chat created by assisted setup from Home.
 await control({op:'setup',connected:false});await page.reload();
 await page.waitForFunction(()=>{
  const state=window.amplifier.getState(),session=state?.sessions?.find(row=>row.id===state.selectedSessionId);
  return session&&state.setup?.providersLoadedAt&&state.setup.providersWorkspace!==session.workspace;
 });
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 // Missing connections in this chat's actual workspace reopen setup on a
 // later visit, without requesting unrelated catalogs while reading history.
 await control({op:'setup',connected:false,sessionId:sid});await page.reload();
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page','ai-connections');
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.screenshot({path:join(evidence,'onboarding-mobile.png')});
 assert.deepEqual(errors,[]);
 console.log('Setup journey passed: later empty connections, assistant handoff, single new chat, Canvas navigation/reload, narrow chat, and mobile.');
}finally{await browser?.close();await vite?.close();fixture.kill('SIGTERM')}
