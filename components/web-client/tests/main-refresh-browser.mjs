import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import {settingsButton} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||await mkdtemp(join(tmpdir(),'main-refresh-'));
await mkdir(output,{recursive:true});
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/main_refresh_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const patch=values=>action('view.update',{patch:values});
 const panel=page.locator('#workspace-canvas'),overview=panel.getByRole('tab',{name:'Chat overview',exact:true});
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByText('Lead with what someone can do here.',{exact:true})).toBeVisible();
 await page.mouse.move(700,20);await page.screenshot({path:join(output,'main-refresh-conversation.png')});
 await page.getByRole('button',{name:'Open Website refresh brief in canvas',exact:true}).click();await expect(panel).toHaveAttribute('data-mode','artifact');
 await overview.click();await expect(panel).toHaveAttribute('data-mode','overview');
 await expect.poll(async()=>Math.round((await panel.boundingBox()).width)).toBe(320);
 await expect(page.getByRole('heading',{name:'Outputs',exact:true})).toBeVisible();
 await page.mouse.move(700,20);await page.screenshot({path:join(output,'main-refresh-overview.png')});
 const separator=page.getByRole('separator',{name:'Resize canvas'});
 await separator.focus();await page.keyboard.press('ArrowLeft');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.canvasOverviewWidth)).toBe(340);
 await page.locator('.a-saved-artifact').filter({hasText:'Website refresh brief'}).click();
 await expect(panel).toHaveAttribute('data-mode','artifact');
 assert.ok((await panel.boundingBox()).width>500);
 await separator.focus();await page.keyboard.press('ArrowRight');
 const artifactWidth=await page.evaluate(()=>window.amplifier.getState().view.canvasWidth);
 await overview.click();await expect(panel).toHaveAttribute('data-mode','overview');
 await expect.poll(async()=>Math.round((await panel.boundingBox()).width)).toBe(340);
 await page.locator('.a-saved-artifact').filter({hasText:'Website refresh brief'}).click();
 await expect.poll(async()=>Math.round((await panel.boundingBox()).width)).toBe(artifactWidth);
 await page.reload();await expect(panel).toHaveAttribute('data-mode','artifact');
 await expect.poll(async()=>Math.round((await panel.boundingBox()).width)).toBe(artifactWidth);
 await page.mouse.move(700,20);await page.screenshot({path:join(output,'main-refresh-canvas.png')});
 await page.getByRole('button',{name:'Focus canvas',exact:true}).click();await expect(panel).toHaveAttribute('data-focused','true');
 await page.keyboard.press('Escape');await expect(panel).toHaveAttribute('data-focused','false');
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
 await settingsButton(page).click();
 await expect(page.getByRole('button',{name:'Help me with settings',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.getByRole('button',{name:/^Activity(?: \d+ unread items)?$/}).click();
 await expect(page.getByRole('region',{name:'Activity to review',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 for(const scheme of ['light','dark']){
  await patch({scheme});
  for(const width of [1440,1024,768,390,320]){
   await page.setViewportSize({width,height:900});
   await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${width}: no page overflow`);
   const chat=await page.locator('.a-conversation').boundingBox(),composer=await page.locator('.a-composer').boundingBox();
   assert.ok(chat.x+chat.width<=width+1&&composer.x+composer.width<=width+1,`${width}: chat and composer fully visible`);
   if(width<520){await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();await expect(page.getByRole('dialog',{name:'Chat options',exact:true})).toBeVisible();await page.getByRole('button',{name:'Close chat options',exact:true}).click();}
   await page.getByRole('button',{name:'Open canvas',exact:true}).click();await expect(panel).toBeVisible();
   if(width<660)assert.ok((await panel.boundingBox()).width<=width);
   await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
   await page.mouse.move(1,1);await page.screenshot({path:join(output,`main-refresh-${scheme}-${width}.png`)});
  }
 }
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await expect(page.locator('.a-inline-artifact').getByRole('heading',{name:'A clearer first impression',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Hide preview',exact:true}).click();
 await expect(page.getByRole('button',{name:'Preview in chat',exact:true})).toBeVisible();
 assert.deepEqual(errors,[]);console.log('PASS: conversation, compact overview, artifact selection, separate durable widths, focus, Activity, Settings, Options, and five light/dark viewport sizes.');
}finally{await browser?.close();fixture.kill()}
