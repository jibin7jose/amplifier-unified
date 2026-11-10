import {fixturePython} from './fixture-python.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const out=process.env.SETTINGS_SCREENSHOTS||'/tmp/settings-fidelity';mkdirSync(out,{recursive:true});
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/settings_fidelity_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),30000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:940},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},colorScheme:'dark'});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await openSettingsPage(page,'ai-connections');
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();
 const names=await page.locator('[data-part=ai-connections] .a-everyday-row strong').allTextContents();
 assert.deepEqual(names,['GitHub Copilot Subscription','ChatGPT Subscription','OpenAI API','Anthropic API','Google Gemini API','OpenAI Compatible API']);
 const contrast=async locator=>{
  const colors=await locator.evaluate(el=>{const s=getComputedStyle(el);return [s.color,s.backgroundColor]});
  const luminance=c=>c.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
  const l=colors.map(luminance);return (Math.max(...l)+.05)/(Math.min(...l)+.05);
 };
 assert.ok(await contrast(page.locator('.a-settings-sidebar button[aria-current=page]'))>=4.5);
 await page.screenshot({path:out+'/providers-dark.png'});
 await page.getByRole('button',{name:/ChatGPT Subscription Sign in/}).click();
 await page.getByLabel('ChatGPT connection',{exact:true}).selectOption('chatgpt_codex');
 await page.getByRole('button',{name:'Get sign-in code',exact:true}).click();
 await expect(page.getByLabel('1. Copy your sign-in code')).toHaveValue('TEST-1234');
 await expect(page.getByRole('button',{name:'Copy sign-in code'})).toHaveText('Copied');
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'TEST-1234');
 await page.getByLabel('1. Copy your sign-in code').focus();
 assert.deepEqual(await page.getByLabel('1. Copy your sign-in code').evaluate(el=>[el.selectionStart,el.selectionEnd]),[0,9]);
 await expect(page.getByRole('link',{name:'Open ChatGPT'})).toHaveAttribute('href','https://auth.openai.com/codex/device');
 assert.equal(await page.getByText('Check sign-in',{exact:true}).count(),0);
 await page.screenshot({path:out+'/device-signin-dark.png'});
 await expect(page.getByText('Signed in. Choose your model below.')).toBeVisible({timeout:15000});
 await expect(page.getByRole('button',{name:'Finish setup',exact:true})).toBeVisible();
 assert.equal(await page.getByLabel('1. Copy your sign-in code').count(),0);
 await page.getByLabel('Model',{exact:true}).selectOption('fixture-alternative');
 await page.getByRole('button',{name:'Finish setup',exact:true}).click();
 await expect(page.getByRole('button',{name:'Connect another service',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('Clipboard blocked'))}}));
 await page.getByRole('button',{name:/ChatGPT Subscription Sign in/}).click();
 await page.getByLabel('ChatGPT connection',{exact:true}).selectOption('chatgpt_codex');
 await page.getByRole('button',{name:'Get sign-in code',exact:true}).click();
 await expect(page.getByText('Select the code to copy it, or use the Copy button.',{exact:true})).toBeVisible();
 await page.getByLabel('1. Copy your sign-in code').focus();
 assert.deepEqual(await page.getByLabel('1. Copy your sign-in code').evaluate(el=>[el.selectionStart,el.selectionEnd]),[0,9]);
 await page.getByRole('button',{name:'Cancel sign-in',exact:true}).click();
 await expect(page.getByText('Sign-in was cancelled. You can try again.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Back',exact:true}).click();
 await page.getByRole('button',{name:/OpenAI Compatible API Connect a local/}).click();
 await page.getByLabel('API base URL').fill('http://localhost:1234/v1');
 await page.getByRole('button',{name:'Save and check connection',exact:true}).click();
 await expect(page.getByRole('button',{name:'Finish setup',exact:true})).toBeVisible();
 assert.ok(await page.evaluate(()=>window.amplifier.getState().setup.providers.some(p=>p.module==='provider-chat-completions'&&p.config.base_url==='http://localhost:1234/v1')));
 await openSettingsPage(page,'routing');
 await expect(page.locator('#routing-profile')).toHaveValue('balanced');
 await expect(page.getByText('Opening balanced…')).toHaveCount(0);
 await page.getByRole('button',{name:'Refresh routing profiles'}).click();
 await openSettingsPage(page,'ai-connections');await openSettingsPage(page,'routing');
 await expect(page.getByText('Opening balanced…')).toHaveCount(0);
 await openSettingsPage(page,'providers');
 if(!await page.locator('#provider-preset').isVisible())await page.locator('.a-provider-access summary').click();
 const choices=await page.locator('#provider-preset option').allTextContents();
 assert.equal(choices.length,10); // nine known modules plus custom
 await openSettingsPage(page,'smart-tools');await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
 await page.getByRole('checkbox',{name:'Select Tool 00',exact:true}).check();
 assert.equal(await page.locator('.a-tool-catalog-row').count(),40);
 const bounds=await page.locator('.a-tool-catalog-row').first().boundingBox();assert.ok(bounds.width>700&&bounds.height<120,JSON.stringify(bounds));
 await page.getByRole('button',{name:'Details for Tool 00',exact:true}).click();
 const detail=await page.locator('.a-tool-catalog-simple .a-collection-detail').boundingBox();assert.ok(detail.width>700);
 await expect(page.getByRole('button',{name:'Back to catalog',exact:true})).toBeVisible();
 await page.screenshot({path:out+'/tool-detail-dark.png'});
 await page.getByRole('button',{name:'Back to catalog',exact:true}).click();
 await openSettingsPage(page,'ai-connections');
 await expect(page.getByRole('button',{name:'Connect another service',exact:true})).toBeVisible();
 const screens=['ai-connections','smart-tools','appearance','voice','notifications','privacy','updates','advanced'];
 for(const mode of ['dark','light']){
  await page.emulateMedia({colorScheme:mode});
  for(const width of [1280,390]){
   await page.setViewportSize({width,height:940});
   for(const id of screens){
    await openSettingsPage(page,id);
    if(id==='smart-tools')await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
    if(id==='appearance')await expect(page.getByRole('button',{name:'Use Amplifier',exact:true})).toBeVisible();
    if(id==='notifications')await page.waitForFunction(()=>window.amplifier.getState().actionStatus?.['notifications.get']?.phase==='ready');
    await expect(page.locator('.a-settings-page-content:not([hidden]) [data-region-pending]')).toHaveCount(0);
    assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),id+' overflow');
    await page.screenshot({path:out+'/'+id+'-'+mode+'-'+width+'.png',animations:'disabled'});
   }
  }
 }
 assert.deepEqual(errors,[]);writeFileSync(out+'/result.json',JSON.stringify({providerOrder:true,deviceLoginAutoCopy:true,deviceLoginAutoAdvance:true,clipboardDeniedFallback:true,deviceCancel:true,compatibleWithoutKey:true,knownProviderCount:9,cachedRoutingOpens:true,longCatalogRows:true,fullWidthDetails:true,screens:32,browserErrors:0},null,2));
 console.log(readFileSync(out+'/result.json','utf8'));
}catch(error){if(page)await page.screenshot({path:out+'/failure.png'});console.error(stderr.slice(-1500));throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
