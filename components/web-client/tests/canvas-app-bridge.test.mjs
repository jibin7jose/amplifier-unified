import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../../amplifier_web/canvas_app_bridge.js',import.meta.url),'utf8').replace('__CANVAS_ID__','"fixture"');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
 const sent=[],listeners={},parent={postMessage:value=>sent.push(value)};
 const realm={parent,structuredClone,setTimeout,clearTimeout,console,document:{readyState:'complete',querySelectorAll:()=>[],documentElement:{style:{setProperty(){},removeProperty(){}},dataset:{}}},addEventListener:(name,fn)=>{(listeners[name]??=[]).push(fn)},removeEventListener(){},ResizeObserver:class{observe(){} disconnect(){}}};
 realm.window=realm;vm.runInNewContext(source,realm);
 const snapshot=(revision=0,theme={tokens:{accent:'#123456'},scheme:'light'})=>({id:'fixture',app:{revision:1,stateRevision:revision,manifest:{},state:{tab:'draw'}},theme});
 const deliver=value=>listeners.message.forEach(fn=>fn({source:parent,data:{type:'canvas-app-host',id:'fixture',channel:'test',...value}}));
 deliver({snapshot:snapshot()});sent.length=0;
 return {app:realm.canvasApp,sent,deliver,snapshot,input:()=>listeners.input.forEach(fn=>fn())};
}

test('custom edits announce immediately while a previous write is pending',async()=>{
 const f=fixture();const first=f.app.patch({tab:'draw'});await tick();const request=f.sent[0];
 const dirty=f.app.setDirty(true);assert.equal(f.sent.at(-1).op,'editing');assert.equal(f.sent.at(-1).editVersion,1);
 f.deliver({requestId:request.requestId,snapshot:f.snapshot(1)});await first;await tick();
 const next=f.sent.at(-1);assert.equal(next.op,'dirty');assert.equal(next.editVersion,1);
 f.deliver({requestId:next.requestId,snapshot:f.snapshot(1)});await dirty;
});

test('writes capture their edit and explicit commit; later edits stay distinct',async()=>{
 const f=fixture();f.input();const token=f.app.getEditVersion();const patch={note:'first'};
 const saved=f.app.patch(patch,{commit:token});patch.note='mutated';f.app.beginEdit();await tick();
 const request=f.sent.at(-1);assert.equal(request.commit,1);assert.equal(request.editVersion,1);assert.equal(request.args.patch.note,'first');assert.equal(f.app.getEditVersion(),2);
 f.deliver({requestId:request.requestId,snapshot:f.snapshot(1)});await saved;
 const tab=f.app.emit('setTab',{tab:'clock'});await tick();assert.equal(f.sent.at(-1).commit,undefined);
 f.deliver({requestId:f.sent.at(-1).requestId,snapshot:f.snapshot(2)});await tab;
 await assert.rejects(f.app.patch({}, {commit:99}),/existing local edit/);
});

test('unchanged snapshots are quiet and pending writes defer subscriber redraws while theme CSS still updates',async()=>{
 const f=fixture(),updates=[];f.app.subscribe(s=>updates.push(s));f.deliver({snapshot:f.snapshot()});assert.equal(updates.length,0);
 const pending=f.app.emit('setTab',{tab:'clock'});await tick();const request=f.sent.at(-1);
 f.deliver({snapshot:f.snapshot(0,{tokens:{accent:'#abcdef'},scheme:'dark'})});assert.equal(updates.length,0);
 f.deliver({requestId:request.requestId,snapshot:f.snapshot(1,{tokens:{accent:'#abcdef'},scheme:'dark'})});await pending;
 assert.equal(updates.length,1);assert.equal(updates[0].app.stateRevision,1);
 f.deliver({snapshot:f.snapshot(1,{tokens:{accent:'#abcdef'},scheme:'dark'})});assert.equal(updates.length,1);
 f.deliver({snapshot:f.snapshot(1,{tokens:{accent:'#abcdef'},scheme:'dark',reducedMotion:true})});assert.equal(updates.length,2);
});

test('failed operations reject without claiming a save; rendering recovery is explicit',async()=>{
 const f=fixture();const token=f.app.beginEdit(),saved=f.app.patch({note:'kept'},{commit:token});await tick();
 f.deliver({requestId:f.sent.at(-1).requestId,error:'Conflict'});await assert.rejects(saved,/Conflict/);assert.equal(f.app.getEditVersion(),token);
 f.app.reportError('Broken render');assert.equal(f.sent.at(-1).status,'error');f.app.reportReady();assert.equal(f.sent.at(-1).status,'ready');
});

test('canvas sizing skips hidden bounds and avoids repeated bitmap resets',()=>{
 const f=fixture();let width=0,height=0,w=300,h=150,writes=0,draws=0;
 const canvas={getBoundingClientRect:()=>({width,height}),getContext:()=>({setTransform(){}}),get width(){return w},set width(v){w=v;writes++},get height(){return h},set height(v){h=v;writes++}};
 const painter=f.app.observeCanvas(canvas,()=>{draws++});assert.equal(draws,0);assert.equal(writes,0);
 width=600;height=260;painter.redraw();assert.equal(draws,1);assert.equal(writes,2);
 painter.redraw();assert.equal(draws,2);assert.equal(writes,2);
 width=0;height=0;painter.redraw();assert.equal(draws,2);assert.equal(writes,2);
 width=600;height=260;painter.redraw();assert.equal(draws,3);assert.equal(writes,2);
 painter.disconnect();painter.redraw();assert.equal(draws,3);
});
