// Only host provenance separates backend work from what the voice model said.
// Unknown historical rows remain ordinary messages; prose is never evidence.
export function isVoiceWorkMessage(message){
 return message?.role==='assistant'&&!message.voiceId&&
  ['amplifier','interrupted-stream'].includes(message.source)&&
  (message.via==='call'||message.inputId?.startsWith('voice:'));
}

export function voiceWorkLabel(message){
 const response=message.response;
 if(message.partial&&message.source==='interrupted-stream')return 'Amplifier work interrupted';
 return response?.version===1&&response.status==='completed'&&response.phase==='final'&&
  response.generationId===message.generationId&&response.finalMessageIds?.includes(message.id)?
  'Amplifier result':'Amplifier work update';
}
