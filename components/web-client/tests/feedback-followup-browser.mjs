import {showFixtureConversation} from './browser-chat-pages.mjs';
// Isolated real UI and service, with all GitHub operations intercepted.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFileSync} from 'node:fs';
import {once} from 'node:events';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, sys, tempfile
from pathlib import Path
from aiohttp import web
sys.path.insert(0, ${JSON.stringify(fileURLToPath(new URL('../../tests/fixtures',import.meta.url)))})
import settings_ui_server as fixture
from amplifier_web import feedback
posts=[]
url=feedback.ISSUES_URL+'/42'
issue={'id':42,'number':42,'html_url':url,'user':{'id':7},'title':'Original report','body':'Original details\\n<!-- amplifier-feedback:original-feedback -->','state':'open','updated_at':'fixture-1'}
async def api(endpoint,payload,*,method=None):
 if method=='PATCH':
  posts.append(payload);issue.update(payload);issue['updated_at']+='x';return issue
 if endpoint.startswith('search/issues?'):return {'total_count':0,'incomplete_results':False,'items':[]}
 if endpoint=='user':return {'id':7,'login':'fixture-owner'}
 if payload is not None:
  posts.append(payload)
  return {'id':123,'html_url':url+'#issuecomment-123','user':{'id':7}}
 if '/comments?' in endpoint:return [{'id':99,'body':'Maintainer response','user':{'id':8,'login':'maintainer'}}]
 return dict(issue)
feedback.github_api=api
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-followup-ui-') as home:
  app=await fixture.main(Path(home));service=app['service']
  service.feedback.accept({'requestId':'original-feedback','title':'Original report','body':'Original details','category':'bug','includeDiagnostics':False})
  await service.feedback.update('original-feedback',status='submitted',url=url)
  service.feedback.accept({'requestId':'unknown-feedback','title':'Unknown report','body':'Unknown details','category':'bug','includeDiagnostics':False})
  await service.feedback.update('unknown-feedback',status='unknown',url=None)
  from amplifier_web.capacity import usage_snapshot
  session=service._session();sid=session['id']
  # Publish actual runtime observations so storage and display revisions see
  # every receipt; mutating an execution tree directly bypasses that boundary.
  for n in range(3):
   await service.on_runtime_event('execution.event',{'id':str(n),'sessionId':sid,'rootSessionId':sid,'kind':'llm','phase':'completed','provider':'fixture','model':'model','producerId':'fixture-worker','liveObservation':True,'revision':1,'startedAt':1,'endedAt':2,'usage':{'inputTokens':8,'outputTokens':2,'totalTokens':10,'costUsd':.01,'costType':'reported'}})
  assert usage_snapshot(service._session(sid))['calls']==3
  async def inspect(request):return web.json_response({'posts':posts,'issue':issue})
  async def maintainer(request):
   issue['body']+='\\nMaintainer addition after the earlier read';issue['updated_at']='fixture-maintainer'
   return web.json_response({'ok':True})
  app.router.add_post('/api/fixture/maintainer',maintainer)
  app.router.add_get('/api/fixture/followup',inspect)
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${fixtureLog}`)))});
let vite,browser,diagnosticPage;
try{
 const port=await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture timeout')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,cacheDir:fileURLToPath(new URL('../.vite-feedback-followup',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];diagnosticPage=page;let pending;
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const command=request.postDataJSON();if(command?.action==='feedback.comment')pending=command.args}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await showFixtureConversation(page,{navPinned:false});await page.waitForSelector('#amp-one');
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill('Keep my private original-chat draft');
 await expect(composer).toHaveDraft('Keep my private original-chat draft');
 const originalChat=await page.evaluate(()=>{const state=window.amplifier.getState();return {id:state.selectedSessionId,messages:state.sessions.find(row=>row.id===state.selectedSessionId)?.messages}});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.getByRole('button',{name:'App options',exact:true}).click();
 await page.getByRole('button',{name:'Send feedback',exact:true}).click();
 await page.getByRole('button',{name:'Check delivery',exact:true}).click();
 await page.getByText('No matching feedback was found in this search. Delivery remains unknown; nothing was resent.',{exact:true}).waitFor();
 assert.equal((await page.evaluate(()=>fetch('/api/fixture/followup').then(r=>r.json()))).posts.length,0);
 await page.getByLabel('Submitted report',{exact:true}).selectOption('original-feedback');
 await page.getByRole('button',{name:'Refresh report',exact:true}).click();
 await page.getByText('Feedback report loaded.',{exact:true}).waitFor();
 await page.getByText('Maintainer response',{exact:true}).waitFor();
 // Each browser owns its selected report page and unsent follow-up draft.
 const other=process.env.CHROMIUM_SINGLE_PROCESS==='1'?await page.context().newPage():await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 other.on('pageerror',error=>errors.push(error.message));
 await other.goto(vite.resolvedUrls.local[0]);await other.waitForSelector('#amp-one');
 assert.notEqual(await other.evaluate(()=>window.amplifier.getState().client.id),await page.evaluate(()=>window.amplifier.getState().client.id));
 await other.getByRole('button',{name:'Open navigation',exact:true}).click();
 await other.getByRole('button',{name:'App options',exact:true}).click();
 await other.getByRole('button',{name:'Send feedback',exact:true}).click();
 await other.getByLabel('Submitted report',{exact:true}).selectOption('original-feedback');
 await other.getByLabel('Add a comment',{exact:true}).fill('Unsent draft on the other browser.');
 await other.evaluate(()=>window.amplifier.dispatch('feedback.get',{requestId:'second-browser-read',feedbackId:'original-feedback',page:2}));
 await other.waitForFunction(()=>window.amplifier.getState().feedback.report?.page===2);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().feedback.report?.page),1);
 await page.getByLabel('Add a comment',{exact:true}).fill('Additional details from the browser.');
 await page.getByRole('button',{name:'Send comment',exact:true}).click();
 await page.getByText('Comment added to your feedback report.',{exact:true}).waitFor();
 assert.equal(await page.getByRole('link',{name:'View comment',exact:true}).getAttribute('href'),'https://github.com/microsoft/amplifier-unified/issues/42#issuecomment-123');
 await page.waitForFunction(()=>window.amplifier.getState().view.feedbackFollowupDraft.body===''&&!window.amplifier.getState().view.feedbackFollowupDraft.pending);
 assert.ok(pending?.requestId);
 await page.evaluate(args=>window.amplifier.dispatch('feedback.comment',args),pending);
 const sent=await page.evaluate(()=>fetch('/api/fixture/followup').then(response=>response.json()));
 assert.equal(sent.posts.length,1);assert.match(sent.posts[0].body,/Additional details from the browser/);
 assert.equal(await other.getByLabel('Add a comment',{exact:true}).inputValue(),'Unsent draft on the other browser.');
 assert.equal(await other.evaluate(()=>window.amplifier.getState().feedback.report?.page),2);
 await page.getByRole('button',{name:'Draft a correction',exact:true}).click();
 await page.getByLabel('Corrected description',{exact:true}).fill('A corrected reproduction without overwriting the maintainer.');
 await page.getByLabel('Corrected description',{exact:true}).blur();
 await page.waitForFunction(()=>window.amplifier.getState().view.feedbackCorrectionDraft?.body==='A corrected reproduction without overwriting the maintainer.');
 await page.reload();await page.getByLabel('Corrected description',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Corrected description',{exact:true}).inputValue(),'A corrected reproduction without overwriting the maintainer.');
 assert.equal(await page.getByRole('button',{name:'Publish correction comment',exact:true}).isEnabled(),false);
 await page.evaluate(()=>fetch('/api/fixture/maintainer',{method:'POST'}));
 const reviewStarted=Date.now();
 await page.getByRole('button',{name:'Review changes',exact:true}).click();
 await page.getByRole('region',{name:'Change comparison',exact:true}).waitFor();
 const reviewMs=Date.now()-reviewStarted;
 await page.getByText('The report changed since you opened it. Review the latest version before publishing a correction.',{exact:true}).waitFor();
 await page.getByText('Latest on GitHub',{exact:true}).click();
 await page.getByText(/Maintainer addition after the earlier read/).waitFor();
 const reviewed=await page.evaluate(()=>window.amplifier.getState().feedback.editReview);
 assert.equal(reviewed.review.original.body,'Original details');
 assert.equal(reviewed.review.previous.body,'Original details\n<!-- amplifier-feedback:original-feedback -->');
 assert.match(reviewed.review.latest.body,/Maintainer addition/);
 if(process.env.FEEDBACK_REVIEW_SCREENSHOT)await page.screenshot({path:process.env.FEEDBACK_REVIEW_SCREENSHOT});
 if(process.env.FEEDBACK_REVIEW_RESULT)writeFileSync(process.env.FEEDBACK_REVIEW_RESULT,JSON.stringify({reviewMs,review:reviewed,githubWritesBeforeReview:1,transport:'real AppService HTTP/SSE; GitHub intercepted',chromiumSingleProcess:process.env.CHROMIUM_SINGLE_PROCESS==='1'},null,2));
 assert.equal(await page.getByRole('link',{name:'Open on GitHub',exact:true}).getAttribute('href'),'https://github.com/microsoft/amplifier-unified/issues/42');
 assert.equal((await page.evaluate(()=>fetch('/api/fixture/followup').then(r=>r.json()))).posts.length,1);
 await page.getByLabel('I reviewed the latest report and want to publish this correction comment.',{exact:true}).check();
 await page.getByRole('button',{name:'Publish correction comment',exact:true}).click();
 await page.getByText('Correction version added; original report retained.',{exact:true}).waitFor();
 const correction=await page.evaluate(()=>window.amplifier.getState().view.feedbackCorrectionDraft.pending);
 await page.evaluate(args=>window.amplifier.dispatch('feedback.update',args),correction);
 assert.equal((await page.evaluate(()=>fetch('/api/fixture/followup').then(r=>r.json()))).posts.length,2);
 assert.match(await page.locator('.a-feedback-report').innerText(),/Original details/);
 assert.match((await page.evaluate(()=>fetch('/api/fixture/followup').then(r=>r.json()))).issue.body,/Maintainer addition after the earlier read/);
 await page.getByRole('button',{name:'Refresh report',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().feedback.report?.updatedAt==='fixture-maintainer');
 await page.getByRole('button',{name:'Close my report',exact:true}).click();
 await page.getByText(/Feedback report closed/).waitFor();
 await page.getByRole('button',{name:'Refresh report',exact:true}).click();
 await page.getByRole('button',{name:'Reopen my report',exact:true}).waitFor();
 await page.getByRole('button',{name:'Reopen my report',exact:true}).click();
 await page.getByText(/Feedback report reopened/).waitFor();
 assert.equal((await page.evaluate(()=>fetch('/api/fixture/followup').then(r=>r.json()))).posts.length,4);
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth+1));
 await page.reload();await page.getByText('Comment added to your feedback report.',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Add a comment',{exact:true}).inputValue(),'');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveDraft('Keep my private original-chat draft');
 assert.deepEqual(await page.evaluate(()=>{const state=window.amplifier.getState();return {id:state.selectedSessionId,messages:state.sessions.find(row=>row.id===state.selectedSessionId)?.messages}}),originalChat,'Feedback receipt/retry/correction must preserve original conversation');
 assert.ok(!(await page.evaluate(()=>fetch('/api/fixture/followup').then(response=>response.json()))).posts.some(row=>JSON.stringify(row).includes('Keep my private original-chat draft')));
 await page.setViewportSize({width:1280,height:900});
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'session-details'}}));
 const usage=page.getByRole('region',{name:'Conversation usage'});
 await usage.getByText(/3 recorded model calls/).waitFor();
 assert.match(await usage.innerText(),/30/);assert.match(await usage.innerText(),/\$0.03/);
 await page.getByText('Usage data',{exact:true}).click();
 const exported=JSON.parse(await usage.locator('pre').innerText());assert.equal(exported.metrics.calls.value,3);assert.equal(exported.metrics.grossTotalTokens.value,30);
 assert.deepEqual(errors,[]);
 console.log('Feedback lifecycle browser passed: report/comments, unknown delivery reconciliation without reposting, persistent correction drafts, explicit original/previous/latest/proposed review and confirmation, maintainer text preservation, exact agent retry, close/reopen without body changes, independent client drafts, narrow layout, and complete conversation usage totals; no live GitHub writes.');
}catch(error){
 if(process.env.FEEDBACK_REVIEW_RESULT&&diagnosticPage&&!diagnosticPage.isClosed()){
  const diagnostics=await diagnosticPage.evaluate(()=>({view:window.amplifier?.getState()?.view,feedback:window.amplifier?.getState()?.feedback,text:document.body.innerText}));
  writeFileSync(process.env.FEEDBACK_REVIEW_RESULT+'.failure.json',JSON.stringify({error:String(error),...diagnostics},null,2));
 }
 throw error;
}finally{
 await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
