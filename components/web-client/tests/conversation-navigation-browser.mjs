import {createServer} from 'vite';
import {showComposerOption} from './composer-options-helpers.mjs';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import assert from 'node:assert/strict';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
const full=Array.from({length:791},(_,i)=>({id:'m'+i,role:i%10===0?'user':'assistant',text:'Message '+i,createdAt:1000+i}));
const messageWindow={mode:'cursor',offset:null,total:null,before:'older-731',hasEarlier:true,windowKey:'latest-731',coverage:{kind:'saved-web',historyComplete:false}};
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{setupJourney:'done',navPinned:true},sessions:[{id:'chat',sessionKind:'root',title:'Long chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:false,historyLoaded:false,historyLoading:false,messages:full.slice(-60),messageWindow,execution:{nodes:[],turns:[]}}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',canvas:{open:false}};
const previews=[],indexes=[],windows=[],earlier=[];let restoring=false;
let browser,vite,page;const errors=[],calls=[];
const shell=createShellHttpFixture(()=>state);
const evidence=process.env.AMPLIFIER_TEST_ARTIFACTS||await mkdtemp(join(tmpdir(),'conversation-navigation-'));
await mkdir(evidence,{recursive:true});
try{
 vite=await createServer({configFile:false,cacheDir:fileURLToPath(new URL('../.vite-legacy-mocks',import.meta.url)),root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});page=await browser.newPage({viewport:{width:1440,height:900}});page.on('pageerror',e=>errors.push(e.stack||e.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(path==='/api/conversation/navigation'){
   const query=new URL(route.request().url()).searchParams,id=query.get('messageId');
   if(!id){indexes.push(query.toString());return route.fulfill({status:500,json:{error:'The loaded-window rail must not request a whole-history index.'}})}
   const index=full.findIndex(row=>row.id===id);
   if(query.get('window')==='true'){
    windows.push({id,restoring});const start=Math.max(0,index-5);
    return route.fulfill({json:{messages:full.slice(start,start+60),offset:start,total:full.length,before:start?'m'+Math.max(0,start-55):null,after:'m'+(start+60),execution:{nodes:[],turns:[]}}});
   }
   previews.push(id);return route.fulfill({status:500,json:{error:'Hover must use text already in the loaded window.'}});
  }
  if(path==='/api/conversation/detail'){
   const query=new URL(route.request().url()).searchParams,before=query.get('before');
   assert.equal(query.get('part'),'messages');assert.equal(query.get('sessionId'),'chat');
   assert.match(before,/^older-\d+$/);const end=Number(before.slice(6)),start=Math.max(0,end-100);
   assert.ok(end>0&&end<=731);earlier.push(before);
   return route.fulfill({json:{items:full.slice(start,end),mode:'cursor',offset:null,total:null,
    before:start?'older-'+start:null,hasEarlier:start>0,windowKey:'page-'+start,
    coverage:{kind:'saved-web',historyComplete:false}}});
  }
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/outputs/media'&&route.request().method()==='GET')
   return route.fulfill({json:{items:[],overflow:false,mediaRevision:0}});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   const envelope=route.request().postDataJSON(),body=envelope.action==='shell.command'?envelope.args:envelope;calls.push(body);
   if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
   return route.fulfill({json:{accepted:true,operationId:body.id,state:shell.state()}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 const marks=page.locator('.a-rail-mark');await expect(marks).toHaveCount(6);
 const assertLoadedMarks=async()=>{
  const visible=await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>row.dataset.messageId));
  const ids=await marks.evaluateAll(rows=>rows.map(row=>row.dataset.turnId));
  assert.deepEqual(ids,visible.filter(id=>full.find(row=>row.id===id)?.role==='user').slice(-100));
 };
 await assertLoadedMarks();assert.deepEqual({indexes,previews,windows,earlier},{indexes:[],previews:[],windows:[],earlier:[]});
 await expect(marks.last()).toHaveAttribute('data-visible','true');
 await marks.nth(3).hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 770');
 await expect(page.locator('.a-rail-preview p')).toHaveText('Message 771');
 assert.deepEqual(previews,[]);
 // Only an explicit earlier-page action broadens the reading window. Its
 // unknown total is not permission to preload the remaining 631 messages.
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id]')).toHaveCount(160);
 await expect(marks).toHaveCount(16);await assertLoadedMarks();
 assert.deepEqual(earlier,['older-731']);assert.deepEqual(windows,[]);
 const bookmarked=page.locator('.a-rail-mark[data-turn-id="m670"]');
 await bookmarked.hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 670');
 const firstMark=await bookmarked.boundingBox(),firstLine=await bookmarked.locator('span').boundingBox();
 assert.ok(firstLine.x-firstMark.x<5,'Marks must start at the rail edge, leaving room for magnification.');
 const bookmark=page.getByRole('button',{name:'Bookmark message',exact:true});
 const bookmarkBox=await bookmark.boundingBox();
 await page.mouse.move(bookmarkBox.x+8,bookmarkBox.y+8,{steps:20});
 await bookmark.click();await expect(bookmark).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Remove message bookmark',exact:true})).toBeVisible();
 await page.mouse.move(600,80);await bookmarked.hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 670');
 assert.deepEqual(previews,[]);
 await bookmarked.click();await expect(page.locator('[data-message-id="m670"]')).toBeVisible();
 assert.deepEqual(windows,[]);assert.equal(await page.locator('[data-message-id]').count(),160);
 // A publication while reading older messages must not repeat the jump.
 const pane=page.locator('.a-messages');
 await pane.evaluate(el=>{el.scrollTop=250;el.dispatchEvent(new Event('scroll'))});
 const readingTop=await pane.evaluate(el=>el.scrollTop);
 state={...state,revision:state.revision+1,sessions:state.sessions.map(s=>({...s,messages:[...s.messages,{id:'m791',role:'assistant',text:'New live reply'}]}))};
 await page.evaluate(value=>window.emitState(value),shell.state());
 await expect(page.locator('[data-message-id="m791"]')).not.toBeInViewport();
 await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBe(readingTop);
 assert.deepEqual(earlier,['older-731']);assert.deepEqual(windows,[]);
 // A reload initially gets only the live tail. Fetch the saved reading window
 // through the same bounded API and retain the offset within its anchor row.
 const reading=await pane.evaluate(el=>{const top=el.getBoundingClientRect().top,node=[...el.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);return {id:node.dataset.messageId,offset:node.getBoundingClientRect().top-top}});
 restoring=true;await page.reload();
 await expect(page.locator(`[data-message-id="${reading.id}"]`)).toBeVisible();
 await expect.poll(()=>pane.evaluate((el,id)=>el.querySelector(`[data-message-id="${id}"]`).getBoundingClientRect().top-el.getBoundingClientRect().top,reading.id)).toBeCloseTo(reading.offset,0);
 restoring=false;assert.deepEqual(windows,[{id:reading.id,restoring:true}]);
 assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('amplifier.message-bookmarks.v1')).chat),['m670']);
 assert.ok(await page.locator('[data-message-id]').count()<=60);
 await page.evaluate(value=>window.emitState(value),shell.state());
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 await expect(page.locator('[data-message-id="m791"]')).toBeVisible();
 assert.ok(await page.locator('[data-message-id]').count()<=61);
 await expect(marks).toHaveCount(6);await assertLoadedMarks();
 await marks.nth(4).hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 780');
 // The current/last turn must use the same fisheye width as any other turn.
 const preview=page.locator('.a-rail-preview');
 const expandedWidth=async mark=>(await mark.locator('span').boundingBox()).width;
 await expect.poll(()=>expandedWidth(marks.nth(4))).toBeCloseTo(37.8,1);
 const ordinaryWidth=await expandedWidth(marks.nth(4));
 const assertPreviewGap=async mark=>{
  const line=await mark.locator('span').boundingBox(),card=await preview.boundingBox();
  assert.ok(card.x-line.x-line.width>=6&&card.x-line.x-line.width<=10,'Preview should stay close to the expanded mark without overlapping it.');
 };
 await assertPreviewGap(marks.nth(4));
 await page.screenshot({path:join(evidence,'conversation-navigation.png')});
 await marks.last().hover();await expect(preview.locator('.a-rail-preview-jump')).toHaveText('Message 790');
 await expect.poll(()=>expandedWidth(marks.last())).toBeCloseTo(ordinaryWidth,1);
 await assertPreviewGap(marks.last());
 await page.screenshot({path:join(evidence,'conversation-navigation-last.png')});
 await marks.nth(4).hover();await expect(preview.locator('.a-rail-preview-jump')).toHaveText('Message 780');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await marks.nth(4).locator('span').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
 assert.equal(await page.locator('.a-messages').getAttribute('data-overflow-above'),'true');
 assert.notEqual(await page.locator('.a-messages').evaluate(el=>getComputedStyle(el).maskImage),'none');
 await page.emulateMedia({forcedColors:'active'});
 assert.equal(await page.locator('.a-messages').evaluate(el=>getComputedStyle(el).maskImage),'none');
 // Navigation responds to the available chat width, not just the window.
 await page.emulateMedia({forcedColors:'none'});
 const conversation=page.locator('.a-conversation'),rail=page.locator('.a-conversation-rail');
 await expect(rail).toBeVisible();
 await conversation.evaluate(el=>el.style.maxWidth='700px');
 await expect(rail).toBeHidden();await expect(preview).toBeHidden();
 await conversation.evaluate(el=>el.style.removeProperty('max-width'));
 await expect(rail).toBeVisible();
 await page.setViewportSize({width:1100,height:900});
 await expect.poll(()=>conversation.evaluate(el=>el.clientWidth)).toBeLessThan(961);
 await expect(rail).toBeHidden();
 await pane.evaluate(el=>{el.scrollTop=(el.scrollHeight-el.clientHeight)/2;el.dispatchEvent(new Event('scroll'))});
 const latest=page.getByRole('button',{name:'Jump to latest messages'});await expect(latest).toBeVisible();
 // Published compact composer keeps both controls in one paged Chat options dialog.
 // Reopen for each control. The compact layout need not overlap Latest;
 // when it does, keep the original intersection hit test. Always hit-test
 // Close and separated Latest as well, so visibility alone cannot pass.
 for(const label of ['Conversation model','Registered root bundles']){
  await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click({timeout:8000});
  const popup=page.getByRole('dialog',{name:'Chat options',exact:true});await expect(popup).toBeVisible();
  await showComposerOption(page,label==='Conversation model'?'model':'bundle');
  await expect(popup.getByRole('combobox',{name:label,exact:true})).toBeVisible();
  const unobscured=()=>popup.evaluate((el,jump)=>{
   const a=el.getBoundingClientRect(),b=document.querySelector(jump).getBoundingClientRect();
   const left=Math.max(a.left,b.left),right=Math.min(a.right,b.right),top=Math.max(a.top,b.top),bottom=Math.min(a.bottom,b.bottom);
   if(right>left&&bottom>top&&!el.contains(document.elementFromPoint((left+right)/2,(top+bottom)/2)))return false;
   // When separated, keep both the popup and Latest hit-testable.
   if((right<=left||bottom<=top)&&(!el.contains(document.elementFromPoint(a.x+a.width/2,a.y+a.height/2))||!document.querySelector(jump).contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2))))return false;
   const close=el.querySelector('[aria-label="Close chat options"]'),rect=close.getBoundingClientRect();
   return close.contains(document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2));
  },'.a-chat-jump button');
  await expect.poll(unobscured).toBe(true);
  // Deliberate detector control, not product evidence: an intercepting layer
  // must fail the same predicate even when Latest itself does not overlap.
  await popup.evaluate(el=>{
   const r=el.querySelector('[aria-label="Close chat options"]').getBoundingClientRect(),block=document.createElement('div');
   block.id='navigation-obscuration-control';
   Object.assign(block.style,{position:'fixed',left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px',zIndex:'2147483647',pointerEvents:'auto'});
   document.body.append(block);
  });
  try{assert.equal(await unobscured(),false,'An obscured control must fail the hit test')}
  finally{await page.evaluate(()=>document.getElementById('navigation-obscuration-control')?.remove())}
  await expect.poll(unobscured).toBe(true);
  await popup.getByRole('button',{name:'Close chat options',exact:true}).click();
 }
 await page.screenshot({path:join(evidence,'conversation-navigation-narrow.png')});
 await page.setViewportSize({width:390,height:844});await expect(rail).toBeHidden();
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Chat options',exact:true})).toBeVisible();
 await showComposerOption(page,'model');
 await expect(page.getByRole('combobox',{name:'Conversation model',exact:true})).toBeVisible();
 await showComposerOption(page,'bundle');
 await expect(page.getByRole('combobox',{name:'Registered root bundles',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.setViewportSize({width:1440,height:900});await expect(rail).toBeVisible();
 // New input leaves an older reading window; arrival alone did not.
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id="m670"]')).toHaveCount(1);
 await page.locator('.a-rail-mark[data-turn-id="m670"]').click();
 await expect(page.locator('[data-message-id="m670"]')).toBeInViewport();
 assert.deepEqual(earlier,['older-731','older-731']);
 restoring=true;await page.reload();
 await expect(page.getByRole('button',{name:'Return to latest',exact:true})).toBeVisible();restoring=false;
 await page.locator('.ProseMirror').fill('A new question');await page.locator('.ProseMirror').press('Enter');
 await expect(page.getByRole('button',{name:'Return to latest',exact:true})).toHaveCount(0);
 await expect(page.locator('[data-message-id="m791"]')).toHaveCount(1);
 assert.deepEqual(indexes,[]);assert.deepEqual(previews,[]);assert.ok(windows.every(row=>row.restoring));
 assert.deepEqual(errors,[]);console.log('Loaded-window rail, zero index/hover requests, explicit earlier paging, local jumps, reading restoration, and return to latest passed.');
}catch(error){console.error(JSON.stringify({errors,calls:calls.slice(-8),indexes,previews,windows,earlier}));await page?.screenshot({path:'/tmp/conversation-navigation-failure.png'});throw error;}finally{await browser?.close();await vite?.close();}
