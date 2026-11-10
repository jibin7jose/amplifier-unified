import test from 'node:test';
import assert from 'node:assert/strict';
import {peerRuns} from '../src/peer-groups.js';
import {withArtifactRows} from '../src/artifact-placement.js';
const peer=(id,source='a',extra={})=>({kind:'message',id,message:{id,role:'user',inputOrigin:'peer',text:'same',attribution:{caption:'Sent by Amplifier from another chat',peer:{sourceId:source,available:true}},...extra}});
const ordinary=(kind,id)=>({kind,id,message:{id,role:'assistant'}});
test('peer runs retain exact originals and A-B-A order',()=>{
 const input=[peer('1'),peer('2','b'),peer('3')],out=peerRuns(input);
 assert.equal(out.length,1);assert.equal(out[0].kind,'peers');assert.deepEqual(out[0].rows,input);
 assert.equal(new Set(out[0].rows.map(x=>x.id)).size,3);
});
test('all non-peer rows, unknown provenance and system notifications split',()=>{
 for(const kind of ['message','work','artifacts','gap','approval','question']){
  const out=peerRuns([peer('1'),ordinary(kind,'boundary'),peer('2')]);assert.equal(out.length,3,kind);
 }
 const system=peer('system','a',{attribution:{caption:'Amplifier update'}});
 assert.equal(peerRuns([peer('1'),system,peer('2')]).length,3);
 assert.equal(peerRuns([peer('1'),peer('unknown','a',{attribution:null}),peer('2')]).length,3);
});
test('folded interim Work stays a boundary and absent coverage never joins',()=>{
 const interim={kind:'work',id:'interim',items:[{message:{id:'hidden',role:'assistant'}}]};
 assert.equal(peerRuns([peer('1'),interim,peer('2')]).length,3);
});
test('attention facts stay in originals; no prose inference',()=>{
 const input=[peer('1','a',{text:'approved success'}),peer('2','b',{delivery:{status:'failed'}})];
 assert.deepEqual(peerRuns(input)[0].rows,input);
});
test('real artifact-placement pipeline: empty slots group, actual outputs split',()=>{
 const originals=[peer('1'),peer('2','b'),peer('3')];
 const rows=withArtifactRows(originals);
 assert.equal(peerRuns(rows,new Set()).length,1);
 assert.deepEqual(peerRuns(rows,new Set())[0].rows,originals);
 const actual=peerRuns(rows,new Set(['1']));
 assert.deepEqual(actual.map(row=>row.kind),['message','artifacts','peers']);
 assert.deepEqual(actual[2].rows,originals.slice(1));
 // Missing coverage is conservative, not a declaration that artifacts are empty.
 assert.equal(peerRuns(rows).filter(row=>row.kind==='peers').length,0);
});
