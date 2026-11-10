import test from 'node:test';
import assert from 'node:assert/strict';
import {transcriptFileArgs} from '../src/history-import.js';
test('conversation import preserves full JSONL transcript and forwards optional setup',async()=>{
 const content='{"role":"assistant","tool_calls":[{"id":"call-1"}]}\n{"role":"tool","tool_call_id":"call-1","content":"result"}\n';
 const args=await transcriptFileArgs({name:'conversation.JSONL',size:content.length,text:async()=>content},{title:' Imported ',bundle:' anchors '});
 assert.deepEqual(args,{content,format:'jsonl',title:'Imported',bundle:'anchors'});
 assert.equal((await transcriptFileArgs({name:'export.json',size:2,text:async()=>'{}'})).format,'json');
});
test('file import rejects oversize files before reading and checks decoded byte length',async()=>{
 let read=false;
 await assert.rejects(()=>transcriptFileArgs({name:'large.json',size:1_000_001,text:async()=>{read=true;return '{}'}}),/1 MB/);
 assert.equal(read,false);
 await assert.rejects(()=>transcriptFileArgs({name:'oversize.json',size:2,text:async()=>'é'.repeat(500_001)}),/1 MB/);
 await assert.rejects(()=>transcriptFileArgs({name:'empty.jsonl',size:0,text:async()=>''}),/empty/);
 await assert.rejects(()=>transcriptFileArgs({name:'document.txt',size:1,text:async()=>'x'}),/JSON or JSONL/);
});
