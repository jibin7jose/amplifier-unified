import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Markdown} from '../src/markdown.js';
const render=text=>renderToStaticMarkup(React.createElement(Markdown,{text}));
test('assistant and worker Markdown renders headings, code, lists, and GFM tables',()=>{
 const html=render('# Summary\n\n**Ready** with `code`.\n\n- One\n- Two\n\n| Task | Status |\n|---|---|\n| Build | Done |\n\n```python\nprint("hello")\n```');
 for(const fragment of ['<h1>Summary</h1>','<strong>Ready</strong>','<ul>','<table>','<pre>','language-python'])assert.ok(html.includes(fragment),fragment);
});
test('Markdown blocks raw HTML and unsafe URLs, and secures new-window links',()=>{
 const html=render('<script>alert(1)</script>\n\n[Bad](javascript:alert)\n\n[Good](https://example.com)\n\n<img src=x onerror=alert(1)>');
 assert.ok(!html.includes('<script'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('href="javascript:'));
 assert.match(html,/href="https:\/\/example.com"[^>]*target="_blank"[^>]*rel="noopener noreferrer"/);
});
test('images require an explicit click instead of fetching remote assets',()=>{
 const html=render('![A diagram](https://example.com/diagram.png)');
 assert.ok(!html.includes('<img'));assert.ok(html.includes('Image: A diagram'));assert.ok(html.includes('href="https://example.com/diagram.png"'));
});

test('reusable writing retains safe Markdown and inert metadata',()=>{
 const html=render(':::writing{variant="document" id="12345"}\n**A draft**\n\n<script>alert(1)</script>\n:::');
 assert.ok(html.includes('Reusable writing'));assert.ok(html.includes('<strong>A draft</strong>'));assert.ok(html.includes('Copy writing'));assert.ok(!html.includes('<script>'));
});

test('user Markdown keeps literal HTML and writing notation as inert text',()=>{
 const html=renderToStaticMarkup(React.createElement(Markdown,{userContent:true,text:'**Hello** <script>alert(1)</script>\n\n:::writing{variant="document" id="12345"}\nDraft\n:::'}));
 assert.ok(html.includes('<strong>Hello</strong>'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script'));assert.ok(!html.includes('Copy writing'));assert.ok(html.includes(':::writing'));
});

test('block copy controls are labelled once per code/quote, never inline text',()=>{
 const html=render('Ordinary `inline` paragraph.\n\n```js\nconst x = 1;\n```\n\n    indented\n\n> **Quote**\n');
 assert.equal((html.match(/aria-label="Copy code block"/g)||[]).length,2);
 assert.equal((html.match(/aria-label="Copy quote block"/g)||[]).length,1);
 assert.ok(html.includes('<code>inline</code>'));
 assert.ok(html.includes('aria-live="polite"'));
 assert.ok(!html.includes('data-copy-text'));
 assert.equal((render('Only `inline` text.').match(/data-copy-block/g)||[]).length,0);
});

test('Canvas pre overrides retain one shared copy control and safe source spans',()=>{
 const html=renderToStaticMarkup(React.createElement(Markdown,{text:'Before **bold**.\n\n```dot\ndigraph { a -> b }\n```',mapSource:true,
  overrides:{pre:({children})=>React.createElement('div',{className:'custom-preview'},children)}}));
 assert.equal((html.match(/aria-label="Copy code block"/g)||[]).length,1);
 assert.ok(html.includes('custom-preview'));
 assert.ok(html.includes('data-canvas-source-start'));
});

test('writing keeps its own toolbar without adding a second whole-writing copy',()=>{
 const html=render(':::writing{variant="document" id="12345"}\nA reusable paragraph.\n\n```txt\nAn inner block\n```\n:::');
 assert.equal((html.match(/>Copy writing</g)||[]).length,1);
 assert.equal((html.match(/aria-label="Copy code block"/g)||[]).length,1);
 assert.equal((html.match(/aria-label="Copy quote block"/g)||[]).length,0);
});

test('list-contained blocks and tables each have one enabled, explicitly scoped control',()=>{
 const html=render('- Item\n\n  ```txt\n  content\n  ```\n\n  > **quoted**\n\n  | Left | Right |\n  |:---|---:|\n  |a\\|b|α😀|\n\nNeighbor `inline`.');
 for(const label of ['Copy code block','Copy quote block','Copy Markdown source']){
  assert.equal((html.match(new RegExp(`aria-label="${label}"`,'g'))||[]).length,1);
  assert.ok(!html.includes(`disabled="" aria-label="${label}"`));
 }
 assert.ok(html.includes('a-table-scroll'));assert.ok(html.includes('<code>inline</code>'));
 assert.ok(!html.includes('Exact block copy inside lists is not available.'));
});
test('table copy wrappers preserve Canvas text source spans including writing offsets',()=>{
 const table='| Left | Right |\r\n| :--- | ---: |\r\n| a\\|b | **α😀** |\r\n';
 const before=':::writing{variant="document" id="12345"}\nA draft\n:::\n\n';
 const text=before+table;
 const html=renderToStaticMarkup(React.createElement(Markdown,{text,mapSource:true}));
 const start=text.indexOf('α😀'),end=start+'α😀'.length;
 assert.ok(html.includes(`data-canvas-source-start="${start}" data-canvas-source-end="${end}"`));
 assert.equal((html.match(/aria-label="Copy Markdown source"/g)||[]).length,1);
 assert.ok(html.includes('a-table-scroll'));
});
test('custom table renderer retains exactly one raw-source control',()=>{
 const html=renderToStaticMarkup(React.createElement(Markdown,{text:'| One |\n|---|\n|two|',overrides:{
  table:({children})=>React.createElement('table',{'data-custom-table':true},children)
 }}));
 assert.equal((html.match(/aria-label="Copy Markdown source"/g)||[]).length,1);
 assert.ok(html.includes('data-custom-table'));
});
