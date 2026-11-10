export function notificationBody(message,settings={}){
 return settings.preview===true?(message.text?.slice(0,180)||'Your response is ready.'):'Your Amplifier response is ready.';
}
export function desktopNotificationsEnabled(settings={}){return settings.desktop!==false}

// Completed off-page conversations may no longer have message bodies in the
// browser snapshot. Their compact notification records survive that transition.
export function notificationMessages(state){
 if(Array.isArray(state.notificationMessages))return state.notificationMessages.filter(message=>message.id&&message.sessionId&&message.role==='assistant'&&message.via==='text');
 const messages=new Map();
 for(const session of state.sessions||[])for(const message of session.messages||[]){
  if(message.id&&message.role==='assistant'&&message.via==='text')messages.set(message.id,{...message,sessionId:session.id});
 }
 return [...messages.values()];
}
