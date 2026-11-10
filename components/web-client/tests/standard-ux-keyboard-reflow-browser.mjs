import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

// Sequential Tab acceptance, not programmatic focus plus key activation.
// Half-sized CSS viewport exercises 200% zoom-equivalent reflow; browser/OS
// zoom controls and magnifier behavior remain separate manual acceptance.
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('Owned AMPLIFIER_TEST_ARTIFACTS is required');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/standard_ux_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
const observations=[];
try{
 const port=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 for(const zoom of [1,2]){
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  const context=await browser.newContext({viewport:{width:1440/zoom,height:1000/zoom},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  page.setDefaultTimeout(7000);
  await page.goto('http://127.0.0.1:'+port);await showFixtureConversation(page);
  const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();
  await composer.fill('Preserve keyboard reflow draft '+zoom);
  // Existing composer focus is the starting point. Every subsequent activation
  // must be reached through the browser's actual sequential focus order.
  const reach=async(selector,label)=>{
   const target=typeof selector==='string'?page.locator(selector):selector;
   for(let tabs=0;tabs<160;tabs++){
    if(await target.count()===1&&await target.evaluate(el=>el===document.activeElement)){
     await expect(target).toBeInViewport({ratio:1});
     const geometry=await page.evaluate(()=>{const el=document.activeElement,r=el.getBoundingClientRect(),s=getComputedStyle(el);return {x:r.x,y:r.y,width:r.width,height:r.height,viewportWidth:innerWidth,viewportHeight:innerHeight,visible:s.visibility!=='hidden'&&s.display!=='none',focusVisible:el.matches(':focus-visible'),outline:s.outlineStyle,outlineWidth:s.outlineWidth,shadow:s.boxShadow,name:el.getAttribute('aria-label')||el.textContent.trim()}});
     assert.ok(geometry.visible&&geometry.width>0&&geometry.height>0,label+' is visible');
     assert.ok(geometry.x>=-1&&geometry.x+geometry.width<=geometry.viewportWidth+1,label+' fits horizontally');
     assert.ok(geometry.y>=-1&&geometry.y+geometry.height<=geometry.viewportHeight+1,label+' focused target scrolls into view');
     assert.ok(geometry.focusVisible,label+' uses keyboard focus');
     assert.ok((geometry.outline!=='none'&&parseFloat(geometry.outlineWidth)>0)||geometry.shadow!=='none',label+' has a visible focus indicator');
     observations.push({zoom,label,tabs,geometry});return;
    }
    await page.keyboard.press('Tab');
   }
   throw Error('Not sequentially keyboard reachable: '+label);
  };
  if(await page.getByRole('button',{name:'Open navigation',exact:true}).isVisible()){
   await reach('button[aria-label="Open navigation"]','Open navigation');await page.keyboard.press('Enter');
  }
  await reach(page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/}),'Settings');
  await page.keyboard.press('Enter');
  const panel=page.locator('.a-settings-experience');await expect(panel).toBeVisible();
  for(const id of ['ai','smart-tools','appearance','workspaces','voice','notifications','privacy','updates']){
   if(await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true'){
    await reach('.a-settings-mobile-head button[aria-label^="Back to "]','Back to Settings');await page.keyboard.press('Enter');await expect(panel).toHaveAttribute('data-settings-index','true');
   }
   await reach('[data-settings-section="'+id+'"]',id+' section');await page.keyboard.press('Enter');
   await expect(panel.locator('.a-settings-page-content:not([hidden])')).toBeVisible();
   const overflow=await panel.evaluate(el=>({scroll:el.scrollWidth,width:el.clientWidth}));assert.ok(overflow.scroll<=overflow.width+1,id+' panel has no horizontal overflow');
   await page.screenshot({path:output+'/'+zoom+'x-'+id+'.png'});
  }
  await reach(page.getByRole('button',{name:await panel.getAttribute('data-compact')==='true'?'Close settings':'Close panel',exact:true}),'Close settings');await page.keyboard.press('Enter');await expect(panel).toHaveCount(0);
  await expect(composer).toHaveDraft('Preserve keyboard reflow draft '+zoom);
  assert.deepEqual(errors,[]);
  await context.close();await browser.close();browser=null;
 }
 await writeFile(output+'/observations.json',JSON.stringify({passed:true,observations,limits:['Synthetic app runtime; no provider calls','Named ordinary Settings destinations and navigation entry only; not every control','720x500 CSS viewport (200% equivalent of1440x1000), not browser toolbar/OS magnifier acceptance']},null,2)+'\n');
}finally{
 await writeFile(output+'/observations-partial.json',JSON.stringify({observations},null,2)+'\n');
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){const stopped=once(fixture,'exit');fixture.kill('SIGTERM');await Promise.race([stopped,new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture shutdown timeout')),5000).unref())])}
 assert.equal(fixture.exitCode,0);assert.equal(fixture.signalCode,null);
}
