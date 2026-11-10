import assert from 'node:assert/strict';
import {test} from 'node:test';
import {canvasReference} from '../src/canvas-links.js';

test('only exact saved references can become Canvas actions',()=>{
 assert.deepEqual(canvasReference('amplifier-canvas://artifact/abc-123?session=chat-123&version=2'),{id:'abc-123',sessionId:'chat-123',version:2});
 for(const bad of [
  'https://example.com/?session=chat&version=2','javascript:alert(1)',
  'amplifier-canvas://artifact/a?session=chat&version=0',
  'amplifier-canvas://artifact/a?session=chat&version=9007199254740992',
  'amplifier-canvas://artifact/a?session=chat&version=2&action=session.delete',
  'amplifier-canvas://artifact/a?session=chat&version=2&version=3',
  'amplifier-canvas://artifact/a?session=chat&version=2#command',
  'amplifier-canvas://artifact/a/b?session=chat&version=2',
  'amplifier-canvas://artifact/a?session=chat','amplifier-canvas://artifact/a?version=2',
  'amplifier-canvas://artifact/a?session=%2Fsecret&version=2',
 ])assert.equal(canvasReference(bad),null,bad);
});
