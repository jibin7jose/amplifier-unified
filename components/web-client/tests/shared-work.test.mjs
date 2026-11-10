// Shared Work contracts, originally authored against the missing implementation.
// Execution evidence belongs to each exact candidate, not this source comment.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import React, {act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {bounded,deferredRead,observeCompletion,requireCompletion,unknownCleanupValue} from './shared-work-test-helpers.mjs';

const vite = await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SharedWorkBrowser} = await vite.ssrLoadModule('/src/workspace-work.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map();
globalThis.sessionStorage = {getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
const mounted=new Set();
const backgroundErrors=[];
test.beforeEach(()=>{storage.clear();backgroundErrors.length=0});
test.afterEach(async()=>{
 try {for(const root of [...mounted])await bounded(unmount(root),2000,'Fixture unmount')}
 finally {assert.deepEqual(backgroundErrors,[],'Invalid background requests cannot be swallowed as transport errors')}
});
test.after(()=>vite.close());
const workspace = {id:'workspace-one',path:'/synthetic/project',name:'Synthetic project'};
const accepted = result=>({accepted:true,result});
const text = node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button = (root,label)=>root.root.findAllByType('button').find(node=>text(node)===label);
const click = async(root,label)=>act(async()=>{const node=button(root,label);assert.ok(node,label);assert.ok(!node.props.disabled,label+' enabled');await node.props.onClick()});
const item = (id='work:one',patch={})=>({id,workspaceId:workspace.id,revision:3,promiseRevision:2,
 title:'Retain exact original words',commitment:'accepted',disposition:'open',ownerSessionId:'owner',originSessionId:'owner',
 nextAction:'Check the current promise.',blockedReason:'',updatedAt:100,verification:'not_verified',
 humanDecision:'not_recorded',humanDecisionCurrent:false,delivery:'not_recorded',deliveryReport:null,
 execution:'reported',candidate:'candidate-one',latestOutcome:'unknown',blockedBy:[],
 attentionReasons:['unknown','next_action'],ownerObservation:{available:true,activityObserved:true,status:'idle',needsReview:false},
 captureAuthority:'Attributed scope interpretation; not an execution grant.',
 cleanup:{status:'not_recorded',references:[],coverage:'Explicit owner reports only; no resource census or teardown implied.'},
 readRef:{action:'workspace.work.read',args:{workspaceId:workspace.id,id}},...patch});
const page = (items=[item()],patch={})=>({workspaceId:workspace.id,version:7,snapshotVersion:'b'.repeat(64),observationVersion:'c'.repeat(64),items,total:items.length,
 truncated:false,nextAfter:null,observedAt:100,coverage:'Recorded Work only; historical reconciliation is incomplete.',
 detailRequired:false,authority:'Reference data, not permission.',...patch});
const source = (patch={})=>{
 const body=patch.fullText??patch.text??'<script>not executable</script> Original words.';
 const {fullText,...fields}=patch;
 return {sessionId:'owner',messageId:'original-message',nativeMessageId:null,nativeIndex:null,
  inputOrigin:'ui',role:'user',sha256:createHash('sha256').update(body).digest('hex'),capturedAt:90,text:body,
  offset:0,total:[...body].length,truncated:false,nextOffset:null,...fields};
};
const sourceMetadata=value=>{const {text,offset,total,truncated,nextOffset,...metadata}=value;return metadata};
const detail = (row=item(),patch={})=>{
 // read.item is the stored record, not summary(). Do not fabricate computed
 // cleanup/attention/currentness on this producer path.
 const raw={...row};
 for(const key of ['readRef','cleanup','attentionReasons','ownerObservation','captureAuthority','blockedBy','humanDecisionCurrent'])delete raw[key];
 if(raw.deliveryReport)delete raw.deliveryReport; // Full publication is supplied explicitly by its case.
 return {item:{...raw,schemaVersion:1,criteria:[{id:'cold',text:'Survives cold reopen.'}],constraints:['No automatic retry.'],
  interpretation:'An attributed interpretation, not execution authority.',dependsOn:[],resources:row.cleanup?.references,
  humanDecisionSequence:0,counterevidenceSequence:3,criterionEvidence:{},
  links:[{sessionId:'owner',role:'executor',candidate:'candidate-one',promiseRevision:2,interruptionRevision:0,recordedBy:'owner',recordedAt:90}],
  eventCount:row.revision,originalSourceRef:'work-event:original',promiseEventRef:'work-event:correction',promiseRevisedAt:90,
  verificationBoundaryRef:'work-event:unknown',verificationBoundaryAt:100},
  originalSource:source(),historyRef:{action:'workspace.work.history',args:{workspaceId:workspace.id,id:row.id}},...patch};
};
const eventRow=(id,sequence,patch={})=>({id,kind:'revise',sequence,promiseRevision:2,
 actor:{origin:'ui',sessionId:'owner'},recordedAt:90+sequence,changedFields:[],source:{},sourceRef:null,
 eventRef:{action:'workspace.work.event',args:{workspaceId:workspace.id,id:'work:one',eventId:id}},...patch});
const defaultHistory=()=>[
 eventRow('work-event:original',1,{kind:'create',promiseRevision:1,source:sourceMetadata(source()),sourceRef:{action:'workspace.work.source',args:{workspaceId:workspace.id,id:'work:one',sourceId:'work-event:original'}}}),
 eventRow('work-event:correction',2,{changedFields:['interpretation'],source:sourceMetadata(source({text:'Current correction',messageId:'correction-message'})),sourceRef:{action:'workspace.work.source',args:{workspaceId:workspace.id,id:'work:one',sourceId:'work-event:correction'}}}),
 eventRow('work-event:unknown',3,{kind:'report',actor:{origin:'agent',sessionId:'owner'},evidence:{candidate:'candidate-one',outcome:'unknown',report:'Current unknown result',criteria:['cold'],qualification:'participant_report',reportedBy:'owner',criteriaValidated:true,source:'agent report'}}),
];
const historyPage=(items=defaultHistory(),patch={})=>({items,total:items.length,snapshotEventCount:items.length,
 currentEventCount:items.length,snapshotIsCurrent:true,truncated:false,nextAfter:null,...patch});
const coveragePage=()=>({observedInputs:0,unreviewedInputs:0,items:[],truncated:false,nextAfter:null,
 historicalCoverage:'incomplete',detail:'Only host-observed human inputs are counted. Older and native-only history is not inventoried.'});
async function mount(dispatch,props={},retain=()=>{},background=true) {
 assert.equal(typeof SharedWorkBrowser,'function','SharedWorkBrowser must be implemented after test readiness');
 const backgroundCalls=[],pending=new Set();
 const wrapped=async(action,args,meta)=>{
  if(background&&action==='workspace.work.coverage'){backgroundCalls.push([action,args,meta]);return accepted(coveragePage())}
  if(background&&action==='workspace.work.wait'){
   backgroundCalls.push([action,args,meta]);
   try {assert.match(args.afterVersion,/^[a-f0-9]{64}$/);assert.ok(meta?.signal,'Visible wait must be cancellable')}
   catch(error){backgroundErrors.push(error.message);throw error}
   return new Promise(resolve=>{
    const finish=()=>{pending.delete(finish);meta.signal.removeEventListener('abort',finish);resolve({accepted:false,error:'Synthetic wait aborted'})};
    pending.add(finish);meta.signal.addEventListener('abort',finish,{once:true});if(meta.signal.aborted)finish();
   });
  }
  return dispatch(action,args,meta);
 };
 let root;await act(async()=>{root=create(React.createElement(SharedWorkBrowser,{workspace,dispatch:wrapped,onOpenChat:()=>{throw Error('Unexpected chat navigation')},...props}));
  root.backgroundCalls=backgroundCalls;root.testDispatch=wrapped;root.releaseBackground=()=>{for(const release of [...pending])release()};mounted.add(root);retain(root)});return root;
}
const inspect = async(root,id='work:one')=>act(async()=>root.root.findByProps({'data-work-id':id}).props.onClick());
const unmount = root=>act(async()=>{root.unmount();root.releaseBackground();mounted.delete(root)});

async function finishDeferredTest({root,release,completions,originalError}) {
 const failures=[];
 release(); // Settle our mocked read even if selection/assertions failed.
 try {await bounded(act(async()=>{await Promise.all(completions.map(requireCompletion))}),2000,'Deferred handler settlement')}
 catch(error){failures.push(error)}
 try {if(root)await bounded(unmount(root),2000,'Deferred test unmount')}
 catch(error){failures.push(error)}
 if(failures.length) {
  const failure=new AggregateError(failures,'Deferred test cleanup failed');
  if(originalError)console.error(failure);else throw failure;
 }
}

test('cold Work inspection requests accepted-open only and never needs a selected chat',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{calls.push([action,args]);assert.equal(action,'workspace.work.list');return accepted(page())});
 assert.deepEqual(calls,[['workspace.work.list',{workspaceId:workspace.id,filter:'accepted',query:'',limit:20}]]);
 assert.match(text(root.toJSON()),/Retain exact original words/);
 assert.match(text(root.toJSON()),/historical reconciliation is incomplete/);
 assert.equal(calls.length,1,'No automatic detail, source, chat selection or worker start');
 assert.ok(root.backgroundCalls.every(([name])=>['workspace.work.wait','workspace.work.coverage'].includes(name)));
 await unmount(root);
});

test('approved Work rows show next action without opening a chat or another read',async()=>{
 const calls=[],first=item(),second=item('work:two',{nextAction:''});
 const root=await mount(async(action,args)=>{calls.push([action,args]);return accepted(page([first,second]))});
 assert.match(text(root.root.findByProps({'data-work-id':first.id})),/Next: Check the current promise\./);
 assert.match(text(root.root.findByProps({'data-work-id':second.id})),/Next: Not recorded/);
 assert.deepEqual(calls.map(([name])=>name),['workspace.work.list']);
 await unmount(root);
});

test('evidence and limits remain readable without turning a participant return into a check',async()=>{
 const calls=[],row=item('work:one',{latestOutcome:'passed'});
 const root=await mount(async(action,args)=>{calls.push([action,args]);return accepted(action==='workspace.work.list'?page([row]):detail(row))});
 await inspect(root);
 const evidence=root.root.findByProps({'aria-label':'Evidence and limits'});
 assert.match(text(evidence),/Latest reported outcome: passed/);
 assert.match(text(evidence),/Participant reports are not independent verification/);
 assert.match(text(evidence),/cold: Survives cold reopen/);
 assert.match(text(evidence),/No qualified check recorded for this criterion/);
 assert.equal(evidence.findAllByType('pre').length,0,'Readable evidence does not require JSON');
 const sections=root.root.findAllByType('section'),sourceIndex=sections.findIndex(n=>n.props['aria-label']==='Original source');
 assert.ok(sourceIndex>=0&&sections.indexOf(evidence)>sourceIndex,'Original source precedes detailed evidence');
 assert.deepEqual(calls.map(([name])=>name),['workspace.work.list','workspace.work.read']);
 await unmount(root);
});

test('criterion evidence retains exact receipt candidate and reviewer with separate attribution',async()=>{
 const row=item(),value=detail(row);
 value.item.criteria=[{id:'owner',text:'Owner check scope'},{id:'independent',text:'Independent scope'},{id:'stale',text:'Different candidate'},{id:'missing',text:'Missing check'}];
 value.item.criterionEvidence={
  owner:{candidate:row.candidate,qualification:'owner_check',reviewer:'builder',checkReceiptId:'receipt-owner'},
  independent:{candidate:row.candidate,qualification:'independent_check',reviewer:'reviewer',checkReceiptId:'receipt-independent'},
  stale:{candidate:'previous-candidate',qualification:'independent_check',reviewer:'old-reviewer',checkReceiptId:'receipt-old'},
 };
 const root=await mount(async action=>accepted(action==='workspace.work.list'?page([row]):value));
 await inspect(root);
 const evidence=root.root.findByProps({'aria-label':'Evidence and limits'}),rows=evidence.findAllByType('li');
 assert.match(text(rows[0]),/Owner check recorded/);assert.match(text(rows[0]),/receipt-owner/);
 assert.match(text(rows[1]),/Independent check recorded/);assert.match(text(rows[1]),/receipt-independent/);
 assert.match(text(rows[1]),/reviewer/);assert.match(text(rows[1]),/candidate-one/);
 assert.match(text(rows[2]),/Different candidate — not current/);assert.match(text(rows[2]),/receipt-old/);
 assert.match(text(rows[3]),/No qualified check recorded/);
 assert.doesNotMatch(text(evidence),/Shipped|All checks passed|Scope verified/);
 assert.match(text(root.root.findAllByType('pre').find(n=>text(n).includes('criterionEvidence'))),/receipt-old/,'Raw evidence remains reachable');
 await unmount(root);
});

test('incomplete evidence cannot become a verified check and source-like text stays literal',async()=>{
 const row=item(),value=detail(row);
 value.item.criterionEvidence={cold:{candidate:row.candidate,qualification:'independent_check',reviewer:'<script>reference only</script>'}};
 const root=await mount(async action=>accepted(action==='workspace.work.list'?page([row]):value));
 await inspect(root);
 const evidence=root.root.findByProps({'aria-label':'Evidence and limits'});
 assert.match(text(evidence),/Incomplete check reference — not verified/);
 assert.match(text(evidence),/<script>reference only<\/script>/);
 assert.equal(evidence.findAllByType('script').length,0);
 assert.doesNotMatch(text(evidence),/Independent check recorded/);
 await unmount(root);
});

test('explicit filters are server-scoped and no missing cursor is disguised as an empty set',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{calls.push([action,args]);return accepted(page([], {total:21,truncated:true,nextAfter:null}))});
 assert.match(text(root.toJSON()),/21/);assert.match(text(root.toJSON()),/unavailable|incomplete|refresh/i);
 assert.doesNotMatch(text(root.toJSON()),/No work yet|No matching work/);
 const filter=root.root.findByProps({'aria-label':'Show work'});
 assert.deepEqual(filter.findAllByType('option').map(node=>node.props.value),['accepted','all','attention','proposed','closed']);
 await act(async()=>filter.props.onChange({target:{value:'closed'}}));assert.equal(calls.at(-1)[1].filter,'closed');await unmount(root);
});

test('bounded list pagination sends exact opaque cursor and rejects a changed snapshot',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{calls.push([action,args]);if(args.after)return {accepted:false,error:'Work changed; refresh the list.'};return accepted(page([item()],{total:21,truncated:true,nextAfter:'opaque-cursor'}))});
 await click(root,'Next work');assert.equal(calls.at(-1)[1].after,'opaque-cursor');
 assert.match(text(root.toJSON()),/Work changed/);assert.match(text(root.toJSON()),/Retain exact original words/);
 assert.match(text(root.toJSON()),/stale|previous observation/i);assert.doesNotMatch(text(root.toJSON()),/No work yet/);await unmount(root);
});

test('remounted stale cursor can restart explicitly without losing query, filter or selected detail',async()=>{
 let stale=false;const calls=[],row=item();
 const dispatch=async(action,args)=>{
  calls.push([action,args]);
  if(action==='workspace.work.read')return accepted(detail(row));
  assert.equal(action,'workspace.work.list');
  if(args.after&&stale)return {accepted:false,error:'Work changed; refresh the list.'};
  return accepted(page([row],args.after?{}:{total:21,truncated:true,nextAfter:'saved-cursor'}));
 };
 let root=await mount(dispatch);
 await act(async()=>root.root.findByProps({'aria-label':'Show work'}).props.onChange({target:{value:'attention'}}));
 await act(async()=>root.root.findByProps({'aria-label':'Find work'}).props.onChange({target:{value:'Retain'}}));
 await click(root,'Next work');await inspect(root);await unmount(root);
 stale=true;calls.length=0;root=await mount(dispatch);
 assert.deepEqual(calls.filter(([name])=>name==='workspace.work.list').map(([,args])=>args.after),['saved-cursor']);
 assert.equal(root.root.findAllByProps({'data-work-id':row.id}).length,0,'No first page was accepted');
 assert.match(text(root.toJSON()),/Work changed/);
 assert.ok(button(root,'Restart Work pages'),'Restart exists even without a page');
 assert.ok(root.root.findByProps({'aria-label':'Work details'}));
 await click(root,'Restart Work pages');
 const lists=calls.filter(([name])=>name==='workspace.work.list');
 assert.equal(lists.length,2,'One explicit restart, not an automatic retry loop');
 assert.deepEqual(lists.at(-1)[1],{workspaceId:workspace.id,filter:'attention',query:'Retain',limit:20});
 assert.equal(root.root.findByProps({'aria-label':'Show work'}).props.value,'attention');
 assert.equal(root.root.findByProps({'aria-label':'Find work'}).props.value,'Retain');
 assert.equal(root.root.findByProps({'data-work-id':row.id}).props['aria-pressed'],true);
 assert.ok(root.root.findByProps({'aria-label':'Work details'}));
 await unmount(root);
});

test('participant return keeps all six facts distinct and missing cleanup stays unknown',async()=>{
 // Negative malformed-provenance projection: role=user must not override peer.
 // Deliberately missing cleanup is distinct from the normal not_recorded DTO.
 const missing=item();delete missing.cleanup;
 const calls=[],opened=[];const root=await mount(async(action,args)=>{calls.push([action,args]);if(action==='workspace.work.list')return accepted(page([missing]));if(action==='workspace.work.read')return accepted(detail(missing,{originalSource:source({inputOrigin:'peer'})}));if(action==='workspace.work.history')return accepted(historyPage());throw Error(action)},{onOpenChat:async id=>{opened.push(id);return {accepted:true}}});
 await inspect(root);const value=text(root.toJSON());
 for(const label of ['Commitment','Execution','Verification','Human decision','Delivery','Cleanup'])assert.ok(value.includes(label),label);
 assert.match(value,/Returned.*unverified/i);assert.match(value,/not recorded|unknown/i);
 const cleanupTerms=root.root.findAllByType('dt').filter(node=>text(node)==='Cleanup');
 assert.equal(cleanupTerms.length,1,'Exactly one Cleanup fact');
 const term=cleanupTerms[0],siblings=term.parent.children.filter(node=>typeof node!=='string');
 const cleanup=siblings[siblings.indexOf(term)+1];
 assert.equal(cleanup?.type,'dd','Cleanup has its own value, not another fact’s text');
 assert.match(text(cleanup),unknownCleanupValue,'Missing cleanup is unknown/not recorded, never None');
 assert.doesNotMatch(value,/Human request|Shipped|All checks passed|Cleanup complete/);
 assert.match(value,/peer/i);assert.deepEqual(opened,[]);
 const open=root.root.findAllByType('button').find(node=>node.props['aria-label']==='Open chat: owner');
 assert.ok(open);await act(async()=>open.props.onClick());assert.deepEqual(opened,['owner']);
 assert.ok(calls.every(([name])=>['workspace.work.list','workspace.work.read','workspace.work.history'].includes(name)));await unmount(root);
});

test('all original source pages use returned offsets and preserve provenance/hash',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{
  calls.push([action,args]);if(action==='workspace.work.list')return accepted(page());
  if(action==='workspace.work.read')return accepted(detail(item(),{originalSource:source(args.sourceOffset?{fullText:'cafeend',text:'end',offset:4,total:7}:{fullText:'cafeend',text:'cafe',offset:0,total:7,truncated:true,nextOffset:4})}));
  if(action==='workspace.work.history'){
   const rows=defaultHistory();rows[0]={...rows[0],source:sourceMetadata(source({text:'cafeend'}))};
   return accepted(historyPage(rows));
  }throw Error(action);
 });
 await inspect(root);await click(root,'Read more source');
 assert.equal(calls.filter(([name])=>name==='workspace.work.read').at(-1)[1].sourceOffset,4);
 assert.match(text(root.toJSON()),/cafeend/);assert.match(text(root.toJSON()),new RegExp(source({text:'cafeend'}).sha256));await unmount(root);
});

test('correction source uses exact allowed history ref and keeps old evidence attributed',async()=>{
 const calls=[],ref={action:'workspace.work.source',args:{workspaceId:workspace.id,id:'work:one',sourceId:'event-correction'}};
 const history=historyPage([defaultHistory()[0],{id:'event-return',kind:'report',sequence:2,promiseRevision:1,actor:{origin:'agent',sessionId:'owner'},recordedAt:80,
  changedFields:[],source:{},sourceRef:null,eventRef:{action:'workspace.work.event',args:{workspaceId:workspace.id,id:'work:one',eventId:'event-return'}},
  evidence:{candidate:'old-candidate',outcome:'passed',report:'Old participant success',qualification:'participant_report',criteria:['cold'],criteriaValidated:true,reportedBy:'owner',source:'agent report'}},
  {id:'event-correction',kind:'revise',sequence:3,promiseRevision:2,actor:{origin:'ui',sessionId:'owner'},recordedAt:90,changedFields:['criteria'],
   eventRef:{action:'workspace.work.event',args:{workspaceId:workspace.id,id:'work:one',eventId:'event-correction'}},source:sourceMetadata(source({text:'Exact correction'})),sourceRef:ref}]);
 const corrected=item('work:one',{execution:'not_started',latestOutcome:'not_reported',candidate:'old-candidate'});
 const correctedRaw={...detail(corrected).item,promiseEventRef:'event-correction',verificationBoundaryRef:'event-correction',
  verificationBoundaryAt:90,counterevidenceSequence:0,links:[]};
 const root=await mount(async(action,args)=>{calls.push([action,args]);return accepted(action==='workspace.work.list'?page([corrected]):action==='workspace.work.read'?detail(corrected,{item:correctedRaw}):action==='workspace.work.history'?history:{source:source({inputOrigin:'ui',text:'Exact correction'})})});
 await inspect(root);await click(root,'Read history');await click(root,'Read correction source');
 assert.deepEqual(calls.at(-1),[ref.action,ref.args]);assert.match(text(root.toJSON()),/Exact correction/);
 assert.match(text(root.toJSON()),/Old participant success/);assert.doesNotMatch(text(root.toJSON()),/Independent checks passed/);await unmount(root);
});

test('obsolete workspace result cannot replace the current workspace or start a detail read',async()=>{
 const old=deferredRead(),calls=[],completions=[observeCompletion(old.promise)];
 const release=()=>old.resolve(accepted(page([item('work:old',{title:'Obsolete result'})])));
 const dispatch=async(action,args)=>{calls.push([action,args]);return args.workspaceId===workspace.id?old.promise:accepted(page([item('work:new',{workspaceId:'other',title:'Current workspace result'})],{workspaceId:'other'}))};
 let root,originalError;
 try {
  root=await bounded(mount(dispatch,{},value=>{root=value}),2000,'Mount pending workspace');
  await bounded(act(async()=>root.update(React.createElement(SharedWorkBrowser,{workspace:{id:'other',path:'/synthetic/other'},dispatch:root.testDispatch}))),2000,'Select new workspace');
  await bounded(act(async()=>{release();await old.promise}),2000,'Release obsolete workspace read');
  assert.match(text(root.toJSON()),/Current workspace result/);assert.doesNotMatch(text(root.toJSON()),/Obsolete result/);
  assert.ok(calls.every(([name])=>name==='workspace.work.list'));
 } catch(error){originalError=error;throw error}
 finally{await finishDeferredTest({root,release,completions,originalError})}
});

test('obsolete item detail cannot replace the later explicit item selection',async()=>{
 const old=deferredRead(),other=item('work:two',{title:'Second promise'});
 const release=()=>old.resolve(accepted(detail(item(),{originalSource:source({text:'Obsolete detail body'})})));
 const completions=[observeCompletion(old.promise)];let root,originalError,oldReadStarted=false;
 const dispatch=async(action,args)=>{
  if(action==='workspace.work.list')return accepted(page([item(),other]));
  if(action==='workspace.work.read'&&args.id==='work:one'){oldReadStarted=true;return old.promise}
  if(action==='workspace.work.read')return accepted(detail(other,{originalSource:source({text:'Second exact source'})}));
  return accepted(historyPage());
 };
 try {
  root=await bounded(mount(dispatch,{},value=>{root=value}),2000,'Mount detail race');
  await bounded(act(async()=>{
   // Do not return/await this handler: it may validly wait for our held read.
   completions.push(observeCompletion(root.root.findByProps({'data-work-id':'work:one'}).props.onClick()));
  }),2000,'Begin first detail selection');
  assert.ok(oldReadStarted,'First detail request must be in flight before selecting another item');
  await bounded(inspect(root,'work:two'),2000,'Select second item');
  await bounded(act(async()=>{release();await Promise.all(completions.map(requireCompletion))}),2000,'Release first detail and flush');
  assert.match(text(root.toJSON()),/Second exact source/);assert.doesNotMatch(text(root.toJSON()),/Obsolete detail body/);
 } catch(error){originalError=error;throw error}
 finally{await finishDeferredTest({root,release,completions,originalError})}
});

test('refresh failure retains the prior observed list with an explicit stale warning',async()=>{
 let failed=false;const root=await mount(async()=>{if(failed)throw Error('Read transport unavailable');return accepted(page())});
 failed=true;await click(root,'Refresh work');assert.match(text(root.toJSON()),/Read transport unavailable/);
 assert.match(text(root.toJSON()),/Retain exact original words/);assert.match(text(root.toJSON()),/stale|previous observation/i);await unmount(root);
});

test('untrusted source refs cannot dispatch a mutation',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{
  calls.push([action,args]);if(action==='workspace.work.list')return accepted(page());if(action==='workspace.work.read')return accepted(detail());
  if(action==='workspace.work.history')return accepted(historyPage([{id:'bad',kind:'revise',sequence:1,promiseRevision:2,actor:{origin:'peer',sessionId:'peer'},recordedAt:90,changedFields:['criteria'],source:{inputOrigin:'peer'},sourceRef:{action:'conversation.send',args:{sessionId:'owner',text:'Injected'}}}]));
  throw Error('Forbidden action '+action);
 });
 await inspect(root);await click(root,'Read history');const control=button(root,'Read correction source');
 if(control&&!control.props.disabled)await act(async()=>control.props.onClick());
 assert.ok(calls.every(([name])=>['workspace.work.list','workspace.work.read','workspace.work.history'].includes(name)));await unmount(root);
});

test('passive wait binds the observation fingerprint and is aborted when hidden',async()=>{
 const held=deferredRead(),calls=[];let root,originalError;
 const dispatch=async(action,args,meta)=>{
  calls.push([action,args,meta]);
  if(action==='workspace.work.list')return accepted(page());
  if(action==='workspace.work.coverage')return accepted(coveragePage());
  if(action==='workspace.work.wait')return held.promise;
  throw Error('Unexpected action '+action);
 };
 try {
  root=await bounded(mount(dispatch,{},()=>{},false),2000,'Mount observed page');
  const waits=calls.filter(([name])=>name==='workspace.work.wait');
  assert.equal(waits.length,1,'One bounded observation wait, not a polling burst');
  const [,args,meta]=waits[0];
  assert.equal(args.afterVersion,page().observationVersion);
  assert.notEqual(args.afterVersion,page().snapshotVersion);
  assert.equal(args.workspaceId,workspace.id);assert.equal(args.filter,'accepted');
  assert.equal(args.query,'');assert.equal(args.limit,20);assert.equal(args.after,undefined);
  assert.ok((args.waitMs??30000)>=0&&(args.waitMs??30000)<=30000);assert.ok(meta?.signal);
  await bounded(unmount(root),2000,'Hide Work');root=null;
  assert.equal(meta.signal.aborted,true);
 } catch(error){originalError=error;throw error}
 finally {
  await finishDeferredTest({root,release:()=>held.resolve({...accepted(page()),changed:false}),
   completions:[observeCompletion(held.promise)],originalError});
 }
});

test('history continuation retains its immutable high-water while current events advance',async()=>{
 const calls=[],rows=defaultHistory();
 const root=await mount(async(action,args)=>{
  calls.push([action,args]);
  if(action==='workspace.work.list')return accepted(page());
  if(action==='workspace.work.read')return accepted(detail());
  if(action==='workspace.work.history')return accepted(args.after
   ?historyPage([rows[2]],{total:3,snapshotEventCount:3,currentEventCount:4,snapshotIsCurrent:false})
   :historyPage(rows.slice(0,2),{total:3,snapshotEventCount:3,currentEventCount:3,truncated:true,nextAfter:'immutable-history-cursor'}));
  throw Error(action);
 });
 await inspect(root);await click(root,'Read history');await click(root,'Next history');
 assert.equal(calls.at(-1)[1].after,'immutable-history-cursor');
 assert.match(text(root.toJSON()),/Current unknown result/);
 assert.match(text(root.toJSON()),/snapshot|newer history/i);
 assert.doesNotMatch(text(root.toJSON()),/No history/);
});

test('immutable event details use the returned event ref without loading source or chat',async()=>{
 const calls=[],event=eventRow('event-exact',3,{changedFields:['nextAction']});
 const row=item('work:one',{execution:'not_started',latestOutcome:'not_reported',nextAction:'Exact retained action before later corrections'});
 const raw={...detail(row).item,verificationBoundaryRef:'work-event:correction',verificationBoundaryAt:92,
  counterevidenceSequence:0,latestEventRef:'event-exact',links:[]};
 const root=await mount(async(action,args)=>{
  calls.push([action,args]);
  if(action==='workspace.work.list')return accepted(page([row]));
  if(action==='workspace.work.read')return accepted(detail(row,{item:raw}));
  if(action==='workspace.work.history')return accepted(historyPage([defaultHistory()[0],defaultHistory()[1],event]));
  if(action==='workspace.work.event')return accepted({event:{
   id:event.id,kind:event.kind,sequence:3,promiseRevision:2,actor:event.actor,recordedAt:event.recordedAt,
   changes:{nextAction:'Exact retained action before later corrections'},
  }});
  throw Error(action);
 });
 await inspect(root);await click(root,'Read history');await click(root,'Read event: event-exact');
 assert.deepEqual(calls.at(-1),[event.eventRef.action,event.eventRef.args]);
 assert.match(text(root.toJSON()),/Exact retained action before later corrections/);
 assert.ok(calls.every(([name])=>!['coordination.read','session.select','session.warm'].includes(name)));
});

test('reported publication cannot become shipped, verified delivery or a passed check',async()=>{
 const report={promiseRevision:2,candidate:'candidate-one',outcome:'published',reportedBy:'owner',recordedAt:105,
  attribution:'participant_report',hostObserved:false,independentlyVerified:false};
 const row=item('work:one',{revision:4,delivery:'reported_published',deliveryReport:report});
 const raw=detail(row).item;
 raw.deliveryReport={...report,destination:'Synthetic preview',references:['https://example.invalid/release/one'],report:'Publication is a participant report only.'};
 const root=await mount(async action=>{
  if(action==='workspace.work.list')return accepted(page([row]));
  if(action==='workspace.work.read')return accepted(detail(row,{item:raw}));
  throw Error(action);
 });
 await inspect(root);
 assert.match(text(root.toJSON()),/Reported published/);
 assert.match(text(root.toJSON()),/Publication is a participant report only/);
 assert.match(text(root.toJSON()),/Returned.*unverified/i);
 assert.doesNotMatch(text(root.toJSON()),/Shipped|Verified delivery|Independent checks passed/);
});

test('reported cleanup states never certify teardown or an empty resource inventory',async()=>{
 for(const reportedState of ['active','unknown','reaped','observed_absent']) {
  const resource={resourceId:'synthetic-resource',kind:'test-container',reportedState,evidenceReference:'synthetic-receipt',
   ownerSessionId:'owner',recordedAt:101,attribution:'owner_report',independentlyVerified:false};
  const row=item('work:one',{revision:4,cleanup:{status:'reported',references:[resource],coverage:'Explicit owner reports only; no resource census or teardown implied.'}});
  const root=await mount(async action=>accepted(action==='workspace.work.list'?page([row]):detail(row)));
  await inspect(root);
  assert.match(text(root.toJSON()),/owner.report|reported by/i);
  assert.match(text(root.toJSON()),/synthetic-resource/);
  assert.doesNotMatch(text(root.toJSON()),/Cleanup complete|Verified teardown|No resources/);
  await unmount(root);
 }
});

test('fresh human acceptance without counterevidence remains current, not independently verified',async()=>{
 const row=item('work:one',{revision:4,humanDecision:'accepted',humanDecisionCurrent:true,latestOutcome:'passed',
  attentionReasons:[]});
 const raw={...detail(row).item,humanDecisionSequence:4};
 delete raw.counterevidenceSequence; // Canonical producer omits this until adverse evidence exists.
 const root=await mount(async action=>accepted(action==='workspace.work.list'?page([row]):detail(row,{item:raw})));
 await inspect(root);
 const facts=root.root.findByProps({className:'a-shared-facts'}),labels=facts.findAllByType('dt'),values=facts.findAllByType('dd');
 assert.equal(text(values[labels.findIndex(node=>text(node)==='Human decision')]),'accepted');
 assert.doesNotMatch(text(root.toJSON()),/no longer current|later counterevidence retained/i);
 assert.match(text(values[labels.findIndex(node=>text(node)==='Verification')]),/not verified/i);
});

test('human acceptance with later counterevidence stays historical and unverified',async()=>{
 const row=item('work:one',{revision:5,humanDecision:'accepted',humanDecisionCurrent:false,latestOutcome:'failed',
  attentionReasons:['failed','counterevidence_after_decision']});
 const raw={...detail(row).item,humanDecisionSequence:4,counterevidenceSequence:5};
 const root=await mount(async action=>accepted(action==='workspace.work.list'?page([row]):detail(row,{item:raw})));
 await inspect(root);const value=text(root.toJSON());
 assert.match(value,/counterevidence|later.*fail|no longer current/i);
 assert.match(value,/failed/i);assert.match(value,/not verified|unverified/i);
 assert.doesNotMatch(value,/All checks passed|Shipped/);
});

test('zero unresolved observed inputs never means historical inventory is complete',async()=>{
 const root=await mount(async action=>{assert.equal(action,'workspace.work.list');return accepted(page([]))});
 const load=button(root,'Read capture coverage');if(load)await click(root,'Read capture coverage');
 assert.ok(root.backgroundCalls.some(([name])=>name==='workspace.work.coverage'),'Explicit or visible coverage must use its own DTO');
 assert.match(text(root.toJSON()),/incomplete|not inventoried/i);
 assert.doesNotMatch(text(root.toJSON()),/All historical work captured|0 missing tasks|History complete/);
});
