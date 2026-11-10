import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {sourceSpans} from '../src/canvas-reference.js';
import {quoteSourceSpans,quoteSpans} from '../src/message-quote-selection.js';
const mapped=source=>{
 const map={source,leaves:[],flow:[]};
 const html=renderToStaticMarkup(React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm,sourceSpans(source),quoteSourceSpans(source,0,map)]},source));
 return {map,html};
};
const all=map=>map.leaves.map((leaf,index)=>({leaf:index,from:0,to:leaf.value.length}));
const excerpt=result=>result.spans.map(s=>[...result.source].slice(s.start,s.end).join('')).join('');
test('quote UI has no passage picker, footer or fallback entry point',()=>{
 for(const name of ['message-interactions.jsx','message-interactions.css','message-quote-selection.js']){
  const source=readFileSync(new URL('../src/'+name,import.meta.url),'utf8');
  assert.doesNotMatch(source,/Choose passage|PassagePicker|choosePassage|a-quote-picker|a-quote-fallback|Message source for quotation/,name);
 }
});
test('formatting and paragraph separators quote exact raw spans, not the enclosing syntax',()=>{
 const {map}=mapped('**one**\n\n*two*');
 const result=quoteSpans(map,all(map));
 assert.deepEqual(result.spans,[{start:2,end:5},{start:7,end:9},{start:10,end:13}]);
 assert.equal(excerpt(result),'one\n\ntwo');
});
test('links omit destinations and title whitespace, and nested formatting keeps selected words',()=>{
 const {map,html}=mapped('A **bold _inside_** and [label](https://example.test "hidden title") end');
 assert.equal(excerpt(quoteSpans(map,all(map))),'A bold inside and label end');
 assert.match(html,/data-message-quote-leaf/);assert.match(html,/data-canvas-source-start/);
 assert.equal((html.match(/<strong>/g)||[]).length,1);
});
test('list item boundaries retain exact source newlines without fabricating counters',()=>{
 const {map}=mapped('* **first**\n* second\n\n1. third\n2. fourth');
 assert.equal(excerpt(quoteSpans(map,all(map))),'first\nsecond\n\nthird\nfourth');
});
test('repeated phrases use actual occurrence offsets and preserve Unicode and selected spaces',()=>{
 const source='same **same** café 😀 e\u0301 東京  ',{map}=mapped(source);
 const result=quoteSpans(map,[{leaf:1,from:0,to:4},{leaf:2,from:0,to:map.leaves[2].value.length}]);
 assert.equal(result.spans[0].start,7);assert.equal(excerpt(result),'same café 😀 e\u0301 東京');
 // CommonMark removes trailing spaces at the paragraph boundary: it is not
 // permissible to broaden the rendered selection to those invisible characters.
 const direct={source:'  😀  ',leaves:[{start:0,end:6,value:'  😀  '}],flow:[{leaf:0}]};
 assert.equal(excerpt(quoteSpans(direct,[{leaf:0,from:0,to:6}])),'  😀  ');
 assert.match(quoteSpans(direct,[{leaf:0,from:3,to:4}]).error,/complete Unicode/);
});
test('literal code maps only when the rendered payload and final newline exist in source',()=>{
 const {map}=mapped('`inline`\n\n```py\n  code\tvalue\n```');
 assert.equal(excerpt(quoteSpans(map,all(map))),'inline\n\n  code\tvalue\n');
 const normalized=mapped('`a\nb`');assert.ok(normalized.map.flow.some(row=>row.barrier));
});
test('unsupported entities, controls, hard breaks and tables remain barriers, not partial quotes',()=>{
 for(const source of ['first **&amp;** last','first ![image](https://example.test) last','first  \nlast','first\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nlast']){
  const {map}=mapped(source),result=quoteSpans(map,all(map));
  assert.ok(result?.error||map.leaves.length===0,source);
 }
});
test('missing selected leaves and oversized passages cannot silently omit or shorten text',()=>{
 const {map}=mapped('one **two** three');
 assert.ok(quoteSpans(map,[{leaf:0,from:0,to:4},{leaf:2,from:0,to:6}]).error);
 const long=mapped('x'.repeat(12001));assert.match(quoteSpans(long.map,all(long.map)).error,/12,000/);
});
