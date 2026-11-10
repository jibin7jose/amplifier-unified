import test from 'node:test';
import assert from 'node:assert/strict';
import {providerRecordExport} from '../src/provider-record-export.js';

test('exports the loaded request with identity and explicit partial capture metadata',()=>{
 const node={id:'llm:child/one',sessionId:'child',provider:'test',model:'model',
  requestCapture:{redacted:true,truncated:true,limit_chars:1000000},requestDetail:{digest:'record-digest'}};
 const result=providerRecordExport(node,'request','{"input":["[capture limit]"]}');
 assert.match(result.filename,/-request-partial.json$/);
 assert.ok(!result.filename.includes('/'));
 assert.deepEqual(result.content.payload,{input:['[capture limit]']});
 assert.equal(result.content.sessionId,'child');
 assert.equal(result.content.capture.truncated,true);
 assert.equal(result.content.capture.limitCharacters,1000000);
 assert.equal(result.content.capture.recordDigest,'record-digest');
});

test('preserves text responses and unknown metadata without claiming completeness',()=>{
 const result=providerRecordExport({id:'call'},'response','provider summary');
 assert.equal(result.content.kind,'provider.response');
 assert.equal(result.content.format,'text');
 assert.equal(result.content.payload,'provider summary');
 assert.equal(result.content.capture.truncated,null);
 assert.equal(result.content.capture.redacted,null);
 assert.throws(()=>providerRecordExport({id:'call'},'response',null));
});
