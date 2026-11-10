import React,{createContext,useContext,useEffect,useState} from 'react';
const Plans=createContext({});
export function WorkspacePlans({ids,act,children}){
 const [plans,setPlans]=useState({}),key=JSON.stringify(ids);
 useEffect(()=>{
  if(!act||!ids.length){setPlans({});return}
  let live=true,timer;
  async function refresh(){
   if(!document.hidden){try{const result=await act('session.plans',{ids},{feedback:false});if(live)setPlans(Object.fromEntries((result?.result?.items||[]).map(row=>[row.sessionId,row.plan])))}catch{if(live)setPlans(previous=>Object.fromEntries(Object.keys(previous).map(id=>[id,{unavailable:true,readFailed:true}])))}}
   if(live)timer=setTimeout(refresh,15000);
  }
  refresh();return()=>{live=false;clearTimeout(timer)};
 },[key,act]);
 return <Plans.Provider value={{plans,act}}>{children}</Plans.Provider>;
}
export function WorkspacePlan({id}){
 const {plans,act}=useContext(Plans),plan=plans?.[id], [items,setItems]=useState(null),[error,setError]=useState(''),[open,setOpen]=useState(false);
 useEffect(()=>{
  let live=true;setItems(null);setError('');
  if(open&&plan&&!plan.unavailable){
   act('session.plans',{ids:[id],includeItems:true},{feedback:false}).then(result=>{
    if(live)setItems(result?.result?.items?.[0]?.plan?.items||[]);
   }).catch(()=>{if(live)setError('The plan could not be read. Open the chat to inspect it.')});
  }
  return()=>{live=false};
 },[id,plan?.id,open,act,plan?.unavailable]);
 if(!plan)return null;
 const history=plan.history||[],points=history.map((p,i)=>`${history.length===1?0:i*96/(history.length-1)},${28-(p.total?p.done/p.total:0)*24}`).join(' ');
 return <details className="a-workspace-plan" aria-label="Chat plan progress" open={open} onToggle={event=>setOpen(event.currentTarget.open)}>
  <summary><span>{plan.unavailable?'Plan unavailable':`${plan.done} of ${plan.total} steps done`}</span>{!plan.unavailable&&<progress max={Math.max(1,plan.total)} value={plan.done} aria-label={`${plan.done} of ${plan.total} agent-reported steps completed`}/>}<svg viewBox="0 0 96 32" width="96" height="32" role="img" aria-label="Recorded plan progress"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="2"/></svg></summary>
  <p className="a-caption">Agent-reported steps. The line shows retained plan updates; changing the plan can change its percentage.</p>
  {plan.unavailable?<p>The latest plan could not be read. Open the chat to inspect it.</p>:error?<p role="alert">{error}</p>:items?<ol>{items.map((item,index)=><li key={index}>{item.content} <small>{item.status.replace('_',' ')}</small></li>)}</ol>:<p>Loading plan…</p>}
 </details>;
}
