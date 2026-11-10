import React,{useEffect,useRef,useState} from 'react';
import {Mic,Square} from 'lucide-react';
import {DICTATION_KEY,dictationVisible,appendDictation,DraftDictation} from './dictation.js';
function preference(){try{const value=localStorage.getItem(DICTATION_KEY);return ['show','hide'].includes(value)?value:'auto'}catch{return 'auto'}}
function usePreference(){
 const [value,setValue]=useState(preference),[error,setError]=useState('');
 useEffect(()=>{const sync=()=>setValue(preference());window.addEventListener('storage',sync);window.addEventListener('dictation-preference',sync);return()=>{window.removeEventListener('storage',sync);window.removeEventListener('dictation-preference',sync)}},[]);
 return [value,next=>{try{localStorage.setItem(DICTATION_KEY,next)}catch{setError('Could not save this preference. Allow browser storage and try again.');return}setError('');setValue(next);window.dispatchEvent(new Event('dictation-preference'))},error];
}
const recognitionAPI=()=>window.SpeechRecognition||window.webkitSpeechRecognition;
export function DictationSettings(){
 const [value,setValue,error]=usePreference(),available=!!recognitionAPI();
 return <section className="a-dictation-settings"><h3>Dictation</h3><label htmlFor="dictation-visibility">Microphone in the message box</label><select id="dictation-visibility" value={value} onChange={e=>setValue(e.target.value)}><option value="auto">Automatic</option><option value="show">Show</option><option value="hide">Hide</option></select>{error&&<p role="alert">{error}</p>}<p className="a-caption">Applies to this browser. Automatic shows dictation on supported desktops and hides it on iOS and Android, where keyboards usually include it. Dictation adds text to your draft; you choose when to send.</p><p className="a-caption">{available?'Your browser handles speech recognition and may send audio to its speech service.':'This browser does not support speech recognition. You can use keyboard dictation where available.'}</p></section>;
}
export function DictationButton({draft,onChange,disabled,controlRef}){
 const [preference]=usePreference(),[listening,setListening]=useState(false),[error,setError]=useState(''),latest=useRef({draft,onChange});latest.current={draft,onChange};
 const controller=useRef(null),visible=dictationVisible(preference,navigator,!!recognitionAPI());
 useEffect(()=>{
  const Recognition=recognitionAPI();if(!Recognition)return;
  const control=new DraftDictation(Recognition,{language:navigator.language,onText:text=>{const value=appendDictation(latest.current.draft,text);latest.current.draft=value;latest.current.onChange(value)},onState:setListening,onError:setError});
  controller.current=control;controlRef.current=control;
  const cancel=()=>control.cancel();window.addEventListener('pagehide',cancel);
  return()=>{control.onState=()=>{};control.cancel();window.removeEventListener('pagehide',cancel);if(controlRef.current===control)controlRef.current=null;controller.current=null};
 },[controlRef]);
 useEffect(()=>{if(disabled||!visible)controller.current?.cancel()},[disabled,visible]);
 if(!visible)return null;
 return <div className="a-dictation"><button type="button" className="a-icon" aria-label={listening?'Stop dictation':'Dictate message'} aria-pressed={listening} disabled={disabled} title={listening||error?undefined:'Dictate into your draft'} onClick={()=>{setError('');listening?controller.current?.stop():controller.current?.start()}}>{listening?<Square/>:<Mic/>}</button>{(listening||error)&&<span className="a-dictation-feedback" role="status">{error||'Listening…'}</span>}</div>;
}
