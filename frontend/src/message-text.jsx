import React,{memo,useMemo} from 'react';
import {DetailText} from './conversation-detail';

// One text/detail/Markdown owner for ordinary and attributed incoming messages.
// Origin-specific controls and provenance stay with their enclosing message.
export const MessageText=memo(function MessageText({text,reference,role,sessionId,messageId,workspace,act,displayOnly}){
 const writingContext=useMemo(()=>role==='assistant'&&!displayOnly?{sessionId,messageId,act}:undefined,[role,sessionId,messageId,act,displayOnly]);
 const fileContext=useMemo(()=>role==='assistant'?{sessionId,workspace,act}:undefined,[role,sessionId,workspace,act]);
 return <DetailText text={text} reference={reference} markdown userContent={role==='user'} automatic={role==='assistant'} writingContext={writingContext} fileContext={fileContext}/>;
});