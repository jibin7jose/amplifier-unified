import {fixturePython} from './fixture-python.mjs';
// Synthetic OAuth only: exercises the real app actions with no provider traffic.
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/settings_fidelity_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
const signInContrast=[];
async function checkSignInLabel(name){
 const link=page.getByRole('link',{name,exact:true});
 for(const scheme of ['light','dark'])for(const width of [1280,390]){
  await page.emulateMedia({colorScheme:scheme});await page.setViewportSize({width,height:940});
  await link.scrollIntoViewIfNeeded();await expect(link).toHaveText(name);
  await link.focus();await expect(link).toBeFocused();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const measured=await link.evaluate(el=>{
   const style=getComputedStyle(el),box=el.getBoundingClientRect();
   const luminance=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{
    v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;
   }).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   const values=[luminance(style.color),luminance(style.backgroundColor)];
   return {color:style.color,background:style.backgroundColor,contrast:(Math.max(...values)+.05)/(Math.min(...values)+.05),width:box.width,height:box.height};
  });
  signInContrast.push({name,scheme,viewport:width,...measured});
  assert.ok(measured.contrast>=4.5,JSON.stringify(signInContrast.at(-1)));
  assert.ok(measured.width>80&&measured.height>=36,JSON.stringify(measured));
 }
 await page.setViewportSize({width:1280,height:940});
}
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),30000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});fixture.on('exit',code=>reject(Error('Fixture exited '+code+stderr)))});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:940},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await openSettingsPage(page,'ai-connections');
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();
 await page.getByRole('button',{name:/ChatGPT Subscription Sign in/}).click();
 await expect(page.getByLabel('ChatGPT connection',{exact:true})).toHaveValue('chatgpt_codex');
 await page.getByLabel('ChatGPT connection',{exact:true}).selectOption('chatgpt_plan');
 await page.getByText('Using a remote host, such as Spark?',{exact:true}).click();
 await expect(page.getByRole('link',{name:'Amplifier setup commands',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Continue with ChatGPT',exact:true}).click();
 await expect(page.getByRole('link',{name:'Continue with ChatGPT',exact:true})).toHaveAttribute('href',/auth.openai.com\/api\/accounts\/authorize/);
 await checkSignInLabel('Continue with ChatGPT');
 assert.equal(await page.getByLabel('1. Copy your sign-in code').count(),0);
 await expect(page.getByText('You’re using your ChatGPT plan',{exact:true})).toBeVisible({timeout:15000});
 await page.getByRole('button',{name:'Got it',exact:true}).click();
 await expect(page.getByText('You’re using your ChatGPT plan',{exact:true})).toHaveCount(0);
 await page.getByLabel('Model',{exact:true}).selectOption('fixture-alternative');
 await page.getByRole('button',{name:'Finish setup',exact:true}).click();
 await expect(page.getByRole('button',{name:'Connect another service',exact:true})).toBeVisible();
 const state=await page.evaluate(()=>window.amplifier.getState());
 const provider=state.setup.providers.find(row=>row.config.auth_mode==='chatgpt_plan');
 assert.ok(provider?.account.planEnabled);assert.ok(provider.accountConnected);assert.equal(provider.config.default_model,'fixture-alternative');
 assert.ok(!JSON.stringify(state).includes('fixture-private'));
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();
 await page.getByRole('button',{name:/ChatGPT Subscription Sign in/}).click();
 await page.getByLabel('ChatGPT connection',{exact:true}).selectOption('chatgpt_codex');
 await page.getByRole('button',{name:'Get sign-in code',exact:true}).click();
 await expect(page.getByLabel('1. Copy your sign-in code')).toHaveValue('TEST-1234');
 await expect(page.getByRole('link',{name:'Open ChatGPT',exact:true})).toHaveAttribute('href','https://auth.openai.com/codex/device');
 await checkSignInLabel('Open ChatGPT');
 await page.getByRole('button',{name:'Cancel sign-in',exact:true}).click();
 await expect(page.getByText('Sign-in was cancelled. You can try again.',{exact:true})).toBeVisible();
 await openSettingsPage(page,'providers');
 await page.locator(`[data-collection-id="${provider.id}"] button`).click();
 await expect(page.getByLabel('ChatGPT connection',{exact:true})).toHaveValue('chatgpt_plan');
 await expect(page.getByRole('link',{name:'Manage usage',exact:true}).first()).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 await page.screenshot({path:'/tmp/chatgpt-sign-in-mobile.png'});
 await page.setViewportSize({width:1280,height:940});await page.screenshot({path:'/tmp/chatgpt-sign-in-desktop.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({explicitPlanFlow:true,noDeviceCodeRequired:true,remoteHelp:true,firstGrantWelcome:true,savedProfile:true,advancedParity:true,mobile:true,noTokensInPublicState:true,signInContrast,browserErrors:0}));
}catch(error){if(page)await page.screenshot({path:'/tmp/chatgpt-sign-in-failure.png'});throw error}finally{await browser?.close();fixture.kill('SIGTERM')}
