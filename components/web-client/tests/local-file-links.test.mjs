import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {Markdown} from '../src/markdown.js';
import {localFilePath,LocalFileLink} from '../src/local-file-links.js';

globalThis.IS_REACT_ACT_ENVIRONMENT=true;

const context={sessionId:'original',workspace:'/project',act:()=>{throw Error('Rendering must not perform actions')}};
test('file references decode paths and strip location suffixes without accepting remote URLs',()=>{
 for(const [value,path] of [['docs/plan.md','docs/plan.md'],['./Plan%20one.md','./Plan one.md'],['/project/run.py:42:3','/project/run.py'],['file:///project/a.md','/project/a.md'],['run.py:42','run.py']])assert.equal(localFilePath(value),path);
 for(const value of ['https://a/b.md','javascript:bad.md','//server/a.md','file://server/a.md','#section','www.example.com','data:text/a.md','a%00.md','command','mailto:a@b.md'])assert.equal(localFilePath(value),null,value);
});
test('chat links, inline-code paths and bare paths become explicit Canvas buttons only',()=>{
 const html=renderToStaticMarkup(React.createElement(Markdown,{fileContext:context,text:'[Plan](docs/plan.md) and `src/run.py:42` plus docs/notes.md.\n\n[Web](https://example.com/a.md) [Section](#here)\n\n```sh\ncat docs/secret.md\n```'}));
 assert.equal((html.match(/data-action="canvas.openFile"/g)||[]).length,3);
 assert.match(html,/href="https:\/\/example.com\/a.md"/);
 assert.match(html,/href="#here"/);
 assert.match(html,/<pre><code class="language-sh">cat docs\/secret.md/);
 assert.doesNotMatch(html,/href="docs\/plan.md"/);
});
test('standalone Markdown and URL-looking inline code do not acquire file actions',()=>{
 const plain=renderToStaticMarkup(React.createElement(Markdown,{text:'[Plan](docs/plan.md) and `src/run.py`'}));
 assert.doesNotMatch(plain,/canvas.openFile/);
 const chat=renderToStaticMarkup(React.createElement(Markdown,{fileContext:context,text:'`https://example.com/a.py` and [bad](javascript:bad.md)'}));
 assert.doesNotMatch(chat,/canvas.openFile|href="javascript/);
 const incomplete=renderToStaticMarkup(React.createElement(Markdown,{fileContext:{sessionId:'chat'},text:'[File](file:///project/a.md)'}));
 assert.doesNotMatch(incomplete,/file:\/\/\/|canvas.openFile/);
});

test('click binds the original chat and workspace, blocks duplicate submission, and reports unavailable locally',async()=>{
 const calls=[];let resolve,root,operation;
 const act=(name,args)=>{calls.push({name,args});return new Promise(done=>{resolve=done})};
 await renderAct(async()=>{root=create(React.createElement(LocalFileLink,{path:'docs/plan.md',context:{...context,act}},'Plan'))});
 assert.deepEqual(calls,[]);
 const click=root.root.findByType('button').props.onClick;
 await renderAct(async()=>{operation=click();click()});
 assert.deepEqual(calls,[{name:'canvas.openFile',args:{sessionId:'original',workspace:'/project',path:'docs/plan.md'}}]);
 assert.equal(root.root.findByType('button').props.disabled,true);
 await renderAct(async()=>{resolve({accepted:true,result:{status:'unavailable',message:'File unavailable'}});await operation});
 assert.equal(root.root.findByProps({role:'status'}).children.join(''),' File unavailable');
 assert.equal(root.root.findByType('button').props.disabled,false);
 await renderAct(async()=>{operation=root.root.findByType('button').props.onClick()});
 await renderAct(async()=>{resolve({accepted:true,result:{status:'opened'}});await operation});
 assert.equal(root.root.findAllByProps({role:'status'}).length,0);
 await renderAct(async()=>root.unmount());
});

test('action failure is confined to the clicked reference',async()=>{
 let root;
 await renderAct(async()=>{root=create(React.createElement(LocalFileLink,{path:'docs/plan.md',context:{...context,act:async()=>{throw Error('internal detail')}}},'Plan'))});
 await renderAct(async()=>root.root.findByType('button').props.onClick());
 assert.match(JSON.stringify(root.toJSON()),/This file could not be opened/);
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/internal detail/);
 await renderAct(async()=>root.unmount());
});

test('a state refresh preserves the clicked reference pending state and unavailable notice',async()=>{
 let root,resolve,operation;
 const act=()=>new Promise(done=>{resolve=done});
 const render=()=>React.createElement(Markdown,{text:'[Plan](docs/plan.md)',fileContext:{...context,act}});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>{operation=root.root.findByType('button').props.onClick()});
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('button').props.disabled,true);
 await renderAct(async()=>{resolve({result:{status:'unavailable',message:'File unavailable'}});await operation});
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByProps({role:'status'}).children.join(''),' File unavailable');
 await renderAct(async()=>root.unmount());
});
