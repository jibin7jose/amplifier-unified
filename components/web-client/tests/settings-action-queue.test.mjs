import test from 'node:test';
import assert from 'node:assert/strict';
import {createSettingsActionQueue,settingsDraftKey,isProviderCatalogRead,isDraftDefaultsRead,isAppearanceRead} from '../src/settings-action-queue.js';

const gate=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
const draft=model=>({patch:{providerEditor:{id:'provider',model}}});

test('only complete settings snapshots without explicit command semantics are replaceable',()=>{
 assert.ok(settingsDraftKey('view.update',draft('fast')));
 for(const field of ['aiConnectionEditor','composerModel','composerBundle','bundleDefaultsDraft'])assert.ok(settingsDraftKey('view.update',{patch:{[field]:{model:'fast'}}}));
 for(const meta of [{id:'retry-id'},{expectedRevision:0},{signal:new AbortController().signal},{pendingViewToken:Symbol()}])assert.equal(settingsDraftKey('view.update',draft('fast'),meta),null);
 for(const patch of [{draft:'Chat text'},{panel:'settings'},{providerEditor:{},panel:'settings'},{providerEditor:null},{providerEditor:[]}])assert.equal(settingsDraftKey('view.update',{patch}),null);
 assert.equal(settingsDraftKey('providers.save',draft('fast')),null);
 assert.notEqual(settingsDraftKey('view.update',{...draft('fast'),sessionId:'a'}),settingsDraftKey('view.update',{...draft('fast'),sessionId:'b'}));
});

test('queued typing sends the latest complete draft before save and settles every caller',async()=>{
 const enqueue=createSettingsActionQueue(),blocked=gate(),queue={current:blocked.promise},calls=[],settled=[];
 const requests=Array.from({length:20},(_,i)=>enqueue(queue,async()=>{calls.push(i);return i},'editor').then(result=>settled.push([i,result])));
 const save=enqueue(queue,async()=>calls.push('save'));
 assert.deepEqual(calls,[]);
 blocked.resolve();await Promise.all([...requests,save]);
 assert.deepEqual(calls,[19,'save']);assert.equal(settled.length,20);assert.ok(settled.every(([,result])=>result===19));
});

test('in-flight drafts and actions on other lanes are barriers',async()=>{
 const enqueue=createSettingsActionQueue(),first=gate(),queue={current:Promise.resolve()},other={current:Promise.resolve()},calls=[];
 const a=enqueue(queue,async()=>{calls.push('first');await first.promise},'editor');
 await Promise.resolve();
 const b=enqueue(queue,async()=>calls.push('second'),'editor');
 const navigation=enqueue(other,async()=>calls.push('navigation'));
 const c=enqueue(queue,async()=>calls.push('third'),'editor');
 await navigation;first.resolve();await Promise.all([a,b,c]);
 assert.deepEqual(calls,['first','navigation','second','third']);
});

test('save keeps separate snapshots on either side of the durable action',async()=>{
 const enqueue=createSettingsActionQueue(),queue={current:Promise.resolve()},calls=[];
 const promises=[enqueue(queue,async()=>calls.push('before'),'editor'),enqueue(queue,async()=>calls.push('save')),enqueue(queue,async()=>calls.push('after'),'editor')];
 await Promise.all(promises);assert.deepEqual(calls,['before','save','after']);
});

test('all replaced callers see failure and later commands still run',async()=>{
 const enqueue=createSettingsActionQueue(),queue={current:Promise.resolve()},failure=new Error('Rejected draft');
 const first=enqueue(queue,async()=>assert.fail('Replaced draft ran'),'editor');
 const second=enqueue(queue,async()=>{throw failure},'editor');
 const save=enqueue(queue,async()=>'saved');
 const results=await Promise.allSettled([first,second,save]);
 assert.equal(results[0].reason,failure);assert.equal(results[1].reason,failure);assert.equal(results[2].value,'saved');
});

test('catalog lookups observe earlier writes without holding up later saves',async()=>{
 const enqueue=createSettingsActionQueue(),commands={current:Promise.resolve()},reads={current:Promise.resolve()},writing=gate(),reading=gate(),calls=[];
 const first=enqueue(commands,async()=>{calls.push('write');await writing.promise});
 const read=enqueue(reads,async()=>{calls.push('read');await reading.promise},null,commands.current);
 const save=enqueue(commands,async()=>calls.push('save'));
 await Promise.resolve();assert.deepEqual(calls,['write']);
 writing.resolve();await save;assert.ok(calls.includes('save'));
 // No timers or network timeout are needed before this save can complete.
 reading.resolve();await Promise.all([first,read]);assert.ok(calls.indexOf('read')>calls.indexOf('write'));
 for(const action of ['providers.list','providers.models','providers.schema'])assert.equal(isProviderCatalogRead(action),true);
 for(const action of ['providers.save','runtime.control','bundle.switch','providers.credentials'])assert.equal(isProviderCatalogRead(action),false);
 assert.equal(isProviderCatalogRead('providers.list',{expectedRevision:1}),false,'Revision-conditional actions retain normal ordering');
});

test('draft defaults are independent reads but retain revision-conditional ordering',()=>{
 assert.equal(isDraftDefaultsRead('configuration.defaults'),true);
 assert.equal(isDraftDefaultsRead('configuration.defaults',{expectedRevision:0}),false);
 assert.equal(isDraftDefaultsRead('session.create'),false);
});
test('only unconditional appearance list/read bypass the command tail',()=>{
 for(const action of ['theme.list','theme.read']){
  assert.equal(isAppearanceRead(action),true);
  assert.equal(isAppearanceRead(action,{expectedRevision:0}),false);
 }
 for(const action of ['theme.apply','theme.preview','theme.save','theme.update','theme.delete','theme.import','theme.reset','theme.export','theme.revert'])
  assert.equal(isAppearanceRead(action),false,action+' retains its original lane');
});
test('independent appearance reads observe preceding writes but do not block Stop, approval, writes or each other',async()=>{
 const enqueue=createSettingsActionQueue(),commands={current:Promise.resolve()},writing=gate(),reading=gate(),calls=[];
 const prior=enqueue(commands,async()=>{calls.push('write-before');await writing.promise});
 const read=enqueue({current:Promise.resolve()},async()=>{calls.push('theme.list');await reading.promise},null,commands.current);
 const sibling=enqueue({current:Promise.resolve()},async()=>calls.push('theme.read'),null,commands.current);
 const stop=enqueue(commands,async()=>calls.push('conversation.stop'));
 const approval=enqueue(commands,async()=>calls.push('approval.respond'));
 const later=enqueue(commands,async()=>calls.push('write-after'));
 await Promise.resolve();assert.deepEqual(calls,['write-before']);
 writing.resolve();await Promise.all([prior,sibling,stop,approval,later]);
 assert.ok(calls.indexOf('theme.list')>calls.indexOf('write-before'));
 assert.ok(calls.indexOf('theme.read')>calls.indexOf('write-before'));
 assert.deepEqual(calls.filter(value=>!value.startsWith('theme.')),
  ['write-before','conversation.stop','approval.respond','write-after']);
 reading.resolve();await read;assert.equal(calls.filter(value=>value==='theme.list').length,1);
});
