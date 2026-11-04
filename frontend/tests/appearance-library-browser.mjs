import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
// Packaged application, isolated storage, no external model or feedback calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance library fixture'});
let browser,failure;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-appearance-library';
const focused=process.argv.includes('--catalog-thumbnails-only');
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('session.create');await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep my unfinished ideas.');
 await openSettingsPage(page,'appearance');
 await expect(page.getByRole('button',{name:'Use Aurora',exact:true})).toBeVisible();
 await expect(page.locator('#theme-css')).toBeHidden();
 if(focused){
  // Retained metadata must remain visible when a later refresh cannot finish.
  const original=(await state()).theme,selected=(await state()).selectedSessionId;
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect(page.locator('.a-settings-experience')).toBeHidden();
  await page.route('**/api/actions',async route=>{
   const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
   if(body?.action==='theme.list')await route.abort('failed');else await route.continue();
  });
  await openSettingsPage(page,'appearance');
  await expect(page.getByRole('button',{name:'Use Aurora',exact:true})).toBeVisible();
  await expect(page.getByText('Loading appearances…',{exact:true})).toHaveCount(0);
  assert.deepEqual((await state()).theme,original,'Reading a thumbnail never applies an appearance');
  await page.unrouteAll({behavior:'wait'});
  const measurements=[];
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1050});
   for(const mode of ['light','dark']){
    await page.getByRole('combobox',{name:'Color mode',exact:true}).selectOption(mode);
    const card=page.getByRole('button',{name:'Use Digital Rain',exact:true});
    await card.scrollIntoViewIfNeeded();
    await expect(card.locator('[data-thumbnail-state=ready]')).toHaveAttribute('data-thumbnail-mode',mode);
    const iframe=card.locator('iframe');await expect(iframe).toHaveAttribute('sandbox','');
    await expect(iframe).toHaveAttribute('tabindex','-1');
    const frame=await (await iframe.elementHandle()).contentFrame();assert.ok(frame);
    await expect(frame.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);
    const specimen=await frame.locator('#amp-one').evaluate(el=>({
     scheme:el.dataset.themeScheme,version:el.dataset.thumbnailScene,
     ink:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
     art:getComputedStyle(el.querySelector('[data-part=conversation]'),'::before').backgroundImage,
     composer:!!el.querySelector('[data-part=composer-surface]'),
     paragraphs:el.querySelectorAll('.a-markdown p').length,
     code:el.querySelector('pre code')?.textContent,
     csp:document.querySelector('meta[http-equiv="Content-Security-Policy"]').content,
    }));
    assert.match(specimen.art,/data:image\/png;base64,/,'Actual embedded Rain artwork, not a palette fallback');
    assert.ok(specimen.composer&&specimen.paragraphs>=2);assert.match(specimen.code,/Make it real/);
    assert.match(specimen.csp,/default-src 'none'/);
    const entry=(await action('theme.read',{id:'builtin:digital-rain'})).result;
    await expect(card.locator('[data-appearance-fingerprint]')).toHaveAttribute('data-appearance-fingerprint',entry.fingerprint);
    assert.equal((await state()).theme.name,original.name,'Thumbnail mode does not select a different appearance');
    measurements.push({width,mode,...specimen});
    await page.screenshot({path:`${out}/catalog-digital-rain-${mode}-${width}.png`});
   }
  }
  // Keep the user's existing single-click apply interaction.
  await page.getByRole('button',{name:'Use Digital Rain',exact:true}).click();
  await expect.poll(async()=>(await state()).theme.name).toBe('Digital Rain');
  await expect(page.getByRole('button',{name:'Cancel preview',exact:true})).toHaveCount(0);
  assert.equal((await state()).selectedSessionId,selected);
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect(page.locator('.a-settings-experience')).toBeHidden();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished ideas.');
  assert.deepEqual(errors,[]);
  await (await import('node:fs/promises')).writeFile(`${out}/catalog-thumbnail-measurements.json`,JSON.stringify(measurements,null,2));
  console.log('Appearance catalog/thumbnails passed: warm cards, shared CSS specimen, light/dark and desktop/mobile, isolated frames, click-to-apply and preserved draft/selection.');
 }else{
 await page.screenshot({path:out+'/appearance-gallery.png'});
 // Everyday cards now save immediately. Verify every built-in and both color
 // modes through that public path; previews belong to saved custom themes.
 for(const name of ['Aurora','Atelier','Graphite','Digital Rain']){
  await page.getByRole('button',{name:'Use '+name,exact:true}).click();
  await expect.poll(async()=>(await state()).theme.name).toBe(name);
  await expect(page.getByRole('button',{name:'Cancel preview',exact:true})).toHaveCount(0);
  for(const mode of ['light','dark']){
   await page.getByRole('combobox',{name:'Color mode',exact:true}).selectOption(mode);
   await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);
   await page.screenshot({path:out+'/'+name.toLowerCase()+'-'+mode+'.png'});
  }
 }
 await page.getByRole('button',{name:'Use Aurora',exact:true}).click();
 await expect.poll(async()=>(await state()).theme.name).toBe('Aurora');
 const background=()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).backgroundImage);
 await expect.poll(background).not.toBe('none');
 await page.getByText('Layout & background',{exact:true}).click();
 const decoration=page.getByRole('checkbox',{name:'Show decorative theme background'});
 await decoration.uncheck();await expect.poll(background).toBe('none');await decoration.check();await expect.poll(background).not.toBe('none');
 for(const width of [760,390,320]){
  await page.setViewportSize({width,height:900});
  await expect(page.getByRole('button',{name:'Use Aurora',exact:true})).toBeVisible();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('.a-settings-content').evaluate(el=>el.scrollTop=0);
  await page.screenshot({path:out+'/appearance-'+width+'.png'});
 }
 await page.setViewportSize({width:1440,height:1050});
 await page.getByRole('button',{name:'Save, import & export themes',exact:true}).click();
 await page.locator('#theme-import').setInputFiles({name:'Ocean.amplifier.css',mimeType:'text/css',buffer:Buffer.from('#amp-one{--a-accent:#137c8b;background-image:none}')});
 await expect(page.getByRole('button',{name:'Preview Ocean',exact:true})).toBeVisible();
 assert.equal((await state()).theme.name,'Aurora','Uploading does not change the current theme');
 await page.reload();await expect(page.getByRole('button',{name:'Preview Ocean',exact:true})).toBeVisible();assert.equal((await state()).theme.name,'Aurora');
 const before=(await state()).theme.css;
 await page.getByRole('button',{name:'Preview Ocean',exact:true}).click();
 await expect(page.getByRole('button',{name:'Use Ocean',exact:true})).toBeVisible();assert.equal((await state()).theme.css,before);
 await page.getByRole('button',{name:'Cancel preview',exact:true}).click();
 await expect(page.getByRole('button',{name:'Use Ocean',exact:true})).toHaveCount(0);assert.equal((await state()).theme.css,before);
 await page.locator('#theme-import').setInputFiles({name:'Bad.css',mimeType:'text/css',buffer:Buffer.from('@import "https://example.com/style.css";')});
 await expect(page.getByRole('button',{name:'Preview Bad',exact:true})).toHaveCount(0);assert.equal((await state()).theme.name,'Aurora');
 await page.getByText('Advanced customization',{exact:true}).click();await expect(page.locator('#theme-css')).toBeVisible();await page.locator('#theme-name').fill('My Aurora');await page.getByRole('button',{name:'Save as copy',exact:true}).click();await expect(page.getByRole('button',{name:'Preview My Aurora',exact:true})).toBeVisible();
 await page.getByText('Advanced customization',{exact:true}).click();
 await page.getByRole('button',{name:'Preview Ocean',exact:true}).click();
 await page.getByRole('button',{name:'Use Ocean',exact:true}).click();
 await expect.poll(async()=>(await state()).theme.name).toBe('Ocean');
 await page.getByRole('button',{name:'Preview My Aurora',exact:true}).click();
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await expect.poll(async()=>(await state()).view.themePreview).toBe(false);assert.equal((await state()).theme.name,'Ocean');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished ideas.');assert.deepEqual(errors,[]);
 console.log('Appearance library passed: four autosaved dual-mode built-ins, saved-theme preview/apply/cancel, background toggle, persistent uploads, rejected remote CSS, named save, responsive gallery and preserved draft.');
 }
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Appearance library fixture',close:[['Appearance library browser',()=>browser?.close()]],stop,originalError:failure})}
