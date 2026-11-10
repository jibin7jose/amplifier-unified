import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SubagentHistory,SubagentHistoryButton}=await server.ssrLoadModule('/src/subagent-history.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('subagent drilldown is bounded, searchable, and uses shared view and session actions',async()=>{
 const parent={id:'root',title:'A project',sessionKind:'root',subagentCount:123};
 const savedChildren=Array.from({length:123},(_,index)=>({id:'worker-'+index,title:'Research '+index,sessionKind:'worker',parentId:'root'}));
 const state={browserResources:['selectedContext'],view:{},sessions:[parent]};
 // Model the canonical server's explicit page response, never a resident fallback.
 const receive=()=>{const {filter='',index=0}=state.view.subagentHistory||{},rows=savedChildren.filter(row=>row.title.includes(filter)),pages=Math.max(1,Math.ceil(rows.length/50)),actual=Math.min(pages-1,index),start=actual*50;
  state.browserResources=['selectedContext','subagentNavigation'];state.subagentNavigation={items:rows.slice(start,start+50),total:rows.length,unfilteredTotal:123,index:actual,pages,start,end:Math.min(rows.length,start+50),scope:{sessionId:'root',filter,index}};
 };
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')Object.assign(state.view,args.patch);return {accepted:true}};
 let button,list;
 await renderAct(async()=>{button=create(React.createElement(SubagentHistoryButton,{state,session:parent,act}))});
 assert.match(JSON.stringify(button.toJSON()),/123/);
 await renderAct(async()=>button.root.findByType('button').props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{panel:'subagent-history',subagentHistory:{sessionId:'root',filter:'',index:0}}}});
 receive();
 const render=()=>React.createElement(SubagentHistory,{state,session:parent,act});
 await renderAct(async()=>{list=create(render())});
 assert.equal(list.root.findAllByProps({'data-action':'session.select'}).length,50);
 const more=list.root.findAllByType('button').find(node=>node.children.includes('More subagents'));
 await renderAct(async()=>more.props.onClick());
 assert.equal(calls.at(-1).args.patch.subagentHistory.index,1);
 await renderAct(async()=>list.update(render()));
 assert.match(JSON.stringify(list.toJSON()),/Loading subagent conversations/);
 receive();await renderAct(async()=>list.update(render()));
 assert.equal(list.root.findAllByProps({'data-action':'session.select'})[0].props.title,'worker-50');
 await renderAct(async()=>list.root.findByType('input').props.onChange({target:{value:'Research 122'}}));
 assert.deepEqual(calls.at(-1).args.patch.subagentHistory,{sessionId:'root',filter:'Research 122',index:0});
 receive();await renderAct(async()=>list.update(render()));
 const children=list.root.findAllByProps({'data-action':'session.select'});assert.equal(children.length,1);
 await renderAct(async()=>children[0].props.onClick());
 assert.deepEqual(calls.slice(-2),[{name:'session.select',args:{id:'worker-122'}},{name:'view.update',args:{patch:{panel:null}}}]);
 await renderAct(async()=>{button.unmount();list.unmount()});
});

test('failed subagent selection keeps the drilldown open and no children needs no button',async()=>{
 const parent={id:'root',subagentCount:0},state={browserResources:['selectedContext'],view:{},sessions:[parent]},calls=[];
 const act=async(name,args)=>{calls.push({name,args});return {accepted:false}};let root;
 await renderAct(async()=>{root=create(React.createElement(SubagentHistoryButton,{state,session:parent,act}))});
 assert.equal(root.toJSON(),null);
 parent.subagentCount=1;state.browserResources=['selectedContext','subagentNavigation'];
 state.subagentNavigation={items:[{id:'child',sessionKind:'worker',parentId:'root'}],total:1,unfilteredTotal:1,index:0,pages:1,start:0,end:1,scope:{sessionId:'root',filter:'',index:0}};
 await renderAct(async()=>root.update(React.createElement(SubagentHistory,{state,session:parent,act})));
 await renderAct(async()=>root.root.findByProps({'data-action':'session.select'}).props.onClick());
 assert.deepEqual(calls,[{name:'session.select',args:{id:'child'}}]);
 await renderAct(async()=>root.unmount());
});

test('bounded subagent drilldown uses server totals and waits for off-page results',async()=>{
 const parent={id:'root',title:'Project',subagentCount:123},state={browserResources:['selectedContext','subagentNavigation'],library:{bounded:true},view:{},sessions:[parent],subagentNavigation:{items:[{id:'remote-worker',title:'Remote worker'}],total:123,unfilteredTotal:123,index:0,pages:3,start:0,end:50,scope:{sessionId:'root',filter:'',index:0}}};
 const calls=[],act=async(name,args)=>{calls.push({name,args});Object.assign(state.view,args.patch);return {accepted:true}};let root;
 await renderAct(async()=>{root=create(React.createElement(SubagentHistoryButton,{state,session:parent,act}))});
 assert.match(JSON.stringify(root.toJSON()),/123/);
 const render=()=>React.createElement(SubagentHistory,{state,session:parent,act});
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAllByProps({'data-action':'session.select'}).length,1);
 await renderAct(async()=>root.root.findAllByType('button').find(node=>node.children.includes('More subagents')).props.onClick());
 assert.equal(calls.at(-1).args.patch.subagentHistory.index,1);
 await renderAct(async()=>root.update(render()));
 assert.match(JSON.stringify(root.toJSON()),/Loading subagent conversations/);
 assert.equal(root.root.findAllByProps({'data-action':'session.select'}).length,0);
 state.subagentNavigation={...state.subagentNavigation,items:[{id:'next-worker',title:'Next worker'}],index:1,start:50,end:100,scope:{sessionId:'root',filter:'',index:1}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByProps({'data-action':'session.select'}).props.title,'next-worker');
 await renderAct(async()=>root.unmount());
});
