import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

// Real navigation controls and projections, disposable 108-workspace history.
// API calls only prepare pins; all measured navigation is through visible UI.
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/standard-ux-navigation-gaps';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/chat_library_server.py'],{env:{...process.env,AMPLIFIER_NAVIGATION_GAPS:'1'},stdio:['ignore','pipe','pipe']});
const evidence=new StandardUxEvidence(output,{source:'integration-fc0d05227 plus test-only fixture',state:'synthetic-standard',runtime:'passive; no provider',timings:'shared-load observations'});
let browser,page,stderr='',releaseSelection;fixture.stderr.on('data',b=>stderr=(stderr+b).slice(-16000));
try{
 await mkdir(output,{recursive:true});
 const port=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout '+stderr)),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+' '+stderr))});fixture.stdout.on('data',b=>{text+=b;const match=text.match(/\{"port":\s*(\d+)\}/);if(match){clearTimeout(timer);resolve(Number(match[1]))}})});
 const base='http://127.0.0.1:'+port;browser=await chromium.launch({headless:true,args:['--single-process','--no-zygote','--disable-gpu']});
 page=await browser.newPage({viewport:{width:1400,height:1000},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(8000);evidence.page=page;
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState()),info=async()=>(await page.request.get(base+'/api/fixture/info')).json();
 const act=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const click=(name,target)=>evidence.action(name,()=>target.click());
 const probe=async(id,flows,run)=>{const result=await evidence.probe(id,flows,async()=>{await run();return await evidence.snapshot(page,id,'#amp-one')});if(result.status!=='passed')throw Error('Stopped after '+id)};
 await page.goto(base);await showFixtureConversation(page,{navPinned:true});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();const initial=await info(),a=initial.initialSession,b=initial.quietSession;
 const section=id=>page.locator(`[data-sidebar-section="${id}"]`),toggle=id=>section(id).locator('.a-sidebar-section-heading>button').first();
 const pinned=section('pinned'),composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await probe('section-isolation-and-empty-pins',['nav.sections'],async()=>{
  await expect(section('recent').locator('[data-session-id]')).toHaveCount(20);
  await expect(pinned).toHaveCount(0);evidence.assertion('Empty pinned section is omitted after resources settle',await pinned.count(),0);
  await act('session.pin',{id:a,pinned:true});await act('session.pin',{id:b,pinned:true});await expect(pinned.locator('[data-session-id]')).toHaveCount(2);
  const before=await Promise.all(['pinned','workspaces','recent'].map(id=>toggle(id).getAttribute('aria-expanded')));
  await click('Collapse only Recent',toggle('recent'));await expect(toggle('recent')).toHaveAttribute('aria-expanded','false');
  evidence.assertion('Pinned unchanged',await toggle('pinned').getAttribute('aria-expanded'),before[0]);evidence.assertion('Workspace unchanged',await toggle('workspaces').getAttribute('aria-expanded'),before[1]);
  await toggle('recent').focus();await page.keyboard.press('Enter');await expect(toggle('recent')).toHaveAttribute('aria-expanded','true');
  await act('session.pin',{id:a,pinned:false});await act('session.pin',{id:b,pinned:false});await expect(pinned).toHaveCount(0);
  await act('session.pin',{id:a,pinned:true});await act('session.pin',{id:b,pinned:true});await expect(pinned.locator('[data-session-id]')).toHaveCount(2);
 });
 const pin=id=>pinned.locator(`[data-session-id="${id}"]`),choose=id=>pin(id).locator('[data-navigation-select]');
 const agree=async(id,title,text)=>{
  await expect.poll(async()=>(await state()).selectedSessionId).toBe(id);
  await expect(page.locator('.a-work-heading strong')).toHaveText(title);await expect(pin(id)).toHaveClass(/is-selected/);
  await expect(page.locator('.a-messages')).toContainText(text);
  await click('Open current chat actions',page.getByRole('button',{name:'Chat',exact:true}));
  const menu=page.getByRole('group',{name:'Chat',exact:true});await expect(menu.getByRole('button',{name:'Chat info',exact:true})).toBeEnabled();
  await click('Inspect selected chat identity',menu.getByRole('button',{name:'Chat info',exact:true}));
  await expect(page.getByRole('dialog').last()).toContainText(id);
  await click('Close exact chat info',page.getByRole('button',{name:'Close panel',exact:true}));
 };
 await probe('selected-context-and-late-receipt',['nav.select'],async()=>{
  await click('Select saved Alpha chat',choose(a));await agree(a,'Alpha 201','Saved response for alpha-201');await composer.fill('Alpha private draft');
  let entered;const gate=new Promise(r=>entered=r);let held=false;
  await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON(),command=body?.action==='shell.command'?body.args:body;if(!held&&command?.action==='session.select'&&command.args?.id===b){held=true;const response=await route.fetch();await new Promise(r=>{releaseSelection=r;entered()});try{await route.fulfill({response})}catch{}}else await route.continue()});
  await click('Select Quiet with acknowledgement held',choose(b));await Promise.race([gate,new Promise((_,reject)=>setTimeout(()=>reject(Error('Expected shell selection receipt did not reach hold gate')),8000))]);
  await click('Return to Alpha before old receipt',choose(a));await expect.poll(async()=>(await state()).selectedSessionId).toBe(a);
  releaseSelection();releaseSelection=null;await page.unrouteAll({behavior:'wait'});
  await agree(a,'Alpha 201','Saved response for alpha-201');await expect(composer).toHaveDraft('Alpha private draft');
  await click('Select Quiet normally',choose(b));await agree(b,'Quiet older chat','An earlier fixture response');
  await click('Return to exact Alpha',choose(a));await agree(a,'Alpha 201','Saved response for alpha-201');await expect(composer).toHaveDraft('Alpha private draft');
 });
 await probe('workspace-hierarchy-and-page-scope',['nav.workspace-browse'],async()=>{
  await click('Open All workspaces',page.getByRole('button',{name:'All workspaces',exact:true}));
  const explorer=page.locator('.a-work-browser .a-workspace-explorer'),filter=explorer.getByRole('searchbox',{name:'Filter workspaces',exact:true});
  await filter.fill('*/paged/team-*');
  // The old unfiltered page also has100 rows and two pages. Wait for the exact
  // authoritative scope before using its contents as the paging oracle.
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.workspaces.workspaceExplorer?.filter)).toBe('*/paged/team-*');
  await expect(explorer.locator('.a-workspace-row')).toHaveCount(100);await expect(explorer).toContainText('Page 1 of 2');
  const pageOne=await explorer.locator('[data-workspace-path]').evaluateAll(rows=>rows.map(r=>r.dataset.workspacePath));
  await click('Next workspace folder page',explorer.getByRole('button',{name:'Show more workspace folders',exact:true}));await expect(explorer.locator('.a-workspace-row')).toHaveCount(5);await expect(explorer).toContainText('Page 2 of 2');
  const pageTwo=await explorer.locator('[data-workspace-path]').evaluateAll(rows=>rows.map(r=>r.dataset.workspacePath));evidence.assertion('Every expected workspace exactly once',new Set([...pageOne,...pageTwo]).size,105);
  evidence.assertion('Page results stay in exact filter',pageTwo.every(path=>/\/paged\/team-\d{3}$/.test(path)));
  await filter.fill('*/paged/team-104');await expect(explorer.locator('.a-workspace-row')).toHaveCount(1);await expect(explorer.getByRole('button',{name:'Show more workspace folders'})).toHaveCount(0);await expect(explorer.locator('.a-workspace-count')).toHaveAttribute('aria-label','1 chats');
  await filter.fill('no-such-workspace');await expect(explorer.getByText('No matching workspaces.',{exact:true})).toBeVisible();await expect(explorer.locator('.a-workspace-row')).toHaveCount(0);
  await filter.fill('');await click('Browse actual folder hierarchy',explorer.getByRole('button',{name:'Browse folders',exact:true}));
  await click('Browse parent folder',explorer.getByRole('button',{name:'Go to parent workspace folder',exact:true}));
  const folder=initial.paths.one.replace(/\/development\/playground$/,'/paged'),ancestor=explorer.locator(`[data-workspace-path="${folder}"]`);
  await expect(ancestor).toBeVisible();await expect(ancestor).not.toHaveClass(/is-workspace/);await expect(ancestor.locator('.a-workspace-count')).toHaveAttribute('aria-label','105 workspaces');
  await expect(ancestor.getByRole('button',{name:'Open chats in '+folder,exact:true})).toHaveCount(0);
  await click('Browse non-workspace ancestor',ancestor.getByRole('button',{name:'Browse '+folder,exact:true}));await expect(explorer.locator('.a-workspace-row')).toHaveCount(100);await expect(explorer).toContainText('Page 1 of 2');
  evidence.assertion('Browsing folders leaves selected chat alone',(await state()).selectedSessionId,a);
 });
 await probe('narrow-navigation-focus-and-close',['nav.drawer'],async()=>{
  await click('Return to pinned Alpha',choose(a));await expect(composer).toHaveDraft('Alpha private draft');
  await page.setViewportSize({width:390,height:844});const drawer=page.getByRole('dialog',{name:'Workspaces and conversations',exact:true}),opener=page.getByRole('button',{name:'Open navigation',exact:true});
  if(await drawer.isVisible())await page.getByRole('button',{name:'Close navigation',exact:true}).tap();
  await opener.focus();await page.keyboard.press('Enter');await expect(drawer).toBeVisible();await page.keyboard.press('Escape');await expect(drawer).toBeHidden();await expect(opener).toBeFocused();
  await opener.tap();await expect(drawer).toBeVisible();await page.getByRole('button',{name:'Close navigation',exact:true}).tap();await expect(drawer).toBeHidden();await expect(opener).toBeFocused();
  await expect(composer).toHaveDraft('Alpha private draft');await expect(composer).toBeEnabled();evidence.assertion('Conversation no longer inert',await composer.evaluate(el=>!!el.closest('[inert]')),false);
  const final=await info();evidence.assertion('No browsing runtime starts',final.runtimeStarts,[]);evidence.assertion('No browsing runtime inputs',final.runtimeSends,[]);evidence.assertion('No browser exceptions',errors,[]);
 });
 console.log('PASS navigation gaps:4 exact probes; passive history, folder paging and real touch/keyboard drawer closure.');
}finally{
 releaseSelection?.();await browser?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await new Promise(resolve=>{const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})})}
 await writeFile(output+'/fixture.stderr',stderr);await evidence.save();
}
