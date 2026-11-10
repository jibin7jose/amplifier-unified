import test from 'node:test';
import assert from 'node:assert/strict';
import {beginRegionActivity} from '../src/activity-feedback.js';
import {createActionFeedback} from '../src/action-feedback.js';

function element(action,region){
 const attributes=new Map(),listeners=new Map();
 return {tagName:'BUTTON',dataset:{action},getAttribute:name=>attributes.get(name)??null,
  setAttribute:(name,value)=>attributes.set(name,value),removeAttribute:name=>attributes.delete(name),
  closest:selector=>selector==='[data-action]'?null:selector.startsWith('button')?undefined:region,
  addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),
  fire(type,button){let prevented=false;listeners.get(type)?.({type,target:{closest:()=>button},preventDefault:()=>{prevented=true},stopPropagation(){}});return prevented;},
 };
}
test('overlapping region work ends independently and preserves existing accessibility state',()=>{
 const region=element();region.setAttribute('aria-busy','false');
 const first=beginRegionActivity(region),second=beginRegionActivity(region);
 first();first();assert.equal(region.getAttribute('aria-busy'),'true');
 second();assert.equal(region.getAttribute('aria-busy'),'false');assert.equal(region.getAttribute('data-region-pending'),null);
});
test('only the initiating action is busy; another click cannot repeat it; cleanup permits retry',async()=>{
 const root=element(),region=element(),button=element('refresh',region),feedback=createActionFeedback();
 const detach=feedback.attach(root);root.fire('click',button);
 const unrelated=feedback.begin('other');assert.equal(button.getAttribute('aria-busy'),null);unrelated();
 const finish=feedback.begin('refresh');assert.equal(button.getAttribute('aria-busy'),'true');assert.equal(button.getAttribute('aria-disabled'),'true');
 assert.equal(root.fire('click',button),true);assert.equal(root.fire('change',button),false);
 finish();assert.equal(button.getAttribute('aria-disabled'),null);assert.equal(button.getAttribute('aria-busy'),null);assert.equal(root.fire('click',button),false);
 await new Promise(resolve=>setTimeout(resolve,0));feedback.begin('refresh');assert.equal(button.getAttribute('aria-busy'),null,'background work must not animate the last focused control');detach();
});
test('optimistic navigation remains usable while its save is pending',()=>{
 const root=element(),button=element('view.update'),feedback=createActionFeedback();feedback.attach(root);root.fire('click',button);
 feedback.begin('view.update');assert.equal(button.getAttribute('aria-busy'),null);assert.equal(root.fire('click',button),false);
});
