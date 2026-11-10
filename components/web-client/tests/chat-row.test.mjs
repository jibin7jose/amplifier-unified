import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatRow}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceRow}=await vite.ssrLoadModule('/src/workspace-explorer.jsx');
test.after(()=>vite.close());
const chat={id:'chat',title:'Review',workspaceId:'workspace',workspace:'/work/client',workspaceName:'Customer work',workspaceLabel:'client'};
const render=(row=chat,props={},showPaths=false)=>renderToStaticMarkup(React.createElement(ChatRow,{
 chat:row,model:{state:{settings:{workspaces:{showPaths}}},choose:()=>{}},now:1,...props
}));

test('chat rows and drag previews use the registered workspace name, not its folder label',()=>{
 for(const floating of [false,true]){
  const markup=render(chat,{floating});
  assert.match(markup,/data-has-workspace="true"/);
  assert.match(markup,/<small class="a-nav-chat-workspace" title="\/work\/client">Customer work<\/small>/);
 }
});
test('the full-path preference replaces the name without changing workspace membership',()=>{
 const markup=render(chat,{},true);
 assert.match(markup,/data-show-paths="true" data-has-workspace="true"/);
 assert.match(markup,/<small class="a-nav-chat-workspace" title="\/work\/client">\/work\/client<\/small>/);
});
test('standalone managed folders and absent workspace IDs never acquire workspace subtitles',()=>{
 for(const row of [
  {...chat,workspaceId:null},
  {...chat,location:{kind:'managed'},workspace:'/chats/chat/files'}
 ])assert.match(render(row),/data-has-workspace="false"/);
 assert.match(render({...chat,location:{kind:'managed'}}),/>No workspace<\/small>/);
 assert.match(render(chat,{showLocation:false}),/data-has-workspace="false"/);
});

test('pinned workspace and drag preview keep folder identity alongside each activity badge',()=>{
 const row={workspaceId:'workspace',path:'/work/client',name:'client',customName:'Customer work'};
 for(const floating of [false,true])for(const activity of [
  {},{activityCounts:{working:1}},{activityCounts:{attention:1,decision:1}},
  {activityCounts:{waiting:1}},{activityCounts:{paused:1}},{unread:1}
 ]){
  const markup=renderToStaticMarkup(React.createElement(WorkspaceRow,{
   row:{...row,...activity},state:{},now:1,pinnedShortcut:true,floating
  }));
  assert.match(markup,/a-pinned-workspace/);
  assert.match(markup,/<span class="a-workspace-identity"><svg[^>]*aria-hidden="true"/);
  assert.match(markup,/<small class="a-workspace-result-path"[^>]*><span>Workspace<\/span>/);
  if(Object.keys(activity).length)assert.match(markup,/class="a-navigation-status"[^>]+aria-label="/);
 }
 const markup=renderToStaticMarkup(React.createElement(WorkspaceRow,{
  row,state:{settings:{workspaces:{showPaths:true}}},now:1,pinnedShortcut:true
 }));
 assert.match(markup,/>Workspace · \/work\/client<\/span>/);
});