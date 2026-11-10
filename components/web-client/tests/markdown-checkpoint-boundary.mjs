// Real composer JSON through the canonical draft validator, then back to the
// composer. Run only in the prepared guest; no provider or network needed.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {TextSelection} from 'prosemirror-state';
import {composerState,composerMarkdown,composerDocument,composerSendDocument,parseDocument,insertQuote,formatComposerBlock} from '../src/markdown-composer.js';
const samples=['- # Heading','- > Quotation','- [x] Task\n  - [ ] Nested','| A | B |\n| --- | ---: |\n| ~~one~~ | `two` |','[repository](ssh://example.test/repo)','![inert](data:image/png;base64,AAAA)','~~Before~~ after','hello'];
const quote={id:'source',sessionId:'synthetic',messageId:'source',speaker:'Amplifier',origin:'assistant',role:'assistant',kind:'whole',excerpt:'verbatim',spans:[{start:0,end:8}],sourceDigest:'a'.repeat(64)};
const cases=samples.map(text=>{
 const view={state:composerState(text),composing:false,focus(){}};view.dispatch=tr=>view.state=view.state.apply(tr);
 if(text==='hello'){view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,6)));formatComposerBlock(view,'markdown_source');}
 insertQuote(view,quote,{doc:view.state.doc,pos:text==='~~Before~~ after'?8:view.state.doc.content.size});
 return {text:composerMarkdown(view.state.doc),document:composerDocument(view.state.doc),send:composerSendDocument(view.state.doc),json:view.state.doc.toJSON()};
});
const code=`import json,sys
from amplifier_web.message_interactions import resolve_document
payload=json.load(sys.stdin)
session={'id':'synthetic','messageQuotes':{'source':payload['quote']}}
result=[]
for row in payload['cases']:
    draft=resolve_document(session,row['document'],draft=True)
    sent=resolve_document(session,row['send'])
    result.append({'draft':draft,'send':sent})
print(json.dumps(result,ensure_ascii=False))
`;
const result=spawnSync(process.env.AMPLIFIER_TEST_PYTHON||'python',['-c',code],{input:JSON.stringify({cases,quote}),encoding:'utf8',cwd:new URL('../../',import.meta.url),timeout:30000});
assert.equal(result.status,0,result.stderr);
const restored=JSON.parse(result.stdout);
restored.forEach((row,i)=>{assert.deepEqual(parseDocument(cases[i].text,row.draft).toJSON(),cases[i].json);assert.deepEqual(row.send,cases[i].send);});
console.log(JSON.stringify({passed:true,cases:samples.length,checks:['actual-editor-json','canonical-draft-validator','exact-editor-restore','send-projection','immutable-quote-resolution']}));
