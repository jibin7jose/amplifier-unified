import test from 'node:test';
import assert from 'node:assert/strict';
import {createPendingView} from '../src/pending-view.js';

test('an empty-composer draft stays scoped when a conversation is selected',()=>{
 const pending=createPendingView();
 pending.add({draft:'Before any conversation'},null);
 assert.equal(pending.apply({selectedSessionId:null,view:{draft:''}}).view.draft,'Before any conversation');
 assert.equal(pending.apply({selectedSessionId:'chat',view:{draft:'This chat'}}).view.draft,'This chat');
});

test('first-conversation binding keeps draft ordering without reviving old edits',()=>{
 const pending=createPendingView(),blank=pending.add({draft:''},null),typed=pending.add({draft:'Next draft'},null);
 pending.bindDraft(blank,'first');pending.bindDraft(typed,'first');
 assert.equal(pending.apply({selectedSessionId:'first',view:{draft:'Old draft'}}).view.draft,'Next draft');
 pending.settle(blank);
 assert.equal(pending.apply({selectedSessionId:'other',view:{draft:'Other draft'}}).view.draft,'Other draft');
 pending.settle(typed);pending.bindDraft(typed,'first');
 assert.equal(pending.apply({selectedSessionId:'first',view:{draft:'Saved draft'}}).view.draft,'Saved draft');
});

test('navigation paints without changing the authoritative snapshot or session data',()=>{
 const pending=createPendingView(),state={revision:1,view:{panel:null},sessions:[{id:'chat',messages:[]}]};
 const token=pending.add({panel:'settings'}),shown=pending.apply(state);
 assert.equal(shown.view.panel,'settings');assert.equal(state.view.panel,null);
 assert.equal(shown.sessions,state.sessions);assert.equal(shown.revision,1);
 pending.settle(token);assert.equal(pending.apply(state),state);
});

test('acknowledging an older navigation keeps a newer choice and unrelated pending fields',()=>{
 const pending=createPendingView(),state={view:{panel:null,settingsSection:'setup'}};
 const open=pending.add({panel:'settings'}),section=pending.add({settingsSection:'maintenance'}),close=pending.add({panel:null});
 pending.settle(open);
 const acknowledged={...state,view:{...state.view,panel:'settings'}};
 assert.deepEqual(pending.apply(acknowledged).view,{panel:null,settingsSection:'maintenance'});
 pending.settle(section);pending.settle(close);
 const final={view:{panel:null,settingsSection:'maintenance'}};assert.equal(pending.apply(final),final);
});

test('failed older patches roll back only themselves, including repeated values',()=>{
 const pending=createPendingView(),state={view:{panel:null,settingsSection:'setup'}};
 const first=pending.add({panel:'settings',settingsSection:'maintenance'}),second=pending.add({panel:'settings'});
 pending.settle(first);
 assert.deepEqual(pending.apply(state).view,{panel:'settings',settingsSection:'setup'});
 pending.settle(second);assert.equal(pending.apply(state),state);
});

test('SSE updates keep pending intent while preserving newer shared data',()=>{
 const pending=createPendingView(),token=pending.add({panel:'settings'});
 const streamed={revision:8,view:{panel:'activity',draft:'Agent draft'},sessions:[{id:'chat',streaming:'New response'}]};
 assert.deepEqual(pending.apply(streamed).view,{panel:'settings',draft:'Agent draft'});
 assert.equal(pending.apply(streamed).sessions,streamed.sessions);
 pending.settle(token);assert.equal(pending.apply(streamed),streamed,'a rejected action returns to the latest shared state');
});

test('matching server values do not acknowledge a still-queued later request',()=>{
 const pending=createPendingView(),older=pending.add({panel:null}),later=pending.add({panel:'settings'});
 assert.equal(pending.apply({view:{panel:'settings'}}).view.panel,'settings');
 pending.settle(older);
 assert.equal(pending.apply({view:{panel:null}}).view.panel,'settings');
 pending.settle(later);assert.equal(pending.apply({view:{panel:null}}).view.panel,null);
});

test('Canvas visibility stays immediate, ordered, and bound to its chat and artifact',()=>{
 const pending=createPendingView(),state={selectedSessionId:'a',client:{hostInstanceId:'host'},canvas:{id:'artifact',open:false},view:{canvasFocused:false}};
 const open=pending.addCanvas(state,true),close=pending.addCanvas(state,false),reopen=pending.addCanvas(state,true);
 assert.equal(pending.apply(state).canvas.open,true);
 assert.equal(pending.apply(state).canvas.visibilityPending,true);
 pending.settle(open);pending.settle(close);
 assert.equal(pending.apply({...state,canvas:{...state.canvas,open:false}}).canvas.open,true);
 assert.equal(pending.apply({...state,canvas:{...state.canvas,open:true}}).canvas.visibilityPending,true,'An SSE value cannot settle a queued visibility action');
 assert.equal(pending.apply({...state,selectedSessionId:'b'}).canvas.open,false);
 assert.equal(pending.apply({...state,selectedSessionId:'b'}).canvas.visibilityPending,undefined);
 assert.equal(pending.apply({...state,canvas:{id:'new',open:false}}).canvas.open,false);
 assert.equal(pending.apply({...state,canvas:{id:'new',open:false}}).canvas.visibilityPending,undefined);
 assert.equal(pending.apply({...state,client:{hostInstanceId:'restarted'}}).canvas.open,false);
 assert.equal(pending.apply({...state,client:{hostInstanceId:'restarted'}}).canvas.visibilityPending,undefined);
 pending.settle(reopen);assert.equal(pending.apply(state).canvas.open,false);
 assert.equal(pending.apply(state).canvas.visibilityPending,undefined);
});

test('pending Canvas preferences cannot leak to the next chat',()=>{
 const pending=createPendingView();pending.add({canvasWidth:720,canvasFocused:true,canvasDraft:{filter:'old'},navWidth:280},'one');
 const state={selectedSessionId:'two',view:{canvasWidth:340,canvasFocused:false}};
 assert.deepEqual(pending.apply(state).view,{canvasWidth:340,canvasFocused:false,navWidth:280});
});
