import test from 'node:test';
import assert from 'node:assert/strict';
import {updateReleaseSummary} from '../src/update-release-summary.js';
import {updateOverview} from '../src/update-overview.js';

const notes=version=>({version,title:'Release '+version,changes:['Changes for '+version],notices:[]});
const app={current:'0.20.205',latest:'v0.20.206',status:'update',revision:'b'.repeat(40),runningRevision:'a'.repeat(40),releaseNotes:[notes('0.20.206'),notes('0.20.205')]};
function summary(updates={}){updates={application:app,...updates};return updateReleaseSummary(updates,updates.application,updateOverview(updates))}

test('offered notes are visible before install and installed notes when current',()=>{
 assert.equal(summary().stage,'before');assert.equal(summary().notes.version,'0.20.206');
 assert.equal(summary({application:{...app,status:'current'}}).notes.version,'0.20.205');
});
test('queued target keeps its own notes when a newer release is discovered',()=>{
 const result=summary({pendingApp:{latest:'v0.20.204',releaseNotes:[notes('0.20.204')]},phase:'app-staged'});
 assert.equal(result.stage,'during');assert.equal(result.notes.version,'0.20.204');
});
test('staging a superseding candidate follows its revision, not the prior queued version',()=>{
 const result=summary({pendingApp:{latest:'v0.20.204'},phase:'staging',diagnostics:{kind:'application',revision:app.revision}});
 assert.equal(result.version,'0.20.206');assert.equal(result.stage,'during');
});
test('feature preparation at the running revision cannot borrow a newer offered release',()=>{
 assert.equal(summary({phase:'staging',diagnostics:{kind:'application',revision:app.runningRevision}}).version,'0.20.205');
 assert.equal(summary({phase:'staging',diagnostics:{kind:'application',revision:'c'.repeat(40)}}).version,null);
});
test('replacement and restart targets outrank availability, including errors and uncertain restart',()=>{
 for(const key of ['pendingReplacement','pendingRestart']){
  const result=summary({[key]:{version:'0.20.205',requestStatus:'uncertain'},phase:'error'});
  assert.equal(result.version,'0.20.205');assert.equal(result.stage,'during');
 }
});
test('unknown pending identity never claims installed or borrows latest notes',()=>{
 for(const patch of [{pendingReplacement:{}},{pendingRestart:{}},{pendingApp:{}},{pendingApp:{latest:'javascript:alert(1)'}}]){
  const result=summary(patch);assert.equal(result.version,null);assert.equal(result.notes,null);assert.equal(result.url,null);assert.equal(result.stage,'during');
 }
});
test('runner wait shows newer offered notes, while confirmed installed identity stays separate',()=>{
 const updates={applicationResult:{attemptId:'a'.repeat(32),phase:'restart-ack',version:app.current,revision:app.runningRevision,verifiedAt:10},pendingSmartTools:[{runnerSource:true}],phase:'staged'};
 assert.equal(summary(updates).stage,'before');assert.equal(summary(updates).notes.version,'0.20.206');
 assert.equal(summary({...updates,pendingSmartTools:[{previous:'ordinary-tool'}]}).stage,'after');
 assert.equal(summary({...updates,pendingSmartTools:[],application:{...app,status:'current'}}).stage,'after');
 assert.equal(summary({...updates,pendingSmartTools:[]}).stage,'before');
});
test('ecosystem work does not claim a newer app is installing',()=>{
 const result=summary({phase:'validating',sequence:{stage:'included',install:true},diagnostics:{kind:'ecosystem'}});
 assert.equal(result.stage,'installed');assert.equal(result.version,app.current);
});
test('missing notes keep exact target and a safe release link without unrelated history',()=>{
 const result=summary({pendingRestart:{version:'0.20.204'}});
 assert.equal(result.version,'0.20.204');assert.equal(result.notes,undefined);
 assert.equal(result.url,'https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.204');
});

test('failed optional tool does not conceal newer app notes',()=>{
 const result=summary({phase:'available',pendingSmartTools:[{previous:'stories-old',failure:{reason:'smart-tool-update-failed'}}]});
 assert.equal(result.stage,'before');assert.equal(result.notes.version,'0.20.206');
});
