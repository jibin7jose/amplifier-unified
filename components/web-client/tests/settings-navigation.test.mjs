import {test} from 'node:test';
import assert from 'node:assert/strict';
import {settingsSections,advancedGroups,settingsLocation,settingsPatch,settingsUnread} from '../src/settings-navigation.js';
import {settingsTrail} from '../src/settings-mobile.js';

test('every destination round-trips through the existing public view action',()=>{
 const seen=new Set();
 for(const section of settingsSections)for(const [page] of section.pages){
  assert.ok(!seen.has(page));seen.add(page);
  const patch=settingsPatch(page);
  assert.deepEqual(Object.keys(patch).sort(),['panel','settingsExpanded','settingsSection']);
  assert.equal(settingsLocation(patch).page,page);
  assert.equal(settingsLocation(patch).section.id,section.id);
 }
 assert.equal(seen.size,32);assert.ok(seen.has('storage'));assert.ok(seen.has('testing'));assert.ok(seen.has('model-capacity'));
 assert.ok(seen.has('desktop'));assert.ok(!seen.has('publishing'));assert.ok(seen.has('workspaces'));
 assert.throws(()=>settingsPatch('not-a-page'),/Unknown settings page/);
});
test('saved and agent navigation from the old hierarchy stays meaningful',()=>{
 assert.equal(settingsLocation({settingsSection:'maintenance',settingsExpanded:['notifications']}).section.id,'notifications');
 assert.equal(settingsPatch('loaded-modules').panel,'chat-models');
 assert.equal(settingsPatch('recall').panel,'chat-memory');
 assert.equal(settingsPatch('conversation').panel,'session-details');
 assert.equal(settingsLocation({settingsSection:'capabilities',settingsExpanded:['registries']}).section.id,'advanced');
 assert.equal(settingsLocation({panel:'appearance',settingsExpanded:['providers']}).page,'appearance');
 assert.equal(settingsLocation({settingsExpanded:['unknown','routing']}).page,'routing');
 assert.equal(settingsLocation({settingsSection:'maintenance',settingsExpanded:[]}).page,'updates');
 assert.equal(settingsLocation({settingsSection:'capabilities',settingsExpanded:[]}).page,'app-bundles');
 assert.equal(settingsLocation({settingsExpanded:[]}).page,'ai-connections');
});
test('unread items follow their destination and are counted exactly once',()=>{
 const state={attention:{pages:{providers:2,routing:1,notifications:4,registries:3,'smart-tools':5}}};
 const counts=Object.fromEntries(settingsSections.map(s=>[s.id,settingsUnread(state,s)]));
 assert.equal(counts.ai,0);assert.equal(counts.advanced,6);assert.equal(counts.notifications,4);assert.equal(counts['smart-tools'],5);
 assert.equal(Object.values(counts).reduce((a,b)=>a+b,0),15);
});

test('retired image settings fall back to AI connections without changing provider drafts',()=>{
 assert.ok(!settingsSections.some(section=>section.pages.some(([page])=>page==='image-generation')));
 assert.ok(!advancedGroups.some(group=>group.pages.includes('image-generation')));
 assert.throws(()=>settingsPatch('image-generation'),/Unknown settings page/);
 for(const settingsSection of [undefined,'setup','advanced']){
  const view={panel:'settings',settingsSection,settingsExpanded:['image-generation'],
   aiConnectionEditor:{step:'images',imageEdited:true},imageSettingsEditor:{imageMode:'off'},
   providerEditor:{id:'one',model:'unfinished-model',scope:'project'}};
  const before=structuredClone(view);
  assert.equal(settingsLocation(view).page,'ai-connections');
  assert.equal(settingsLocation(view).section.id,'ai');
  assert.deepEqual(settingsTrail(view).map(row=>row.key),['index','ai-connections']);
  assert.deepEqual(view,before);
 }
 assert.equal(settingsLocation({settingsExpanded:['image-generation','providers']}).page,'providers');
});
