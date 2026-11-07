import assert from 'node:assert/strict';
import test from 'node:test';
import {openSettingsDialog} from './browser-settings.mjs';

// Discovery can open onboarding while navigation is being activated. The
// fixture click may tolerate that race; an instrumented gesture failure may not.
function onboardingRace({arrives=true}={}){
 let visible=false;
 const error=new Error('Navigation gesture failed');
 const navigation={isVisible:async()=>false,click:async()=>{visible=arrives;throw error}};
 const hidden={isVisible:async()=>false};
 const page={
  waitForFunction:async()=>{},
  evaluate:async()=>false,
  locator:()=>({isVisible:async()=>visible}),
  getByRole:(_role,{name})=>name==='Open navigation'?navigation:hidden,
 };
 return {page,error};
}

test('instrumented Settings navigation preserves a gesture error when onboarding appears',async()=>{
 const {page,error}=onboardingRace();
 const labels=[];
 await assert.rejects(openSettingsDialog(page,{activate:async(label,control)=>{
  labels.push(label);await control.click();
 }}),caught=>caught===error);
 assert.deepEqual(labels,['Open navigation']);
});

test('ordinary Settings navigation tolerates onboarding arriving during its click',async()=>{
 await openSettingsDialog(onboardingRace().page);
});

test('ordinary Settings navigation preserves click failure without onboarding',async()=>{
 const {page,error}=onboardingRace({arrives:false});
 await assert.rejects(openSettingsDialog(page),caught=>caught===error);
});
