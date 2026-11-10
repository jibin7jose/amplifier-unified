import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
test.after(()=>vite.close());
const {ContextLimits}=await vite.ssrLoadModule('/src/context-inspector.jsx');
test('identical missing preflight causes are grouped with every field name',()=>{
 const reason='Exact matching preflight was not captured.';
 const html=renderToStaticMarkup(React.createElement(ContextLimits,{call:{limits:{},budgetStatus:'not_captured',limitEvidence:Object.fromEntries(['requestInputAllowance','providerCount','providerEstimate'].map(k=>[k,{reason,status:'unavailable'}]))}}));
 assert.match(html,/Input allowance · Provider count receipt · Provider fit estimate/);
 assert.equal(html.split(reason).length-1,1);
 assert.match(html,/Not captured/);assert.doesNotMatch(html,/>0<|0%/);
});
test('explicit automatic evidence is distinct from unknown and not reported',()=>{
 const html=renderToStaticMarkup(React.createElement(ContextLimits,{call:{limits:{modelWindow:8000},budgetStatus:'not_reported',limitEvidence:{configuredContextBudget:{status:'automatic',reason:'Automatic policy; no fixed limit.'}}}}));
 assert.match(html,/8,000/);assert.match(html,/Automatic/);assert.match(html,/Not reported/);assert.match(html,/Unknown/);
});
test('historical absence is unknown, not a reason inferred from current settings',()=>{
 const html=renderToStaticMarkup(React.createElement(ContextLimits,{call:{}}));
 assert.match(html,/current settings are not historical evidence/);
 assert.doesNotMatch(html,/Automatic|Unsupported|>0</);
});