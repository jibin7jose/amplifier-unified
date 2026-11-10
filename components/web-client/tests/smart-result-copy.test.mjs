// Manager-only component checks. Compile the actual owned ToolResult, not a
// duplicate preview formula, without initializing settings or any connector.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {transformSync} from 'esbuild';
import {BlockCopy} from '../src/block-copy.js';

const source=readFileSync(new URL('../src/smart-tools.jsx',import.meta.url),'utf8');
const component=source.slice(source.indexOf('function ToolResult('),source.indexOf('\nfunction parseEnvironment('));
assert.ok(component.startsWith('function ToolResult('));
const dependencies=['working','pretty'].map(name=>source.match(new RegExp(`^const ${name} = .*;`,'m'))[0]).join('\n');
const compiled=transformSync(dependencies+'\n'+component,{loader:'jsx',format:'cjs'}).code;
const ToolResult=new Function('React','BlockCopy',compiled+'\nreturn ToolResult;')(React,BlockCopy);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

test('copy uses only the exact displayed bounded string and never reads a full resource',async()=>{
 const originalNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator'),originalFetch=globalThis.fetch;
 const copies=[],reads=[];
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:text=>{copies.push(text);return Promise.resolve()}}}});
 globalThis.fetch=(...args)=>{reads.push(args);throw Error('Unexpected full-resource fetch')};
 let renderer;
 try{
  const long='α😀\r\n\t'.repeat(6000);
  const cases=[
   {result:'Complete shown text\r\n\tα😀',label:'Copy shown output'},
   {result:long,label:'Copy shown output (truncated)'},
   {result:{$resource:'opaque-fixture-resource',preview:long,bytes:90000},label:'Copy shown output (truncated)'},
   {result:{$resource:'opaque-fixture-resource',summary:'Only the summary'},label:'Copy shown output (truncated)'},
   {result:{partial:true,text:'A partial response'},label:'Copy shown output (truncated)'},
  ];
  for(const [index,{result,label}] of cases.entries()){
   const operation={id:'shown-'+index,status:'completed',result};
   await act(async()=>{if(renderer)renderer.update(React.createElement(ToolResult,{operation}));else renderer=create(React.createElement(ToolResult,{operation}))});
   const pre=renderer.root.findByType('pre'),button=renderer.root.findByType('button');
   const preview=result?.$resource?(result.summary??result.preview):result;
   const expected=(typeof preview==='string'?preview:JSON.stringify(preview,null,2)).slice(0,24000);
   assert.equal(pre.props.children,expected);assert.equal(button.props['aria-label'],label);
   await act(async()=>{await button.props.onClick()});
   assert.equal(copies.at(-1),expected);assert.ok(copies.at(-1).length<=24000);
   assert.equal(renderer.root.findByProps({role:'status'}).props.children,'Copied.');
  }
  assert.deepEqual(reads,[]);
 }finally{
  if(renderer)await act(async()=>renderer.unmount());
  globalThis.fetch=originalFetch;
  if(originalNavigator)Object.defineProperty(globalThis,'navigator',originalNavigator);else delete globalThis.navigator;
 }
});
test('missing, empty and loading previews have no copy control or complete-copy claim',async()=>{
 for(const operation of [null,{status:'running',result:'not yet'},{status:'completed'},{
  status:'completed',result:{$resource:'opaque-fixture-resource',bytes:100}
 },{status:'completed',result:''}]){
  let renderer;
  try{
   await act(async()=>{renderer=create(React.createElement(ToolResult,{operation}))});
   assert.equal(renderer.root.findAllByType(BlockCopy).length,0);
   assert.equal(renderer.root.findAllByType('pre').length,0);
   assert.ok(!JSON.stringify(renderer.toJSON()).includes('full result'));
  }finally{await act(async()=>renderer.unmount())}
 }
});