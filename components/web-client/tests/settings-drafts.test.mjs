import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct,useLayoutEffect} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SettingsDraftProvider,useSettingsDraft,useSettingsDrafts,useGuardedSettingsPanel,useGuardedSettingsDraft}=await server.ssrLoadModule('/src/settings-drafts.jsx');
const {useSettingsHistory}=await server.ssrLoadModule('/src/settings-layout.jsx');
const {settingsTrail,settingsBaseNavigation}=await server.ssrLoadModule('/src/settings-mobile.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const handlers=new Map();globalThis.window={addEventListener:(key,fn)=>handlers.set(key,fn),removeEventListener:key=>handlers.delete(key)};
test.after(()=>server.close());
function Draft({entry}){useSettingsDraft('connection',entry);return null}
let guard;
function Fixture({entry}){guard=useSettingsDrafts();return React.createElement(SettingsDraftProvider,{value:guard.context},React.createElement(Draft,{entry}),guard.dialog)}
const click=(root,label)=>root.root.findAllByType('button').find(button=>button.children.includes(label)).props.onClick();
test('a committed unsaved settings edit blocks navigation before passive effects run',async()=>{
 const observations=[];let root;
 function CommitProbe({dirty}){
  const navigation=useSettingsDrafts();
  useLayoutEffect(()=>{observations.push({dirty,blocked:navigation.hasUnsaved()})});
  return React.createElement(SettingsDraftProvider,{value:navigation.context},
   React.createElement(Draft,{entry:{label:'Provider configuration',dirty}}));
 }
 await renderAct(async()=>root=create(React.createElement(CommitProbe,{dirty:false})));
 observations.length=0;
 await renderAct(async()=>root.update(React.createElement(CommitProbe,{dirty:true})));
 assert.ok(observations.length>0);
 assert.ok(observations.every(row=>row.blocked),'The newly committed edit must already be protected when navigation can read it');
 await renderAct(async()=>root.unmount());
});
test('closing dirty connection setup requires review, keep, or explicit discard; secrets stay out of the prompt',async()=>{
 const calls=[],entry={label:'AI connection setup',dirty:true,review:()=>calls.push('review'),discard:()=>calls.push('discard')};let root,pending;
 await renderAct(async()=>root=create(React.createElement(Fixture,{entry})));
 assert.equal(guard.count,1);assert.ok(handlers.has('beforeunload'));
 await renderAct(async()=>{pending=guard.confirmClose()});
 assert.equal(root.root.findByProps({role:'alertdialog'}).props['aria-modal'],'true');
 await renderAct(async()=>click(root,'Keep settings open'));assert.equal(await pending,false);assert.deepEqual(calls,[]);
 await renderAct(async()=>{pending=guard.confirmClose()});
 await renderAct(async()=>click(root,'Review and save'));assert.equal(await pending,false);assert.deepEqual(calls,['review']);
 await renderAct(async()=>{pending=guard.confirmClose();assert.equal(guard.confirmClose(),pending)});
 await renderAct(async()=>click(root,'Discard changes'));assert.equal(await pending,true);assert.deepEqual(calls,['review','discard']);
 await renderAct(async()=>root.update(React.createElement(Fixture,{entry:{...entry,dirty:false}})));
 assert.equal(guard.count,0);assert.equal(await guard.confirmClose(),true);assert.equal(handlers.has('beforeunload'),false);
 await renderAct(async()=>root.unmount());
});

test('incoming shared panel changes retain the editor until confirmed, including a newer request',async()=>{
 let visible,finish,dirty=true,root;const restored=[],navigation={current:{hasUnsaved:()=>dirty,confirmClose:()=>new Promise(resolve=>{finish=resolve})}};
 function Panel({requested}){visible=useGuardedSettingsPanel(requested,navigation,panel=>restored.push(panel));return React.createElement('div',{'data-panel':visible})}
 await renderAct(async()=>root=create(React.createElement(Panel,{requested:'settings'})));
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:null})));
 assert.equal(visible,'settings');
 await renderAct(async()=>finish(false));assert.deepEqual(restored,['settings']);assert.equal(visible,'settings');
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:'settings'})));
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:null})));
 const stale=finish;
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:'runtime'})));
 await renderAct(async()=>stale(true));assert.equal(visible,'settings');
 await renderAct(async()=>finish(true));assert.equal(visible,'runtime');
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:'settings'})));
 dirty=false;await renderAct(async()=>root.update(React.createElement(Panel,{requested:null})));
 assert.equal(visible,null);await renderAct(async()=>root.unmount());
});

test('approved editor survives the discard reset until the selected target is restored',async()=>{
 let root,visible,finish,restore;const restored=[];
 const registry={hasUnsaved:()=>true,confirmClose:()=>new Promise(resolve=>{finish=resolve})};
 function Editor({requested}){visible=useGuardedSettingsDraft(requested,requested.id,value=>{restored.push(value);return new Promise(resolve=>{restore=resolve})});return null}
 const render=requested=>React.createElement(SettingsDraftProvider,{value:registry},React.createElement(Editor,{requested}));
 try{
  await renderAct(async()=>{root=create(render({id:'old'}))});
  await renderAct(async()=>root.update(render({id:'new'})));assert.equal(visible.id,'old');
  await renderAct(async()=>finish(true));assert.equal(visible.id,'new');
  const initialFinish=finish;
  await renderAct(async()=>root.update(render({id:'reset-old'})));assert.equal(visible.id,'new');assert.equal(finish,initialFinish,'discard reset must not prompt twice');
  await renderAct(async()=>{root.update(render({id:'new'}));restore()});assert.equal(visible.id,'new');assert.deepEqual(restored,[{id:'new'}]);
 }finally{await renderAct(async()=>root?.unmount())}
});

// Exercise the actual history hook, including its programmatic trail shrink.
// Browser traversal is queued so a test chooses its delivery boundary, not a sleep.
async function settingsHistoryFixture(){
 const previous={history:globalThis.history,requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame};
 const entries=[{}],traversals=[],actions=[],navigation={current:null};
 let index=0,root,controller,guard,view={...settingsBaseNavigation('providers'),providerEditor:{detailOpen:true}},dirty=true,privateValue='PRIVATE-SETTINGS-DRAFT',closed=0;
 globalThis.requestAnimationFrame=()=>1;globalThis.cancelAnimationFrame=()=>{};
 globalThis.history={
  get state(){return entries[index]},
  pushState(value){entries.splice(++index);entries[index]=value},
  replaceState(value){entries[index]=value},
  go(delta){traversals.push(index+delta)},
  back(){this.go(-1)},
 };
 function Frame(){
  guard=useSettingsDrafts();
  controller=useSettingsHistory({compact:true,trail:settingsTrail(view),view,
   act:(action,args)=>actions.push({action,args}),close:()=>closed++,body:{current:null},navigationRef:navigation,
   confirmClose:guard.confirmClose,hasUnsaved:guard.hasUnsaved});
  const entry={label:'Provider configuration',dirty,review:()=>{},discard:()=>{
   privateValue='';dirty=false;view={...view,providerEditor:{detailOpen:false}};
   root.update(React.createElement(Frame));
  }};
  return React.createElement(SettingsDraftProvider,{value:guard.context},React.createElement(Draft,{entry}),guard.dialog);
 }
 await renderAct(async()=>root=create(React.createElement(Frame)));
 const deliver=async target=>{index=target;await renderAct(async()=>{await handlers.get('popstate')?.({state:entries[index]})})};
 return {
  actions,traversals,navigation,entries,
  get index(){return index},get privateValue(){return privateValue},get closed(){return closed},
  get pop(){return handlers.get('popstate')},
  async confirm(choice){let pending;await renderAct(async()=>{pending=navigation.current.confirmClose()});await renderAct(async()=>click(root,choice));return pending},
  async flush(){assert.ok(traversals.length);await deliver(traversals.shift())},
  async popTo(target){await deliver(target)},
  async update(next){view=next;await renderAct(async()=>root.update(React.createElement(Frame)))},
  async clean(){await renderAct(async()=>root.unmount());Object.assign(globalThis,previous)},
 };
}

test('discard trail reconciliation cannot enqueue a Settings reopen after external tab close',async()=>{
 const f=await settingsHistoryFixture();
 try{
  assert.equal(f.index,4);
  assert.equal(await f.confirm('Discard changes'),true);
  assert.equal(f.privateValue,'');assert.deepEqual(f.traversals,[3]);
  await f.flush();
  assert.deepEqual(f.actions,[],'Reconciling the already-current parent must not publish a Settings reopen');
  assert.equal(f.index,3);
 }finally{await f.clean()}
});

test('Keep settings open preserves private memory and genuine Settings Back and Forward still navigate',async()=>{
 const f=await settingsHistoryFixture();
 try{
  assert.equal(await f.confirm('Keep settings open'),false);
  assert.equal(f.privateValue,'PRIVATE-SETTINGS-DRAFT');assert.deepEqual(f.traversals,[]);
  assert.equal(JSON.stringify(f.entries).includes(f.privateValue),false);
  await f.popTo(3);assert.equal(f.actions.length,1);assert.equal(f.actions[0].args.patch.providerEditor.detailOpen,false);
  await f.update(f.actions[0].args.patch);
  await f.popTo(4);assert.equal(f.actions.length,2);assert.equal(f.actions[1].args.patch.providerEditor.detailOpen,true);
  assert.equal(f.privateValue,'PRIVATE-SETTINGS-DRAFT');
 }finally{await f.clean()}
});

test('a retired Settings history callback cannot navigate a deliberately reopened lifetime',async()=>{
 const old=await settingsHistoryFixture(),pop=old.pop,marker={...old.entries[3]};
 await old.clean();
 const fresh=await settingsHistoryFixture();
 try{
  await renderAct(async()=>{await pop({state:marker})});
  assert.deepEqual(old.actions,[],'Retired history must not publish navigation');
  assert.deepEqual(fresh.actions,[]);assert.equal(fresh.privateValue,'PRIVATE-SETTINGS-DRAFT');
  await fresh.popTo(3);assert.equal(fresh.actions.length,1);
 }finally{await fresh.clean()}
});

test('late Settings confirmation cannot restore browser history after unmount',async()=>{
 const f=await settingsHistoryFixture();let pending;
 await renderAct(async()=>{pending=f.pop({state:{}})});
 const entries=JSON.stringify(f.entries);
 await f.clean();await pending;
 assert.equal(JSON.stringify(f.entries),entries,'Retired confirmation must not push Settings entries');
 assert.equal(f.closed,0);assert.deepEqual(f.actions,[]);
});
