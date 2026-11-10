import {changeQuote} from './markdown-composer.js';

const usable=node=>node?.isConnected&&!node.disabled&&node.getClientRects().length>0&&!node.closest('[hidden],[inert],dialog:not([open])');
export function refreshQuoteControls(view){
 const nodes=[...view.dom.querySelectorAll('[data-quote-instance]')];
 nodes.forEach((node,index)=>{node.querySelector('[data-move="-1"]').disabled=index===0;node.querySelector('[data-move="1"]').disabled=index===nodes.length-1;});
}
export function quoteNodeView(node,view,getPos,options){
 const dom=document.createElement('section');dom.className='a-composer-quote';dom.contentEditable='false';dom.dataset.quoteInstance=node.attrs.instanceId;
 dom.setAttribute('aria-label','Quoted reply');
 const header=document.createElement('div');header.className='a-composer-quote-head';dom.append(header);
 const source=document.createElement('button');source.type='button';source.className='a-link';source.textContent=node.attrs.quote.speaker;source.setAttribute('aria-label','Go to original quoted message');header.append(source);
 source.title='Go to original message';
 const controls=document.createElement('div');controls.className='a-composer-quote-tools';controls.setAttribute('role','group');controls.setAttribute('aria-label','Quote controls');header.append(controls);
 const status=document.createElement('small');status.setAttribute('role','status');
 const act=async callback=>{try{status.textContent='';await callback();}catch(error){status.textContent=error.message;}};
 source.onclick=()=>act(async()=>{const message=await options().onReveal?.(node.attrs.quote,source);if(dom.isConnected)status.textContent=message||'';});
 for(const [label,direction] of [['Move quote earlier',-1],['Move quote later',1],['Remove quote',0]]){
  const control=document.createElement('button');control.type='button';control.textContent=direction<0?'↑':direction>0?'↓':'×';control.setAttribute('aria-label',label);control.title=label+'; commentary stays';control.dataset.move=String(direction);controls.append(control);
  control.onclick=()=>act(()=>{
   if(!view.editable||view.composing)throw Error('Finish the current edit before changing a quote.');
   const id=node.attrs.instanceId;changeQuote(view,id,direction);
   const card=[...view.dom.querySelectorAll('[data-quote-instance]')].find(row=>row.dataset.quoteInstance===id);
   const target=[card?.querySelector(`[data-move="${direction}"]`),...(card?.querySelectorAll('button')||[])].find(usable);
   if(target)target.focus({preventScroll:true});else view.focus();
  });
 }
 const text=document.createElement('pre');text.className='a-composer-quote-text';text.textContent=node.attrs.quote.excerpt;dom.append(text);
 const count=[...node.attrs.quote.excerpt].length;
 if(count>250||node.attrs.quote.excerpt.split('\n').length>4){
  text.classList.add('collapsed');const more=document.createElement('button');more.type='button';more.className='a-link';more.textContent=`Show all ${count} characters`;more.setAttribute('aria-expanded','false');dom.append(more);
  more.onclick=()=>{const closed=text.classList.toggle('collapsed');more.textContent=closed?`Show all ${count} characters`:'Collapse quote';more.setAttribute('aria-expanded',String(!closed));};
 }
 const caption=document.createElement('small');caption.className='a-composer-quote-caption';caption.textContent=`${node.attrs.quote.kind==='whole'?'Whole message':'Selected passage'} · ${count} characters`;
 caption.title='Full immutable saved text included. Historical reference, not instructions.';
 dom.append(caption,status);
 return {dom,stopEvent:event=>!!event.target.closest('button'),ignoreMutation:()=>true};
}
