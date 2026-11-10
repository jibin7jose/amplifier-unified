// Manager runs this in the existing DTU: node --test tests/block-copy.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {blockPayload,copySourceBlocks,CopyControl} from '../src/block-copy.js';
import {writeClipboardText,clipboardNotice} from '../src/clipboard-copy.js';
const require=createRequire(new URL('../package.json',import.meta.url));
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {default:ReactMarkdown}=await import(require.resolve('react-markdown'));
const {default:remarkGfm}=await import(require.resolve('remark-gfm'));
const {default:Renderer,act}=await import(require.resolve('react-test-renderer'));
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

function payloads(source){
 const found=new Map();
 renderToStaticMarkup(React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm,copySourceBlocks(source,found)]},source));
 return [...found.values()];
}
const values=source=>payloads(source).map(payload=>payload.text);

test('fenced payload preserves CRLF, tabs, Unicode, blank/trailing lines and excludes neighbors',()=>{
 const raw='\t  α😀\r\n\r\n  tail\t\r\n\r\n';
 assert.deepEqual(values('Before\r\n\r\n```js\r\n'+raw+'```\r\n\r\nAfter `inline`'),[raw]);
});
test('multiple backtick/tilde fences use their own raw spans',()=>{
 assert.deepEqual(values('````md\n```literal```\n\n````\n\nNeighbor\n\n~~~txt\r\nβ\t\r\n~~~~\r\n'),['```literal```\n\n','β\t\r\n']);
});
test('unclosed and empty fences do not invent final newlines',()=>{
 assert.deepEqual(values('```text\r\none\t\r\ntwo'),['one\t\r\ntwo']);
 assert.deepEqual(values('```\n```'),['']);
 assert.deepEqual(values('```'),['']);
});
test('fence content indentation is copied raw, not the normalized AST value',()=>{
 assert.deepEqual(values('  ```txt\r\n  preserved\r\n\tindented\r\n  ```'),['  preserved\r\n\tindented\r\n']);
});
test('indented code removes only structural four columns',()=>{
 assert.deepEqual(values('Before\n\n      keep two\r\n\t\tkeep tab\r\n    α😀\r\n\r\nAfter'),['  keep two\r\n\tkeep tab\r\nα😀\r\n']);
 assert.deepEqual(values(' \tcontent\n'),['content\n']);
});
test('quotes preserve inner Markdown, lazy lines, CRLF and a nested quote source',()=>{
 assert.deepEqual(values('> **outer**\r\n> > `inner`\r\n> > next\r\n'),[
  '**outer**\r\n> `inner`\r\n> next\r\n','`inner`\r\nnext\r\n'
 ]);
 assert.deepEqual(values('> first\nlazy **line**\n'),['first\nlazy **line**\n']);
});
test('code in a quote removes container markers, not literal greater-than in code',()=>{
 const result=values('> ```txt\r\n> > literal\r\n> \tα\r\n> ```\r\n');
 assert.equal(result[1],'> literal\r\n\tα\r\n');
});
test('inline code is unchanged and never enters the block map',()=>{
 assert.deepEqual(payloads('Only `inline` with **formatting**.'),[]);
});
test('list fences copy exact bytes with marker-width, nested, tab and CRLF containers',()=>{
 const fixtures=[
  ['- Item\n\n  ```txt\n  exact\n  ```\n','exact\n'],
  ['- ```txt\r\n  \tα😀\r\n  \r\n    tail\t\r\n  \r\n  ```\r\n','\tα😀\r\n\r\n  tail\t\r\n\r\n'],
  ['10) Item\r\n\r\n    ~~~txt\r\n    \tordered\r\n    ~~~\r\n','\tordered\r\n'],
  ['1. Outer\n\n   - Inner\n\n     ```txt\n       preserve two\n     ```\n','  preserve two\n'],
  ['- Outer\n\n  12. Inner\n\n      - Deep\n\n        ```txt\n        α😀\n        ```\n','α😀\n'],
  ['1.\tOuter\r\n\r\n\t- Inner\r\n\r\n\t  ```txt\r\n\t  \tα😀\r\n\t  \r\n\t    tail\t\r\n\t  \r\n\t  ```\r\n','\tα😀\r\n\r\n  tail\t\r\n\r\n'],
  // >4 columns after the marker means one structural padding column.
  ['-     ```literal\n','```literal\n'],
 ];
 for(const [source,expected] of fixtures)assert.deepEqual(values(source),[expected],JSON.stringify(source));
 assert.ok(blockPayload('x',{type:'code'}).unavailable);
});
test('list indented code removes structural columns but keeps actual content tabs',()=>{
 assert.deepEqual(values('- Item\r\n\r\n      \tkeep tab\r\n        keep two\r\n      α😀\r\n'),['\tkeep tab\r\n  keep two\r\nα😀\r\n']);
 assert.deepEqual(values('1.\tItem\r\n\r\n\t\t\tkeep tab\r\n\t\tα😀\r\n'),['\tkeep tab\r\nα😀\r\n']);
 // The tab crossing the two-column list boundary leaves two code columns.
 assert.deepEqual(values('- Item\n\n\t  content\n'),['content\n']);
 assert.deepEqual(values('- Item\n\n\t\tcontent\n'),['  content\n']);
});
test('list/quote combinations remove only their actual ancestors and keep inner Markdown',()=>{
 const inList='- Item\r\n\r\n  > **quote**\r\n  > ```txt\r\n  > > literal\tα😀\r\n  > ```\r\n';
 assert.deepEqual(values(inList),['**quote**\r\n```txt\r\n> literal\tα😀\r\n```\r\n','> literal\tα😀\r\n']);
 const inQuote='> 1. Item\r\n>\r\n>    ```txt\r\n>    \tα😀\r\n>    ```\r\n';
 assert.deepEqual(values(inQuote),['1. Item\r\n\r\n   ```txt\r\n   \tα😀\r\n   ```\r\n','\tα😀\r\n']);
 assert.deepEqual(values('- > first\nlazy **line**\n'),['first\nlazy **line**\n']);
 assert.deepEqual(values('- > outer\n  > > **inner**\n  > > next\n'),['outer\n> **inner**\n> next\n','**inner**\nnext\n']);
});
test('tables copy Markdown source with CRLF, alignment and escaped pipes, never neighboring text',()=>{
 const table='| Left | Right |\r\n| :--- | ---: |\r\n| a\\|b | **α😀** |\r\n';
 assert.deepEqual(values('Before\r\n\r\n'+table+'\r\nAfter `inline`'),[table]);
 assert.deepEqual(values('- Item\r\n\r\n'+table.split('\r\n').filter(Boolean).map(line=>'  '+line+'\r\n').join('')),[table]);
 assert.deepEqual(values(table.split('\r\n').filter(Boolean).map(line=>'> '+line+'\r\n').join('')),[
  table,table
 ]);
});
test('source positions are not changed by the copy plugin',()=>{
 const source='> quote\n\n```\nraw\n```',map=new Map();
 const tree={type:'root',children:[{type:'code',value:'normalized',position:{start:{offset:9},end:{offset:source.length}}}]};
 const before=JSON.stringify(tree);copySourceBlocks(source,map)()(tree);
 assert.equal(JSON.stringify(tree),before);
 const nested='- Item\r\n\r\n  > | Left | Right |\r\n  > |:---|---:|\r\n  > |a\\|b|α😀|\r\n';
 let snapshot;
 const check=()=>tree=>{
  const original=JSON.stringify(tree);copySourceBlocks(nested,new Map())()(tree);
  snapshot=JSON.stringify(tree);assert.equal(snapshot,original);
 };
 renderToStaticMarkup(React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm,check]},nested));
 assert.ok(snapshot.includes('"type":"table"'));
});
test('clipboard write starts synchronously and waits for completion, with explicit denial/unsupported notices',async()=>{
 let finish,seen;const pending=new Promise(resolve=>finish=resolve);
 const completion=writeClipboardText('raw\r\n\t😀',{writeText:text=>{seen=text;return pending}});
 assert.equal(seen,'raw\r\n\t😀');assert.equal(completion,pending);
 finish();await completion;
 assert.throws(()=>writeClipboardText('raw',null),{name:'ClipboardUnavailable'});
 assert.match(clipboardNotice({name:'ClipboardUnavailable'}),/unavailable/);
 assert.match(clipboardNotice({name:'NotAllowedError'}),/denied/);
 await assert.rejects(()=>writeClipboardText('raw',{writeText:()=>Promise.reject(Object.assign(Error('denied'),{name:'NotAllowedError'}))}),{name:'NotAllowedError'});
});

test('rapid clicks write once; a late version completion cannot target or mark the new version',async()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'navigator'),calls=[],gates=[];
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:text=>{
  calls.push(text);return new Promise(resolve=>gates.push(resolve));
 }}}});
 let renderer,first,second;
 try{
  await act(async()=>{renderer=Renderer.create(React.createElement(CopyControl,{text:'version one\r\n',identity:'one'}))});
  const button=()=>renderer.root.findByType('button');
  await act(async()=>{first=button().props.onClick();second=button().props.onClick()});
  await second;assert.deepEqual(calls,['version one\r\n']);assert.equal(button().props.disabled,true);
  await act(async()=>{renderer.update(React.createElement(CopyControl,{text:'version two\t',identity:'two'}))});
  assert.equal(button().props.disabled,false);
  await act(async()=>{gates[0]();await first});
  assert.equal(renderer.root.findByProps({role:'status'}).props.children,'');
  let next;await act(async()=>{next=button().props.onClick()});
  assert.deepEqual(calls,['version one\r\n','version two\t']);
  await act(async()=>{gates[1]();await next});
  assert.equal(renderer.root.findByProps({role:'status'}).props.children,'Copied.');
 }finally{
  if(renderer)await act(async()=>renderer.unmount());
  if(previous)Object.defineProperty(globalThis,'navigator',previous);else delete globalThis.navigator;
 }
});