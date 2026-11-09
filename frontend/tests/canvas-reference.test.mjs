import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Markdown} from '../src/markdown.js';
import {sourceLineLookup} from '../src/canvas-reference.js';

const render=(text,mapSource=true)=>renderToStaticMarkup(React.createElement(Markdown,{text,mapSource}));

test('rendered Markdown maps repeated phrases and formatting to exact source offsets',()=>{
 const source='# Plan\n\nFirst **repeated** &amp; 😀 `code`.\n\nFirst **repeated**.';
 const html=render(source),first=source.indexOf('repeated'),second=source.lastIndexOf('repeated');
 for(const start of [first,second])assert.ok(html.includes(`data-canvas-source-start="${start}" data-canvas-source-end="${start+8}"`));
 assert.match(html,/<strong><span[^>]+>repeated<\/span><\/strong>/);
 assert.match(html,/data-canvas-literal="true">code/);
 assert.ok(!render(source,false).includes('data-canvas-source'));
});

test('writing blocks retain whole-document source offsets without mapping controls',()=>{
 const source='Before\n\n:::writing{variant="document" id="12345"}\n**Body**\n:::writing-looking text\n:::\n\nAfter';
 const html=render(source),at=source.indexOf('Body');
 assert.ok(html.includes(`data-canvas-source-start="${at}" data-canvas-source-end="${at+4}"`));
 assert.ok(html.includes('Copy writing'));
 assert.ok(!html.includes('data-canvas-source-start="undefined"'));
 const end=source.indexOf('After');assert.ok(html.includes(`data-canvas-source-start="${end}"`));
});

test('source mapping retains safe rendering and leaves remote images inert',()=>{
 const html=render('[link](javascript:alert)\n\n![diagram](https://example.com/a.png)\n\n<script>unsafe()</script>');
 assert.ok(!html.includes('<script'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('href="javascript:'));
 assert.ok(html.includes('Image: diagram'));
});


test('code source locations exclude fence language and delimiter matches',()=>{
 const source='```python\npython\n```\n\n`` ` ``';
 const html=render(source),payload=source.indexOf('python',4),tick=source.lastIndexOf('`',source.length-4);
 assert.ok(html.includes(`data-canvas-source-start="${payload}"`));
 assert.ok(html.includes(`data-canvas-source-start="${tick}"`));
});

test('diagram fence identity uses whole-document UTF-16 offsets across writing blocks and CRLF',()=>{
 const source='😀 Before\r\n\r\n> ```mermaid\r\n> flowchart LR\r\n> A-->B\r\n> ```\r\n\r\n:::writing{variant="document" id="12345"}\n```dot\ndigraph {a->b}\n```\n:::\n';
 const html=render(source);
 for(const language of ['mermaid','dot']){
  const offset=source.indexOf('```'+language);
  assert.ok(html.includes(`data-canvas-fence-offset="${offset}"`),`${language} must identify its original fence`);
 }
 assert.ok(!render(source,false).includes('data-canvas-fence-offset'));
});

test('line lookup scans once and handles CRLF and surrogate pairs at repeated lookups',()=>{
 const source='😀 first\r\nsecond\rthird\nlast',lineAt=sourceLineLookup(source);
 for(let i=0;i<1000;i++){
  assert.equal(lineAt(source.indexOf('second')),2);
  assert.equal(lineAt(source.indexOf('third')),3);
  assert.equal(lineAt(source.indexOf('last')),4);
 }
 assert.equal(lineAt(0),1);
});
