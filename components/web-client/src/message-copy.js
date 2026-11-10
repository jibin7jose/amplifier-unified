import {readDetail} from './detail-read.js';
// Browser navigation carries only visible catalog pages. Copy is still allowed
// for an explicitly addressed message without switching the shared selection.
export async function messageTextForCopy(state,effect,request){
 const find=snapshot=>snapshot?.sessions?.find(session=>session.id===effect.sessionId)?.messages?.find(message=>message.id===effect.messageId);
 let message=find(state);
 if(!message){
  const scoped=await request('/api/state?sessionId='+encodeURIComponent(effect.sessionId));
  message=find(scoped.state||scoped);
 }
 if(!message)throw Error('Message no longer available');
 const text=message.textDetail?await readDetail(message.textDetail,request):message.text||'';
 return effect.chatReferenceText?`${text}\n\n${effect.chatReferenceText}`:text;
}
