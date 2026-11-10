import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const [{ProviderSettings,RoutingSettings},{RuntimeSettings},{TurnTimeline,WorkTimeline},{RegistrySettings},{MaintenanceSettings}]=await Promise.all(['setup.jsx','runtime-settings.jsx','timeline.jsx','registry.jsx','maintenance.jsx'].map(file=>server.ssrLoadModule('/src/'+file)));
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
const {ConversationDetails}=await server.ssrLoadModule('/src/conversation-controls.jsx');
const act=()=>{};
test.after(()=>server.close());
test('setup panels render redacted provider configuration and valid required routing roles',()=>{
 const state={view:{providerEditor:{module:'provider-openai',credentialMode:'private'},routingEditor:{matrix:{roles:{general:{description:'General',candidates:[{provider:'openai',model:'model'}]},fast:{description:'Fast',candidates:[{provider:'openai',model:'model'}]}}}}},setup:{providers:[{id:'openai',module:'provider-openai',config:{api_key:'[REDACTED]'},credentialsConfigured:true}],matrices:[]}};
 const provider=renderToStaticMarkup(React.createElement(ProviderSettings,{state,session:{id:'s',status:'ready'},act}));
 assert.match(provider,/credentials ready/);assert.match(provider,/type="password"/);assert.match(provider,/data-action="providers.save"/);
 const routing=renderToStaticMarkup(React.createElement(RoutingSettings,{state,act}));
 assert.match(routing,/general description/);assert.match(routing,/data-collection-id="fast"/);assert.match(routing,/routing.save/);
});
test('runtime controls expose only bundle-supported goal actions',()=>{
 const state={view:{runtimeDraft:{tab:'direction'}},runtimeControl:{s:{'catalog.inspect':{capabilities:{goals:false,modes:false}}}}};
 const html=renderToStaticMarkup(React.createElement(RuntimeSettings,{state,session:{id:'s',status:'ready'},act}));
 assert.match(html,/does not expose a goal controller/);assert.match(html,/disabled=""[^>]*data-action="runtime.control"/);
});
test('legacy summaries stay in explicit details, not substituted for original replies',()=>{
 const data={turns:[{id:'t',phase:'complete',aggregateUsage:{calls:1,inputTokens:100,outputTokens:20,costUsd:0,costType:'unavailable'}}],nodes:[{id:'tool',kind:'tool',turnId:'t',label:'delegate',phase:'complete'},{id:'child',parentId:'tool',kind:'worker',turnId:'t',label:'Research',summary:'**Result:** complete',phase:'complete'}]};
 const state={view:{executionExpanded:['turn:t','exchange:child:details']}};
 const html=renderToStaticMarkup(React.createElement(TurnTimeline,{data,turnId:'t',state,act}));
 assert.match(html,/Research/);assert.match(html,/<strong>Result:<\/strong>/);assert.doesNotMatch(html,/cost unavailable|usage pending/);assert.doesNotMatch(html,/\$0\.00/);
 assert.match(html,/Saved task summary/);assert.match(html,/not the original reply/);
});

for(const [phase,label] of [['stopped','Stopped'],['cancelled','Cancelled'],['interrupted','Interrupted']])test(`${phase} work headers describe their terminal outcome`,()=>{
 const data={turns:[{id:'t',phase,startedAt:1,endedAt:10},{id:'earlier',phase:'completed',startedAt:0,endedAt:1}],nodes:[
  {id:'model',kind:'llm',turnId:'t',phase:'completed',startedAt:1,endedAt:5}]};
 const state={view:{executionExpanded:['closed:turn:t','closed:work:earlier']}};
 const html=renderToStaticMarkup(React.createElement(TurnTimeline,{data,turnId:'t',state,act}));
 assert.match(html,new RegExp(label+' · 9s'));
 assert.match(html,new RegExp('data-view-label="'+label+' · 9s · expand work details"'));
 assert.doesNotMatch(html,/Completed work|Worked for|aria-label="Completed"/);
 const grouped=renderToStaticMarkup(React.createElement(WorkTimeline,{items:[{turnId:'earlier'},{turnId:'t'}],data,state,act}));
 assert.match(grouped,new RegExp(label+' · 10s'));
 assert.doesNotMatch(grouped,/Worked for|aria-label="Completed"/);
});

test('live work timers advance without changing single or grouped interface report labels',()=>{
 const originalNow=Date.now;
 try{
  const data={turns:[{id:'t',phase:'running',startedAt:1},{id:'earlier',phase:'completed',startedAt:0,endedAt:1}],nodes:[]};
  const state={view:{executionExpanded:['closed:turn:t','closed:work:earlier']}};
  for(const [Component,props] of [[TurnTimeline,{turnId:'t'}],[WorkTimeline,{items:[{turnId:'earlier'},{turnId:'t'}]}]]){
   const render=()=>renderToStaticMarkup(React.createElement(Component,{data,state,act,...props}));
   Date.now=()=>20000;const first=render();
   Date.now=()=>25000;const later=render();
   const report=html=>{const match=html.match(/data-view-label="([^"]+)"/);assert.ok(match,"Work control must have a stable interface-report label");return match[1]};
   const visible=html=>html.match(/class="a-execution-label">([^<]+)/)[1];
   assert.equal(report(first),'Working… · expand work details');
   assert.equal(report(later),report(first));
   assert.notEqual(visible(later),visible(first));
   data.turns[0]={...data.turns[0],phase:'interrupted',endedAt:25};
   assert.match(report(render()),/^Interrupted · /);
   data.turns[0]={id:'t',phase:'running',startedAt:1};
  }
 }finally{Date.now=originalNow}
});

test('rejected follow-up is not labeled submitted and running worker is not completed',()=>{
 const worker={id:'w',kind:'worker',turnId:'t',sessionId:'child',label:'Review',phase:'running',exchange:{id:'d',phase:'running',delegateRequest:'Review'}};
 const data={turns:[{id:'t',phase:'running'}],nodes:[worker,
  {id:'read',kind:'tool',turnId:'t',sessionId:'child',parentId:'w',phase:'completed'},
  {id:'followup',kind:'tool',turnId:'t',sessionId:'root',delegate:worker,phase:'error',exchange:{id:'followup',delegateRequest:'Continue',phase:'error'}}]};
 const html=renderToStaticMarkup(React.createElement(TurnTimeline,{data,turnId:'t',state:{view:{executionExpanded:['turn:t','delegate:t:w']}},act}));
 assert.match(html,/Follow-up error; delivery was not confirmed/);
 assert.doesNotMatch(html,/Submitted/);assert.match(html,/Delegate is working/);
 assert.match(html,/Stop worker/);
});

for(const phase of ['cancelled','stopped'])test(`persistent reports remain chronological and cannot hide worker ${phase}`,()=>{
 const worker={id:'w',kind:'worker',turnId:'t',sessionId:'child',label:'Review',phase,exchange:{id:'d',phase:'running',delegateRequest:'Review',delivery:'separate'}};
 const data={turns:[{id:'t',phase:'completed'}],nodes:[worker,
  {id:'followup',kind:'tool',turnId:'t',delegate:worker,startedAt:21,exchange:{id:'followup',delegateRequest:'SECOND REQUEST',phase:'completed'}},
  {id:'report1',kind:'exchange',turnId:'t',delegate:worker,startedAt:20,exchange:{id:'report1',delegateResponse:'FIRST REPORT',report:true}},
  {id:'report2',kind:'exchange',turnId:'t',delegate:worker,startedAt:22,exchange:{id:'report2',delegateResponse:'SECOND REPORT',report:true}}]};
 const html=renderToStaticMarkup(React.createElement(TurnTimeline,{data,turnId:'t',state:{view:{executionExpanded:['turn:t','delegate:t:w']}},act}));
 assert.ok(html.indexOf('FIRST REPORT')<html.indexOf('SECOND REQUEST'));
 assert.ok(html.indexOf('SECOND REQUEST')<html.indexOf('SECOND REPORT'));
 assert.match(html,new RegExp('Worker '+phase));assert.doesNotMatch(html,/Waiting for response|delegate below/);
});

test('advanced registries expose scoped source controls through the shared registry',()=>{
 const state={view:{settingsExpanded:['registries'],registryDraft:{open:true,tab:'sources'}},registry:{sources:[{kind:'module',name:'tool-custom',source:'/workspace/tool',scope:'project'}]}};
 const html=renderToStaticMarkup(React.createElement(RegistrySettings,{state,act}));
 assert.match(html,/tool-custom/);assert.match(html,/data-action="sources.save"/);assert.match(html,/data-action="sources.remove"/);
});
test('recovery requires a matching preview and explicit reset text',()=>{
 const state={view:{settingsExpanded:['reset'],maintenanceDraft:{openSections:['reset'],resetParts:['runtime'],resetConfirmation:''}},maintenance:{resetPreview:{parts:['runtime'],paths:['/private/runtime']}}};
 const html=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(html,/Type RESET/);assert.match(html,/disabled=""[^>]*data-action="maintenance.reset"[^>]*>Apply selected reset/);
 const changed={...state,view:{...state.view,maintenanceDraft:{...state.view.maintenanceDraft,resetParts:['settings']}}};
 assert.doesNotMatch(renderToStaticMarkup(React.createElement(MaintenanceSettings,{state:changed,act})),/Apply selected reset/);
});

test('module behavior validation opt-in and real results are visible through shared controls',()=>{
 const state={view:{settingsExpanded:['registries'],registryDraft:{open:true,tab:'modules',id:'tool-fixture',behavioral:true,behaviorResultsExpanded:true}},registry:{validation:{id:'tool-fixture',passed:true,checks:[],behavioral:{passed:false,exitCode:1,tests:[{name:'tool response contract',status:'failed'},{name:'optional streaming',status:'skipped'}]}}}};
 const html=renderToStaticMarkup(React.createElement(RegistrySettings,{state,act}));
 assert.match(html,/Also run module behavior tests/);assert.match(html,/data-action="modules.validate"/);assert.match(html,/Behavior tests need attention/);assert.match(html,/tool response contract/);assert.match(html,/skipped/);
});

test('provider environment selection shows default, custom availability and no key input',()=>{
 const state={view:{providerEditor:{module:'provider-openai',credentialMode:'environment',envVar:'TEAM_API_KEY'}},setup:{credentialCheck:{module:'provider-openai',defaultEnvVar:'OPENAI_API_KEY',envVar:'TEAM_API_KEY',available:true}}};
 const html=renderToStaticMarkup(React.createElement(ProviderSettings,{state,act}));
 assert.match(html,/OPENAI_API_KEY/);assert.match(html,/TEAM_API_KEY/);assert.match(html,/Found — this variable will supply the key/);
 assert.match(html,/data-action="providers.credentials"/);assert.doesNotMatch(html,/type="password"/);
 state.setup.credentialCheck.available=false;
 assert.match(renderToStaticMarkup(React.createElement(ProviderSettings,{state,act})),/Not found in the app environment/);
});

test('available updates are visible without opening the full source inventory',()=>{
 const state={view:{},settings:{},updates:{lastCheck:1,available:2,items:[{id:'current',label:'Already current source',status:'current'},{id:'changed',label:'Updated community bundle',status:'update',current:'abc',latest:'def'},{id:'pinned',label:'Pinned library',status:'pinned'}]}};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state,act}));
 assert.match(html,/Ecosystem updates/);assert.match(html,/Updated community bundle/);assert.match(html,/1 available/);
 assert.doesNotMatch(html,/Already current source|Pinned library/);assert.match(html,/Show all 3 sources/);
 state.view.maintenanceDraft={updatesExpanded:true};
 const all=renderToStaticMarkup(React.createElement(UpdateSettings,{state,act}));
 assert.match(all,/Already current source/);assert.match(all,/Pinned library/);
});
test('no pending updates still distinguishes failed checks from current sources',()=>{
 const state={view:{},settings:{},updates:{lastCheck:1,items:[{id:'failed',label:'Offline source',status:'check_failed'}]}};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state,act}));
 assert.match(html,/No installable updates remain, but some checks or local changes need review/);assert.match(html,/Needs attention/);assert.match(html,/Offline source/);assert.match(html,/a-check-result error/);
});

test('provider model results and metadata choices are visible even after other management actions finish',()=>{
 const state={view:{providerEditor:{id:'openai',module:'provider-openai',model:'gpt-6-astra',config:'{"reasoning_effort":"high"}'}},management:{phase:'ready',operation:'notifications.get'},modelCatalogs:{'openai-connection':{phase:'ready',supported:true,models:[{id:'gpt-6-astra',display_name:'Astra'}]}},setup:{providerCatalogs:{openai:{sharedCatalogKey:'openai-connection'}},metadata:{'provider-openai':{info:{config_fields:[{id:'reasoning_effort',display_name:'Reasoning effort',field_type:'choice',choices:['low','high'],requires_model:true},{id:'enabled',field_type:'boolean'}]}}},operations:{'providers.models:openai':{phase:'ready'}}}};
 const html=renderToStaticMarkup(React.createElement(ProviderSettings,{state,act}));
 assert.match(html,/Found 1 models/);assert.doesNotMatch(html,/Available models/);assert.match(html,/<select id="provider-model-catalog"/);
 assert.match(html,/<select id="provider-option-reasoning_effort"/);
 assert.match(html,/<option value="high" selected="">high/);
 assert.match(html,/<select id="provider-option-enabled"/);
 state.setup.operations['providers.models:openai']={phase:'error',error:'Provider check timed out. Please retry.'};
 assert.match(renderToStaticMarkup(React.createElement(ProviderSettings,{state,act})),/Provider check timed out/);
});


test('native CLI history is automatic and stale sharing settings fall back to maintenance menu',()=>{
 const state={view:{settingsSection:'maintenance',settingsExpanded:['shared-history']},sessions:[{id:'native',title:'CLI chat'}],selectedSessionId:'native'};
 const menu=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(menu,/Conversation history/);
 assert.doesNotMatch(menu,/Same-chat CLI and web|Browse shared conversations/);
 state.view.settingsExpanded=['history'];
 const history=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(history,/CLI projects and conversations appear automatically/);
 assert.doesNotMatch(history,/data-action="history.export"/);
 assert.match(history,/Chat → Share or save/);
 assert.match(history,/data-action="history.importFile"/);
 assert.doesNotMatch(history,/data-action="history.import"|Browse saved conversations/);
});

test('module mount failures expose remediation without another disclosure',()=>{
 const session={id:'fixture',status:'error',error:'Configured modules failed to mount',moduleFailures:[{module:'tool-fixture',reason_code:'invalid_entry_point',guidance:'Check the module entry point and async mount function.'}]};
 const html=renderToStaticMarkup(React.createElement(ConversationDetails,{session,act}));
 assert.match(html,/Some parts of this chat could not load/);
 assert.doesNotMatch(html,/could not identify the cause/);
 assert.match(html,/tool-fixture/);assert.match(html,/Check the module entry point and async mount function/);
 assert.doesNotMatch(html,/<summary>Runtime message/);
});


test('saved observation windows and partial live tails do not invent whole-turn counts',()=>{
 for(const flags of [{observationWindow:true},{partialWindow:true}]){
  const turn={id:'window',phase:flags.partialWindow?'running':'recorded',startedAt:1,...flags};
  const data={turns:[turn],nodes:[{id:'entry',kind:'tool',turnId:'window',label:'Recorded tool',phase:'recorded'}]};
  const html=renderToStaticMarkup(React.createElement(TurnTimeline,{data,turnId:'window',state:{view:{}},act}));
  assert.doesNotMatch(html,/a-execution-call-counts|a-execution-usage/);
  assert.match(html,flags.observationWindow?/Recent saved work/:/Working/);
 }
});
