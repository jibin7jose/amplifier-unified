import {readComposerDraft} from './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{scheme:'system',navPinned:true},sessions:['a','b','c'].map(id=>({id,title:'Conversation '+id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[],draft:id==='b'?'Other draft':''})),workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'a',selectedWorkspaceId:'project',setup:{providers:[],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},attention:{items:[],sessions:{}}};
const calls=[],errors=[];let browser,vite,heldSend;
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900},colorScheme:'dark'});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{const sources=[];window.EventSource=class extends EventTarget{constructor(){super();sources.push(this)}close(){}};window.emitState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})))});
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/api/state')return route.fulfill({json:state});
  if(url.pathname==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(url.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=route.request().postDataJSON();calls.push(body);const {action,args}=body;
  if(action==='conversation.send'){heldSend={route,body};return}
  if(action==='session.rename'){await new Promise(r=>setTimeout(r,350));state.sessions.find(s=>s.id===args.id).title=args.title}
  if(action==='session.select'){state.selectedSessionId=args.id;state.view.draft=state.sessions.find(s=>s.id===args.id).draft||''}
  if(action==='view.update'){const patch={...args.patch};if('draft'in patch){state.sessions.find(s=>s.id===(args.sessionId||state.selectedSessionId)).draft=patch.draft;if(args.sessionId&&args.sessionId!==state.selectedSessionId)delete patch.draft}state.view={...state.view,...patch}}
  if(action==='attention.read')for(const item of state.attention.items)if(args.fingerprints[item.id]===item.fingerprint)item.read=true;
  state.revision++;return route.fulfill({json:{accepted:true,state}});
 });
 const emit=async()=>{state.revision++;await page.evaluate(state=>window.emitState(state),state)};
 await page.goto(vite.resolvedUrls.local[0]);await page.getByRole('button',{name:'Settings',exact:true}).waitFor();
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).colorScheme),'light dark');
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 // Conversation details are in the Conversation settings group.
 const field=page.getByLabel('Conversation name',{exact:true});
 if(!await field.isVisible()){const button=page.getByRole('button',{name:/Current conversation/});if(await button.count())await button.click()}
 await field.waitFor();await field.fill('');await field.pressSequentially('Reliable typing',{delay:15});for(let i=0;i<4;i++)await emit();
 assert.equal(await field.inputValue(),'Reliable typing');assert.equal(calls.filter(c=>c.action==='session.rename').length,0);
 await page.getByRole('button',{name:'Save name',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#session-title')?.disabled===false);
 assert.equal(await field.inputValue(),'Reliable typing');assert.equal(calls.filter(c=>c.action==='session.rename').length,1);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('[role=dialog]'));
 const menu=page.getByRole('combobox',{name:'Select conversation'});await menu.focus();const before=await menu.locator('option').allTextContents();state.headerChatNavigation={items:[...state.sessions].reverse(),total:3};await emit();
 assert.deepEqual(await menu.locator('option').allTextContents(),before);await menu.blur();await page.waitForFunction(()=>document.querySelector('select[aria-label="Select conversation"] option').value==='c');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.fill('Original message');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Sending…',{exact:true}).waitFor();
 for(let i=0;i<100&&!heldSend;i++)await new Promise(r=>setTimeout(r,10));assert.ok(heldSend);
 await menu.selectOption('b');await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId==='b');assert.equal(await readComposerDraft(composer),'Other draft');
 const {route,body}=heldSend;state.sessions.find(s=>s.id==='a').messages.push({id:'sent',role:'user',text:body.args.text,inputId:body.id});state.sessions.find(s=>s.id==='a').draft='';state.revision++;await route.fulfill({json:{accepted:true,state}});await page.waitForTimeout(100);
 assert.equal(await readComposerDraft(composer),'Other draft');assert.equal(await menu.inputValue(),'b');assert.equal(calls.filter(c=>c.action==='conversation.send').length,1);
 await menu.selectOption('a');await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId==='a');assert.equal(await readComposerDraft(composer),'','A settled send must not resurrect its pre-debounce draft on return');await menu.selectOption('b');await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId==='b');
 state.sessions.find(s=>s.id==='b').error='Fixture failure';state.attention.items=[{id:'session:b',fingerprint:'first-error',title:'Conversation needs attention',detail:'Fixture failure',sessionId:'b'}];await emit();await page.getByRole('button',{name:'Dismiss conversation error'}).click();await page.waitForFunction(()=>!document.querySelector('button[aria-label="Dismiss conversation error"]'));
 assert.equal(state.sessions.find(s=>s.id==='b').error,'Fixture failure');state.attention.items[0]={...state.attention.items[0],fingerprint:'new-error',read:false};await emit();await page.getByRole('button',{name:'Dismiss conversation error'}).waitFor();assert.deepEqual(errors,[]);
 const stalled=await browser.newPage();await stalled.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}}});let first=true;
 await stalled.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname;if(path==='/api/state'&&first){first=false;return}return route.fulfill({json:path==='/api/state'?state:path==='/api/actions'?[]:{ok:true}})});
 await stalled.goto(vite.resolvedUrls.local[0]);await stalled.getByRole('button',{name:'Retry connection'}).waitFor({timeout:13000});await stalled.getByRole('button',{name:'Retry connection'}).click();await stalled.getByRole('button',{name:'Settings',exact:true}).waitFor();
 console.log('Verified: delayed rename, stable menu, send visibility, independent selection and draft isolation, repeated errors, system theme, startup retry.');
}finally{await browser?.close();await vite?.close()}
