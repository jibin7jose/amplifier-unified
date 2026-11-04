// Shared by the visible setup page and the independent background worker.
// A fetch TypeError cannot distinguish TLS, network policy and reachability.
async function connectionRequest(host,path,payload,token){
 const headers={'Content-Type':'application/json'};
 if(token)headers.Authorization='Bearer '+token;
 let response;
 try{
  response=await fetch(host+'/api/connections/v1'+path,{method:payload===undefined?'GET':'POST',headers,
   credentials:'omit',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(25000),
   ...(payload===undefined?{}:{body:JSON.stringify(payload)})});
 }catch(cause){
  const timeout=cause.name==='TimeoutError'||cause.name==='AbortError';
  const error=Error(timeout?'The connection to '+host+' timed out. Check that this device can reach the service, then retry.':
   'Cannot reach '+host+' from the extension. Check the service address, browser network access and HTTPS trust. Continuing past a certificate warning does not establish trust for the background connection. Open connection help, then retry.');
  error.code='connection_transport';throw error;
 }
 const text=await response.text();
 let value;try{value=JSON.parse(text)}catch{}
 if(!response.ok){const error=Error(value?.error||text||`Connection failed (${response.status})`);error.status=response.status;throw error}
 if(value===undefined)throw Error('The service returned an unexpected response. Open the service to check the connection.');
 return value;
}
