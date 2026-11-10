import React from 'react';
import {LoaderCircle} from 'lucide-react';

export function WorkProjectionStatus({session,messages=session?.messages}){
 const progress=session?.workProjection;
 if(!progress||progress.status==='ready')return null;
 const retrying=progress.status==='retrying',unavailable=progress.status==='unavailable';
 if(!retrying&&!unavailable&&progress.status!=='loading')return null;
 // Routine projection refreshes are not a new history visit. Inserting even
 // an empty status row above readable text moves the conversation. The DOM
 // receipt still reports noncurrent Work; errors remain independently visible.
 const retained=!!session.id&&progress.receipt?.sessionId===session.id;
 if(progress.status==='loading'&&(messages?.length||session.streaming||retained))return null;
 return <p role="status" className="a-session-history" data-part="work-projection-status">
  {!unavailable&&<LoaderCircle className="a-progress-spinner" aria-hidden="true"/>}
  {unavailable?'Work details exceed the current loading limit. Your conversation is still available.':retrying?'Work details could not finish loading. Retrying shortly. You can keep reading this chat.':'Loading work details…'}
 </p>;
}
