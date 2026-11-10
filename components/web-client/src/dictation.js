// Browser dictation writes draft text only. It never creates or sends a chat.
export const DICTATION_KEY='amplifier.dictation.visibility';
export function dictationVisible(preference,nav,available){
 if(!available||preference==='hide')return false;
 if(preference==='show')return true;
 const mobile=/Android|iPhone|iPad|iPod/i.test(nav?.userAgent||'')||(/Mac/i.test(nav?.platform||'')&&nav?.maxTouchPoints>1);
 return !mobile;
}
export function appendDictation(draft,text){
 text=text.trim();if(!text)return draft;
 return draft+(draft&&!/\s$/.test(draft)&&! /^[,.;:!?]/.test(text)?' ':'')+text;
}
export class DraftDictation {
 constructor(Recognition,{onText,onState,onError,language}){Object.assign(this,{Recognition,onText,onState,onError,language});this.current=null}
 start(){
  if(this.current)return;
  let recognition;
  try{recognition=new this.Recognition()}catch{this.onError('Dictation could not start. Check your browser’s microphone settings.');return}
  const seen=new Set();this.current=recognition;
  recognition.lang=this.language||'en-US';recognition.continuous=true;recognition.interimResults=false;
  const current=()=>this.current===recognition;
  recognition.onresult=event=>{
   if(!current())return;
   for(let i=event.resultIndex;i<event.results.length;i++)if(event.results[i].isFinal&&!seen.has(i)){
    seen.add(i);this.onText(event.results[i][0].transcript);
   }
  };
  recognition.onend=()=>{if(current()){this.current=null;this.onState(false)}};
  recognition.onerror=event=>{
   if(!current())return;
   this.cancel();
   this.onError(({ 'not-allowed':'Allow microphone access in your browser to dictate.', 'service-not-allowed':'Dictation is unavailable in this browser. You can keep typing.', 'audio-capture':'No microphone is available.', 'network':'Dictation could not connect. Try again or keep typing.', 'no-speech':'No speech was heard. Try again.' })[event.error]||'Dictation stopped. Your draft is kept.');
  };
  try{this.onState(true);recognition.start()}catch{this.cancel();this.onError('Dictation could not start. Check your browser’s microphone settings.')}
 }
 stop(){try{this.current?.stop()}catch{this.cancel()}}
 cancel(){const recognition=this.current;this.current=null;if(recognition){recognition.onresult=recognition.onerror=recognition.onend=null;try{recognition.abort()}catch{}this.onState(false)}}
}
