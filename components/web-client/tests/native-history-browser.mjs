import {openSettingsPage} from './browser-settings.mjs';
// The real frontend against synthetic API state. No runtime, model, or user files.
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {shellFor} from './shell-host.mjs';

const message=i=>({id:`native-${i}`,role:i%2?'assistant':'user',text:`History ${i}\n\n${'A useful saved paragraph. '.repeat(30)}`,createdAt:i+1});
const session={id:'native-chat',sessionKind:'root',title:'Native project chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,sharedHistoryOffset:20,sharedHistoryUserTurnOffset:10,messages:Array.from({length:20},(_,i)=>message(i+20))};
const child={id:'child-chat',sessionKind:'worker',parentId:'native-chat',nativeParentId:'native-chat',title:'Saved worker',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:false,messages:[],historyReadOnlyReason:'This is a saved worker conversation. Open its parent chat to continue.'};
let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{navPinned:true},sessions:[session,child],workspaces:[{id:'project',name:'Fixture project',path:'/fixture',available:true}],selectedSessionId:session.id,selectedWorkspaceId:'project',setup:{providers:[],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
state.sessions.push(...Array.from({length:4998},(_,i)=>({...child,sessionKind:'root',parentId:null,nativeParentId:null,id:'summary-'+i,title:'Indexed conversation '+i,historyLoaded:false,historyReadOnlyReason:null})));
// A previously saved custom skin must not restore truncation of the full path.
state.theme={name:'Saved custom skin',css:'#amp-one .a-nav-workspace-path>span{white-space:nowrap;text-overflow:ellipsis;overflow:hidden}'};
state.workspaceExplorer={path:'/',parentPath:null,breadcrumbs:[{name:'/',path:'/'}],filter:'',page:1,pages:1,totalWorkspaces:1,rows:[{path:'/fixture',name:'fixture',workspaceId:'project',chatCount:4999,canBrowse:false,unread:0}]};
const calls=[],errors=[];
let browser,vite;
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  const sources=[];
  window.EventSource=class extends EventTarget{constructor(){super();sources.push(this);setTimeout(()=>this.onopen?.(),0)}close(){}};
  window.emitFixtureState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})));
 });
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/shell')return route.fulfill({json:shellFor(state,()=>{}).data});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   let {action,args}=route.request().postDataJSON();
   if(action==='shell.command')({action,args}=args);
   if(action==='shell.view.update')action='view.update';
   // Render receipts do not mutate the fixture, matching the production host.
   if(action==='shell.report')return route.fulfill({json:{accepted:true}});
   calls.push({action,args});
   if(action==='view.update')state.view={...state.view,...args.patch};
   if(action==='workspace.select')state.selectedWorkspaceId=args.id;
   if(action==='session.history'){
    assert.deepEqual(args,{id:session.id,before:20,limit:100});
    session.messages=[...Array.from({length:20},(_,i)=>message(i)),...session.messages];session.sharedHistoryOffset=0;session.sharedHistoryUserTurnOffset=0;
   }
   if(action==='session.select'){state.selectedSessionId=args.id;if(args.id===child.id)child.historyLoading=true}
   state.revision++;
   return route.fulfill({json:{accepted:true,state}});
  }
  return route.fulfill({json:{ok:true}});
 });
 const started=performance.now();
 await page.goto(vite.resolvedUrls.local[0]);await page.locator('.a-workspace-row').waitFor();
 assert.equal(await page.locator('.a-workspace-row').count(),1);
 await page.locator('.a-workspace-row').getByRole('button',{name:/Details and actions/}).click();
 await page.locator('.a-navigation-flyout').getByText('/fixture',{exact:true}).waitFor();
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('.a-nav-chat').count(),40);
 assert.equal(await page.locator('.a-nav-chat-select').filter({hasText:'Saved worker'}).count(),0);
 assert.equal(await page.locator('.a-session-select option[value="child-chat"]').count(),0);
 assert.ok(await page.locator('.a-session-select option').count()<=101);
 const domNodes=await page.locator('*').count();assert.ok(domNodes<5000,`${domNodes} DOM nodes for summary-only chats`);
 const input=page.getByRole('textbox',{name:'Message Amplifier'}),draftStarted=performance.now();
 await input.fill('A draft with five thousand indexed chats');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='A draft with five thousand indexed chats');
 const draftUpdateMs=Math.round(performance.now()-draftStarted);
 assert.ok(draftUpdateMs<3000,`Draft update took ${draftUpdateMs}ms with 5000 summaries`);
 await input.fill('');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='');
 await page.getByRole('button',{name:'Show more conversations',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().view.navRecentView?.navChatPage?.index===1);
 assert.equal(await page.locator('.a-nav-chat').count(),40);
 await page.getByRole('searchbox',{name:'Filter conversations'}).fill('Indexed conversation 4997');
 await page.waitForFunction(()=>document.querySelectorAll('.a-nav-chat').length===1);
 assert.match(await page.locator('.a-nav-chat-select').innerText(),/4997/);
 await page.getByRole('searchbox',{name:'Filter conversations'}).fill('');
 await page.waitForFunction(()=>document.querySelectorAll('.a-nav-chat').length===40);
 console.log(JSON.stringify({summaryCount:5000,domNodes,draftUpdateMs,checksElapsedMs:Math.round(performance.now()-started)}));
 const pane=page.locator('.a-messages');
 await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 assert.equal(calls.some(call=>['runtime.control','providers.list'].includes(call.action)),false,'reading native history must not mount a runtime or inspect providers');
 const before=await pane.evaluate(element=>{element.scrollTop=0;return element.querySelector('[data-message-id="native-20"]').getBoundingClientRect().y});
 await page.locator('[data-message-id="native-0"]').waitFor();
 await page.waitForTimeout(100);
 const after=await page.locator('[data-message-id="native-20"]').boundingBox();
 assert.ok(Math.abs(after.y-before)<3,`loading older messages moved reading position by ${after.y-before}px`);
 assert.equal(calls.filter(call=>call.action==='session.history').length,1,'scrolling upward requests one page without clicking');
 assert.equal(await page.getByRole('button',{name:'Load earlier conversation',exact:true}).count(),0);
 await page.getByRole('button',{name:'Refresh workspaces and chats',exact:true}).click();
 assert.ok(calls.some(call=>call.action==='history.refresh'));
 await page.getByRole('button',{name:'Chat details',exact:true}).click();
 await page.getByRole('button',{name:'Subagent history (1)',exact:true}).click();
 await page.getByRole('heading',{name:'Subagent history',exact:true}).waitFor();
 await page.getByRole('button',{name:'Saved worker',exact:true}).click();
 await page.getByText('Loading conversation…',{exact:true}).waitFor();
 assert.equal(await page.getByRole('heading',{name:'What shall we work on?'}).count(),0);
 child.historyLoading=false;child.historyLoaded=true;child.messages=[message(1)];state.revision++;
 await page.evaluate(state=>window.emitFixtureState(state),state);
 await page.getByText(child.historyReadOnlyReason,{exact:true}).waitFor();
 assert.equal(await page.getByRole('textbox',{name:'Message Amplifier'}).isDisabled(),true);
 assert.equal(await page.getByRole('button',{name:'Model and reasoning settings'}).isDisabled(),true);
 assert.equal(await page.getByRole('button',{name:'Start voice call'}).isDisabled(),true);
 assert.equal(calls.some(call=>call.action==='runtime.control'),false);
 assert.equal(await page.locator('.a-session-select option[value="child-chat"]').count(),0);
 await page.getByText('Subagent conversation',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Open parent chat',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId==='native-chat');
 session.historyActivity={diagnostics:[{code:'scan_limit',source:'events'},{code:'recovered_backup',source:'transcript'}]};state.revision++;
 await page.evaluate(state=>window.emitFixtureState(state),state);
 await page.getByText('Showing a recovered history backup. Original files are unchanged.',{exact:true}).waitFor();
 await page.getByText('Some saved activity is unavailable or outside the loaded window. Conversation text comes from the saved transcript.',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Settings',exact:true}).click();

 assert.equal(await page.getByRole('button',{name:'Same-chat CLI and web',exact:true}).count(),0);
 await openSettingsPage(page,'history');
 await page.getByText(/Your CLI projects and conversations appear automatically/).waitFor();
 assert.deepEqual(errors,[]);
 console.log('Native history browser checks passed: no automatic runtime mount, stable scroll paging, refresh, loading and read-only states, automatic sharing settings.');
}finally{await browser?.close();await vite?.close()}
