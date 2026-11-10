import test from 'node:test';
import assert from 'node:assert/strict';
import {quoteNotice,quoteNoticeAfterRemoval,quoteNoticeAfterSend,visibleQuoteNotice} from '../src/quote-notice.js';
test('quote feedback is visible only in its originating chat',()=>{
 for(const text of ['Adding quote…','Quote added. Earlier quotes, commentary and attachments kept.','Choose a passage of at most 12,000 characters. Nothing was shortened.']){
  const notice=quoteNotice('a',text);
  assert.equal(visibleQuoteNotice(notice,'a'),text);
  assert.equal(visibleQuoteNotice(notice,'b'),'');
  assert.equal(visibleQuoteNotice(notice,undefined),'');
 }
});
test('a late refusal after navigation waits in its originating chat',()=>{
 const notice=quoteNotice('a','The conversation changed. The quote was not added.');
 assert.equal(visibleQuoteNotice(notice,'b'),'','the newly selected chat shows nothing');
 assert.equal(visibleQuoteNotice(notice,'a'),'The conversation changed. The quote was not added.','returning shows why the quote is missing');
});
test('sending ends the feedback of the chat it was sent from',()=>{
 const notice=quoteNotice('a','Quote added. Earlier quotes, commentary and attachments kept.');
 assert.equal(quoteNoticeAfterSend(notice,'a'),null);
 assert.equal(quoteNoticeAfterSend(notice,'b'),notice,'a send elsewhere leaves it in place');
 assert.equal(quoteNoticeAfterSend(null,'a'),null);
});
test('removing the last quote ends that chat\'s feedback',()=>{
 const added=quoteNotice('a','Quote added. Earlier quotes, commentary and attachments kept.');
 assert.equal(quoteNoticeAfterRemoval(added,'a',0),null);
 assert.equal(quoteNoticeAfterRemoval(added,'a',1),added,'a remaining quote keeps it');
 assert.equal(quoteNoticeAfterRemoval(added,'b',0),added,'a removal elsewhere leaves it in place');
 assert.equal(quoteNoticeAfterRemoval(null,'a',0),null);
});
test('a late refusal outlasts removing other quotes, not the last one',()=>{
 const refused=quoteNotice('a','The conversation changed. The quote was not added.');
 assert.equal(quoteNoticeAfterRemoval(refused,'a',1),refused,'it still explains the quote that never arrived');
 assert.equal(quoteNoticeAfterRemoval(refused,'b',0),refused);
 assert.equal(quoteNoticeAfterRemoval(refused,'a',0),null);
});
test('no notice, or one without an originating chat, shows nothing',()=>{
 assert.equal(visibleQuoteNotice(null,'a'),'');
 assert.equal(visibleQuoteNotice(quoteNotice(undefined,'Quote added.'),undefined),'');
});
