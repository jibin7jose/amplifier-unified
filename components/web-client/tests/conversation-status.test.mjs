import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConversationError}=await server.ssrLoadModule('/src/conversation-controls.jsx');
const {ChatOverview}=await server.ssrLoadModule('/src/work-overview.jsx');
test.after(()=>server.close());

test('failure banner identifies the recorded incident and respects acknowledgement',()=>{
 const props={state:{},act:()=>{},session:{id:'chat',error:'Runtime failed',failure:{summary:'The manager turn failed.',recordedAt:1790517218.5,generationId:'failed-turn'}}};
 const html=renderToStaticMarkup(React.createElement(ConversationError,props));
 assert.match(html,/A turn stopped/);assert.match(html,/2026-09-27T13:53:38.500Z/);assert.match(html,/View error details/);
 assert.doesNotMatch(html,/Conversation stopped/);
 assert.equal(renderToStaticMarkup(React.createElement(ConversationError,{...props,state:{attention:{items:[{id:'session:chat',read:true}]}}})), '');
 assert.equal(renderToStaticMarkup(React.createElement(ConversationError,{...props,session:{id:'chat',status:'idle',failure:props.session.failure}})), '');
});

test('finished-only worker summary remains visible without eager worker rows',()=>{
 const session={id:'chat',workers:[],workerSummary:{bounded:true,total:1000,finished:1000,
  recentFinished:[{id:'last',name:'Last saved worker',status:'completed'}]}};
 const html=renderToStaticMarkup(React.createElement(ChatOverview,{state:{selectedSessionId:'chat',sessions:[session],view:{}},act:()=>{},changeDraft:()=>{}}));
 assert.match(html,/1000.*finished observations/);assert.match(html,/Last saved worker/);
});
