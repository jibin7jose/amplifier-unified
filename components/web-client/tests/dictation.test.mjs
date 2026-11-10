import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dictationVisible,appendDictation,DraftDictation} from '../src/dictation.js';
test('dictation defaults follow platform and explicit device override',()=>{
 const desktop={userAgent:'Windows NT',platform:'Win32',maxTouchPoints:0};
 for(const nav of [{userAgent:'Android'},{userAgent:'iPhone'},{userAgent:'Macintosh',platform:'MacIntel',maxTouchPoints:5}]){
  assert.equal(dictationVisible('auto',nav,true),false);assert.equal(dictationVisible('show',nav,true),true);
 }
 assert.equal(dictationVisible('auto',desktop,true),true);
 assert.equal(dictationVisible('hide',desktop,true),false);
 assert.equal(dictationVisible('show',desktop,false),false);
});
test('final results append once; edits survive and callbacks after cancellation cannot write',()=>{
 let speech,draft='Typed first',listening=false,error='';
 class Recognition{constructor(){speech=this}start(){}abort(){this.aborted=true}stop(){this.stopped=true}}
 const control=new DraftDictation(Recognition,{onText:text=>draft=appendDictation(draft,text),onState:value=>listening=value,onError:value=>error=value,language:'fr-FR'});
 control.start();assert.equal(listening,true);assert.equal(speech.lang,'fr-FR');
 const result=(text,final=true)=>Object.assign([{transcript:text}],{isFinal:final});
 speech.onresult({resultIndex:0,results:[result('interim',false)]});assert.equal(draft,'Typed first');
 speech.onresult({resultIndex:0,results:[result('spoken one')]});
 draft+=' and typed again';speech.onresult({resultIndex:0,results:[result('spoken one'),result('spoken two')]});
 assert.equal(draft,'Typed first spoken one and typed again spoken two');
 control.stop();assert.equal(speech.stopped,true);assert.equal(listening,true);
 speech.onend();assert.equal(listening,false);
 control.start();const late=speech.onresult;control.cancel();assert.equal(speech.aborted,true);
 late({resultIndex:0,results:[result('must not arrive')]});assert.ok(!draft.includes('must not arrive'));
 control.start();speech.onerror({error:'not-allowed'});assert.match(error,/microphone access/);assert.equal(listening,false);assert.ok(draft.endsWith('spoken two'));
});

test('unavailable speech service fails without losing the draft or throwing',()=>{
 let error='';const control=new DraftDictation(class {constructor(){throw Error('unavailable')}},{onText:()=>assert.fail('must not write'),onState:()=>assert.fail('must not start'),onError:value=>error=value});
 assert.doesNotThrow(()=>control.start());assert.match(error,/could not start/);assert.equal(control.current,null);
});
