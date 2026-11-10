import test from 'node:test';
import assert from 'node:assert/strict';
import {messageTime} from '../src/message-time.js';
const at=(year,month,day,hour=15,minute=8)=>new Date(year,month-1,day,hour,minute).getTime();
test('message dates show time today, weekday in the past week, and older calendar dates',()=>{
 const now=at(2026,10,7);
 assert.equal(messageTime(at(2026,10,7,7,20)/1000,now,'en-US').text,'7:20 AM');
 assert.equal(messageTime(at(2026,10,6)/1000,now,'en-US').text,'Tuesday 3:08 PM');
 assert.equal(messageTime(at(2026,9,30)/1000,now,'en-US').text,'Sep 30');
 assert.equal(messageTime(at(2025,10,7)/1000,now,'en-US').text,'Oct 7, 2025');
 assert.equal(messageTime(at(2026,10,9)/1000,now,'en-US').text,'Oct 9');
 assert.equal(messageTime(NaN,now),null);
});
test('calendar-day boundaries use local dates and preserve the complete accessible timestamp',()=>{
 const value=messageTime(at(2026,10,6,23,59)/1000,at(2026,10,7,0,1),'en-US');
 assert.match(value.text,/Tuesday/);assert.match(value.full,/2026/);assert.match(value.iso,/^2026-10-/);
});
