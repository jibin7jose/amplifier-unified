import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
import {createPendingView} from '../src/pending-view.js';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.location={origin:'http://fixture.invalid'};
globalThis.mcpTestBridges=[];
const listeners=new Map();
globalThis.document={visibilityState:'visible',addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};
globalThis.window={matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})};
const server=await createServer({resolve:{alias:{'@modelcontextprotocol/ext-apps/app-bridge':fileURLToPath(new URL('./fixtures/mcp-app-bridge-stub.js',import.meta.url))}},server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {McpAppViewer}=await server.ssrLoadModule('/src/mcp-app-viewer.jsx');
const {McpContractReview}=await server.ssrLoadModule('/src/mcp-contract-review.jsx');
const {McpAppVisibilityProvider,MCP_VISIBILITY}=await server.ssrLoadModule('/src/mcp-app-lifecycle.jsx');
test.after(()=>server.close());
const response=value=>({ok:true,status:200,text:async()=>JSON.stringify(value)});
const available={source:'available',status:'ready',message:'Tool view connected',canReconnect:true,bindingRevision:'saved-binding'};
test.beforeEach(()=>{globalThis.fetch=async path=>{assert.ok(path.endsWith('/status'));return response(available)}});

test('canvas, Library and document visibility update context without remounting an edited iframe',async()=>{
 const frame={contentWindow:{},draft:'unsaved'},canvas={id:'view',open:true};let component,visible=true;
 const render=()=>React.createElement(McpAppVisibilityProvider,{visible},React.createElement(McpAppViewer,{canvas,act:async()=>({})}));
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),initialSrc=frame.src,initialCount=mcpTestBridges.length;
 try{
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  canvas.open=false;await act(async()=>component.update(render()));assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  canvas.open=true;visible=false;await act(async()=>component.update(render()));assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  visible=true;await act(async()=>component.update(render()));assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  document.visibilityState='hidden';await act(async()=>listeners.get('visibilitychange')());assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  document.visibilityState='visible';await act(async()=>listeners.get('visibilitychange')());assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  assert.equal(mcpTestBridges.length,initialCount);assert.equal(frame.src,initialSrc);assert.equal(frame.draft,'unsaved');assert.equal(bridge.closed,undefined);
 }finally{await act(async()=>component.unmount())}
 assert.equal(bridge.closed,true);assert.equal(listeners.has('visibilitychange'),false);
});
test('optimistic restore pauses MCP reads until acknowledgement and retains the edited iframe on failure',async()=>{
 const pending=createPendingView(),reports=[],sent=[],frame={contentWindow:{},draft:'unsaved'};
 let state={selectedSessionId:'chat',client:{hostInstanceId:'host'},canvas:{id:'acknowledged',open:true},view:{}},component;
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/status'))return response(available);
  if(path.endsWith('/tools'))return response({tools:[{name:'read',annotations:{readOnlyHint:true}}]});
  assert.equal(state.canvas.open,true,'A tool call must not race the server visibility acknowledgement');
  sent.push(JSON.parse(options.body));return response({status:'completed',result:{content:[]}});
 };
 const render=()=>React.createElement(McpAppViewer,{canvas:pending.apply(state).canvas,act:async(name,args)=>reports.push({name,args})});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),initialSrc=frame.src,initialCount=mcpTestBridges.length;
 const update=()=>component.update(render());
 const begin=async open=>{let token;await act(async()=>{token=pending.addCanvas(state,open);update()});return token};
 const finish=async(token,open)=>act(async()=>{state={...state,canvas:{...state.canvas,open}};pending.settle(token);update()});
 try{
  await act(async()=>bridge.oncalltool({name:'read'}));assert.equal(sent.length,1);
  const hide=await begin(false);
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  await finish(hide,false);
  const restore=await begin(true);
  assert.equal(pending.apply(state).canvas.open,true,'The panel still paints before acknowledgement');
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  await act(async()=>assert.rejects(bridge.oncalltool({name:'read'}),/paused/));
  assert.equal(sent.length,1,'No server request is admitted while restore is pending');
  await finish(restore,true);
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  await act(async()=>bridge.oncalltool({name:'read'}));assert.equal(sent.length,2);
  const hideAgain=await begin(false);await finish(hideAgain,false);
  const rejectedRestore=await begin(true);await finish(rejectedRestore,false);
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false,'A failed restore cannot enable reads');
  await act(async()=>assert.rejects(bridge.oncalltool({name:'read'}),/paused/));
  assert.equal(sent.length,2);assert.equal(reports.filter(row=>row.args.status==='error').length,0);
  assert.equal(mcpTestBridges.length,initialCount);assert.equal(frame.src,initialSrc);assert.equal(frame.draft,'unsaved');assert.equal(bridge.closed,undefined);
 }finally{await act(async()=>component.unmount())}
});
test('only explicitly read-only calls are coalesced; mutations and unknown tools retain separate IDs',async()=>{
 let release;const slow=new Promise(resolve=>release=resolve),sent=[];
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/status'))return response(available);
  if(path.endsWith('/tools'))return response({tools:[{name:'read',annotations:{readOnlyHint:true}},{name:'write',annotations:{readOnlyHint:false}}]});
  const body=JSON.parse(options.body);sent.push(body);if(body.name==='read')await slow;
  return response({status:'completed',result:{content:[]}});
 };
 let component;await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'calls',open:true},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  let calls;await act(async()=>{
   calls=[bridge.oncalltool({name:'read'}),bridge.oncalltool({name:'read'}),bridge.oncalltool({name:'write'}),bridge.oncalltool({name:'write'}),bridge.oncalltool({name:'unknown'}),bridge.oncalltool({name:'unknown'})];
   await new Promise(resolve=>setImmediate(resolve));
  });
  assert.equal(sent.filter(row=>row.name==='read').length,1);
  assert.equal(sent.filter(row=>row.name==='write').length,2);
  assert.equal(sent.filter(row=>row.name==='unknown').length,2);
  assert.equal(new Set(sent.map(row=>row.id)).size,5);
  await act(async()=>{release();await Promise.all(calls)});
 }finally{await act(async()=>component.unmount())}
});
test('catalog failure preserves forwarding and request identity for unclassified tools',async()=>{
 const sent=[];
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/status'))return response(available);
  if(path.endsWith('/tools'))throw Error('Catalog temporarily unavailable');
  sent.push(JSON.parse(options.body));return response({status:'completed',result:{content:[]}});
 };
 let component;await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'missing-catalog',open:true},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  await act(async()=>Promise.all([bridge.oncalltool({name:'save'}),bridge.oncalltool({name:'save'})]));
  assert.equal(sent.length,2);assert.notEqual(sent[0].id,sent[1].id);
 }finally{await act(async()=>component.unmount())}
});
test('expected paused or overloaded reads do not publish host errors or attention',async()=>{
 let release;const slow=new Promise(resolve=>release=resolve),reports=[],sent=[];
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/status'))return response(available);
  if(path.endsWith('/tools'))return response({tools:[{name:'read',annotations:{readOnlyHint:true}}]});
  sent.push(JSON.parse(options.body));await slow;return response({status:'completed',result:{content:[]}});
 };
 let component;const canvas={id:'suspended',open:false},actCommand=async(name,args)=>{reports.push({name,args})};
 const render=()=>React.createElement(McpAppViewer,{canvas,act:actCommand});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  await act(async()=>assert.rejects(bridge.oncalltool({name:'read'}),/paused/));assert.equal(sent.length,0);
  canvas.open=true;await act(async()=>component.update(render()));
  let pending;await act(async()=>{
   pending=Array.from({length:6},(_,i)=>bridge.oncalltool({name:'read',arguments:{i}}));
   await assert.rejects(bridge.oncalltool({name:'read',arguments:{i:6}}),/Too many/);
  });
  assert.equal(sent.length,2);assert.equal(reports.filter(row=>row.args.status==='error').length,0);
  await act(async()=>{release();await Promise.all(pending)});
 }finally{await act(async()=>component.unmount())}
});
test('missing saved source is reported before frame navigation or a misleading handshake timeout',async()=>{
 globalThis.fetch=async()=>response({...available,source:'unavailable',status:'source_unavailable',canReconnect:false,message:'Saved source is unavailable; its reference remains.'});
 const frame={contentWindow:{}},before=mcpTestBridges.length,reports=[];let component;
 await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'missing',open:true},act:async(name,args)=>reports.push(args)}),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 try{
  assert.equal(mcpTestBridges.length,before);assert.equal(frame.src,undefined);
  assert.equal(reports.at(-1).message,'Saved source is unavailable; its reference remains.');
  assert.equal(component.root.findByProps({'aria-label':'Reconnect tool view'}).props.disabled,true);
 }finally{await act(async()=>component.unmount())}
});
test('explicit reconnect uses the reviewed binding and retains the mounted frame and input',async()=>{
 const frame={contentWindow:{},draft:'unsaved'},sent=[];let component;
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/status'))return response({...available,status:'disconnected',message:'Saved source available; reconnect.'});
  if(path==='/api/actions'){sent.push(JSON.parse(options.body));return response({accepted:true,operationId:'reconnect-receipt'})}
  assert.equal(path,'/api/smart-tools/operations/reconnect-receipt');return response({status:'completed',result:available});
 };
 await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'retained',open:true,mcp:{}},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),count=mcpTestBridges.length,src=frame.src;
 try{
  await act(async()=>bridge.oninitialized());
  await act(async()=>component.root.findByProps({'aria-label':'Reconnect tool view'}).props.onClick());
  assert.equal(sent.length,1);assert.equal(sent[0].action,'smartTools.reconnectView');
  assert.deepEqual(sent[0].args,{canvasId:'retained',expectedBindingRevision:'saved-binding'});
  assert.equal(mcpTestBridges.length,count);assert.equal(frame.src,src);assert.equal(frame.draft,'unsaved');
  assert.equal(component.root.findByProps({'data-phase':'ready'}).props.role,'status');
  assert.equal(bridge.toolListChanges,1);assert.equal(bridge.resourceListChanges,1);
  await act(async()=>component.root.findByProps({'aria-label':'Reconnect tool view'}).props.onClick());
  assert.equal(bridge.toolListChanges,2,'A second explicit disconnected-to-ready recovery emits one new notification');
 }finally{await act(async()=>component.unmount())}
});

test('shared connection recovery notifies an initialized app once without a reload or grant change',async()=>{
 const frame={contentWindow:{},draft:'unsaved'},canvas={id:'shared-recovery',open:true,mcp:{allowedTools:['read']}},sent=[];
 let component,connectionKey='offline',availability={...available,status:'disconnected'};
 globalThis.fetch=async path=>{assert.ok(path.endsWith('/status'));sent.push(path);return response(availability)};
 const render=()=>React.createElement(McpAppViewer,{canvas,connectionKey,act:async()=>({})});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),src=frame.src,count=mcpTestBridges.length;
 try{
  await act(async()=>bridge.oninitialized());
  assert.equal(bridge.toolListChanges,undefined);
  availability={...available};connectionKey='online';
  await act(async()=>component.update(render()));
  assert.equal(bridge.toolListChanges,1);assert.equal(bridge.resourceListChanges,1);
  connectionKey='online-again';await act(async()=>component.update(render()));
  assert.equal(bridge.toolListChanges,1,'Repeated ready status must not repeat startup reads');
  assert.equal(bridge.resourceListChanges,1);
  assert.equal(frame.src,src);assert.equal(frame.draft,'unsaved');assert.equal(mcpTestBridges.length,count);
  assert.deepEqual(canvas.mcp.allowedTools,['read']);assert.equal(sent.length,3);
  assert.deepEqual(bridge.args[2].serverTools,{listChanged:true});
  assert.deepEqual(bridge.args[2].serverResources,{listChanged:true});
 }finally{await act(async()=>component.unmount())}
});

test('historical views omit reconnection and denied views explain disabled recovery without notifications',async()=>{
 for(const status of ['saved_version','contract_changed','account_review_required','configuration_changed']){
  globalThis.fetch=async()=>response({...available,status,canReconnect:false,message:'Review the saved view before using live features.'});
  let component;
  await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:status,open:true,readOnlyVersion:status==='saved_version'},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
  const bridge=mcpTestBridges.at(-1);
  try{
   await act(async()=>bridge.oninitialized());
   if(status==='saved_version'){
    assert.equal(component.root.findAllByProps({'aria-label':'Reconnect tool view'}).length,0);
   }else{
    const button=component.root.findByProps({'aria-label':'Reconnect tool view'});
    assert.equal(button.props.disabled,true);assert.equal(button.props.title,'Review the saved view before using live features.');
   }
   assert.equal(bridge.toolListChanges,undefined);assert.equal(bridge.resourceListChanges,undefined);
  }finally{await act(async()=>component.unmount())}
 }
});


test('automatic saved-version pin preserves DOM but changes authority and denies old app calls',async()=>{
 const frame={contentWindow:{},draft:'unfinished'},initial={id:'pin',open:true,revision:1,resourceRevision:'live',generation:1,renderIdentity:'mounted-live'};
 let component,canvas=initial;const calls=[];
 globalThis.fetch=async path=>{calls.push(path);assert.ok(path.includes('/status'));return response({...available,status:canvas.readOnlyVersion?'saved_version':'ready'})};
 const render=()=>React.createElement(McpAppViewer,{canvas,act:async()=>({})});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),count=mcpTestBridges.length,src=frame.src;
 try{
  await act(async()=>bridge.oninitialized());
  canvas={...initial,readOnlyVersion:true,selectedVersion:1,resourceRevision:'historical',generation:2};
  await act(async()=>component.update(render()));
  assert.equal(frame.src,src);assert.equal(frame.draft,'unfinished');assert.equal(mcpTestBridges.length,count);
  assert.ok(calls.at(-1).includes('resourceRevision=historical'));
  await assert.rejects(bridge.oncalltool({name:'write',arguments:{}}),/read-only/);
  assert.deepEqual(await bridge.onlisttools(),{tools:[]});
  assert.equal(bridge.closed,undefined);assert.equal(bridge.toolListChanges,undefined);
 }finally{await act(async()=>component.unmount())}
});

test('late approval status refresh cannot mask a newer contract refusal',async()=>{
 let finish,component,reads=0;
 const held=new Promise(resolve=>{finish=resolve});
 let canvas={id:'review-status',open:true,revision:1,resourceRevision:'v1',generation:1,renderIdentity:'retained'};
 globalThis.fetch=async()=>{
  reads++;
  if(reads===2)return held;
  return response(reads===1?available:{...available,status:'contract_changed',canReconnect:false,bindingRevision:'new-binding',message:'Newer contract requires review'});
 };
 const render=()=>React.createElement(McpAppViewer,{canvas,act:async()=>({})});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 try{
  await act(async()=>mcpTestBridges.at(-1).oninitialized());
  let pending;await act(async()=>{pending=component.root.findByType(McpContractReview).props.onApplied()});
  canvas={...canvas,revision:2,resourceRevision:'v2',generation:2};
  await act(async()=>component.update(render()));
  assert.equal(reads,3);
  await act(async()=>{finish(response(available));await pending});
  const reconnect=component.root.findByProps({'aria-label':'Reconnect tool view'});
  assert.equal(reconnect.props.disabled,true);
  assert.equal(reconnect.props.title,'Newer contract requires review');
 }finally{await act(async()=>component.unmount())}
});
