// Test-only oracle: latest setup status is not the retained completion ledger.
import {isDeepStrictEqual as equal} from 'node:util';

export function listRequestProblems(request,binding,original=request){
 const problems=[],command=request?.command,receipt=request?.receipt,witness=request?.witness;
 if(!request||!command?.id)return ['unknown list request'];
 if(command.action!=='providers.list'||!equal(command.args,binding.listArgs))problems.push('list request scope');
 if(request.phase!=='received'||request.status!==200||receipt?.accepted!==true||receipt.operationId!==command.id)problems.push('list admission');
 if(request.clientId!==binding.clientId||receipt?.hostInstanceId!==binding.hostInstanceId)problems.push('list client/host');
 // Captured synchronously at fetch, not inferred from the later receipt revision.
 // This is browser-observed configuration, bracketed by authoritative state reads.
 if(!witness||!equal(witness.command,command)||witness.clientId!==binding.clientId||
    witness.observedClientId!==binding.clientId||witness.hostInstanceId!==binding.hostInstanceId)problems.push('list witness');
 if(witness?.configurationRevision!==binding.configurationRevision)problems.push('list configuration');
 if(!Number.isSafeInteger(request.sequence)||!Number.isSafeInteger(witness?.sequence)||
    !Number.isSafeInteger(witness?.revision)||!Number.isSafeInteger(receipt?.stateRevision)||witness.revision>receipt.stateRevision)problems.push('list revision');
 if(command.id!==binding.listId&&(!original||request.sequence<=original.sequence||
    witness?.sequence<=original.witness?.sequence||receipt?.stateRevision<=original.receipt?.stateRevision))problems.push('list successor order');
 return problems;
}

function completionProblems(completion,request,binding){
 const id=request?.command?.id,body=completion?.body;
 if(!completion)return ['list completion missing'];
 if(completion.id!==id||completion.name!=='providers.list'||!equal(completion.args,binding.listArgs)||
    completion.client?.id!==binding.clientId||completion.client?.hostInstanceId!==binding.hostInstanceId||
    !equal(completion.receipt,request?.receipt)||completion.status!==200||body?.path!=='/managementResults/'+id)
  return ['list completion identity'];
 const result=Object.fromEntries((body.items||[]).map(row=>[row.key,row.value]));
 if(result.phase!=='ready'||result.error!==null)return ['list completion not ready'];
 if(!Number.isSafeInteger(body.revision)||body.revision<request.receipt.stateRevision)return ['list completion revision'];
 return [];
}

export function discoveryProblems(row,binding,{actions=[],completions={},candidateId}={}){
 const problems=[],draft=row.view?.newSessionDraft,setup=row.setup||{},defaults=row.defaults;
 if(row.client?.id!==binding.clientId||row.client?.hostInstanceId!==binding.hostInstanceId)problems.push('client/host');
 if(!Number.isSafeInteger(row.revision)||row.revision<binding.minimumRevision||row.configurationRevision!==binding.configurationRevision)problems.push('stale revision');
 if(row.selectedSessionId||draft?.workspace!==''||draft?.location?.kind!=='managed'||(draft.bundle||'')!=='')problems.push('draft scope');
 if(Object.keys(draft?.selection||{}).length)problems.push('explicit draft pin');
 if(row.defaultsKey!==binding.managedKey||setup.providersWorkspace!==binding.managedWorkspace||setup.providersRequestedWorkspace!==''||setup.providersLocation?.kind!=='managed')problems.push('catalog scope');
 if(!setup.providersLoadedAt||!['native-a','native-b'].every(id=>setup.providers?.some(p=>p.id===id&&p.enabled!==false)))problems.push('provider rows');
 if(defaults?.phase!=='ready'||!['native-a','native-b'].every(id=>defaults.providers?.some(p=>p.id===id)))problems.push('default rows');
 if(defaults?.globalOnly!==true||defaults?.bundle!==binding.rootBundle)problems.push('default scope');
 if(!equal(defaults?.effective,binding.effective))problems.push('effective default');
 if(!['native-a','native-b'].every(id=>{
  const entry=setup.providerCatalogs?.[id];
  return entry?.phase==='ready'&&!entry.error&&!entry.modelsDeferred&&entry.sharedCatalogKey&&entry.metadata?.info&&
   entry.models?.some(model=>(typeof model==='string'?model:model.id)==='native-model');
 }))problems.push('catalog settlement');
 const canonical=row.canonical,operation=canonical?.setup?.operations?.['providers.list:'];
 if(canonical?.hostInstanceId!==binding.hostInstanceId||canonical?.configurationRevision!==binding.configurationRevision)problems.push('canonical revision');
 if(canonical?.setup?.configurationTarget!=null)problems.push('targeted settings snapshot');
 if(operation?.phase!=='ready'||operation?.error||operation?.configurationTarget!=null)problems.push('list operation');
 if(!candidateId||operation?.commandId!==candidateId)problems.push('list currentness');
 const originals=actions.filter(r=>r.command?.id===binding.listId),candidates=actions.filter(r=>r.command?.id===candidateId);
 if(originals.length!==1||candidates.length!==1)return [...problems,'unknown or duplicate list request'];
 const original=originals[0],candidate=candidates[0];
 for(const request of new Set([original,candidate])){
  problems.push(...listRequestProblems(request,binding,original));
  const completion=completions[request.command.id];
  problems.push(...completionProblems(completion,request,binding));
  // Detail revision is a read barrier, NOT the command's stored completion time.
  if(completion?.status===200&&(!Number.isSafeInteger(canonical?.revision)||
     row.revision<completion.body.revision||canonical.revision<completion.body.revision))problems.push('stale completion snapshot');
 }
 return [...new Set(problems)];
}
