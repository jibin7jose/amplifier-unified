import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkspaceReview}=await vite.ssrLoadModule('/src/workspace-review.jsx');
const {RelatedWork}=await vite.ssrLoadModule('/src/coordination.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const storage=new Map();globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
test.after(()=>vite.close());test.beforeEach(()=>storage.clear());
const text=node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button=(root,label)=>root.root.findAllByType('button').find(node=>text(node)===label);
const click=(root,label)=>act(async()=>{const node=button(root,label);assert.ok(node,label);assert.equal(!!node.props.disabled,false);await node.props.onClick()});
const accepted=result=>({accepted:true,result});
const chat={sessionId:'own',title:'Saved work',status:'idle',activityObserved:true,approvals:0,questions:0,handoffs:{items:[]},executionDirectory:'/workspace',executionRevision:0};
const page=(patch={})=>({sessionId:'own',workspace:'/workspace',capturedAt:2,items:[chat],claims:{status:'unassigned',items:[],total:0},sourceObservations:[],installedApp:{serverVersion:'test',frontend:{id:'build',version:'test'}},nextAfter:null,...patch});

test('review stays demand-loaded and opening it makes one scoped passive read',async()=>{
 const calls=[];let root;
 const dispatch=async(action,args)=>{calls.push([action,args]);if(action==='coordination.context')return accepted({workspace:'/workspace',requests:[]});if(action==='coordination.list')return accepted({items:[]});if(action==='coordination.review')return accepted(page());throw Error(action)};
 storage.set('private-draft','Do not change');
 await act(async()=>{root=create(React.createElement(RelatedWork,{dispatch,sessionId:'own'}))});
 assert.equal(calls.some(([name])=>name==='coordination.review'),false);
 const disclosure=root.root.findAllByType('details').find(node=>node.findAllByType('summary').some(node=>text(node)==='Review before continuing'));
 await act(async()=>{const event={open:true};disclosure.props.onToggle({target:event,currentTarget:event})});
 assert.deepEqual(calls.at(-1),['coordination.review',{sessionId:'own',limit:20}]);
 assert.equal(calls.length,3);assert.equal(storage.get('private-draft'),'Do not change');
 await act(async()=>root.unmount());
});

test('review makes decisions and limits concrete without declaring ready or replaying work',async()=>{
 let root;const calls=[],sections=[],opened=[];
 const value=page({items:[{...chat,status:'interrupted',configurationPending:true,approvals:1,expiredApprovals:1,responseAt:1,handoffs:{items:[{id:'move',phase:'unknown',updatedAt:1}]}},{...chat,sessionId:'peer',title:'Other work',status:'working',activityObserved:false,executionUnverified:true}],claims:{status:'active',items:[{taskId:'task',title:'Check files',detail:'The holder was explicitly stopped. Release or reassign explicitly.'}],total:1},sourceObservations:[{id:'observation',kind:'check',status:'mismatch',capturedAt:1}]});
 const dispatch=async(action,args)=>{calls.push([action,args]);return accepted(value)};
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'own',workspace:'/workspace',onShowSection:name=>sections.push(name),onOpenChat:async id=>{opened.push(id);return {accepted:true}}}))});
 for(const fragment of ['Review pending decisions','Review permissions that expired after the app restarted','Reconcile the unfinished move','Review pending settings or file permissions','Inspect prior effects before repeating','Previously active; current execution unverified','Saved source check: mismatch','Claims do not lock files'])assert.ok(text(root.toJSON()).includes(fragment),fragment);
 await click(root,'Review shared tasks');await click(root,'Review source checks');
 assert.deepEqual(sections,['assignment','integration']);assert.deepEqual(opened,[]);
 await act(async()=>root.root.findByProps({'aria-label':'Open chat to review: Other work'}).props.onClick());
 assert.deepEqual(opened,['peer']);assert.deepEqual(calls,[['coordination.review',{sessionId:'own',limit:20}]]);
 await act(async()=>root.unmount());
});

test('next and previous pages replace bounded rows and refresh returns to first page',async()=>{
 const calls=[];let root;const dispatch=async(action,args)=>{calls.push(args);return accepted(args.after?page({items:[{...chat,title:'Second page'}]}):page({nextAfter:'cursor'}))};
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'own'}))});
 await click(root,'Next review page');assert.doesNotMatch(text(root.toJSON()),/Saved work/);assert.match(text(root.toJSON()),/Second page/);
 await click(root,'Previous review page');await click(root,'Next review page');await click(root,'Refresh review');
 assert.deepEqual(calls.map(row=>row.after??null),[null,'cursor',null,'cursor',null]);
 assert.equal(button(root,'Previous review page'),undefined);await act(async()=>root.unmount());
});

test('late old-scope reads and probes cannot replace the new chat review or leave controls stuck',async()=>{
 let finishOld,finishProbe,root;const dispatch=async(action,args)=>{if(action==='runtime.provenance')return new Promise(resolve=>{finishProbe=resolve});if(args.sessionId==='old')return new Promise(resolve=>{finishOld=resolve});return accepted(page({sessionId:args.sessionId,items:[{...chat,title:args.sessionId}]}))};
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'old'}))});
 await act(async()=>root.update(React.createElement(WorkspaceReview,{dispatch,sessionId:'own'})));
 await act(async()=>finishOld(accepted(page({sessionId:'old',items:[{...chat,title:'Old response'}]}))));
 assert.doesNotMatch(text(root.toJSON()),/Old response/);
 await act(async()=>{void button(root,'Inspect this chat’s loaded sources').props.onClick()});
 await act(async()=>root.update(React.createElement(WorkspaceReview,{dispatch,sessionId:'new'})));
 await act(async()=>finishProbe(accepted({status:'Old worker'})));
 assert.doesNotMatch(text(root.toJSON()),/Old worker/);assert.equal(button(root,'Inspect this chat’s loaded sources').props.disabled,false);
 await act(async()=>root.unmount());
});

test('wrong-scope and failed observations keep private drafts and offer explicit refresh',async()=>{
 storage.set('composer','Unsent');let bad=true,root;
 const dispatch=async()=>accepted(page({workspace:bad?'/foreign':'/workspace'}));
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'own',workspace:'/workspace'}))});
 assert.match(text(root.toJSON()),/review scope changed/);assert.doesNotMatch(text(root.toJSON()),/Saved work/);
 bad=false;await click(root,'Refresh review');assert.match(text(root.toJSON()),/Saved work/);assert.equal(storage.get('composer'),'Unsent');await act(async()=>root.unmount());
});

test('worker source observation is explicit, read-only, and does not certify installed adoption',async()=>{
 const calls=[];let root;const dispatch=async(action,args)=>{calls.push([action,args]);return accepted(action==='coordination.review'?page():{status:'unknown',reason:'Worker is not running'})};
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'own'}))});
 assert.equal(calls.length,1);await click(root,'Inspect this chat’s loaded sources');
 assert.deepEqual(calls.at(-1),['runtime.provenance',{sessionId:'own'}]);assert.match(text(root.toJSON()),/Worker is not running/);assert.match(text(root.toJSON()),/not an automatic release-adoption check/);
 await act(async()=>root.unmount());
});

test('refresh during explicit navigation never leaves chat controls stuck after rejection',async()=>{
 let root,finish;const dispatch=async()=>accepted(page());
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'own',onOpenChat:()=>new Promise(resolve=>{finish=resolve})}))});
 await act(async()=>{void button(root,'Open chat to review').props.onClick()});
 assert.equal(button(root,'Opening…').props.disabled,true);
 await click(root,'Refresh review');
 await act(async()=>finish({accepted:false,error:'Selection refused'}));
 assert.match(text(root.toJSON()),/Selection refused/);assert.equal(button(root,'Open chat to review').props.disabled,false);
 await act(async()=>root.unmount());
});

test('dormant native approvals show uncertainty rather than actionable pending permission',async()=>{
 let root;const dispatch=async()=>accepted(page({items:[{...chat,activityObserved:false,approvals:0,unverifiedApprovals:1}]}));
 await act(async()=>{root=create(React.createElement(WorkspaceReview,{dispatch,sessionId:'own'}))});
 assert.match(text(root.toJSON()),/no observed live waiter/);assert.match(text(root.toJSON()),/Open the chat to reconcile saved permission requests/);assert.doesNotMatch(text(root.toJSON()),/pending approvals/);
 await act(async()=>root.unmount());
});
