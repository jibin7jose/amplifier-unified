import test from 'node:test';
import assert from 'node:assert/strict';
import {clearAppearancePreview,previewAppearance} from '../src/appearance-preview.js';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('retired pending preview is cleared before a reopened owner can preview',async()=>{
 const response=deferred(),calls=[],values=[];const owner=value=>values.push(value);let current=true;
 const act=async(action,args)=>{calls.push([action,args]);if(action==='theme.preview'&&args.name==='old')return response.promise;return {accepted:true}};
 const old=previewAppearance(owner,act,{name:'old'},()=>current);await tick();current=false;
 const next=previewAppearance(owner,act,{name:'new'},()=>true);await tick();assert.equal(calls.length,1);
 response.resolve({accepted:true});await Promise.all([old,next]);
 assert.deepEqual(calls.map(([action,args])=>[action,args.name||args.patch.themePreview]),[['theme.preview','old'],['view.update',false],['theme.preview','new']]);
 assert.deepEqual(values,[true]);
});
test('failure and failed cleanup do not poison a later preview',async()=>{
 const response=deferred(),owner=()=>{};let current=true;
 const old=previewAppearance(owner,async action=>action==='theme.preview'?response.promise:Promise.reject(Error('cleanup failed')),{},()=>current);
 const rejected=assert.rejects(old,/cleanup failed/);await tick();current=false;response.reject(Error('response lost'));await rejected;
 let ran=false;await previewAppearance(owner,async()=>{ran=true;return {accepted:true}},{},()=>true);assert.equal(ran,true);
});
test('retired queued preview never dispatches and another owner remains independent',async()=>{
 const response=deferred(),owner=()=>{};let ran=0;
 const first=previewAppearance(owner,()=>response.promise,{},()=>true);await tick();
 const stale=previewAppearance(owner,()=>{ran++;return {accepted:true}},{},()=>false);
 await previewAppearance(()=>{},()=>{ran++;return {accepted:true}},{},()=>true);assert.equal(ran,1);
 response.resolve({accepted:true});await Promise.all([first,stale]);assert.equal(ran,1);
});
test('settled cleanup is ordered before the next preview and rejection does not activate',async()=>{
 const response=deferred(),values=[],owner=value=>values.push(value),calls=[];
 const clear=clearAppearancePreview(owner,()=>{calls.push('clear');return response.promise});
 const next=previewAppearance(owner,()=>{calls.push('preview');return {accepted:false,error:'not accepted'}},{},()=>true);
 const rejected=assert.rejects(next,/not accepted/);await tick();assert.deepEqual(calls,['clear']);response.resolve({accepted:true});await clear;await rejected;
 assert.deepEqual(calls,['clear','preview']);assert.deepEqual(values,[false]);
});
