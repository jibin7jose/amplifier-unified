import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {settingsButton} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/theme-surfaces';
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/main_refresh_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 await mkdir(out,{recursive:true});
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:960},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const presentation=async patch=>{await page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id})},patch);
  if(patch.scheme)await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',patch.scheme);
  if(patch.layout)await expect(page.locator('#amp-one')).toHaveAttribute('data-layout',patch.layout);
  if('decorations' in patch)await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations',patch.decorations?'on':'off');
 };
 const style=selector=>page.locator(selector).evaluate(el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return {left:r.left,right:r.right,bottom:r.bottom,width:r.width,gap:s.columnGap,tl:s.borderTopLeftRadius,tr:s.borderTopRightRadius,bl:s.borderBottomLeftRadius,br:s.borderBottomRightRadius,art:s.backgroundImage}});
 const chat='.a-conversation',canvas='#workspace-canvas',nav='.a-nav-rail';
 const assertFits=async()=>assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal page overflow');
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByText('Lead with what someone can do here.',{exact:true})).toBeVisible();
 const draft='Keep this unfinished thought.';await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 await page.getByRole('button',{name:'Open Website refresh brief in canvas',exact:true}).click();
 for(const [id,gap,radius] of [['aurora',10,22],['atelier',7,10],['graphite',4,4],['default',0,0]]){
  const theme=(await action('theme.read',{id:'builtin:'+id})).result;
  await action('theme.preview',{name:theme.name,css:theme.css});
  await expect.poll(async()=>(await style('.a-layout')).gap).toBe(gap+'px');
  await action('theme.apply',{name:theme.name,css:theme.css});
  for(const scheme of ['light','dark']){
   await presentation({scheme,layout:'balanced',decorations:true});
   await expect.poll(async()=>(await style(chat)).tr).toBe(radius+'px');
   const c=await style(chat),v=await style(canvas),n=await style(nav);
   assert.equal(n.tl,'0px');assert.equal(n.tr,radius+'px');assert.equal(c.tl,radius+'px');assert.equal(v.tl,radius+'px');assert.equal(v.tr,'0px');
   assert.equal(c.bl,'0px');assert.equal(c.br,'0px');assert.ok(Math.abs(v.left-c.right-gap)<1);assert.ok(Math.abs(c.left-n.right-gap)<1);assert.ok(c.width>=360);assert.ok(v.right<=1441);assert.ok(Math.abs(c.bottom-960)<1,'conversation remains anchored to bottom');await assertFits();
   await page.mouse.move(600,20);await page.screenshot({path:out+'/'+id+'-'+scheme+'.png'});
  }
 }
 const aurora=(await action('theme.read',{id:'builtin:aurora'})).result;
 await action('theme.apply',{name:aurora.name,css:aurora.css});
 await presentation({scheme:'light',layout:'work'});
 await expect.poll(async()=>(await style(canvas)).tl).toBe('22px');assert.equal((await style(canvas)).tr,'22px');assert.equal((await style(chat)).tr,'0px');
 await page.getByRole('button',{name:'Focus canvas',exact:true}).click();await expect(page.locator(canvas)).toHaveAttribute('data-focused','true');
 assert.equal((await style(canvas)).tl,'0px');assert.equal((await style(canvas)).tr,'0px');
 await page.keyboard.press('Escape');
 await presentation({decorations:false});
 for(const selector of ['#amp-one',chat,nav,'.a-canvas-chrome'])assert.equal((await style(selector)).art,'none');
 assert.equal((await style('.a-layout')).gap,'10px');
 await presentation({decorations:true,layout:'balanced'});assert.notEqual((await style(nav)).art,'none');
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();assert.equal((await style(chat)).tr,'0px');
 await action('view.update',{patch:{navPinned:false,navExpanded:false}});await expect(page.locator(nav)).toBeHidden();assert.equal((await style(chat)).tl,'0px');
 // At this boundary, a 16px gap must remeasure fitPanels without a viewport resize.
 await page.setViewportSize({width:904,height:960});await action('view.update',{patch:{navPinned:true,navWidth:216}});
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 await action('theme.apply',{name:'Boundary',css:aurora.css+'\n#amp-one{--a-panel-gap:16px}#amp-one[data-theme-scheme=dark]{--a-panel-gap:8px}'});
 await expect.poll(async()=>(await style('.a-layout')).gap).toBe('16px');await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked','false');assert.ok((await style(chat)).width>=360);assert.ok((await style(canvas)).right<=905);await assertFits();
 await presentation({scheme:'dark'});await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked','true');assert.ok((await style(canvas)).right<=905);
 await page.setViewportSize({width:1440,height:960});await presentation({scheme:'light'});await action('theme.apply',{name:'Aurora',css:aurora.css});
 await page.getByRole('separator',{name:'Resize canvas'}).focus();await page.keyboard.press('ArrowLeft');await assertFits();
 // Exercise user-facing save -> apply -> download -> import -> reload.
 await settingsButton(page).click();await page.locator('[data-settings-section="appearance"]').click();
 await page.getByRole('button',{name:'Save, import & export themes',exact:true}).click();
 await page.getByText('Advanced customization',{exact:true}).click();await page.locator('#theme-name').fill('My panel theme');
 const custom=aurora.css+'\n#amp-one{--a-panel-gap:13px;--a-panel-radius:17px}';await page.locator('#theme-css').fill(custom);
 await page.getByRole('button',{name:'Save as copy',exact:true}).click();await expect(page.getByRole('button',{name:'Preview My panel theme',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Apply appearance',exact:true}).click();await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');
 const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Export current appearance',exact:true}).click();
 const exported=await readFile(await (await downloaded).path(),'utf8');assert.equal(exported,custom);
 await page.locator('#theme-import').setInputFiles({name:'Imported panels.css',mimeType:'text/css',buffer:Buffer.from(exported)});
 await expect(page.getByRole('button',{name:'Preview Imported panels',exact:true})).toBeVisible();await page.getByRole('button',{name:'Preview Imported panels',exact:true}).click();
 const applied=page.waitForResponse(response=>{if(!response.url().endsWith('/api/actions'))return false;const body=response.request().postDataJSON();return body?.action==='theme.apply'&&body.args?.name==='Imported panels'});
 await page.getByRole('button',{name:'Use Imported panels',exact:true}).click();await applied;
 const closed=page.waitForResponse(response=>{if(!response.url().endsWith('/api/actions'))return false;const body=response.request().postDataJSON();return body?.action==='view.update'&&body.args?.patch?.panel===null});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await closed;await expect(page.locator('.a-settings-overlay')).toBeHidden();
 await page.reload();await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 for(const width of [760,390,320]){
  await page.setViewportSize({width,height:900});await expect.poll(async()=>(await style('.a-layout')).gap).toBe('0px');await assertFits();
  if(await page.locator(canvas).isVisible())await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
  assert.equal((await style(chat)).tl,'0px');assert.equal((await style(chat)).tr,'0px');
  if(width<520){await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();await expect(page.getByRole('dialog',{name:'Chat options',exact:true})).toBeVisible();await page.getByRole('button',{name:'Close chat options',exact:true}).click();}
  await page.screenshot({path:out+'/narrow-'+width+'.png'});await page.getByRole('button',{name:'Open canvas',exact:true}).click();
  await expect(page.locator(canvas)).toBeVisible();assert.equal((await style(canvas)).tl,'0px');assert.equal((await style(canvas)).tr,'0px');await assertFits();
 }
 assert.deepEqual(errors,[]);console.log('Theme surfaces passed: four themes in light/dark, interior corners, gaps, resize boundary, canvas sides/focus, decoration toggle, save/export/import/reload, drafts and narrow controls.');
}finally{await browser?.close();fixture.kill()}
