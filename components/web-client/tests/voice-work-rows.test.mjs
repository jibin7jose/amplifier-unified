import test from 'node:test';
import assert from 'node:assert/strict';
import {voiceWorkRows,voiceWorkStream,retainVoiceWorkIds} from '../src/voice-work-rows.js';
import {conversationWorkRows,turnPlacements,combinedWork} from '../src/timeline-data.js';
import {groupForMessage,foldInterimRows} from '../src/interim-replies.js';

const input='voice:call:request';
const backend=(id,extra={})=>({id,role:'assistant',source:'amplifier',via:'call',inputId:input,text:id,...extra});
const turn=(anchor,extra={})=>({id:input+'@'+anchor,originalTurnId:input,inputId:input,anchorMessageId:anchor,
 phase:'completed',startedAt:1,endedAt:3,aggregateUsage:{calls:1,totalTokens:10},...extra});
const rows=(messages,turns,stream)=>{
 const data={turns,nodes:[]};return voiceWorkRows(conversationWorkRows(messages,turnPlacements(messages,data)),data,stream);
};

test('backend messages join exact-owned Work segments without changing accounting or source',()=>{
 const messages=[{id:'user',role:'user'},backend('update'),backend('result')],turns=[turn('user'),turn('update',{startedAt:4,endedAt:6})];
 const before=JSON.stringify({messages,turns}),grouped=rows(messages,turns);
 assert.deepEqual(grouped.map(row=>row.kind),['message','work']);
 assert.deepEqual(grouped[1].items.map(item=>item.turnId||item.message.id),[turns[0].id,'update',turns[1].id,'result']);
 assert.deepEqual(grouped[1].voiceInputIds,[input]);assert.equal(grouped[1].summaryId,'turn:'+turns[0].id);
 const visibleTurns=grouped[1].items.filter(item=>item.turnId).map(item=>turns.find(turn=>turn.id===item.turnId));
 assert.deepEqual(combinedWork(visibleTurns,10),combinedWork(turns,10));
 assert.equal(JSON.stringify({messages,turns}),before);
});

test('spoken transcripts and typed answers remain outside; concurrent voice requests cannot cross owners',()=>{
 const other='voice:call:other';
 const messages=[{id:'user',role:'user'},backend('first'),{id:'spoken',role:'assistant',voiceId:'call',via:'call'},
  backend('second',{inputId:other}),backend('typed',{via:'chat',inputId:'typed'}),backend('unknown',{source:undefined})];
 const turns=[turn('user'),turn('spoken',{id:other+'@spoken',originalTurnId:other,inputId:other})];
 const grouped=rows(messages,turns);
 assert.deepEqual(grouped.filter(row=>row.kind==='message').map(row=>row.id),['user','spoken','typed','unknown']);
 assert.deepEqual(grouped.filter(row=>row.voiceWork).map(row=>row.items.filter(item=>item.message).map(item=>item.message.id)),[['first'],['second']]);
 assert.deepEqual(grouped.filter(row=>row.voiceWork).map(row=>row.voiceInputIds),[[input],[other]]);
});

test('known orphan backend content remains under neutral Work without borrowing unrelated execution',()=>{
 const messages=[{id:'user',role:'user'},backend('orphan'),backend('orphan-2')];
 const unrelated={id:'typed@user',originalTurnId:'typed',inputId:'typed',anchorMessageId:'user',phase:'completed',startedAt:1,endedAt:8};
 const grouped=rows(messages,[unrelated]),fallback=grouped.find(row=>row.voiceWork);
 assert.equal(fallback.items.some(item=>item.turnId),false);
 assert.deepEqual(fallback.items.map(item=>item.message.id),['orphan','orphan-2']);
 assert.ok(fallback.summaryId.startsWith('voice-work:'));
 assert.equal(grouped.filter(row=>row.kind==='work').length,2);
 assert.equal(groupForMessage(grouped,'orphan'),fallback);
 const known=backend('native-copy',{nativeMessageId:'saved-native'});
 assert.equal(groupForMessage(rows([known],[]),'saved-native').items[0].message,known);
});

test('ambiguous input ownership is not resolved by order, timestamps, or current work',()=>{
 const messages=[{id:'user',role:'user'},backend('answer')];
 const turns=[turn('user'),turn('user',{id:'different@user',originalTurnId:'different',inputId:input,startedAt:100})];
 const grouped=rows(messages,turns),answer=grouped.find(row=>row.items?.some(item=>item.message?.id==='answer'));
 assert.equal(answer.items.some(item=>item.turnId),false);
});

test('first voice stream uses the same Work group; typed and unknown streams are not classified',()=>{
 const session={streaming:'First chunk',streamingId:'stream-id',streamingInputId:input};
 const stream=voiceWorkStream(session),turns=[turn('user',{phase:'running',endedAt:undefined})];
 const before=rows([{id:'user',role:'user'}],turns),during=rows([{id:'user',role:'user'}],turns,stream);
 assert.equal(during[1].id,before[1].id);assert.equal(during[1].summaryId,before[1].summaryId);
 assert.equal(during[1].items.at(-1).message,stream);
 for(const streamingInputId of [undefined,'typed'])assert.equal(voiceWorkStream({...session,streamingInputId}),null);
 assert.equal(voiceWorkStream({...session,streaming:''}),null);
});

test('orphan stream, saved receipt and later execution preserve one disclosure identity',()=>{
 const stream=voiceWorkStream({streaming:'First chunk',streamingId:'stream-id',streamingInputId:input});
 const before=rows([{id:'user',role:'user'}],[],stream),old=before.find(row=>row.voiceWork);
 const final=backend('saved',{streamId:'stream-id'});
 const after=retainVoiceWorkIds(rows([{id:'user',role:'user'},final],[turn('user')]),before);
 const group=after.find(row=>row.voiceWork);
 assert.equal(group.id,old.id);assert.equal(group.summaryId,old.summaryId);
 assert.equal(group.items.filter(item=>item.message).length,1);
 assert.equal(group.items.at(-1).message.id,'saved');
 assert.equal(group.items[0].turnId,turn('user').id);
});

test('voice usage lifecycle is not mistaken for a backend request and optional interim fold cannot consume voice Work',()=>{
 const voiceUsage={id:'voice:call@user',originalTurnId:'voice:call',lifecycle:'voice',anchorMessageId:'user',phase:'running'};
 const ordinary=rows([{id:'user',role:'user'}],[voiceUsage]);assert.equal(ordinary[1].voiceWork,false);
 const grouped=rows([{id:'user',role:'user'},backend('update')],[turn('user')]);
 assert.deepEqual(foldInterimRows(grouped,{turns:[turn('user')]},true),grouped);
});
