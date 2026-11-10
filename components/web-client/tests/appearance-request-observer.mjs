// Test-only request classification. Never throw from a Playwright event callback.
export function createAppearanceRequestObserver(){
 const actions=[],errors=[],catalogRequests=[],cache=new WeakMap(),observed=new WeakSet();
 let errorCount=0,catalogCount=0;
 function record(error,context){
  errorCount++;
  if(errors.length<16)errors.push({context,message:String(error?.message||error).slice(0,500)});
 }
 function inspect(request){
  if(cache.has(request))return cache.get(request);
  let body=null,method,endpoint;
  try{
   method=request.method();endpoint=new URL(request.url()).pathname;
   if(endpoint==='/api/actions'){
    if(method!=='POST'){
     catalogCount++;
     if(catalogRequests.length<16)catalogRequests.push({method,endpoint});
    }else{
     const value=request.postDataJSON();
     if(!value||typeof value!=='object'||Array.isArray(value)||typeof value.action!=='string'||!value.action.trim())throw Error('Action POST requires an object with a nonempty action name.');
     if(value.args!==undefined&&(!value.args||typeof value.args!=='object'||Array.isArray(value.args)))throw Error('Action POST args must be an object when supplied.');
     body=value;
    }
   }
  }catch(error){record(error,{stage:'request',method,endpoint})}
  cache.set(request,body);
  return body;
 }
 function observe(request){
  try{
   const body=inspect(request);
   if(body&&!observed.has(request)){
    const row={action:body.action,argsKeys:Object.keys(body.args||{})};
    actions.push(row);observed.add(request);
   }
  }catch(error){record(error,{stage:'projection'})}
 }
 return {
  actions,inspect,observe,record,
  matches(request,name){try{return inspect(request)?.action===name}catch(error){record(error,{stage:'predicate'});return false}},
  evidence(){return {errors:[...errors],errorCount,catalogRequests:[...catalogRequests],catalogCount}},
  assertHealthy(){if(errorCount)throw Error('Appearance request observer failed: '+JSON.stringify({errorCount,errors}))},
 };
}
