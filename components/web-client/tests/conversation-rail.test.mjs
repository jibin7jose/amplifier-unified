import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConversationRail}=await server.ssrLoadModule('/src/conversation-rail.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('opening, previewing and switching conversations never requests a complete history index',async()=>{
 const original=globalThis.fetch;let root;const requests=[],jumps=[];
 globalThis.fetch=async(...args)=>{requests.push(args);throw Error('Unexpected history read')};
 const props={sessionId:'chat',messages:[{id:'recent-user',role:'user',text:'Recent question'},{id:'recent-answer',role:'assistant',text:'Recent answer'}],onJump:id=>jumps.push(id)};
 try{
  await act(async()=>{root=create(React.createElement(ConversationRail,props))});
  const mark=root.root.findByProps({'data-turn-id':'recent-user'});
  await act(async()=>mark.props.onMouseEnter());
  assert.match(JSON.stringify(root.toJSON()),/Recent answer/);
  await act(async()=>mark.props.onClick());assert.deepEqual(jumps,['recent-user']);
  await act(async()=>root.update(React.createElement(ConversationRail,{...props,sessionId:'other',messages:[{id:'other-user',role:'user',text:'Another chat'}]})));
  assert.equal(root.root.findAllByProps({'data-turn-id':'recent-user'}).length,0);
  assert.equal(root.root.findAllByProps({'data-turn-id':'other-user'}).length,1);
  assert.deepEqual(requests,[]);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});
