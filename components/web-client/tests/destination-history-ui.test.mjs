import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DestinationHistory}=await vite.ssrLoadModule('/src/destination-history.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalDocument=globalThis.document;
globalThis.document={addEventListener(){},removeEventListener(){},activeElement:null};
test.after(()=>{globalThis.document=originalDocument;return vite.close()});
const history={ready:true,pending:false,entries:[{key:'a',kind:'chat',id:'a',label:'Alpha',workspaceLabel:'Project'},{key:'b',kind:'chat',id:'b',label:'Beta',workspaceLabel:'Project'}],cursor:1,detached:false,backKey:'a',forwardKey:null,memoryOnly:false};
history.recent=[history.entries[1],history.entries[0]];history.current=history.entries[1];
const element=(props={})=>React.createElement(DestinationHistory,{history,onVisit(){},onRecent(){},...props});
const label=(root,name)=>root.root.findByProps({'aria-label':name});

test('header always has explicit controls, with no remembered-chat return label',()=>{
 const html=renderToStaticMarkup(element());
 assert.match(html,/Back in navigation history/);assert.match(html,/Forward in navigation history/);assert.match(html,/Recent places/);
 assert.doesNotMatch(html,/Back to chat|Working|Stop|Alpha|Beta/);
 assert.match(renderToStaticMarkup(element({history:null})),/disabled/);
});

test('one private recent menu opens by click; selection uses stable place identity, not visit key',async()=>{
 let root;const visits=[];let focus=0;
 await act(async()=>{root=create(element({onRecent:key=>visits.push(key),onVisit(){assert.fail('A recent place is not a chronological jump')}}))});
 await act(async()=>label(root,'Recent places').props.onClick({currentTarget:{focus(){focus++}}}));
 assert.equal(root.root.findAllByProps({role:'menu'}).length,1);
 assert.equal(root.root.findByProps({role:'menu'}).props['data-private'],'true');
 const items=root.root.findAllByProps({role:'menuitem'});assert.equal(items[0].props['aria-current'],'page');assert.equal(items[0].props.disabled,true);
 assert.equal(items[0].props['data-view-label'],'Private navigation destination');
 assert.equal(items[1].props['data-visit-key'],undefined);
 await act(async()=>items[1].props.onClick());assert.deepEqual(visits,['["chat","a"]']);assert.equal(focus,1);
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>root.unmount());
});

test('mouse hold opens one menu and consumes the follow-up click; touch does not hold',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let root;const visits=[];
 await act(async()=>{root=create(element({onVisit:key=>visits.push(key)}))});
 const button=()=>label(root,'Back in navigation history');
 const event={pointerType:'mouse',button:0,clientX:10,clientY:10,currentTarget:{focus(){}}};
 await act(async()=>{button().props.onPointerDown(event);t.mock.timers.tick(449)});
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>t.mock.timers.tick(1));assert.equal(root.root.findAllByProps({role:'menu'}).length,1);
 await act(async()=>{button().props.onPointerUp();button().props.onClick({preventDefault(){}})});assert.deepEqual(visits,[]);
 await act(async()=>root.unmount());
 await act(async()=>{root=create(element({onVisit:key=>visits.push(key)}))});
 await act(async()=>{button().props.onPointerDown({...event,pointerType:'touch'});t.mock.timers.tick(500)});
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>button().props.onClick({preventDefault(){}}));assert.deepEqual(visits,['a']);
 await act(async()=>root.unmount());t.mock.timers.reset();
});

test('pointer movement cancels hold and pending state disables all controls',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let root;
 await act(async()=>{root=create(element())});
 const button=label(root,'Back in navigation history');
 await act(async()=>{button.props.onPointerDown({pointerType:'pen',button:0,clientX:0,clientY:0,currentTarget:{}});button.props.onPointerMove({clientX:20,clientY:0});t.mock.timers.tick(500)});
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>root.update(element({history:{...history,pending:true}})));
 assert.ok(root.root.findAllByType('button').every(button=>button.props.disabled));
 await act(async()=>root.unmount());t.mock.timers.reset();
});

test('Back and Forward step menus keep repeated visit keys and direction; keyboard and context menu use the same steps',async()=>{
 const entries=[{key:'a1',kind:'chat',id:'a',label:'Same'},{key:'b1',kind:'chat',id:'b',label:'Same'},{key:'a2',kind:'chat',id:'a',label:'Same'},{key:'c1',kind:'chat',id:'c',label:'Gamma'}];
 const state={...history,entries,cursor:2,current:entries[2],backKey:'b1',forwardKey:'c1',recent:[entries[2],entries[3],entries[1]]};
 let root,focused=0;const visits=[];
 await act(async()=>{root=create(element({history:state,onVisit:key=>visits.push(key)}))});
 const opener={disabled:false,dataset:{historyDirection:'back'},focus(){focused++}};
 await act(async()=>root.root.findByProps({role:'group'}).props.onKeyDown({key:'ArrowDown',target:{closest:()=>opener},preventDefault(){}}));
 assert.equal(root.root.findByProps({role:'menu'}).props['aria-label'],'Back steps');
 assert.deepEqual(root.root.findAllByProps({role:'menuitem'}).map(item=>item.props['data-visit-key']),['b1','a1']);
 await act(async()=>root.root.findByProps({role:'group'}).props.onKeyDown({key:'Escape',preventDefault(){},stopPropagation(){}}));
 assert.equal(focused,1);
 await act(async()=>label(root,'Forward in navigation history').props.onContextMenu({currentTarget:opener,preventDefault(){}}));
 assert.equal(root.root.findByProps({role:'menu'}).props['aria-label'],'Forward steps');
 const rows=root.root.findAllByProps({role:'menuitem'});assert.equal(rows.length,1);
 await act(async()=>rows[0].props.onClick());assert.deepEqual(visits,['c1']);
 await act(async()=>root.update(element({history:{...state,cursor:3,current:entries[3],backKey:'a2',forwardKey:null}})));
 await act(async()=>label(root,'Back in navigation history').props.onContextMenu({currentTarget:opener,preventDefault(){}}));
 assert.deepEqual(root.root.findAllByProps({role:'menuitem'}).map(item=>item.props['data-visit-key']),['a2','b1','a1']);
 await act(async()=>root.unmount());
});

test('hold cancellation, busy closure and disabled context actions do not invoke selection',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let root;
 await act(async()=>{root=create(element({onVisit(){assert.fail('cancelled hold must not navigate')}}))});
 const button=()=>label(root,'Back in navigation history'),event={pointerType:'mouse',button:0,clientX:1,clientY:1,currentTarget:{focus(){}}};
 for(const handler of ['onPointerLeave','onPointerCancel']){
  await act(async()=>{button().props.onPointerDown(event);button().props[handler]();t.mock.timers.tick(500)});
  assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 }
 await act(async()=>button().props.onContextMenu({...event,preventDefault(){}}));
 assert.equal(root.root.findAllByProps({role:'menu'}).length,1);
 await act(async()=>root.update(element({history:{...history,pending:true}})));
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>button().props.onContextMenu({...event,preventDefault(){assert.fail('busy menu must not open')}}));
 await act(async()=>root.unmount());t.mock.timers.reset();
});

test('touch context menu stays excluded while a later keyboard context menu remains usable',async()=>{
 let root;const event={pointerType:'touch',button:0,currentTarget:{focus(){}}};
 await act(async()=>{root=create(element())});
 const button=()=>label(root,'Back in navigation history');
 await act(async()=>button().props.onPointerDown(event));
 await act(async()=>button().props.onContextMenu({...event,nativeEvent:{pointerType:'touch'},preventDefault(){assert.fail('Touch must keep native behavior')}}));
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>root.root.findByProps({role:'group'}).props.onKeyDown({key:'ContextMenu'}));
 await act(async()=>button().props.onContextMenu({currentTarget:event.currentTarget,nativeEvent:{},preventDefault(){}}));
 assert.equal(root.root.findByProps({role:'menu'}).props['aria-label'],'Back steps');
 await act(async()=>root.unmount());
});
