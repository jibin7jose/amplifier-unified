import test from 'node:test';
import assert from 'node:assert/strict';
import {imageJobs,imageJobLabel,imageGalleryEntries} from '../src/image-generation.js';

test('saved results replace only matching image work in the same chat and turn',()=>{
 const jobs=[{id:'a',messageId:'origin',requestId:'a',phase:'running',operation:'generate'},
  {id:'b',messageId:'origin',requestId:'b',phase:'running',operation:'edit'},
  {id:'c',messageId:'later',requestId:'c',phase:'running',operation:'generate'}];
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}],canvasArtifacts:[
  {sessionId:'s',messageId:'origin',imageRequestId:'a'},
  {sessionId:'other',messageId:'origin',imageRequestId:'b'},
  {sessionId:'s',messageId:'later',imageRequestId:'b'}]};
 assert.deepEqual(imageJobs(state,'origin').map(row=>row.id),['b']);
 assert.equal(imageJobLabel(imageJobs(state,'origin')[0]),'Editing image…');
 assert.deepEqual(imageJobs({...state,selectedSessionId:'missing'},'origin'),[]);
});

test('stopped and unknown work do not imply ongoing progress or automatic retry',()=>{
 assert.equal(imageJobLabel({phase:'completed'}),'Image generated');
 assert.equal(imageJobLabel({phase:'error'}),'Image generation did not finish');
 for(const phase of ['unknown','interrupted'])assert.equal(imageJobLabel({phase}),'Image generation stopped; check activity for its outcome');
});


test('gallery retains request order and selection identity through out-of-order receipts',()=>{
 const jobs=['a','b','c'].map(id=>({id,requestId:id,messageId:'origin',phase:'running'}));
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}]};
 const image=id=>({row:{id:'image-'+id,imageRequestId:id},version:1});
 const second=image('b'),first=image('a');
 const pending=imageGalleryEntries(state,'origin',[]);
 const mixed=imageGalleryEntries(state,'origin',[second]);
 assert.deepEqual(mixed.map(entry=>entry.id),pending.map(entry=>entry.id));
 assert.equal(mixed[0].item,undefined);assert.equal(mixed[1].item,second);
 const finished=imageGalleryEntries(state,'origin',[second,first]);
 assert.equal(finished[0].item,first);assert.equal(finished[1].item,second);
 assert.deepEqual(imageGalleryEntries(state,'later',[]),[]);
 const revised={...first,version:2};
 assert.equal(imageGalleryEntries(state,'origin',[first,revised]).length,4,'Additional versions are preserved');
 assert.equal(imageGalleryEntries({...state,selectedSessionId:'other'},'origin',[first])[0].item,first,'Saved images work without retained execution');
});


test('correcting a rejected image request keeps one slot and the completed result',()=>{
 const rejected={id:'first',requestId:'same-effect',messageId:'origin',operation:'generate',phase:'error'};
 const completed={...rejected,id:'second',phase:'completed'};
 const state=jobs=>({selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}]});
 const item={row:{id:'saved',imageRequestId:'same-effect'},version:1};
 const before=imageGalleryEntries(state([rejected]),'origin',[]);
 const after=imageGalleryEntries(state([rejected,completed]),'origin',[item]);
 assert.equal(after.length,1);
 assert.equal(after[0].id,before[0].id);
 assert.equal(after[0].job.phase,'completed');
 assert.equal(after[0].item,item);
 assert.equal(imageGalleryEntries(state([rejected,{...completed,requestId:'another'}]),'origin',[]).length,2);
});
