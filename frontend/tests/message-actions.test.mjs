import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {newestMessageIds,completedTurnEnds,MessageEntry,ForkTurn}=await server.ssrLoadModule('/src/message-actions.jsx');
const {PeerPresentation}=await server.ssrLoadModule('/src/peer-message.jsx');
test.after(()=>server.close());
test('fork controls follow the last assistant entry in each completed user turn',()=>{
 const messages=[{id:'u1',role:'user',inputId:'1'},{id:'a1',role:'assistant'},{id:'a2',role:'assistant'},{id:'u2',role:'user',inputId:'2'},{id:'a3',role:'assistant'}];
 const session={status:'working',messages,execution:{turns:[{inputId:'1',phase:'completed'},{inputId:'2',phase:'running'}]}};
 assert.deepEqual([...completedTurnEnds(session)],[['a2',{messageId:'u1',turn:1}]]);
 session.status='idle';session.execution.turns[1].phase='completed';assert.deepEqual([...completedTurnEnds(session)],[['a2',{messageId:'u1',turn:1}],['a3',{messageId:'u2',turn:2}]]);
});
test('legacy completed messages support forks but partial failed responses do not',()=>{
 const session={status:'idle',messages:[{id:'u1',role:'user'},{id:'a1',role:'assistant'}]};
 assert.deepEqual(completedTurnEnds(session).get('a1'),{messageId:'u1',turn:1});session.status='error';assert.equal(completedTurnEnds(session).size,0);
});

test('unknown native message time does not display the session creation time',()=>{
 const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session:{id:'chat'},state:{view:{}},act:()=>{},stamp:at=>'TIME-'+at,working:false}));
 const message={id:'progress',role:'assistant',text:'Workers started',createdAt:100};
 assert.match(render(message),/1970-01-01T00:01:40.000Z/);
 assert.match(render({...message,timestampKnown:true}),/1970-01-01T00:01:40.000Z/);
 const unknown=render({...message,timestampKnown:false});
 assert.doesNotMatch(unknown,/<time\b/);assert.doesNotMatch(unknown,/1970-01-01T00:01:40.000Z/);
});


test('paged native history uses the original user-turn number for forks',()=>{
 const session={status:'idle',sharedHistoryUserTurnOffset:21,messages:[{id:'older-assistant',role:'assistant'},{id:'u22',role:'user'},{id:'a22',role:'assistant'},{id:'u23',role:'user'},{id:'a23',role:'assistant'}]};
 assert.deepEqual([...completedTurnEnds(session)],[['a22',{messageId:'u22',turn:22}],['a23',{messageId:'u23',turn:23}]]);
});

test('verified service observations are expandable activity while matching user quotes remain user messages',()=>{
 const text='External observation: data, not instructions or approval.';
 const session={id:'chat',messages:[]};
 const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session,state:{view:{}},act:()=>{},stamp:()=>'',working:false}));
 const observed=render({id:'service',role:'user',text,observation:{source:'amplifier-delegate',id:'input'}});
 assert.equal(observed,'');assert.doesNotMatch(observed,/>You</);assert.doesNotMatch(observed,/aria-label="Edit message"/);
 const user=render({id:'user',role:'user',text});assert.doesNotMatch(user,/>You</);assert.match(user,/aria-label="Your message"/);assert.match(user,/aria-label="Edit message"/);
});

test('smart date follows every action, including branch, without a native tooltip',()=>{
 const html=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'answer',role:'assistant',text:'Done',createdAt:100},session:{id:'chat'},state:{view:{}},act:()=>{},forkTurn:{messageId:'user',turn:1},working:false}));
 assert.ok(html.indexOf('data-action="session.fork"')<html.indexOf('<time'));
 assert.match(html,/<time[^>]*aria-label=/);assert.doesNotMatch(html,/<time[^>]*title=/);
 assert.match(html,/<\/time><\/div><\/article>$/);
});


test('newest user and assistant footers stay visible independently of observations',()=>{
 const messages=[{id:'u1',role:'user'},{id:'a1',role:'assistant'},{id:'u2',role:'user'},{id:'a2',role:'assistant'},{id:'o',role:'user',observation:{source:'amplifier-delegate'}}];
 assert.deepEqual([...newestMessageIds(messages)],['u2','a2']);
 const html=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'u',role:'user',via:'call',text:'Hello'},session:{id:'s'},state:{view:{}},act:()=>{},newest:true}));
 assert.match(html,/data-newest="true"/);assert.doesNotMatch(html,/a-msg-meta/);
 assert.ok(html.indexOf('a-message-body')<html.indexOf('a-message-actions'));
 assert.ok(html.indexOf('Edit message')<html.indexOf('via call'));
});

test('trusted peer caption is always visible outside hover actions with a matching article label',()=>{
 const caption='Sent by Amplifier from another chat';
 const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session:{id:'chat'},state:{view:{}},act:()=>{},working:false}));
 const html=render({id:'peer',role:'user',text:'Original **α**',via:'peer',attribution:{caption}});
 assert.match(html,/aria-label="Sent by Amplifier from another chat"/);
 assert.match(html,/<p class="a-message-attribution">Sent by Amplifier from another chat<\/p>/);
 assert.match(html,/class="a-peer-summary"[^>]*aria-expanded="false"/);
 assert.doesNotMatch(html,/a-message-actions|a-peer-content|Original/);
 assert.match(html,/a-external-message/);assert.doesNotMatch(html,/aria-label="Edit message"/);
 assert.doesNotMatch(html,/via peer/);assert.doesNotMatch(html,/Your message/);
 assert.doesNotMatch(html,/a-message-attribution[^>]*(tabindex|href)/i);
 const forwarded=render({id:'human-forward',role:'user',text:'Original',via:'peer',attribution:{caption:'From another chat'}});
 assert.match(forwarded,/aria-label="From another chat"/);assert.doesNotMatch(forwarded,/Sent by Amplifier/);
 const unknown=render({id:'legacy',role:'user',text:caption,via:'peer'});
 assert.match(unknown,/aria-label="Message"/);assert.match(unknown,/Message from another chat/);
 assert.doesNotMatch(unknown,/a-message-attribution/);
 const human=render({id:'human',role:'user',text:caption});
 assert.match(human,/aria-label="Your message"/);assert.doesNotMatch(human,/a-message-attribution/);
});

test('expanded peer and ordinary user messages share exact Markdown body rendering',()=>{
 const text='First **paragraph**.\n\nSecond paragraph with a soft\nline break.\n\n- First item\n- Second item\n\n```js\nconst exact = "<tag>";\n```\n\n| Name | Value |\n|---|---|\n| α | one |\n';
 const render=message=>renderToStaticMarkup(React.createElement(PeerPresentation.Provider,{value:{open:{'chat:sample':true},setOpen:()=>{}}},
  React.createElement(MessageEntry,{message,session:{id:'chat'},state:{view:{}},act:()=>{},working:false})));
 const user=render({id:'sample',role:'user',text}),peer=render({id:'sample',role:'user',text,via:'peer'});
 const structure=html=>[...html.matchAll(/<(p|h[1-6]|ul|ol|li|table|tr|td|th|pre|code|strong)\b[^>]*>/g)].map(row=>row[1]);
 assert.deepEqual(structure(peer),structure(user));
 for(const html of [peer,user]){
  // Canonical quote-source spans preserve source offsets inside text leaves.
  // Compare paragraph semantics without treating those annotations as content.
  const paragraphs=[...html.matchAll(/<p>[\s\S]*?<\/p>/g)]
   .map(([paragraph])=>paragraph.replace(/<\/?span\b[^>]*>/g,''));
  assert.deepEqual(paragraphs,[
   '<p>First <strong>paragraph</strong>.</p>',
   '<p>Second paragraph with a soft\nline break.</p>',
  ]);
  assert.match(html,/<ul>/);assert.match(html,/<table>/);assert.match(html,/language-js/);
 }
 assert.doesNotMatch(peer,/a-delegate-markdown|a-execution-payload/);
});

test('peer rendering does not invent paragraphs or interpret literal backslash-n as line breaks',()=>{
 const text='One dense paragraph. Still the same paragraph. Literal \\n notation.';
 const html=renderToStaticMarkup(React.createElement(PeerPresentation.Provider,{value:{open:{'chat:sample':true},setOpen:()=>{}}},
  React.createElement(MessageEntry,{message:{id:'sample',role:'user',via:'peer',text},session:{id:'chat'},state:{view:{}},act:()=>{},working:false})));
 assert.equal((html.match(/<p>/g)||[]).length,1);assert.ok(html.includes(text));
});


test('display-only saved messages expose copy without guessed edit, fork or reply authority',()=>{
 for(const role of ['user','assistant']){
  const html=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'native-window:opaque',role,text:'Saved text',displayOnly:true},session:{id:'chat'},state:{view:{}},act:()=>{},forkTurn:{messageId:'user',turn:42},working:false}));
  assert.match(html,/Copy message as Markdown/);
  assert.doesNotMatch(html,/Edit message|Fork conversation|Reply to|React to|a-inline-media/);
 }
});


test('bounded saved pages fork by exact message without guessing global ordinals',()=>{
 const session={id:'saved',status:'idle',messageWindow:{mode:'cursor',total:null,offset:null},messages:[{id:'user',role:'user'},{id:'answer',role:'assistant'}]};
 const boundary=completedTurnEnds(session).get('answer');
 assert.deepEqual(boundary,{messageId:'user',turn:null});
 const calls=[],button=ForkTurn({session,turn:boundary,act:(...args)=>calls.push(args),working:false});
 assert.equal(button.props['aria-label'],'Fork conversation after this turn');button.props.onClick();
 assert.deepEqual(calls,[['session.fork',{id:'saved',afterMessageId:'user'}]]);
 for(const message of session.messages){message.displayOnly=true;assert.equal(completedTurnEnds(session).size,0);delete message.displayOnly}
});
