import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {InlineMedia}=await server.ssrLoadModule('/src/inline-media.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalFetch=globalThis.fetch,originalObserver=globalThis.IntersectionObserver;
let root,requests;
const render=props=>React.createElement(InlineMedia,{sessionId:'chat-a',messageId:'answer',...props});
const metadata=(overflow=false)=>({items:[],overflow,mediaRevision:0});
const complete=async(index,value=metadata())=>act(async()=>requests[index].resolve(new Response(JSON.stringify(value))));
test.beforeEach(()=>{
 requests=[];globalThis.IntersectionObserver=undefined;
 globalThis.fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,signal:options.signal,resolve,reject}));
});
test.afterEach(async()=>{await act(async()=>root?.unmount());root=null;globalThis.fetch=originalFetch;globalThis.IntersectionObserver=originalObserver});
test.after(()=>server.close());

test('known-empty and uncovered selected messages issue no metadata requests',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:false}))});
 for(const props of [{mediaRevision:99,linkedMediaAvailable:false},{sessionId:'chat-b'},
  {sessionId:'chat-b',linkedMediaAvailable:null},{sessionId:'chat-b',linkedMediaAvailable:'true'}])
  await act(async()=>root.update(render(props)));
 assert.equal(requests.length,0);assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
});

test('positive coverage requests once, false aborts and fences an unresolved response',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 assert.equal(requests.length,1);assert.match(requests[0].url,/sessionId=chat-a/);
 await act(async()=>root.update(render({linkedMediaAvailable:true})));
 assert.equal(requests.length,1,'unchanged publication does not repeat the request');
 await act(async()=>root.update(render({linkedMediaAvailable:false})));
 assert.equal(requests[0].signal.aborted,true);
 await complete(0,metadata(true));assert.equal(root.root.findAllByType('p').length,0);
 await act(async()=>root.update(render({linkedMediaAvailable:true})));
 assert.equal(requests.length,2,'relink at revision zero recovers discovery');
 await complete(1,metadata(true));assert.equal(root.root.findAllByType('p').length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:false})));
 assert.equal(root.root.findAllByType('p').length,0,'unlink clears completed metadata too');
});

test('selection and message changes cannot display the previous owner while coverage is pending',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 await complete(0,metadata(true));assert.equal(root.root.findAllByType('p').length,1);
 await act(async()=>root.update(render({sessionId:'chat-b'})));
 assert.equal(root.root.findAllByType('p').length,0);assert.equal(requests.length,1);
 await act(async()=>root.update(render({sessionId:'chat-b',linkedMediaAvailable:true})));
 assert.equal(requests.length,2);
 await act(async()=>root.update(render({sessionId:'chat-b',messageId:'other'})));
 assert.equal(requests[1].signal.aborted,true);await complete(1,metadata(true));
 assert.equal(root.root.findAllByType('p').length,0);assert.equal(requests.length,2);
});

test('metadata errors are owned by the message and clear when its registry becomes empty',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 await act(async()=>requests[0].reject(Error('synthetic failure')));
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:false})));
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
 await act(async()=>root.update(render({linkedMediaAvailable:true,mediaRevision:2})));
 assert.equal(requests.length,2);await complete(1);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
});

test('positive registry coverage keeps viewport ownership and disposes pending reads',async()=>{
 let visible;
 globalThis.IntersectionObserver=class{constructor(callback){visible=callback}observe(){}disconnect(){}};
 await act(async()=>{root=create(render({linkedMediaAvailable:true}),{createNodeMock:()=>({})})});
 assert.equal(requests.length,0);
 await act(async()=>visible([{isIntersecting:true}]));assert.equal(requests.length,1);
 await act(async()=>visible([{isIntersecting:false}]));assert.equal(requests[0].signal.aborted,true);
 await complete(0,metadata(true));assert.equal(root.root.findAllByType('p').length,0);
 await act(async()=>visible([{isIntersecting:true}]));assert.equal(requests.length,2);
 await act(async()=>root.unmount());root=null;assert.equal(requests[1].signal.aborted,true);
});

test('configuration changes defer discovery and settle with one current metadata request',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true,configurationBusy:true}))});
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:true,mediaRevision:2})));
 assert.equal(requests.length,0,'temporary transcript indices cannot start discovery');
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false,mediaRevision:2})));
 assert.equal(requests.length,1);
 await complete(0,metadata(true));
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false,mediaRevision:2})));
 assert.equal(requests.length,1,'unchanged settled publication does not poll');
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:true,mediaRevision:2})));
 assert.equal(root.root.findAllByType('p').length,1,'already-qualified metadata remains visible for this owner');
 assert.equal(requests.length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false,mediaRevision:2})));
 assert.equal(requests.length,2);await complete(1);
});

test('entering configuration change aborts and fences pending success and failure',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:true})));
 assert.equal(requests[0].signal.aborted,true);
 await complete(0,metadata(true));
 assert.equal(root.root.findAllByType('p').length,0,'late success cannot publish during transition');
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false})));
 assert.equal(requests.length,2);
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:true})));
 assert.equal(requests[1].signal.aborted,true);
 await act(async()=>requests[1].reject(Error('late identity failure')));
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false})));
 assert.equal(requests.length,3);await complete(2);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
});

test('transition hides an old error, requalifies once, and keeps genuine failures manually retryable',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 const rejectIdentity=async index=>act(async()=>requests[index].resolve(new Response(JSON.stringify({
  accepted:false,error:'Choose an existing saved assistant message in this conversation.'}),{status:400})));
 await rejectIdentity(0);assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:true})));
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
 assert.equal(requests.length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false})));
 assert.equal(requests.length,2);await rejectIdentity(1);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1,'a stable identity failure remains actionable');
 await act(async()=>root.update(render({linkedMediaAvailable:true,configurationBusy:false})));
 assert.equal(requests.length,2,'arbitrary failures are not automatically retried');
 await act(async()=>root.root.findByType('button').props.onClick());
 assert.equal(requests.length,3);await complete(2);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
});

test('selection during configuration change cannot retain or fetch another message metadata',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});await complete(0,metadata(true));
 await act(async()=>root.update(render({sessionId:'chat-b',messageId:'other',linkedMediaAvailable:true,configurationBusy:true})));
 assert.equal(root.root.findAllByType('p').length,0);assert.equal(requests.length,1);
 await act(async()=>root.update(render({sessionId:'chat-b',messageId:'other',linkedMediaAvailable:true,configurationBusy:false})));
 assert.equal(requests.length,2);assert.match(requests[1].url,/sessionId=chat-b&messageId=other/);await complete(1);
 await act(async()=>root.update(render({sessionId:'chat-b',messageId:'other',linkedMediaAvailable:false,configurationBusy:true})));
 await act(async()=>root.update(render({sessionId:'chat-b',messageId:'other',linkedMediaAvailable:false,configurationBusy:false})));
 assert.equal(requests.length,2,'settlement does not manufacture positive registry coverage');
});
