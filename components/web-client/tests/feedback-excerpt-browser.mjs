// Real source UI against an isolated host. GitHub and providers are mocked;
// this test must never create a real issue or read existing app data.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const bounded=(promise,label)=>Promise.race([promise,new Promise((_,reject)=>setTimeout(()=>reject(Error(label+' timed out')),15000).unref())]);

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, sys, tempfile
from pathlib import Path
from aiohttp import web
sys.path.insert(0, ${JSON.stringify(fileURLToPath(new URL('../../tests/fixtures',import.meta.url)))})
import settings_ui_server as fixture
from amplifier_web import feedback
calls=[]
uploads=[]
async def github_api(endpoint, payload):
 uploads.append({'endpoint':endpoint,'payload':payload})
 if payload is None:return {'private':False,'full_name':feedback.REPOSITORY}
 if endpoint.endswith('/git/refs'):return {'object':{'sha':'c'*40}}
 return {'sha':'c'*40 if endpoint.endswith('/git/commits') else 'a'*40}
feedback.github_api=github_api
feedback.shutil.which=lambda name:'/fixture/gh'
async def create_issue(title, body):
 calls.append({'title':title,'body':body})
 await asyncio.sleep(1.5)
 return feedback.ISSUES_URL+'/42'
feedback.create_issue=create_issue
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-feedback-ui-') as home:
  app=await fixture.main(Path(home));service=app['service']
  service._session()['messages']=[{'id':'first','role':'user','text':'Earlier private context'}, {'id':'last','role':'assistant','text':'The relevant failure was reading /home/test/private.txt with api_key=fixture-key.'}]
  service.state['updates'].update(application={'id':'application','kind':'app','label':'Amplifier Unified','current':'0.6.3','latest':'v0.6.4','status':'update','detail':'A newer application release is available; installation restarts the host when idle.'},appAvailable=True)
  async def inspect(request):return web.json_response({'calls':calls,'uploads':uploads})
  app.router.add_get('/api/fixture/feedback',inspect)
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('exit',code=>reject(new Error(`Feedback fixture exited ${code}: ${fixtureLog}`)))});
let vite,browser,releaseHeld=()=>{};
try{
 const port=await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Feedback fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];

 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'feedback'}}));
 const inspect=()=>page.evaluate(()=>fetch('/api/fixture/feedback').then(response=>response.json()));
 // Simulate independently delivered agent actions without joining the browser's
 // serial command queue, which is intentionally awaiting the held response.
 const direct=(action,args)=>page.evaluate(async({action,args})=>{
  const response=await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Amplifier-Client':window.amplifier.getState().client.id},body:JSON.stringify({action,args})});
  if(!response.ok)throw Error('Fixture action failed: '+response.status);return response.json();
 },{action,args});
 assert.deepEqual((await inspect()).uploads,[]);
 await page.getByLabel('Title',{exact:true}).fill('Reviewed excerpt fixture');
 await page.getByLabel('Details',{exact:true}).fill('A reproducible issue.');
 await page.getByRole('checkbox',{name:'Include reproduction diagnostics'}).uncheck();
 await page.getByRole('button',{name:'Attach transcript excerpt',exact:true}).click();
 await expect(page.getByLabel('Excerpt start')).toHaveValue('last');
 await expect(page.getByLabel('Excerpt end')).toHaveValue('last');
 await page.getByRole('button',{name:'Preview selected excerpt'}).click();
 const editor=page.getByLabel('Edit feedback excerpt'),attach=page.getByRole('button',{name:'Attach reviewed excerpt',exact:true});
 await expect(editor).toBeVisible();
 const initial=await editor.inputValue();assert.ok(!initial.includes('/home/test')&&!initial.includes('fixture-key')&&!initial.includes('Earlier private context'));
 await expect(page.getByText('Publicly visible on GitHub',{exact:true})).toBeVisible();
 assert.equal(await attach.isDisabled(),true);
 await page.getByRole('checkbox',{name:/I reviewed this exact text/}).check();
 await page.getByRole('checkbox',{name:/I reviewed the scope/}).check();
 const edited=initial+'\nManually narrowed reproduction context.\n';
 await editor.fill(edited);
 assert.equal(await attach.isDisabled(),true);
 assert.equal(await page.getByRole('checkbox',{name:/I reviewed this exact text/}).isChecked(),false);
 await page.getByRole('button',{name:'Review edited excerpt',exact:true}).click();
 await expect(page.getByRole('button',{name:'Review edited excerpt',exact:true})).toHaveCount(0);
 const exact=await editor.inputValue();
 await page.getByRole('checkbox',{name:/I reviewed this exact text/}).check();
 const extra=page.getByRole('checkbox',{name:/I reviewed the scope/});if(await extra.count())await extra.check();
 // Hold a successful staging response after the server committed it. The
 // form locks local edits/sending, and a newer agent edit must survive it.
 let stageArrived,releaseStage;
 const arrived=new Promise(resolve=>stageArrived=resolve),gate=new Promise(resolve=>releaseStage=resolve);
 releaseHeld=releaseStage;
 const holdStage=async route=>{
  if(route.request().postDataJSON()?.action!=='feedback.excerpt.stage')return route.continue();
  const response=await route.fetch();stageArrived();await gate;await route.fulfill({response});
 };
 await page.route('**/api/actions',holdStage);
 await attach.click();await bounded(arrived,'staging response');
 await expect(page.getByLabel('Title',{exact:true})).toBeDisabled();
 await expect(page.locator('form').getByRole('button',{name:'Send feedback',exact:true})).toBeDisabled();
 await page.waitForFunction(()=>window.amplifier.getState().view.feedbackDraft.attachments?.length===1);
 await direct('view.update',{patch:{feedbackDraft:{...await page.evaluate(()=>window.amplifier.getState().view.feedbackDraft),title:'Newer agent title',body:'Newer agent details'}}});
 await expect(page.getByLabel('Title',{exact:true})).toHaveValue('Newer agent title');
 releaseStage();
 await expect(page.getByRole('button',{name:'Excerpt attached locally',exact:true})).toBeVisible();
 await page.unroute('**/api/actions',holdStage);
 await expect(page.getByLabel('Title',{exact:true})).toHaveValue('Newer agent title');
 await expect(page.getByLabel('Details',{exact:true})).toHaveValue('Newer agent details');
 assert.equal((await inspect()).uploads.filter(row=>row.payload!==null).length,0);
 const send=page.locator('form').getByRole('button',{name:'Send feedback',exact:true});
 assert.equal(await send.isDisabled(),true);
 await page.getByRole('checkbox',{name:/Include the reviewed conversation excerpts/}).check();
 await page.setViewportSize({width:390,height:844});
 await send.scrollIntoViewIfNeeded();
 assert.ok(await page.locator('[data-part="feedback"]').evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({path:'/tmp/feedback-excerpt-mobile.png',animations:'disabled'});
 await send.click();
 await expect(page.getByText('Feedback sent. Thank you.',{exact:true}).first()).toBeVisible();
 const result=await inspect(),blobs=result.uploads.filter(row=>row.endpoint.endsWith('/git/blobs'));
 assert.equal(blobs.length,1);assert.equal(Buffer.from(blobs[0].payload.content,'base64').toString('utf8'),exact);
 assert.equal(result.calls.length,1);assert.match(result.calls[0].body,/publicly visible/);
 assert.equal(result.calls[0].title,'Newer agent title');assert.match(result.calls[0].body,/Newer agent details/);
 // Delay a new staging request before it reaches the server, then change
 // chats. Neither the old review nor its late result may enter the new draft.
 await page.reload();await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'feedback'}}));
 await page.getByRole('button',{name:'Attach transcript excerpt',exact:true}).click();
 await page.getByRole('button',{name:'Preview selected excerpt'}).click();
 await expect(page.getByLabel('Edit feedback excerpt')).toBeVisible();
 await page.getByRole('checkbox',{name:/I reviewed this exact text/}).check();
 const warnings=page.getByRole('checkbox',{name:/I reviewed the scope/});if(await warnings.count())await warnings.check();
 let requestArrived,releaseRequest;
 const waiting=new Promise(resolve=>requestArrived=resolve),requestGate=new Promise(resolve=>releaseRequest=resolve);
 releaseHeld=releaseRequest;
 const holdRequest=async route=>{
  if(route.request().postDataJSON()?.action!=='feedback.excerpt.stage')return route.continue();
  requestArrived();await requestGate;await route.continue();
 };
 await page.route('**/api/actions',holdRequest);
 await page.getByRole('button',{name:'Attach reviewed excerpt',exact:true}).click();await bounded(waiting,'staging request');
 await direct('session.create',{title:'Other synthetic conversation'});
 await direct('view.update',{patch:{panel:'feedback',feedbackDraft:{title:'Other draft',body:'Keep these details',attachments:[],includeDiagnostics:false}}});
 const rejected=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='feedback.excerpt.stage');
 releaseRequest();assert.equal((await rejected).status(),409);
 await page.unroute('**/api/actions',holdRequest);
 await expect(page.getByLabel('Title',{exact:true})).toHaveValue('Other draft');
 await expect(page.getByLabel('Details',{exact:true})).toHaveValue('Keep these details');
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().view.feedbackDraft.attachments),[]);
 assert.equal((await inspect()).calls.length,1);
 assert.deepEqual(errors,[]);
 console.log('Feedback excerpt browser passed: opt-in minimal range, redacted editable review, verified public destination, edits revoke consent, local staging, separate final consent, exact single upload, mobile layout, delayed staging lock, newer agent edits preserved, chat-switch rejection; GitHub intercepted.');
}catch(error){console.error(error);throw error}finally{releaseHeld();await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit')}}
