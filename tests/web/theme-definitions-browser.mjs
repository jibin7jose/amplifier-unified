import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({colorScheme:'dark',viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 // Appearance paints before persistence. Exercise that boundary with a slow
 // apply response instead of allowing reload to cancel an unsaved preference.
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='shell.changes.apply')await new Promise(resolve=>setTimeout(resolve,500));await route.continue()});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const presentation=patch=>page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id})},patch);
 const tokens=['bg','surface','soft','ink','muted','line','accent','tint','green','danger'];
 const definition={version:1,palette:{light:Object.fromEntries(tokens.map(key=>[key,key==='bg'?'#eef8f6':'#245b53'])),dark:Object.fromEntries(tokens.map(key=>[key,key==='bg'?'#102c28':'#b4e4d8']))},background:{light:'linear-gradient(135deg, #b4e4d8, #eef8f6)',dark:'radial-gradient(ellipse at top, #245b53, #102c28)'}};
 const css=()=>page.locator('#amp-one').evaluate(el=>{const s=getComputedStyle(el);return {background:s.backgroundImage,color:s.backgroundColor,scheme:s.colorScheme}});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await action('session.create');
 const draft='Preserve this unsent draft\nwith eight\nsaved\nlines\nthat need\nroom\nto stay\nreadable';
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 // Filling a contenteditable creates paragraphs; compare the canonical Markdown
 // captured before any theme operation, while still checking every input line.
 const savedDraft=await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'}));
 assert.deepEqual(savedDraft.split(/\n+/),draft.split('\n'));
 assert.equal(savedDraft,draft.replaceAll('\n','\n\n'));
 console.log(JSON.stringify({draftBeforeTheme:savedDraft}));
 await presentation({scheme:'light'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
 await action('theme.preview',{name:'Quiet water',definition});
 await expect.poll(async()=>(await css()).color).toBe('rgb(238, 248, 246)');
 const preview=await css();assert.match(preview.background,/linear-gradient/);assert.doesNotMatch(preview.background,/207, 196, 255/);
 await action('theme.apply',{name:'Quiet water',definition});assert.deepEqual(await css(),preview);
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.getByRole('button',{name:/^Settings/}).click();
 await page.locator('[data-settings-section="appearance"]').click();
 await page.getByText('Layout & background',{exact:true}).click();
 const decoration=page.getByRole('checkbox',{name:'Show decorative theme background'});
 const setDecoration=async enabled=>{
  await expect(decoration).toBeEnabled();
  const prepared=page.waitForResponse(response=>{
   const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;
   return response.url().endsWith('/api/actions')&&body?.action==='shell.changes.prepare'&&body.args.composition.presentation.decorations===enabled;
  });
  const applied=page.waitForResponse(async response=>{
   const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;
   if(!response.url().endsWith('/api/actions')||body?.action!=='shell.changes.apply')return false;
   return body.args.changeId===(await (await prepared).json()).result.id;
  });
  await decoration.setChecked(enabled);
  await expect.poll(async()=>(await css()).background).toBe(enabled?preview.background:'none');
  assert.ok((await prepared).ok());
  const receipt=await applied;assert.ok(receipt.ok());assert.equal((await receipt.json()).accepted,true);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState()?.effectiveComposition.presentation.decorations)).toBe(enabled);
  await expect(decoration).toBeEnabled();
 };
 await setDecoration(false);
 await setDecoration(true);
 // Keep Settings open: its focus trap must attach after shell restoration.
 // Delay shell preferences on reload: the app must not paint system-dark UI
 // while waiting for the authoritative client composition to arrive.
 let release,continued;const gate=new Promise(resolve=>{release=resolve});
 const continuation=new Promise(resolve=>{continued=resolve});
 await page.route('**/api/shell?*',async route=>{await gate;await route.continue();continued()});
 await page.reload({waitUntil:'domcontentloaded'});
 await expect(page.locator('.boot')).toBeVisible();await expect(page.locator('#amp-one')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'light');
 assert.equal(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor),'rgb(238, 248, 246)');
 release();await continuation;await page.unrouteAll({behavior:'wait'});
 await expect(page.getByRole('dialog')).toBeVisible();
 await expect.poll(()=>page.getByRole('dialog').evaluate(el=>el.contains(document.activeElement))).toBe(true);
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(savedDraft);
 await expect.poll(()=>page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(170);
 assert.deepEqual(await css(),preview);
 await presentation({scheme:'system'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','dark');
 await expect.poll(async()=>(await css()).color).toBe('rgb(16, 44, 40)');
 await page.emulateMedia({colorScheme:'light'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
 await expect.poll(async()=>(await css()).background).toBe(preview.background);
 await action('theme.apply',{name:'Flat water',definition:{version:1,palette:definition.palette}});
 await expect.poll(async()=>(await css()).background).toBe('none');
 assert.deepEqual(errors,[]);
 console.log('Complete theme preview/apply, decoration toggle, system appearance, reload first-paint preference and draft preservation passed.');
}finally{await browser?.close();fixture.kill()}
