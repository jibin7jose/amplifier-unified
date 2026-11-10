import test from 'node:test';
import assert from 'node:assert/strict';
import {discoveryProblems} from './provider-native-readiness.mjs';

function scenario(){
 const binding={clientId:'client',hostInstanceId:'host',configurationRevision:0,minimumRevision:37,listId:'explicit',
  listArgs:{workspace:'',location:{kind:'managed'}},managedKey:'["","","managed"]',managedWorkspace:'/fixture/app',rootBundle:'/fixture/root.yaml',
  effective:{instance:'native-a',model:'native-model',effort:'low'}};
 const request=(id,sequence,revision)=>{
  const command={id,action:'providers.list',args:structuredClone(binding.listArgs)};
  return {command,sequence,clientId:'client',phase:'received',status:200,
   receipt:{accepted:true,revision,stateRevision:revision,operationId:id,hostInstanceId:'host'},
   witness:{command:structuredClone(command),sequence,clientId:'client',observedClientId:'client',hostInstanceId:'host',configurationRevision:0,revision:revision-1}};
 };
 const explicit=request('explicit',10,24),successor=request('successor',12,31);
 const completion=(r,revision)=>({id:r.command.id,name:'providers.list',args:structuredClone(binding.listArgs),receipt:structuredClone(r.receipt),
  client:{id:'client',hostInstanceId:'host'},status:200,body:{path:'/managementResults/'+r.command.id,revision,items:[{key:'phase',value:'ready'},{key:'error',value:null}]}});
 const providers=[{id:'native-a'},{id:'native-b'}];
 const row={client:{id:'client',hostInstanceId:'host'},revision:37,configurationRevision:0,selectedSessionId:null,
  view:{newSessionDraft:{workspace:'',location:{kind:'managed'},bundle:'',selection:{}}},defaultsKey:binding.managedKey,
  defaults:{phase:'ready',providers,globalOnly:true,bundle:binding.rootBundle,effective:structuredClone(binding.effective)},
  setup:{providersWorkspace:binding.managedWorkspace,providersRequestedWorkspace:'',providersLocation:{kind:'managed'},providersLoadedAt:1,providers,
   providerCatalogs:Object.fromEntries(providers.map(p=>[p.id,{phase:'ready',models:['native-model'],sharedCatalogKey:p.id,metadata:{info:{id:p.id}}}]))},
  canonical:{revision:37,hostInstanceId:'host',configurationRevision:0,setup:{configurationTarget:null,operations:{'providers.list:':{commandId:'successor',phase:'ready',error:null,configurationTarget:null}}}}};
 return {row,binding,evidence:{actions:[explicit,successor],completions:{explicit:completion(explicit,30),successor:completion(successor,37)},candidateId:'successor'}};
}
const check=s=>discoveryProblems(s.row,s.binding,s.evidence);
const operation=s=>s.row.canonical.setup.operations['providers.list:'];
const successor=s=>s.evidence.actions[1];

test('ordered completion then same-scope successor then defaults settlement is valid',()=>{
 const s=scenario();delete s.evidence.completions.successor;s.row.defaults.phase='working';
 assert.ok(check(s).includes('list completion missing'));
 s.evidence.completions.successor=scenario().evidence.completions.successor;
 assert.ok(check(s).includes('default rows'));
 s.row.defaults.phase='ready';
 // The former fixed-ID assertion fails on this valid ordering.
 assert.notEqual(operation(s).commandId,s.binding.listId);
 assert.deepEqual(check(s),[]);
 assert.equal(s.binding.listId,'explicit','Do not silently rebind the original operation');
});
test('original request may remain current after completion',()=>{
 const s=scenario();operation(s).commandId='explicit';s.evidence.candidateId='explicit';
 assert.deepEqual(check(s),[]);
});
test('lookup must be followed by a fresh still-current snapshot',()=>{
 const s=scenario();s.row.revision=s.row.canonical.revision=36;
 assert.ok(check(s).includes('stale completion snapshot'));
 s.row.revision=s.row.canonical.revision=37;assert.deepEqual(check(s),[]);
 operation(s).commandId='third';assert.ok(check(s).includes('list currentness'));
});

for(const [name,mutate,reason] of [
 ['unknown latest ID',s=>{operation(s).commandId=s.evidence.candidateId='unknown'},'unknown or duplicate list request'],
 ['duplicate candidate',s=>{s.evidence.actions.push(structuredClone(successor(s)))},'unknown or duplicate list request'],
 ['missing original',s=>{s.evidence.actions.shift()},'unknown or duplicate list request'],
 ['wrong workspace',s=>{successor(s).command.args.workspace='/other'},'list request scope'],
 ['wrong location',s=>{successor(s).command.args.location.kind='workspace'},'list request scope'],
 ['targeted settings request',s=>{successor(s).command.args.configurationTarget={kind:'global'}},'list request scope'],
 ['extra session scope',s=>{successor(s).command.args.sessionId='other'},'list request scope'],
 ['wrong action',s=>{successor(s).command.action='providers.models'},'list request scope'],
 ['wrong client',s=>{successor(s).clientId='other'},'list client/host'],
 ['wrong receipt host',s=>{successor(s).receipt.hostInstanceId='other'},'list client/host'],
 ['wrong observed client',s=>{successor(s).witness.observedClientId='other'},'list witness'],
 ['wrong observed host',s=>{successor(s).witness.hostInstanceId='other'},'list witness'],
 ['missing request-time witness',s=>{delete successor(s).witness},'list witness'],
 ['wrong observed configuration',s=>{successor(s).witness.configurationRevision=1},'list configuration'],
 ['pending receipt',s=>{successor(s).phase='sent'},'list admission'],
 ['rejected receipt',s=>{successor(s).receipt.accepted=false},'list admission'],
 ['wrong operation admission',s=>{successor(s).receipt.operationId='other'},'list admission'],
 ['older request',s=>{successor(s).sequence=9},'list successor order'],
 ['older witness',s=>{successor(s).witness.sequence=9},'list successor order'],
 ['older admission',s=>{successor(s).receipt.stateRevision=23},'list successor order'],
 ['missing successor completion',s=>{delete s.evidence.completions.successor},'list completion missing'],
 ['pending successor completion',s=>{s.evidence.completions.successor.body.items[0].value='working'},'list completion not ready'],
 ['failed successor completion',s=>{s.evidence.completions.successor.body.items[0].value='error'},'list completion not ready'],
 ['successor completion error',s=>{s.evidence.completions.successor.body.items[1].value='failed'},'list completion not ready'],
 ['original completion failed',s=>{s.evidence.completions.explicit.body.items[0].value='error'},'list completion not ready'],
 ['wrong completion path',s=>{s.evidence.completions.successor.body.path='/managementResults/other'},'list completion identity'],
 ['wrong completion client',s=>{s.evidence.completions.successor.client.id='other'},'list completion identity'],
 ['stale completion revision',s=>{s.evidence.completions.successor.body.revision=30},'list completion revision'],
 ['pending current slot',s=>{operation(s).phase='working'},'list operation'],
 ['failed current slot',s=>{operation(s).phase='error';operation(s).error='bad'},'list operation'],
 ['replaced current ID',s=>{operation(s).commandId='third'},'list currentness'],
 ['stale snapshot',s=>{s.row.revision=36},'stale revision'],
 ['stale canonical snapshot',s=>{s.row.canonical.revision=36},'stale completion snapshot'],
 ['changed configuration',s=>{s.row.configurationRevision=1},'stale revision'],
 ['changed canonical configuration',s=>{s.row.canonical.configurationRevision=1},'canonical revision'],
 ['wrong catalog scope',s=>{s.row.setup.providersLocation.kind='workspace'},'catalog scope'],
 ['targeted canonical state',s=>{s.row.canonical.setup.configurationTarget={kind:'global'}},'targeted settings snapshot'],
 ['empty providers',s=>{s.row.setup.providers=[]},'provider rows'],
 ['wrong default',s=>{s.row.defaults.effective.instance='native-b'},'effective default'],
 ['catalog still working',s=>{s.row.setup.providerCatalogs['native-a'].phase='working'},'catalog settlement'],
 ['draft pin',s=>{s.row.view.newSessionDraft.selection={instance:'native-a'}},'explicit draft pin'],
])test('rejects '+name,()=>{const s=scenario();mutate(s);assert.ok(check(s).includes(reason),JSON.stringify(check(s)))});
