// A mark represents the user message and the work/replies before the next one.
// Measure only loaded turns, coalescing scroll and layout changes into one frame.
export function observeVisibleTurns(pane,ids,onVisible,leadingTurn){
 const view=pane.ownerDocument.defaultView;
 let frame=0,last='',disposed=false;
 const observed=new Set();
 const measure=()=>{
  frame=0;if(disposed)return;
  const bounds=pane.getBoundingClientRect();
  const nodes=new Map([...pane.querySelectorAll('[data-message-id]')].map(node=>[node.dataset.messageId,node]));
  const starts=ids.map(id=>nodes.get(id)?.getBoundingClientRect().top??(id===leadingTurn?nodes.values().next().value?.getBoundingClientRect().top:undefined));
  const visible=ids.filter((id,index)=>{
   const top=starts[index],bottom=starts[index+1]??bounds.top+pane.scrollHeight-pane.scrollTop;
   return top!==undefined&&top<bounds.bottom&&bottom>bounds.top;
  });
  const signature=JSON.stringify(visible);
  if(signature!==last){last=signature;onVisible(visible)}
 };
 const schedule=()=>{if(!frame&&!disposed)frame=view.requestAnimationFrame(measure)};
 const resize=new view.ResizeObserver(schedule);
 const observeChildren=()=>{
  for(const child of observed)if(child.parentElement!==pane){resize.unobserve(child);observed.delete(child)}
  for(const child of pane.children)if(!observed.has(child)){resize.observe(child);observed.add(child)}
 };
 const mutations=new view.MutationObserver(()=>{observeChildren();schedule()});
 resize.observe(pane);observeChildren();mutations.observe(pane,{childList:true,subtree:true});
 pane.addEventListener('scroll',schedule,{passive:true});schedule();
 return ()=>{disposed=true;view.cancelAnimationFrame(frame);resize.disconnect();mutations.disconnect();pane.removeEventListener('scroll',schedule)};
}
