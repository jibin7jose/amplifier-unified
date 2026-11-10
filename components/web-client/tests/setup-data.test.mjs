import test from 'node:test';
import assert from 'node:assert/strict';
import {providerConfig,modelOptions,updateCandidate,updateRole} from '../src/setup-data.js';
test('provider configuration accepts environment references but rejects literal credentials',()=>{
 assert.deepEqual(providerConfig('{"api_key":"${OPENAI_API_KEY}","model":"custom"}'),{api_key:'${OPENAI_API_KEY}',model:'custom'});
 assert.throws(()=>providerConfig('{"api_key":"real-secret"}'),/private API key/);
 assert.throws(()=>providerConfig('[]'),/object/);
});
test('advanced image backend config preserves the independent chat model',()=>{
 const config={default_model:'kept-chat-model',reasoning_effort:'high',api_key:'${TEAM_API_KEY}',image_generation:{enabled:true,id:'images',model:'chosen-image-model'}};
 assert.deepEqual(providerConfig(JSON.stringify(config)),config);
});
test('routing candidate updates preserve fallback configuration and other roles',()=>{
 const original={name:'mine',roles:{coding:{description:'Code',candidates:[{provider:'openai',model:'one',config:{effort:'high'}},{provider:'other',model:'fallback'}]},fast:{candidates:[]}}};
 const changed=updateCandidate(original,'coding',0,{model:'two'});
 assert.equal(changed.roles.coding.candidates[0].model,'two');assert.equal(original.roles.coding.candidates[0].model,'one');
 assert.equal(changed.roles.coding.candidates[0].config.effort,'high');assert.deepEqual(changed.roles.fast,original.roles.fast);
 assert.equal(updateRole(changed,'new',{candidates:[]}).roles.coding.description,'Code');
});
test('provider model catalogs accept native records and string identifiers',()=>{
 assert.deepEqual(modelOptions({models:['one',{id:'two',display_name:'Two'},{}]}),[{id:'one',name:'one'},{id:'two',name:'Two'}]);
});

import {providerFields} from '../src/setup-data.js';
test('provider options follow native visibility rules and exclude secret fields',()=>{
 const metadata={info:{config_fields:[{id:'key',field_type:'secret',default:'private'},{id:'model'},{id:'reasoning_effort',field_type:'choice',requires_model:true,choices:['low','high']},{id:'long_context',field_type:'boolean',show_when:{default_model:'matches:^gpt-6'}}]}};
 assert.deepEqual(providerFields(metadata,{}),[]);
 assert.deepEqual(providerFields(metadata,{default_model:'gpt-5'}).map(f=>f.id),['reasoning_effort']);
 assert.deepEqual(providerFields(metadata,{default_model:'GPT-6-astra'}).map(f=>f.id),['reasoning_effort','long_context']);
});
