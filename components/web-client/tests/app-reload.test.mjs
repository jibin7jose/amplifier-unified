import {test} from 'node:test';
import assert from 'node:assert/strict';
import {changedFrontend,reloadWhenSaved} from '../src/app-reload.js';
test('only a different valid installed frontend prompts reload, including a same-version rebuild',()=>{
 const loaded={id:'aaaaaaaaaaaaaaaa',version:'0.20.15'};
 assert.equal(changedFrontend(loaded,{id:'bbbbbbbbbbbbbbbb',version:'0.20.15'}),true);
 assert.equal(changedFrontend(loaded,{...loaded,version:'0.20.16'}),true);
 assert.equal(changedFrontend(loaded,{...loaded}),false);
 for(const value of [null,{}, {id:'dev'},{id:'unknown'}])assert.equal(changedFrontend(loaded,value),false);
 assert.equal(changedFrontend({id:'dev'},loaded),false);
});
test('reload waits for saves and refuses to reload after a failed save',async()=>{
 const events=[];let finish;const saved=new Promise(resolve=>finish=resolve);
 const pending=reloadWhenSaved({prepare:()=>saved,reload:()=>events.push('reload')});
 assert.deepEqual(events,[]);finish();await pending;assert.deepEqual(events,['reload']);
 await assert.rejects(reloadWhenSaved({prepare:async()=>{throw Error('offline')},reload:()=>events.push('lost draft')}),/offline/);
 assert.deepEqual(events,['reload']);
});
