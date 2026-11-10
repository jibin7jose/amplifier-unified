// Tab-private destinations, never conversation bodies, drafts, or replayable commands.
const STORAGE_KEY='amplifier.destinationHistory';
const MAX_ENTRIES=30, MAX_BYTES=65536, LABEL_LIMIT=160;
const text=value=>typeof value==='string'?value.slice(0,LABEL_LIMIT):'';
const id=value=>typeof value==='string'&&value.length>0&&value.length<=200?value:null;
const tabs=new Set(['chats','work','files','details']);
export const sameDestination=(a,b)=>!!a&&!!b&&a.kind===b.kind&&(a.id||null)===(b.id||null);
export const destinationKey=entry=>JSON.stringify([entry.kind,entry.id||null]);
const sameRoute=(a,b)=>a===null&&b===null||sameDestination(a,b)&&(a.tab||null)===(b.tab||null);

export function destination(state){
  const view=state?.view||{},surface=view.workSurface||'chat';
  if(surface==='home')return {kind:'home',label:'Home'};
  if(surface==='workspaces')return {kind:'workspaces',label:'All workspaces'};
  if(surface==='chats')return {kind:'chats',label:'Search chats'};
  if(surface==='workspace'){
    const identity=id(view.workWorkspaceId);if(!identity)return null;
    const workspace=state.workspaces?.find(row=>row.id===identity);
    return {kind:'workspace',id:identity,tab:tabs.has(view.workWorkspaceTab)?view.workWorkspaceTab:'chats',label:text(workspace?.name)||'Workspace'};
  }
  if(surface!=='chat'||!id(state?.selectedSessionId))return null;
  const identity=state.selectedSessionId,session=state.sessions?.find(row=>row.id===identity);
  const workspace=state.workspaces?.find(row=>row.id===(session?.workspaceId||state.selectedWorkspaceId));
  return {kind:'chat',id:identity,label:text(session?.title)||'Chat',workspaceLabel:text(workspace?.name)};
}

export function destinationCommand(entry){
  if(entry.kind==='chat')return ['session.select',{id:entry.id}];
  if(entry.kind==='workspace')return ['view.update',{patch:{workSurface:'workspace',workWorkspaceId:entry.id,workWorkspaceTab:entry.tab,navExpanded:false}}];
  return ['view.update',{patch:{workSurface:entry.kind,navExpanded:false}}];
}

function cleanEntry(row){
  if(!row||!id(row.key)||!['home','chat','workspace','workspaces','chats'].includes(row.kind))return null;
  if(['chat','workspace'].includes(row.kind)&&!id(row.id))return null;
  if(row.kind==='workspace'&&!tabs.has(row.tab))return null;
  return {key:row.key,kind:row.kind,...(['chat','workspace'].includes(row.kind)?{id:row.id}:{}),...(row.kind==='workspace'?{tab:row.tab}:{}),label:text(row.label),...(row.kind==='chat'?{workspaceLabel:text(row.workspaceLabel)}:{})};
}

export function createDestinationHistory({storage=()=>globalThis.sessionStorage,key=()=>crypto.randomUUID(),changed=()=>{}}={}){
  let scope=null,entries=[],recent=[],cursor=-1,detached=false,current=null,pending=null,loaded=false,memoryOnly=false,lastPublished=null,observed=null;
  const retiredScopes=new Set();
  const snapshot=()=>({entries:entries.map(row=>({...row})),recent:recent.map(row=>({...row})),cursor,detached,pending:!!pending,ready:!!scope,memoryOnly,
    current:!detached&&entries[cursor]?{...entries[cursor]}:null,
    backKey:scope&&!pending?entries[detached?cursor:cursor-1]?.key||null:null,
    forwardKey:scope&&!pending?entries[cursor+1]?.key||null:null});
  const persist=()=>{
    if(!scope)return;
    try{
      const value=JSON.stringify({version:1,scope,entries,cursor,recent});
      if(new TextEncoder().encode(value).length>MAX_BYTES)throw Error('History too large');
      storage().setItem(STORAGE_KEY,value);
    }catch{memoryOnly=true}
  };
  const publish=()=>{
    const value=snapshot(),serialized=JSON.stringify(value);
    if(serialized===lastPublished)return;
    persist();lastPublished=JSON.stringify(snapshot());changed(snapshot());
  };
  const restore=nextScope=>{
    pending?.resolve(false);
    entries=[];recent=[];cursor=-1;detached=false;pending=null;current=null;loaded=false;scope=nextScope;observed=null;
    try{
      const raw=storage().getItem(STORAGE_KEY);
      if(!raw)return;
      if(new TextEncoder().encode(raw).length>MAX_BYTES)throw Error('History too large');
      const value=JSON.parse(raw);
      if(value.version!==1||value.scope!==scope||!Array.isArray(value.entries)||value.entries.length>MAX_ENTRIES)throw Error('Different history scope');
      const rows=value.entries.map(cleanEntry);
      if(rows.some(row=>!row)||new Set(rows.map(row=>row.key)).size!==rows.length||!Number.isInteger(value.cursor)||value.cursor< -1||value.cursor>=rows.length||(rows.length>0&&value.cursor<0))throw Error('Invalid history');
      entries=rows;cursor=value.cursor;
      // Old trails have no activation order. Seed from their last recorded
      // occurrence, then confirmed navigation (including traversal) owns MRU.
      const savedRecent=Array.isArray(value.recent)&&value.recent.length<=MAX_ENTRIES?value.recent.map(cleanEntry):null;
      const candidates=savedRecent?.every(Boolean)?savedRecent:[...rows].reverse();
      const seen=new Set();
      recent=candidates.filter(row=>{const identity=destinationKey(row);if(seen.has(identity))return false;seen.add(identity);return true});
    }catch{try{storage().removeItem(STORAGE_KEY)}catch{memoryOnly=true}}
  };
  const refreshLabels=state=>{
    // Only already-projected metadata. Absence from a bounded page is not deletion.
    const refresh=row=>{
      if(row.kind==='chat'){
        const session=state.sessions?.find(item=>item.id===row.id);
        if(!session)return row;
        const workspace=state.workspaces?.find(item=>item.id===session.workspaceId);
        return {...row,label:text(session.title)||row.label,workspaceLabel:text(workspace?.name)||row.workspaceLabel};
      }
      if(row.kind==='workspace')return {...row,label:text(state.workspaces?.find(item=>item.id===row.id)?.name)||row.label};
      return row;
    };
    entries=entries.map(refresh);recent=recent.map(refresh);
  };
  const remember=entry=>{
    recent=[{...entry},...recent.filter(row=>!sameDestination(row,entry))].slice(0,MAX_ENTRIES);
  };
  const record=route=>{
    current=route;
    if(!route){detached=true;return}
    if(!detached&&sameDestination(entries[cursor],route)){
      entries[cursor]={...entries[cursor],...route};remember(entries[cursor]);return;
    }
    // Explicitly selecting the current saved destination from New chat reconnects
    // to that visit; it is not a second copy and does not discard Forward.
    if(detached&&sameDestination(entries[cursor],route)){
      entries[cursor]={...entries[cursor],...route};detached=false;remember(entries[cursor]);return;
    }
    entries=entries.slice(0,cursor+1);entries.push({...route,key:key()});
    if(entries.length>MAX_ENTRIES)entries.shift();
    cursor=entries.length-1;detached=false;
    remember(entries[cursor]);
  };
  const settle=confirmed=>{
    const attempt=pending;pending=null;
    if(!confirmed)detached=!sameDestination(entries[cursor],current);
    publish();attempt?.resolve(confirmed);return confirmed;
  };
  const reconcile=()=>{
    if(!pending?.receipt)return null;
    const {receipt,frames,entry}=pending;
    if(observed&&observed.host!==receipt.hostInstanceId)return settle(false);
    // Reconnect snapshots are not new visits, but can corroborate a receipt.
    const eligible=frames.filter(frame=>frame.host===receipt.hostInstanceId&&frame.revision>=receipt.stateRevision);
    if(!eligible.length)return null;
    const match=eligible.findIndex(frame=>sameRoute(frame.route,entry));
    if(match<0)return settle(false);
    if(pending.mode==='place')record(eligible[match].route);
    else{
      const index=entries.findIndex(row=>row.key===pending.key);
      if(index<0)return settle(false);
      cursor=index;detached=false;current=eligible[match].route;entries[cursor]={...entries[cursor],...current};
      remember(entries[cursor]);
    }
    for(const frame of eligible.slice(match+1)){
      if(frame.baseline){current=frame.route;detached=!sameDestination(entries[cursor],current)}
      else if(!sameRoute(current,frame.route))record(frame.route);
    }
    return settle(true);
  };
  const observe=(state,{baseline=false}={})=>{
    const nextScope=state?.client?.historyScope;
    if(nextScope&&retiredScopes.has(nextScope))return;
    if(typeof nextScope==='string'&&nextScope&&nextScope!==scope){
      if(scope&&!baseline)return;
      if(scope)retiredScopes.add(scope);
      restore(nextScope);
    }
    if(!scope)return;
    // A stream opened under an old sign-in cannot restore the trail after 401.
    if(nextScope!==scope)return;
    const host=state.client?.hostInstanceId,revision=state.revision;
    if(observed&&(host!==observed.host&&!baseline||host===observed.host&&revision<observed.revision))return;
    observed={host,revision};refreshLabels(state);
    const route=destination(state);
    if(pending){
      // Collapse progress, not distinct destinations. Keep the latest qualifying
      // revision for a continuous visit so it can confirm a delayed receipt.
      const last=pending.frames.at(-1),frame={route,revision,host,baseline};
      if(last&&last.host===host&&last.baseline===baseline&&sameRoute(last.route,route))pending.frames[pending.frames.length-1]=frame;
      else pending.frames.push(frame);
      current=route;
      if(pending.frames.length>MAX_ENTRIES){settle(false);return}
      reconcile();return;
    }
    if(!loaded||baseline){
      if(!entries.length&&route)record(route);
      else{current=route;detached=!sameDestination(entries[cursor],route);if(!detached)entries[cursor]={...entries[cursor],...route}}
      loaded=true;
    }else if(!sameRoute(current,route)||(!current&&route))record(route);
    else current=route;
    publish();
  };
  return {
    snapshot,observe,
    begin(visitKey){
      if(!scope||pending)return null;
      if(!detached&&entries[cursor]?.key===visitKey)return null;
      const entry=entries.find(row=>row.key===visitKey);if(!entry)return null;
      let resolve;const confirmed=new Promise(done=>{resolve=done});
      pending={key:visitKey,entry:{...entry},frames:[],resolve,receipt:null};
      publish();return {...entry,confirmed};
    },
    beginRecent(placeKey,selected=null,expectedScope=scope){
      if(!scope||expectedScope!==scope||pending)return null;
      const available=recent.find(row=>destinationKey(row)===placeKey);
      // Preserve the selected tab through the navigation queue; a preceding
      // visit can refresh this same place before our command gets to execute.
      const entry=selected?cleanEntry(selected):available;
      if(!available||!entry||destinationKey(entry)!==placeKey)return null;
      if(!entry||!detached&&sameRoute(entries[cursor],entry))return null;
      let resolve;const confirmed=new Promise(done=>{resolve=done});
      pending={mode:'place',entry:{...entry},frames:[],resolve,receipt:null};
      publish();return {...entry,confirmed};
    },
    finish(receipt){
      if(!pending)return false;
      if(receipt?.accepted!==true||!Number.isInteger(receipt.stateRevision)||typeof receipt.hostInstanceId!=='string')return settle(false);
      pending.receipt=receipt;return reconcile();
    },
    invalidate(nextScope=null){
      // A reconnect may already have established the new sign-in before the
      // first HTTP response reports that same transition. Do not retire it.
      if(scope&&nextScope===scope)return;
      if(scope)retiredScopes.add(scope);
      pending?.resolve(false);
      scope=null;entries=[];recent=[];cursor=-1;current=null;pending=null;loaded=false;detached=false;observed=null;
      try{storage().removeItem(STORAGE_KEY)}catch{memoryOnly=true}
      lastPublished=null;publish();
    },
  };
}
