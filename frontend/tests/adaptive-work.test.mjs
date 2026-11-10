import test from 'node:test';
import assert from 'node:assert/strict';
import {startingWorkView,homePatch,readWorkspaceChatChoices,saveWorkspaceChatChoices,workspaceChatQuery,currentWorkspaceChatQuery,currentWorkPage,currentWorkContext,focusPosition,stepFocus,readFocusCursor,saveFocusCursor} from '../src/adaptive-work.js';
test('appearance chooses arrival only; explicit starting preference wins',()=>{
 assert.equal(startingWorkView({interfaceDetail:'minimal'}),'focus');
 assert.equal(startingWorkView({interfaceDetail:'standard'}),'brief');
 assert.equal(startingWorkView({interfaceDetail:'detailed'}),'studio');
 assert.equal(startingWorkView({interfaceDetail:'minimal',workStartView:'studio'}),'studio');
 const patch=homePatch({interfaceDetail:'detailed'},{workHomeFilter:'working'});
 assert.equal(patch.navPinned,true);assert.equal(patch.workHomeView,'studio');
 for(const key of ['selectedSessionId','draft','canvas','workHomeScope','workStartView'])assert.equal(Object.hasOwn(patch,key),false);
});
test('late pages cannot answer a new project, search or page',()=>{
 const data={scope:'a',requestedScope:'a',query:'launch',requestedPages:{idle:1}};
 assert.equal(currentWorkPage(data,{workHomeScope:'a',workHomeQuery:'launch',workHomePages:{idle:1}}),true);
 for(const changes of [{workHomeScope:'b'},{workHomeQuery:'offsite'},{workHomePages:{idle:2}},{navAge:'24h'}])
  assert.equal(currentWorkPage(data,{workHomeScope:'a',workHomeQuery:'launch',workHomePages:{idle:1},...changes}),false);
});

test('A to B to A restores bounded workspace choices and cursor without sharing request keys',()=>{
 const values=new Map(),browser={sessionStorage:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}};
 const choices={workHomeQuery:'workspace',workHomeFilter:'idle',workHomePages:{idle:1}};
 saveWorkspaceChatChoices(browser,'one',choices);
 const view={workHomeScope:'one',...readWorkspaceChatChoices(browser,'one')};
 const cursor={key:JSON.stringify(['one','one','workspace']),kind:'idle',page:1,index:2,id:'a-52'};
 saveFocusCursor(browser,cursor,'focus:one');
 saveWorkspaceChatChoices(browser,'two',{workHomeQuery:'other',workHomeFilter:'unread',workHomePages:{unread:2}});
 saveFocusCursor(browser,{...cursor,id:'b-102'},'focus:two');
 assert.deepEqual(readWorkspaceChatChoices(browser,'one'),choices);
 assert.deepEqual(readFocusCursor(browser,'focus:one'),cursor);
 const request=workspaceChatQuery(view,'one');
 assert.deepEqual(request,{workspaceId:'one',query:'workspace',pages:{idle:1}});
 assert.equal(currentWorkspaceChatQuery(request,view,'one'),true);
 assert.equal(currentWorkspaceChatQuery({...request,workspaceId:'two'},view,'one'),false);
 assert.equal(currentWorkspaceChatQuery({...request,query:'other'},view,'one'),false);
 assert.equal(currentWorkspaceChatQuery({...request,pages:{idle:2}},view,'one'),false);
 assert.equal(currentWorkspaceChatQuery({pages:{idle:1},query:'workspace',workspaceId:'one'},view,'one'),true,'Wire property order is not request identity');
 assert.deepEqual(Object.keys(request),['workspaceId','query','pages'],'No saved map, mode, filter or storage keys leave the browser');
 const page={scope:'one',requestedScope:'one',query:'workspace',requestedPages:{idle:1}};
 assert.equal(currentWorkPage(page,view,'one'),true);
 assert.equal(currentWorkPage(page,{...view,workHomeScope:'two'},'two'),false);
 assert.equal(currentWorkPage(page,{...view,workHomeQuery:'changed'},'one'),false);
 assert.equal(currentWorkPage(page,{...view,workHomePages:{idle:2}},'one'),false);
    const finite={...view,navAge:'24h'};
    assert.equal(currentWorkPage({...page,age:'24h'},finite,'one'),true);
    assert.equal(currentWorkPage({...page,age:'all'},finite,'one'),false);
    assert.deepEqual(workspaceChatQuery(finite,'one'),request,'Shared age never enters workspace-local requests');
 saveWorkspaceChatChoices(browser,'one',{...choices,workHomeFilter:'all'});
 assert.equal(readWorkspaceChatChoices(browser,'one').workHomeQuery,'workspace','Clearing status retains workspace search');
 const context={scope:'one',requestedScope:'one',workspace:{id:'one',path:'/one'}};
 assert.equal(currentWorkContext(context,view,'one'),context);
 assert.equal(currentWorkContext(context,{...view,workHomeScope:'two'},'two'),null);
 const blocked={get sessionStorage(){throw Error('Unavailable')}};
 assert.deepEqual(readWorkspaceChatChoices(blocked,'one'),{workHomeFilter:'all',workHomeQuery:'',workHomePages:{}});
 assert.doesNotThrow(()=>saveWorkspaceChatChoices(blocked,'one',choices));
 saveWorkspaceChatChoices(browser,'bad',{workHomeQuery:'x'.repeat(501),workHomeFilter:'wrong',workHomePages:{idle:-1,unread:1_000_001,attention:true,paused:4,extra:1},extra:'never stored'});
 assert.deepEqual(readWorkspaceChatChoices(browser,'bad'),{workHomeFilter:'all',workHomeQuery:'x'.repeat(500),workHomePages:{paused:4}});
});

test('pinned Home accepts its effective project while fencing request and pin changes',()=>{
 const data={scope:'two',requestedScope:'all',query:'',requestedPages:{}};
 assert.equal(currentWorkPage(data,{},'two'),true);
 assert.equal(currentWorkPage(data,{},'one'),false);
 assert.equal(currentWorkPage(data,{}),false);
 assert.equal(currentWorkPage(data,{workHomeScope:'managed'},'two'),false);
 assert.equal(currentWorkPage({...data,requestedScope:'managed'},{workHomeScope:'managed'},'two'),true);
 assert.equal(currentWorkPage({...data,scope:'missing',unavailable:true},{},'missing'),true);
});

test('project context survives result refresh while fencing scope, pin and availability changes',()=>{
 const context={scope:'one',requestedScope:'one',workspace:{id:'one',path:'/one'},unavailable:false};
 assert.equal(currentWorkContext(context,{workHomeScope:'one',workHomeQuery:'new',workHomePages:{idle:2}}),context);
 assert.equal(currentWorkContext(context,{workHomeScope:'two'}),null);
 assert.equal(currentWorkContext(context,{workHomeScope:'one'},'two'),null);
 const pinned={...context,requestedScope:'all'};
 assert.equal(currentWorkContext(pinned,{},'one'),pinned);
 const unavailable={...pinned,workspace:null,unavailable:true};
 assert.equal(currentWorkContext(unavailable,{},'one'),unavailable);
 assert.equal(currentWorkContext(undefined,{}),null);
});

test('Home preserves an explicitly opened or closed sidebar independently of appearance',()=>{
 const pinned=homePatch({interfaceDetail:'minimal'},{},{navPinned:true,navExpanded:false});
 assert.equal(pinned.navPinned,true);
 assert.equal(pinned.workHomeView,'focus');
 const hidden=homePatch({interfaceDetail:'detailed'},{},{navPinned:false,navExpanded:false});
 assert.equal(hidden.navPinned,false);
 assert.equal(hidden.workHomeView,'studio');
 const drawer=homePatch({interfaceDetail:'standard'},{navExpanded:false},{navExpanded:true,navPinned:false});
 assert.equal(drawer.navExpanded,false);
});

test('returning Home retains choices across chat/project visits and appearance changes',()=>{
 const choices={workHomeView:'studio',workHomeFilter:'idle',workHomeScope:'project-a',workHomeQuery:'launch',workHomePages:{idle:2},navPinned:true};
 for(const workSurface of ['chat','workspace','workspaces','chats']){
  const away={...choices,workSurface,workWorkspaceId:'project-b',selectedSessionId:'other'};
  const back={...away,...homePatch({interfaceDetail:'minimal',workStartView:'focus'},{},away)};
  for(const [key,value] of Object.entries(choices))assert.deepEqual(back[key],value,key);
  assert.equal(back.workSurface,'home');assert.equal(back.selectedSessionId,'other');
 }
 const reset={...choices,...homePatch({interfaceDetail:'minimal'},{workHomeView:'focus',workHomeFilter:'all'},choices)};
 assert.equal(reset.workHomeView,'focus');assert.equal(reset.workHomeFilter,'all');
 assert.equal(reset.workHomeScope,'project-a');assert.equal(reset.workHomeQuery,'launch');
});

test('Focus follows identity through live reorder and advances safely when an item leaves',()=>{
 const page={index:0,start:0,total:3,pages:1,items:[{id:'a'},{id:'b'},{id:'c'}]};
 const cursor=focusPosition(page,null);
 assert.deepEqual(cursor,{page:0,index:0,id:'a'});
 const second=stepFocus(page,cursor,1);
 assert.equal(second.id,'b');
 assert.equal(focusPosition({...page,items:[{id:'b'},{id:'a'},{id:'c'}]},second).index,0);
 assert.equal(focusPosition({...page,items:[{id:'a'},{id:'c'}]},second).id,'c');
 assert.equal(focusPosition({...page,items:[{id:'a'}]},second).id,'a');
 assert.equal(focusPosition({...page,items:[]},second),null);
 assert.equal(stepFocus(page,cursor,-1),null);
 assert.equal(stepFocus(page,focusPosition(page,{page:0,index:2,id:'c'}),1),null);
 assert.deepEqual(page.items,[{id:'a'},{id:'b'},{id:'c'}]);
});

test('Focus crosses bounded pages in both directions without assuming a page size',()=>{
 const page={index:0,start:0,total:5,pages:2,items:[{id:'a'},{id:'b'}]};
 const next=stepFocus(page,{page:0,index:1,id:'b'},1);
 assert.deepEqual(next,{page:1,index:0,id:null});
 const following={index:1,start:2,total:5,pages:2,items:[{id:'c'},{id:'d'},{id:'e'}]};
 assert.equal(focusPosition(following,next).id,'c');
 const back=stepFocus(following,focusPosition(following,next),-1);
 assert.deepEqual(back,{page:0,index:-1,id:null});
 assert.equal(focusPosition(page,back).id,'b');
 assert.equal(focusPosition(page,{page:8,index:4,id:'old'}).id,'a','A clamped page starts safely');
});

test('Focus retains only a tab-local cursor and tolerates unavailable or invalid storage',()=>{
 const values=new Map(),browser={sessionStorage:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}};
 const cursor={key:'scope/query',kind:'attention',page:2,index:0,id:'chat-a'};
 saveFocusCursor(browser,cursor);assert.deepEqual(readFocusCursor(browser),cursor);
 const workspaceKey='amplifier.work-focus.v1:workspace:one:chats',workspaceCursor={...cursor,id:'workspace-chat'};
 saveFocusCursor(browser,workspaceCursor,workspaceKey);
 assert.deepEqual(readFocusCursor(browser,workspaceKey),workspaceCursor);
 assert.deepEqual(readFocusCursor(browser),cursor,'Workspace browsing cannot overwrite the Home pass');
 saveFocusCursor(browser,{...cursor,index:'wrong'});assert.equal(readFocusCursor(browser),null);
 const blocked={get sessionStorage(){throw Error('Storage unavailable')}};
 assert.equal(readFocusCursor(blocked),null);assert.doesNotThrow(()=>saveFocusCursor(blocked,cursor));
});
