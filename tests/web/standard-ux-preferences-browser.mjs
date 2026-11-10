import {openSettingsSection} from '../../.build/web-client/tests/browser-settings.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('An owned AMPLIFIER_TEST_ARTIFACTS directory is required');
const evidence=new StandardUxEvidence(output,{baseline:'1d993e0bb32bc90b75fc66f56f1b9014c2d63f87',sourceReceipt:'execution-source.json',data:'synthetic-standard',runtime:'Settings fixture, no real provider',permission:'Synthetic denied browser API; actual public request control',timings:'Shared DTU functional observations, not controlled performance'});
const variants=[{id:'desktop-pointer',width:1366,height:900,mode:'pointer'},{id:'desktop-keyboard',width:1366,height:900,mode:'keyboard'},{id:'narrow-touch',width:390,height:844,mode:'touch'},{id:'narrow-keyboard',width:390,height:844,mode:'keyboard'}].filter(row=>!process.env.STANDARD_UX_VARIANTS||process.env.STANDARD_UX_VARIANTS.split(',').includes(row.id));
await mkdir(output,{recursive:true});let failed=false;
for(const variant of variants){
 let fixture,browser,page,logs='',releaseHeld;
 const errors=[],calls=[],external=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/standard_ux_server.py'],{stdio:['ignore','pipe','pipe']});fixture.stderr.on('data',chunk=>logs+=chunk);
  const port=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Preferences fixture startup timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Preferences fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
  const url='http://127.0.0.1:'+port;
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width:variant.width,height:variant.height},hasTouch:variant.mode==='touch',isMobile:variant.mode==='touch',colorScheme:'light',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
  await page.route('**/*',route=>{const parsed=new URL(route.request().url());if(['http:','https:'].includes(parsed.protocol)&&parsed.hostname!=='127.0.0.1'){external.push(parsed.origin);return route.abort()}return route.continue()});
  await page.addInitScript(()=>{window.__notificationRequests=[];class DeniedNotification{static permission='default';static async requestPermission(){window.__notificationRequests.push({active:!!navigator.userActivation?.isActive});this.permission='denied';return 'denied'}}Object.defineProperty(window,'Notification',{value:DeniedNotification,configurable:true})});
  evidence.page=page;
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  const presentation=()=>page.evaluate(()=>window.amplifier.getShellState()?.effectiveComposition.presentation);
  const act=async(label,control)=>evidence.action(label,async()=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(variant.mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press(await control.evaluate(el=>el.matches('input[type=checkbox],input[type=radio]'))?'Space':'Enter')}else if(variant.mode==='touch')await control.tap();else await control.click()});
  const section=async id=>{
   await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
   await expect(page.locator('#amp-one')).toBeVisible();
   await openSettingsSection(page,id,{activate:act});
  };
  const probe=async(id,workflows,run)=>{const record=await evidence.probe(variant.id+'.'+id,workflows,async()=>{await run();return {variant,...await evidence.snapshot(page,variant.id+'.'+id,'#amp-one')}});if(record.status!=='passed')throw Error('Dependent preference checks stopped after '+id)};
  const setPresentation=async(key,value,control,kind='select')=>{
   await expect(control).toBeEnabled();
   const prepared=page.waitForResponse(response=>{const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;return new URL(response.url()).pathname==='/api/actions'&&body?.action==='shell.changes.prepare'&&body.args?.composition?.presentation?.[key]===value});
   const applied=page.waitForResponse(async response=>{const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;if(new URL(response.url()).pathname!=='/api/actions'||body?.action!=='shell.changes.apply')return false;return body.args.changeId===(await(await prepared).json()).result.id});
   if(kind==='select')await evidence.action('Choose '+key+'='+value+' through native select (picker gesture not qualified)',()=>control.selectOption(value));else await act('Set '+key+'='+value,control);
   evidence.assertion('Prepare '+key+' accepted',(await prepared).ok());const result=await applied;evidence.assertion('Apply '+key+' HTTP succeeds',result.ok());evidence.assertion('Apply '+key+' accepted',(await result.json()).accepted,true);
   await expect.poll(async()=>(await presentation())[key]).toBe(value);await expect(control).toBeEnabled();evidence.assertion(key+' exact canonical preference',(await presentation())[key],value);
  };
  let selected;
  await probe('entry',['entry.boot','settings.destinations'],async()=>{await evidence.action('Open clean synthetic Settings app',()=>page.goto(url));await showFixtureConversation(page,{navPinned:true});await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);selected=(await state()).selectedSessionId;await expect(page.getByRole('textbox',{name:'Message Amplifier',exact:true})).toBeEditable();await evidence.action('Type retained unsent preference draft',()=>page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Keep this unsent preference draft'));await section('appearance');evidence.assertion('Settings retains selected chat',(await state()).selectedSessionId,selected)});
  await probe('color-detail',['appearance.mode'],async()=>{
   for(const value of ['dark','light','system']){await setPresentation('scheme',value,page.getByRole('combobox',{name:'Color mode',exact:true}));const painted=value==='system'?'light':value;await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',painted)}
   for(const [value,label] of [['minimal','Focused'],['detailed','Everything'],['standard','Balanced']]){await setPresentation('interfaceDetail',value,page.getByRole('radio',{name:new RegExp('^'+label)}),'radio');await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail',value)}
  });
  await probe('layout',['appearance.layout','appearance.interim'],async()=>{
   for(const value of ['inline','active','overview','composer'])await setPresentation('planPlacement',value,page.getByLabel('Plan location',{exact:true}));
   const collapse=page.getByRole('checkbox',{name:/^Collapse interim replies/});for(const value of [true,false])await setPresentation('collapseInterimReplies',value,collapse,'checkbox');
   await expect(page.locator('#collapse-interim-help')).toContainText('Applies to this browser.');evidence.assertion('Interim preference scope is explicit',(await page.locator('#collapse-interim-help').innerText()).includes('Applies to this browser.'));
   await act('Open Layout & background',page.getByText('Layout & background',{exact:true}));
   for(const value of ['conversation','work','balanced']){await setPresentation('layout',value,page.getByLabel('Layout',{exact:true}));await expect(page.locator('#amp-one')).toHaveAttribute('data-layout',value)}
   const decorations=page.getByRole('checkbox',{name:'Show decorative theme background',exact:true});for(const value of [false,true]){await setPresentation('decorations',value,decorations,'checkbox');await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations',value?'on':'off')}
   await evidence.action('Reload after accepted preferences',()=>page.reload());await section('appearance');const current=await presentation();for(const [key,value] of Object.entries({scheme:'system',interfaceDetail:'standard',planPlacement:'composer',collapseInterimReplies:false,layout:'balanced',decorations:true}))evidence.assertion('Reload preserves '+key,current[key],value);
  });
  await probe('notification-defaults',['notifications.phone','notifications.browser'],async()=>{
   await section('notifications');const badge=page.getByRole('checkbox',{name:/^Finished responses in the app badge/});await expect(badge).not.toBeChecked();evidence.assertion('Badge initially off',(await state()).notificationSettings?.completionBadge??false,false);
   await act('Open phone notification setup',page.getByText('Set up phone notifications',{exact:true}));await expect(page.getByRole('checkbox',{name:'Include a response preview',exact:true})).not.toBeChecked();evidence.assertion('Response preview initially off',(await state()).notificationSettings?.preview??false,false);
   evidence.assertion('No permission prompt before explicit request',await page.evaluate(()=>window.__notificationRequests),[]);
  });
  await probe('badge-pending',['notifications.badge','settings.save'],async()=>{
   const badge=page.getByRole('checkbox',{name:/^Finished responses in the app badge/});let held=false;const release=new Promise(resolve=>releaseHeld=resolve);
   const handler=async route=>{const body=route.request().postDataJSON();if(body?.action==='notifications.save'&&body.args?.patch?.completionBadge===true){held=true;await release}await route.continue()};await page.route('**/api/actions',handler);
   await act('Enable finished-response badge',badge);await expect.poll(()=>held).toBe(true);await expect(badge).toBeDisabled();await expect(page.getByRole('checkbox',{name:/^On this device/})).toBeDisabled();
   evidence.assertion('One held preference submission',calls.filter(row=>row.action==='notifications.save'&&row.args?.patch?.completionBadge===true).length,1);
   releaseHeld();releaseHeld=null;await expect.poll(async()=>(await state()).notificationSettings?.completionBadge).toBe(true);await expect(badge).toBeEnabled();await page.unroute('**/api/actions',handler);
   await evidence.action('Reload saved badge preference',()=>page.reload());await section('notifications');await expect(badge).toBeChecked();evidence.assertion('Badge survives reload',(await state()).notificationSettings.completionBadge,true);
  });
  await probe('notification-permission',['notifications.browser'],async()=>{
   const desktop=page.getByRole('checkbox',{name:/^On this device/});await expect(desktop).toBeChecked();await act('Disable device notifications',desktop);await expect.poll(async()=>(await state()).notificationSettings?.desktop).toBe(false);await expect(desktop).toBeEnabled();
   evidence.assertion('Saved preference does not request permission',await page.evaluate(()=>window.__notificationRequests.length),0);
   await act('Explicitly request browser permission',page.getByRole('button',{name:'Allow browser notifications',exact:true}));await expect.poll(()=>page.evaluate(()=>window.__notificationRequests.length)).toBe(1);
   evidence.assertion('Synthetic browser denied request',await page.evaluate(()=>Notification.permission),'denied');evidence.assertion('Denied permission leaves saved preference off',(await state()).notificationSettings.desktop,false);await expect(desktop).not.toBeChecked();
   await expect(page.getByRole('status').filter({hasText:'Browser notifications are blocked.'})).toContainText('browser’s site settings');await expect(page.getByRole('button',{name:'Allow browser notifications',exact:true})).toHaveCount(0);
   evidence.assertion('Denied permission explains recovery',await page.getByRole('status').filter({hasText:'Browser notifications are blocked.'}).innerText(),'Browser notifications are blocked. Allow notifications for this site in your browser’s site settings, then return here. Your saved preference is unchanged.');
   await evidence.action('Simulate permission changed in browser settings',()=>page.evaluate(()=>{Notification.permission='granted';window.dispatchEvent(new Event('focus'))}));await expect(page.getByRole('status').filter({hasText:'This browser has permission'})).toBeVisible();await expect(desktop).not.toBeChecked();
   await evidence.action('Simulate browser without notification capability',()=>page.evaluate(()=>{delete window.Notification;window.dispatchEvent(new Event('amplifier:notification-permission'))}));await expect(page.getByRole('status').filter({hasText:'This browser does not support notifications.'})).toContainText('Ready for you');await expect(page.getByRole('button',{name:'Allow browser notifications',exact:true})).toHaveCount(0);await expect(desktop).not.toBeChecked();
   evidence.assertion('Unsupported capability retains saved preference',(await state()).notificationSettings.desktop,false);
   evidence.assertion('One explicit permission command',calls.filter(row=>row.action==='notification.request').length,1);
  });
  await probe('close-retention',['settings.destinations','settings.narrow','accessibility.privacy'],async()=>{
   evidence.assertion('Dialog has no horizontal overflow',await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   await act('Close Settings',page.locator('button[aria-label="Close settings"]:visible,button[aria-label="Close panel"]:visible').last());await expect(page.locator('.a-settings-experience')).toHaveCount(0);await expect(page.getByRole('textbox',{name:'Message Amplifier',exact:true})).toHaveDraft('Keep this unsent preference draft');
   evidence.assertion('Selected chat unchanged',(await state()).selectedSessionId,selected);evidence.assertion('No hidden model input',calls.filter(row=>['conversation.send','session.send'].includes(row.action)),[]);evidence.assertion('No external browser requests',external,[]);evidence.assertion('No browser errors',errors,[]);
  });
 }catch(error){failed=true;console.error(variant.id,error.message)}finally{
  releaseHeld?.();
  try{await browser?.close()}catch(error){failed=true;logs+='\nBrowser cleanup failure: '+error.message;console.error(variant.id,'browser cleanup',error.message)}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(output+'/'+variant.id+'-fixture.log',logs);await evidence.save()}
 }
}
await evidence.save();if(failed||evidence.probes.some(row=>row.status==='failed'))process.exitCode=1;
