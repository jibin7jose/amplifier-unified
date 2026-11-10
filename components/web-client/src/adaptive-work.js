import {workGroups} from './work-status.js';
const workKinds=Object.keys(workGroups);
export const workViews=['focus','brief','studio'];
export function startingWorkView(presentation={}){
 return workViews.includes(presentation.workStartView)?presentation.workStartView:
  ({minimal:'focus',standard:'brief',detailed:'studio'}[presentation.interfaceDetail||presentation.executionDetail]||'brief');
}
export function homePatch(presentation={},extra={},view={}){
 return {workSurface:'home',workHomeView:view.workHomeView||startingWorkView(presentation),workHomeFilter:view.workHomeFilter||'all',
  navExpanded:view.navExpanded??false,navPinned:view.navPinned??((presentation.interfaceDetail||presentation.executionDetail)==='detailed'),...extra};
}

// Like chat reading positions, Home's scroll stays in this browser tab. Wait for
// the matching resource before restoring; a loading placeholder has no height.
export function workReadingKey(view,mode){
 return JSON.stringify([mode,view.workHomeScope||'all',view.workHomeQuery||'',view.workHomeFilter||'all',
  ...workKinds.map(key=>view.workHomePages?.[key]||0)]);
}
export function rememberHomeScroll(pane,key,storageKey='amplifier.work-home-reading.v1'){
 const browser=pane.ownerDocument.defaultView;
 let storage,saved,timer;
 try{storage=browser.sessionStorage;saved=JSON.parse(storage.getItem(storageKey)||'null')}catch{}
 pane.scrollTop=saved?.key===key&&Number.isFinite(saved.top)?Math.max(0,saved.top):0;
 let top=pane.scrollTop;
 const persist=()=>{browser.clearTimeout(timer);try{storage?.setItem(storageKey,JSON.stringify({key,top}))}catch{}};
 const scroll=()=>{top=pane.scrollTop;browser.clearTimeout(timer);timer=browser.setTimeout(persist,200)};
 pane.addEventListener('scroll',scroll,{passive:true});browser.addEventListener('pagehide',persist);
 return ()=>{persist();pane.removeEventListener('scroll',scroll);browser.removeEventListener('pagehide',persist)};
}
export function currentWorkPage(value,view={},pinnedScope=null){
 return value?.scope===(pinnedScope||view.workHomeScope||'all')
  &&value?.requestedScope===(view.workHomeScope||'all')&&value?.query===(view.workHomeQuery||'')
  &&workKinds.every(key=>(value.requestedPages?.[key]||0)===(view.workHomePages?.[key]||0));
}
export function currentWorkContext(value,view={},pinnedScope=null){
 return value?.scope===(pinnedScope||view.workHomeScope||'all')
  &&value?.requestedScope===(view.workHomeScope||'all')?value:null;
}

// A Focus pass is a tab-local reading position, never a shared skip/read state.
const focusStorageKey='amplifier.work-home-focus.v1';
export function readFocusCursor(browser){
 try{
  const value=JSON.parse(browser.sessionStorage.getItem(focusStorageKey));
  return value&&typeof value.key==='string'&&workKinds.includes(value.kind)
   &&Number.isSafeInteger(value.page)&&value.page>=0&&Number.isSafeInteger(value.index)&&value.index>=-1
   &&(value.id===null||typeof value.id==='string')?value:null;
 }catch{return null}
}
export function saveFocusCursor(browser,value){
 try{browser.sessionStorage.setItem(focusStorageKey,JSON.stringify(value))}catch{}
}
export function focusPosition(page,cursor){
 if(!page?.items?.length)return null;
 const saved=cursor?.page===page.index?cursor:null;
 const found=saved?.id?page.items.findIndex(chat=>chat.id===saved.id):-1;
 const index=found>=0?found:Math.min(page.items.length-1,Math.max(0,saved?.index===-1?page.items.length-1:saved?.index||0));
 return {page:page.index,index,id:page.items[index].id};
}
export function stepFocus(page,position,direction){
 if(!position)return null;
 const index=position.index+direction;
 if(index>=0&&index<page.items.length)return {page:page.index,index,id:page.items[index].id};
 if(direction>0&&page.index+1<page.pages)return {page:page.index+1,index:0,id:null};
 if(direction<0&&page.index>0)return {page:page.index-1,index:-1,id:null};
 return null;
}
