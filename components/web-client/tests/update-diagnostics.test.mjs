import test from 'node:test';
import assert from 'node:assert/strict';
import {updateClientDiagnostics} from '../src/update-diagnostics.js';
import {setFeedbackEventStream} from '../src/feedback-diagnostics.js';

test('report records the displayed update state and transport without private state',()=>{
 setFeedbackEventStream('reconnecting');
 const state={revision:42,sessions:[{title:'private conversation'}],client:{hostInstanceId:'private-host'},
  updates:{phase:'installed',detail:'private path',lastCheck:123,lastAttempt:120,available:1,
   sequence:{stage:'included',nextStage:'other',install:true,private:'private account'}}};
 const result=updateClientDiagnostics(state,{navigator:{userAgent:'private user agent',onLine:true},document:{visibilityState:'visible'}});
 assert.deepEqual(result.updates,{phase:'installed',stage:'included',nextStage:'other',installRequested:true,hasError:false,revision:42,lastCheck:123,lastAttempt:120,available:1});
 assert.equal(result.eventStream,'reconnecting');
 assert.equal(result.visible,true);
 assert.equal(JSON.stringify(result).includes('private'),false);
});

test('malformed status values are excluded rather than copied',()=>{
 const result=updateClientDiagnostics({revision:Infinity,updates:{phase:'secret',lastCheck:'private path',available:-1,sequence:{stage:'secret',nextStage:'secret'}}},{});
 assert.equal(result.updates.phase,'unknown');
 assert.equal(result.updates.stage,null);
 assert.equal(result.updates.nextStage,null);
 assert.equal(Object.hasOwn(result.updates,'revision'),false);
 assert.equal(Object.hasOwn(result.updates,'available'),false);
 assert.equal(JSON.stringify(result).includes('secret'),false);
});
