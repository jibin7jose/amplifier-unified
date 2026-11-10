import {useNarrowScreen} from './responsive-navigation';
import React,{createContext,useContext,useLayoutEffect,useRef,useState} from 'react';

export const CHAT_MIN=360,CANVAS_MIN=300,NAV_MIN=216;
export function fitPanels({available=1200,gap=12,rail=0,navPinned=false,canvasOpen=false,navWidth=240,canvasWidth=440,priority='canvas'}={}){
 const overlay=canvasOpen&&available<CHAT_MIN+CANVAS_MIN+rail+gap*(rail?2:1);
 const reserveCanvas=canvasOpen&&!overlay;
 const docked=navPinned&&available>=CHAT_MIN+NAV_MIN+(reserveCanvas?CANVAS_MIN+gap*2:gap);
 const space=available-gap*((docked||rail?1:0)+(reserveCanvas?1:0))-CHAT_MIN;
 const navMax=Math.min(16384,Math.max(NAV_MIN,space-(reserveCanvas?CANVAS_MIN:0)));
 const canvasMax=Math.min(16384,Math.max(CANVAS_MIN,space-(docked?NAV_MIN:rail)));
 let nav=docked?Math.min(Math.max(NAV_MIN,navWidth),navMax):rail;
 let canvas=Math.min(Math.max(CANVAS_MIN,canvasWidth),canvasMax);
 if(reserveCanvas){
  if(priority==='nav')canvas=Math.max(CANVAS_MIN,Math.min(canvas,space-nav));
  else if(docked)nav=Math.max(NAV_MIN,Math.min(nav,space-canvas));
 }
 return {overlay,docked,nav,canvas,navMax,canvasMax,expandedNav:docked?nav:Math.min(Math.max(NAV_MIN,navWidth),Math.max(NAV_MIN,available-rail))};
}
export function showingOverview(state){
 if(state.peerPreview)return false;
 return !state.view?.settingsCanvas&&(!!state.view?.canvasDraft?.library||!state.canvasWorkspace?.views?.length||!!state.canvas?.placeholder);
}
const Layout=createContext(null);
export function WorkspaceLayout({state,act,children,presentation={},appearanceCss='',appearanceScheme=''}){
 const narrow=useNarrowScreen('(max-width: 900px)');
 const host=useRef(null),[metrics,setMetrics]=useState({available:1200,gap:12,rail:0}),[draft,setDraft]=useState(null);
 useLayoutEffect(()=>{
  const el=host.current;if(!el)return;
  const measure=()=>setMetrics({available:el.clientWidth,gap:parseFloat(getComputedStyle(el).columnGap)||0,rail:0});
  const observer=new ResizeObserver(measure);observer.observe(el);measure();return()=>observer.disconnect();
 },[appearanceCss,appearanceScheme,presentation.layout,state.view?.layout]);
 const view=state.view||{},canvasOnLeft=(presentation.layout||view.layout)==='work',overview=showingOverview(state),widthKey=overview?'canvasOverviewWidth':'canvasWidth',canvasWidth=view[widthKey]??(overview?320:(presentation.layout||view.layout)==='conversation'?440:Math.max(440,metrics.available*.5)),sizes=fitPanels({...metrics,navPinned:!narrow&&!!view.navPinned,canvasOpen:!!state.selectedSessionId&&!!state.canvas?.open,navWidth:view.navWidth,canvasWidth,...draft});
 const persist=(key,value)=>{
  const fitted=fitPanels({...metrics,navPinned:!narrow&&!!view.navPinned,canvasOpen:!!state.selectedSessionId&&!!state.canvas?.open,navWidth:view.navWidth,canvasWidth,[key]:value,priority:key==='navWidth'?'nav':'canvas'});
  const patch={[key==='canvasWidth'?widthKey:key]:Math.round(value)};
  if(fitted.docked&&state.canvas?.open&&!fitted.overlay){patch.navWidth=Math.round(fitted.nav);patch[widthKey]=Math.round(fitted.canvas)}
  setDraft(null);act('view.update',{patch});
 };
 return <Layout.Provider value={{...sizes,narrow,canvasOnLeft,preview:(key,value)=>setDraft({[key]:value,priority:key==='navWidth'?'nav':'canvas'}),cancel:()=>setDraft(null),persist}}>
  <main ref={host} className="a-layout" data-part="workspace" data-narrow={narrow} data-canvas-open={!!state.selectedSessionId&&!!state.canvas?.open} data-pane-layout="" data-canvas-mode={overview?'overview':'artifact'} data-canvas-overlay={sizes.overlay} style={{'--nav-width':sizes.expandedNav+'px','--canvas-width':sizes.canvas+'px','--chat-min':CHAT_MIN+'px'}}>{children}</main>
 </Layout.Provider>;
}
export function usePanelLayout(state,act){
 const context=useContext(Layout);
 return context||{...fitPanels({navPinned:state.view?.navPinned,canvasOpen:!!state.selectedSessionId&&state.canvas?.open,navWidth:state.view?.navWidth,canvasWidth:state.view?.canvasWidth}),preview:()=>{},cancel:()=>{},persist:(key,value)=>act('view.update',{patch:{[key]:value}})};
}
export function PaneResizer({layout,pane}){
 const drag=useRef(null),nav=pane==='nav',key=nav?'navWidth':'canvasWidth',width=nav?layout.nav:layout.canvas,min=nav?NAV_MIN:CANVAS_MIN,max=nav?layout.navMax:layout.canvasMax;
 const clamp=value=>Math.round(Math.max(min,Math.min(max,value)));
 const direction=nav||layout.canvasOnLeft?1:-1;
 const value=e=>clamp(drag.current.width+(e.clientX-drag.current.x)*direction);
 return <div className={nav?'a-nav-resize':'a-canvas-resize'} role="separator" aria-label={nav?'Resize navigation':'Resize canvas'} aria-orientation="vertical" aria-valuemin={min} aria-valuemax={Math.floor(max)} aria-valuenow={Math.round(width)} tabIndex={0} data-action="view.update"
  onPointerDown={e=>{e.preventDefault();drag.current={x:e.clientX,width};e.currentTarget.setPointerCapture(e.pointerId)}}
  onPointerMove={e=>{if(drag.current)layout.preview(key,value(e))}}
  onPointerUp={e=>{if(drag.current){const next=value(e);drag.current=null;layout.persist(key,next)}}}
  onPointerCancel={()=>{drag.current=null;layout.cancel()}}
  onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();layout.persist(key,e.key==='Home'?min:e.key==='End'?Math.floor(max):clamp(width+(e.key==='ArrowLeft'?-20:20)*direction))}}}/>;
}
