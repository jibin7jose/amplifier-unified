import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const otherWorkspaceId='00000000-0000-4000-8000-000000000002';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/configuration_scope_server.py',import.meta.url))]);
let browser,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),60000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});fixture.on('exit',code=>reject(Error('Fixture exited '+code+stderr)))});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],requests=[],failedResponses=[],responseReads=[],shellResponses=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('response',response=>{
  const path=new URL(response.url()).pathname;
  if(!path.startsWith('/api/'))return;
  const request=response.request(),payload=path==='/api/shell/resources'?request.postDataJSON():null;
  const row={path,status:response.status(),clientId:payload?.clientId,demandId:payload?.demandId};
  if(path==='/api/shell/resources')shellResponses.push(row);
  if(row.status>=400){
   failedResponses.push(row);
   responseReads.push(response.json().then(body=>{row.error=typeof body.error==='string'?body.error.slice(0,160):null},()=>{row.error=null}));
  }
 });
 page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/api/actions'))requests.push(request.postDataJSON());});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const closeSettingsDialog=async()=>{
  const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
  await expect(dialog).toBeVisible();
  // Compact Settings owns its close button; desktop uses the dialog header.
  const compactClose=dialog.getByRole('button',{name:'Close settings',exact:true});
  const close=await compactClose.isVisible()?compactClose:dialog.getByRole('button',{name:'Close panel',exact:true});
  await expect(close).toBeVisible();
  await close.click();
  await expect(dialog).toBeHidden();
 };
 const files=async()=>await (await page.request.get(base+'/fixture/configuration-files')).json();
 const savedModel=settings=>settings.config.providers.find(row=>row.id==='one').config.default_model;
 const noImageSettings=async()=>{
  await expect(page.getByRole('button',{name:'Manage image generation',exact:true,includeHidden:true})).toHaveCount(0);
  await expect(page.getByLabel('Use for images',{exact:true})).toHaveCount(0);
  await expect(page.locator('[data-settings-destination="image-generation"]')).toHaveCount(0);
 };
 const assertTarget=(args,target,scope)=>{
  assert.deepEqual(args.configurationTarget,target);
  assert.equal(args.scope,scope);
  assert.equal(args.id,'one');
  for(const key of ['sessionId','workspace','nativeProject','location'])assert.equal(Object.hasOwn(args,key),false,key);
 };
 await page.goto(base);await page.waitForSelector('#amp-one');
 const selectedId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.ok(selectedId);
 const before=await files();
 await action('view.update',{patch:{aiConnectionEditor:{scope:'project'},providerEditor:{scope:'local'}}});
 await openSettingsPage(page,'ai-connections');
 const content=()=>page.locator('.a-settings-page-content:not([hidden])');
 await expect(content().getByText('selected-only-model',{exact:false})).toHaveCount(0);
 await expect(content().getByText('fixture-model',{exact:false}).first()).toBeVisible();
 await noImageSettings();
 await content().getByRole('button',{name:'Advanced connection settings',exact:true}).click();
 await content().locator('[data-collection-id="one"] button').click();
 await expect(content().getByLabel('Default model',{exact:true})).toHaveValue('fixture-model');
 await expect(content().getByLabel('Save in',{exact:true})).toHaveCount(0);
 await content().getByLabel('Default model',{exact:true}).fill('global-saved-model');
 await page.locator('.a-settings-experience').getByRole('button',{name:'Save provider',exact:true}).click();
 await expect.poll(async()=>requests.filter(r=>r.action==='providers.save').length).toBe(1);
 assertTarget(requests.find(r=>r.action==='providers.save').args,{kind:'global'},'global');
 await expect.poll(async()=>savedModel((await files()).global)).toBe('global-saved-model');
 await expect(content().getByText('Provider saved. New conversations will use these settings.',{exact:true})).toBeVisible();
 const globalSaved=await files();
 assert.notDeepEqual(globalSaved.global,before.global);
 assert.deepEqual(globalSaved.selected,before.selected);
 assert.deepEqual(globalSaved.other,before.other);
 assert.deepEqual(globalSaved.global.routing,before.global.routing);
 for(const destination of ['providers','routing','registries']){
  await openSettingsPage(page,destination);
  assert.equal(await content().locator('option[value="project"],option[value="local"]').count(),0,destination);
 }
 await closeSettingsDialog();
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:otherWorkspaceId,workWorkspaceTab:'details'}});
 const workspace=page.getByRole('region',{name:'Workspace configuration',exact:true});
 await workspace.getByRole('group',{name:'Workspace settings pages',exact:true})
  .getByRole('button',{name:'AI connections',exact:true}).click();
 await expect(workspace.getByText('other-only-model',{exact:false})).toBeVisible();
 await noImageSettings();
 await workspace.getByRole('button',{name:'Advanced connection settings',exact:true}).click();
 await workspace.locator('[data-collection-id="one"] button').click();
 await expect(workspace.getByLabel('Default model',{exact:true})).toHaveValue('other-only-model');
 await expect(workspace.getByLabel('Save in')).toBeVisible();
 assert.deepEqual(await workspace.getByLabel('Save in').locator('option').evaluateAll(rows=>rows.map(row=>row.value)),['local','project']);
 await expect(workspace.getByLabel('Save in')).toHaveValue('local');
 await workspace.getByLabel('Save in').selectOption('project');
 await expect(workspace.getByLabel('Save in')).toHaveValue('project');
 await workspace.getByLabel('Save in').selectOption('local');
 await workspace.getByLabel('Default model',{exact:true}).fill('workspace-saved-model');
 await workspace.getByRole('button',{name:'Save provider',exact:true}).click();
 await expect.poll(async()=>requests.filter(r=>r.action==='providers.save').length).toBe(2);
 const changed=requests.filter(r=>r.action==='providers.save').at(-1).args;
 assertTarget(changed,{kind:'workspace',workspaceId:otherWorkspaceId},'local');
 await expect.poll(async()=>savedModel((await files()).other)).toBe('workspace-saved-model');
 await expect(workspace.getByText('Provider saved. New conversations will use these settings.',{exact:true})).toBeVisible();
 const workspaceSaved=await files();
 assert.notDeepEqual(workspaceSaved.other,before.other);
 assert.deepEqual(workspaceSaved.global,globalSaved.global);
 assert.deepEqual(workspaceSaved.selected,before.selected);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selectedId);
 await page.screenshot({path:'/tmp/settings-scope-workspace.png'});
 await openSettingsPage(page,'providers');
 await content().locator('[data-collection-id="one"] button').click();
 await expect(content().getByLabel('Default model',{exact:true})).toHaveValue('global-saved-model');
 await expect(content().getByLabel('Save in',{exact:true})).toHaveCount(0);
 await openSettingsPage(page,'ai-connections');
 await expect(content().getByText('global-saved-model',{exact:false})).toBeVisible();
 await expect(content().getByText('workspace-saved-model',{exact:false})).toHaveCount(0);
 await noImageSettings();
 assert.equal(await content().locator('option[value="project"],option[value="local"]').count(),0);
 await page.screenshot({path:'/tmp/settings-scope-global.png'});
 // The same global settings must survive rendering beside a help chat. A
 // canvas used to lose configurationTarget in the browser demand projection.
 await closeSettingsDialog();
 await action('view.update',{patch:{workSurface:'chat'}});
 await action('canvas.show',{kind:'settings'});
 const canvas=page.locator('.a-settings-canvas');
 await expect(canvas.getByText('global-saved-model',{exact:false})).toBeVisible();
 await expect(canvas.getByText('workspace-saved-model',{exact:false})).toHaveCount(0);
 await expect(canvas.getByText('Start with one connection',{exact:true})).toHaveCount(0);
 await openSettingsPage(page,'appearance');
 // Navigation paints optimistically; reload only after the host saves it.
 const savedNavigation=page.waitForResponse(response=>{
  const request=response.request();
  if(request.method()!=='POST'||!request.url().includes('/api/actions'))return false;
  const body=request.postDataJSON();
  return body?.action==='view.update'&&body?.args?.patch?.settingsExpanded?.[0]==='ai-connections';
 });
 await openSettingsPage(page,'ai-connections');
 assert.equal((await savedNavigation).ok(),true);
 await expect(canvas.getByText('global-saved-model',{exact:false})).toBeVisible();
 await page.reload();
 await expect(canvas.getByText('global-saved-model',{exact:false})).toBeVisible();
 await noImageSettings();
 await action('canvas.close');
 await openSettingsPage(page,'ai-connections');

 const help=page.getByRole('button',{name:'Help me with settings',exact:true});
 await expect(help).toHaveCount(1);
 await expect(help).toBeVisible();
 const top=await help.boundingBox();
 await page.locator('.a-settings-content').evaluate(node=>node.scrollTop=node.scrollHeight);
 assert.equal((await help.boundingBox()).y,top.y,'Help stays in the fixed header');
 assert.equal(await page.locator('.a-settings-content').getByRole('button',{name:'Help me with settings',exact:true}).count(),0);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:800});
  await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
  await expect.poll(async()=>{const rect=await help.boundingBox();return Boolean(rect&&rect.x>=0&&rect.x+rect.width<=width)}).toBe(true);
  await expect(page.getByRole('button',{name:'Close settings',exact:true})).toBeVisible();
 }
 await page.screenshot({path:'/tmp/settings-scope-header-mobile.png'});
 assert.deepEqual(await files(),workspaceSaved,'Canvas navigation and reload must not change any configuration root');
 // Every unrelated account/model/navigation edit kept the legacy denials.
 for(const key of ['global','other','otherProject'])assert.equal((await files())[key].image_generation.mode,'off');
 const diagnostics=()=>content().getByRole('region',{name:'Capability configuration diagnostics'});
 await expect(diagnostics().getByText('tool-image',{exact:true})).toBeVisible();
 const usePolicy=diagnostics().getByRole('button',{name:'Use composed policy for new chats'});
 await expect(usePolicy).toBeDisabled();
 await diagnostics().getByRole('checkbox').focus();await page.keyboard.press('Space');
 await expect(usePolicy).toBeEnabled();
 await usePolicy.focus();await page.keyboard.press('Enter');
 await expect.poll(async()=>(await files()).global.image_generation.mode).toBeUndefined();
 assert.equal((await files()).global.image_generation.retained,true);
 await expect(diagnostics().getByRole('status').filter({hasText:'Readback confirmed'})).toBeVisible();
 assert.deepEqual((await files()).other,workspaceSaved.other);
 assert.deepEqual((await files()).otherProject,workspaceSaved.otherProject);
 await expect(page.getByRole('alert')).toHaveCount(0);
 await page.screenshot({path:(process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp')+'/reconcile-global-narrow.png'});
 await page.setViewportSize({width:1440,height:1000});
 await closeSettingsDialog();
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:otherWorkspaceId,workWorkspaceTab:'details',workWorkspaceSettings:'registries'}});
 const scoped=workspace.getByRole('region',{name:'Capability configuration diagnostics'});
 await expect(scoped.locator('.a-config-diagnostic')).toHaveCount(2);
 await scoped.getByRole('checkbox').check();
 await scoped.getByRole('button',{name:'Use composed policy for new chats'}).click();
 await expect.poll(async()=>(await files()).other.image_generation.mode).toBeUndefined();
 assert.equal((await files()).otherProject.image_generation.mode,'off','Project denial still applies');
 await workspace.getByLabel('Save in',{exact:true}).selectOption('project');
 await expect(scoped.locator('.a-config-diagnostic')).toHaveCount(1);
 await scoped.getByRole('checkbox').check();
 await scoped.getByRole('button',{name:'Use composed policy for new chats'}).click();
 await expect.poll(async()=>(await files()).otherProject.image_generation.mode).toBeUndefined();
 assert.equal((await files()).otherProject.image_generation.retained,true);
 assert.deepEqual((await files()).selected,before.selected);
 const reconciliations=requests.filter(r=>r.action==='modules.reconcile');
 assert.deepEqual(reconciliations.map(r=>r.args.scope),['global','local','project']);
 assert.deepEqual(reconciliations.map(r=>r.args.configurationTarget),[{kind:'global'},{kind:'workspace',workspaceId:otherWorkspaceId},{kind:'workspace',workspaceId:otherWorkspaceId}]);
 assert.ok(reconciliations.every(r=>r.args.confirm===true&&r.args.resolution==='inherit'&&r.args.expectedRevision.length===64));
 await expect(page.getByRole('alert')).toHaveCount(0);
 await page.screenshot({path:(process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp')+'/reconcile-workspace-desktop.png'});
 await page.reload();
 await page.waitForFunction(id=>window.amplifier?.getState()?.selectedSessionId===id,selectedId);
 await noImageSettings();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selectedId);
 assert.deepEqual(requests.filter(r=>['providers.configureImages','providers.imageModels'].includes(r.action)),[]);
 assert.deepEqual(requests.filter(r=>r.action==='runtime.control'&&['provider.select','configuration.apply'].includes(r.args?.operation)),[]);
 await expect(page.getByRole('alert')).toHaveCount(0);
 await Promise.all(responseReads);
 // Scope navigation can supersede an in-flight shell read. Require the exact
 // stale-scope response AND a later successful newer demand for that client.
 const recovered=row=>row.path==='/api/shell/resources'&&row.status===409&&
  ['Shell scope changed; request the current client scope.','Shell demand is stale or reuses an identity for different resources.'].includes(row.error)&&
  typeof row.clientId==='string'&&Number.isInteger(row.demandId)&&
  shellResponses.some(next=>next.status===200&&next.clientId===row.clientId&&next.demandId>row.demandId);
 await expect.poll(()=>failedResponses.filter(row=>!recovered(row))).toEqual([]);
 console.log('Shell superseded-read observations: '+JSON.stringify(failedResponses));
 assert.deepEqual(errors,[]);
 console.log('PASS: image settings absent; ordinary provider saves target independent roots; scope controls, selected chat and drafts isolated.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
