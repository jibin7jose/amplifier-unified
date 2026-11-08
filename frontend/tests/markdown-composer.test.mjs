import {test} from 'node:test';
import assert from 'node:assert/strict';
import {composerState,composerMarkdown,parseComposer,composerNewline,composerClipboard,composerEnter,composerDocument,composerSendDocument,insertQuote,changeQuote,toggleComposerMark,emptyComposerCode} from '../src/markdown-composer.js';
import {undo,redo} from 'prosemirror-history';
import {Node} from 'prosemirror-model';
import {MarkdownSerializer} from 'prosemirror-markdown';
test('ordinary drafts do not serialize discarded structured checkpoints',t=>{
 const docs=['', 'A **small** draft', Array.from({length:300},(_,i)=>`Paragraph ${i}: **keep** this text.`).join('\n\n')].map(parseComposer);
 const json=t.mock.method(Node.prototype,'toJSON'),markdown=t.mock.method(MarkdownSerializer.prototype,'serialize');
 for(const doc of docs){
  assert.equal(composerDocument(doc),null);
  assert.equal(composerSendDocument(doc),null);
 }
 assert.equal(json.mock.callCount(),0);
 assert.equal(markdown.mock.callCount(),0);
});
test('round-trippable commentary drops its checkpoint without serializing discarded nodes',t=>{
 const doc=parseComposer('Keep **this**\n\nAnd this paragraph.');
 const json=t.mock.method(Node.prototype,'toJSON');
 assert.equal(composerDocument(doc,true),null);
 assert.equal(json.mock.callCount(),0);
});
function editor(){const view={state:composerState(''),composing:false};view.dispatch=tr=>{view.state=view.state.apply(tr)};return view;}
function type(view,text){for(const char of text){const {from,to}=view.state.selection;const handled=view.state.plugins.some(plugin=>plugin.props.handleTextInput?.(view,from,to,char,()=>view.state.tr.insertText(char)));if(!handled)view.dispatch(view.state.tr.insertText(char));}}
test('closing Markdown marks format immediately and stop the mark at the cursor',()=>{
 const view=editor();type(view,'**bold** _emphasis_ `code` plain');
 assert.equal(composerMarkdown(view.state.doc),'**bold** *emphasis* `code` plain');
 assert.equal(view.state.doc.textContent,'bold emphasis code plain');
 assert.deepEqual(view.state.doc.firstChild.lastChild.marks,[]);
});
test('headings and ordered/unordered lists use Markdown and continue through newline',()=>{
 for(const [prefix,node] of [['# ','heading'],['* ','bullet_list'],['1. ','ordered_list']]){
  const view=editor();type(view,prefix+'First');assert.equal(view.state.doc.firstChild.type.name,node);
  composerNewline(view.state,view.dispatch);type(view,'Second');
  if(node.endsWith('list'))assert.equal(view.state.doc.firstChild.childCount,2);
  assert.match(composerMarkdown(view.state.doc),/Second/);
 }
});
test('code remains literal and saved Markdown parses back to equivalent content',()=>{
 const value='## Header\n\n* One\n* Two\n\n```js\nconst raw = "**not bold**";\n```';
 const doc=parseComposer(value);assert.ok(parseComposer(composerMarkdown(doc)).eq(doc));
 const view=editor();type(view,'```js ');type(view,'**literal**');
 assert.equal(view.state.doc.firstChild.type.name,'code_block');assert.equal(view.state.doc.textContent,'**literal**');
});
test('image references stay textual and do not create an image element',()=>{
 const doc=parseComposer('![diagram](https://example.test/image.png)');
 const image=doc.firstChild.firstChild;assert.equal(image.type.name,'image');
 assert.equal(image.type.spec.toDOM(image)[0],'span');
 assert.equal(composerMarkdown(doc),'![diagram](https://example.test/image.png)');
});

test('copying rich text keeps Markdown marks for plain-text paste',()=>{
 const doc=parseComposer('Keep **this** and *that*.');
 assert.equal(composerClipboard(doc.slice(6,10)),'**this**');
});

import {TextSelection,AllSelection} from 'prosemirror-state';
import {formatComposerBlock,setComposerLink} from '../src/markdown-composer.js';
test('raw and labeled links survive paste, typing and Markdown round trips',()=>{
 for(const text of ['https://example.com/path?q=1','[Example](https://example.com/path)']){
  const doc=parseComposer(text);assert.equal(doc.firstChild.firstChild.marks[0].type.name,'link');
  assert.ok(parseComposer(composerMarkdown(doc)).eq(doc));
  const view=editor();type(view,text+' ');assert.ok(view.state.doc.firstChild.firstChild.marks.some(m=>m.type.name==='link'));
 }
 for(const text of ['[bad](javascript:alert)','[bad](data:text/html,bad)'])assert.equal(parseComposer(text).firstChild.firstChild.marks.length,0);
});
test('block formatting converts only selected list items and preserves inline marks',()=>{
 const view={state:composerState('- **one**\n- two\n- three'),focus(){}};view.dispatch=tr=>{view.state=view.state.apply(tr)};
 let pos;view.state.doc.descendants((node,p)=>{if(node.isText&&node.text==='two')pos=p});
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,pos,pos+3)));
 formatComposerBlock(view,'h2');
 assert.match(composerMarkdown(view.state.doc),/## two/);assert.match(composerMarkdown(view.state.doc),/\*\*one\*\*/);
 assert.equal(view.state.doc.lastChild.type.name,'bullet_list');
 formatComposerBlock(view,'ordered_list');assert.match(composerMarkdown(view.state.doc),/1\. two/);
 formatComposerBlock(view,'text');assert.equal(view.state.selection.$from.parent.type.name,'paragraph');
});
test('link edits are undoable, preserve labels and reject unsafe schemes',()=>{
 const view={state:composerState('label'),focus(){}};view.dispatch=tr=>{view.state=view.state.apply(tr)};
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,6)));
 assert.equal(setComposerLink(view,'javascript:alert(1)'),false);
 assert.equal(setComposerLink(view,'https://example.com'),true);assert.equal(composerMarkdown(view.state.doc),'[label](https://example.com)');
 assert.equal(setComposerLink(view,''),true);assert.equal(composerMarkdown(view.state.doc),'label');
});

test('select all converts list types without nesting',()=>{
 const view={state:composerState('1. **one**'),focus(){}};view.dispatch=tr=>{view.state=view.state.apply(tr)};view.dispatch(view.state.tr.setSelection(new AllSelection(view.state.doc)));
 formatComposerBlock(view,'bullet_list');assert.equal(view.state.doc.firstChild.firstChild.firstChild.type.name,'paragraph');assert.equal(view.state.doc.textContent,'one');
});

test('Enter opens bare/language/tilde fences and preserves indentation without sending',()=>{
 for(const opener of ['```','```js','```c++','~~~python']){
  const view=editor();type(view,opener);
  assert.equal(view.state.doc.firstChild.type.name,'paragraph');
  assert.equal(composerEnter(view),true);
  assert.equal(view.state.doc.firstChild.type.name,'code_block');
  assert.equal(view.state.doc.firstChild.attrs.params,opener.slice(3));
  type(view,'  **literal**');assert.equal(composerEnter(view),true);type(view,'next');
  assert.equal(view.state.doc.textContent,'  **literal**\n  next');
  assert.equal(view.state.doc.firstChild.firstChild.marks.length,0);
  assert.equal(composerEnter(view,{exit:true}),true);
  assert.equal(view.state.selection.$from.parent.type.name,'paragraph');
  type(view,'outside');assert.equal(composerEnter(view),false,'normal Enter remains Send outside code');
 }
});
test('fence conversion is undoable, literal backticks and IME do not trigger it',()=>{
 const view=editor();type(view,'```js');
 composerEnter(view);assert.ok(undo(view.state,view.dispatch));
 assert.equal(view.state.doc.firstChild.type.name,'paragraph');assert.equal(view.state.doc.textContent,'```js');
 assert.ok(redo(view.state,view.dispatch));assert.equal(view.state.doc.firstChild.type.name,'code_block');
 assert.ok(emptyComposerCode(view.state,view.dispatch));assert.equal(view.state.doc.firstChild.type.name,'paragraph');
 assert.ok(undo(view.state,view.dispatch));assert.equal(view.state.doc.firstChild.type.name,'code_block');
 for(const value of ['literal ```','\\```','`inline`','```bad`info']){
  const literal=editor();type(literal,value);assert.equal(composerEnter(literal),false,value);
 }
 const ime=editor();type(ime,'```');ime.composing=true;assert.equal(composerEnter(ime),false);
});

const gfmCases=[
 ['paragraphs and soft/hard breaks','First\nsecond  \nthird\n\nlast','paragraph'],
 ['ATX headings','# One\n\n###### Six','heading'],
 ['setext headings','Title\n=====','heading'],
 ['emphasis and strong','*one* **two** _three_ __four__','paragraph'],
 ['strikethrough','~~removed~~','paragraph'],
 ['multi-backtick inline code','`` a ` b ``','paragraph'],
 ['fenced language and indented code','~~~js extra\n  const x = 1;\n~~~\n\n    indented','code_block'],
 ['nested blockquote','> Outer\n>\n> > Inner','blockquote'],
 ['nested lists','1. One\n   - nested\n     - deep\n2. Two','ordered_list'],
 ['nested task lists','- [x] Done\n  - [ ] Nested\n- [ ] Next','bullet_list'],
 ['table alignment and inline formatting','| Left | Right |\n| :--- | ---: |\n| **bold** | `code` |','table'],
 ['escaped pipes in tables','| A | B |\n| --- | --- |\n| a\\|b | `c\\|d` |','table'],
 ['autolinks','www.example.com and person@example.com and <https://example.com/a>','paragraph'],
 ['links with title','[label](https://example.com "title")','paragraph'],
 ['images with literal formatted alt','![**alt**](https://example.com/image.png "image")','paragraph'],
 ['entities and escapes','&copy; &amp; \\*literal\\* \\[text\\]','paragraph'],
 ['thematic breaks','---\n\n***','horizontal_rule'],
];
for(const [name,input,kind] of gfmCases)test('GFM coverage: '+name,()=>{
 const doc=parseComposer(input);assert.equal(doc.firstChild.type.name,kind,'must be rich, not an unobserved fallback');
 assert.ok(parseComposer(composerMarkdown(doc)).eq(doc),'semantic roundtrip');
});
test('GFM structures retain task state, strike marks, alignment and table text',()=>{
 const tasks=parseComposer('- [x] Done\n- [ ] Next').firstChild;
 assert.equal(tasks.child(0).attrs.checked,true);assert.equal(tasks.child(1).attrs.checked,false);
 assert.equal(parseComposer('~~removed~~').firstChild.firstChild.marks[0].type.name,'strike');
 const table=parseComposer('| a | b |\n| :--- | ---: |\n| 1 | 2 |').firstChild;
 assert.equal(table.firstChild.child(0).attrs.align,'left');assert.equal(table.firstChild.child(1).attrs.align,'right');
 assert.equal(table.child(1).textContent,'12');
 assert.equal(parseComposer('- \\[x] literal').firstChild.firstChild.attrs.checked,null);
});
test('table continuation exits without splitting cells or losing later columns',()=>{
 const view={state:composerState('| A | B |\n| --- | --- |\n| left | right |')};view.dispatch=tr=>view.state=view.state.apply(tr);
 let pos;view.state.doc.descendants((node,p)=>{if(node.isText&&node.text==='left')pos=p+2;});
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,pos)));
 const table=view.state.doc.firstChild;
 composerEnter(view,{continueWriting:true});
 assert.ok(view.state.doc.firstChild.eq(table));assert.equal(view.state.selection.$from.parent.type.name,'paragraph');
 assert.ok(parseComposer(composerMarkdown(view.state.doc)).firstChild.eq(table));
});
test('fences inside tasks retain a leading task paragraph and valid Markdown',()=>{
 for(const boundary of ['enter','space']){
  const view=editor();type(view,'- [ ] ```js');
  if(boundary==='space')type(view,' ');else composerEnter(view);
  type(view,'code');
  const item=view.state.doc.firstChild.firstChild;
  assert.equal(item.attrs.checked,false);assert.equal(item.child(0).type.name,'paragraph');
  assert.equal(item.child(1).type.name,'code_block');
  assert.ok(parseComposer(composerMarkdown(view.state.doc)).eq(view.state.doc));
 }
 assert.equal(parseComposer('Use `<div>` and `$x$` here').firstChild.type.name,'paragraph');
});
test('typed tasks continue unchecked; strike and selected commands are undoable',()=>{
 const view=editor();view.focus=()=>{};type(view,'- [x] Done');
 assert.equal(view.state.doc.firstChild.firstChild.attrs.checked,true);
 composerNewline(view.state,view.dispatch);type(view,'Next');
 assert.equal(view.state.doc.firstChild.child(1).attrs.checked,false);
 const strike=editor();type(strike,'~~gone~~');assert.equal(strike.state.doc.firstChild.firstChild.marks[0].type.name,'strike');
 const selected={state:composerState('selected'),focus(){}};selected.dispatch=tr=>selected.state=selected.state.apply(tr);
 selected.dispatch(selected.state.tr.setSelection(TextSelection.create(selected.state.doc,1,9)));
 toggleComposerMark(selected,'code');assert.equal(composerMarkdown(selected.state.doc),'`selected`');
 assert.ok(undo(selected.state,selected.dispatch));assert.equal(composerMarkdown(selected.state.doc),'selected');
 for(const kind of ['code_block','blockquote','task_list']){
  const before=selected.state.doc;formatComposerBlock(selected,kind);
  assert.notDeepEqual(selected.state.doc.toJSON(),before.toJSON());
  assert.ok(undo(selected.state,selected.dispatch));assert.ok(selected.state.doc.eq(before),'one Undo reverses '+kind);
 }
});

const rawCases=[
 ['reference links and image definitions','Before [label][id] ![alt][id]\n\n[id]: https://example.com "Title"\n\nAfter'],
 ['footnotes','Before[^note]\n\n[^note]: footnote\n    continued\n\nAfter'],
 ['alerts','> [!NOTE]\n> Keep this **alert**\n\nAfter'],
 ['math','Before $x_1$ and $$y$$\n\nAfter'],
 ['raw HTML','<details>\n<summary>Literal</summary>\n<script>alert(1)</script>\n</details>'],
 ['CRLF and Unicode','Before\r\n\r\n[^n]: café e\u0301 東京\t\r\n\r\nAfter\r\n'],
];
for(const [name,input] of rawCases)test('lossless source: '+name,()=>{
 const view={state:composerState(input)};view.dispatch=tr=>view.state=view.state.apply(tr);
 assert.equal(view.state.doc.firstChild.type.name,'markdown_source');
 assert.equal(composerMarkdown(view.state.doc),input);
 view.dispatch(view.state.tr.insertText('Changed ',1));
 const expected='Changed '+input;
 for(let cycle=0;cycle<2;cycle++){
  view.state=composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc));
  assert.equal(composerMarkdown(view.state.doc),expected);
 }
 assert.equal(view.state.doc.firstChild.type.spec.toDOM(view.state.doc.firstChild)[0],'pre');
});
// Custom schemes are outside the rich-link checkpoint policy. Preserve the
// whole reference plus excerpt as source; do not look only for a blockquote node.
const canvasQuoteLabel='Résumé 東京';
const canvasQuoteUri='amplifier-canvas://artifact/canvas-copy-source?session=synthetic-canvas-source&version=17';
const canvasQuoteExcerpt='Café e\u0301 — 東京\nSecond line: 𐐷 remains verbatim.';
const canvasQuoteBlock=canvasQuoteExcerpt.split('\n').map(line=>'> '+line).join('\n');
const canvasQuoteSource=`[${canvasQuoteLabel}](${canvasQuoteUri})\n\n${canvasQuoteBlock}`;
function assertExactCanvasQuoteSource(doc){
 assert.deepEqual(doc.toJSON(),{type:'doc',content:[{type:'markdown_source',content:[{type:'text',text:canvasQuoteSource}]}]});
 assert.equal(composerMarkdown(doc),canvasQuoteSource);
}
test('versioned canvas reference and Unicode excerpt remain exact source through two checkpoint reloads',()=>{
 assertExactCanvasQuoteSource(parseComposer(canvasQuoteSource));
 let state=composerState(canvasQuoteSource);
 assertExactCanvasQuoteSource(state.doc);
 for(let cycle=0;cycle<2;cycle++){
  const checkpoint=JSON.parse(JSON.stringify(composerDocument(state.doc)));
  assert.deepEqual(checkpoint,{version:1,segments:[{type:'text',text:canvasQuoteSource,content:state.doc.firstChild.toJSON()}]});
  state=composerState(composerMarkdown(state.doc),checkpoint);
  assertExactCanvasQuoteSource(state.doc);
  assertExactCanvasQuoteSource(parseComposer(composerMarkdown(state.doc)));
 }
});
const httpsQuoteUri='https://example.test/artifact/canvas-copy-source?session=synthetic-canvas-source&version=17';
for(const [name,source,href] of [
 ['HTTPS reference and Unicode excerpt remain rich through two checkpoint reloads',`[${canvasQuoteLabel}](${httpsQuoteUri})\n\n${canvasQuoteBlock}`,httpsQuoteUri],
 ['plain Unicode blockquote remains rich through two checkpoint reloads',canvasQuoteBlock,null],
])test(name,()=>{
 const initial=parseComposer(source);
 let state=composerState(source);
 for(let cycle=0;cycle<=2;cycle++){
  const doc=state.doc;
  assert.ok(doc.eq(initial),'reload preserves the complete rich document');
  assert.equal(doc.childCount,href?2:1);
  if(href){
   const paragraph=doc.firstChild;
   assert.equal(paragraph.type.name,'paragraph');
   assert.equal(paragraph.childCount,1);
   assert.equal(paragraph.textContent,canvasQuoteLabel);
   assert.deepEqual(JSON.parse(JSON.stringify(paragraph.firstChild.marks.map(mark=>mark.toJSON()))),[{type:'link',attrs:{href,title:null}}]);
  }
  const quote=doc.lastChild;
  assert.equal(quote.type.name,'blockquote');
  assert.equal(quote.childCount,1);
  assert.equal(quote.firstChild.type.name,'paragraph');
  assert.equal(quote.textContent,canvasQuoteExcerpt);
  assert.equal(composerMarkdown(doc),source);
  if(cycle<2){
   const checkpoint=composerDocument(doc);
   assert.equal(checkpoint,null,'lossless rich Markdown needs no source checkpoint');
   state=composerState(composerMarkdown(doc),checkpoint);
  }
 }
});
test('exact canvas source oracle rejects missing URI, changed session/version and truncated or normalized excerpt',()=>{
 assertExactCanvasQuoteSource(parseComposer(canvasQuoteSource));
 for(const corrupted of [
  canvasQuoteBlock,
  canvasQuoteSource.replace('session=synthetic-canvas-source','session=another-session'),
  canvasQuoteSource.replace('version=17','version=1'),
  canvasQuoteSource.slice(0,-1),
  canvasQuoteSource.normalize('NFC'),
 ]){
  assert.notEqual(corrupted,canvasQuoteSource,'negative control must change the fixture');
  assert.throws(()=>assertExactCanvasQuoteSource(parseComposer(corrupted)),{code:'ERR_ASSERTION'});
 }
});
test('website notation remains source, with no account lookup or executable image',()=>{
 const source='@someone #123 :smile: https://example.com/issues/1';
 assert.equal(parseComposer(source).textContent,source);
 for(const text of ['[bad](javascript:alert)','[bad](data:text/html,bad)','[bad](vbscript:bad)']){
  const doc=parseComposer(text);let links=0;doc.descendants(node=>{links+=node.marks.filter(m=>m.type.name==='link').length});assert.equal(links,0);
 }
});
test('GFM commentary uses existing text-only wire segments around immutable quotes',()=>{
 const view={state:composerState('~~Before~~\n\n- [x] Task'),composing:false};view.dispatch=tr=>view.state=view.state.apply(tr);
 const quote={id:'source',sessionId:'synthetic',messageId:'source',speaker:'Amplifier',origin:'assistant',role:'assistant',excerpt:'verbatim',spans:[{start:0,end:8}],sourceDigest:'a'.repeat(64)};
 insertQuote(view,quote,{doc:view.state.doc,pos:view.state.doc.content.size});
 const document=composerSendDocument(view.state.doc),quoted=document.segments.find(part=>part.type==='quote');
 assert.deepEqual(quoted.quote,quote);
 assert.equal(document.segments[0].content,undefined);assert.equal(document.segments[1].content,undefined);
 for(let cycle=0;cycle<2;cycle++){
  view.state=composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc));
  assert.deepEqual(composerSendDocument(view.state.doc),document);
 }
 changeQuote(view,quoted.instanceId,0);assert.equal(composerMarkdown(view.state.doc).includes('verbatim'),false);
 assert.match(composerMarkdown(view.state.doc),/~~Before~~/);assert.match(composerMarkdown(view.state.doc),/\[x\] Task/);
});

// These two are required acceptance controls, not optional skips. They expose
// the remaining draft-checkpoint contract dependency until it is resolved.
test('source-mode checkpoint retains Enter semantics after two reloads',()=>{
 const view={state:composerState('hello'),focus(){}};view.dispatch=tr=>view.state=view.state.apply(tr);
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,6)));
 formatComposerBlock(view,'markdown_source');
 for(let cycle=0;cycle<2;cycle++){
  view.state=composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc));
  assert.equal(view.state.doc.firstChild.type.name,'markdown_source','explicit source mode must survive reload');
  assert.equal(composerEnter(view),true,'Enter must still not send');
 }
});
test('quoted struck commentary retains exact split whitespace through reload',()=>{
 const view={state:composerState('~~Before~~ after'),composing:false};view.dispatch=tr=>view.state=view.state.apply(tr);
 const quote={id:'source',sessionId:'synthetic',messageId:'source',speaker:'Amplifier',origin:'assistant',role:'assistant',excerpt:'verbatim',spans:[{start:0,end:8}],sourceDigest:'a'.repeat(64)};
 insertQuote(view,quote,{doc:view.state.doc,pos:8});
 const before=view.state.doc,document=composerDocument(before);
 view.state=composerState(composerMarkdown(before),document);
 assert.ok(view.state.doc.eq(before),'split commentary whitespace and marks must not be discarded');
});
