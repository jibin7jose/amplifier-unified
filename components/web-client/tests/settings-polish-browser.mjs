import {fixturePython} from './fixture-python.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {settingsSections} from '../src/settings-navigation.js';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const out=process.env.SETTINGS_SCREENSHOTS||'/tmp/settings-polish-audit';mkdirSync(out,{recursive:true});
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/settings_polish_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);let files=[];
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),30000);fixture.stdout.on('data',data=>{const m=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}})});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:940},colorScheme:'dark',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const snap=async name=>{await page.getByText('Loading appearances…',{exact:true}).waitFor({state:'hidden'});await page.screenshot({path:out+'/'+name+'.png',animations:'disabled'});files.push(name)};
 await page.goto(base);await openSettingsPage(page,'ai-connections');
 await page.getByRole('button',{name:/ChatGPT Subscription.*Account connected/}).click();
 const gap=await page.locator('.a-connection-secondary-actions').evaluate(el=>{const [a,b]=el.children;return b.getBoundingClientRect().left-a.getBoundingClientRect().right});assert.ok(gap>=12);await snap('provider-spacing');
 await openSettingsPage(page,'smart-tools');
 await expect(page.getByText('No tools installed yet',{exact:true})).toBeVisible();
 assert.equal(await page.locator('.a-tool-browse-tabs button').first().innerText(),'Installed');await snap('tools-empty');
 await page.getByRole('button',{name:'Add from GitHub URL',exact:true}).click();await page.getByLabel('Repository URL').fill('https://github.com/example/tool');await page.getByRole('button',{name:'Inspect source',exact:true}).click();await page.getByText('Requirements from the tool author',{exact:true}).waitFor();await snap('tools-url');
 await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
 for(const n of ['00','02','03'])await page.getByRole('checkbox',{name:'Select Tool '+n,exact:true}).check();
 await page.getByRole('button',{name:'Review & install',exact:true}).click();
 await expect(page.getByRole('region',{name:'Review installation'})).toBeVisible();await expect(page.getByRole('checkbox',{name:'Select Tool 00'})).toHaveCount(0);
 assert.ok(await page.getByRole('heading',{name:'Install 3 tools?'}).evaluate(el=>document.activeElement===el));await snap('tools-review');
 await page.getByRole('button',{name:'Install selected (3)',exact:true}).click();
 await expect(page.getByRole('checkbox',{name:'Select Tool 00'})).toHaveCount(0);
 await page.getByRole('button',{name:'Retry unfinished installations',exact:true}).waitFor();await snap('tools-results');
 await page.getByRole('button',{name:'Retry unfinished installations',exact:true}).click();await page.waitForFunction(()=>window.amplifier.getState().smartTools.operations.filter(op=>op.action==='smartTools.installBatch').at(-1)?.status==='completed');
 await page.getByRole('button',{name:'View installed tools',exact:true}).click();await snap('tools-installed');
 await page.getByRole('button',{name:/tool-0 Installed/}).click();await page.getByRole('button',{name:'Open setup instructions',exact:true}).click();
 await expect(page.locator('[data-settings-page=smart-tools]')).toBeVisible();await expect(page.getByRole('region',{name:'Setup instructions'})).toContainText('Set up tool-0');await snap('tool-setup-instructions');
 await page.getByRole('button',{name:'Configure MCP connection',exact:true}).click();await expect(page.getByLabel('Display name',{exact:true})).toHaveValue('tool-0');await snap('tool-setup-connection');
 await openSettingsPage(page,'tool-connections');await expect(page.getByText('0 ready · 0 configured',{exact:true})).toHaveCount(0);await snap('advanced-tool-list');
 await page.getByRole('button',{name:/tool-0 Installed/}).click();await page.getByRole('button',{name:'Open setup instructions',exact:true}).click();await snap('advanced-tool-setup');
 await page.getByRole('button',{name:'Configure MCP connection',exact:true}).click();await page.getByLabel('Connection type',{exact:true}).selectOption('streamable-http');await snap('advanced-tool-remote');
 for(const width of [390,1280]){await page.setViewportSize({width,height:940});for(const type of ['streamable-http','stdio']){await page.getByLabel('Connection type',{exact:true}).selectOption(type);assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await snap(`advanced-tool-${type}-${width}`);await page.locator('.a-settings-content').evaluate(el=>el.scrollTop=el.scrollHeight);await snap(`advanced-tool-${type}-${width}-lower`)}}
 await openSettingsPage(page,'voice');await page.getByRole('button',{name:'Choose a voice',exact:true}).click();await snap('voice-picker');
 await page.getByRole('button',{name:'Preview Marin',exact:true}).click();await page.locator('audio').waitFor();await page.waitForFunction(()=>{const a=document.querySelector('audio');return a&&a.readyState>=2&&a.currentTime>0&&!a.error});assert.ok((await page.locator('audio').getAttribute('src')).startsWith('blob:'));await snap('voice-sample');
 await page.getByRole('radio',{name:/Willow English/}).click();await expect(page.locator('.a-voice-current')).toContainText('Willow');
 // Closed release disclosures do not acknowledge a notice; actual visible content does.
 await openSettingsPage(page,'updates');
 const notice=()=>page.evaluate(()=>window.amplifier.getState().attention.items.find(i=>i.id.startsWith('release-notice:')));
 await expect.poll(async()=>(await notice())?.read).toBe(false);
 assert.equal((await notice()).read,false);await page.waitForTimeout(1000);assert.equal((await notice()).read,false);
 await page.locator('summary').filter({hasText:/^Important changes/}).click();await page.getByRole('region',{name:'High-impact changes'}).getByText('New Settings layout',{exact:true}).scrollIntoViewIfNeeded();
 await page.waitForFunction(()=>window.amplifier.getState().attention.items.find(i=>i.id.startsWith('release-notice:'))?.read===true);
 await expect(page.getByRole('region',{name:'High-impact changes'}).getByText('New Settings layout',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:/Mark.*(read|reviewed)/})).toHaveCount(0);
 for(const scenario of ['checking','waiting','error']){
  await context.request.post(base+'/fixture/scenario',{data:{name:scenario}});await openSettingsPage(page,'updates');
  await expect(page.locator('[aria-label="Overall update status"]')).toBeVisible();
  if(scenario==='checking'){await expect(page.locator('#update-action-status')).toContainText('Checking included components');await expect(page.locator('[data-action="updates.check"]')).toBeDisabled()}
  if(scenario==='error')await expect(page.getByText('Last update did not finish:',{exact:false})).toBeVisible();
  await snap('updates-'+scenario);
 }
 await context.request.post(base+'/fixture/scenario',{data:{name:'current'}});
 const basic=['ai-connections','smart-tools','appearance','voice','notifications','privacy','updates','advanced'];
 for(const appearance of ['Amplifier','Aurora','Atelier','Graphite']){
  await openSettingsPage(page,'appearance');await page.getByRole('button',{name:'Use '+appearance,exact:true}).click();
  await page.waitForFunction(name=>window.amplifier.getState().theme.name===name,appearance);
  await page.reload();await page.locator('.a-settings-experience').waitFor({state:'visible'});await openSettingsPage(page,'appearance');
  assert.equal(await page.evaluate(()=>window.amplifier.getState().theme.name),appearance);
  for(const scheme of ['light','dark'])for(const width of [390,1280]){
   await page.emulateMedia({colorScheme:scheme});await page.setViewportSize({width,height:940});
   const pages=appearance==='Graphite'?settingsSections.flatMap(s=>s.pages.map(([id])=>id)):basic;
   for(const id of pages){
    await openSettingsPage(page,id);assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),id+' overflow');await snap(`${appearance}-${scheme}-${width}-${id}`);
    // Also capture expanded basic disclosures and lower controls, without invoking any setting mutation.
    if(basic.includes(id)){
     const details=page.locator('.a-settings-page-content:not([hidden]) details:not([open])');
     const count=await details.count();
     for(let i=0;i<Math.min(count,6);i++){const next=page.locator('.a-settings-page-content:not([hidden]) details:not([open])>summary').first();if(await next.isVisible())await next.click();else break}
     if(count){await page.locator('.a-settings-content').evaluate(el=>el.scrollTop=el.scrollHeight);await snap(`${appearance}-${scheme}-${width}-${id}-expanded`)}
    }
   }
  }
 }
 assert.deepEqual(errors,[]);writeFileSync(out+'/results.json',JSON.stringify({screenshots:files.length,files,errors,providerSpacing:true,exclusiveInstallation:true,voicePreview:true,readOnView:true,themeReload:true},null,2));console.log(JSON.stringify({screenshots:files.length,errors}));
}catch(error){if(page)await page.screenshot({path:out+'/failure.png'});console.error(stderr.slice(-2000));throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
