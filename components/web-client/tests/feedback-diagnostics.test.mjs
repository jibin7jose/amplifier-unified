import test from 'node:test';
import assert from 'node:assert/strict';
import {feedbackDiagnostics,trackAction,setFeedbackEventStream} from '../src/feedback-diagnostics.js';

test('diagnostics recognize Edge ahead of its Chrome token and expose only selected facts',()=>{
 const env={navigator:{userAgent:'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/140.0.1 Safari/537.36 Edg/140.0.2 PRIVATE',onLine:true,serviceWorker:{controller:{secret:'PRIVATE'}}},innerWidth:1400,innerHeight:900,devicePixelRatio:2,isSecureContext:true,matchMedia:query=>({matches:query==='(prefers-color-scheme: dark)'}),performance:{now:()=>2000},document:{visibilityState:'visible'},location:{href:'PRIVATE'}};
 const facts=feedbackDiagnostics({view:{scheme:'system'},messages:['PRIVATE'],settings:{secret:'PRIVATE'}},env);
 assert.equal(facts.browser,'Edge');assert.equal(facts.browserVersion,'140.0.2');assert.equal(facts.deviceOS,'Windows');assert.equal(facts.resolvedAppearance,'dark');assert.equal(facts.serviceWorkerControlled,true);assert.equal(facts.pageAgeSeconds,2);assert.equal(JSON.stringify(facts).includes('PRIVATE'),false);
 const settle=trackAction();assert.equal(feedbackDiagnostics({},env).pendingActions,1);settle();assert.equal(feedbackDiagnostics({},env).pendingActions,0);
});


test('event stream reports its own lifecycle, independently of navigator connectivity',()=>{
 const environment={navigator:{onLine:true}};
 for(const value of ['connecting','open','reconnecting','closed']){
  setFeedbackEventStream(value);
  assert.equal(feedbackDiagnostics({},environment).eventStream,value);
  assert.equal(feedbackDiagnostics({},environment).online,true);
 }
 setFeedbackEventStream('PRIVATE arbitrary state');
 assert.equal(feedbackDiagnostics({},environment).eventStream,'unknown');
});

test('overlay display mode is an installed app, independent of overlay geometry',()=>{
 for(const mode of ['standalone','window-controls-overlay','browser']){
  const environment={navigator:{},matchMedia:query=>({matches:query===`(display-mode: ${mode})`})};
  assert.equal(feedbackDiagnostics({},environment).standalone,mode!=='browser');
 }
 assert.equal(feedbackDiagnostics({},{navigator:{standalone:true}}).standalone,true);
});

test('window diagnostics distinguish browser overlay state from app layout without collecting content',()=>{
 const environment={innerWidth:1000,innerHeight:800,navigator:{},document:{getElementById:()=>({getAttribute:key=>key==='data-window-controls-overlay'?'true':'false'})}};
 assert.equal(feedbackDiagnostics({},environment).windowControlsOverlay,'unsupported');
 const overlay={visible:false,getTitlebarAreaRect:()=>({x:80,y:0,width:920,height:32,private:'PRIVATE'})};
 environment.navigator.windowControlsOverlay=overlay;
 assert.equal(feedbackDiagnostics({},environment).windowControlsOverlay,'hidden');
 overlay.visible=true;
 const result=feedbackDiagnostics({},environment);
 assert.equal(result.windowControlsOverlay,'visible');assert.equal(result.windowChromeLayout,'integrated');
 assert.equal(JSON.stringify(result).includes('PRIVATE'),false);
 environment.innerWidth=500;assert.equal(feedbackDiagnostics({},environment).windowControlsOverlay,'invalid-geometry');
 overlay.getTitlebarAreaRect=()=>{throw Error('PRIVATE')};assert.equal(feedbackDiagnostics({},environment).windowControlsOverlay,'unavailable');
 environment.document={};assert.equal(feedbackDiagnostics({},environment).windowChromeLayout,'standard');
});
