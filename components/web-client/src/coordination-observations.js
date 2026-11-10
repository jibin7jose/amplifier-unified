// Private comparison cursors, never shared host read-state or conversation text.
const KEY='amplifier.related-work-seen.v1';
export function cursorStore(storage){
 let rows={};const refresh=()=>{try{const value=JSON.parse(storage?.getItem(KEY)||'null');if(value&&typeof value==='object'&&!Array.isArray(value))rows=value}catch{}};
 refresh();
 return {
  read(id){refresh();return typeof rows[id]?.cursor==='string'?rows[id].cursor:undefined},
  mark(id,expected,cursor){
   if(this.read(id)!==expected||typeof cursor!=='string')return false;
   rows[id]={cursor,at:Date.now()};rows=Object.fromEntries(Object.entries(rows).sort((a,b)=>(b[1]?.at||0)-(a[1]?.at||0)).slice(0,32));
   try{storage?.setItem(KEY,JSON.stringify(rows));return true}catch{return false}
  },
 };
}

// A token can be committed once, including when React replays layout effects.
// Samples stay within this mounted recap; no session identity or text is retained.
export function recapCommitTiming(now=()=>performance.now()){
 const samples=[],tokens=new WeakSet();
 return {
  received(){const token={at:now(),recorded:false};tokens.add(token);return token},
  committed(token,end=now()){
   if(!token||!tokens.has(token)||token.recorded)return false;
   token.recorded=true;
   if(!Number.isFinite(token.at)||token.at<0||!Number.isFinite(end)||end<token.at)return false;
   samples.push({receivedMs:token.at,committedMs:end,elapsedMs:end-token.at});
   if(samples.length>30)samples.shift();return true;
  },
  snapshot(){return {status:samples.length?'observed':'unavailable',scope:'current recap component mount',
   boundary:'recap dispatch response delivered to component → React layout effect after DOM mutations',
   clock:'performance.now',unit:'milliseconds',samples:samples.map(row=>({...row}))}},
 };
}

export function portableClientTiming(actions,requests,recap=recapCommitTiming().snapshot()){
 const pick=(rows,fields)=>rows.slice(-30).map(row=>Object.fromEntries(fields.filter(key=>Number.isFinite(row[key])&&row[key]>=0).map(key=>[key,row[key]])));
 return {actionQueue:pick(actions,['at','queueMs','runMs']),request:pick(requests,['at','elapsedMs','serverMs','status']),
  rendering:recap,
  limitations:['Browser actions include network and response handling; these are not render or provider timings.',
   'Client samples belong to this browser page and may include other chats. They contain no routes, command names, identifiers, drafts or request bodies.',
   'Recap commit samples are local to this mounted recap. They begin after dispatch resolves, exclude network delivery, and do not measure physical paint or global application rendering.',
   'Client and host timestamps may differ; overlapping durations must not be summed.']};
}
