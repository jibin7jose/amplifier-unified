import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {NativeProviderSettings}=await server.ssrLoadModule('/src/native-provider-settings.jsx');
test.after(()=>server.close());
test('unavailable native transport remains clear with disabled compaction',()=>{
 const html=renderToStaticMarkup(React.createElement(NativeProviderSettings,{results:{'native.status':{supported:false,reason:'Selected Terra uses request boundaries.'}},run:()=>{},busy:false,active:false}));
 assert.match(html,/Selected Terra uses request boundaries/);
 assert.match(html,/disabled=""[^>]*data-action="runtime.control"[^>]*>Compact/);
});
test('unknown outcome is explicit and active work never allows compaction',()=>{
 const html=renderToStaticMarkup(React.createElement(NativeProviderSettings,{results:{'native.status':{supported:true,compactAvailable:true,receipt:{outcome:'unknown'}}},run:()=>{},busy:false,active:true}));
 assert.match(html,/outcome is unknown/);assert.match(html,/not replayed/);
 assert.match(html,/disabled=""[^>]*data-action="runtime.control"[^>]*>Compact/);
});
test('eligibility is not live qualification or application',()=>{
 const html=renderToStaticMarkup(React.createElement(NativeProviderSettings,{results:{'native.status':{supported:true,model:'gpt-6.1-terra'}},run:()=>{},busy:false,active:false}));
 assert.match(html,/eligible for gpt-6.1-terra/);
 assert.match(html,/not verified by this check/);
 assert.match(html,/queued, not applied/);
 assert.match(html,/successor response/);
});
