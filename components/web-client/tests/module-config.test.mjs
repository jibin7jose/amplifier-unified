import test from 'node:test';
import assert from 'node:assert/strict';
import {moduleRows,updateModule,parsePlan} from '../src/module-config.js';
test('module editor preserves source, instance identity, and sibling config when changing one module',()=>{
 const plan={session:{orchestrator:{module:'loop-live'},context:{module:'context-simple'}},providers:[{module:'provider-openai',instance_id:'work',source:'git+https://example.test/provider',config:{model:'example',temperature:.3}}],tools:[{module:'tool-bash',config:{timeout:10}}]};
 const rows=moduleRows(plan);assert.equal(rows.find(r=>r.section==='providers').id,'work');
 const updated=updateModule(plan,'tools:0',{enabled:false});assert.equal(updated.tools[0].enabled,false);assert.equal(plan.tools[0].enabled,undefined);
 assert.deepEqual(updated.providers,plan.providers);assert.equal(updated.providers[0].source,plan.providers[0].source);
 const configured=updateModule(updated,'providers:0',{config:{model:'other'}});assert.equal(configured.providers[0].instance_id,'work');assert.equal(configured.tools[0].enabled,false);
});
test('required context/orchestrator cannot be removed with a toggle',()=>{
 assert.throws(()=>updateModule({session:{orchestrator:{module:'loop-live'}}},'session:orchestrator',{enabled:false}),/requires an orchestrator/);
});
test('invalid JSON/config shapes are rejected before applying',()=>{
 for(const text of ['[]','null','"config"','{broken'])assert.throws(()=>parsePlan(text));
 assert.deepEqual(parsePlan('{"tools":[]}'),{tools:[]});
});
