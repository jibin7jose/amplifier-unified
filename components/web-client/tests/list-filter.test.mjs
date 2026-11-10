import test from 'node:test';
import assert from 'node:assert/strict';
import {listMatcher,filterList} from '../src/list-filter.js';
test('plain search is case insensitive; fnmatch patterns match whole fields',()=>{
 for(const [pattern,value,expected] of [['OpenAI','provider-openai',true],['tool-*','tool-filesystem',true],['tool-*','hooks-tool-logs',false],['*openai*','PROVIDER-OPENAI',true],['gpt-?','gpt-6',true],['gpt-?','gpt-66',false],['model-[1-3]','model-2',true],['model-[!13]','model-2',true],['model-[!13]','model-3',false],['[[]test','[test',true],['[]]test',']test',true],['[broken','[broken',true],['file(1).*','file(1).yaml',true],['','anything',true],['*','',true],['a/**','a/b/c',true]])assert.equal(listMatcher(pattern)(value),expected,pattern+' on '+value);
});
test('filter searches public fields independently without modifying ordering or input',()=>{
 const items=[{id:'tool-a',name:'Alpha'},{id:'tool-b',name:'Beta'},{id:'hooks-c',name:'Alpha'}];
 assert.deepEqual(filterList(items,'tool-[ab]',r=>[r.id,r.name]),items.slice(0,2));
 assert.deepEqual(filterList(items,'Alpha',r=>[r.id,r.name]),[items[0],items[2]]);
 assert.equal(items.length,3);
 assert.equal(listMatcher('*a'.repeat(80)+'b')('a'.repeat(500)),false);
});
