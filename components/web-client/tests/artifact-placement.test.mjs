import test from 'node:test';
import assert from 'node:assert/strict';
import {withArtifactRows} from '../src/artifact-placement.js';
const message=(id,role)=>({kind:'message',id,message:{id,role}});
test('request outputs follow their work and response without moving into a later request',()=>{
 const rows=[message('origin','user'),{kind:'work',id:'work'},message('answer','assistant'),message('later','user'),{kind:'work',id:'later-work'}];
 const before=JSON.stringify(rows),placed=withArtifactRows(rows);
 assert.deepEqual(placed.map(row=>row.id),['origin','work','answer','artifacts:answer','artifacts:origin','later','later-work','artifacts:later']);
 assert.equal(JSON.stringify(rows),before);
 assert.equal(placed.find(row=>row.id==='artifacts:origin').message,rows[0].message);
});
test('running and reloaded work use the same request slot, including no answer yet',()=>{
 const rows=[message('origin','user'),{kind:'work',id:'running-work'}];
 assert.deepEqual(withArtifactRows(rows).map(row=>row.id),['origin','running-work','artifacts:origin']);
 assert.deepEqual(withArtifactRows(JSON.parse(JSON.stringify(rows))),withArtifactRows(rows));
 assert.deepEqual(withArtifactRows([]),[]);
});
