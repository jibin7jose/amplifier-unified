// Interactive archive behavior only. The public archive action preserves selection.
export function archiveDestination(state,chat){
 const workspace=chat.location?.kind!=='managed'&&state.workspaces?.find(row=>row.id===chat.workspaceId||row.path===chat.workspace);
 return {workSurface:workspace?'workspace':'chats',workWorkspaceId:workspace?.id||null,workWorkspaceTab:'chats',panel:null,navExpanded:false};
}
export function archiveVisit(state,revision){
 return JSON.stringify([revision,state?.client?.hostInstanceId,state?.selectedSessionId,state?.selectedWorkspaceId,state?.view?.workSurface||'chat',state?.view?.workWorkspaceId,state?.view?.workWorkspaceTab,state?.view?.panel]);
}
export function createArchiveAction({getState,getRevision,dispatch,navigate,onChange}){
 let sequence=0,notice=null;
 const pending=new Set();
 const publish=()=>onChange({notice,pending:[...pending]});
 const update=(operation,value)=>{if(operation===sequence){notice={operation,...value};publish()}};
 async function run(chat,archive=true){
  // One interactive organization change at a time: never hide another target's
  // rejection or replace an in-flight archive's Undo with a later operation.
  const id=chat.id;if(pending.size)return;
  const operation=++sequence,state=getState(),visit=archiveVisit(state,getRevision());
  const leaving=archive&&state?.selectedSessionId===id&&(state?.view?.workSurface||'chat')==='chat';
  const destination=archiveDestination(state,chat),isActive=()=>operation===sequence,isCurrent=()=>isActive()&&archiveVisit(getState(),getRevision())===visit;
  pending.add(id);update(operation,{id,title:chat.title||'Untitled chat',phase:'pending',text:archive?'Archiving…':'Restoring…'});
  let receipt;
  try{
   receipt=await dispatch(archive?'session.archive':'session.restore',{id});
   if(receipt?.accepted===false){
    update(operation,{id,phase:'error',text:typeof receipt.error==='string'?receipt.error:receipt.error?.message||'The change was not saved.'});return;
   }
   if(receipt?.accepted!==true||receipt.result?.id!==id||receipt.result?.archived!==archive){
    update(operation,{id,phase:'unknown',text:'Could not confirm the change. Check Archived chats before trying again.'});return;
   }
  }catch(error){
   const rejected=error.status>=400&&error.status<500&&error.status!==408;
   update(operation,{id,phase:rejected?'error':'unknown',text:rejected?error.message:'Could not confirm the change. Check Archived chats before trying again.'});return;
  }finally{pending.delete(id);publish()}
  // The mutation is finished. Navigation must never hold Undo or other changes.
  const success={id,title:chat.title||'Untitled chat',phase:'success',archive,text:archive?'Chat archived':'Chat restored'};
  update(operation,success);
  if(leaving&&isCurrent()){
   // Navigation is a separate outcome: a failed browse must not claim archive failed.
   try{
    const result=await navigate(destination,isCurrent,isActive);
    if(result?.accepted===false)update(operation,{...success,text:'Chat archived. Your current view was kept.'});
   }catch{
    update(operation,{...success,text:'Chat archived. Could not open the chat list.'});
   }
  }
 }
 return {run,dismiss:()=>{sequence++;notice=null;publish()}};
}