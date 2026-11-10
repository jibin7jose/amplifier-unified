import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-surface-radius';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await page.goto(url);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await action('session.create');await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep my unfinished draft.');
 await openSettingsPage(page,'appearance');
 await mkdir(out,{recursive:true});
 for(const [id,radius] of [['default','24px'],['aurora','22px'],['atelier','9px'],['graphite','5px']]){
  const value=(await action('theme.read',{id:'builtin:'+id})).result;
  await action('theme.preview',{name:value.name,css:value.css});
  for(const scheme of ['Light','Dark']){
   if(await page.locator('#appearance-color-mode').isVisible())await page.locator('#appearance-color-mode').selectOption(scheme.toLowerCase());else await page.getByRole('button',{name:scheme,exact:true}).click();
   await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme.toLowerCase());
   for(const selector of ['.a-conversation','.a-composer','.a-nav-rail','.a-dialog']){
    await expect.poll(()=>page.locator(selector).first().evaluate(el=>getComputedStyle(el).borderTopLeftRadius),{message:id+' '+scheme+' '+selector}).toBe(radius);
   }
   await page.screenshot({path:out+'/'+id+'-'+scheme.toLowerCase()+'.png'});
  }
 }
 for(const width of [760,390,320]){
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await expect.poll(()=>page.locator('.a-nav-rail').evaluate(el=>getComputedStyle(el).borderTopLeftRadius)).toBe(width<=760?'0px':'5px');
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished draft.');
 assert.deepEqual(errors,[]);
 console.log('PASS: all four appearances, both schemes, shared surface radii, narrow layouts, forced colors, reduced motion and draft preservation');
}finally{await browser?.close();fixture.kill();}
