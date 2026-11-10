import test from 'node:test';
import assert from 'node:assert/strict';
import {createPendingView} from '../../.build/web-client/src/pending-view.js';
import {acceptedCanvasReceipt,settleCanvasReceipts,assertCanvasVisibility,acceptedCanvasVisibility} from './canvas-test-receipts.mjs';

test('receipt barrier accepts only the exact fully read accepted command',async()=>{
 const row={request:{id:'close-details'},status:200,body:{accepted:true}},entry={row,settled:Promise.resolve()};
 assert.equal(await acceptedCanvasReceipt(entry,'close-details'),row);
 await assert.rejects(()=>acceptedCanvasReceipt(entry,undefined),/must have an id/);
 await assert.rejects(()=>acceptedCanvasReceipt(entry,'different-command'),/clicked command/);
 for(const bad of [{body:{accepted:false}},{readError:'lost body'},{transportError:'disconnected'},{body:undefined}]){
  await assert.rejects(()=>acceptedCanvasReceipt({row:{...row,...bad},settled:Promise.resolve()},'close-details'));
 }
});
test('receipt barrier fails an unsettled body at the existing eight-second bound',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const result=acceptedCanvasReceipt({row:{request:{id:'close-details'},status:200},settled:new Promise(()=>{})},'close-details');
 const failed=assert.rejects(result,/remained unsettled/);
 t.mock.timers.tick(8000);await failed;
});
test('all-receipt drain records and fails any unsettled response without clearing it',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const row={request:{id:'late-action',action:'view.update'},status:200};
 const failed=assert.rejects(settleCanvasReceipts([{row,settled:new Promise(()=>{})}]),/Every observed receipt must settle/);
 t.mock.timers.tick(8000);await failed;assert.match(row.receiptTimeout,/view.update late-action/);assert.equal(row.body,undefined);
});
test('visibility acceptance rejects the actual optimistic projection and waits for the exact receipt',async()=>{
 const expected={commandId:'reopen',clientId:'client',hostInstanceId:'host',sessionId:'chat',canvasId:'document',open:true};
 const server={revision:5,client:{id:'client',hostInstanceId:'host'},selectedSessionId:'chat',canvas:{id:'document',open:false},view:{}};
 const pending=createPendingView();pending.addCanvas(server,true);
 const painted=pending.apply(server);
 assert.equal(painted.canvas.open,true,'The old DOM-only inference would already pass');
 assert.throws(()=>assertCanvasVisibility(painted,expected),/still awaits acceptance/);
 assert.throws(()=>assertCanvasVisibility(server,expected),/Server visibility/);
 let finish,reads=0;
 const row={request:{id:'reopen',action:'canvas.visibility',args:{sessionId:'chat',canvasId:'document',open:true}},status:200};
 const entry={row,settled:new Promise(resolve=>{finish=resolve})};
 const accepted={...server,revision:6,canvas:{...server.canvas,open:true}};
 const result=acceptedCanvasVisibility(entry,expected,async()=>{reads++;return accepted});
 await Promise.resolve();assert.equal(reads,0,'No authoritative-read success before the receipt settles');
 row.body={accepted:true,revision:6,hostInstanceId:'host'};finish();assert.equal((await result).snapshot,accepted);
 for(const changed of [{canvas:{...accepted.canvas,open:false}},{canvas:{...accepted.canvas,visibilityPending:true}},{revision:5},{client:{id:'other',hostInstanceId:'host'}},{selectedSessionId:'other'}]){
  await assert.rejects(()=>acceptedCanvasVisibility(entry,expected,async()=>({...accepted,...changed})));
 }
 let invalidReads=0;
 await assert.rejects(()=>acceptedCanvasVisibility({...entry,row:{...row,body:{...row.body,accepted:false}}},expected,async()=>{invalidReads++;return accepted}),/explicitly accept/);
 assert.equal(invalidReads,0,'A rejected receipt cannot qualify by server readback');
 await assert.rejects(()=>acceptedCanvasVisibility(entry,{...expected,commandId:'other'},async()=>accepted),/clicked command/);
});
