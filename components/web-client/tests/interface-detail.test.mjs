import test from 'node:test';
import assert from 'node:assert/strict';
import {detailOpen,toggleDetail} from '../src/interface-detail.js';
test('Everything opens work automatically while explicit collapse persists across display levels',()=>{
 let expanded=new Set();assert.equal(detailOpen(expanded,'turn:1','minimal'),false);
 assert.equal(detailOpen(expanded,'turn:1','detailed'),true);
 expanded=toggleDetail(expanded,'turn:1','detailed');assert.equal(detailOpen(expanded,'turn:1','detailed'),false);
 assert.equal(detailOpen(expanded,'turn:2','detailed'),true);
 expanded=toggleDetail(expanded,'turn:1','minimal');assert.equal(detailOpen(expanded,'turn:1','minimal'),true);
});
