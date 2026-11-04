import test from 'node:test';
import assert from 'node:assert/strict';
import {createAppearanceCatalog,appearanceCatalogFor,clearAppearanceCatalogs} from '../src/appearance-catalog.js';
const library=name=>({items:[{id:'builtin:one',name,fingerprint:'a'}],errors:[],directory:''});
const receipt=result=>({accepted:true,result});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
test('startup and Settings share one in-flight metadata read and a fresh cached result',async()=>{
 let calls=0;const d=deferred(),catalog=createAppearanceCatalog(),act=()=>{calls++;return d.promise};
 const first=catalog.refresh(act);assert.equal(catalog.refresh(act),first);
 await Promise.resolve();assert.equal(calls,1);
 d.resolve(receipt(library('One')));await first;
 assert.equal((await catalog.refresh(act)).items[0].name,'One');assert.equal(calls,1);
});
test('stale refresh and error keep the last useful cards on screen',async()=>{
 let time=0;const catalog=createAppearanceCatalog({now:()=>time,freshMs:10});
 await catalog.refresh(()=>receipt(library('Old')));time=11;
 const d=deferred(),read=catalog.refresh(()=>d.promise);
 assert.equal(catalog.getSnapshot().library.items[0].name,'Old');
 d.reject(Error('Offline'));await assert.rejects(read,/Offline/);
 assert.equal(catalog.getSnapshot().library.items[0].name,'Old');assert.equal(catalog.getSnapshot().error,'Offline');
});
test('a forced post-mutation refresh cannot be overwritten by an earlier listing',async()=>{
 const catalog=createAppearanceCatalog(),old=deferred();
 const early=catalog.refresh(()=>old.promise);
 await catalog.refresh(()=>receipt(library('New')),{force:true});
 old.resolve(receipt(library('Old')));await early;
 assert.equal(catalog.getSnapshot().library.items[0].name,'New');
});
test('authentication clear fences late reads and removes all cached CSS',async()=>{
 const catalog=createAppearanceCatalog(),d=deferred(),item={id:'saved:x',fingerprint:'a'};
 await catalog.read(item,()=>receipt({css:'old',fingerprint:'a'}));
 const old=catalog.refresh(()=>d.promise);catalog.clear();d.resolve(receipt(library('Old')));await old;
 assert.equal(catalog.getSnapshot().library,null);
 assert.equal((await catalog.read(item,()=>receipt({css:'new',fingerprint:'a'}))).css,'new');
});
test('a confirmed hidden mutation invalidates metadata without blanking cards or accepting an older listing',async()=>{
 const catalog=createAppearanceCatalog(),d=deferred();
 await catalog.refresh(()=>receipt(library('Cached')));
 const old=catalog.refresh(()=>d.promise,{force:true});
 catalog.invalidate();
 assert.equal(catalog.getSnapshot().library.items[0].name,'Cached');
 await catalog.refresh(()=>receipt(library('Changed')));
 d.resolve(receipt(library('Old')));await old;
 assert.equal(catalog.getSnapshot().library.items[0].name,'Changed');
});
test('body reads deduplicate by exact fingerprint, invalidate changed versions and retry errors',async()=>{
 const catalog=createAppearanceCatalog(),item={id:'saved:x',fingerprint:'a'};let calls=0;
 const act=()=>{calls++;return receipt({css:'body',fingerprint:'a'})};
 const first=catalog.read(item,act);assert.equal(catalog.read(item,act),first);await first;assert.equal(calls,1);
 await assert.rejects(catalog.read({...item,fingerprint:'b'},act),/changed/);
 await catalog.read({...item,fingerprint:'b'},()=>receipt({css:'new',fingerprint:'b'}));
 assert.equal(calls,2);
});
test('CSS cache is bounded and metadata refresh never performs theme.read',async()=>{
 const catalog=createAppearanceCatalog({maxBodies:1}),calls=[];
 const act=(action,args)=>{calls.push(action);return receipt(action==='theme.list'?library('One'):{css:args.id,fingerprint:'a'})};
 await catalog.refresh(act);assert.deepEqual(calls,['theme.list']);
 await catalog.read({id:'one',fingerprint:'a'},act);await catalog.read({id:'two',fingerprint:'a'},act);
 await catalog.read({id:'one',fingerprint:'a'},act);assert.equal(calls.filter(x=>x==='theme.read').length,3);
});
test('host identity and auth reset never share another host catalog',async()=>{
 clearAppearanceCatalogs();const one=appearanceCatalogFor('host-one'),two=appearanceCatalogFor('host-two');
 await one.refresh(()=>receipt(library('One')));assert.equal(two.getSnapshot().library,null);
 clearAppearanceCatalogs();assert.equal(one.getSnapshot().library,null);assert.notEqual(appearanceCatalogFor('host-one'),one);
});
test('malformed/rejected responses fail visibly, not as an empty successful gallery',async()=>{
 const catalog=createAppearanceCatalog();
 for(const value of [{accepted:false,result:library('No')},receipt({items:[]}),receipt(null)])
  await assert.rejects(catalog.refresh(()=>value),/unavailable/);
 assert.equal(catalog.getSnapshot().library,null);
});
test('late CSS from a previous authentication identity cannot populate a thumbnail',async()=>{
 const catalog=createAppearanceCatalog(),d=deferred(),read=catalog.read({id:'one',fingerprint:'a'},()=>d.promise);
 catalog.clear();d.resolve(receipt({css:'private-old-body',fingerprint:'a'}));
 await assert.rejects(read,/connection changed/);
});