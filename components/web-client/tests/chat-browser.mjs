import {startChatFixture} from './chat-fixture.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const {base,stop}=await startChatFixture();
let browser,failure;
try{
browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)});
const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5V8AAAAASUVORK5CYII=','base64');
 await page.goto(base);await showFixtureConversation(page);await page.waitForSelector('#amp-one');
 assert.equal(await page.locator('.a-modality').count(),0);
 assert.equal(await page.locator('.a-conversation .a-operations,.a-conversation .a-computation').count(),0);
 const initial=await page.locator('.a-composer').boundingBox();assert.ok(initial.y>250&&initial.y<650);
 await page.getByRole('button',{name:'Model and reasoning settings'}).click();
 await showComposerOption(page,'provider');
 await page.locator('#chat-provider').waitFor();
 await showComposerOption(page,'model');
 await page.locator('#chat-model').selectOption('fixture-vision');
 await page.waitForFunction(()=>Object.values(window.amplifier.getState().runtimeControl||{}).some(c=>c['configuration.catalog']?.effective?.model==='fixture-vision'));
 await showComposerOption(page,'effort');
 await page.locator('#chat-effort').press('End');
 await page.waitForFunction(()=>window.amplifier.getState().view.composerModel?.effort==='high');
 await page.waitForFunction(()=>Object.values(window.amplifier.getState().runtimeControl||{}).some(c=>c['configuration.catalog']?.pinned&&c['configuration.catalog']?.effective?.effort==='high'));
 // A host view refresh during a pointer gesture must not restore the old
 // effort before pointer-up. The selected value persists on release/reopen.
 const slider=page.locator('#chat-effort'),box=await slider.boundingBox();
 await page.mouse.move(box.x+box.width-8,box.y+box.height/2);await page.mouse.down();
 await page.mouse.move(box.x+8,box.y+box.height/2);
 await action('view.update',{patch:{composerModel:await page.evaluate(()=>({...window.amplifier.getState().view.composerModel}))}});
 assert.equal(await slider.getAttribute('aria-valuetext'),'low');
 await page.mouse.up();
 await page.waitForFunction(()=>Object.values(window.amplifier.getState().runtimeControl||{}).some(c=>c['configuration.catalog']?.effective?.effort==='low'));
 await slider.press('End');
 await page.waitForFunction(()=>Object.values(window.amplifier.getState().runtimeControl||{}).some(c=>c['configuration.catalog']?.effective?.effort==='high'));
 await page.getByRole('button',{name:'Close chat options'}).click();
 assert.match(await page.getByRole('button',{name:'Model and reasoning settings'}).innerText(),/fixture-vision/);
 assert.match(await page.getByRole('button',{name:'Model and reasoning settings'}).innerText(),/fixture-vision \(high\)/);
 if(process.argv.includes('--model-only')){
  assert.deepEqual(errors,[]);console.log('Conversation model browser checks passed: shared catalog, saved model/effort, selection label, no runtime control discovery.');
 }else{
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'pixel.png',mimeType:'image/png',buffer:png});
 await page.locator('.a-composer .a-attachment').waitFor();
 assert.equal(await page.locator('.a-composer .a-attachment img').count(),1);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Please inspect this image.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.locator('.a-assistant h2').waitFor();
 assert.equal(await page.locator('.a-user .a-attachment img').count(),1);
 assert.equal(await page.locator('.a-composer .a-attachment').count(),0);
 assert.ok(await page.locator('.a-messages').evaluate(el=>el.scrollHeight>el.clientHeight));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight));
 const chat=await page.locator('.a-conversation').boundingBox();assert.ok(chat.y+chat.height<=900&&chat.y+chat.height>=870);
 await page.screenshot({path:'/tmp/amplifier-chat-desktop.png'});
 // Clipboard image and dropped text file both enter the same persistent attachment draft.
 await page.locator('.a-composer').evaluate((el,encoded)=>{const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)),data=new DataTransfer();data.items.add(new File([bytes],'pasted.png',{type:'image/png'}));el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));},png.toString('base64'));
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).draftAttachments?.length===1);
 await page.locator('.a-composer').evaluate(el=>{const data=new DataTransfer();data.items.add(new File(['hello'],'dropped.txt',{type:'text/plain'}));el.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));});
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).draftAttachments?.length===2);
 await action('canvas.show',{kind:'markdown',title:'Agent output',content:'# A useful document\n\nCanvas content.'});
 await page.getByText('A useful document',{exact:true}).waitFor();
 await page.screenshot({path:'/tmp/amplifier-chat-canvas.png'});
 const separator=page.getByRole('separator',{name:'Resize canvas'});await separator.focus();await page.keyboard.press('ArrowLeft');
 await page.waitForFunction(()=>window.amplifier.getState().view.canvasWidth===460);
 await action('canvas.show',{kind:'a2ui',title:'A next step',surface:{surfaceId:'test',root:'root',components:[{id:'root',component:{Button:{child:'label',action:{name:'review'}}}},{id:'label',component:{Text:{text:{literalString:'Review the plan'}}}}]}});
 await page.getByRole('button',{name:'Review the plan',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.events.at(-1)?.name==='review');
 await action('canvas.close',{});
 await page.getByRole('button',{name:'Pin navigation open',exact:true}).hover();
 await page.getByRole('button',{name:'Pin navigation open',exact:true}).click();
 await page.locator('.a-navigation-workspace').waitFor();
 await page.getByRole('searchbox',{name:'Filter conversations'}).fill('Settings*');
 await page.waitForFunction(()=>document.querySelectorAll('.a-nav-chat').length===1);
 assert.equal(await page.locator('.a-nav-chat').count(),1);
 await page.getByRole('searchbox',{name:'Filter conversations'}).fill('no-match*');
 await page.waitForFunction(()=>document.querySelectorAll('.a-nav-chat').length===0);
 await page.getByRole('searchbox',{name:'Filter conversations'}).fill('');
 await page.locator('.a-navigation-workspace').getByRole('button',{name:/Details and actions/}).click();
 await page.locator('.a-navigation-flyout').getByRole('button',{name:'Rename',exact:true}).click();
 await page.locator('#nav-workspace-name').fill('My project');await page.getByRole('button',{name:'Save name',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().workspaces.some(w=>w.name==='My project'));
 await page.locator('.a-nav-chat').filter({has:page.locator('.a-nav-chat-select').filter({hasText:'Settings test'})}).getByRole('button',{name:/Details and actions/}).click();
 await page.getByRole('button',{name:'Rename Settings test',exact:true}).click();
 await page.getByRole('textbox',{name:'New name for Settings test'}).fill('Renamed chat');
 await page.waitForFunction(async()=>{const view=window.amplifier.getState().renderedView,index=view.controls.findIndex(c=>c.label==='New name for Settings test');if(index<0)return false;const result=await fetch('/api/state/detail?'+new URLSearchParams({path:`/devices/${view.clientId}/controls/${index}/value`}));return result.ok&&(await result.json()).value==='Renamed chat'});
 await page.getByRole('button',{name:'Save conversation name',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().sessions.some(s=>s.title==='Renamed chat'));
 await page.waitForFunction(()=>document.querySelectorAll('button[aria-label]:has(svg):not([title]),a[aria-label]:has(svg):not([title])').length===0);
 await page.getByRole('button',{name:'Unpin navigation',exact:true}).click();
 await page.mouse.move(1000,30);

 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
 const composer=await page.locator('.a-composer').boundingBox();assert.ok(composer.x>=0&&composer.x+composer.width<=390);
 await page.screenshot({path:'/tmp/amplifier-chat-mobile.png'});
 assert.deepEqual(errors,[]);console.log('Chat browser checks passed: composer, pin/effort, upload/paste/drop, Markdown, scroll containment, canvas, narrow layout.');
}
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Chat fixture',close:[['Chat browser',()=>browser?.close()]],stop,originalError:failure})}
