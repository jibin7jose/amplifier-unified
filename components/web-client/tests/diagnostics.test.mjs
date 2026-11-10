import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DiagnosticsSettings}=await server.ssrLoadModule('/src/diagnostics.jsx');
test.after(()=>server.close());globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const config={enabled:true,retentionDays:30,maxRecords:25000,streams:['updates'],destinations:[]};
function state(extra={}){return {view:{settingsSection:'maintenance',settingsExpanded:['diagnostics'],...extra},diagnostics:{config,streams:[{id:'updates',label:'Update diagnostics'},{id:'conversation',label:'Conversation text (content)',content:true}],local:{records:5},destinations:[],results:{}}};}
test('local capture and content choices expose shared actions without an implicit destination',()=>{
 const html=renderToStaticMarkup(React.createElement(DiagnosticsSettings,{state:state(),act:()=>{}}));
 assert.match(html,/App forwarding starts only after you save an enabled destination/);assert.match(html,/Conversation text/);assert.match(html,/5 records/);
 assert.match(html,/data-action="diagnostics.configure"/);assert.match(html,/data-action="diagnostics.records"/);
 assert.match(html,/data-action="view.update"/);assert.doesNotMatch(html,/type="password"/);
});
test('adding a destination updates agent-readable draft; save uses the same typed action',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(DiagnosticsSettings,{state:state(),act:(name,args)=>{calls.push([name,args]);return {accepted:true}}}))});
 const add=root.root.findAll(n=>n.type==='button').find(n=>n.children.includes('Add server'));
 await renderAct(async()=>add.props.onClick());
 assert.equal(calls[0][0],'view.update');const draft=calls[0][1].patch.diagnosticsDraft;
 assert.equal(draft.config.destinations[0].enabled,false);assert.equal(draft.destinationId,draft.config.destinations[0].id);
 const updated=state({diagnosticsDraft:draft});
 await renderAct(async()=>root.update(React.createElement(DiagnosticsSettings,{state:updated,act:(name,args)=>{calls.push([name,args]);return {accepted:true}}})));
 const check=root.root.findAll(n=>n.type==='button'&&n.props['data-action']==='diagnostics.test')[0];assert.equal(check.props.disabled,true);
 const save=root.root.findAll(n=>n.type==='button'&&n.props['data-action']==='diagnostics.configure')[0];
 await renderAct(async()=>save.props.onClick());
 assert.equal(calls[1][0],'diagnostics.configure');assert.equal(calls[1][1].config.destinations[0].apiKeyEnv,'AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY');
 assert.equal(calls[2][0],'view.update');assert.equal(calls[2][1].patch.diagnosticsDraft.config,null);
 await renderAct(async()=>root.unmount());
});

test('a rejected save or removal retains the shared configuration draft',async()=>{
 const calls=[],draft={destinationId:'saved',config:{...config,destinations:[{id:'saved',name:'Personal',url:'https://example.test',streams:[],authMode:'static',apiKeyEnv:'CI_KEY'}]}};let root;
 await renderAct(async()=>{root=create(React.createElement(DiagnosticsSettings,{state:state({diagnosticsDraft:draft}),act:async(name,args)=>{calls.push([name,args]);return undefined}}))});
 const buttons=root.root.findAll(n=>n.type==='button'&&n.props['data-action']==='diagnostics.configure');
 for(const button of buttons)await renderAct(async()=>button.props.onClick());
 assert.equal(calls.length,2);assert.ok(calls.every(([name])=>name==='diagnostics.configure'));
 await renderAct(async()=>root.unmount());
});


test('recording changes save immediately without committing unfinished destination or numeric drafts',async()=>{
 const draft={config:{...config,providerRequests:true,retentionDays:21,destinations:[{id:'unsaved',name:'Unsaved',enabled:true,streams:[]}]}};
 const value=state({diagnosticsDraft:draft});value.diagnostics.config={...config,providerRequests:false};
 const html=renderToStaticMarkup(React.createElement(DiagnosticsSettings,{state:value,act:()=>{}}));
 assert.match(html,/Saved recording setting:.*Off/s);assert.doesNotMatch(html,/Unsaved change/);
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(DiagnosticsSettings,{state:value,act:async(name,args)=>{calls.push([name,args]);return {accepted:true}}}))});
 const toggle=root.root.findAllByType('input').find(n=>n.props.type==='checkbox'&&n.parent.children.some(c=>typeof c==='string'&&c.includes('Record provider')));
 await renderAct(async()=>toggle.props.onChange({target:{checked:true}}));
 assert.deepEqual(calls[0],['diagnostics.configure',{patch:{providerRequests:true}}]);
 assert.equal(calls[1][1].patch.diagnosticsDraft.config.retentionDays,21);
 assert.equal(calls[1][1].patch.diagnosticsDraft.config.destinations[0].id,'unsaved');
 await renderAct(async()=>root.unmount());
});

test('a capture switch queues behind a numeric save without dropping either change',async()=>{
 const calls=[];let root,release;
 const send=(name,args)=>{calls.push([name,args]);if(name==='diagnostics.configure'&&calls.filter(c=>c[0]===name).length===1)return new Promise(resolve=>release=resolve);return Promise.resolve({accepted:true})};
 await renderAct(()=>{root=create(React.createElement(DiagnosticsSettings,{state:state(),act:send}))});
 try{
  const days=root.root.findAllByType('input').find(n=>n.parent.children.includes('Index days'));
  await renderAct(()=>days.props.onBlur({target:{value:'21',checkValidity:()=>true}}));
  const record=root.root.findAllByType('input').find(n=>n.parent.children.some(c=>typeof c==='string'&&c.includes('Record provider')));
  await renderAct(()=>{record.props.onChange({target:{checked:true}})});
  assert.equal(record.props.checked,true);
  assert.equal(calls.filter(c=>c[0]==='diagnostics.configure').length,1);
  await renderAct(async()=>{release({accepted:true})});
  assert.deepEqual(calls.filter(c=>c[0]==='diagnostics.configure').map(c=>c[1]),[{patch:{retentionDays:21}},{patch:{providerRequests:true}}]);
 }finally{await renderAct(()=>root.unmount())}
});
