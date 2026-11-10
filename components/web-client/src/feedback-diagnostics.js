import {validTitlebarRect} from './window-controls-overlay.js';
export const frontendBuild=typeof __AMPLIFIER_BUILD__==='undefined'?{version:'dev',id:'dev'}:__AMPLIFIER_BUILD__;
function windowChromeFacts(environment){
 let windowControlsOverlay='unsupported';
 try{
  const overlay=environment.navigator?.windowControlsOverlay;
  if(overlay)windowControlsOverlay=overlay.visible!==true?'hidden':
   validTitlebarRect(overlay.getTitlebarAreaRect(),environment.innerWidth,environment.innerHeight)?'visible':'invalid-geometry';
 }catch{windowControlsOverlay='unavailable'}
 const root=environment.document?.getElementById?.('amp-one');
 const windowChromeLayout=root?.getAttribute('data-window-controls-overlay')==='true'?
  (root.getAttribute('data-window-controls-overlay-stacked')==='true'?'stacked':'integrated'):'standard';
 return {windowControlsOverlay,windowChromeLayout};
}
const pending=new Map();
let eventStream='unknown';
export function setFeedbackEventStream(value){eventStream=['connecting','open','reconnecting','closed'].includes(value)?value:'unknown'}
export function trackAction(){const id=Symbol();pending.set(id,Date.now());return()=>pending.delete(id)}
export function feedbackDiagnostics(state,environment=globalThis){
 const nav=environment.navigator||{},ua=nav.userAgent||'',match=ua.match(/(Edg)\/([0-9.]+)/)||ua.match(/(Firefox)\/([0-9.]+)/)||ua.match(/(Chrome)\/([0-9.]+)/)||ua.match(/(Version)\/([0-9.]+)/);
 const dark=!!environment.matchMedia?.('(prefers-color-scheme: dark)').matches;
 const appearance=['light','dark','system'].includes(state?.view?.scheme)?state.view.scheme:'system';
 return {frontendVersion:frontendBuild.version,frontendBuild:frontendBuild.id,...windowChromeFacts(environment),
  browser:match?({Edg:'Edge',Firefox:'Firefox',Chrome:'Chrome',Version:'Safari'}[match[1]]):'Other',browserVersion:match?.[2]||'',
  deviceOS:/Android/.test(ua)?'Android':/iPhone|iPad|iPod/.test(ua)?'iOS':/Windows/.test(ua)?'Windows':/Macintosh/.test(ua)?'macOS':/Linux/.test(ua)?'Linux':'Other',
  width:Math.min(32768,environment.innerWidth||0),height:Math.min(32768,environment.innerHeight||0),pixelRatio:Math.min(16,environment.devicePixelRatio||1),
  colorPreference:dark?'dark':'light',appearance,resolvedAppearance:appearance==='system'?(dark?'dark':'light'):appearance,
  standalone:!!(environment.matchMedia?.('(display-mode: standalone)').matches||environment.matchMedia?.('(display-mode: window-controls-overlay)').matches||nav.standalone),
  secureContext:!!environment.isSecureContext,online:nav.onLine!==false,eventStream,serviceWorkerControlled:!!nav.serviceWorker?.controller,
  reducedMotion:!!environment.matchMedia?.('(prefers-reduced-motion: reduce)').matches,visible:environment.document?.visibilityState!=='hidden',
  pageAgeSeconds:Math.min(31536000,Math.floor((environment.performance?.now()||0)/1000)),
  pendingActions:pending.size,oldestPendingMs:pending.size?Date.now()-Math.min(...pending.values()):0};
}
