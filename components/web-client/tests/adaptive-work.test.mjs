import test from 'node:test';
import assert from 'node:assert/strict';
import {startingWorkView,homePatch,currentWorkPage,currentWorkContext,focusPosition,stepFocus,readFocusCursor,saveFocusCursor} from '../src/adaptive-work.js';
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
 for(const changes of [{workHomeScope:'b'},{workHomeQuery:'offsite'},{workHomePages:{idle:2}}])
  assert.equal(currentWorkPage(data,{workHomeScope:'a',workHomeQuery:'launch',workHomePages:{idle:1},...changes}),false);
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
 saveFocusCursor(browser,{...cursor,index:'wrong'});assert.equal(readFocusCursor(browser),null);
 const blocked={get sessionStorage(){throw Error('Storage unavailable')}};
 assert.equal(readFocusCursor(blocked),null);assert.doesNotThrow(()=>saveFocusCursor(blocked,cursor));
});
