import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TextSelection,NodeSelection} from 'prosemirror-state';
import {undo,redo} from 'prosemirror-history';
import {composerState,composerDocument,composerMarkdown,insertQuote,changeQuote,moveComposerBoundary} from '../src/markdown-composer.js';
const quote=id=>({id,sessionId:'synthetic',messageId:id,speaker:'Amplifier',origin:'assistant',role:'assistant',kind:'passage',sourceDigest:'a'.repeat(64),spans:[{start:1,end:30}],excerpt:'  café e\u0301 東京\n\n• one\n    code\tvalue\n',truncated:false});
function editor(text){const view={state:composerState(text),composing:false};view.dispatch=tr=>view.state=view.state.apply(tr);return view;}
const at=view=>({doc:view.state.doc,pos:view.state.selection.to});
const texts=view=>(composerDocument(view.state.doc)?.segments||[]).filter(x=>x.type==='text').map(x=>x.text);
const quotes=view=>composerDocument(view.state.doc).segments.filter(x=>x.type==='quote');
test('three references accumulate; quote-only swaps/removal retain every commentary segment',()=>{
 const view=editor('Before\n\nBetween\n\nAfter');
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 insertQuote(view,quote('second'),{doc:view.state.doc,pos:view.state.doc.content.size});
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:view.state.doc.content.size});
 const original=quotes(view),commentary=texts(view);assert.equal(original.length,3);assert.equal(new Set(original.map(x=>x.instanceId)).size,3);
 changeQuote(view,original[1].instanceId,-1);
 assert.deepEqual(quotes(view).map(x=>x.quote.id),['second','first','first']);assert.deepEqual(texts(view),commentary);
 changeQuote(view,original[0].instanceId,0);
 assert.deepEqual(quotes(view).map(x=>x.instanceId),[original[1].instanceId,original[2].instanceId]);assert.deepEqual(texts(view),commentary,'quote removal preserves every commentary segment');
 assert.equal(quotes(view)[0].quote.excerpt,quote('second').excerpt);
 const restored=editor('');restored.state=composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc));
 assert.deepEqual(composerDocument(restored.state.doc),composerDocument(view.state.doc));
});
test('held insertion honors captured caret; changed draft and IME reject without mutation',()=>{
 const view=editor('Before and after'),captured={doc:view.state.doc,pos:7};
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1)));
 insertQuote(view,quote('first'),captured);
 const segments=composerDocument(view.state.doc).segments;assert.equal(segments[0].text,'Before');assert.equal(segments[1].type,'quote');assert.equal(segments[2].text,' and after');
 const old=at(view);view.dispatch(view.state.tr.insertText('new',1));const before=view.state.doc;
 assert.throws(()=>insertQuote(view,quote('second'),old),/draft changed/);assert.equal(view.state.doc,before);
 view.composing=true;assert.throws(()=>insertQuote(view,quote('second'),at(view)),/draft changed/);assert.equal(view.state.doc,before);
});
test('selected commentary is not replaced by adding a reference',()=>{
 const view=editor('Retain these words');view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,7)));
 insertQuote(view,quote('source'),at(view));
 assert.equal(texts(view).join(''),'Retain these words');
});
test('typing-after-quote preserves every inserted quote, including adjacent additions',()=>{
 const view=editor('Before');
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 assert.ok(view.state.selection instanceof TextSelection);
 view.dispatch(view.state.tr.insertText('X'));
 assert.equal(quotes(view).length,1,'typing-after-quote must preserve the inserted quote');
 assert.equal(texts(view).join(''),'BeforeX');
 let pos;view.state.doc.forEach((node,index)=>{if(node.type.name==='reference')pos=index;});
 view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc,pos)));
 insertQuote(view,quote('second'),at(view));
 view.dispatch(view.state.tr.insertText('Y'));
 assert.deepEqual(quotes(view).map(x=>x.quote.id),['first','second']);
 assert.equal(texts(view).join(''),'BeforeYX');
});
test('reload preserves exact commentary at split boundaries and repeated save cycles',()=>{
 const view=editor('Hello world\n\n**café** e\u0301 東京\n\n```py\n  code\tvalue\n```');
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 const original=composerDocument(view.state.doc);
 assert.equal(original.segments[0].text,'Hello ');
 for(let cycle=0;cycle<2;cycle++){
  view.state=composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc));
  assert.deepEqual(composerDocument(view.state.doc),original,'reload must preserve exact commentary at split boundary');
 }
});
test('explicit quote removal is undoable without changing commentary',()=>{
 const view=editor('Before\n\nAfter');insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 const original=composerDocument(view.state.doc),id=quotes(view)[0].instanceId;
 changeQuote(view,id,0);assert.equal(composerDocument(view.state.doc),null);
 assert.ok(undo(view.state,view.dispatch));assert.deepEqual(composerDocument(view.state.doc),original);
 assert.ok(redo(view.state,view.dispatch));assert.equal(composerDocument(view.state.doc),null);
});
test('plain drafts stay plain while the last removed quote retains exact commentary',()=>{
 const plain=editor('Hello');plain.dispatch(plain.state.tr.insertText(' ',6));
 assert.equal(composerDocument(plain.state.doc),null,'ordinary drafts must not become attributed documents');
 const view=editor('Hello world');insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 const original=texts(view);changeQuote(view,quotes(view)[0].instanceId,0);
 const checkpoint=composerDocument(view.state.doc,true);
 assert.deepEqual(checkpoint.segments.map(part=>part.text),original);
 const restored=composerState(composerMarkdown(view.state.doc),checkpoint);
 assert.deepEqual(composerDocument(restored.doc,true),checkpoint);
});
test('document boundary navigation leaves quote islands for editable text, preserving shift and IME',()=>{
 const view=editor('Before');let focused=0;view.focus=()=>focused++;
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 assert.ok(moveComposerBoundary(view,{key:'End',ctrlKey:true}));
 view.dispatch(view.state.tr.insertText('Between the passages'));
 assert.deepEqual(texts(view),['Before','Between the passages']);
 assert.equal(focused,1);
 assert.ok(moveComposerBoundary(view,{key:'Home',metaKey:true,shiftKey:true}));
 assert.equal(view.state.selection.from,1);assert.equal(view.state.selection.empty,false);
 const selection=view.state.selection;
 assert.equal(moveComposerBoundary(view,{key:'End',ctrlKey:true,isComposing:true}),false);
 assert.equal(view.state.selection,selection);
});
