import {useCallback,useEffect,useRef,useState} from 'react';

const clamp=(value,low,high)=>Math.max(low,Math.min(high,value));
const position=view=>({zoom:view.zoom||1,panX:view.panX||0,panY:view.panY||0});

// Gestures render locally. Persist one final view, never a request per pointer
// event. The caller keys this hook's owner to the exact displayed artifact.
export function useDiagramNavigation(view,save,enabled){
 const [value,setValue]=useState(()=>position(view)),[stage,setStage]=useState(null),[size,setSize]=useState({width:0,height:0}),[error,setError]=useState('');
 const current=useRef(value),drag=useRef(null),wheelTimer=useRef(null),frame=useRef(null),sequence=useRef(0),saving=useRef(false),alive=useRef(true),saveRef=useRef(save);saveRef.current=save;
 const paint=next=>{current.current=next;if(frame.current===null)frame.current=requestAnimationFrame(()=>{frame.current=null;setValue(current.current)})};
 const persist=async next=>{
  const own=++sequence.current;saving.current=true;setError('');
  try{await saveRef.current(next)}catch{if(alive.current&&own===sequence.current)setError('Could not save the diagram view. Try the view controls again.')}
  finally{if(own===sequence.current)saving.current=false}
 };
 useEffect(()=>{if(!drag.current&&!wheelTimer.current&&!saving.current){current.current=position(view);setValue(current.current)}},[view.zoom,view.panX,view.panY]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;clearTimeout(wheelTimer.current);if(frame.current!==null)cancelAnimationFrame(frame.current)}},[]);
 useEffect(()=>{
  if(!stage||!enabled)return;
  const measure=()=>setSize({width:Math.max(1,stage.clientWidth-24),height:Math.max(1,stage.clientHeight-24)});
  measure();const observer=new ResizeObserver(measure);observer.observe(stage);return()=>observer.disconnect();
 },[stage,enabled]);
 const change=patch=>{clearTimeout(wheelTimer.current);wheelTimer.current=null;const next={...current.current,...patch};paint(next);persist(next)};
 const zoomAt=(zoom,x=0,y=0)=>{
  const before=current.current,nextZoom=clamp(zoom,.2,4),ratio=nextZoom/before.zoom;
  return {zoom:nextZoom,panX:clamp(x-(x-before.panX)*ratio,-10000,10000),panY:clamp(y-(y-before.panY)*ratio,-10000,10000)};
 };
 useEffect(()=>{
  if(!stage||!enabled)return;
  const wheel=event=>{
   if(drag.current||!event.deltaY)return;
   event.preventDefault();const box=stage.getBoundingClientRect();
   const pixels=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?box.height:1);
   paint(zoomAt(current.current.zoom*Math.exp(-clamp(pixels,-500,500)*.002),event.clientX-box.x-box.width/2,event.clientY-box.y-box.height/2));
   clearTimeout(wheelTimer.current);wheelTimer.current=setTimeout(()=>{wheelTimer.current=null;persist(current.current)},180);
  };
  stage.addEventListener('wheel',wheel,{passive:false});return()=>stage.removeEventListener('wheel',wheel);
 },[stage,enabled]);
 const move=event=>{const start=drag.current;if(!start||event.pointerId!==start.pointerId)return;paint({...start.value,panX:clamp(start.value.panX+event.clientX-start.x,-10000,10000),panY:clamp(start.value.panY+event.clientY-start.y,-10000,10000)})};
 const cancel=event=>{if(drag.current?.pointerId!==event.pointerId)return;const start=drag.current;drag.current=null;paint(start.value);if(start.pendingWheel)persist(start.value)};
 const events=enabled?{
  onPointerDown:event=>{if(event.button!==0||drag.current)return;const pendingWheel=wheelTimer.current!==null;clearTimeout(wheelTimer.current);wheelTimer.current=null;drag.current={pointerId:event.pointerId,x:event.clientX,y:event.clientY,value:current.current,pendingWheel};event.currentTarget.setPointerCapture(event.pointerId)},
  onPointerMove:move,
  onPointerUp:event=>{if(drag.current?.pointerId!==event.pointerId)return;move(event);drag.current=null;event.currentTarget.releasePointerCapture(event.pointerId);persist(current.current)},
  onPointerCancel:cancel,onLostPointerCapture:cancel,
  onKeyDown:event=>{const moves={ArrowLeft:[-30,0],ArrowRight:[30,0],ArrowUp:[0,-30],ArrowDown:[0,30]};
   if(moves[event.key]){event.preventDefault();change({panX:clamp(current.current.panX+moves[event.key][0],-10000,10000),panY:clamp(current.current.panY+moves[event.key][1],-10000,10000)})}
   else if(['+','=','-','0'].includes(event.key)){event.preventDefault();change(event.key==='0'?{zoom:1,panX:0,panY:0}:zoomAt(current.current.zoom*(event.key==='-'?1/1.2:1.2)))}}
 }:{};
 return {value,size,error,ref:useCallback(node=>setStage(node),[]),events,fit:()=>change({zoom:1,panX:0,panY:0}),zoom:factor=>change(zoomAt(current.current.zoom*factor))};
}
