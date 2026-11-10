// Production frontend/host, synthetic credentials and files; no provider calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py','--voice-settings'],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 let action='voice.configure',held=null,count=0,rejectSave=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action!==action)return route.continue();
  count++;held=body;const rejected=rejectSave;
  await new Promise(resolve=>release=resolve);
  if(rejected)return route.fulfill({status:409,json:{accepted:false,error:'Fixture save rejected. Draft retained.'}});
  return route.continue();
 });
 await page.goto(url);await openSettingsPage(page,'voice');
 const key=page.locator('#voice-api-key');
 await key.fill('synthetic-voice-key');
 const save=page.getByRole('button',{name:'Save voice connection',exact:true});
 const width=(await save.boundingBox()).width;
 await save.focus();await page.keyboard.press('Enter');
 await expect.poll(()=>held?.action).toBe(action);
 await expect(key).toBeDisabled();
 await expect(page.locator('input[name="voice-key-source"]').first()).toBeDisabled();
 const saving=page.getByRole('button',{name:'Saving…',exact:true});
 await expect(saving).toBeDisabled();
 assert.equal((await saving.boundingBox()).width,width);
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 // Lock only this write scope: choosing a model is still available.
 await expect(page.locator('#preferred-voice')).toBeEnabled();
 await page.keyboard.press('Enter');assert.equal(count,1);
 for(const scheme of ['light','dark'])for(const motion of ['reduce','no-preference'])for(const detail of ['minimal','standard','detailed']){
  await page.locator('#amp-one').evaluate((el,value)=>el.dataset.interfaceDetail=value,detail);
  await page.emulateMedia({colorScheme:scheme,reducedMotion:motion});
  await expect(saving).toBeVisible();await expect(key).toBeDisabled();
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
 await expect(saving).toBeVisible();
 release();release=null;
 await expect(page.getByRole('dialog',{name:'Settings',exact:true}).getByRole('alert').filter({hasText:'Fixture save rejected'})).toBeVisible();
 await expect(key).toBeEnabled();await expect(key).toHaveValue('synthetic-voice-key');
 await page.emulateMedia({forcedColors:'none'});
 rejectSave=false;held=null;await save.click();await expect.poll(()=>held?.action).toBe(action);
 release();release=null;
 await expect(page.getByText('Voice key configured',{exact:true})).toBeVisible();
 await page.getByText('Change voice connection',{exact:true}).click();
 await expect(key).toHaveValue('');
 // Shared file picker keeps an explicit working label for async import.
 await openSettingsPage(page,'custom-appearance');
 action='theme.import';held=null;count=0;
 const upload=page.getByRole('button',{name:'Upload appearance',exact:true});
 const uploadWidth=(await upload.boundingBox()).width;
 await page.locator('#theme-import').setInputFiles({name:'fixture.css',mimeType:'text/css',buffer:Buffer.from('/* synthetic appearance */\n#amp-one { --accent: #336699; }')});
 await expect.poll(()=>held?.action).toBe(action);
 const uploading=page.getByRole('button',{name:'Uploading appearance…',exact:true});
 await expect(uploading).toBeDisabled();await expect(page.locator('#theme-import')).toBeDisabled();
 assert.equal((await uploading.boundingBox()).width,uploadWidth);
 await page.locator('.a-appearance-import .a-file-drop').evaluate(el=>{const data=new DataTransfer();data.items.add(new File(['/* second selection */'],'second.css',{type:'text/css'}));el.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));});
 await page.setViewportSize({width:390,height:844});
 await expect(uploading).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 release();release=null;await expect(upload).toBeEnabled();
 await expect(page.getByText('fixture added to your library. Select it to preview.',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Preview fixture',exact:true})).toBeVisible();
 assert.equal(count,1);assert.deepEqual(errors,[]);
 assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Settings save feedback passed: scoped credential locking, duplicate suppression, failure/draft/retry, stable labels, shared file progress, motion/contrast and mobile.');
}finally{release?.();await browser?.close();fixture.kill('SIGTERM')}
