import React,{useEffect,useId,useMemo,useRef,useState} from 'react';
import ReactMarkdown from 'react-markdown';
import {Markdown} from './markdown';
import {DocumentOutput} from './inline-artifacts';
import './questions.css';

function ReviewArtifact({reference,question,state,dispatch,onExpandedChange}){
 return <DocumentOutput item={{row:reference,version:reference.version}} state={state} dispatch={dispatch}
  questionBinding={{questionId:question.id,questionContentRevision:question.contentRevision||1}} onExpandedChange={onExpandedChange}/>;
}

function QuestionCard({question,sessionId,state,dispatch,onPreviewChange,history}){
 const q=question,draftKey=JSON.stringify(['amplifier.questionDraft.v1',state.client?.hostInstanceId||globalThis.location?.origin||'local',sessionId,q.id,q.contentRevision||1]);
 const [text,setText]=useState(()=>{try{return q.status==='pending'?(sessionStorage.getItem(draftKey)||'').slice(0,8000):''}catch{return ''}});
 const [alternativeOpen,setAlternativeOpen]=useState(()=>!!text);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[draftWarning,setDraftWarning]=useState(''),[deliveryNote,setDeliveryNote]=useState('');
 const pending=useRef(false),request=useRef(null);
 const label=q.prompt.replace(/[#*_`]/g,'').slice(0,200),fileContext=useMemo(()=>({sessionId,act:dispatch}),[sessionId,dispatch]);
 const editText=value=>{
  setText(value);
  try{if(value)sessionStorage.setItem(draftKey,value);else sessionStorage.removeItem(draftKey);setDraftWarning('')}
  catch{setDraftWarning('This browser could not retain the answer draft across reload. Keep this page open.')}
 };
 useEffect(()=>{if(q.status!=='pending')try{sessionStorage.removeItem(draftKey)}catch{}},[q.status,draftKey]);
 async function submit(event,optionId=''){
  event.preventDefault();if(pending.current)return;
  const answer=optionId?{optionId}:{text};
  if(!optionId&&!answer.text.trim())return;
  // Keep the same command after a lost reply. Closed revisions also prevent
  // duplicate delivery after reload or submission from a second device.
  const args={sessionId,id:q.id,expectedRevision:q.revision,...answer};
  const key=JSON.stringify(args);
  if(request.current?.key!==key)request.current={key,id:crypto.randomUUID()};
  pending.current=true;setBusy(true);setError('');
  try{const result=await dispatch('question.answer',args,{id:request.current.id});if(!result?.accepted)throw Error(result?.error||'The answer could not be confirmed.')}
  catch(error){
   // A confirmed dirty-view rejection recorded no answer. A later explicit
   // click after finishing the edit is a new attempt, not a lost-reply replay.
   if(error.code==='canvas_view_dirty')request.current=null;
   setError(error.message||'The answer could not be confirmed. Your choice is kept.');
  }
  finally{pending.current=false;setBusy(false)}
 }
 async function skip(){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');
  try{const result=await dispatch('question.cancel',{sessionId,id:q.id,expectedRevision:q.revision,reason:'Skipped by the user'});if(!result?.accepted)throw Error(result?.error||'The question was not cancelled.')}
  catch(error){setError(error.message)}finally{pending.current=false;setBusy(false)}
 }
 async function checkDelivery(){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setDeliveryNote('');
  try{
   const result=await dispatch('conversation.delivery',{sessionId,inputId:q.delivery.inputId});
   if(!result?.accepted)throw Error(result?.error||'Delivery could not be checked.');
   setDeliveryNote(result.result?.delivery==='accepted'?'Amplifier received this answer. It was not sent again.':'Delivery is still unconfirmed. Nothing was sent again; other work can continue.');
  }catch(error){setError(error.message||'Delivery could not be checked. Nothing was sent again.')}
  finally{pending.current=false;setBusy(false)}
 }
 const delivery=q.delivery?.status;
 const original=<>
  <Markdown text={q.prompt} className="a-question-prompt" userContent fileContext={fileContext}/>
  <Markdown text={q.dependency} className="a-caption a-question-dependency" userContent fileContext={fileContext}/>
 </>;
 const answerEditor=<>
  <label className="a-question-text">Your answer<textarea aria-label={`Your answer: ${label}`} value={text} rows={2} maxLength={8000} onChange={event=>editText(event.target.value)}/></label>
  <div className="a-dialog-actions"><button className="a-primary" data-action="question.answer" disabled={busy||!text.trim()}>{busy?'Saving answer…':'Submit answer'}</button></div>
 </>;
 return <details className="a-question-item" open={history?undefined:true}>
  <summary className="a-question-summary" hidden={!history} aria-label={`${q.status==='answered'?'Answered':'Closed'} question: ${label}`}>
   <strong>{q.status==='answered'?'Answered':'Closed question'}</strong>
   <span><ReactMarkdown allowedElements={['strong','em','del','code','br']} unwrapDisallowed skipHtml>{q.answer?.text||q.prompt}</ReactMarkdown></span>
  </summary>
  <article className="a-question" aria-label={`Question ${q.id}`} data-question-id={q.id}>
  <div className="a-question-kind">{q.status==='pending'?(q.required?'Your answer is needed':'Optional question'):q.status==='answered'?'Answered':'Closed question'}</div>
  {q.status==='pending'?<div key="prompt">{original}</div>:<div key="answer" className="a-question-result" role="status">
   {q.status==='answered'?<><strong>Your answer:</strong><Markdown text={q.answer.text} userContent fileContext={fileContext}/><small>{delivery==='accepted'?'Answer saved and delivered to Amplifier.':delivery==='sending'?'Answer saved. Confirming delivery…':delivery==='rejected'?'Answer saved. Amplifier could not accept it.': 'Answer saved. Delivery is unconfirmed; it has not been sent again.'}</small>{q.delivery?.message&&delivery!=='accepted'&&<p>{q.delivery.message}</p>}</>:<small>{q.status==='superseded'?'Replaced by a newer question.':'Question cancelled without an answer.'}</small>}
   {q.status==='answered'&&delivery==='unknown'&&q.delivery?.inputId&&<div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} onClick={checkDelivery}>{busy?'Checking delivery…':'Check delivery'}</button></div>}
   {deliveryNote&&delivery!=='accepted'&&<p>{deliveryNote}</p>}
  </div>}
  {!!q.artifacts?.length&&<div key="review-artifacts" className="a-question-artifacts" aria-label="Documents for this decision">{q.artifacts.map(ref=><ReviewArtifact key={`${ref.id}:${ref.version}`} reference={ref} question={q} state={state} dispatch={dispatch}
   onExpandedChange={expanded=>onPreviewChange(q.id,`${ref.id}:${ref.version}`,expanded)}/>)}</div>}
  {q.status==='pending'?<form key="decision" onSubmit={event=>submit(event)} aria-label={`Answer: ${label}`}>
   <fieldset disabled={busy}><legend className="a-sr-only">{label}</legend>
    {q.options.map((choice,index)=><div key={choice.id}>
     <button type="button" className="a-question-option" data-action="question.answer" aria-describedby={choice.description?`${q.id}-choice-${index}-description`:undefined} onClick={event=>submit(event,choice.id)}>
      <strong><ReactMarkdown allowedElements={['strong','em','del','code','br']} unwrapDisallowed skipHtml>{choice.label}</ReactMarkdown></strong>
     </button>
     {choice.description&&<div id={`${q.id}-choice-${index}-description`} className="a-question-option-description"><Markdown text={choice.description} userContent fileContext={fileContext}/></div>}
    </div>)}
    {q.allowFreeText&&(q.options.length?<details className="a-question-alternative" open={alternativeOpen} onToggle={event=>setAlternativeOpen(event.currentTarget.open)}>
     <summary>Write a different answer</summary>{answerEditor}
    </details>:answerEditor)}
   </fieldset>
   {!q.required&&<div className="a-dialog-actions"><button className="a-soft" type="button" data-action="question.cancel" disabled={busy} onClick={skip}>Skip question</button></div>}
   {q.required&&<details className="a-question-context"><summary>Question context</summary><p className="a-caption">This step waits for your answer. Other work can continue.</p></details>}
  </form>:<details key="original" className="a-question-original">
   <summary>Original question &amp; provenance</summary>{original}
   {q.answer&&<p className="a-caption">Answer to question revision {q.answer.questionRevision}.</p>}
  </details>}
  {error&&<p role="alert">{error}</p>}
  {draftWarning&&<p role="status">{draftWarning}</p>}
 </article></details>;
}

export function activeQuestions(session){
 return (session?.questions||[]).filter(q=>q.status==='pending'||q.status==='answered'&&q.delivery?.status!=='accepted');
}

export function QuestionNotice({session,paneRef}){
 const active=activeQuestions(session);
 if(!active.length)return null;
 const pending=active.filter(q=>q.status==='pending').length;
 return <div className="a-question-notice" role="status"><span>{pending?`${pending===1?'A question needs':pending+' questions need'} your answer`:'An answer needs delivery confirmation'}</span><button type="button" className="a-link" onClick={()=>{const target=paneRef.current?.querySelector('[data-part="questions"]');if(target){target.scrollIntoView({block:'start'});target.focus({preventScroll:true})}}}>Review {pending?'questions':'answer'}</button></div>;
}

export function Questions({session,state={},dispatch,history=false,reviewState}){
 const localReviews=useState({}),[previews,setPreviews]=reviewState||localReviews,owner=useId();
 useEffect(()=>()=>setPreviews(current=>Object.fromEntries(Object.entries(current).filter(([,entry])=>entry.owner!==owner))),[setPreviews,owner]);
 const onPreviewChange=(questionId,reference,expanded)=>setPreviews(current=>{
  const references={...current[questionId]?.references,[reference]:expanded},next={...current};
  if(Object.values(references).some(Boolean))next[questionId]={owner,references};
  else delete next[questionId];
  return next;
 });
 const active=activeQuestions(session);
 // Preserve a mounted review through answer/delivery/status updates. A late
 // renderer dirty report still has its editor; closing the review lets the
 // completed card leave the active surface. Status is not content identity.
 const questions=(session?.questions||[]).filter(q=>previews[q.id]
  ?previews[q.id].owner===owner
  :history?!active.includes(q):active.includes(q));
 if(!questions.length)return null;
 const cards=questions.map(q=><QuestionCard key={`${q.id}:${q.contentRevision||1}`} question={q} sessionId={session.id} state={state} dispatch={dispatch} onPreviewChange={onPreviewChange} history={history}/>);
 return history?<details className="a-question-history"><summary>Answers and closed questions ({questions.length})</summary><div className="a-questions">{cards}</div></details>:<section tabIndex={-1} className="a-questions" data-part="questions" aria-label="Questions for you">{cards}</section>;
}
