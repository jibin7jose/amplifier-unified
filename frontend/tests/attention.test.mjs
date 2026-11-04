import {shellFor} from './shell-host.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AttentionBadge,AttentionReview,ActivityPanel,FailureDisposition,FailureDispositions,readItems,reviewItems,completionToRead}=await server.ssrLoadModule('/src/attention.jsx');
const {request}=await server.ssrLoadModule('/src/api.js');
const {FeedbackNotice}=await server.ssrLoadModule('/src/feedback.jsx');
const {browserPreviewPolicy,BrowserPreview}=await server.ssrLoadModule('/src/canvas-library.jsx');
const {WorkspaceRail:Rail}=await server.ssrLoadModule('/src/shell-panels.jsx');
const {WorkHeader}=await server.ssrLoadModule('/src/work-shell.jsx');
const {WorkNavigationContext}=await server.ssrLoadModule('/src/work-navigation.js');
test.after(()=>server.close());
function WorkspaceRail(props){return React.createElement(Rail,{...props,shell:shellFor(props.state,props.act)})}
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
test('completion badges roll up across workspaces, collapsed navigation, activity and chat rows',()=>{
 const state={view:{},selectedWorkspaceId:'one',workspaceExplorer:{rows:[{path:'/one',name:'one',workspaceId:'one',chatCount:1,unread:1,canBrowse:false},{path:'/two',name:'two',workspaceId:'two',chatCount:1,unread:1,canBrowse:false}],totalWorkspaces:2},workspaces:[{id:'one',name:'One',path:'/one',available:true},{id:'two',name:'Two',path:'/two',available:true}],sessions:[{id:'a',title:'Finished plan',workspace:'/one'}],attention:{unread:2,settingsUnread:0,sections:{chats:2},sessions:{a:1},workspaces:{one:1,two:1},items:[{id:'completion:a',title:'Response ready',label:'Finished plan',sessionId:'a',fingerprint:'g1'}]}};
 assert.match(render(WorkspaceRail,{state,act:()=>{}}),/Open chats in \/two/);
 const header=React.createElement(WorkNavigationContext.Provider,{value:{history:null,visitHistory(){}}},
  React.createElement(WorkHeader,{state,act:()=>{},presentation:{}}));
 assert.match(renderToStaticMarkup(header),/2 unread items/);
 assert.match(render(ActivityPanel,{state,act:()=>{}}),/Finished plan/);
 assert.equal(render(AttentionBadge,{state,settings:true}),'');
});
test('acknowledgement includes the exact observed fingerprints',async()=>{
 let sent;await readItems((name,args)=>sent={name,args},[{id:'completion:a',fingerprint:'g1'}]);
 assert.deepEqual(sent,{name:'attention.read',args:{ids:['completion:a'],fingerprints:{'completion:a':'g1'}}});
});
test('updates keep visible notice acknowledgement and also allow explicit inbox review',()=>{
 const state={attention:{items:[{id:'update:one',page:'updates',fingerprint:'v1',read:false}]}};
 assert.equal(render(AttentionReview,{state,page:'updates',act:()=>{}}),'');
 assert.match(render(ActivityPanel,{state,act:()=>{}}),/Mark all reviewed/);
});
test('viewing a conversation can only acknowledge its completion, not errors or permissions',()=>{
 const state={selectedSessionId:'target',attention:{items:[{id:'session:target',sessionId:'target'},{id:'approval:permission',sessionId:'target'},{id:'completion:other',sessionId:'other'}]}};
 assert.equal(completionToRead(state),undefined);
 const completion={id:'completion:target',sessionId:'target'};state.attention.items.push(completion);
 assert.equal(completionToRead(state),completion);completion.read=true;
 assert.equal(completionToRead(state),undefined);
});
test('off-page errors and approvals open their owning conversation through shared actions',async()=>{
 const state={attention:{items:[{id:'session:failed',sessionId:'failed',title:'Conversation needs attention'},{id:'approval:permission',sessionId:'blocked',title:'Approval requested'}]}},calls=[];
 const act=async(name,args)=>{calls.push({name,args})};let root;
 await renderAct(async()=>{root=create(React.createElement(ActivityPanel,{state,act}))});
 const controls=root.root.findAllByProps({'data-action':'session.select'});
 for(const control of controls)await renderAct(async()=>control.props.onClick());
 assert.deepEqual(calls,[{name:'session.select',args:{id:'failed'}},{name:'view.update',args:{patch:{panel:null}}},{name:'session.select',args:{id:'blocked'}},{name:'view.update',args:{patch:{panel:null}}}]);
 await renderAct(async()=>root.unmount());
});
test('successful feedback stays in history, while pending and failed delivery remain visible',()=>{
 for(const status of ['sending','submitted','failed','unknown']){
  const receipt={requestId:'req-123',status,message:`Outcome ${status}`};
  const state={view:{panel:null},feedback:{requests:[receipt]},attention:{items:[{...receipt,id:'feedback:req-123',fingerprint:status}]}};
  const html=render(FeedbackNotice,{state,act:()=>{}});
  if(status==='submitted'){assert.equal(html,'');continue}
  assert.match(html,status==='sending'?/sending in the background/:new RegExp(`Outcome ${status}`));
  assert.match(html,/View (status|feedback)/);
  if(status==='submitted')assert.match(html,/a-check-result success/);
  if(['failed','unknown'].includes(status))assert.match(html,/a-check-result error/);
  state.attention.items[0].read=true;
  if(status!=='sending')assert.equal(render(FeedbackNotice,{state,act:()=>{}}),'');
 }
});
test('browser preview detects mixed content and handles secure loopback exceptions',()=>{
 assert.equal(browserPreviewPolicy('http://100.64.0.10:5190/content','https://host.local:8941/').blocked,true);
 assert.equal(browserPreviewPolicy('https://site.example/','https://host.local/').blocked,false);
 assert.equal(browserPreviewPolicy('http://127.0.0.1:5190/','https://host.local/').blocked,false);
 assert.equal(browserPreviewPolicy('http://[::1]:5190/','https://host.local/').localWarning,true);
 assert.equal(browserPreviewPolicy('http://localhost:5190/','http://localhost:8941/').localWarning,false);
 assert.equal(browserPreviewPolicy('http://localhost.evil.example/','https://host.local/').blocked,true);
});
test('external browser fallback is visible without expanding canvas controls',()=>{
 const html=render(BrowserPreview,{canvas:{id:'frame',kind:'browser',url:'https://example.com',title:'Example'},act:()=>{}});
 assert.match(html,/a-browser-help/);assert.match(html,/Open in browser/);assert.match(html,/Preview help/);assert.doesNotMatch(html,/allow-same-origin/);
});

test('reviewed Settings notice text stays readable while chat-specific items stay with their chat',()=>{
 const item={id:'backup',page:'repair',title:'Backup saved',detail:'Your backup is ready',fingerprint:'one'};
 const state={attention:{items:[item,{id:'chat-error',page:'repair',title:'Chat failure',sessionId:'chat'}]}};
 const before=render(AttentionReview,{state,page:'repair',act:()=>{}});
 item.read=true;
 assert.equal(render(AttentionReview,{state,page:'repair',act:()=>{}}),before);
 assert.match(before,/Your backup is ready/);assert.doesNotMatch(before,/Chat failure/);
});

test('manual unread requires a new explicit visit, including on an already-open second device',()=>{
 const item={id:'unread:chat',visit:'new-marker',fingerprint:'new',read:false};
 const state={selectedSessionId:'chat',view:{unreadVisit:'previous-marker'},attention:{items:[item]}};
 assert.equal(completionToRead(state),undefined);
 state.view.unreadVisit='new-marker';
 assert.equal(completionToRead(state),item);
 item.read=true;
 assert.equal(completionToRead(state),undefined);
});


test('app badges can be quiet while completions stay unread in chat lists',()=>{
 const state={attention:{unread:12,badgeUnread:0,sections:{chats:12}}};
 assert.equal(render(AttentionBadge,{state}),'');
 assert.match(render(AttentionBadge,{state,section:'chats'}),/12 unread items/);
});
test('read update failures precede quiet news and cannot produce a healthy empty state',()=>{
 const state={attention:{items:[
  {id:'release-notice:one',title:'Optional news',informational:true,read:false},
  {id:'updates:failure:ecosystem',title:'Component update needs attention',detail:'Current components remain active.',
   actionRequired:true,read:true,occurrences:3}
 ]}};
 const html=render(ActivityPanel,{state,act:()=>{}});
 assert.match(html,/Needs action/);
 assert.match(html,/Reviewed · still unresolved/);
 assert.match(html,/3 occurrences/);
 assert.doesNotMatch(html,/All caught up/);
 assert.ok(html.indexOf('Needs action')<html.indexOf('News and receipts'));
 assert.doesNotMatch(html,/Mark reviewed: Optional news/);
});
test('bulk review is bounded and snapshots exact fingerprints without opening chats',async()=>{
 const items=Array.from({length:1101},(_,i)=>({id:'completion:'+i,fingerprint:'v1'})),calls=[];
 await reviewItems(async(name,args)=>{calls.push({name,args})},items);
 assert.deepEqual(calls.map(c=>c.args.ids.length),[500,500,101]);
 assert.ok(calls.every(c=>c.name==='attention.read'));
 assert.equal(calls[2].args.fingerprints['completion:1100'],'v1');
});
test('explicit disposition freezes the reviewed occurrence; read and new arrivals cannot approve it',async()=>{
 let root;const calls=[],item={id:'updates:failure-overflow',title:'Additional update failures need review',detail:'Recovery is unverified.',occurrences:2,dispositionOccurrence:'old'};
 const act=async(name,args)=>{calls.push({name,args});throw Error('Update failure changed; review the current occurrence.')};
 await renderAct(async()=>{root=create(React.createElement(FailureDisposition,{item,act}))});
 const button=text=>root.root.findAllByType('button').find(n=>n.children.includes(text));
 await renderAct(async()=>button('Close unresolved notice').props.onClick());
 await renderAct(async()=>root.update(React.createElement(FailureDisposition,{item:{...item,occurrences:3,dispositionOccurrence:'new'},act})));
 await renderAct(async()=>button('Close with recovery unverified').props.onClick());
 assert.deepEqual(calls,[{name:'updates.failureDisposition',args:{id:item.id,expectedOccurrence:'old',disposition:'acknowledged-uncertainty'}}]);
 assert.match(root.root.findByProps({role:'alert'}).children[0],/changed/);
 await renderAct(async()=>button('Cancel').props.onClick());
 assert.equal(root.root.findAllByProps({'aria-label':'Confirm uncertainty disposition'}).length,0);
 await renderAct(async()=>root.unmount());
 assert.equal(render(FailureDisposition,{item:{id:'ordinary'},act}),'');
 const history=render(FailureDispositions,{state:{updates:{diagnostics:{failureDispositions:[{id:'receipt',at:1,target:item.id,occurrences:2}]}}}});
 assert.match(history,/Closed with recovery unverified/);assert.doesNotMatch(history,/Succeeded/);
});
test('unreadable disposition acknowledgements keep confirmation open without replay or verified recovery',async()=>{
 const original=globalThis.fetch;
 try{
  for(const [status,body] of [[502,'<html>Proxy acknowledgement unavailable</html>'],[200,'{"accepted":']]){
   const item={id:'updates:failure-overflow',title:'Additional update failures need review',
    detail:'Recovery is unverified.',occurrences:2,dispositionOccurrence:'a'.repeat(64)};
   const calls=[],errors=[];let root;
   globalThis.fetch=async(path,options)=>{
    assert.equal(path,'/api/actions');assert.equal(options.method,'POST');
    calls.push(JSON.parse(options.body));
    return new Response(body,{status});
   };
   const act=async(action,args)=>{
    try{return await request('/api/actions',{method:'POST',body:{action,args}})}
    catch(error){errors.push(error);throw error}
   };
   const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
   try{
    await renderAct(async()=>{root=create(React.createElement(FailureDisposition,{item,act}))});
    await renderAct(async()=>button('Close unresolved notice').props.onClick());
    await renderAct(async()=>button('Close with recovery unverified').props.onClick());
    assert.deepEqual(calls,[{action:'updates.failureDisposition',args:{id:item.id,
     expectedOccurrence:item.dispositionOccurrence,disposition:'acknowledged-uncertainty'}}]);
    assert.equal(errors.length,1);assert.equal(errors[0].code,'invalid_response');assert.equal(errors[0].unconfirmed,true);
    assert.match(root.root.findByProps({role:'alert'}).children[0],/unexpected response/);
    assert.equal(root.root.findAllByProps({'aria-label':'Confirm uncertainty disposition'}).length,1);
    const newer={...item,occurrences:3,dispositionOccurrence:'b'.repeat(64)};
    await renderAct(async()=>root.update(React.createElement(FailureDisposition,{item:newer,act})));
    assert.equal(calls.length,1,'A new occurrence must not replay the uncertain disposition');
    await renderAct(async()=>button('Cancel').props.onClick());
    assert.equal(root.root.findAllByProps({'aria-label':'Confirm uncertainty disposition'}).length,0);
    await renderAct(async()=>button('Close unresolved notice').props.onClick());
    globalThis.fetch=async(path,options)=>{
     calls.push(JSON.parse(options.body));
     return new Response(JSON.stringify({accepted:true,result:{status:'acknowledged-uncertainty',recoveryVerified:false}}));
    };
    await renderAct(async()=>button('Close with recovery unverified').props.onClick());
    assert.equal(calls.length,2,'Only a fresh explicit confirmation sends another command');
    assert.equal(calls[1].args.expectedOccurrence,newer.dispositionOccurrence);
    assert.equal(root.root.findAllByProps({'aria-label':'Confirm uncertainty disposition'}).length,0);
   }finally{if(root)await renderAct(async()=>root.unmount())}
  }
 }finally{globalThis.fetch=original}
});
test('finished response review excludes questions and errors and failures can be retried',async()=>{
 const items=[{id:'completion:a',fingerprint:'a',label:'First',title:'Response ready'},{id:'question:q',fingerprint:'q',title:'Answer needed'},{id:'provider-auth:p',fingerprint:'p',title:'Sign in',reviewable:false}];
 const calls=[];let fail=true,root;
 await renderAct(async()=>{root=create(React.createElement(ActivityPanel,{state:{attention:{items}},act:async(name,args)=>{calls.push({name,args});if(fail)throw Error('Offline')}}))});
 const button=()=>root.root.findAllByType('button').find(n=>n.children.includes('Mark responses reviewed'));
 await renderAct(async()=>button().props.onClick());
 assert.equal(root.root.findByProps({role:'alert'}).children[0],'Offline');
 fail=false;await renderAct(async()=>button().props.onClick());
 assert.deepEqual(calls.map(c=>c.args.ids),[['completion:a'],['completion:a']]);
 const all=root.root.findAllByType('button').find(n=>n.children.includes('Mark all reviewed'));
 await renderAct(async()=>all.props.onClick());assert.deepEqual(calls.at(-1).args.ids,['completion:a','question:q']);
 await renderAct(async()=>root.unmount());
});
