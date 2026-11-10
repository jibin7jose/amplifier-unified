import test from 'node:test';
import assert from 'node:assert/strict';
import {resizeComposer,composerPrimaryAction,composerExpanded,quoteComposerHeightBudget} from '../src/composer.js';
test('compact presentation expands content without altering it or action priority',()=>{
 assert.equal(composerExpanded({draft:''}),false);
 assert.equal(composerExpanded({draft:'Ready to send'}),false);
 for(const value of [{draft:'First\nSecond'},{draft:'x'.repeat(81)},{attachments:[{id:'file'}]},{quote:{id:'quote'}}]){
  const before=structuredClone(value);
  assert.equal(composerExpanded(value),true);assert.deepEqual(value,before);
 }
 assert.equal(composerExpanded({draft:'x'.repeat(80)}),false);
});
test('composer starts compact, expands with content, and scrolls only at its bound',()=>{
 const element={style:{},scrollHeight:28};resizeComposer(element);assert.equal(element.style.height,'44px');assert.equal(element.style.overflowY,'hidden');
 element.scrollHeight=112;resizeComposer(element);assert.equal(element.style.height,'112px');
 element.scrollHeight=260;resizeComposer(element);assert.equal(element.style.height,'180px');assert.equal(element.style.overflowY,'auto');
 element.scrollHeight=24;resizeComposer(element);assert.equal(element.style.height,'44px');assert.equal(element.style.overflowY,'hidden');
});
test('quote drafts have a viewport-bounded reading area without enlarging plain drafts',()=>{
 let quote=true;
 const element={style:{},hasAttribute:()=>true,querySelector:()=>quote?{}:null};
 resizeComposer(element);assert.equal(element.style.maxHeight,'min(400px, 35dvh)');assert.equal(element.style.overflowY,'auto');
 quote=false;resizeComposer(element);assert.equal(element.style.maxHeight,'180px');
});
test('quote height reserves the conversation after enlarged chrome and padding-floor overflow',()=>{
 const layout={viewportHeight:844,contentBottom:800,messagesTop:100,messagesBottom:400,composerBottom:792,composerMarginBottom:8,editorHeight:295};
 assert.equal(quoteComposerHeightBudget(layout),295.4);
 // At the padding floor, the form extends 6px below its content boundary.
 const cramped={...layout,messagesBottom:144,composerBottom:806};
 assert.equal(quoteComposerHeightBudget(cramped),245);
 assert.ok(quoteComposerHeightBudget(cramped)>181);
 const naive=cramped.editorHeight+(cramped.messagesBottom-cramped.messagesTop)-80;
 assert.equal(naive,259);assert.ok(quoteComposerHeightBudget(cramped)<naive,'Overflow is part of the budget');
 // After shrinking, editor + chrome is unchanged: no feedback-loop ratchet.
 assert.equal(quoteComposerHeightBudget({...cramped,editorHeight:245,composerBottom:756}),245);
 assert.equal(quoteComposerHeightBudget(layout),295.4,'More space restores the original ceiling');
 assert.equal(quoteComposerHeightBudget({...layout,viewportHeight:2000,contentBottom:2000}),400);
 assert.equal(quoteComposerHeightBudget({...cramped,contentBottom:200}),44);
});

test('content keeps Send during voice, work, and pending attachments',()=>{
 for(const input of [{draft:'A message'},{attachments:[{id:'file'}]}]){
  for(const voiceStatus of ['idle','connecting','connected','ending']){
   const action=composerPrimaryAction({...input,voiceStatus,working:true,responseActive:true});
   assert.equal(action.action,'conversation.send');assert.equal(action.label,'Send message');assert.equal(action.disabled,false);
  }
 }
 const upload=composerPrimaryAction({uploading:true,voiceStatus:'connected',responseActive:true});
 assert.equal(upload.action,'conversation.send');assert.equal(upload.disabled,true);
});

test('an empty composer ends voice before stopping background work, otherwise offers voice',()=>{
 for(const voiceStatus of ['connecting','connected','ending']){
  const action=composerPrimaryAction({draft:' \n',voiceStatus,responseActive:true,executionUnavailable:true,historyPending:true});
  assert.equal(action.action,'call.end');assert.equal(action.disabled,voiceStatus==='ending');assert.equal(action.icon,'cancel');
 }
 const stop=composerPrimaryAction({responseActive:true});
 assert.equal(stop.action,'conversation.stop');assert.equal(stop.disabled,false);assert.equal(stop.icon,'cancel');
 assert.equal(composerPrimaryAction({responseActive:true,stopping:true}).disabled,true);
 for(const voiceStatus of ['idle','ended','error'])assert.equal(composerPrimaryAction({voiceStatus}).action,'call.start');
});

test('pending sends never expose an accidental voice launch and readiness still gates sending',()=>{
 for(const flag of ['newChatPending','sending']){
  const pending=composerPrimaryAction({[flag]:true});assert.equal(pending.action,'conversation.send');assert.equal(pending.disabled,true);
 }
 for(const flag of ['busy','historyPending','executionUnavailable']){
  assert.equal(composerPrimaryAction({[flag]:true}).disabled,true);
  const send=composerPrimaryAction({draft:'Retain this draft',voiceStatus:'connected',[flag]:true});
  assert.equal(send.action,'conversation.send');assert.equal(send.disabled,true);
 }
 assert.equal(composerPrimaryAction({voiceStarting:true}).disabled,true);
});
