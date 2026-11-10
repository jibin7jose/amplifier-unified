import {useEffect,useRef} from 'react';

const layers=[];

// Keep local form values mounted; outside dismissal only changes visibility.
export function useOutsideDismiss(open,ref,dismiss,extraRef){
 const callback=useRef(dismiss);callback.current=dismiss;
 useEffect(()=>{
  if(!open||!ref.current)return;
  let downOutside=false;const layer={};layers.push(layer);
  const outside=event=>{const path=event.composedPath();return !!ref.current&&!path.includes(ref.current)&&!(extraRef?.current&&path.includes(extraRef.current))};
  const down=event=>{downOutside=outside(event)};
  const click=event=>{if(downOutside&&outside(event))callback.current('outside')};
  const key=event=>{if(event.key==='Escape'&&layers.at(-1)===layer){event.preventDefault();event.stopImmediatePropagation();callback.current('escape')}};
  document.addEventListener('pointerdown',down,true);document.addEventListener('click',click,true);document.addEventListener('keydown',key,true);
  return()=>{layers.splice(layers.indexOf(layer),1);document.removeEventListener('pointerdown',down,true);document.removeEventListener('click',click,true);document.removeEventListener('keydown',key,true)};
 },[open,ref,extraRef]);
}
