import test from 'node:test';
import assert from 'node:assert/strict';
import {durationHint} from '../src/duration-hint.js';
const session={status:'working',activity:{phase:'model'},turnDurationHint:{version:1,basis:'recent_similar_turns',scope:'turn',sampleCount:5,startedAt:100,lowerSeconds:30,upperSeconds:100}};
test('range is historical total turn duration and overruns do not count down to zero',()=>{
 assert.equal(durationHint(session,110000).text,'Estimated turn: 30s–2 min');
 assert.match(durationHint(session,210000).text,/Taking longer/);
 assert.match(durationHint(session,110000).detail,/not a deadline/);
});
test('no stale estimate for completed turns, delegates, decisions or insufficient evidence',()=>{
 for(const status of ['idle','stopping','interrupted'])assert.equal(durationHint({...session,status}),null);
 assert.equal(durationHint({...session,activity:{phase:'workers'}}),null);
 assert.equal(durationHint({...session,approvals:[{}]}),null);
 assert.equal(durationHint({...session,turnDurationHint:{...session.turnDurationHint,sampleCount:4}}),null);
});
