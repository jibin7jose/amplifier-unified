import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {inputPreview,retainedInputPreview,readablePayload} from '../src/tool-input-preview.js';
import {actionHeading,actionContent} from '../src/execution-content.js';
const cases=JSON.parse(readFileSync(new URL('../../tests/fixtures/tool-preview-cases.json',import.meta.url)));
for(const row of cases)test('shared preview: '+row.name,()=>{
 assert.equal(inputPreview(row.input),row.inputPreview);
 const node={tool:row.tool,input:JSON.stringify(row.input),phase:'completed'};
 const expected=row.actionPath||row.inputPreview;
 assert.equal(actionHeading(node).target,expected);
 assert.equal(actionHeading({...node,input:undefined,inputPreview:row.inputPreview,actionPath:row.actionPath,inputSummary:row.tool==='app_control'?{operation:'dispatch'}:{}}).target,expected);
 assert.equal(actionContent({...node,input:undefined,inputPreview:row.inputPreview}).input,undefined,'Summary is never passed off as complete input');
});
test('preview work and traversal stay bounded for unfamiliar arguments',()=>{
 const huge={};for(let i=0;i<1000;i++)huge['key'+i]='x'.repeat(1000);
 assert.ok(inputPreview(huge).length<=180);assert.ok(inputPreview(huge).endsWith('…'));
 assert.match(inputPreview({query:'word '.repeat(1000)}),/…/);
 assert.equal(retainedInputPreview('{"secret":"incomplete'),'');
 assert.equal(retainedInputPreview('{'.repeat(20000)),'');
 assert.equal(retainedInputPreview(undefined),'');
 assert.equal(retainedInputPreview('plain text'),'"plain text"');
 assert.match(inputPreview({nested:{nested:{large:'hidden'}}}),/\{…\}/);
});
test('readable JSON changes only layout; numeric spelling, duplicate keys and escaped text survive',()=>{
 const raw='{"id":9007199254740993,"n":1e+09,"k":"a,b:[]{}\\n\\\"","k":"duplicate","empty":{}}';
 const display=readablePayload(raw);
 assert.match(display,/\n  "id": 9007199254740993,/);assert.match(display,/"n": 1e\+09/);
 assert.equal((display.match(/"k":/g)||[]).length,2);
 assert.deepEqual(JSON.parse(display),JSON.parse(raw));
 for(const literal of ['{"incomplete":','<script>literal</script>','a'.repeat(300000)])assert.equal(readablePayload(literal),literal);
 const deep='['.repeat(40)+'0'+']'.repeat(40);assert.equal(readablePayload(deep),deep);
});

test('unrepresentable legacy numeric IDs are omitted rather than rounded in fallback hints',()=>{
 const row={tool:'lookup_record',input:'{"id":9007199254740993}',phase:'completed'};
 assert.equal(actionHeading(row).target,'id=…');
 assert.equal(actionHeading({...row,inputPreview:'id=9007199254740993'}).target,'id=9007199254740993');
 assert.equal(inputPreview({limit:0}),'limit=0');
});
