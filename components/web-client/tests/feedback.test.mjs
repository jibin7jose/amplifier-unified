import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {FeedbackPanel,FeedbackDiagnostics,LocalFeedbackReport}=await server.ssrLoadModule('/src/feedback.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const draft={title:'Canvas bug',body:'A preview stopped responding.',category:'bug',includeDiagnostics:false};
const base=()=>({view:{feedbackDraft:{...draft}},feedback:{requests:[],diagnostics:{appVersion:'0.6.4',osFamily:'Darwin'}}});

test('feedback defaults to reviewed text without diagnostics or private-state attachments',()=>{
 const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state:base(),act:()=>{}}));
 assert.match(html,/Canvas bug/);assert.match(html,/A preview stopped responding/);
 assert.match(html,/type="checkbox"/);assert.doesNotMatch(html,/checked=""/);
 assert.match(html,/credentials are not attached/);assert.doesNotMatch(html,/Included: app/);
 assert.match(html,/microsoft\/amplifier-unified/);
});

test('a new feedback form requires explicit diagnostic opt-in',()=>{
 const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state:{view:{},feedback:{}},act:()=>{}}));
 assert.doesNotMatch(html,/checked=""/);
 assert.match(html,/Prepare local report/);
});

test('local report prepares only exact supplied text and does not submit or overwrite a draft',async()=>{
 const calls=[];let root;
 try{
  await renderAct(async()=>root=create(React.createElement(LocalFeedbackReport,{draft,state:{},act:async(name,args)=>{
   calls.push({name,args});return {accepted:true,result:{submitted:false,content:'# Exact report',filename:'amplifier-feedback.md'}};
  }})));
  await renderAct(async()=>root.root.findByProps({'data-action':'feedback.report'}).props.onClick());
  assert.deepEqual(calls,[{name:'feedback.report',args:{title:draft.title,body:draft.body,category:'bug',includeDiagnostics:false,destination:'none'}}]);
  assert.equal(root.root.findByProps({'aria-label':'Local feedback report preview'}).props.value,'# Exact report');
  const link=root.root.findByProps({href:'https://github.com/microsoft/amplifier-unified/issues/new'});
  assert.equal(link.props.href.includes('?'),false,'Report text must not leak into a browser URL');
  assert.match(JSON.stringify(root.toJSON()),/Nothing is posted|not.*submitted/);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('late local report cannot overwrite newly edited feedback',async()=>{
 let release,root,pending,current=draft;const calls=[];
 const render=()=>React.createElement(LocalFeedbackReport,{draft:current,state:{},act:async(name,args)=>{
  calls.push({name,args});return new Promise(resolve=>{release=resolve});
 }});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{pending=root.root.findByProps({'data-action':'feedback.report'}).props.onClick()});
  current={...draft,body:'New text'};
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>{release({accepted:true,result:{submitted:false,content:'# Stale report'}});await pending});
  assert.equal(root.root.findAllByProps({'aria-label':'Local feedback report preview'}).length,0);
  assert.equal(calls.length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('local diagnostics from a previous chat visit cannot revive after switching away and back',async()=>{
 let release,root,pending,selectedSessionId='A';
 const render=()=>React.createElement(LocalFeedbackReport,{draft:{...draft,includeDiagnostics:true},state:{selectedSessionId},act:()=>new Promise(resolve=>release=resolve)});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{pending=root.root.findByProps({'data-action':'feedback.report'}).props.onClick()});
  selectedSessionId='B';await renderAct(async()=>root.update(render()));
  selectedSessionId='A';await renderAct(async()=>root.update(render()));
  await renderAct(async()=>{release({accepted:true,result:{submitted:false,content:'Previous visit'}});await pending});
  assert.equal(root.root.findAllByProps({'aria-label':'Local feedback report preview'}).length,0);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('a lost acknowledgement keeps the exact request and frozen text for explicit retry',async()=>{
 let state=base(),root;const calls=[];
 async function action(name,args){
  calls.push({name,args:structuredClone(args)});
  if(name==='view.update'){
   state={...state,view:{...state.view,...args.patch}};
   root.update(React.createElement(FeedbackPanel,{state,act:action}));
  }else throw new Error('Network response lost');
  return {accepted:true,state};
 }
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const first=calls.find(call=>call.name==='feedback.submit').args;
 assert.ok(first.requestId);assert.equal(first.body,draft.body);
 assert.equal(root.root.findByProps({id:'feedback-body'}).props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/Check submission/);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const submissions=calls.filter(call=>call.name==='feedback.submit');
 assert.equal(submissions.length,2);assert.deepEqual(submissions[1].args,first);
 await renderAct(async()=>root.unmount());
});

test('request payload is frozen before waiting for draft persistence and double clicks do not send twice',async()=>{
 let release,root;const calls=[];const wait=new Promise(resolve=>release=resolve);
 async function action(name,args){calls.push({name,args});if(name==='view.update')await wait;return {accepted:true};}
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state:base(),act:action}))});
 let submit;
 await renderAct(async()=>{submit=root.root.findByType('form').props.onSubmit({preventDefault(){}})});
 assert.equal(root.root.findByProps({id:'feedback-body'}).props.disabled,true);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 await renderAct(async()=>{release();await submit});
 const sends=calls.filter(call=>call.name==='feedback.submit');assert.equal(sends.length,1);assert.equal(sends[0].args.body,draft.body);
 await renderAct(async()=>root.unmount());
});

test('durable success and uncertainty render meaningful results without allowing a repost',()=>{
 for(const status of ['submitted','unknown']){
  const requestId='fixture-123';
  const state=base();state.view.feedbackDraft.pending={...draft,requestId};
  state.feedback.requests=[{requestId,status,message:status==='submitted'?'Feedback sent. Thank you.':'GitHub may have received this feedback.',...(status==='submitted'?{url:'https://github.com/microsoft/amplifier-unified/issues/42'}:{})}];
  const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state,act:()=>{}}));
  assert.match(html,/New feedback/);assert.doesNotMatch(html,/>Check submission</);
  if(status==='submitted'){assert.match(html,/a-check-result success/);assert.match(html,/View issue/)}
  else {assert.match(html,/a-check-result error/);assert.match(html,/Check repository issues/)}
 }
});

test('only confirmed feedback clears the saved form and receipts never clear a newer draft',async()=>{
 for(const status of ['submitted','failed','unknown','sending']){
  let state=base(),root;const calls=[],requestId='form-reset-'+status;
  state.view.feedbackDraft.pending={...draft,requestId};
  state.feedback.requests=[{requestId,status,title:draft.title,message:'Receipt '+status,url:'https://github.com/microsoft/amplifier-unified/issues/42'}];
  const action=async(name,args)=>{
   calls.push({name,args});state={...state,view:{...state.view,...args.patch}};
   root.update(React.createElement(FeedbackPanel,{state,act:action}));return {accepted:true};
  };
  await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
  if(status==='submitted'){
   assert.equal(root.root.findByProps({id:'feedback-title'}).props.value,'');
   assert.equal(root.root.findByProps({id:'feedback-body'}).props.value,'');
   assert.deepEqual(state.view.feedbackDraft.attachments,[]);
   assert.equal(state.view.feedbackDraft.pending,undefined);
   assert.match(JSON.stringify(root.toJSON()),/Receipt submitted/);
   await renderAct(async()=>root.root.findByProps({id:'feedback-body'}).props.onChange({target:{value:'Next feedback'}}));
   await renderAct(async()=>root.update(React.createElement(FeedbackPanel,{state:{...state,feedback:{...state.feedback,requests:[...state.feedback.requests]}},act:action})));
   assert.equal(root.root.findByProps({id:'feedback-body'}).props.value,'Next feedback');
  }else{
   assert.equal(root.root.findByProps({id:'feedback-body'}).props.value,draft.body);
   assert.equal(root.root.findByProps({id:'feedback-body'}).props.disabled,true);
   assert.equal(calls.length,0);
  }
  assert.ok(calls.every(call=>call.name==='view.update'),'Receipt handling never submits feedback');
  await renderAct(async()=>root.unmount());
 }
});

test('every editable form control publishes shared view state, including optional diagnostics',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state:base(),act:async(name,args)=>calls.push({name,args})}))});
 await renderAct(async()=>root.root.findByProps({id:'feedback-title'}).props.onChange({target:{value:'Edited title'}}));
 await renderAct(async()=>root.root.findByProps({id:'feedback-category'}).props.onChange({target:{value:'idea'}}));
 const checkbox=root.root.findAll(node=>node.type==='input'&&node.props.type==='checkbox')[0];
 await renderAct(async()=>checkbox.props.onChange({target:{checked:true}}));
 await renderAct(async()=>new Promise(resolve=>setTimeout(resolve,300)));
 const saved=calls.at(-1);assert.equal(saved.name,'view.update');assert.equal(saved.args.patch.feedbackDraft.title,'Edited title');assert.equal(saved.args.patch.feedbackDraft.category,'idea');assert.equal(saved.args.patch.feedbackDraft.includeDiagnostics,true);
 assert.match(JSON.stringify(root.toJSON()),/Preview reproduction diagnostics/);assert.equal(calls.some(call=>call.name==='feedback.diagnostics'),false);
 await renderAct(async()=>root.unmount());
});

test('selected attachment IDs are frozen through lost acknowledgement and preview is agent-visible',async()=>{
 const file={id:'a'.repeat(32),name:'report.png',mime:'image/png',size:68,url:'/ignored'};
 let state=base(),root;state.view.feedbackDraft.attachments=[file];
 const calls=[];
 async function action(name,args){
  calls.push({name,args:structuredClone(args)});
  if(name==='view.update'){state={...state,view:{...state.view,...args.patch}};root.update(React.createElement(FeedbackPanel,{state,act:action}))}
  else throw new Error('Response lost');
  return {accepted:true,state};
 }
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Preview report.png'}).props.onClick());
 await renderAct(async()=>new Promise(resolve=>setTimeout(resolve,300)));
 assert.equal(calls.at(-1).args.patch.feedbackDraft.previewId,file.id);
 assert.equal(root.root.findByProps({alt:'Preview of report.png'}).props.src,'/api/attachments/'+file.id);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const first=calls.find(call=>call.name==='feedback.submit').args;
 assert.deepEqual(first.attachmentIds,[file.id]);
 assert.equal(root.root.findAllByProps({'aria-label':'Remove report.png'}).length,0);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const sends=calls.filter(call=>call.name==='feedback.submit');assert.equal(sends.length,2);assert.deepEqual(sends[1].args,first);
 await renderAct(async()=>root.unmount());
});

test('an untrusted shared preview URL is never used as an image source',()=>{
 const state=base();state.view.feedbackDraft.attachments=[{id:'a'.repeat(32),name:'screenshot.png',size:1,mime:'image/png',url:'https://outside.example/collect'}];
 const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state,act:()=>{}}));
 assert.match(html,/\/api\/attachments\/aaaaaaaa/);assert.doesNotMatch(html,/outside\.example/);
 assert.match(html,/Review file destination/);assert.match(html,/visibility you explicitly review/);assert.match(html,/24 MB total/);
});

 test('an explicit saved diagnostic opt-in remains checked',()=>{
  const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state:{view:{feedbackDraft:{includeDiagnostics:true}},feedback:{requests:[]}},act:()=>{}}));
  assert.match(html,/checked=""/);assert.match(html,/Include reproduction diagnostics/);
 });
 test('slow acknowledgements do not overwrite newer typing or queue every keystroke',async()=>{
  let root,release;const calls=[];
  const state=base();
  async function action(name,args){calls.push(args);await new Promise(resolve=>release=resolve);return {accepted:true}}
  await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
  const change=value=>root.root.findByProps({id:'feedback-title'}).props.onChange({target:{value}});
  await renderAct(async()=>{change('First edit');await new Promise(resolve=>setTimeout(resolve,300))});
  await renderAct(async()=>{change('Second edit');change('Third edit');change('Latest edit')});
  await renderAct(async()=>{root.update(React.createElement(FeedbackPanel,{state:{...state,view:{feedbackDraft:{...draft,title:'First edit'}}},act:action}))});
  assert.equal(root.root.findByProps({id:'feedback-title'}).props.value,'Latest edit');assert.equal(calls.length,1);
  await renderAct(async()=>{release();await new Promise(resolve=>setTimeout(resolve,10))});
  assert.equal(calls.length,2);assert.equal(calls[1].patch.feedbackDraft.title,'Latest edit');
  await renderAct(async()=>release());await renderAct(async()=>root.unmount());
 });


test('complete diagnostics load only on disclosure or explicit refresh and show saved snapshots on demand',async()=>{
 let root;const calls=[];
 const facts={schemaVersion:2,appVersion:'0.20.10',conversation:{messages:97},troubleshooting:{recordedFailure:{category:'worker_startup',inputRelation:'earlier'}}};
 const action=async(name,args)=>{calls.push({name,args});return {accepted:true,result:{snapshot:args.requestId?'accepted':'current',diagnostics:facts}}};
 await renderAct(async()=>{root=create(React.createElement(FeedbackDiagnostics,{state:base(),act:action}))});
 assert.equal(calls.length,0);
 await renderAct(async()=>root.update(React.createElement(FeedbackDiagnostics,{state:{...base(),revision:99},act:action})));
 assert.equal(calls.length,0,'state publications do not collect diagnostics');
 const toggle=open=>root.root.findByType('details').props.onToggle({currentTarget:{open}});
 await renderAct(async()=>toggle(true));
 assert.equal(calls.length,1);assert.equal(calls[0].name,'feedback.diagnostics');assert.ok(calls[0].args.deviceDiagnostics);
 assert.deepEqual(JSON.parse(root.root.findByType('pre').props.children),facts);
 await renderAct(async()=>{toggle(false);toggle(true)});
 assert.equal(calls.length,1,'reopening a loaded preview does not poll');
 await renderAct(async()=>root.root.findByType('button').props.onClick());assert.equal(calls.length,2);
 await renderAct(async()=>root.unmount());
 await renderAct(async()=>{root=create(React.createElement(FeedbackDiagnostics,{state:base(),act:action,requestId:'saved-report'}))});
 assert.equal(calls.length,2);
 await renderAct(async()=>toggle(true));assert.deepEqual(calls[2].args,{requestId:'saved-report'});
 assert.match(JSON.stringify(root.toJSON()),/snapshot saved when this submission was accepted/);
 await renderAct(async()=>root.unmount());
});

test('diagnostic opt-out and read failures stay honest without automatic retries',async()=>{
 let root,count=0;
 await renderAct(async()=>{root=create(React.createElement(FeedbackDiagnostics,{state:base(),requestId:'opted-out',act:async()=>({result:{snapshot:'accepted',diagnostics:null}})}))});
 await renderAct(async()=>root.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
 assert.match(JSON.stringify(root.toJSON()),/No diagnostics were saved/);
 await renderAct(async()=>root.unmount());
 await renderAct(async()=>{root=create(React.createElement(FeedbackDiagnostics,{state:base(),act:async()=>{count++;throw Error('PRIVATE network error')}}))});
 await renderAct(async()=>root.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
 assert.equal(count,1);assert.match(JSON.stringify(root.toJSON()),/could not be loaded/);assert.doesNotMatch(JSON.stringify(root.toJSON()),/PRIVATE/);
 await renderAct(async()=>root.update(React.createElement(FeedbackDiagnostics,{state:base(),act:async()=>{count++}})));
 assert.equal(count,1);
 await renderAct(async()=>root.unmount());
});


test('excerpt staging locks send, preserves newer feedback text, and ignores late results after a chat switch',async()=>{
 for(const switchChat of [false,true]){
  let state={...base(),selectedSessionId:'source',sessions:[{id:'source',messages:[]},{id:'other',messages:[]}]},root,release;
  const calls=[],file={id:'a'.repeat(32),name:'excerpt.md',size:7,mime:'text/markdown',excerpt:{sha256:'b'.repeat(64),visibility:'public'}};
  async function action(name,args){
   calls.push({name,args:structuredClone(args)});
   if(name==='feedback.excerpt.stage')return new Promise(resolve=>{release=()=>resolve({accepted:true,result:file,state:{view:{feedbackDraft:{...draft,attachments:[file]}}}})});
   if(name==='view.update'){state={...state,view:{...state.view,...args.patch}};root.update(React.createElement(FeedbackPanel,{state,act:action}))}
   return {accepted:true,state};
  }
  await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
  let staging;
  await renderAct(async()=>{staging=root.root.find(node=>node.type?.name==='FeedbackExcerpt').props.onStage({id:'source',reviewId:'review'})});
  assert.equal(root.root.findByProps({id:'feedback-title'}).props.disabled,true);
  await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(calls.some(call=>call.name==='feedback.submit'),false);
  // A newer shared agent edit can arrive even though local typing is locked.
  state={...state,selectedSessionId:switchChat?'other':'source',view:{feedbackDraft:{...draft,title:'Newer title',body:'Newer details',attachments:[]}}};
  await renderAct(async()=>root.update(React.createElement(FeedbackPanel,{state,act:action})));
  await renderAct(async()=>{release();await staging});
  assert.equal(root.root.findByProps({id:'feedback-title'}).props.value,'Newer title');
  assert.equal(root.root.findByProps({id:'feedback-body'}).props.value,'Newer details');
  assert.equal(root.root.findAllByProps({'aria-label':'Preview excerpt.md'}).length,switchChat?0:1);
  assert.equal(root.root.findByProps({id:'feedback-title'}).props.disabled,false);
  if(!switchChat){
   const approval=root.root.findAll(node=>node.type==='input'&&node.props.type==='checkbox')[0];
   assert.equal(approval.props.checked,false);
   await renderAct(async()=>approval.props.onChange({target:{checked:true}}));
   await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
   const sent=calls.find(call=>call.name==='feedback.submit').args;
   assert.deepEqual(sent.confirmedExcerpts,[{id:file.id,sha256:file.excerpt.sha256}]);
  }
  await renderAct(async()=>root.unmount());
 }
});
