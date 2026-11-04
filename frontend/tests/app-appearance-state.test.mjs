import test from 'node:test';
import assert from 'node:assert/strict';
import {appAppearanceState} from '../src/app-appearance-state.js';

test('connected global activity is independent of selected chat and UI spinners',()=>{
 const state={appActivity:{active:true},sessions:[{id:'visible',status:'idle'}],selectedSessionId:'visible'};
 const value=appAppearanceState(state,true,true);
 assert.equal(value['data-app-activity'],'active');assert.match(value.className,/\ba-app-active\b/);
 assert.doesNotMatch(value.className,/\ba-app-idle\b/);
});
test('a known idle snapshot is distinct from disconnected or absent activity',()=>{
 assert.equal(appAppearanceState({appActivity:{active:false}},true,true)['data-app-activity'],'idle');
 for(const [state,connected] of [[{},true],[{appActivity:{active:true}},false],[{appActivity:{active:false}},false]]){
  const result=appAppearanceState(state,connected,true);
  assert.equal(result['data-app-activity'],'unknown');assert.doesNotMatch(result.className,/\ba-app-active\b|\ba-app-idle\b/);
 }
});
test('attention is the canonical global unread projection, not a loaded notice page or badge preference',()=>{
 const result=appAppearanceState({appActivity:{active:false},attention:{unread:2,badgeUnread:0,items:[]}},true,true);
 assert.equal(result['data-app-attention'],'unread');assert.match(result.className,/\ba-app-attention\b/);
 assert.equal(result['data-app-activity'],'idle');
 const reviewed=appAppearanceState({attention:{unread:0,items:[{read:true}]}},true);
 assert.equal(reviewed['data-app-attention'],'none');assert.doesNotMatch(reviewed.className,/\ba-app-attention\b/);
});
test('connection class is a passive fact, with no mutation of its inputs',()=>{
 const state={appActivity:{active:true},attention:{unread:1}},original=JSON.stringify(state);
 const result=appAppearanceState(state,false);
 assert.equal(result['data-app-connection'],'disconnected');assert.match(result.className,/a-app-disconnected/);
 assert.equal(JSON.stringify(state),original);
});
for(const priorActive of [true,false])test(`reconnect open cannot revive prior ${priorActive?'active':'idle'} before current baseline`,()=>{
 const prior={appActivity:{active:priorActive}};
 assert.equal(appAppearanceState(prior,true,true)['data-app-activity'],priorActive?'active':'idle');
 for(const [connected,baselineReady] of [[false,false],[true,false],[true,undefined]]){
  const result=appAppearanceState(prior,connected,baselineReady);
  assert.equal(result['data-app-activity'],'unknown');
  assert.doesNotMatch(result.className,/\ba-app-active\b|\ba-app-idle\b/);
  assert.equal(result['data-app-connection'],connected?'connected':'disconnected');
 }
 const next={appActivity:{active:!priorActive}};
 assert.equal(appAppearanceState(next,true,true)['data-app-activity'],priorActive?'idle':'active');
 assert.equal(prior.appActivity.active,priorActive,'the retained snapshot is not rewritten');
});
