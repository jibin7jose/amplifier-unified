import test from 'node:test';
import assert from 'node:assert/strict';
import {foldInterimRows,interimResponse,interimOpen,groupForMessage,retainInterimIds} from '../src/interim-replies.js';
import {conversationWorkRows,turnPlacements,combinedWork} from '../src/timeline-data.js';
import {withArtifactRows} from '../src/artifact-placement.js';

const response={version:1,generationId:'g',status:'completed',phase:'interim',finalMessageIds:['final','final-2'],inputIds:['input'],anchorMessageId:'user'};
const message=(id,extra={})=>({id,role:'assistant',generationId:'g',text:id,...extra});
function fixture(){
 const messages=[{id:'user',role:'user'},message('progress',{response}),message('update',{response}),message('final'),message('final-2')];
 const data={nodes:[],turns:['user','progress','update','final-2'].map((id,index)=>({
  id:'input@'+id,originalTurnId:'input',anchorMessageId:id,phase:'completed',
  startedAt:10+index*5,endedAt:15+index*5,aggregateUsage:{calls:1,totalTokens:10},
 }))};
 return {messages,data,rows:withArtifactRows(conversationWorkRows(messages,turnPlacements(messages,data)))};
}
test('off/missing/invalid preference returns original rows, without mutation',()=>{
 const {rows,data}=fixture(),before=JSON.stringify(rows);
 for(const value of [undefined,false,'true',1])assert.equal(foldInterimRows(rows,data,value),rows);
 foldInterimRows(rows,data,true);assert.equal(JSON.stringify(rows),before);
});
test('completion moves updates and work into one mixed group; multipart finals and later work stay outside',()=>{
 const {rows,data}=fixture(),folded=foldInterimRows(rows,data,true),group=folded.find(row=>row.interim);
 assert.deepEqual(group.items.map(item=>item.message?.id||item.turnId),['input@user','progress','input@progress','update','input@update']);
 assert.deepEqual(group.outputs.map(row=>row.message.id),['progress','update']);
 assert.deepEqual(folded.filter(row=>row.kind==='message').map(row=>row.id),['user','final','final-2']);
 assert.equal(folded.at(-2).items[0].turnId,'input@final-2');
 assert.equal(combinedWork(group.items.filter(i=>i.turnId).map(i=>data.turns.find(t=>t.id===i.turnId)),100).usage.totalTokens,30);
});
test('without confirmed provenance idle and later work never qualify a message',()=>{
 for(const extra of [{},{partial:true,response},{streaming:true,response},{response:{...response,status:'failed'}},
  {response:{...response,phase:'final'}},{response:{...response,generationId:'foreign'}},{response:{...response,finalMessageIds:[]}}]){
  assert.equal(interimResponse(message('progress',extra)),null);
 }
});
test('human/peer boundaries split groups and unrelated/live work is not swallowed',()=>{
 const {rows,data}=fixture();
 rows.splice(4,0,{kind:'message',id:'steer',message:{id:'steer',role:'user',inputOrigin:'peer'}});
 const folded=foldInterimRows(rows,data,true);
 assert.equal(folded.filter(row=>row.interim).length,2);
 assert.ok(folded.some(row=>row.id==='steer'));
 const live=structuredClone(fixture());live.data.turns[1].phase='running';delete live.data.turns[1].endedAt;
 assert.ok(foldInterimRows(live.rows,live.data,true).some(row=>!row.interim&&row.items?.some(i=>i.turnId==='input@progress')));
 const ids=foldInterimRows(live.rows,live.data,true).filter(row=>row.interim).map(row=>row.id);
 assert.equal(new Set(ids).size,ids.length);
});
test('earlier pages retain the open group identity rather than remounting its messages',()=>{
 const {rows,data}=fixture();
 const earlier=foldInterimRows(rows.slice(4),data,true);
 const all=retainInterimIds(foldInterimRows(rows,data,true),earlier);
 const old=earlier.find(row=>row.interim),current=all.find(row=>row.interim);
 assert.equal(current.id,old.id);
 assert.equal(interimOpen(new Set([old.id]),current),true);
});
test('message-only turns and final off-page use host annotation, not loaded suffix',()=>{
 const rows=withArtifactRows([{kind:'message',id:'progress',message:message('progress',{response})}]);
 const folded=foldInterimRows(rows,{turns:[]},true);
 assert.equal(folded.length,1);assert.equal(folded[0].items[0].message.id,'progress');
 assert.equal(groupForMessage(folded,'progress'),folded[0]);
 assert.equal(groupForMessage(folded,'final'),undefined);
});
test('deferred generations remain unchanged while other completed history can fold',()=>{
 const {rows,data}=fixture();
 assert.deepEqual(foldInterimRows(rows,data,true,{deferred:new Set(['g'])}),rows);
});
test('explicit disclosure choices win; Everything is not a default-open override',()=>{
 const {rows,data}=fixture(),group=foldInterimRows(rows,data,true).find(row=>row.interim);
 assert.equal(interimOpen(new Set(),group),false);
 assert.equal(interimOpen(new Set([group.id]),group),true);
 assert.equal(interimOpen(new Set(['turn:input@progress']),group),true);
 assert.equal(interimOpen(new Set(['turn:input@progress','closed:'+group.id]),group),false);
});