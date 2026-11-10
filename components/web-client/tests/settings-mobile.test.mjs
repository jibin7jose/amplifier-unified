import {test} from 'node:test';
import assert from 'node:assert/strict';
import {settingsPatch,settingsSections,settingsParent} from '../src/settings-navigation.js';
import {settingsTrail,settingsBaseNavigation,mergeSettingsNavigation,settingsIndexPatch} from '../src/settings-mobile.js';
test('mobile navigation preserves current editor drafts and resets only navigation flags',()=>{
 const view={providerEditor:{id:'one',model:'latest-draft',detailOpen:true,order:{ids:['two','one']}},settingsFilters:{providers:'latest-filter'}};
 const patch=mergeSettingsNavigation(view,settingsBaseNavigation('providers'));
 assert.deepEqual(patch.providerEditor,{id:'one',model:'latest-draft',detailOpen:false,order:{ids:['two','one']},orderOpen:false});
 assert.equal('settingsFilters' in patch,false);
 assert.deepEqual(settingsTrail(settingsIndexPatch).map(row=>row.key),['index']);
});
test('every settings destination has an index and a section parent',()=>{
 for(const section of settingsSections)for(const [page]of section.pages){
  const trail=settingsTrail(settingsBaseNavigation(page));assert.deepEqual(trail.map(row=>row.key),['index',...(settingsParent(page)?[settingsParent(page)]:[]),page]);
 }
});
test('nested routing navigation excludes editable model/profile contents',()=>{
 const view={...settingsPatch('routing'),routingEditor:{role:'coding',candidate:2,candidateOpen:true,detailOpen:true,matrix:{secretFixture:'never snapshot'}}};
 const trail=settingsTrail(view);assert.deepEqual(trail.map(row=>row.key),['index','advanced','routing','routing/role/coding','routing/choice/coding']);
 assert.equal(JSON.stringify(trail).includes('never snapshot'),false);
 const parent=mergeSettingsNavigation(view,trail.at(-2).navigation);assert.equal(parent.routingEditor.candidateOpen,false);assert.equal(parent.routingEditor.matrix.secretFixture,'never snapshot');
});
test('starter detail navigation retains draft privacy and returns to Workspaces',()=>{
 const view={...settingsPatch('workspace-starters'),workspaceStarterEditor:{id:'custom',detailOpen:true}};
 const state={workspaceStarters:{items:[{id:'custom',name:'Team starter',instructions:'PRIVATE-INSTRUCTIONS'}]}};
 const trail=settingsTrail(view,state);
 assert.deepEqual(trail.map(row=>row.key),['index','workspaces','workspace-starters','starters/detail']);
 assert.equal(JSON.stringify(trail).includes('PRIVATE-INSTRUCTIONS'),false);
 assert.equal(mergeSettingsNavigation(view,trail.at(-2).navigation).workspaceStarterEditor.detailOpen,false);
});
test('catalog source setup and module details have meaningful parents',()=>{
 const tool=settingsTrail({...settingsPatch('smart-tools'),smartToolsEditor:{page:'source',returnPage:'catalog',repository:'private-repo'}});assert.deepEqual(tool.map(row=>row.key),['index','smart-tools','tools/catalog','tools/source']);assert.equal(JSON.stringify(tool).includes('private-repo'),false);
 assert.equal(settingsPatch('loaded-modules').panel,'chat-models');
});

import {currentRoutingOrder,routingOrderDraft} from '../src/setup-data.js';
test('routing order drafts cannot index a replaced or shortened candidate set',()=>{
 const choices=[{provider:'one',model:'first',config:{custom:'keep'}},{provider:'two',model:'second'}];
 const draft={...routingOrderDraft(choices,'general'),ids:['1','0']};
 assert.equal(currentRoutingOrder(choices,'general',draft),true);assert.equal(routingOrderDraft(choices,'general',draft),draft);
 for(const changed of [choices.slice(0,1),[...choices,{provider:'three',model:'third'}],[choices[0],{provider:'other',model:'replacement'}]]){
  assert.equal(currentRoutingOrder(changed,'general',draft),false);const fresh=routingOrderDraft(changed,'general',draft);assert.equal(currentRoutingOrder(changed,'general',fresh),true);assert.match(fresh.notice,/choices changed/);
 }
 assert.equal(currentRoutingOrder(choices,'general',{...draft,ids:['0','0']}),false);
 assert.equal(currentRoutingOrder(choices,'fast',draft),false);assert.equal(choices[0].config.custom,'keep');
});
