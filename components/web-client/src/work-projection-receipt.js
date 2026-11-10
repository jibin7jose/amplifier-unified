const statuses=new Set(['loading','retrying','unavailable','ready']);
const identity=value=>typeof value==='string'&&value.length>0&&value.length<=200;
const count=value=>Number.isSafeInteger(value)&&value>=0;

// DOM-only observation of the host's accepted display generation. This does
// not reconstruct Work, imply runtime completion, or expose source identities.
export function workProjectionReceipt(session,hostInstanceId){
 const projection=session?.workProjection;
 if(!identity(session?.id)||!identity(hostInstanceId)||!statuses.has(projection?.status))return undefined;
 const supplied=projection.receipt;
 const window=supplied?.scope==='recent-events';
 const valid=supplied?.sessionId===session.id&&supplied?.hostInstanceId===hostInstanceId&&
  Number.isSafeInteger(supplied.generation)&&supplied.generation>0&&
  (window?identity(supplied.windowKey)&&supplied.historyComplete===false&&
   ['sourceCount','itemCount','scannedBytes','scannedEvents'].every(key=>count(supplied[key])):
   ['sourceCount','nodeCount','turnCount'].every(key=>count(supplied[key])));
 const receipt=valid?{generation:supplied.generation,sourceCount:supplied.sourceCount,
  ...(window?{scope:'recent-events',windowKey:supplied.windowKey,historyComplete:false,
   itemCount:supplied.itemCount,scannedBytes:supplied.scannedBytes,scannedEvents:supplied.scannedEvents}:
   {nodeCount:supplied.nodeCount,turnCount:supplied.turnCount})}:null;
 const value=JSON.stringify({version:window?2:1,hostInstanceId,sessionId:session.id,status:projection.status,
  current:projection.current===true&&projection.status==='ready'&&valid,receipt});
 return new TextEncoder().encode(value).byteLength<=1024?value:undefined;
}
