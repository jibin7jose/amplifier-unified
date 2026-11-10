import React from 'react';
import {GitBranch,ChevronRight,ChevronLeft} from 'lucide-react';
import {subagentCount,subagentPage} from './chat-navigation';

export function SubagentHistoryButton({state,session,act}){
 const count=subagentCount(state,session);
 if(!count)return null;
 return <button type="button" className="a-soft" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:'subagent-history',subagentHistory:{sessionId:session.id,filter:'',index:0}}})}><GitBranch/>Subagent history ({count})</button>;
}
export function SubagentHistory({state,session,act}){
 const saved=state.view?.subagentHistory||{},parent=state.sessions?.find(row=>row.id===saved.sessionId)||session;
 const filter=saved.filter||'',page=subagentPage(state,parent),{pages,index}=page;
 const update=patch=>act('view.update',{patch:{subagentHistory:{sessionId:parent?.id,filter,index,...patch}}});
 const choose=async id=>{const result=await act('session.select',{id});if(result&&result.accepted!==false)await act('view.update',{patch:{panel:null}})};
 return <div data-part="subagent-history">
  <p>Saved subagent conversations from {parent?.title||'this conversation'}. Open one to read its work.</p>
  <label htmlFor="subagent-history-filter">Find subagent conversations</label><input id="subagent-history-filter" type="search" value={filter} placeholder="Search or pattern, e.g. *research*" data-action="view.update" onChange={event=>update({filter:event.target.value,index:0})}/>
  <p className="a-caption" role="status">{page.pending?'Loading subagent conversations…':`${page.total} of ${page.unfilteredTotal} subagent conversations`}</p>
  <div className="a-subagent-history-list" aria-busy={!!page.pending}>{page.items.map(row=><button key={row.id} type="button" className="a-soft" data-action="session.select" onClick={()=>choose(row.id)} title={row.description||row.nativeIdentity||row.id}><GitBranch/><span>{row.title||'Subagent conversation'}</span><ChevronRight/></button>)}</div>
  {!page.total&&!page.pending&&<p>{filter?'No matching subagent conversations.':'No saved subagent conversations for this chat.'}</p>}
  {pages>1&&<div className="a-dialog-actions"><button type="button" className="a-soft" data-action="view.update" disabled={index===0} onClick={()=>update({index:index-1})}><ChevronLeft/>Previous subagents</button><span className="a-caption">{page.start+1}–{page.end} of {page.total}</span><button type="button" className="a-soft" data-action="view.update" disabled={index===pages-1} onClick={()=>update({index:index+1})}>More subagents<ChevronRight/></button></div>}
 </div>;
}
