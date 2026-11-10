import test from 'node:test';
import assert from 'node:assert/strict';
import {navigationSnapshot} from '../src/shell/navigation-snapshot.js';
import {createConversationNavigation} from '../src/conversation-navigation.js';

test('late sidebar snapshots cannot override pending or confirmed selection',()=>{
 const old={selectedSessionId:'old',selectedWorkspaceId:'work',chatNavigation:{items:[]}};
 const pending={selectedSessionId:'new',selectedWorkspaceId:'other',navigationPending:true};
 const shown=navigationSnapshot(old,pending);
 assert.equal(shown.selectedSessionId,'new');assert.equal(shown.navigationPending,true);
 assert.equal(shown.chatNavigation,old.chatNavigation);
 assert.equal(navigationSnapshot(old,{...pending,navigationPending:false}).selectedSessionId,'new');
 assert.equal(navigationSnapshot(shown,pending),shown);
 const failed=navigationSnapshot(shown,{selectedSessionId:'old',selectedWorkspaceId:'work'});
 assert.equal(failed.selectedSessionId,'old');assert.equal(failed.navigationPending,false);
});

test('a chat visible only in a sidebar page can enter immediate loading',()=>{
 const navigation=createConversationNavigation();
 const state={selectedSessionId:'old',sessions:[{id:'old',messages:[{text:'Old history'}]}],view:{},client:{hostInstanceId:'host'}};
 const token=navigation.begin(state,'new',{id:'new',title:'Another page',workspaceId:'work'});
 assert.ok(token);
 const shown=navigation.apply(state);
 assert.equal(shown.selectedSessionId,'new');
 assert.deepEqual(shown.sessions.find(row=>row.id==='new').messages,[]);
 assert.equal(shown.sessions.find(row=>row.id==='new').historyLoading,true);
});

test('workspace browsing keeps its own scope while the active chat stays selected',()=>{
 const state={selectedSessionId:'chat-a',selectedWorkspaceId:'a',view:{workSurface:'workspace',workWorkspaceId:'b'}};
 const response={selectedSessionId:'chat-a',selectedWorkspaceId:'b',workspaces:[{id:'b'}],view:{}};
 const shown=navigationSnapshot(response,state);
 assert.equal(shown.selectedWorkspaceId,'b');assert.equal(shown.selectedSessionId,'chat-a');
 assert.equal(shown.view.workWorkspaceId,'b');assert.equal(shown.navigationWorkspacePending,false);
 assert.equal(navigationSnapshot(shown,state),shown);
 const stale=navigationSnapshot({...response,selectedWorkspaceId:'a'},state);
 assert.equal(stale.selectedWorkspaceId,'b');assert.equal(stale.navigationWorkspacePending,true);
 assert.equal(navigationSnapshot(stale,state).navigationWorkspacePending,true);
 const returned=navigationSnapshot(shown,{...state,view:{workSurface:'chat'}});
 assert.equal(returned.selectedWorkspaceId,'a');assert.equal(returned.view.workWorkspaceId,null);
 assert.equal(returned.navigationWorkspacePending,true);
});

test('an explicitly pinned navigation component retains its workspace scope',()=>{
 const shown=navigationSnapshot({selectedWorkspaceId:'pinned',view:{}},{selectedWorkspaceId:'a',selectedSessionId:'chat-a',view:{workSurface:'workspace',workWorkspaceId:'b'}},{mode:'pinned',workspaceId:'pinned'});
 assert.equal(shown.selectedWorkspaceId,'pinned');assert.equal(shown.navigationWorkspacePending,false);
 assert.equal(shown.view.workWorkspaceId,null);
});
