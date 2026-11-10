import {openSettingsPage} from './browser-settings.mjs';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{setupJourney:'done',navPinned:true,panel:'settings',settingsSection:'setup',settingsExpanded:['providers'],providerEditor:{id:'one',module:'provider-openai',model:'model-a',config:'{}',scope:'global',credentialMode:'private',source:'',advanced:false,detailOpen:true,dirty:true}},sessions:[{id:'chat',sessionKind:'root',title:'Saved chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{configurationTarget:{kind:'global'},providers:[{id:'one',module:'provider-openai',config:{default_model:'model-a'},credentialsConfigured:true}],providersLoadedAt:1,providersWorkspace:'/fixture',metadata:{'provider-openai':{info:{config_fields:[]}}},providerCatalogs:{one:{phase:'ready',models:[{id:'model-a'},{id:'model-b'}]}}},canvas:{open:false}};
let browser,vite,page;const errors=[],waiting=[],calls=[];
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function pending(action){for(let i=0;i<100&&!waiting.some(row=>row.body.action===action);i++)await sleep(10);const index=waiting.findIndex(row=>row.body.action===action);assert.ok(index>=0,'Missing '+action);return waiting.splice(index,1)[0]}
const shell=createShellHttpFixture(()=>state);
try{
 vite=await createServer({configFile:false,cacheDir:fileURLToPath(new URL('../.vite-legacy-mocks',import.meta.url)),root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-sandbox','--single-process','--no-zygote']}: {})});page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   const body=route.request().postDataJSON();calls.push(body);
   if(body.action==='shell.report')return route.fulfill({json:{accepted:true}});
   if(['providers.models','locations.list','session.draft','notifications.save','maintenance.backup'].includes(body.action)){waiting.push({route,body});return}
   if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
   return route.fulfill({json:{accepted:true,state:shell.state()}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);await page.locator('#provider-model').waitFor();
 const region=page.locator('[data-activity-region="provider-models"]');
 const surface=el=>{const s=getComputedStyle(el);return {background:s.backgroundImage,shadow:s.boxShadow,animation:s.animationName,border:s.borderTopWidth}};
 const beforeSurface=await region.evaluate(surface);
 const button=page.locator('[data-action="providers.models"]'),beforeIcon=await button.locator('svg').first().boundingBox();
 await button.click();const refresh=await pending('providers.models');
 assert.equal(await page.locator('[data-action="providers.models"][data-action-pending]').count(),1);
 assert.deepEqual(await region.evaluate(surface),beforeSurface,'busy does not paint a region wash or top edge');
 const pendingIcon=await button.locator('svg').first().evaluate(el=>({width:el.clientWidth,height:el.clientHeight}));
 assert.equal(pendingIcon.width+4,beforeIcon.width,'busy icon keeps its existing outer width');
 assert.equal(pendingIcon.height+4,beforeIcon.height,'busy icon keeps its existing outer height');
 assert.equal(await button.locator('svg').first().evaluate(el=>getComputedStyle(el).animationName),'a-action-working');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await button.locator('svg').first().evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.emulateMedia({reducedMotion:'no-preference'});
 // Test the shared feedback against presentation preferences and an authored
 // surface. Pending state must not override theme decoration or geometry.
 const original=await page.locator('#amp-one').evaluate(el=>({style:el.getAttribute('style'),scheme:el.dataset.themeScheme,detail:el.dataset.interfaceDetail,decorations:el.dataset.decorations}));
 for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed'])for(const decorations of ['on','off'])for(const reducedMotion of ['reduce','no-preference']){
  await page.emulateMedia({colorScheme:scheme,reducedMotion});
  await page.locator('#amp-one').evaluate((el,{scheme,detail,decorations})=>{el.dataset.themeScheme=scheme;el.dataset.interfaceDetail=detail;el.dataset.decorations=decorations}, {scheme,detail,decorations});
  const surfaces=await region.evaluate(el=>{const fields=['backgroundImage','backgroundColor','boxShadow','borderTopWidth','borderTopColor','animationName'];const read=()=>Object.fromEntries(fields.map(k=>[k,getComputedStyle(el)[k]]));const pending=read();el.removeAttribute('data-region-pending');const idle=read();el.setAttribute('data-region-pending','');return {pending,idle}});
  assert.deepEqual(surfaces.pending,surfaces.idle);
  assert.equal(await button.locator('svg').first().evaluate(el=>getComputedStyle(el).animationName),reducedMotion==='reduce'?'none':'a-action-working');
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
 assert.equal(await button.evaluate(el=>getComputedStyle(el).opacity),'1');
 assert.equal(await button.locator('svg').first().evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.emulateMedia({forcedColors:'none',colorScheme:'light',reducedMotion:'no-preference'});
 await page.locator('#amp-one').evaluate((el,value)=>{el.dataset.themeScheme=value.scheme;el.dataset.interfaceDetail=value.detail;el.dataset.decorations=value.decorations},original);
 assert.equal(await page.locator('[data-activity-region="provider-models"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('#provider-model').inputValue(),'model-a');
 assert.equal(await page.locator('#provider-model-catalog option[value="model-b"]').count(),1);
 assert.equal(await page.locator('[data-activity-region="provider-options"]').getAttribute('aria-busy'),null);
 state.setup.operations={'providers.models:one':{phase:'working',commandId:refresh.body.id,configurationTarget:{kind:'global'}}};state.setup.providerCatalogs.one={...state.setup.providerCatalogs.one,phase:'working'};state.revision++;
 await refresh.route.fulfill({json:{accepted:true,state:shell.state()}});
 await page.waitForFunction(()=>!document.querySelector('[data-action="providers.models"][data-action-pending]'));
 assert.equal(await page.locator('[data-activity-region="provider-models"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('[data-action="providers.models"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('#provider-model-catalog option[value="model-b"]').count(),1);
 await page.locator('#provider-source').count();
 // User edits survive independent completions and dismissal.
 state.setup.operations['providers.models:one']={phase:'error',commandId:refresh.body.id,error:'Fixture unavailable',configurationTarget:{kind:'global'}};state.setup.providerCatalogs.one={...state.setup.providerCatalogs.one,phase:'error'};state.revision++;
 await page.evaluate(value=>window.emitState(value),shell.state());
 await page.waitForFunction(()=>!document.querySelector('[data-activity-region="provider-models"][data-region-pending]'));
 assert.equal(await page.locator('#provider-model').evaluate(el=>el.tagName),'INPUT');
 assert.equal(await page.locator('#provider-model-options option[value="model-b"]').count(),1);
 await page.locator('#provider-model').fill('custom-model-after-failure');await page.locator('#provider-id').click();
 await page.waitForFunction(()=>window.amplifier.getState().view.providerEditor.model==='custom-model-after-failure');
 await page.locator('#provider-id').fill('unsaved-name');
 assert.equal(await page.locator('#provider-id').inputValue(),'unsaved-name');
 await page.locator('#provider-id').click();assert.equal(await page.locator('[role="dialog"]').count(),1);
 const input=await page.locator('#provider-id').boundingBox();await page.mouse.move(input.x+10,input.y+10);await page.mouse.down();await page.mouse.move(2,2);await page.mouse.up();assert.equal(await page.locator('[role="dialog"]').count(),1);
 await page.mouse.click(2,2);await page.getByRole('alertdialog').waitFor();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();assert.equal(await page.locator('#provider-id').inputValue(),'unsaved-name');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{providerEditor:{...window.amplifier.getState().view.providerEditor,advanced:true}}}));
 await page.locator('.a-path-field').getByRole('button',{name:'Browse',exact:true}).click();const locations=await pending('locations.list');
 state.locationListing={controlId:'provider-source',path:'/fixture',parent:'/',entries:[{name:'Old folder',path:'/fixture/old',directory:true}]};state.actionStatus={'locations.list':{phase:'ready',target:{controlId:'provider-source'}}};state.revision++;
 await locations.route.fulfill({json:{accepted:true,state:shell.state()}});await page.getByRole('button',{name:'Old folder',exact:true}).waitFor();
 await page.getByRole('button',{name:'Go',exact:true}).click();const nextLocations=await pending('locations.list');state.actionStatus['locations.list'].phase='working';state.revision++;await page.evaluate(value=>window.emitState(value),shell.state());
 assert.equal(await page.getByRole('button',{name:'Old folder',exact:true}).count(),1);assert.equal(await page.locator('.a-location-picker').getAttribute('aria-busy'),'true');
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.a-location-picker').evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.screenshot({path:'/tmp/amplifier-region-feedback.png',animations:'disabled'});
 await nextLocations.route.fulfill({status:500,json:{error:'Fixture failed'}});
 await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.a-location-picker'));assert.equal(await page.locator('[role="dialog"]').count(),1);
 await page.mouse.click(2,2);await page.getByRole('button',{name:'Discard changes',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'));
 await page.getByRole('button',{name:'New chat',exact:true}).click();const create=await pending('session.draft');assert.equal(await page.getByRole('button',{name:'New chat',exact:true}).getAttribute('aria-busy'),'true');
 assert.equal(await page.getByRole('button',{name:'New chat',exact:true}).getAttribute('aria-disabled'),'true');
 await page.getByRole('button',{name:'New chat',exact:true}).evaluate(el=>el.click());
 await page.getByRole('button',{name:'New chat',exact:true}).focus();await page.keyboard.press('Enter');
 assert.equal(calls.filter(row=>row.action==='session.draft').length,1);
 await create.route.fulfill({status:500,json:{error:'Fixture rejected creation'}});await page.waitForFunction(()=>!document.querySelector('[data-action="session.draft"][data-action-pending]'));
 // Maintenance actions keep lifecycle feedback after their HTTP receipt.
 state.notificationSettings={server:'https://old.example',desktop:true};state.revision++;
 await page.evaluate(value=>window.emitState(value),shell.state());
 await openSettingsPage(page,'notifications');
 await page.getByText('Set up phone notifications',{exact:true}).click();
 await page.getByLabel(/^Topic/).fill('synthetic-private-topic');
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();const notification=await pending('notifications.save');
 state.actionStatus['notifications.save']={phase:'working',commandId:notification.body.id};state.revision++;
 await notification.route.fulfill({json:{accepted:true,operationId:notification.body.id,state:shell.state()}});
 await page.waitForFunction(()=>!document.querySelector('button[data-action="notifications.save"][data-action-pending]'));
 assert.equal(await page.getByRole('button',{name:'Saving…',exact:true}).isDisabled(),true);
 // This form intentionally allows a newer edit while the old snapshot saves.
 await page.getByLabel(/^Topic/).fill('newer-synthetic-topic');
 state.actionStatus['notifications.save'].phase='ready';state.revision++;
 await page.evaluate(value=>window.emitState(value),shell.state());
 await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 assert.equal(await page.getByLabel(/^Topic/).inputValue(),'newer-synthetic-topic');
 await openSettingsPage(page,'repair');await page.getByRole('button',{name:'Create full backup',exact:true}).click();const backup=await pending('maintenance.backup');
 state.actionStatus['maintenance.backup']={phase:'working',commandId:backup.body.id};state.management={phase:'working',operation:'maintenance.backup'};state.revision++;
 await backup.route.fulfill({json:{accepted:true,state:shell.state()}});
 await page.waitForFunction(()=>!document.querySelector('[data-action="maintenance.backup"][data-action-pending]'));
 assert.equal(await page.getByRole('button',{name:'Create full backup',exact:true}).getAttribute('aria-busy'),'true');
 state.actionStatus['maintenance.backup'].phase='ready';state.management.phase='ready';state.maintenance={backup:'/fixture/private-backup.zip'};state.revision++;
 await page.evaluate(value=>window.emitState(value),shell.state());await page.getByText('Saved backup: /fixture/private-backup.zip',{exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Create full backup',exact:true}).getAttribute('aria-busy'),null);
 // The reported Updates surface retains plain readable status, without a
 // region wash or top-edge bar, while the operation remains in progress.
 state.updates={phase:'checking',detail:'Checking included components…',application:{status:'current',version:'0.20.87'}};state.revision++;
 await page.evaluate(value=>window.emitState(value),shell.state());await openSettingsPage(page,'updates');
 await page.waitForFunction(()=>document.querySelector('[data-activity-region="updates"]')?.getAttribute('aria-busy')==='true');
 const checkButton=page.locator('[data-action="updates.check"]');
 assert.equal(await checkButton.locator('svg').evaluate(el=>getComputedStyle(el).color),await checkButton.evaluate(el=>getComputedStyle(el).color),'checking icon uses the button foreground, not its accent background');
 assert.notEqual(await checkButton.locator('svg').evaluate(el=>getComputedStyle(el).color),await checkButton.evaluate(el=>getComputedStyle(el).backgroundColor));
 for(const region of await page.locator('[data-region-pending]').all()){
  assert.equal(await region.evaluate(el=>getComputedStyle(el).animationName),'none');
  assert.equal(await region.evaluate(el=>getComputedStyle(el).backgroundImage),'none');
 }
 await page.locator('.a-settings-page-content:not([hidden])').screenshot({path:'/tmp/amplifier-updates-calm-feedback.png',animations:'disabled'});
 assert.deepEqual(errors,[]);console.log('Activity feedback browser passed: immediate button state, lifecycle refresh, retained options and drafts, outside dismissal, nested Escape, reduced motion, failure cleanup, duplicate suppression.');
}catch(error){console.error({browserErrors:errors});if(page){await page.screenshot({path:'/tmp/amplifier-feedback-failure.png'}).catch(()=>{});console.error((await page.locator('body').innerText()).slice(0,5000))}throw error}finally{await browser?.close();await vite?.close()}
