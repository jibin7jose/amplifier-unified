import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {chatPage} from '../src/chat-navigation.js';
const vite=await createServer({cacheDir:'.vite-navigation-query-failure',server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatList,ChatSearch,ChatDetails}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
const {NavigationResourceStatus}=await vite.ssrLoadModule('/src/shell/navigation-resource-status.jsx');
test.after(()=>vite.close());
const fixture=(section,status)=>({library:{bounded:true},view:{navChatScope:'all'},selectedSessionId:'selected',
 navigationResourcePending:status?{[section==='search'?'sidebarSearch':'sidebarWorkspace']:{status}}:{}});
const render=(section,state)=>{
 const page=chatPage(state,undefined,{section}),model={state,act:()=>{},choose:()=>{}};
 return renderToStaticMarkup(React.createElement(React.Fragment,null,
  React.createElement(NavigationResourceStatus,{state,resource:section==='search'?'sidebarSearch':'sidebarWorkspace',label:section==='search'?'chat search':'workspace chats',refresh:()=>{}}),
  React.createElement(section==='search'?ChatSearch:ChatList,{page,model,view:state.view,viewAct:()=>{},now:0})));
};
for(const section of ['search','workspace']){
 test(`${section}: cold failure offers Retry without claiming loading or empty results`,()=>{
  const state=fixture(section,'retry'),page=chatPage(state,undefined,{section}),html=render(section,state);
  assert.equal(page.pending,false);assert.equal(page.unavailable,true);
  assert.match(html,/Could not update/);assert.match(html,/Retry/);
  assert.doesNotMatch(html,/Loading conversations|Searching…|No chats found|Your conversations will appear|0 chats/);
  assert.match(render(section,fixture(section,'pending')),/Loading|Searching/);
  const settled=fixture(section);settled.sidebarNavigation={[section]:{...chatPage(settled,undefined,{section}),pending:false}};
  assert.match(render(section,settled),section==='search'?/No chats found/:/Your conversations will appear/);
 });
 test(`${section}: failed refresh retains matching rows and does not reuse a different scope`,()=>{
  const state=fixture(section,'retry'),page={...chatPage(state,undefined,{section}),pending:false,unavailable:false,total:1,end:1,items:[{id:'retained',title:'Retained canonical chat'}]};
  state.sidebarNavigation={[section]:page};
  assert.equal(chatPage(state,undefined,{section}),page);
  assert.match(render(section,state),/Retained canonical chat/);
  state.view={...state.view,navFilter:'new query'};
  assert.equal(chatPage(state,undefined,{section}).unavailable,true);
  assert.doesNotMatch(render(section,state),/Retained canonical chat/);
 });
}
test('a different resource failure cannot mark the current query unavailable',()=>{
 const state=fixture('workspace','retry');
 assert.equal(chatPage(state,undefined,{section:'search'}).pending,true);
 assert.equal(chatPage(state,undefined,{section:'search'}).unavailable,undefined);
});
test('retained rows never offer the inverse read action while fresh receipts are unavailable',()=>{
 for(const kind of ['unread','idle']){
  const state={library:{bounded:true},attention:{sessions:{},items:[]}};
  const html=renderToStaticMarkup(React.createElement(ChatDetails,{
   chat:{id:'retained',title:'Retained chat',activity:{kind,label:kind==='unread'?'New response':'Idle'}},
   model:{state,draft:{},act:()=>{},choose:()=>{},setDraft:()=>{}},now:0,close:()=>{}
  }));
  const action=kind==='unread'?'attention.read':'session.markUnread';
  assert.match(html,new RegExp(`disabled="" data-action="${action}"[^>]*>Mark ${kind==='unread'?'read':'unread'}</button>`));
  if(kind==='unread')assert.doesNotMatch(html,/>Mark unread</);
 }
});
