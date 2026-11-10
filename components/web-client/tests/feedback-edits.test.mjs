import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {FeedbackCorrection}=await server.ssrLoadModule('/src/feedback-edits.jsx');
test.after(()=>server.close());globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const report={feedbackId:'feedback-original',requestId:'baseline-read',editableTitle:'Original',editableBody:'Body',url:'https://github.com/microsoft/amplifier-unified/issues/42'};
function state(){return {view:{feedbackCorrectionDraft:{feedbackId:report.feedbackId,title:'Proposed',body:'Proposed body',readRequestId:report.requestId,reviewRequestId:'review-intent'}},feedback:{editReview:{requestId:'review-intent',feedbackId:report.feedbackId,status:'completed',review:{original:{title:'Original',body:'Body'},latest:{title:'Maintainer title',body:'Maintainer body',revision:'a'.repeat(64)},previous:{title:'Original',body:'Body'},proposed:{title:'Proposed',body:'Proposed body'},changedSinceRead:true}}}}}
function button(root,action){return root.root.findByProps({'data-action':action})}
function checkbox(root){return root.root.findByProps({type:'checkbox'})}

test('review requires explicit confirmation and resets consent when text changes',async()=>{
 let root;const calls=[];await renderAct(async()=>{root=create(React.createElement(FeedbackCorrection,{state:state(),report,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}}))});
 assert.equal(button(root,'feedback.update').props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/report changed since you opened it/);
 await renderAct(async()=>checkbox(root).props.onChange({target:{checked:true}}));
 assert.equal(button(root,'feedback.update').props.disabled,false);
 await renderAct(async()=>root.root.findByProps({id:'feedback-corrected-body'}).props.onChange({target:{value:'Different body'}}));
 assert.equal(button(root,'feedback.update').props.disabled,true);
 assert.equal(calls.filter(row=>row.name==='feedback.update').length,0);
 await renderAct(async()=>root.unmount());
});

test('copied or missing private review cannot enable publishing',async()=>{
 let root;const value=state();delete value.feedback.editReview;delete value.view.feedbackCorrectionDraft.reviewRequestId;
 await renderAct(async()=>{root=create(React.createElement(FeedbackCorrection,{state:value,report,act:async()=>({accepted:true})}))});
 assert.equal(button(root,'feedback.update').props.disabled,true);
 assert.equal(root.root.findAllByProps({type:'checkbox'}).length,0);
 await renderAct(async()=>root.unmount());
});

test('review intent must be acknowledged before read, publish intent before mutation',async()=>{
 for(const action of ['feedback.edits.review','feedback.update']){
  let root;const calls=[];
  await renderAct(async()=>{root=create(React.createElement(FeedbackCorrection,{state:state(),report,act:async(name,args)=>{calls.push({name,args});return {accepted:false}}}))});
  if(action==='feedback.update'){
   await renderAct(async()=>checkbox(root).props.onChange({target:{checked:true}}));
   await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
  }else await renderAct(async()=>button(root,action).props.onClick());
  assert.equal(calls.filter(row=>row.name===action).length,0);
  assert.match(JSON.stringify(root.toJSON()),/could not be saved/);
  await renderAct(async()=>root.unmount());
 }
});

test('lost publish response freezes exact reviewed payload and retry never creates a new identity',async()=>{
 let root;const calls=[];
 const action=async(name,args)=>{calls.push({name,args});if(name==='feedback.update')throw Error('Response lost');return {accepted:true}};
 await renderAct(async()=>{root=create(React.createElement(FeedbackCorrection,{state:state(),report,act:action}))});
 await renderAct(async()=>checkbox(root).props.onChange({target:{checked:true}}));
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const sends=calls.filter(row=>row.name==='feedback.update');assert.equal(sends.length,2);assert.deepEqual(sends[1].args,sends[0].args);
 assert.equal(sends[0].args.reviewRequestId,'review-intent');assert.equal(sends[0].args.expectedRevision,'a'.repeat(64));
 assert.equal(root.root.findByProps({id:'feedback-corrected-body'}).props.disabled,true);
 await renderAct(async()=>root.unmount());
});
