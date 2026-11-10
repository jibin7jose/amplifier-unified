import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
// Uses the real chat UI and isolated runtime. No model or existing user data.
// Vite serves source directly, so this regression does not rebuild static assets.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, sys, tempfile
from pathlib import Path
from aiohttp import web
sys.path.insert(0, ${JSON.stringify(fileURLToPath(new URL('../../tests/fixtures',import.meta.url)))})
import chat_ui_server as fixture
class ScrollRuntime(fixture.ChatRuntime):
 async def send(self, session, text, input_id, emit):
  await emit('runtime.status', {'sessionId':session['id'], 'status':'working'})
  await asyncio.Event().wait()
fixture.fixture.Runtime=ScrollRuntime
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-scroll-ui-') as home:
  app=await fixture.fixture.main(Path(home))
  service=app['service']
  session=service.state['sessions'][0]
  session['messages']=[{'id':f'history-{i}', 'role':'user' if i%2==0 else 'assistant', 'text':f'History {i}\\n\\n'+('A useful paragraph. '*35), 'createdAt':i+1} for i in range(30)]
  async def grow(request):
   await service.on_runtime_event('assistant.delta', {'sessionId':session['id'], 'text':'\\n\\nMore streaming content. '*25})
   return web.json_response({'ok':True})
  app.router.add_post('/api/fixture/grow', grow)
  async def hidden_reply(request):
   await service.on_runtime_event('assistant.message', {'sessionId':session['id'], 'text':'Fresh reply received while this tab was hidden.'})
   return web.json_response({'ok':True})
  app.router.add_post('/api/fixture/hidden-reply', hidden_reply)
  runner=web.AppRunner(app);await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>{fixtureLog+=chunk});
const fixtureReady=new Promise((resolve,reject)=>{
 let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('exit',code=>reject(new Error(`Scroll fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page;
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
try{
 await mkdir(output,{recursive:true});
 const port=await Promise.race([fixtureReady,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Scroll fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const requests=[];page.on('request',request=>requests.push(new URL(request.url()).pathname));
 await page.goto(vite.resolvedUrls.local[0]);await showFixtureConversation(page,{navPinned:true});await page.waitForSelector('#amp-one');

 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const calls=[];page.on('request',request=>{if(request.url().includes('/api/actions')){try{calls.push(request.postDataJSON())}catch{}}});
 const openChat=async label=>{await page.getByRole('button',{name:'Chat',exact:true}).click();await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:label,exact:true}).click()};
 const close=async()=>{await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)};
 await expect(page.getByRole('button',{name:'Chat controls',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Chat actions',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await expect(page.getByRole('group',{name:'Chat',exact:true}).getByRole('button')).toHaveText(['Rename','Pin','Share or save','Chat info','Help & diagnostics','Advanced','Archive']);
 await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeFocused();
 await openChat('Chat info');
 const info=page.locator('.a-chat-info');
 await expect(info.locator(':scope > :first-child')).toContainText('Session ID');
 const sid=await page.evaluate(()=>{const s=window.amplifier.getState();const row=s.sessions.find(row=>row.id===s.selectedSessionId);return row.runtimeSessionId||row.nativeIdentity||row.id});
 await expect(info.locator(':scope > :first-child')).toContainText(sid);
 await expect(info).not.toContainText('Not reported');await expect(info).not.toContainText('partial');
 await page.context().grantPermissions(['clipboard-read','clipboard-write']);
 await info.getByRole('button',{name:'Copy session id',exact:true}).click();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),sid);
 await info.getByText('Locations & identifiers',{exact:true}).click();
 await expect(info.getByText('History folder on disk',{exact:true})).toBeVisible();
 await expect(info.getByText('Execution folder',{exact:true})).toBeVisible();
 await info.getByText('Call receipts & provider quota',{exact:true}).first().click();
 await expect(info.getByRole('button',{name:'Inspect provider quota'})).toBeVisible();
 await expect(info.getByRole('button',{name:'Save budget'})).toHaveCount(0);
 await close();
 await openChat('Rename');
 await expect(page.getByRole('checkbox',{name:'Automatic chat naming'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Regenerate chat name'})).toBeVisible();
 await page.getByLabel('Chat name',{exact:true}).fill('Unsaved name');
 await action('view.update',{patch:{panel:'chat-share'}});
 await expect(page.getByText('Save to a file',{exact:true})).toBeVisible();
 await page.getByText('More formats',{exact:true}).click();await expect(page.getByRole('button',{name:'Export JSON',exact:true})).toBeVisible();
 await page.getByText('Share a copy',{exact:true}).click();
 await action('view.update',{patch:{panel:'chat-rename'}});
 await expect(page.getByLabel('Chat name',{exact:true})).toHaveValue('Unsaved name');
 await close();
 await openChat('Help & diagnostics');
 assert.equal(calls.filter(row=>row.action==='feedback.diagnostics').length,0);
 await page.getByText('Preview reproduction diagnostics',{exact:true}).click();
 await expect(page.getByRole('button',{name:'Copy report',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Copy report',exact:true}).click();
 const diagnostic=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));
 assert.ok(diagnostic);assert.ok(!JSON.stringify(diagnostic).includes('A useful paragraph'));
 assert.ok(!JSON.stringify(diagnostic).includes('/opt/unified'));
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Save report',exact:true}).click();assert.equal((await download).suggestedFilename(),'amplifier-diagnostics.json');
 await page.getByRole('button',{name:'Diagnostic recording settings',exact:true}).click();
 await expect(page.getByRole('heading',{name:'App diagnostic capture',exact:true})).toBeVisible();await close();
 await openChat('Advanced');
 const groups=['Work & automation','Model & tools','Context & recovery','Computer access'];
 for(const name of groups){
  await page.getByRole('navigation',{name:'Advanced chat options'}).getByRole('button',{name:new RegExp('^'+name.replace('&','&'))}).click();
  await expect(page.locator('#panel-title')).toHaveText(name);
  if(name==='Work & automation'){await expect(page.getByRole('heading',{name:'Task execution folder'})).toBeVisible();await expect(page.getByRole('button',{name:'Delegate work',exact:true})).toBeVisible()}
  if(name==='Model & tools'){await expect(page.getByRole('heading',{name:'Tools',exact:true})).toBeVisible();await expect(page.getByText('Conversation modules',{exact:true})).toBeVisible()}
  if(name==='Context & recovery'){await expect(page.getByRole('button',{name:'Clear context',exact:true})).toBeDisabled();await expect(page.getByText('Recovery & recorded errors',{exact:true})).toBeVisible()}
  await page.getByRole('button',{name:'Back to Advanced',exact:true}).click();
  await expect(page.locator('#panel-title')).toHaveText('Advanced');
  await expect.poll(()=>page.evaluate(()=>{
   const active=document.activeElement;
   return !!active?.closest('[role=dialog]')&&!!active.getClientRects().length;
  }),{message:'Focus must return to a visible control in the updated dialog'}).toBe(true);
 }
 await close();
 // Existing agent navigation must still reveal the requested computer section.
 await action('view.update',{patch:{panel:'runtime',runtimeDraft:{section:'screen-source'}}});
 await expect(page.locator('[data-runtime-section="screen-source"]')).toBeVisible();await close();
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 await expect(page.locator('.a-canvas-delivery summary').filter({hasText:'Outputs & review'})).toBeVisible();
 await expect(page.locator('.a-canvas-delivery summary').filter({hasText:/^Publishing$/})).toBeVisible();
 await page.locator('[data-overview-section="review"] > summary').click();
 await expect(page.getByRole('button',{name:'Inspect outputs',exact:true})).toBeVisible();
 const outputLast=page.locator('[data-overview-section="review"] button').last();await outputLast.scrollIntoViewIfNeeded();await expect(outputLast).toBeInViewport();
 await page.locator('[data-overview-section="publishing"] > summary').click();
 const publishingLast=page.locator('[data-overview-section="publishing"] button').last();await publishingLast.scrollIntoViewIfNeeded();await expect(publishingLast).toBeInViewport();
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
 // Opening menus and inspecting diagnostics must not start work or mutate settings.
 assert.equal(calls.filter(row=>['conversation.send','tool.invoke','session.rename','session.naming','feedback.submit','capacity.set','context.clear','computer.visual.capture'].includes(row.action)).length,0);
 for(const scheme of ['light','dark'])for(const width of [1200,360]){
  await page.setViewportSize({width,height:900});await action('view.update',{patch:{scheme,panel:null,navExpanded:false}});
  await openChat('Chat info');
  await expect(info.locator(':scope > :first-child')).toBeVisible();
  const box=await page.getByRole('dialog').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width+1);
  await page.screenshot({path:join(output,`chat-menu-${scheme}-${width}.png`)});await close();
 }
 assert.deepEqual(errors,[]);
 console.log('Chat menu browser checks passed: one menu, session ID first/copy, accurate paths, rename draft/auto, sharing, lazy safe diagnostics/copy/download/settings, four advanced groups, legacy computer navigation, canvas destinations, no unintended mutations, light/dark desktop/mobile.');
}finally{
 await page?.unrouteAll({behavior:'ignoreErrors'});await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
