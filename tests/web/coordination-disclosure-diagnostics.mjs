// Passive, bounded facts for a disclosure failure; never retain frame bodies,
// chat text, URLs or credentials, and never replay the failed interaction.
export async function observeCoordinationDisclosures(page){
 const events=[],sessions=new Map(),limit=128;let dropped=0,connection=0;
 const session=value=>{
  if(typeof value!=='string'||!value)return null;
  if(!sessions.has(value)&&sessions.size<32)sessions.set(value,`session-${sessions.size+1}`);
  return sessions.get(value)||'other-session';
 };
 const panel=value=>value==null?null:['coordination','chat-work','session-details'].includes(value)?value:'other-panel';
 const record=fact=>{if(events.length===limit){events.shift();dropped++}events.push({at:Date.now(),...fact})};
 const prefix='COORDINATION_DISCLOSURE_DIAGNOSTIC ';
 page.on('console',message=>{
  const text=message.text();if(!text.startsWith(prefix)||text.length>2048)return;
  try{const fact=JSON.parse(text.slice(prefix.length));record({...fact,selected:session(fact.selected),panel:panel(fact.panel)})}catch{}
 });
 page.on('websocket',socket=>{
  const id=++connection;record({kind:'socket-open',connection:id});
  socket.on('close',()=>record({kind:'socket-close',connection:id}));
  socket.on('framereceived',({payload})=>{
   if(payload.length>2*1024*1024){record({kind:'frame-too-large',connection:id});return}
   try{
    const {event,data}=JSON.parse(String(payload));if(!data||!['state','state-delta'].includes(event))return;
    const fact={kind:event,connection:id,revision:data.revision,baseRevision:data.baseRevision};let relevant=event==='state';
    if(event==='state'){fact.selected=session(data.selectedSessionId);fact.panel=panel(data.view?.panel)}
    for(const change of event==='state-delta'?(data.changes||[]):[]){
     const path=change.path;if(!Array.isArray(path))continue;
     if(path.length===0){fact.selected=session(change.value?.selectedSessionId);fact.panel=panel(change.value?.view?.panel);relevant=true}
     if(path.length===1&&path[0]==='selectedSessionId'){fact.selected=session(change.value);relevant=true}
     if(path[0]==='view'&&(path.length===1||(path.length===2&&path[1]==='panel'))){fact.panel=panel(path.length===1?change.value?.panel:change.value);relevant=true}
    }
    if(relevant)record(fact);
   }catch{}
  });
 });
 await page.addInitScript(({prefix})=>{
  const labels=new Set(['Follow up or watch work','Exact requests, messages and saved results']);
  const known=new WeakMap();let next=0;
  const identify=node=>{
   if(!(node instanceof HTMLDetailsElement))return null;
   if(!known.has(node)){
    const label=node.querySelector(':scope > summary')?.textContent?.trim();if(!labels.has(label))return null;
    known.set(node,{node:++next,label,mounted:false});
   }
   return known.get(node);
  };
  const emit=(kind,node)=>{
   const entry=identify(node);if(!entry)return;
   const state=window.amplifier?.getState?.();
   console.debug(prefix+JSON.stringify({kind,document:performance.timeOrigin,node:entry.node,label:entry.label,
    open:node.open,selected:state?.selectedSessionId??null,panel:state?.view?.panel??null}));
  };
  const visit=(node,callback)=>{
   if(!(node instanceof Element))return;
   if(node.matches('details'))callback(node);
   for(const child of node.querySelectorAll('details'))callback(child);
   const parent=node.closest('details');if(parent&&parent!==node)callback(parent);
  };
  new MutationObserver(records=>{
   for(const record of records){
    for(const node of record.removedNodes)visit(node,details=>{const entry=known.get(details);if(entry?.mounted&&!details.isConnected){entry.mounted=false;emit('unmount',details)}});
    for(const node of record.addedNodes)visit(node,details=>{const entry=identify(details);if(entry&&!entry.mounted&&details.isConnected){entry.mounted=true;emit('mount',details)}});
   }
  }).observe(document,{subtree:true,childList:true});
  document.addEventListener('click',event=>{const summary=event.target.closest?.('summary');if(summary)emit('click',summary.parentElement)},true);
  document.addEventListener('toggle',event=>emit('toggle',event.target),true);
 },{prefix});
 return ()=>({limit,dropped,events:[...events]});
}
