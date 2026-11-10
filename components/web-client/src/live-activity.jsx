import React,{useEffect,useState} from 'react';
import {liveActivity} from './activity';
import {durationHint} from './duration-hint';

// Elapsed time belongs to this display, not to the transcript's parent.
export function LiveActivity({session}){
 const [now,setNow]=useState(Date.now);
 const live=liveActivity(session,now),hint=durationHint(session,now);
 useEffect(()=>{
  if(!live)return;
  const tick=()=>{if(!document.hidden)setNow(Date.now())};
  const timer=setInterval(tick,1000);
  document.addEventListener('visibilitychange',tick);
  return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',tick)};
 },[!!live,session?.id]);
 if(!live)return null;
 return <div className="a-live-activity" data-part="activity" data-view-source="activity" role="status" aria-live="polite"><span className="a-activity-pulse" aria-hidden="true"><b/><b/><b/></span><div><strong>{live.label}</strong>{(live.toolLabels.length>0||live.lastTool||live.workerCount>0)&&<small>{live.toolLabels.join(' · ')}{live.lastTool&&`Last tool: ${live.lastTool}`}{live.workerCount>0&&live.phase!=='workers'?`${live.toolLabels.length||live.lastTool?' · ':''}${live.workerCount} active ${live.workerCount===1?'worker':'workers'}`:''}</small>}{hint&&<small className="a-duration-hint" title={hint.detail}>{hint.text}</small>}</div>{live.elapsed&&<span className="a-activity-elapsed" role="timer" aria-live="off" aria-label={`Elapsed ${live.elapsed}`}>{live.elapsed}</span>}</div>;
}