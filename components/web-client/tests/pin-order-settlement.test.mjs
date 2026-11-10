import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({cacheDir:'.vite-pin-settlement-tests',server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {PinnedItems}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const ids=['session:a','session:b'],expected=['session:b','session:a'];
const item=id=>({id,kind:'session',row:{id:id.slice(8),title:id,pinned:true}});
async function fixture(dispatch=async()=>({accepted:true}),initial={}){
 let current={generation:1,selectedSessionId:'a',selectedWorkspaceId:'w',navigationResources:['pinnedNavigation'],view:{},pinnedItemIds:ids,pinnedNavigation:{items:ids.map(item),total:2,index:0,pages:1},...initial};
 const listeners=new Set(),calls=[];
 const host={getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}};
 const model={host,state:current,view:{},act:(...args)=>{calls.push(args);return dispatch(...args)},setDraft(){},choose(){}};
 let root;const element=()=>React.createElement(PinnedItems,{page:current.pinnedNavigation,model,now:0});
 await act(async()=>{root=create(element())});
 const update=async(patch,render=true)=>{await act(async()=>{current={...current,...patch};if(render)model.state=current;for(const fn of [...listeners])fn();if(render)root.update(element())})};
 const key=async(id,key)=>{await act(async()=>root.root.findByProps({'aria-label':'Reorder '+id}).props.onKeyDown({key,preventDefault(){}}))};
 return {root,host,calls,listeners,update,key,get current(){return current},order:()=>root.root.findAll(node=>node.type==='div'&&node.props['data-order-id']).map(node=>node.props['data-order-id']),busy:id=>root.root.findByProps({'aria-label':'Reorder '+id}).props['aria-disabled'],close:async()=>act(async()=>root.unmount())};
}
test('accepted command queues a second reorder until its exact query page arrives',async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');assert.deepEqual(f.order(),expected);assert.equal(f.busy('session:b'),undefined);
  await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  assert.deepEqual(f.order(),expected);await f.key('session:b','ArrowDown');assert.equal(f.calls.length,1);
  await f.update({pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)},navigationResourcePending:{}});
  assert.equal(f.busy('session:b'),undefined);assert.deepEqual(f.order(),ids);
  assert.deepEqual(f.calls[1],['navigation.pinOrder',{ids}]);
  await f.update({pinnedItemIds:ids,pinnedNavigation:{...f.current.pinnedNavigation,items:ids.map(item)}});
  assert.deepEqual(f.order(),ids);assert.equal(f.busy('session:b'),undefined);
 }finally{await f.close()}
});
test('a concurrent authoritative order overrides intent without replay or a stuck busy control',async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');
  await f.update({pinnedItemIds:['session:a'],pinnedNavigation:{items:[item('session:a')],total:1,index:0,pages:1}});
  assert.equal(f.calls.length,1);assert.equal(f.listeners.size,0);assert.deepEqual(f.order(),['session:a']);
  assert.match(f.root.toJSON().map?JSON.stringify(f.root.toJSON()):'',/Pins changed elsewhere/);
 }finally{await f.close()}
});
test('query failure releases busy state with a read error and never replays the saved command',async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'retry'}}});
  assert.equal(f.listeners.size,0);assert.equal(f.busy('session:b'),true);assert.equal(f.calls.length,1);assert.match(JSON.stringify(f.root.toJSON()),/display could not be refreshed/);
 }finally{await f.close()}
});
test('changing the page scope cancels the old order wait',async()=>{
 const f=await fixture();try{await f.key('session:b','ArrowUp');await f.update({view:{navPinnedPage:1}});assert.equal(f.listeners.size,0);assert.equal(f.busy('session:b'),undefined);assert.match(JSON.stringify(f.root.toJSON()),/Pinned view changed/)}finally{await f.close()}
});
test('unmount cancels a held stream wait and a late receipt cannot subscribe again',async()=>{
 let release;const f=await fixture(()=>new Promise(resolve=>{release=resolve}));await f.key('session:b','ArrowUp');await f.close();await act(async()=>release({accepted:true}));assert.equal(f.listeners.size,0);assert.equal(f.calls.length,1);
 const g=await fixture();await g.key('session:b','ArrowUp');assert.equal(g.listeners.size,1);await g.close();assert.equal(g.listeners.size,0);
});
test('offline stream timeout is bounded and does not replay intent',async t=>{
 const f=await fixture();t.mock.timers.enable({apis:['setTimeout']});try{
  await f.key('session:b','ArrowUp');await act(async()=>t.mock.timers.tick(10001));assert.equal(f.listeners.size,0);assert.equal(f.busy('session:b'),undefined);assert.equal(f.calls.length,1);assert.match(JSON.stringify(f.root.toJSON()),/display has not arrived/);
 }finally{t.mock.timers.reset();await f.close()}
});

test('selection-retained pins stay readable but cannot begin keyboard or pointer reordering',async()=>{
 const f=await fixture();try{
  await f.update({navigationResources:[],navigationResourceRetained:{pinnedNavigation:{pinnedNavigation:f.current.pinnedNavigation}},navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  assert.equal(f.busy('session:b'),true);await f.key('session:b','ArrowUp');
  await act(async()=>f.root.root.findByProps({'aria-label':'Reorder session:b'}).props.onPointerDown({button:0}));
  assert.equal(f.calls.length,0);assert.deepEqual(f.order(),ids);
  await f.update({navigationResources:['pinnedNavigation'],navigationResourceRetained:{},navigationResourcePending:{}});
  assert.equal(f.busy('session:b'),undefined);await f.key('session:b','ArrowUp');assert.equal(f.calls.length,1);
 }finally{await f.close()}
});

test('two adjacent sequences move different items while preserving hidden and cross-workspace pins',async()=>{
 const all=['session:a','workspace:other','session:b','session:hidden','session:c'];
 const visible=['session:a','session:b','session:c'];
 const f=await fixture(undefined,{pinnedItemIds:all,pinnedNavigation:{items:visible.map(item),total:3,index:0,pages:1}});
 const settle=async(total,shown)=>f.update({pinnedItemIds:total,pinnedNavigation:{...f.current.pinnedNavigation,items:shown.map(item)},navigationResourcePending:{}});
 try{
  // A first, then a different item C. Its optimistic gesture is kept locally
  // until A's exact display is current, without sending from mismatched inputs.
  await f.key('session:a','ArrowDown');
  const first=['workspace:other','session:b','session:a','session:hidden','session:c'];
  assert.deepEqual(f.calls[0],['navigation.pinOrder',{ids:first}]);
  await f.update({pinnedItemIds:first,navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  await f.key('session:c','ArrowUp');assert.equal(f.calls.length,1);
  await settle(first,['session:b','session:a','session:c']);
  const second=['workspace:other','session:b','session:c','session:a','session:hidden'];
  assert.deepEqual(f.calls[1],['navigation.pinOrder',{ids:second}]);
  await settle(second,['session:b','session:c','session:a']);
  assert.deepEqual(f.order(),['session:b','session:c','session:a']);
  assert.equal(f.busy('session:c'),undefined);
  // Adjacent cycle: B then A, derived from the settled total order, never from
  // the three displayed rows alone.
  await f.key('session:b','ArrowDown');
  const third=['workspace:other','session:c','session:b','session:a','session:hidden'];
  assert.deepEqual(f.calls[2],['navigation.pinOrder',{ids:third}]);
  await settle(third,['session:c','session:b','session:a']);
  await f.key('session:a','ArrowUp');
  const fourth=['workspace:other','session:c','session:a','session:b','session:hidden'];
  assert.deepEqual(f.calls[3],['navigation.pinOrder',{ids:fourth}]);
  await settle(fourth,['session:c','session:a','session:b']);
  assert.deepEqual(f.order(),['session:c','session:a','session:b']);
  assert.equal(f.listeners.size,0);assert.doesNotMatch(JSON.stringify(f.root.toJSON()),/Reconnect|Pins changed elsewhere/);
  assert.deepEqual([...new Set(f.current.pinnedItemIds)].sort(),[...all].sort());
 }finally{await f.close()}
});

test('display before a delayed first receipt retains another item without sending it early',async()=>{
 let release;const firstReceipt=new Promise(resolve=>{release=resolve});
 let count=0;const all=['session:a','session:b','session:c'],first=['session:b','session:a','session:c'],second=['session:b','session:c','session:a'];
 const f=await fixture(()=>++count===1?firstReceipt:Promise.resolve({accepted:true}),{pinnedItemIds:all,pinnedNavigation:{items:all.map(item),total:3,index:0,pages:1}});
 try{
  await f.key('session:a','ArrowDown');
  await f.update({pinnedItemIds:first,pinnedNavigation:{...f.current.pinnedNavigation,items:first.map(item)}});
  await f.key('session:c','ArrowUp');assert.equal(f.calls.length,1);
  await act(async()=>release({accepted:true}));assert.equal(f.busy('session:c'),undefined);
  assert.deepEqual(f.calls[1],['navigation.pinOrder',{ids:second}]);
  await f.update({pinnedItemIds:second,pinnedNavigation:{...f.current.pinnedNavigation,items:second.map(item)}});
  assert.equal(f.calls.length,2);assert.deepEqual(f.order(),second);assert.equal(f.listeners.size,0);
 }finally{await f.close()}
});

for(const message of ['HTTP 409: pin order changed','Connection lost: outcome unconfirmed'])test(`${message} is not retried or reported as saved`,async()=>{
 const f=await fixture(async()=>{throw Error(message)});
 try{
  await f.key('session:b','ArrowUp');assert.equal(f.calls.length,1);
  assert.equal(f.busy('session:b'),undefined);assert.equal(f.listeners.size,0);
  assert.deepEqual(f.order(),ids);assert.match(JSON.stringify(f.root.toJSON()),new RegExp(message));
  assert.doesNotMatch(JSON.stringify(f.root.toJSON()),/Pin order was saved/);
  await f.update({pinnedItemIds:expected,pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)}});
  assert.deepEqual(f.order(),expected);assert.equal(f.calls.length,1);
 }finally{await f.close()}
});

test('a different item cannot use a stale displayed page while a concurrent order is pending',async()=>{
 const all=['session:a','session:b','session:c'],first=['session:b','session:a','session:c'],concurrent=['session:c','session:b','session:a'];
 const f=await fixture(undefined,{pinnedItemIds:all,pinnedNavigation:{items:all.map(item),total:3,index:0,pages:1}});
 try{
  await f.key('session:a','ArrowDown');
  await f.update({pinnedItemIds:first,pinnedNavigation:{...f.current.pinnedNavigation,items:first.map(item)}});
  assert.equal(f.busy('session:b'),undefined);
  // The real resource owner can retain old page rows alongside a newer
  // canonical vector. This is pending coverage, not selection-retained data.
  await f.update({pinnedItemIds:concurrent,navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  await f.key('session:b','ArrowDown');
  assert.equal(f.calls.length,1,'Do not derive one mutation from mismatched canonical and displayed orders');
  await f.update({pinnedNavigation:{...f.current.pinnedNavigation,items:concurrent.map(item)},navigationResourcePending:{}});
  await f.key('session:b','ArrowDown');
  const final=['session:c','session:a','session:b'];
  assert.deepEqual(f.calls[1],['navigation.pinOrder',{ids:final}]);
  await f.update({pinnedItemIds:final,pinnedNavigation:{...f.current.pinnedNavigation,items:final.map(item)}});
  assert.deepEqual(f.order(),final);assert.equal(f.listeners.size,0);
  assert.doesNotMatch(JSON.stringify(f.root.toJSON()),/Reconnect/);
 }finally{await f.close()}
});

test('publication before React render cannot dispatch a move using an obsolete page',async()=>{
 const f=await fixture();try{
  await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'pending'}}},false);
  await f.key('session:b','ArrowUp');
  assert.equal(f.calls.length,0);assert.match(JSON.stringify(f.root.toJSON()),/pinned list is updating/);
 }finally{await f.close()}
});

for(const change of [{navigationResources:[]},{navigationResourceRetained:{pinnedNavigation:{}}},{navigationResourceScope:['replacement-host']}])
test('lost resource coverage or scope cannot falsely settle a saved order '+JSON.stringify(change),async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');
  await f.update({pinnedItemIds:expected,pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)},...change});
  if(change.navigationResourceScope)assert.match(JSON.stringify(f.root.toJSON()),/Pinned view changed/);
  else{assert.equal(f.listeners.size,1)}
  assert.equal(f.calls.length,1);
 }finally{await f.close()}
});

const refreshButton=f=>f.root.root.findAll(node=>node.type==='button'&&node.props.children==='Refresh pinned list')[0];
test('saved display timeout offers one authoritative read, without replay or reconnect',async t=>{
 const f=await fixture();let reads=0;
 f.host.refreshResources=async()=>{reads++;await f.update({pinnedItemIds:expected,pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)},navigationResourcePending:{}})};
 t.mock.timers.enable({apis:['setTimeout']});
 try{
  await f.key('session:b','ArrowUp');await act(async()=>t.mock.timers.tick(10001));
  assert.equal(reads,0);assert.equal(f.calls.length,1);assert.ok(refreshButton(f));
  await act(async()=>refreshButton(f).props.onClick());
  assert.equal(reads,1);assert.equal(f.calls.length,1);assert.equal(f.listeners.size,0);
  assert.deepEqual(f.order(),expected);assert.equal(f.root.root.findAllByProps({role:'alert'}).length,0);
  await f.key('session:a','ArrowUp');assert.equal(f.calls.length,2);
  await f.update({pinnedItemIds:ids,pinnedNavigation:{...f.current.pinnedNavigation,items:ids.map(item)}});
  assert.deepEqual(f.order(),ids);
 }finally{t.mock.timers.reset();await f.close()}
});

test('a late failed read from A cannot overwrite a newer B move',async t=>{
 const f=await fixture();let rejectRead,readPromise;
 f.host.refreshResources=()=>new Promise((_,reject)=>{rejectRead=reject});
 t.mock.timers.enable({apis:['setTimeout']});
 try{
  await f.key('session:b','ArrowUp');await act(async()=>t.mock.timers.tick(10001));
  await act(async()=>{readPromise=refreshButton(f).props.onClick()});
  await f.update({pinnedItemIds:expected,pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)}});
  await f.key('session:a','ArrowUp');
  await f.update({pinnedItemIds:ids,pinnedNavigation:{...f.current.pinnedNavigation,items:ids.map(item)}});
  await act(async()=>{rejectRead(Error('late old read failure'));await readPromise});
  assert.equal(f.calls.length,2);assert.deepEqual(f.order(),ids);
  assert.equal(f.root.root.findAllByProps({role:'alert'}).length,0);assert.equal(f.listeners.size,0);
 }finally{t.mock.timers.reset();await f.close()}
});

for(const outcome of ['pending','rejected','never'])test(`display reconciliation ${outcome} stays honest and bounded`,async t=>{
 const f=await fixture();let reads=0;
 f.host.refreshResources=async()=>{reads++;if(outcome==='rejected')throw Error('Read unavailable');if(outcome==='never')await new Promise(()=>{});};
 t.mock.timers.enable({apis:['setTimeout']});
 try{
  await f.key('session:b','ArrowUp');await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  await act(async()=>t.mock.timers.tick(10001));
  let read;await act(async()=>{read=refreshButton(f).props.onClick()});
  if(outcome!=='rejected')await act(async()=>t.mock.timers.tick(10001));
  await read;assert.equal(reads,1);assert.equal(f.calls.length,1);assert.equal(f.listeners.size,0);
  assert.match(JSON.stringify(f.root.toJSON()),outcome==='rejected'?/Read unavailable/:/No pin command was repeated/);
  assert.ok(refreshButton(f));assert.equal(f.busy('session:b'),true);
 }finally{t.mock.timers.reset();await f.close()}
});

test('refresh waits for a cold page after its HTTP response without a second read',async t=>{
 const f=await fixture();let reads=0,read;
 f.host.refreshResources=async()=>{reads++};
 t.mock.timers.enable({apis:['setTimeout']});
 try{
  await f.key('session:b','ArrowUp');await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  await act(async()=>t.mock.timers.tick(10001));await act(async()=>{read=refreshButton(f).props.onClick()});
  assert.equal(reads,1);assert.equal(f.listeners.size,1);assert.match(JSON.stringify(f.root.toJSON()),/Reading pins/);
  await f.update({pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)},navigationResourcePending:{}});
  await read;assert.equal(reads,1);assert.equal(f.calls.length,1);assert.equal(f.listeners.size,0);
  assert.equal(f.root.root.findAllByProps({role:'alert'}).length,0);
 }finally{t.mock.timers.reset();await f.close()}
});

test('a delayed pre-save read cannot confirm an old coherent order',async t=>{
 const f=await fixture();let release,read;
 f.host.refreshResources=()=>new Promise(resolve=>{release=resolve});
 t.mock.timers.enable({apis:['setTimeout']});
 try{
  await f.key('session:b','ArrowUp');await act(async()=>t.mock.timers.tick(10001));
  await act(async()=>{read=refreshButton(f).props.onClick()});
  await act(async()=>release(f.current));
  assert.equal(f.listeners.size,1);assert.deepEqual(f.order(),ids);
  await act(async()=>t.mock.timers.tick(10001));await read;
  assert.match(JSON.stringify(f.root.toJSON()),/Could not confirm the saved pin order/);assert.equal(f.calls.length,1);
 }finally{t.mock.timers.reset();await f.close()}
});

test('external unpin after a timeout retires the old saved-order recovery',async t=>{
 const f=await fixture();let reads=0;
 f.host.refreshResources=async()=>{reads++};
 t.mock.timers.enable({apis:['setTimeout']});
 try{
  await f.key('session:b','ArrowUp');await act(async()=>t.mock.timers.tick(10001));
  await f.update({pinnedItemIds:['session:a'],pinnedNavigation:{items:[item('session:a')],total:1,index:0,pages:1}});
  await act(async()=>refreshButton(f).props.onClick());
  assert.match(JSON.stringify(f.root.toJSON()),/Pins changed elsewhere/);
  assert.equal(refreshButton(f),undefined);assert.equal(f.listeners.size,0);assert.equal(f.calls.length,1);assert.equal(reads,0);
  assert.deepEqual(f.order(),['session:a']);
 }finally{t.mock.timers.reset();await f.close()}
});

test('two different moves before the first receipt and readback preserve both intents',async()=>{
 let release;const receipt=new Promise(resolve=>{release=resolve});let count=0;
 const all=['session:a','session:b','session:c'],first=['session:b','session:a','session:c'],second=['session:b','session:c','session:a'];
 const f=await fixture(()=>++count===1?receipt:Promise.resolve({accepted:true}),{pinnedItemIds:all,pinnedNavigation:{items:all.map(item),total:3,index:0,pages:1}});
 try{
  await f.key('session:a','ArrowDown');await f.key('session:c','ArrowUp');
  assert.deepEqual(f.order(),second);assert.equal(f.calls.length,1);
  await act(async()=>release({accepted:true}));assert.equal(f.calls.length,1);
  await f.update({pinnedItemIds:first,pinnedNavigation:{...f.current.pinnedNavigation,items:first.map(item)}});
  assert.deepEqual(f.order(),second);assert.deepEqual(f.calls[1],['navigation.pinOrder',{ids:second}]);
  await f.update({pinnedItemIds:second,pinnedNavigation:{...f.current.pinnedNavigation,items:second.map(item)}});
  assert.deepEqual(f.order(),second);assert.equal(f.calls.length,2);assert.equal(f.listeners.size,0);
 }finally{await f.close()}
});

for(const error of ['HTTP 409 rejected','Outcome unknown'])test(`queued different move is not sent after ${error}`,async()=>{
 let reject;const receipt=new Promise((_,no)=>{reject=no});
 const all=['session:a','session:b','session:c'];
 const f=await fixture(()=>receipt,{pinnedItemIds:all,pinnedNavigation:{items:all.map(item),total:3,index:0,pages:1}});
 try{
  await f.key('session:a','ArrowDown');await f.key('session:c','ArrowUp');assert.equal(f.calls.length,1);
  await act(async()=>reject(Error(error)));
  assert.equal(f.calls.length,1);assert.deepEqual(f.order(),all);assert.equal(f.listeners.size,0);
  assert.match(JSON.stringify(f.root.toJSON()),/Later queued moves were not sent/);
 }finally{await f.close()}
});

test('a stalled save bounds queued gestures with visible backpressure',async()=>{
 let reject;const f=await fixture(()=>new Promise((_,no)=>{reject=no}));
 try{
  await f.key('session:b','ArrowUp');
  for(let index=0;index<32;index++)await f.key('session:b',index%2===0?'ArrowDown':'ArrowUp');
  const preview=f.order();await f.key('session:b','ArrowDown');
  assert.deepEqual(f.order(),preview);assert.equal(f.calls.length,1);
  assert.equal(f.busy('session:b'),true);assert.match(JSON.stringify(f.root.toJSON()),/queue is full/);
  await act(async()=>reject(Error('Outcome unknown')));
  assert.equal(f.calls.length,1);assert.deepEqual(f.order(),ids);assert.equal(f.listeners.size,0);
 }finally{await f.close()}
});
