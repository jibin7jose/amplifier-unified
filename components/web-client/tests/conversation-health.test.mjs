import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConversationDetails,ConversationError}=await server.ssrLoadModule('/src/conversation-controls.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
test('details and recovery use shared actions with immediate pending feedback and no duplicate submission',async()=>{
 let release;const calls=[],act=(name,args)=>{calls.push({name,args});return new Promise(resolve=>{release=resolve})};
 const session={id:'chat',runtimeSessionId:'native',status:'error',workspace:'/workspace',bundle:'work',error:'Manager turn failed'};
 let root,operation;await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session,act}))});
 // Read-only inspection starts when the details component opens.
 assert.deepEqual(calls,[{name:'capacity.read',args:{sessionId:'chat',limit:1}},{name:'session.inspect',args:{id:'chat'}}]);
 assert.equal(root.root.findByProps({'data-action':'session.recover'}).props.disabled,true);
 await renderAct(async()=>{release({accepted:true,result:{failure:{summary:'Invalid image',errorType:'InvalidRequestError'}}});await Promise.resolve()});
 assert.match(JSON.stringify(root.toJSON()),/Invalid image/);
 await renderAct(async()=>{operation=root.root.findByProps({'data-action':'session.recover'}).props.onClick()});
 await renderAct(async()=>root.root.findByProps({'data-action':'session.recover'}).props.onClick());
 assert.equal(calls.length,3);assert.deepEqual(calls[2],{name:'session.recover',args:{id:'chat'}});
 await renderAct(async()=>{release({accepted:false});await operation});
 assert.match(JSON.stringify(root.toJSON()),/Could not create/);await renderAct(async()=>root.unmount());
});
test('dismissal does not remove settings details; active work cannot be recovered',async()=>{
 const session={id:'chat',status:'working',error:'failed'},act=async()=>({accepted:true});let root;
 await renderAct(async()=>{root=create(React.createElement(ConversationError,{session,act,state:{attention:{items:[{id:'session:chat',read:true}]}}}))});
 assert.equal(root.toJSON(),null);
 await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session,act,initiallyOpen:true})));
 assert.equal(root.root.findByProps({'data-action':'session.recover'}).props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/Copy session ID/);
 await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{...session,status:'error',workers:[{status:'working'}]},act,initiallyOpen:true})));
 assert.equal(root.root.findByProps({'data-action':'session.recover'}).props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('dismissing a stopped connection acknowledges only its exact error and never sends a turn',async()=>{
 const calls=[],session={id:'chat',status:'idle',error:'Connection stopped',failure:{category:'worker_exit'}};
 const item={id:'session:chat',fingerprint:'recorded-failure',read:false};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let root;
 try{
  await renderAct(async()=>{root=create(React.createElement(ConversationError,{session,state:{attention:{items:[item]}},act}))});
  assert.deepEqual(calls,[]);
  await renderAct(async()=>root.root.findByProps({'aria-label':'Dismiss conversation error'}).props.onClick());
  assert.deepEqual(calls,[{name:'attention.read',args:{ids:['session:chat'],fingerprints:{'session:chat':'recorded-failure'}}}]);
  await renderAct(async()=>root.update(React.createElement(ConversationError,{session,state:{attention:{items:[{...item,read:true}]}},act})));
  assert.equal(root.toJSON(),null);
  assert.equal(session.error,'Connection stopped');
  assert.equal(calls.length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('error banner opens recorded details through a shared action without retrying work',async()=>{
 const calls=[];
 let root;await renderAct(async()=>{root=create(React.createElement(ConversationError,{session:{id:'chat',error:'failed'},state:{},act:async(name,args)=>{calls.push({name,args});return {accepted:true}}}))});
 assert.equal(root.root.findAllByType('button').length,2);assert.equal(root.toJSON().props.className,'a-alert');
 await renderAct(async()=>root.root.findByProps({'data-action':'view.update'}).props.onClick());
 assert.deepEqual(calls,[{name:'view.update',args:{patch:{panel:'chat-error'}}}]);
 await renderAct(async()=>root.unmount());
});

test('context-limit recovery alert shares active-work guard and cannot invoke recovery',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const session={id:'chat',status:'error',error:'context limit',workers:[{status:'working'}],
  failure:{category:'context_limit',summary:'Context limit',guidance:'Create a recovery copy.'}};
 let root;await renderAct(async()=>{root=create(React.createElement(ConversationError,{session,act,state:{attention:{items:[]}}}))});
 const recovery=root.root.findByProps({'data-action':'session.recover'});
 assert.equal(recovery.props.disabled,true);
 await renderAct(async()=>recovery.props.onClick());
 assert.deepEqual(calls,[]);
 await renderAct(async()=>root.unmount());
});

test('structured failure remains visible without inventing an empty runtime message',async()=>{
 const failure={category:'invalid_image',errorType:'ValueError',summary:'The provider rejected an image.',guidance:'Inspect the retained evidence.'};
 const session={id:'chat',status:'stopped',failure},act=async()=>({accepted:true,result:{status:'stopped',failure}});
 let root;await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session,act}))});
 const content=JSON.stringify(root.toJSON());
 assert.match(content,/The provider rejected an image/);
 assert.match(content,/ValueError/);
 assert.doesNotMatch(content,/Runtime message/);
 // A verified new generation clears current failure without re-fetching the
 // prior report or presenting its error as the new input's failure.
 await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{id:'chat',status:'working'},act})));
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/The provider rejected an image/);
 await renderAct(async()=>root.unmount());
});

test('copy refreshes diagnostics on demand after status changes, with no background polling',async()=>{
 const calls=[],copied=[];let status='idle';
 Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async value=>copied.push(value)}},configurable:true});
 const act=async name=>{calls.push(name);return {accepted:true,result:{status,capturedAt:1790182800}}};
 const session={id:'chat',status};let root;
 await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session,act}))});
 status='stopped';
 await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{...session,status},act})));
 assert.deepEqual(calls,['capacity.read','session.inspect']);
 const button=root.root.findAllByType('button').find(button=>button.children.includes('Copy diagnostics'));
 await renderAct(async()=>button.props.onClick());
 assert.deepEqual(calls,['capacity.read','session.inspect','session.inspect']);
 assert.equal(JSON.parse(copied[0]).status,'stopped');
 assert.equal(JSON.parse(copied[0]).capturedAt,1790182800);
 await renderAct(async()=>root.unmount());
});

test('copy refuses an explicitly stale diagnostic result instead of mislabeling it current',async()=>{
 const copied=[];let stale=false;
 Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async value=>copied.push(value)}},configurable:true});
 const act=async()=>({accepted:true,result:{status:'idle',...(stale?{stale}: {})}});
 let root;await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session:{id:'chat',status:'stopped'},act}))});
 stale=true;
 const button=root.root.findAllByType('button').find(button=>button.children.includes('Copy diagnostics'));
 await renderAct(async()=>button.props.onClick());
 assert.deepEqual(copied,[]);
 assert.match(JSON.stringify(root.toJSON()),/conversation changed while reading its diagnostics/);
 await renderAct(async()=>root.unmount());
});

test('failed diagnostic copy keeps its complete manual report through equivalent server refreshes',async()=>{
 Object.defineProperty(globalThis,'navigator',{value:{userAgent:'fixture-browser',clipboard:{writeText:async()=>{throw Error('denied')}}},configurable:true});
 const failure={category:'app_bridge_timeout',generationId:'failed-turn',recordedAt:42};
 const session={id:'chat',status:'error',failure};
 const act=async()=>({accepted:true,result:{sessionId:'chat',status:'error',diagnosticsSchema:2,failure}});
 let root;
 try{
  await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session,act,focused:true}))});
  await renderAct(async()=>root.root.findAllByType('button').find(button=>button.children.includes('Copy diagnostics')).props.onClick());
  await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{...session,failure:{...failure}},act,focused:true})));
  assert.match(JSON.stringify(root.toJSON()),/Clipboard unavailable/);
  const report=JSON.parse(root.root.findByProps({'aria-label':'Diagnostic report'}).props.value);
  assert.equal(report.failure.generationId,'failed-turn');
  assert.equal(report.client.userAgent,'fixture-browser');
  await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{...session,failure:{...failure,recordedAt:43}},act,focused:true})));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Copy diagnostic text manually/);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('clipboard write starts in the click gesture while its fresh diagnostic content is pending',async()=>{
 const writes=[];let release,delay=false;
 const prior=globalThis.ClipboardItem;
 globalThis.ClipboardItem=class{constructor(content){this.content=content}};
 Object.defineProperty(globalThis,'navigator',{value:{clipboard:{write:async items=>{writes.push(items);const blob=await items[0].content['text/plain'];writes.push(await blob.text())}}},configurable:true});
 const act=async()=>delay?new Promise(resolve=>{release=resolve}):{accepted:true,result:{status:'idle'}};
 let root,operation;
 try{
  await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session:{id:'chat',status:'stopped'},act}))});
  delay=true;
  const button=root.root.findAllByType('button').find(button=>button.children.includes('Copy diagnostics'));
  await renderAct(async()=>{operation=button.props.onClick();assert.equal(writes.length,1)});
  await renderAct(async()=>{release({accepted:true,result:{status:'stopped',capturedAt:1790182800}});await operation});
  assert.equal(JSON.parse(writes[1]).status,'stopped');
  assert.match(JSON.stringify(root.toJSON()),/Diagnostics copied/);
 }finally{await renderAct(async()=>root?.unmount());globalThis.ClipboardItem=prior}
});

test('a late diagnostic response cannot replace the report for a newer failure',async()=>{
 const writes=[];let release,root,operation;
 Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async value=>writes.push(value)}},configurable:true});
 const session={id:'chat',status:'error',failure:{generationId:'old',recordedAt:42}};
 const act=()=>new Promise(resolve=>release=resolve);
 try{
  await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session,act,focused:true}))});
  await renderAct(async()=>{operation=root.root.findAllByType('button').find(button=>button.children.includes('Copy diagnostics')).props.onClick()});
  await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{...session,failure:{generationId:'new',recordedAt:43}},act,focused:true})));
  await renderAct(async()=>{release({accepted:true,result:{status:'error',failure:session.failure}});await operation});
  assert.deepEqual(writes,[]);
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Diagnostics copied|Copy diagnostic text manually/);
 }finally{await renderAct(async()=>root?.unmount())}
});


test('continuation is explicit, guarded against duplicate or active-work sends, and can retry failure',async()=>{
 const calls=[];let release,root,pending;
 const act=(name,args)=>{calls.push({name,args});return new Promise(resolve=>release=resolve)};
 const session={id:'chat',status:'error',error:'ValueError'};
 await renderAct(async()=>{root=create(React.createElement(ConversationError,{session,state:{},act}))});
 assert.deepEqual(calls,[]);
 const button=()=>root.root.findByProps({'data-action':'conversation.send'});
 await renderAct(async()=>{pending=button().props.onClick();button().props.onClick()});
 assert.equal(calls.length,1);assert.equal(button().props.disabled,true);
 assert.equal(calls[0].args.sessionId,'chat');assert.equal(calls[0].args.preserveDraft,true);
 await renderAct(async()=>{release(undefined);await pending});
 assert.match(JSON.stringify(root.toJSON()),/Could not send the continuation/);
 assert.equal(button().props.disabled,false);
 await renderAct(async()=>{pending=button().props.onClick()});
 await renderAct(async()=>{release({accepted:true});await pending});
 assert.equal(calls.length,2);
 await renderAct(async()=>root.update(React.createElement(ConversationError,{session:{...session,status:'working'},state:{},act})));
 assert.equal(button().props.disabled,true);
 await renderAct(async()=>button().props.onClick());assert.equal(calls.length,2);
 await renderAct(async()=>root.unmount());
});
