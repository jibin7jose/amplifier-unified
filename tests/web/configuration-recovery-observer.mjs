import assert from 'node:assert/strict';

// Specific to the recovery journey: passive catalog reads are not submitted
// actions. Callback failures are retained, then raised by the awaited test body.
export function recoveryRequestObserver(base,actions){
 const endpoint=new URL('/api/actions',base),failures=[];
 let catalogReads=0,postRequests=0,failureCount=0;
 const fail=(browser,code)=>{failureCount++;if(failures.length<8)failures.push({browser,code})};
 return {
  record(request,browser){
   let code='request_metadata_failed';
   try{
    const url=new URL(request.url());
    if(url.origin!==endpoint.origin||url.pathname!==endpoint.pathname)return;
    const method=request.method();
    if(method==='GET'){catalogReads++;return}
    if(method!=='POST')return;
    postRequests++;code='action_post_decode_failed';
    const body=request.postDataJSON();
    if(!body||typeof body!=='object'||Array.isArray(body)||
       typeof body.action!=='string'||!/^[a-z][a-z0-9_.]{0,79}$/.test(body.action)||
       (body.args!==undefined&&(!body.args||typeof body.args!=='object'||Array.isArray(body.args)))||
       (body.id!==undefined&&(typeof body.id!=='string'||!body.id||body.id.length>200))){
     fail(browser,'invalid_action_post');return;
    }
    actions.push({browser,action:body.action,args:body.args,id:body.id});
   }catch{fail(browser,code)}
  },
  snapshot(){return {catalogReads,postRequests,recordedActions:actions.length,failureCount,failures:[...failures],omittedFailures:failureCount-failures.length}},
  assertHealthy(){assert.equal(failureCount,0,'Recovery request observer failed: '+JSON.stringify({failureCount,failures}))},
 };
}
