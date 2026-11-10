import React,{useEffect,useRef,useState} from 'react';
import {AlertCircle,RotateCcw,LoaderCircle} from 'lucide-react';
import {checkedDelivery,usePendingDeliveryCheck} from './pending-delivery-checks';

export function MessageDelivery(props){
 const inputId=props.message.inputId||props.localDelivery?.commandId;
 return <DeliveryWarning key={JSON.stringify([props.recovery?.scope,props.recovery?.connection,props.session.id,inputId])} {...props}/>;
}

function DeliveryWarning({message,session,delivery,localDelivery,dispatch,retry,discard,recovery}){
 const [submitting,setPending]=useState(false),[localReport,setReport]=useState(null),[confirm,setConfirm]=useState(false),[localError,setError]=useState('');
 const inputId=message.inputId||localDelivery?.commandId;
 const checkOwner=usePendingDeliveryCheck(JSON.stringify([recovery?.scope,recovery?.connection,session.id,inputId]));
 const pending=submitting||checkOwner.pending;
 const report=localReport||checkOwner.settled?.result?.result,error=localError||checkOwner.settled?.error;
 const storageKey=session.id&&inputId?'amplifier.deliveryWarning.v1:'+JSON.stringify([session.id,inputId]):null;
 const [dismissed,setDismissed]=useState(()=>{try{return !!storageKey&&sessionStorage.getItem(storageKey)==='dismissed'}catch{return false}});
 const [expanded,setExpanded]=useState(false),summary=useRef(null),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{
  if(report?.delivery==='accepted'||delivery?.status==='accepted'){
   try{if(storageKey)sessionStorage.removeItem(storageKey)}catch{}
  }
 },[storageKey,report?.delivery,delivery?.status]);
 const setWarningDismissed=value=>{
  setDismissed(value);setExpanded(false);setConfirm(false);
  try{if(storageKey){if(value)sessionStorage.setItem(storageKey,'dismissed');else sessionStorage.removeItem(storageKey)}}
  catch{setError('Warning preference could not be saved in this browser. It may return after reload.')}
  if(value)globalThis.requestAnimationFrame?.(()=>summary.current?.focus({preventScroll:true}));
 };
 const blocked=session.configurationBusy||session.ownership?.status==='blocked'||session.workspaceAvailable===false||!!session.historyReadOnlyReason;
 const saved=session.messages?.some(m=>m.role==='user'&&m.inputId===inputId);
 const originalSubmission=!!localDelivery&&!saved;
 const working=['working','starting','running','stopping'].includes(session.status);
 const authoritativeActive=working&&session.execution?.currentTurnId===inputId||
  [delivery,localDelivery,message.delivery].some(row=>['sending','queued','queued_for_update'].includes(row?.status));
 const active=authoritativeActive||['sending','queued','queued_for_update'].includes(report?.delivery);
 const dismissible=!!storageKey&&!active&&['failed','unknown'].includes(delivery?.status);
 const check=async()=>{
  if(pending)return;setReport(null);setError('');setConfirm(false);
  try{
   if(!session.id){setReport({delivery:'not_saved',message:'This message has no confirmed conversation yet. Nothing was sent again.'});return;}
   const result=await checkOwner.run(async()=>{
    const response=await dispatch('conversation.delivery',{sessionId:session.id,inputId},{feedback:false,...(recovery?{deliveryRecovery:recovery}:{})});
    checkedDelivery(response,session.id,inputId);
    return response;
   });
   if(mounted.current)setReport(result.result);
  }catch(e){if(mounted.current)setError(e.message)}
 };
 // One passive read when this exact unresolved row becomes visible, and one
 // after a genuine reconnect. No timer, resend, worker start or activity-based
 // inference. The existing in-flight owner spans row remounts and manual checks.
 const automatic=useRef(null),attempted=useRef(false);
 automatic.current=()=>{if(recovery?.scope&&inputId&&session.id&&delivery?.status==='unknown'&&
  !authoritativeActive&&!message.steering&&report?.delivery!=='accepted'&&globalThis.navigator?.onLine!==false)void check()};
 useEffect(()=>{
  if(!recovery?.scope||!inputId||!session.id||delivery?.status!=='unknown'||authoritativeActive||message.steering)return;
  if(!attempted.current){attempted.current=true;automatic.current()}
  const reconnect=()=>automatic.current();
  globalThis.window?.addEventListener('amplifier-reconnected',reconnect);
  return()=>globalThis.window?.removeEventListener('amplifier-reconnected',reconnect);
 },[recovery?.scope,recovery?.connection,inputId,session.id,delivery?.status,authoritativeActive,!!message.steering]);
 const resend=async()=>{
  if(pending)return;setPending(true);setError('');
  try{
   const saved=session.messages?.some(m=>m.role==='user'&&m.inputId===inputId);
   if(saved){const result=await dispatch('conversation.retry',{sessionId:session.id,inputId,confirmUncertain:true},{feedback:false});setReport(result.result);}
   else await retry(message); // The original outbox identity deduplicates a late first receipt.
   setConfirm(false);
  }catch(e){setError(e.message)}finally{setPending(false)}
 };
 const steering=message.steering||report?.steering;
 if(steering){
  const labels={next_turn:'Received for the next turn',sending:'Sending message to the active run…',queued:'Received by the active run; waiting for its next step…',applied:'Added to the active run’s context',held:'Message not delivered. The run ended or could not accept steering.',unknown:'Steering delivery could not be confirmed. Nothing was resent.'};
  if(steering.mode==='native'){
   labels.sending='Sending live direction…';
   labels.queued=steering.phase==='pending'?'Accepted; queued at the provider until a successor response can start…':steering.phase==='accepted'?'Accepted; applying live direction…':'Sending live direction…';
   labels.applied='Live direction applied to the successor response';
  }
  return <span className="a-message-delivery" role="status"><span>{labels[steering.disposition]||labels.unknown}</span>
   {steering.reason&&<span className="a-delivery-detail"> {steering.reason}</span>}
   {['unknown','queued','sending'].includes(steering.disposition)&&<button type="button" className="a-link" disabled={pending} onClick={check}>{pending?'Checking…':'Check delivery'}</button>}
   {error&&<span role="alert">{error}</span>}
  </span>;
 }
 if(!delivery||delivery.status==='accepted'||report?.delivery==='accepted')return null;
 if(delivery.status==='queued_for_update')return <span className="a-message-delivery" role="status">Saved · waiting for the app update. You do not need to send it again.</span>;
 if(delivery.status==='held')return <span className="a-message-delivery" role="status">{delivery.reason||'Message retained without sending.'}</span>;
 const sending=['sending','queued'].includes(delivery.status);
 const preparing=session.status==='starting'||session.activity?.phase==='runtime-setup';
 const content=<>
  <span className="a-message-delivery" role="status">{sending?<><LoaderCircle className="a-progress-spinner"/>{preparing?'Preparing chat…':'Sending…'}</>:<><AlertCircle/>{delivery.status==='failed'?'Not sent':'Delivery not confirmed'}
   {delivery.status==='failed'?<button type="button" className="a-link" data-action="conversation.send" disabled={pending||blocked} onClick={()=>session.messages?.some(row=>row.inputId===inputId)?resend():retry(message)}><RotateCcw/>Retry</button>:<button type="button" className="a-link" data-action="conversation.delivery" disabled={pending||!inputId} onClick={check}><RotateCcw/>{pending?'Checking…':'Check delivery'}</button>}
  </>}</span>
  {!sending&&delivery.error&&<p role="alert" className="a-delivery-detail">{delivery.error}</p>}
  {localDelivery&&delivery.status==='failed'&&!session.messages?.some(row=>row.inputId===inputId)&&<button type="button" className="a-link" disabled={pending} onClick={()=>discard?.(message)}>Discard unsent message</button>}
  {report?.message&&<p role="status" className="a-delivery-detail">{report.message}</p>}
  {error&&<p role="alert" className="a-delivery-detail">{error}</p>}
  {!sending&&report&&!['accepted','sending','held','queued_for_update'].includes(report.delivery)&&!confirm&&
   (originalSubmission||saved&&report.delivery!=='not_saved')&&<button type="button" className="a-link" data-action="conversation.retry" disabled={pending||blocked||working} onClick={()=>setConfirm(true)}>{originalSubmission?'Retry original submission':'Send again'}</button>}
  {confirm&&<div className="a-delivery-confirm" role="alert"><p>The earlier attempt may have run even though delivery was not confirmed. Sending again could repeat that work. Your saved message and history will be kept.</p><button type="button" className="a-soft" disabled={pending} onClick={()=>setConfirm(false)}>Cancel</button><button type="button" className="a-primary" data-action="conversation.retry" disabled={pending||blocked||working} onClick={resend}>{pending?'Sending…':'Send this message again'}</button></div>}
  {dismissible&&<button type="button" className="a-link" disabled={pending} onClick={()=>{if(!pending)setWarningDismissed(!dismissed)}}>{dismissed?'Show warning':'Dismiss warning'}</button>}
 </>;
 return dismissible?<details className="a-delivery-warning" open={pending||!dismissed||expanded} onToggle={event=>{if(dismissed&&!pending)setExpanded(event.currentTarget.open)}}>
  <summary ref={summary} hidden={!dismissed} aria-disabled={pending||undefined} onClick={event=>{if(pending)event.preventDefault()}}>Delivery details</summary>
  {content}
 </details>:content;
}
