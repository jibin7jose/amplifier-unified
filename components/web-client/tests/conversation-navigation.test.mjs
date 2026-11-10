import test from 'node:test';
import assert from 'node:assert/strict';
import {createConversationNavigation,isNavigationAction,usesNavigationQueue} from '../src/conversation-navigation.js';
import {createSettingsActionQueue} from '../src/settings-action-queue.js';
import {createPendingView} from '../src/pending-view.js';

function state(id,revision=1){return {revision,client:{hostInstanceId:'host'},selectedSessionId:id,selectedWorkspaceId:'space',
 sessions:['a','b','c'].map(key=>({id:key,workspaceId:'space',title:key,draft:key+' draft',messages:key===id?[{id:key+'-message',text:key+' history'}]:[]})),
 view:{draft:id+' draft'},canvas:{id:id+'-canvas',open:true},canvasWorkspace:{views:[]},canvasArtifacts:[]}}

for(const field of ['navExpanded','navPinned'])test(`delayed ${field} cannot overtake later browsing, while later drawer intent still wins`,async()=>{
 const enqueue=createSettingsActionQueue(),pending=createPendingView();
 const queues={navigation:{current:Promise.resolve()},commands:{current:Promise.resolve()}};
 let server=state('a'),release;const blocked=new Promise(resolve=>release=resolve),calls=[];
 const change=(patch,wait=Promise.resolve())=>{
  const token=pending.add(patch),args={patch};
  return enqueue(usesNavigationQueue('view.update',args)?queues.navigation:queues.commands,async()=>{
   await wait;server={...server,view:{...server.view,...patch}};calls.push(patch);pending.settle(token);
  });
 };
 const opened=change({[field]:true},blocked),browse={workSurface:'chats',navExpanded:false,navPinned:false};
 const navigated=change(browse);
 assert.equal(pending.apply(server).view[field],false);
 await change({navFilter:'Beta'});assert.deepEqual(calls,[{navFilter:'Beta'}]);
 release();await Promise.all([opened,navigated]);
 assert.equal(pending.apply(server).view[field],false);
 assert.equal(server.view.workSurface,'chats');
 assert.deepEqual(calls,[{navFilter:'Beta'},{[field]:true},browse]);
 // Drawer operations use this queue only; they are not destination changes.
 assert.equal(isNavigationAction('view.update',{patch:{[field]:true}}),false);
 let releaseBrowse;const heldBrowse=new Promise(resolve=>releaseBrowse=resolve);
 const revisited=change({workSurface:'home',[field]:false},heldBrowse),reopened=change({[field]:true});
 assert.equal(pending.apply(server).view[field],true);
 releaseBrowse();await Promise.all([revisited,reopened]);
 assert.equal(pending.apply(server).view[field],true);
});

test('newer browsing paints immediately but commits after a delayed chat selection',async()=>{
 const navigation=createConversationNavigation(),pending=createPendingView(),enqueue=createSettingsActionQueue();
 const queues={navigation:{current:Promise.resolve()},commands:{current:Promise.resolve()}};
 let server=state('a'),release;const blocked=new Promise(resolve=>release=resolve),calls=[];
 const route=(action,args,execute)=>enqueue(isNavigationAction(action,args)?queues.navigation:queues.commands,execute);
 const token=navigation.begin(server,'a');
 const first=route('shell.command',{action:'session.select',args:{id:'a'}},async()=>{await blocked;server={...server,view:{...server.view,workSurface:'chat'}};calls.push('select');navigation.settle(token)});
 const patch={workSurface:'chats',workWorkspaceId:null},viewToken=pending.add(patch);
 const browse=route('view.update',{patch},async()=>{server={...server,view:{...server.view,...patch}};calls.push('browse');pending.settle(viewToken)});
 assert.equal(pending.apply(navigation.apply(server)).view.workSurface,'chats');
 await route('view.update',{patch:{navFilter:'Beta 002'}},async()=>calls.push('filter'));
 assert.deepEqual(calls,['filter']);
 release();await Promise.all([first,browse]);
 assert.deepEqual(calls,['filter','select','browse']);
 assert.equal(pending.apply(navigation.apply(server)).view.workSurface,'chats');
 for(const [action,args] of [['session.select',{id:'b'}],['session.draft',{}],['workspace.select',{id:'space'}],['view.update',{patch:{workWorkspaceId:'space'}}]])assert.equal(isNavigationAction(action,args),true);
 for(const action of ['conversation.send','providers.models','shell.view.update'])assert.equal(isNavigationAction(action,{patch:{workSurface:'chats'}}),false);
});

for(const target of ['a','b'])test('opening '+target+' from a workspace paints the chat before the server responds',()=>{
 const nav=createConversationNavigation(),workspace=state('a');
 workspace.view.workSurface='workspace';workspace.view.workWorkspaceId='space';
 const token=nav.begin(workspace,target),shown=nav.apply(workspace);
 assert.equal(shown.view.workSurface,'chat');assert.equal(shown.selectedSessionId,target);
 assert.equal(shown.navigationPending,true);assert.equal(workspace.view.workSurface,'workspace');
 nav.settle(token);assert.equal(nav.apply(workspace).view.workSurface,'workspace');
});

test('cached chat shows loading until fresh state arrives, while preserving its draft',()=>{
 const nav=createConversationNavigation(),a=state('a'),b=state('b',2);
 nav.remember(a);nav.remember(b);const token=nav.begin(b,'a'),shown=nav.apply(b);
 assert.equal(shown.selectedSessionId,'a');assert.equal(b.selectedSessionId,'b');
 assert.deepEqual(shown.sessions.find(row=>row.id==='a').messages,[]);
 assert.equal(shown.sessions.find(row=>row.id==='a').historyLoading,true);
 assert.equal(shown.view.draft,'a draft');assert.equal(shown.canvas.open,false);
 const fresh=nav.apply(state('a',3));assert.equal(fresh.sessions.find(row=>row.id==='a').messages[0].text,'a history');
 nav.settle(token);assert.equal(nav.apply(b),b);
});

test('late responses and a failed older selection cannot replace newer intent',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);
 const first=nav.begin(a,'b');const second=nav.begin(nav.apply(a),'c');
 nav.settle(first);nav.remember(state('b',2));
 assert.equal(nav.apply(state('b',2)).selectedSessionId,'c');
 nav.settle(second);assert.equal(nav.apply(state('b',2)).selectedSessionId,'b');
});

test('uncached chat shows its own loading state and does not leak the previous canvas',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);nav.begin(a,'b');
 const shown=nav.apply(a),b=shown.sessions.find(row=>row.id==='b');
 assert.equal(b.historyLoading,true);assert.deepEqual(b.messages,[]);assert.equal(shown.canvas.open,false);
 assert.equal(shown.view.draft,'b draft');
});

test('dirty renderers stay mounted until authoritative navigation completes',()=>{
 const nav=createConversationNavigation(),a=state('a');a.canvasWorkspace.views=[{dirty:true}];
 assert.equal(nav.begin(a,'b'),null);assert.equal(nav.apply(a),a);
});

test('host restart clears cached details and pending choices',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);nav.begin(a,'b');
 const restarted={...state('c'),client:{hostInstanceId:'new-host'}};nav.remember(restarted);
 assert.equal(nav.apply(restarted),restarted);nav.begin(restarted,'a');
 assert.equal(nav.apply(restarted).sessions.find(row=>row.id==='a').historyLoading,true);
});

test('display cache is bounded independently of the saved library',()=>{
 const nav=createConversationNavigation(1);nav.remember(state('a'));nav.remember(state('b'));
 nav.begin(state('b'),'a');assert.equal(nav.apply(state('b')).sessions.find(row=>row.id==='a').historyLoading,true);
});

test('a retained hidden dirty viewer blocks speculative navigation too',()=>{
 const navigation=createConversationNavigation(),state={selectedSessionId:'one',sessions:[{id:'one'},{id:'two'}],canvas:{open:false},canvasWorkspace:{views:[{viewId:'primary',dirty:true}]},view:{}};
 assert.equal(navigation.begin(state,'two'),null);
 assert.equal(navigation.apply(state),state);
});

test('a background chat also hides inline execution and transcript details until selected',()=>{
 const nav=createConversationNavigation(),a=state('a');
 Object.assign(a.sessions[1],{messages:[{id:'stale'}],execution:{nodes:[{id:'old'}]},runtimeEvents:[{type:'tool.pre'}],streaming:'Stale partial response'});
 nav.begin(a,'b');const shown=nav.apply(a).sessions.find(row=>row.id==='b');
 assert.deepEqual(shown.messages,[]);assert.equal(shown.execution,undefined);assert.equal(shown.runtimeEvents,undefined);assert.equal(shown.streaming,undefined);
});

test('a pending new chat fences composition without inventing a destination or changing retained drafts',()=>{
 const nav=createConversationNavigation(),before=state('a');nav.remember(before);
 before.canvasWorkspace.views=[{dirty:true}];
 const token=nav.beginDraft(),shown=nav.apply(before);
 assert.equal(shown.navigationPending,true);assert.equal(shown.draftNavigationPending,true);
 assert.equal(shown.selectedSessionId,'a');assert.equal(shown.view,before.view);
 assert.equal(shown.sessions,before.sessions);assert.equal(shown.canvasWorkspace,before.canvasWorkspace);
 const confirmed={...before,selectedSessionId:null,view:{draft:'retained unsent draft',newSessionDraft:{bundle:'work'}}};
 assert.equal(nav.apply(confirmed).draftNavigationPending,true,'receipt settlement owns unlocking, not an intermediate frame');
 nav.settle(token);assert.equal(nav.apply(confirmed),confirmed);
});

test('failed draft navigation unlocks the original chat and cannot unlock a newer selection',()=>{
 const nav=createConversationNavigation(),before=state('a');nav.remember(before);
 const failed=nav.beginDraft();nav.settle(failed);assert.equal(nav.apply(before),before);
 const draft=nav.beginDraft(),selected=nav.begin(nav.apply(before),'b');
 nav.settle(draft);const shown=nav.apply(before);
 assert.equal(shown.selectedSessionId,'b');assert.equal(shown.navigationPending,true);
 assert.equal(shown.draftNavigationPending,undefined);
 nav.settle(selected);assert.equal(nav.apply(before),before);
});

test('a host restart clears the pending draft fence',()=>{
 const nav=createConversationNavigation(),before=state('a');nav.remember(before);nav.beginDraft();
 const restarted={...before,client:{hostInstanceId:'new-host'}};nav.remember(restarted);
 assert.equal(nav.apply(restarted),restarted);
});
