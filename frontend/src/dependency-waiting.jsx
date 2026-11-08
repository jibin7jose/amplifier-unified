import React from 'react';
import {Clock3} from 'lucide-react';
import './dependency-waiting.css';

// Host-derived request relationships only. Expanding is private presentation;
// following a link uses the normal selection action and never sends a draft.
export function DependencyWaiting({activity,sessionId,act}){
 const waiting=activity?.sessionId===sessionId&&activity?.kind==='waiting'&&activity.waiting;
 if(!waiting?.targets?.length)return null;
 const link=row=><>{row.available===false?<span>{row.title} (unavailable)</span>:<button type="button" className="a-link" data-action="session.select" onClick={()=>act('session.select',{id:row.sessionId})}>{row.title}</button>}{['error','failed','stopped','cancelled','interrupted','paused'].includes(row.status)&&<span> — {row.status}</span>}</>;
 return <section key={sessionId} className="a-dependency-waiting" aria-label="Waiting for other chats">
  <Clock3 aria-hidden="true"/>
  {waiting.targets.length===1&&!waiting.hasMore?<span>Waiting for {link(waiting.targets[0])}</span>:<details>
   <summary>{waiting.hasMore?'Waiting for other chats':`Waiting for ${waiting.targets.length} chats`}</summary>
   <ul>{waiting.targets.map(row=><li key={row.sessionId}>{link(row)}</li>)}</ul>
   {waiting.hasMore&&<p>Showing the first saved waits. More pending requests are not shown.</p>}
  </details>}
 </section>;
}