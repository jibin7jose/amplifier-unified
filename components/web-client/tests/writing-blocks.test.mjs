import {test} from 'node:test';
import assert from 'node:assert/strict';
import {writingParts} from '../src/writing-blocks.js';

test('writing blocks preserve surrounding explanation, exact text and email metadata',()=>{
 const parts=writingParts('Here is the draft.\n:::writing{variant="email" id="12345" subject="Review" recipient="person@example.test"}\nHello,\n\nPlease review.\n:::\nNext step.');
 assert.equal(parts.length,3);assert.equal(parts[1].text,'Hello,\n\nPlease review.');assert.equal(parts[1].subject,'Review');assert.equal(parts[2].text,'Next step.');
});
test('quoted examples, unfinished blocks, arbitrary attrs and unknown variants stay ordinary text',()=>{
 for(const text of ['```text\n:::writing{variant="email" id="12345"}\nExample\n:::\n```',':::writing{variant="document" id="12345"}\nStill streaming',':::writing{variant="html" id="12345"}\n<script>\n:::',':::writing{variant="email" id="12345" onclick="alert(1)"}\nNo\n:::'])assert.ok(writingParts(text).every(p=>p.type==='text'));
});
