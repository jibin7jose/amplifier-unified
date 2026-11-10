// Focus/hover tooltip occlusion at the approved compact composer boundary.
// Synthetic host only; screenshots keep the actual focus/pointer state intact.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/composer-header';
const baseline=process.argv.includes('--confirm-existing-defect');
const evidence={kind:baseline?'existing-defect-observation':'corrected-header-regression',observations:[],limits:['Synthetic runtime; no live draft/provider','No claim of identical typography/colors to the prototype']};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 await mkdir(output,{recursive:true});
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:320,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],mutations=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions')mutations.push(r.postDataJSON().action)});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 const editor=page.getByRole('textbox',{name:'Message Amplifier'}),trigger=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
 const options=page.getByRole('dialog',{name:'Chat options',exact:true}),add=page.getByRole('button',{name:'Add attachments',exact:true}),addMenu=page.getByRole('dialog',{name:'Add to your message',exact:true});
 await editor.fill('Retain this private synthetic draft.');
 const frame=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const inspect=async(panel,label)=>{
  await frame();const value=await panel.evaluate(el=>{
   const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
   const heading=el.querySelector('.a-composer-options-heading strong'),range=document.createRange();range.selectNodeContents(heading);const title=rect(range);
   const tips=[...document.querySelectorAll('[role=tooltip]')].filter(t=>t.getClientRects().length).map(t=>({text:t.textContent,...rect(t)}));
   return {titleText:heading.textContent,title,tips,overlap:tips.some(t=>t.left<title.right&&t.right>title.left&&t.top<title.bottom&&t.bottom>title.top),active:document.activeElement?.getAttribute('aria-label'),description:document.activeElement?.getAttribute('aria-describedby'),surface:rect(el)};
  });evidence.observations.push({label,...value});return value;
 };
 const readable=value=>assert.equal(value.overlap,false,'A tooltip must not obscure the visible composer menu heading');
 if(baseline){
  await trigger.click();await expect(options.getByRole('button',{name:'Choose model',exact:true})).toBeFocused();
  await expect(page.getByRole('tooltip',{name:'Choose model',exact:true})).toBeVisible();
  const observed=await inspect(options,'ordinary-open-focus');assert.throws(()=>readable(observed));
  await options.screenshot({path:output+'/before-focused-320.png'});
  await page.mouse.move(0,0);await page.evaluate(()=>document.activeElement.blur());
  await expect(page.getByRole('tooltip')).toHaveCount(0);readable(await inspect(options,'blur-clears-transient-focus-tooltip'));
  evidence.disposition='Real product focus-state occlusion; not screenshot-only decoration. Clearing focus removes it but is not a fix.';
 }else{
  for(const width of [320,520])for(const scheme of ['light','dark']){
   await page.setViewportSize({width,height:844});
   const revision=await page.evaluate(async scheme=>{const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme}}});if(!prepared.accepted)throw Error(prepared.error);const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});if(!applied.accepted)throw Error(applied.error);return applied.result.revision;},scheme);
   await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().revision)).toBe(revision);await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
   await trigger.focus();await page.keyboard.press('Enter');const first=options.getByRole('button',{name:'Choose model',exact:true});await expect(first).toBeFocused();
   readable(await inspect(options,`${width}-${scheme}-keyboard-open`));await expect(page.getByRole('tooltip')).toHaveCount(0);
   // Do not move the mouse or blur the automatically focused row for the capture.
   await options.screenshot({path:`${output}/after-focused-${width}-${scheme}.png`});
   for(const name of ['Choose model','Choose reasoning effort','Choose provider','Choose agent bundle']){
    const row=options.getByRole('button',{name,exact:true});await row.focus();await row.hover();readable(await inspect(options,`${width}-${scheme}-${name}`));await expect(page.getByRole('tooltip')).toHaveCount(0);
    const descriptions=await row.evaluate(el=>(el.getAttribute('aria-describedby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent).filter(Boolean));assert.ok(descriptions.length,'Existing accessible value description stays available');
    await row.click();await expect(options.getByRole('button',{name:'Back to chat options',exact:true})).toBeFocused();await options.getByRole('button',{name:'Back to chat options',exact:true}).click();await expect(row).toBeFocused();readable(await inspect(options,`${width}-${scheme}-back-${name}`));
   }
   await page.keyboard.press('Escape');await expect(trigger).toBeFocused();
   await add.click();await expect(addMenu.getByRole('button',{name:'Attach files',exact:true})).toBeFocused();readable(await inspect(addMenu,`${width}-${scheme}-add-open`));
   await addMenu.screenshot({path:`${output}/after-add-${width}-${scheme}.png`});
   for(const name of ['Attach files','Add an image']){const row=addMenu.getByRole('button',{name,exact:true});await row.focus();await row.hover();readable(await inspect(addMenu,`${width}-${scheme}-${name}`));await expect(page.getByRole('tooltip')).toHaveCount(0);}
   // Disabled access still receives pointer hover in the draft state. It must
   // not introduce another redundant tooltip; no chat is created to test it.
   await addMenu.getByRole('button',{name:'Chat access',exact:true}).hover();
   readable(await inspect(addMenu,`${width}-${scheme}-chat-access-hover`));await expect(page.getByRole('tooltip')).toHaveCount(0);
   await page.keyboard.press('Escape');await expect(add).toBeFocused();await expect(editor).toHaveDraft('Retain this private synthetic draft.');
  }
  // Removing the correction revives the real tooltip and MUST fail the same check.
  await trigger.click();const first=options.getByRole('button',{name:'Choose model',exact:true});await expect(first).toBeFocused();
  await first.evaluate(el=>{el.blur();el.removeAttribute('data-tooltip');el.focus()});
  await expect(page.getByRole('tooltip',{name:'Choose model',exact:true})).toBeVisible();const negative=await inspect(options,'negative-redundant-tooltip-restored');assert.throws(()=>readable(negative));evidence.negativeRejected=true;
  await first.evaluate(el=>{el.blur();el.setAttribute('data-tooltip','off');el.focus()});await expect(page.getByRole('tooltip')).toHaveCount(0);readable(await inspect(options,'correction-restored'));
  // Icon-only help is deliberately unchanged by the scoped row opt-out.
  const close=options.getByRole('button',{name:'Close chat options',exact:true});await close.hover();await expect(page.getByRole('tooltip',{name:'Close chat options',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await expect(options).toHaveCount(0);await expect(trigger).toBeFocused();
 }
 assert.equal(mutations.filter(a=>['conversation.send','conversation.configure','attachment.add','access.set','session.create'].includes(a)).length,0);assert.deepEqual(errors,[]);
 await writeFile(output+'/header.json',JSON.stringify(evidence,null,2));console.log(baseline?'CONFIRMED focus tooltip overlaps heading on existing source; blur removes it.':'PASS composer header: readable on open/focus/hover/Back, names/descriptions retained, icon help retained, real tooltip negative rejected.');
}catch(error){evidence.failure=String(error.stack||error);await writeFile(output+'/header.json',JSON.stringify(evidence,null,2));await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error;
}finally{await browser?.close();if(fixture.exitCode===null&&fixture.signalCode===null){const done=once(fixture,'exit'),timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);fixture.kill('SIGTERM');try{await done}finally{clearTimeout(timer)}}}
