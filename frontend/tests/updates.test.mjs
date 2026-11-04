import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
const {diagnosticReceipt,reconciledFailure}=await server.ssrLoadModule('/src/update-diagnostics.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

const application={id:'application',kind:'app',label:'Amplifier Unified',current:'0.6.3',latest:'v0.6.3',status:'current',channel:'github-releases'};
const component={id:'bundle',label:'Community bundle',status:'update',current:'aaaaaaa',latest:'bbbbbbb'};
function state(updates={}){return {view:{},settings:{updates:{}},updates:{application,items:[],...updates}};}
function render(updates={},view={}){return renderToStaticMarkup(React.createElement(UpdateSettings,{state:{...state(updates),view},act:()=>{}}));}

test('confirmed app success stays visible beside component failure without opening details',()=>{
 const result={attemptId:'a'.repeat(32),phase:'restart-ack',version:application.current,revision:'b'.repeat(40),verifiedAt:10};
 const updates={application:{...application,runningRevision:result.revision},applicationResult:result,
  phase:'error',error:'Component sources changed.',sequence:{stage:'other'},
  diagnostics:{attemptId:'component',batch:{attemptId:'component',kind:'ecosystem',components:['foundation']},
   lastFailure:{attemptId:'component',kind:'ecosystem',phase:'ecosystem-validation',status:'failed',reason:'runtime-source-changed',at:12}}};
 const html=render(updates);
 assert.match(html,/Application updated successfully/);
 assert.match(html,/Some components were not updated/);
 assert.match(html,/Affected batch: foundation/);
 assert.match(html,/Check for updates again before retrying/);
 assert.ok(html.indexOf('Application updated successfully')<html.indexOf('ecosystem-update-details'));
 assert.doesNotMatch(render({...updates,pendingRestart:{requestStatus:'uncertain'}}),/Application updated successfully|Some components were not updated/);
 assert.doesNotMatch(render({...updates,applicationResult:{...result,phase:'restart-reconcile'}}),/Application updated successfully/);
 assert.match(render({...updates,applicationResult:{...result,phase:'restart-reconcile'}}),/Current application verified/);
});

test('current batch names progress and completed batches remain visible without claiming the new batch installed',()=>{
 const html=render({phase:'validating',sequence:{stage:'other',install:true},
  diagnostics:{attemptId:'new',batch:{attemptId:'new',kind:'ecosystem',tier:'other',components:['amplifier-hooks-approval'],componentCount:1,startedAt:1},
   completedBatches:[{attemptId:'old',kind:'ecosystem',tier:'included',components:['foundation'],completedAt:2}]},
  probeProgress:{attemptId:'new',phase:'prepare',completed:24,total:37}});
 assert.match(html,/24 of 37 completed/);assert.match(html,/Current batch: amplifier-hooks-approval/);
 assert.match(html,/Installation history/);assert.match(html,/Included components installed at/);
 assert.doesNotMatch(html,/Other components installed at/);
});
test('prior failure is labeled historical while a different attempt is progressing',()=>{
 const html=render({phase:'validating',diagnostics:{attemptId:'new',lastFailure:{attemptId:'old',phase:'ecosystem-prepare',status:'failed'}}});
 assert.match(html,/This is a different update attempt/);assert.doesNotMatch(html,/Last update did not finish:/);
});

test('update frequency defaults to four hours and submits each supported interval through settings',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state(),act:(name,args)=>calls.push({name,args})}))});
 const select=root.root.findByProps({id:'update-frequency'});
 assert.equal(select.props.value,4);
 assert.deepEqual(select.findAllByType('option').map(option=>[option.props.value,option.children.join('')]),[['1','Every 1 hour'],['4','Every 4 hours'],['8','Every 8 hours'],['24','Daily']]);
 assert.deepEqual(calls,[]);
 for(const value of ['1','4','8','24'])await renderAct(async()=>select.props.onChange({target:{value}}));
 assert.deepEqual(calls,[1,4,8,24].map(intervalHours=>({name:'settings.update',args:{patch:{updates:{intervalHours}}}})));
 await renderAct(async()=>root.unmount());
});

test('saved older update intervals are explained without silently choosing or submitting a replacement',async()=>{
 for(const intervalHours of [6,168]){
  const calls=[];let root;const initial={...state(),settings:{updates:{intervalHours}}};
  await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:initial,act:(name,args)=>calls.push({name,args})}))});
  const select=root.root.findByProps({id:'update-frequency'});
  assert.equal(select.props.value,'');
  assert.equal(select.props['aria-describedby'],'update-frequency-saved');
  assert.deepEqual(select.findAllByType('option').filter(option=>!option.props.disabled).map(option=>option.props.value),['1','4','8','24']);
  assert.match(root.root.findByProps({id:'update-frequency-saved'}).children.join(''),new RegExp(`every ${intervalHours} hours`));
  assert.deepEqual(calls,[]);
  await renderAct(async()=>root.unmount());
 }
 const savedDaily={...state(),settings:{updates:{intervalHours:24}}};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:savedDaily,act:()=>{}}));
 assert.match(html,/<option value="24" selected="">Daily<\/option>/);
 assert.doesNotMatch(html,/update-frequency-saved/);
});

const release={version:'0.6.4',title:'Shared settings',changes:['Workspace settings now apply.'],notices:[{id:'shared',title:'Review shared settings',detail:'A setting affects other apps.',action:'Check your workspace.'}]};
const noticeId='release-notice:0.6.4:shared';
test('initial compact state waits for note details instead of reporting them missing',()=>{
 assert.match(render(),/Loading release notes…/);
 assert.doesNotMatch(render(),/Release notes for this version are unavailable/);
 assert.match(render({application:{...application,releaseNotes:[]}}),/Release notes for this version are unavailable/);
});
test('release summary is before controls and outside disclosures without marking notices read',async()=>{
 const calls=[];let root;
 const initial=state({application:{...application,status:'update',latest:'v0.6.4',releaseNotes:[release]}});
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:initial,act:(...args)=>calls.push(args)}))});
 const card=root.root.findByProps({'data-part':'update-release-summary'});
 assert.match(card.findByType('li').children.join(''),/Workspace settings now apply/);
 for(let parent=card.parent;parent;parent=parent.parent)assert.notEqual(parent.type,'details');
 assert.deepEqual(calls,[]);
 const html=render(initial.updates);
 assert.ok(html.indexOf('data-part="update-release-summary"')<html.indexOf('data-part="update-controls"'));
 assert.ok(html.indexOf('aria-label="Changelog"')<html.indexOf('data-part="ecosystem-update-details"'));
 await renderAct(async()=>root.unmount());
});
test('changelog separates installed, upcoming and earlier versions and escapes authored text',()=>{
 const html=render({application:{...application,releaseNotes:[release,{...release,version:'0.6.3',notices:[]},{...release,version:'0.6.2',title:'<script>bad()</script>',notices:[]}]}});
 assert.match(html,/Changelog/);assert.match(html,/Upcoming/);assert.match(html,/Earlier release/);
 assert.match(html,/Check your workspace/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
 assert.match(html,/available offline/);
});

test('reviewing a high-impact notice uses its fingerprint and retains the changelog',async()=>{
 const calls=[];let root;
 const initial={...state({application:{...application,releaseNotes:[release],status:'update'}}),attention:{items:[{id:noticeId,read:false,fingerprint:'notice-v1'}]}};
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:initial,act:(name,args)=>calls.push({name,args})}))});
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='attention.read').length,0);
 assert.deepEqual(calls,[]);
 const reviewed={...initial,attention:{items:[{...initial.attention.items[0],read:true}]}};
 await renderAct(async()=>root.update(React.createElement(UpdateSettings,{state:reviewed,act:()=>{}})));
 assert.equal(root.root.findAllByProps({'aria-label':'High-impact changes'}).length,1);
 assert.match(JSON.stringify(root.toJSON()),/Check your workspace/);
 assert.match(JSON.stringify(root.toJSON()),/Reviewed/);
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='updates.install')[0].props.disabled,false);
 await renderAct(async()=>root.unmount());
});

test('missing remote notes are honest without hiding installed history or disabling installation',()=>{
 const html=render({application:{...application,status:'update',latest:'v0.6.4',releaseNotes:[release],releaseNotesWarning:'Release notes for the available update could not be loaded.'}});
 assert.match(html,/could not be loaded/);assert.match(html,/View published release/);
 assert.match(html,/<button class="a-primary" data-action="updates.install"/);
});

test('installed app and published release remain visible with source inventory closed',()=>{
 const html=render({items:Array.from({length:87},(_,id)=>({id:String(id),label:'Hidden source '+id,status:'current'}))});
 assert.match(html,/Overall update status/);
 assert.match(html,/Amplifier 0\.6\.3/);
 assert.doesNotMatch(html,/Latest v0\.6\.3/);
 assert.match(html,/Latest release installed/);
 assert.match(html,/Advanced details/);
 assert.match(html,/Show all 87 sources/);
 assert.doesNotMatch(html,/Hidden source 0/);
});

test('app ahead of the published release is honest about release-channel lag',()=>{
 const html=render({application:{...application,latest:'v0.5.6',releaseBehind:true,detail:'The installed version is newer than the latest published release.'}});
 assert.match(html,/Ahead of published release/);
 assert.match(html,/v0\.5\.6/);
 assert.doesNotMatch(html,/Latest release installed|Install app update/);
});

test('app version is visible before checking, and failed checks retain a clear result',()=>{
 const before=render({application:{...application,status:'not_checked',latest:null}});
 assert.match(before,/0\.6\.3/);
 assert.match(before,/Not checked/);
 const failed=render({application:{...application,status:'check_failed',detail:'Sign in with GitHub CLI to check releases.'}});
 assert.match(failed,/a-check-result error/);
 assert.match(failed,/Sign in with GitHub CLI/);
 assert.match(failed,/0\.6\.3/);
 assert.doesNotMatch(failed,/Latest release installed/);
});

test('application and component updates have separate effects and no duplicate app row',()=>{
 const app={...application,status:'update',latest:'v0.6.4'};
 const html=render({application:app,items:[app,component],appAvailable:true});
 assert.equal((html.match(/data-part="application-update"/g)||[]).length,1);
 assert.match(html,/Update Amplifier/);
 assert.match(html,/1 available/);
 assert.match(html,/Community bundle/);
 assert.match(html,/Restarts wait until your work is finished/);
 assert.match(html,/Show all 1 source/);
});

test('staged app, restart, interrupted install and successful restart report their real states',()=>{
 const pendingApp={...application,status:'update',latest:'v0.6.4'};
 const staged=render({pendingApp,phase:'app-staged',detail:'Waiting for active work to finish.'});
 assert.match(staged,/Ready to restart/);
 assert.match(staged,/smart tools/);
 assert.match(staged,/Waiting for your work to finish/);
 assert.doesNotMatch(staged,/a-check-result success/);
 const restarting=render({phase:'activating',pendingRestart:{version:'0.6.4'},detail:'Application installed. Restarting the local host…'});
 assert.match(restarting,/Restarting Amplifier/);
 assert.match(restarting,/Application installed. Restarting the local host/);
 assert.match(restarting,/a-progress-spinner/);
 assert.match(restarting,/id="update-action-status"[^>]*>Waiting for a healthy restarted app/);
 const interrupted=render({phase:'interrupted',detail:'Application installation was interrupted.'});
 assert.match(interrupted,/a-check-result error/);
 assert.match(interrupted,/Application installation was interrupted/);
 const installed=render({phase:'installed',detail:'Application update installed and restarted successfully.'});
 assert.match(installed,/a-check-result success/);
 assert.match(installed,/installed and restarted successfully/);
});

test('prepared app failure is visibly an error and allows the existing install action to retry',async()=>{
 const calls=[];
 let root;
 const app={...application,status:'update',latest:'v0.6.4'};
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state({pendingApp:app,phase:'error',error:'App activation failed. Retry when idle.'}),act:(name,args)=>calls.push({name,args})}))});
 const button=root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='updates.install')[0];
 assert.equal(button.props.disabled,false);
 assert.match(JSON.stringify(root.toJSON()),/App activation failed/);
 await renderAct(async()=>button.props.onClick());
 assert.equal(calls[0].name,'updates.install');
 await renderAct(async()=>root.unmount());
});

test('queued release refresh is read-only and only available before an app handoff',async()=>{
 const queued={pendingApp:{...application,status:'update',latest:'v0.6.4'},phase:'app-staged'};
 for(const patch of [{},{applicationCheckPending:true},{pendingApp:{...queued.pendingApp,featureSelection:{requestId:'chosen'}}},{pendingRestart:{version:'0.6.4'}},{pendingReplacement:{attemptId:'unknown'}},{phase:'staging'},{pendingRelease:'components'},{pendingSmartTools:[{id:'tool'}]}]){
  const calls=[];let root;
  try{
   await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state({...queued,...patch}),act:(action,args)=>calls.push({action,args})}))});
   const check=root.root.findByProps({'data-action':'updates.check'});
   assert.equal(check.props.disabled,Object.keys(patch).length>0,JSON.stringify(patch));
   assert.deepEqual(calls,[]);
   if(!check.props.disabled){assert.match(JSON.stringify(root.toJSON()),/Check for newer release/);await renderAct(async()=>check.props.onClick());assert.deepEqual(calls,[{action:'updates.check',args:undefined}])}
  }finally{await renderAct(async()=>root.unmount())}
 }
 const offline=render({...queued,applicationCheckWarning:'Could not check for a newer release. The prepared update is still available.'});
 assert.match(offline,/prepared update is still available/);assert.match(offline,/Waiting for your work to finish/);
 assert.doesNotMatch(offline,/Updates need attention/);
});

test('a rejected restart keeps its work gate but displays actionable failure instead of restarting',()=>{
 const html=render({phase:'activating',pendingRestart:{version:'0.6.4',requestStatus:'rejected'},
  error:'The app installed, but the managed restart request was rejected. Run amplifier-unified service restart.',
  detail:'Waiting for a healthy restarted host. New work remains paused.'});
 assert.match(html,/Needs attention/);
 assert.match(html,/needs help finishing its restart/);
 assert.match(html,/a-check-result error/);
 assert.match(html,/amplifier-unified service restart/);
 assert.doesNotMatch(html,/<button class="a-primary" data-action="updates.install"/);
 assert.doesNotMatch(html,/Restarting|this page will reconnect/);
});

test('unconfirmed restart request waits for readiness without promising completion',()=>{
 const html=render({phase:'activating',pendingRestart:{version:'0.6.4',requestStatus:'uncertain'},
  detail:'Restart request confirmation was interrupted. Waiting for a healthy restarted host.'});
 assert.match(html,/Awaiting restarted host/);
 assert.match(html,/Waiting for restart confirmation/);
 assert.match(html,/confirmation was interrupted/);
 assert.doesNotMatch(html,/this page will reconnect|installed and restarted successfully|Latest release installed/);
});

test('visible update controls use the shared action registry and only promise ecosystem rollback',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state({items:[component],canRollback:true}),act:(name,args)=>calls.push({name,args})}))});
 for(const action of ['updates.check','updates.install','updates.rollback']){
  const button=root.root.findAll(node=>node.type==='button'&&node.props['data-action']===action)[0];
  assert.equal(button.props.disabled,false);
  await renderAct(async()=>button.props.onClick());
 }
 assert.deepEqual(calls.map(call=>call.name),['updates.check','updates.install','updates.rollback']);
 assert.match(JSON.stringify(root.toJSON()),/Restore previous components/);
 await renderAct(async()=>root.unmount());
});

const failure={id:'failure',at:100,attemptId:'a'.repeat(32),commandId:'b'.repeat(32),kind:'application',phase:'replacement-probe',status:'failed',exitCode:1,durationMs:250,probe:{ok:false,stage:'imports',errorType:'ModuleNotFoundError'}};
test('failed update shows its phase and correlation ID with details collapsed',()=>{
 const html=render({diagnostics:{attemptId:failure.attemptId,lastFailure:failure,events:[failure]}});
 assert.match(html,/Check installed package/);
 assert.match(html,/ModuleNotFoundError/);
 assert.match(html,/Exit 1/);
 assert.match(html,new RegExp(failure.attemptId));
 assert.match(html,/View update details/);
 assert.doesNotMatch(html,/update-diagnostic-receipt/);
});

test('diagnostic disclosure is agent-controllable and displays only sanitized receipt fields',async()=>{
 const calls=[];let root;
 const diagnostics={attemptId:failure.attemptId,lastFailure:{...failure,stdout:'raw secret'},events:[{...failure,stdout:'raw secret',env:{secret:'hidden'}}]};
 const initial=state({diagnostics});
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:initial,act:(name,args)=>calls.push({name,args})}))});
 const toggle=root.root.findAll(node=>node.type==='button'&&node.props.className?.includes('a-update-diagnostics-toggle'))[0];
 await renderAct(async()=>toggle.props.onClick());
 assert.equal(calls[0].name,'view.update');
 assert.equal(calls[0].args.patch.maintenanceDraft.updateDiagnosticsExpanded,true);
 const expanded={...initial,view:{maintenanceDraft:{updateDiagnosticsExpanded:true}}};
 await renderAct(async()=>root.update(React.createElement(UpdateSettings,{state:expanded,act:()=>{}})));
 const receipt=root.root.findByProps({id:'update-diagnostic-receipt'});
 assert.equal(receipt.props.readOnly,true);
 assert.match(receipt.props.value,/replacement-probe/);
 assert.doesNotMatch(receipt.props.value,/raw secret|hidden|stdout"|env"/);
 const parsed=JSON.parse(receipt.props.value);
 assert.equal(parsed.lastFailure.commandId,failure.commandId);
 await renderAct(async()=>root.unmount());
});

test('completed retry retains history without showing an active failure notice',()=>{
 const done={...failure,id:'done',phase:'restart-ack',status:'succeeded',exitCode:0,probe:undefined};
 const html=render({diagnostics:{attemptId:done.attemptId,events:[failure,done],latest:done}});
 assert.match(html,/View update details/);
 assert.doesNotMatch(html,/a-update-failure"/);
});

test('restart request acceptance is distinct from readiness and acknowledgement',()=>{
 const events=[['service-restart-request','accepted'],['restart-readiness','uncertain'],['restart-ack','succeeded'],['restart-reconcile','succeeded'],['service-restart-request','rejected']].map(([phase,status],index)=>({id:String(index),attemptId:'request',phase,status}));
 const html=render({diagnostics:{attemptId:'request',events}},{maintenanceDraft:{updateDiagnosticsExpanded:true}});
 assert.match(html,/Request service restart/);
 assert.match(html,/Check restarted host readiness/);
 assert.match(html,/Confirm restarted version/);
 assert.match(html,/Verify current installation/);
 for(const [status,title] of [['accepted','Accepted'],['uncertain','Unconfirmed'],['rejected','Rejected']]){
  const row=html.match(new RegExp('<li class="'+status+'">(.*?)</li>'))?.[1]||'';
  assert.match(row,new RegExp(title));
  assert.doesNotMatch(row,/lucide-check/);
 }
});

const reconciliation={attemptId:failure.attemptId,version:application.current,revision:'c'.repeat(40),verifiedAt:101};
function reconciledUpdates(patch={}){
 const oldFailure={...failure,phase:'service-restart',exitCode:-15};
 return {phase:'installed',error:null,application:{...application,runningRevision:reconciliation.revision},
  reconciliation,diagnostics:{attemptId:failure.attemptId,lastFailure:oldFailure,events:[oldFailure,{id:'reconciled',attemptId:failure.attemptId,phase:'restart-reconcile',status:'succeeded'}]},...patch};
}
test('verified current installation labels retained restart failure as resolved historical evidence',()=>{
 const updates=reconciledUpdates();
 const html=render(updates,{maintenanceDraft:{updateDiagnosticsExpanded:true}});
 assert.equal(reconciledFailure(updates),true);
 assert.match(html,/a-update-failure resolved/);
 assert.match(html,/Previous update issue resolved/);
 assert.match(html,/current installation is verified healthy/);
 assert.match(html,/<li class="failed historical">/);
 assert.match(html,/Failed \(historical\)/);
 assert.match(html,/Exit -15/);
 assert.doesNotMatch(html,/Last update issue:|Confirm restarted version/);
 const receipt=JSON.parse(diagnosticReceipt(updates.diagnostics,updates.reconciliation));
 assert.equal(receipt.lastFailure.status,'failed');
 assert.equal(receipt.lastFailure.exitCode,-15);
 assert.deepEqual(receipt.reconciliation,reconciliation);
});

test('stale or incomplete reconciliation cannot hide a current failure',()=>{
 for(const patch of [
  {reconciliation:{...reconciliation,attemptId:'different'}},
  {reconciliation:{...reconciliation,version:'0.0.0'}},
  {reconciliation:{...reconciliation,revision:'different'}},
  {reconciliation:{...reconciliation,verifiedAt:undefined}},
  {error:'A new error'},
  {diagnostics:{lastFailure:{...failure,attemptId:'new-failed-attempt'}}},
  {pendingRestart:{version:application.current}},
  {pendingApp:{version:application.current}},
  {pendingReplacement:{}},
 ]){
  const updates=reconciledUpdates(patch);
  assert.equal(reconciledFailure(updates),false);
  const html=render(updates);
  assert.match(html,/Last update issue:/);
  assert.doesNotMatch(html,/Previous update issue resolved|a-update-failure resolved/);
 }
});

test('uncertain replacement never appears current or offers another installation',()=>{
 for(const phase of ['error','interrupted','activating']){
  const html=render({phase,pendingReplacement:{},canRollback:true});
  assert.match(html,/Needs attention/);
  assert.match(html,/installation outcome is unknown/);
  assert.match(html,/Work remains paused/);
  assert.match(html,/installation needs verification/);
  assert.doesNotMatch(html,/Latest release installed/);
  for(const action of ['updates.check','updates.install','updates.rollback']){
   const button=html.match(new RegExp('<button[^>]*data-action="'+action+'"[^>]*>'))?.[0]||'';
   assert.ok(!button||/disabled=""/.test(button));
  }
 }
});

test('a new update check does not turn a verified historical failure back into an active issue',()=>{
 for(const phase of ['checking','checked','available']){
  const updates=reconciledUpdates({phase});
  assert.equal(reconciledFailure(updates),true);
  const html=render(updates);
  assert.match(html,/Previous update issue resolved/);
  assert.doesNotMatch(html,/Last update issue:/);
 }
});

test('copied receipts include version revisions but exclude raw output and extra reconciliation fields',()=>{
 const event={...failure,expectedRevision:'c'.repeat(40),observedRevision:'d'.repeat(40),stdout:'secret output',path:'/private/folder'};
 const receipt=JSON.parse(diagnosticReceipt({attemptId:failure.attemptId,lastFailure:event,events:[event]},{...reconciliation,stdout:'secret output',path:'/private/folder'}));
 assert.equal(receipt.lastFailure.expectedRevision,event.expectedRevision);
 assert.equal(receipt.events[0].observedRevision,event.observedRevision);
 assert.equal(receipt.lastFailure.status,'failed');
 assert.doesNotMatch(JSON.stringify(receipt),/secret output|private\/folder|stdout"|path"/);
});

test('unknown usage keeps failure conditions visible separately even with inventory collapsed',()=>{
 const initial=state({items:[
   {id:'old',kind:'bundle / module',label:'Historical source',ref:'master',usage:'unknown',status:'check_failed'},
   {id:'dirty',kind:'bundle / module',label:'Edited source',ref:'main',usage:'unknown',status:'local_changes',detail:'Tracked source changes preserved.'},
   {id:'active',kind:'bundle / module',label:'Configured source',usage:'configured',status:'check_failed'},
 ],lastCheck:100});
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:initial,act:()=>{}}));
 assert.match(html,/Other cached sources/);
 assert.match(html,/2 sources: 1 check failed, 1 with local changes/);
 assert.match(html,/Historical source/);
 assert.match(html,/Tracked source changes preserved/);
 assert.match(html,/Needs attention/);
 assert.match(html,/Configured source/);
 assert.doesNotMatch(html,/aria-label="Current"|All sources.*current/);
 assert.match(html,/files may still be needed and are kept/);
});

test('unknown update remains installable while unknown pins and current status stay distinct',()=>{
 const html=render({items:[
   {...component,usage:'unknown'},
   {id:'pin',label:'Pinned dependency',usage:'unknown',status:'pinned'},
   {id:'current',label:'Current dependency',usage:'unknown',status:'current'},
 ]});
 assert.match(html,/1 available/);
 assert.match(html,/3 sources: 1 with updates, 1 pinned, 1 current/);
 assert.match(html,/data-action="updates.install"/);
 assert.doesNotMatch(html,/disabled="" data-action="updates.install"/);
 assert.doesNotMatch(html,/Needs attention/);
});

test('runtime preflight identifies the module and recovery action and completes parent progress',()=>{
 const attemptId='e'.repeat(32),commandId='f'.repeat(32);
 const parent={id:'start',attemptId,commandId,phase:'ecosystem-stage',status:'started'};
 const preflight={id:'blocked',attemptId,phase:'ecosystem-runtime-preflight',status:'failed',errorType:'ValueError',package:'amplifier-legacy-hooks',reason:'protected-runtime-source'};
 const events=[parent,{id:'checkout',attemptId,phase:'ecosystem-checkout',status:'succeeded'},preflight,{...parent,id:'end',status:'failed'}];
 const diagnostics={attemptId,lastFailure:preflight,events};
 const html=render({phase:'error',diagnostics},{maintenanceDraft:{updateDiagnosticsExpanded:true}});
 assert.match(html,/Last update issue: Check installed worker sources/);
 assert.match(html,/amplifier-legacy-hooks/);
 assert.match(html,/Local source changes or an explicit dependency override were preserved/);
 assert.match(html,/Bundles &amp; modules/);
 assert.doesNotMatch(html,/Running/);
 assert.match(html,/Switch staged source/);
 assert.equal(JSON.parse(diagnosticReceipt(diagnostics)).lastFailure.reason,'protected-runtime-source');
});


test('main update box summarizes ordered component phases without the source inventory',()=>{
 const html=render({lastCheck:100,sequence:{stage:'included',included:{status:'available',available:3,missing:1},other:{status:'waiting',available:0}}});
 const banner=html.slice(html.indexOf('data-part="application-update"'),html.indexOf('data-part="update-controls"'));
 assert.doesNotMatch(banner,/Included components/);
 assert.match(banner,/3 component updates are ready/);
 assert.doesNotMatch(banner,/Other components/);
 assert.match(banner,/Component updates prepare automatically/);
 assert.doesNotMatch(banner,/a-source-list/);
 assert.match(html,/Update Amplifier/);
});

test('app update leaves both component summaries waiting instead of claiming current',()=>{
 const html=render({sequence:{stage:'application',included:{status:'waiting'},other:{status:'waiting'}},application:{...application,status:'update'}});
 assert.equal((html.match(/After app update/g)||[]).length,2);
 assert.doesNotMatch(html,/Up to date/);
});

test('component check failures and explicit source policies remain visible in the banner',()=>{
 const html=render({sequence:{stage:'included',included:{status:'attention',issues:2},other:{status:'current',protected:1}}});
 assert.match(html,/2 need attention/);
 assert.match(html,/1 kept as configured/);
});


test('disabled update controls explain active component work without expanding details',()=>{
 const html=render({phase:'checking',detail:'Checking included components…',sequence:{stage:'included',nextStage:'included',install:true}});
 const top=html.slice(0,html.indexOf('data-part="ecosystem-update-details"'));
 assert.match(top,/Checking included components/);
 assert.match(top,/id="update-action-status"/);
 assert.match(top,/aria-describedby="update-action-status"/);
 assert.match(top,/this page will update automatically/);
 assert.match(top,/Checking…/);
});


test('every source list is alphabetical and grouped package roles remain inspectable',()=>{
 const items=[{id:'z',label:'org/amplifier-bundle-zebra',status:'current'}, {id:'a',label:'org/amplifier-foundation',status:'current'}, {id:'c',label:'org/amplifier-bundle-context-managed',status:'update',ref:'main',current:'aaaa',latest:'bbbb',kind:'source group',packageCount:2,cacheCopies:1,members:[{id:'cache',kind:'bundle / module'},{id:'one',package:'amplifier-context-managed',subdirectory:'modules/context',kind:'runtime dependency'},{id:'two',package:'amplifier-hooks-context',subdirectory:'modules/hooks',kind:'runtime dependency'}]}];
 const html=render({items},{maintenanceDraft:{updatesExpanded:true}});
 const full=html.slice(html.indexOf('id="update-sources-list"'));
 assert.ok(full.indexOf('amplifier-foundation') < full.indexOf('context-managed'));
 assert.ok(full.indexOf('context-managed') < full.indexOf('zebra'));
 assert.match(full,/2 packages/);
 assert.match(full,/modules\/context/);
 assert.match(full,/modules\/hooks/);
 assert.match(full,/runtime dependency/);
});

test('eligible Smart Tool update keeps its action after a prior install sequence',()=>{
 const html=render({phase:'available',sequence:{stage:'other',install:true},items:[{...component,kind:'smart-tool',usage:'installed',eligible:true}]});
 assert.match(html,/data-action="updates.install"/);
 assert.match(html,/Installed Smart Tool/);
 assert.doesNotMatch(html,/Usage unknown/);
});

test('prepared components keep duplicate install hidden and reveal retry on failure',async()=>{
 let root;
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state({phase:'staged',pendingRelease:'candidate',plan:{activation:'per-worker'}}),act:()=>{}}))});
 assert.equal(root.root.findAllByProps({'data-action':'updates.install'}).length,0);
 assert.equal(root.root.findByProps({'data-action':'updates.check'}).props.disabled,true);
 await renderAct(async()=>root.update(React.createElement(UpdateSettings,{state:state({phase:'error',pendingRelease:'candidate',error:'Activation failed'}),act:()=>{}})));
 assert.equal(root.root.findByProps({'data-action':'updates.install'}).props.disabled,false);
 await renderAct(async()=>root.unmount());
});

test('an error arriving during validation is never hidden by a progress summary',()=>{
 const html=render({phase:'validating',error:'Validation stopped',diagnostics:{attemptId:'new',batch:{attemptId:'new'}},probeProgress:{attemptId:'new',completed:1,total:2}});
 const banner=html.slice(0,html.indexOf('data-part="update-controls"'));
 assert.match(banner,/Updates need attention/);assert.doesNotMatch(banner,/Finishing your update/);
});

test('runner-only wait keeps availability refresh usable without exposing unrelated work as a blocker',async()=>{
 const runner={previous:'old',runnerSource:{runnerKind:'managed-dtu-core'},runnerUpdate:'check'};
 const base={phase:'staged',pendingSmartTools:[runner],blockers:[{kind:'conversation',sessionId:'one',label:'Unrelated chat'}]};
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state(base),act:(name,args)=>calls.push({name,args})}))});
 const check=root.root.findByProps({'data-action':'updates.check'});
 assert.equal(check.props.disabled,false);assert.equal(check.findByType('span').children.join(''),'Check for updates');
 assert.equal(root.root.findAllByProps({'data-part':'update-blockers'}).length,0);
 await renderAct(async()=>check.props.onClick());assert.deepEqual(calls,[{name:'updates.check',args:undefined}]);
 for(const change of [{pendingRelease:'candidate'},{pendingSmartTools:[runner,{previous:'ordinary-tool'}]},{phase:'validating'}]){
  await renderAct(async()=>root.update(React.createElement(UpdateSettings,{state:state({...base,...change}),act:()=>{}})));
  assert.equal(root.root.findByProps({'data-action':'updates.check'}).props.disabled,true);
 }
 await renderAct(async()=>root.unmount());
});

test('runner wait leaves Update Amplifier actionable and retains its separate result',async()=>{
 const runner={previous:'old',runnerSource:{runnerKind:'managed-dtu-core'},runnerUpdate:'check',runnerWaiting:'unknown'};
 const updates={phase:'available',application:{status:'update',current:'0.20.205',latest:'v0.20.207'},pendingSmartTools:[runner]};
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state(updates),act:name=>calls.push(name)}))});
 const install=root.root.findByProps({'data-action':'updates.install'});
 assert.equal(install.props.disabled,false);
 assert.equal(root.root.findByProps({'data-action':'updates.check'}).props.disabled,false);
 assert.equal(root.root.findAllByProps({'aria-label':'Smart Tool update results'}).length,1);
 await renderAct(async()=>install.props.onClick());assert.deepEqual(calls,['updates.install']);
 await renderAct(async()=>root.unmount());
});


test('prepared source mismatch explains preservation and exports only mismatch field names',()=>{
 const failure={id:'mismatch',attemptId:'candidate',phase:'ecosystem-validation',status:'failed',
  errorType:'ValueError',reason:'runtime-installed-source-mismatch',package:'amplifier-foundation',
  resolutionMismatches:['revision','private/source', {path:'/private/cache'},'revision'],
  expectedRevision:'a'.repeat(40),observedRevision:'b'.repeat(40),
  path:'/private/cache',message:'credential-bearing failure'};
 const diagnostics={attemptId:'candidate',lastFailure:failure,events:[failure]};
 const html=render({phase:'error',diagnostics});
 assert.match(html,/amplifier-foundation/);
 assert.match(html,/component changed to a different source during update preparation/);
 assert.match(html,/kept the current components active/);
 assert.match(html,/share the update details/);
 assert.match(html,/Expected revision a{12}/);
 assert.match(html,/Found revision b{12}/);
 const exported=JSON.parse(diagnosticReceipt(diagnostics));
 assert.deepEqual(exported.lastFailure.resolutionMismatches,['revision']);
 assert.deepEqual(exported.events[0].resolutionMismatches,['revision']);
 assert.equal(exported.lastFailure.expectedRevision,'a'.repeat(40));
 assert.equal(exported.lastFailure.observedRevision,'b'.repeat(40));
 assert.doesNotMatch(JSON.stringify(exported),/private|credential/);
});

test('authored constraint names the source check without claiming local edits',()=>{
 const failure={id:'constraint',attemptId:'candidate',phase:'ecosystem-source-preflight',status:'failed',
  reason:'runtime-source-constraint',package:'amplifier-foundation'};
 const diagnostics={attemptId:'candidate',lastFailure:failure,events:[failure]};
 const html=render({phase:'error',diagnostics});
 assert.match(html,/amplifier-foundation/);
 assert.match(html,/could not be reconciled with the selected component/);
 assert.match(html,/does not establish local edits/);
 assert.doesNotMatch(html,/Local source changes or an explicit dependency override were preserved/);
 const receipt=JSON.parse(diagnosticReceipt(diagnostics));
 assert.equal(receipt.lastFailure.reason,'runtime-source-constraint');
});


test('unresolved component attention opens details consistently without downgrading the installed app',()=>{
 const initial=state({phase:'checked',sequence:{stage:'complete'}});
 initial.attention={items:[{id:'updates:failure:old',page:'updates',actionRequired:true,read:true,
  title:'Component update needs attention',detail:'A saved component failure is unresolved.'}]};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:initial,act:()=>{}}));
 assert.match(html,/data-part="ecosystem-update-details" open=""/);
 assert.match(html,/Advanced details · review issue/);
 assert.match(html,/Component update needs attention/);
 assert.match(html,/Latest release installed/);
 assert.doesNotMatch(html,/You’re up to date|Ready for new work/);
});
test('batch presentation bounds full recovery scopes without altering the canonical receipt',()=>{
 const components=Array.from({length:8},(_,i)=>`component-${i+1}`);
 const batch={attemptId:'batch',kind:'ecosystem',tier:'other',components,componentCount:8,startedAt:1};
 const updates={phase:'validating',sequence:{stage:'other'},diagnostics:{attemptId:'batch',batch,
  completedBatches:[{...batch,attemptId:'complete',completedAt:2}]}};
 const before=JSON.stringify(updates),html=render(updates);
 assert.match(html,/component-1, component-2, component-3, component-4, component-5 and 3 more/);
 assert.doesNotMatch(html,/component-6|component-7|component-8/);
 assert.equal(JSON.stringify(updates),before);
});
