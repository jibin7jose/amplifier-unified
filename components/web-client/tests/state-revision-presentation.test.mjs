import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {applyStateDelta,onlyStateRevisionChanged} from '../src/state-transport.js';
import {createPendingView} from '../src/pending-view.js';
import {createConversationNavigation} from '../src/conversation-navigation.js';
import {createDestinationHistory} from '../src/destination-history.js';

// Exercise the actual acceptance and command-fence boundary, without mounting
// unrelated UI. The built-browser check separately measures DOM/render effects.
const source=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
const boundary=source.slice(source.indexOf(' const acceptState=useCallback('),source.indexOf(' const dispatch=useCallback('));
function harness(){
 const painted=[],observed=[],timers=new Map(),requests=[],effects=[],listeners=new Map();
 const context={useCallback:fn=>fn,useEffect:fn=>effects.push(fn),onlyStateRevisionChanged,document:{hidden:false,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
  serverState:{current:null},latest:{current:null},effectHandler:{current:()=>{}},
  seenEffects:{current:new Set()},notificationMessages:()=>[],lastNotify:{current:new Set()},
  messagesPane:{current:null},historyScrollAnchor:{current:null},stickToBottom:{current:true},
  pendingView:{current:createPendingView()},conversationNavigation:{current:createConversationNavigation()},
  destinationHistory:{current:createDestinationHistory({storage:()=>({getItem:()=>null,setItem(){}}),key:()=>String(observed.length)})},
  stateWaiters:{current:new Set()},stateRecovery:{current:null},stateListeners:{current:new Set([state=>observed.push(state)])},
  setState:state=>painted.push(state),setTimeout:fn=>{const id=Symbol();timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id),
  request:path=>{requests.push(path);throw Error('Unexpected recovery request')}};
 const api=runInNewContext(`${boundary}\n({acceptState,awaitState})`,context);
 return {...context,...api,painted,observed,timers,requests,effects,listeners};
}
function baseline(){return {revision:10,client:{hostInstanceId:'host-a',historyScope:'scope-a'},view:{workSurface:'chat',draft:''},selectedSessionId:'one',sessions:[{id:'one',title:'One',messages:[]}],workspaces:[]}}
const advance=(state,revision=state.revision+1,changes=[])=>applyStateDelta(state,{baseRevision:state.revision,revision,changes:[{path:['revision'],value:revision},...changes]});

test('revision-only state keeps causal readback, subscribers and pending command completion without painting',async()=>{
 const h=harness(),initial=baseline();h.acceptState(initial);const waiting=h.awaitState({hostInstanceId:'host-a',stateRevision:11});
 assert.equal(h.timers.size,1);assert.equal(h.stateWaiters.current.size,1);
 const next=advance(initial);h.acceptState(next);await waiting;
 assert.equal(h.painted.length,1);assert.equal(h.serverState.current,next);assert.equal(h.latest.current,next);
 assert.equal(h.observed.at(-1).revision,11);assert.equal(h.observed.length,2);
 assert.equal(h.timers.size,0);assert.equal(h.stateWaiters.current.size,0);assert.equal(h.requests.length,0);
 const material=advance(next,12,[{path:['sessions',0,'title'],value:'Changed'}]);h.acceptState(material);
 assert.equal(h.painted.length,2);assert.equal(h.painted.at(-1).sessions[0].title,'Changed');
 assert.throws(()=>applyStateDelta(next,{baseRevision:10,revision:12,changes:[]}));
 assert.equal(h.serverState.current,material);
});

test('optimistic draft and navigation remain in latest readback until their owners settle',()=>{
 const h=harness(),initial=baseline();initial.sessions.push({id:'two',title:'Two',messages:[]});h.acceptState(initial);
 const draft=h.pendingView.current.add({draft:'Local draft'},'one');
 h.acceptState(advance(initial));assert.equal(h.pendingView.current.apply(h.latest.current).view.draft,'Local draft');
 h.pendingView.current.settle(draft);
 const navigation=h.conversationNavigation.current.begin(h.latest.current,'two');
 h.acceptState(advance(h.serverState.current));assert.equal(h.latest.current.selectedSessionId,'two');
 assert.equal(h.latest.current.navigationPending,true);assert.equal(h.painted.length,1);
 h.conversationNavigation.current.settle(navigation);
 const selected=advance(h.serverState.current,13,[{path:['selectedSessionId'],value:'two'}]);h.acceptState(selected);
 assert.equal(h.painted.at(-1).selectedSessionId,'two');assert.equal(h.latest.current.navigationPending,undefined);
});

test('navigation history can settle an accepted receipt on a revision-only observation',async()=>{
 const h=harness(),initial=baseline();initial.view.workSurface='home';h.acceptState(initial);
 const chat=advance(initial,11,[{path:['view','workSurface'],value:'chat'}]);h.acceptState(chat);
 const history=h.destinationHistory.current,visit=history.begin(history.snapshot().backKey);assert.ok(visit);
 const home=advance(chat,12,[{path:['view','workSurface'],value:'home'}]);h.acceptState(home);
 history.finish({accepted:true,hostInstanceId:'host-a',stateRevision:13});assert.equal(history.snapshot().pending,true);
 h.acceptState(advance(home));assert.equal(await visit.confirmed,true);assert.equal(history.snapshot().pending,false);
 assert.equal(history.snapshot().current.kind,'home');
});

test('fresh/reconnected baselines render, reject late old revisions, and cannot settle another host receipt',async()=>{
 const h=harness(),initial=baseline();h.acceptState(initial);h.acceptState(advance(initial));
 const reconnect=JSON.parse(JSON.stringify(h.serverState.current));h.acceptState(reconnect,{baseline:true});assert.equal(h.painted.length,2);
 h.acceptState(initial);assert.equal(h.serverState.current,reconnect);assert.equal(h.painted.length,2);
 h.acceptState(reconnect,{baseline:true});assert.equal(h.painted.length,3,'Explicit baselines always reach presentation');
 const waiting=h.awaitState({hostInstanceId:'host-b',stateRevision:1});h.acceptState(advance(reconnect));assert.equal(h.stateWaiters.current.size,1);
 const restarted={...baseline(),revision:1,client:{hostInstanceId:'host-b',historyScope:'scope-a'}};h.acceptState(restarted,{baseline:true});await waiting;
 assert.equal(h.painted.at(-1),restarted);assert.equal(h.stateWaiters.current.size,0);
 const next=advance(restarted);assert.equal(next.revision,2);h.acceptState(next);assert.equal(h.latest.current,next);
});

test('removed, newly undefined, nested and host fields are material; diagnostic panel displays revision',()=>{
 const initial=baseline();assert.equal(onlyStateRevisionChanged(initial,advance(initial)),true);
 for(const next of [{...initial,added:undefined},Object.fromEntries(Object.entries(initial).filter(([key])=>key!=='workspaces')),
  {...initial,view:{...initial.view}},{...initial,client:{...initial.client,hostInstanceId:'host-b'}},
  {...initial,revision:9},{...initial,revision:NaN}])assert.equal(onlyStateRevisionChanged(initial,next),false);
 assert.equal(onlyStateRevisionChanged(null,initial),false);
 const h=harness();initial.view.panel='agent';h.acceptState(initial);h.acceptState(advance(initial));assert.equal(h.painted.length,2);
});


test('hidden observations keep current readback and restore it on visibility without replay',()=>{
 const h=harness(),initial=baseline();h.acceptState(initial);const cleanup=h.effects[0]();h.document.hidden=true;
 const pending=h.pendingView.current.add({draft:'Kept while hidden'},'one');
 h.acceptState(advance(initial));assert.equal(h.painted.length,1);assert.equal(h.latest.current.revision,11);
 h.document.hidden=false;h.listeners.get('visibilitychange')();
 assert.equal(h.painted.at(-1).revision,11);assert.equal(h.painted.at(-1).view.draft,'Kept while hidden');
 h.pendingView.current.settle(pending);cleanup();assert.equal(h.listeners.size,0);assert.equal(h.requests.length,0);
});
