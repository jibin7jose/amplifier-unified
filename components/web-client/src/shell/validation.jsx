import React from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';

const fixtureResource=kind=>({id:'fixture-artifact',kind,title:'Fixture artifact',revision:'fixture-revision',
 content:({json:'{"message":"Fixture"}',jsonl:'{"message":"Fixture"}\n',code:'const fixture = true;',html:'<h1>Fixture</h1>',babylon:'<h1>Fixture scene</h1>',mermaid:'graph TD; A-->B',dot:'digraph { A -> B }',image:'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/%3E'})[kind]||'# Fixture document\n\nA saved artifact.',
 ...(kind==='browser'?{url:'https://example.invalid/'}:{}),
 ...(kind==='a2ui'?{surface:{surfaceId:'fixture',root:'text',components:[{id:'text',component:{Text:{text:{literalString:'Fixture'}}}}]}}:{})});

// This page is served by the host validator on a sterile loopback origin. It
// has no app state, credential, service transport, or live command interface.
window.validateShellModule=async(manifest={})=>{
 const listeners=new Set(),commands=[];
 const renderer=manifest.profile==='trusted-native-renderer-v1',component=manifest.profile==='trusted-native-component-v1';
 const caps=new Set(manifest.capabilities||[]);
 function fixture(target,populated){
  if(renderer)return {resource:populated?fixtureResource(target):null,view:{},viewId:'validation-instance'};
  if(component)return {revision:1,compositionRevision:0,generation:0,slot:target,view:{},
   ...(caps.has('shell.read')?{presentation:{},runtime:{available:true},selectedWorkspaceId:populated?'fixture-project':null,selectedSessionId:populated?'fixture-chat':null}:{}),
   ...(caps.has('conversation.summary')?{conversation:populated?{id:'fixture-chat',title:'Fixture conversation',status:'idle',autoName:true}:null}:{}),
   ...(caps.has('canvas.summary')?{canvas:populated?{id:'fixture-artifact',title:'Fixture artifact',kind:'html',open:true}:{open:false}}:{}),
   ...(caps.has('attention.summary')?{attention:{unread:0,sections:{}}}:{})};
  return {generation:0,view:{},selectedWorkspaceId:populated?'fixture-project':null,selectedSessionId:null,
   workspaces:populated?[{id:'fixture-project',name:'Fixture',path:'/fixture/project',available:true}]:[],
   chatNavigation:{items:populated?[{id:'fixture-chat',title:'Fixture chat',workspace:'/fixture/project',workspaceId:'fixture-project',status:'idle',pinned:false}]:[],total:populated?1:0,index:0,pages:1,scope:{}},
   workspaceExplorer:{rows:[],page:1,pages:1,filter:''},sharedHistory:{},library:{bounded:true},attention:{sessions:{}}};
 }
 let snapshot;
 const host=Object.freeze({apiVersion:'1.0',clientId:'validation-client',instanceId:'validation-instance',viewId:'validation-instance',getSnapshot:()=>snapshot,readSource:async()=>snapshot.resource?.content||'',subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)},dispatch:async(action,args={})=>{commands.push({action,args});return {accepted:true}},setDirty:async()=>({accepted:true})});
 const moduleUrl='/module.mjs';
 const factory=(await import(/* @vite-ignore */ moduleUrl)).default;
 if(typeof factory!=='function')throw Error('Export a default component factory.');
 const Component=factory({React});
 if(typeof Component!=='function')throw Error('The factory must return a React component.');
 for(const target of component?manifest.slots:renderer?manifest.resourceKinds:[null])for(let iteration=0;iteration<2;iteration++){
  snapshot=fixture(target,false);
  const root=createRoot(document.getElementById('root'));
  flushSync(()=>root.render(<Component host={host}/>));
  await new Promise(resolve=>setTimeout(resolve,30));
  snapshot=fixture(target,true);
  flushSync(()=>listeners.forEach(listener=>listener()));
  await new Promise(resolve=>setTimeout(resolve,30));
  if(!document.getElementById('root').hasChildNodes())throw Error('Module did not render fixture content.');
  flushSync(()=>root.unmount());
  if(listeners.size)throw Error('Module leaked a host subscription after unmount.');
 }
 return {status:'passed',checks:['closed-import-graph','shared-react-factory',component?'empty-and-populated-declared-slots':renderer?'empty-and-populated-artifact':'empty-and-populated-navigation','mount-update-unmount-remount','subscription-cleanup'],fixtureCommands:commands.length};
};
