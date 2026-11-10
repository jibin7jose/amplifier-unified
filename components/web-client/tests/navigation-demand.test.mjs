import test from 'node:test';
import assert from 'node:assert/strict';
import {navigationResources,hasNavigationResource} from '../src/shell/navigation-demand.js';

import {shellScope} from '../src/shell/resources.js';

const sidebar=['pinnedNavigation','recentNavigation','workspaceShortcuts'];
test('Canvas demand tolerates bootstrap without state and follows only visible overview',()=>{
 for(const state of [null,undefined])assert.deepEqual(navigationResources(state,false),[]);
 const state={canvas:{open:true},view:{canvasDraft:{library:true}}};
 assert.deepEqual(navigationResources(state,false),['recentNavigation','workspaceShortcuts']);
 assert.deepEqual(navigationResources({...state,canvas:{open:false}},false),[]);
 assert.deepEqual(navigationResources({...state,view:{settingsCanvas:true}},false),[]);
});
test('Status requests only open groups, independently of Home filters; hidden navigation requests no rows',()=>{
 const view={navMode:'status',navStatusCollapsed:['idle','working','waiting','paused'],workHomeScope:'project',workHomeQuery:'private search'};
 assert.deepEqual(navigationResources({view}),['workStatusAttention','workStatusUnread']);
 assert.deepEqual(navigationResources({view},true,false),sidebar,'Replacing standard Chats restores Browse demand for remaining navigation');
 assert.deepEqual(navigationResources({view},false),[]);
 assert.deepEqual(navigationResources({view:{...view,workSurface:'home'}},false),['workHome','workspaceShortcuts']);
 assert.deepEqual(navigationResources({view:{...view,workSurface:'home'}}),['workHome','workStatusAttention','workStatusUnread','workspaceShortcuts']);
 assert.deepEqual(navigationResources({view:{workSurface:'chats'}},false),['sidebarSearch']);
 assert.deepEqual(navigationResources({view:{...view,navStatusCollapsed:['idle','working']}}),
  ['workStatusAttention','workStatusPaused','workStatusUnread','workStatusWaiting']);
});
test('built-in demand includes only the sidebar and visible browsing page',()=>{
 for(const view of [{},{workSurface:'chat'},{workSurface:'workspace',workWorkspaceTab:'work'},{workSurface:'workspace',workWorkspaceTab:'files'},{workSurface:'workspace',workWorkspaceTab:'details'}])
  assert.deepEqual(navigationResources({view}),sidebar);
 for(const [view,resource] of [[{workSurface:'chats'},'sidebarSearch'],[{workSurface:'workspace'},'workHome'],[{workSurface:'workspaces'},'workspaceExplorer']])
  assert.deepEqual(navigationResources({view}),[...sidebar,resource].sort());
 assert.deepEqual(navigationResources({view:{workSurface:'workspace'}},false),['workHome']);
});
test('demand scope changes for workspace/tab but not unrelated drafts or progress',()=>{
 const state={view:{workSurface:'workspace',workWorkspaceId:'one'}};
 for(const patch of [{workWorkspaceId:'two'},{workWorkspaceTab:'files'},{workWorkspaceTab:'details'}])
  assert.notDeepEqual(shellScope(state),shellScope({...state,view:{...state.view,...patch}}));
 assert.deepEqual(shellScope(state),shellScope({...state,revision:99,view:{...state.view,draft:'private'}}));
 assert.deepEqual(shellScope({view:{}}),shellScope({view:{workWorkspaceTab:'files'}}));
});
test('built-in resources require explicit coverage from the bundled server',()=>{
 assert.equal(hasNavigationResource({navigationResources:sidebar},'sidebarSearch'),false);
 assert.equal(hasNavigationResource({navigationResources:[]},'workspaceExplorer'),false);
 assert.equal(hasNavigationResource({navigationResources:[...sidebar,'workspaceExplorer']},'workspaceExplorer'),true);
 for(const snapshot of [undefined,{}, {navigationResources:null},{navigationResources:'workspaceExplorer'}])
  assert.equal(hasNavigationResource(snapshot,'workspaceExplorer'),false);
});
