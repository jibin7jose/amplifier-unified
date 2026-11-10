import test from 'node:test';
import assert from 'node:assert/strict';
import {conversationHistoryPending} from '../src/history-window.js';

test('ready cursor pages render without hydrating canonical history',()=>{
 const session={historyLoaded:false,historyLoading:false,messageWindow:{mode:'cursor'},messages:[{id:'saved'}]};
 assert.equal(conversationHistoryPending(session),false);
 assert.equal(conversationHistoryPending({...session,messages:[]}),false);
 assert.equal(session.historyLoaded,false);
 assert.equal(conversationHistoryPending({...session,messageWindow:{mode:'focus',status:'ready'}}),false);
 assert.equal(conversationHistoryPending(session,true),true);
});

test('cursor loading and failure are distinct from canonical hydration',()=>{
 const session={historyLoaded:false,historyLoading:true,messageWindow:{mode:'cursor'},messages:[]};
 assert.equal(conversationHistoryPending(session),true);
 assert.equal(conversationHistoryPending({...session,historyLoading:false,historyError:'Read failed'}),false);
 assert.equal(conversationHistoryPending({...session,messages:[{id:'retained'}]}),false);
 assert.equal(conversationHistoryPending({historyLoaded:false,messages:[]}),true);
 assert.equal(conversationHistoryPending({historyLoaded:false,historyLoading:false,historyError:'Read failed',messages:[]}),false);
 assert.equal(conversationHistoryPending(undefined),false);
 assert.equal(conversationHistoryPending({historyLoading:true,messages:[]},false,[{id:'outbox'}]),false);
});

test('canonical hydration cannot hide retained or newly arriving conversation content',()=>{
 const loading={historyLoaded:false,historyLoading:true};
 for(const messageWindow of [undefined,{offset:40},{mode:'cursor'}]){
  const session={...loading,messageWindow,messages:[{id:'retained'}]};
  assert.equal(conversationHistoryPending(session),false);
  assert.equal(conversationHistoryPending({...session,messages:[]},false,[{id:'outbox'}]),false);
  assert.equal(conversationHistoryPending({...session,messages:[],streaming:'Live response'}),false);
  assert.equal(conversationHistoryPending({...session,messages:[]}),true);
  assert.equal(conversationHistoryPending(session,true),true,'A new navigation owner still gates the old page');
 }
});
