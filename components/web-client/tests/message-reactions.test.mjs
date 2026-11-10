import test from 'node:test';
import assert from 'node:assert/strict';
import {createReactionController,createReactionRegistry} from '../src/message-reactions.js';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const receipt=(reactions,revision=1)=>({accepted:true,result:{sessionId:'s',messageId:'m',reactionActor:'user',reactions,annotationRevision:revision}});
function harness(options={}){const writes=[],reads=[],saved=[];const controller=createReactionController({sessionId:'s',messageId:'m',clientId:'client',send:row=>new Promise((resolve,reject)=>writes.push({row:{...row},resolve,reject})),read:async row=>{reads.push(row);return {sessionId:'s',messageId:'m',status:'unknown'}},save:rows=>saved.push(structuredClone(rows)),...options});controller.observe({reactions:[],annotationRevision:0});return {controller,writes,reads,saved}}
test('intent is immediate and rapid toggles serialize only the same emoji',async()=>{
 const {controller:c,writes}=harness();c.set('heart',true);assert.deepEqual(c.view().reactions,['heart']);c.set('heart',false);assert.deepEqual(c.view().reactions,[]);c.set('check',true);assert.equal(writes.length,2);
 writes[0].resolve(receipt(['heart']));await tick();assert.equal(writes.length,3);assert.equal(writes[2].row.present,false);assert.notEqual(writes[2].row.id,writes[0].row.id);assert.deepEqual(c.view().reactions,['check']);
 writes[2].resolve(receipt([],3));writes[1].resolve(receipt(['heart','check'],2));await tick();assert.deepEqual(c.view().reactions,[],'Late full-state receipt cannot overwrite a newer revision');
});
test('older rejection rolls back only its change, preserving unrelated/newer intent',async()=>{
 const {controller:c,writes}=harness();c.observe({reactions:['kept'],annotationRevision:1});c.set('heart',true);c.set('check',true);writes[0].reject(Object.assign(Error('Message unavailable'),{status:404}));await tick();assert.deepEqual(c.view().reactions,['kept','check']);assert.match(c.view().error,/unavailable/);assert.equal(c.view().unknown,false);
 writes[1].resolve(receipt(['kept','check'],2));await tick();assert.deepEqual(c.view().reactions,['kept','check']);
});
test('newer same-emoji intent survives old acknowledgement and external snapshot',async()=>{
 const {controller:c,writes}=harness();c.set('heart',true);c.set('heart',false);c.observe({reactions:['heart','peer'],annotationRevision:4});writes[0].resolve(receipt(['heart'],1));await tick();assert.deepEqual(c.view().reactions,['peer']);writes[1].resolve(receipt(['peer'],5));await tick();assert.deepEqual(c.view().reactions,['peer']);
});
test('unknown transport outcome is never replayed; exact receipt read settles current state',async()=>{
 let result={sessionId:'s',messageId:'m',status:'unknown'};const {controller:c,writes}=harness({read:async()=>result});c.set('heart',true);writes[0].reject(Object.assign(Error('Offline'),{unconfirmed:true}));await tick();assert.equal(c.view().unknown,true);await c.reconcile();assert.equal(writes.length,1);assert.equal(c.view().unknown,true);result={sessionId:'s',messageId:'m',status:'accepted',receipt:receipt(['heart']),revision:2,current:{reactions:[]}};await c.reconcile();assert.equal(c.view().unknown,false);assert.deepEqual(c.view().reactions,[]);assert.equal(writes.length,1);
});
test('reload retains uncertainty and reads without writing a saved intent',async()=>{
 const first=harness();first.controller.set('heart',true);first.controller.set('heart',false);
 const recovered=harness({restored:first.saved.at(-1),read:async()=>({sessionId:'s',messageId:'m',status:'accepted',receipt:receipt(['heart']),revision:1,current:{reactions:['heart']}})});
 assert.equal(recovered.controller.view().unknown,true);await recovered.controller.reconcile();assert.equal(recovered.writes.length,0);assert.match(recovered.controller.view().error,/not sent/);assert.deepEqual(recovered.controller.view().reactions,['heart']);
});
test('different messages have independent flights; forged actor receipt stays unknown',async()=>{
 const a=harness(),b=harness();a.controller.set('heart',true);b.controller.set('check',true);assert.equal(a.writes.length,1);assert.equal(b.writes.length,1);a.writes[0].resolve({accepted:true,result:{...receipt(['heart']).result,reactionActor:'assistant'}});await tick();assert.equal(a.controller.view().unknown,true);assert.deepEqual(b.controller.view().reactions,['check']);
});
test('remount adopts the same flight owner so late responses cannot dispatch abandoned intent',async()=>{
 const registry=createReactionRegistry(1),first=harness(),c=registry.acquire('s/m',()=>first.controller);
 let updates=0;const leave=c.subscribe(()=>updates++);
 c.set('heart',true);c.set('heart',false);leave();
 registry.acquire('other',()=>harness().controller);
 const remount=registry.acquire('s/m',()=>{throw Error('Must not create a competing controller')});
 const stop=remount.subscribe(()=>updates++);
 remount.set('heart',true);first.writes[0].resolve(receipt(['heart']));await tick();
 assert.equal(first.writes.length,1);assert.deepEqual(remount.view().reactions,['heart']);assert.equal(remount.view().pending.length,0);assert.ok(updates>=4);stop();
});
test('fresh click during restored receipt read is new intent, not a replay',async()=>{
 let release;const {controller:c,writes}=harness({restored:[{id:'old',clientId:'client',emoji:'heart',present:true,desired:true}],
  read:()=>new Promise(resolve=>release=resolve)});
 const reading=c.reconcile();c.set('heart',false);assert.equal(c.view().unknown,true);
 release({sessionId:'s',messageId:'m',status:'accepted',receipt:receipt(['heart']),revision:1,current:{reactions:['heart']}});
 await reading;assert.equal(writes.length,1);assert.notEqual(writes[0].row.id,'old');assert.equal(writes[0].row.present,false);
 writes[0].resolve(receipt([],2));await tick();assert.deepEqual(c.view().reactions,[]);
});
test('lost definite rejection rolls back only the uncertain emoji by reading its receipt',async()=>{
 const {controller:c,writes}=harness({read:async()=>({sessionId:'s',messageId:'m',status:'rejected',
  receipt:{accepted:false,error:'Original message unavailable'},revision:2,current:{reactions:['peer']}})});
 c.set('heart',true);writes[0].reject(Object.assign(Error('lost'),{unconfirmed:true}));await tick();
 await c.reconcile();assert.equal(writes.length,1);assert.equal(c.view().unknown,false);
 assert.deepEqual(c.view().reactions,['peer']);assert.match(c.view().error,/unavailable/);
});
test('unavailable browser storage does not silently promise reload recovery',()=>{
 const {controller:c}=harness({save:()=>false});c.set('heart',true);
 assert.deepEqual(c.view().reactions,['heart']);assert.match(c.view().error,/could not retain/);
});
test('rendering more than the idle cache limit cannot evict controllers before subscription',async()=>{
 const registry=createReactionRegistry(2),rows=Array.from({length:200},(_,i)=>{
  const h=harness();return {key:String(i),...h,first:registry.acquire(String(i),()=>h.controller)};
 });
 const stops=rows.map(row=>row.first.subscribe(()=>{}));
 rows[0].first.set('heart',true);rows[0].first.set('heart',false);stops[0]();
 const current=registry.acquire('0',()=>{throw Error('Controller was evicted before its first subscription')});
 assert.equal(current,rows[0].first);current.set('heart',true);
 rows[0].writes[0].resolve(receipt(['heart']));await tick();assert.equal(rows[0].writes.length,1);
 stops.forEach(stop=>stop());
});
