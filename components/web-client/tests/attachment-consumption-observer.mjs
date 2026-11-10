// Browser-only test sensor. Never edits the product DOM, outbox, or draft state.
// Inject with addInitScript before navigation; detector counterchecks are separate.
export function installAttachmentObserver() {
 const selector='.a-composer';
 const rules=new Map();
 const events=[],violations=[];
 let capped=false,frame=0,raf,observer,last='',stopped=false,previousForm,previousPane;
 const idOf=value=>/\/api\/attachments\/([a-f0-9]{32})(?:[?#]|$)/.exec(value||'')?.[1];
 const push=row=>{if(events.length<6000)events.push({at:performance.now(),frame,...row});else capped=true;};
 function ids(node){
  if(node?.nodeType!==1)return [];
  const found=new Set();
  for(const el of [node,...node.querySelectorAll('[href],[src]')]){
   const id=idOf(el.getAttribute('href'))||idOf(el.getAttribute('src'));
   if(id)found.add(id);
  }
  return [...found];
 }
 function view(){
  const state=window.amplifier?.getState?.();
  const owner=state?.selectedSessionId??null;
  const session=state?.sessions?.find(row=>row.id===owner);
  const form=document.querySelector(selector);
  let outbox=[];try{outbox=JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]')}catch{}
  return {owner,client:state?.client?.id,host:state?.client?.hostInstanceId,
   revision:state?.revision,status:session?.status,
   sourceIds:(session?.draftAttachments||state?.draftAttachments||[]).map(row=>row.id),
   domIds:form?ids(form):[],
   outbox:outbox.map(row=>({id:row.commandId,owner:row.sessionId,status:row.status,ids:row.attachmentIds})),
   accepted:(session?.messages||[]).filter(row=>row.delivery?.status==='accepted').map(row=>row.inputId),
   editable:form?.querySelector('[role="textbox"]')?.getAttribute('contenteditable'),
   sendDisabled:form?.querySelector('[data-action="conversation.send"]')?.disabled,
   textLength:state?.view?.draft?.length||0};
 }
 function forbidden(list,kind,owner){
  for(const id of new Set(list))if(rules.get(id)?.armed&&rules.get(id).owner===owner){
   const row={id,kind,owner,at:performance.now(),frame};
   if(violations.length<100)violations.push(row);else capped=true;
   push({kind:'forbidden-reappearance',...row});
  }
 }
 function armAbsent(owner,list){
  for(const [id,rule] of rules)if(!rule.armed&&owner===rule.owner&&!list.includes(id)){
   rule.armed=true;push({kind:'optimistic-clear-observed',id,owner:rule.owner});
  }
 }
 function sample(kind){
  const state=view(),form=document.querySelector(selector),pane=document.querySelector('[data-part="messages"]');
  // The rendered pane, not optimistic navigation, owns composer observations.
  const owner=pane?.getAttribute('data-session-id')??null;
  if(form&&pane){armAbsent(owner,state.domIds);forbidden(state.domIds,kind,owner);}
  previousForm=form;previousPane=pane;
  const signature=JSON.stringify(state);
  if(signature!==last||kind==='checkpoint'){push({kind,state});last=signature;}
 }
 const mutations=records=>{
  if(!records.length)return;
  if(records.length>6000){capped=true;return;}
  // MutationRecord nodes are live references. Reconstruct this batch backwards
  // in local maps, then replay it forwards; never mutate the DOM. This retains
  // an uncached link inside a wrapper emptied before observer delivery.
  const children=new Map(),parents=new Map(),attrs=new Map();
  const forms=new Set([previousForm,document.querySelector(selector)].filter(Boolean));
  const panes=new Set([previousPane,document.querySelector('[data-part="messages"]')].filter(Boolean));
  const kids=node=>{if(!children.has(node))children.set(node,[...node.childNodes]);return children.get(node);};
  const parent=node=>parents.has(node)?parents.get(node):node.parentNode;
  const attribute=(node,key)=>attrs.get(node)?.has(key)?attrs.get(node).get(key):node.getAttribute?.(key);
  const setAttribute=(node,key,value)=>{if(!attrs.has(node))attrs.set(node,new Map());attrs.get(node).set(key,value);};
  function replaceChildren(record,remove,add){
   const list=kids(record.target);
   for(const node of remove){const at=list.indexOf(node);if(at>=0)list.splice(at,1);parents.set(node,null);}
   let at=record.nextSibling?list.indexOf(record.nextSibling):-1;
   if(at<0)at=record.previousSibling?list.indexOf(record.previousSibling)+1:0;
   list.splice(at,0,...add);
   for(const node of add)parents.set(node,record.target);
  }
  const alive=node=>{const seen=new Set();while(node&&!seen.has(node)){if(node===document)return true;seen.add(node);node=parent(node);}return false;};
  const descendants=node=>{const result=[],pending=[node],seen=new Set();while(pending.length){const current=pending.pop();if(seen.has(current))continue;seen.add(current);result.push(current);pending.push(...kids(current));}return result;};
  for(const record of records)if(record.type==='childList'){
   kids(record.target);
   for(const node of [...record.addedNodes,...record.removedNodes]){
    if(node.nodeType!==1)continue;
    for(const el of [node,...node.querySelectorAll('.a-composer,[data-part="messages"]')]){
     if(el.matches(selector))forms.add(el);
     if(el.matches('[data-part="messages"]'))panes.add(el);
    }
   }
  }
  const afterValues=new Map();
  for(let i=records.length-1;i>=0;i--){
   const record=records[i];
   if(record.type==='childList')replaceChildren(record,[...record.addedNodes],[...record.removedNodes]);
   else {afterValues.set(i,attribute(record.target,record.attributeName));setAttribute(record.target,record.attributeName,record.oldValue);}
  }
  const memberships=()=>{
   const pane=[...panes].find(alive);
   if(!pane)return [];
   const owner=attribute(pane,'data-session-id')??null;
   return [...forms].filter(alive).map(form=>({owner,ids:[...new Set(descendants(form).flatMap(node=>
    [idOf(attribute(node,'href')),idOf(attribute(node,'src'))].filter(Boolean)))]}));
  };
  let before=memberships();
  for(let i=0;i<records.length;i++){
   const record=records[i];
   if(record.type==='childList')replaceChildren(record,[...record.removedNodes],[...record.addedNodes]);
   else setAttribute(record.target,record.attributeName,afterValues.get(i));
   const after=memberships();
   for(const row of after){
    const old=before.find(x=>x.owner===row.owner)?.ids||[];
    const removed=old.filter(id=>!row.ids.includes(id)),added=row.ids.filter(id=>!old.includes(id));
    // Arm at the removal boundary, before a later record in this SAME batch.
    armAbsent(row.owner,row.ids);
    if(added.length||removed.length)push({kind:'composer-mutation',owner:row.owner,added,removed});
    forbidden(added,record.type==='childList'?'added-node':'attribute',row.owner);
   }
   before=after;
  }
  sample('mutation-boundary');
 };
 const tick=()=>{if(stopped)return;frame++;if(frame>18000){capped=true;return;}sample('animation-frame');raf=requestAnimationFrame(tick);};
 const start=()=>{if(observer)return;observer=new MutationObserver(mutations);observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeOldValue:true,attributeFilter:['src','href','disabled','contenteditable','data-session-id']});raf=requestAnimationFrame(tick);};
 window.attachmentTrace={
  watch(owner,list,alreadyConsumed=false){if(observer)mutations(observer.takeRecords());sample('checkpoint');for(const id of list){if(rules.has(id))throw Error('Duplicate watch identity');rules.set(id,{owner,armed:alreadyConsumed});}push({kind:'watch',owner,ids:list,alreadyConsumed});},
  read(){if(observer)mutations(observer.takeRecords());sample('checkpoint');return {events:events.slice(),violations:violations.slice(),rules:[...rules].map(([id,rule])=>({id,...rule})),capped,frame};},
  stop(){if(observer)mutations(observer.takeRecords());sample('checkpoint');stopped=true;observer?.disconnect();cancelAnimationFrame(raf);return this.read();},
 };
 if(document.documentElement)start();else document.addEventListener('DOMContentLoaded',start,{once:true});
}
