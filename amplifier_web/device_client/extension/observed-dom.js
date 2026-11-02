// Generated from amplifier-bundle-ui-use/amplifier_ui_use/dom.js. MIT.
const observeUI = () => {
  // This fixed observer is shipped code. Models never supply JavaScript/selectors.
  const key=Symbol.for('amplifier.ui.observer.v1');
  if(!window[key])window[key]={ids:new WeakMap(),nodes:new Map(),counter:0,document:crypto.randomUUID()};
  const registry=window[key];
  function observe(){
    const elements=[];let eligible=0;
    for(const el of document.querySelectorAll('button,a[href],input,textarea,select,[role="button"],[contenteditable="true"]')){
      const box=el.getBoundingClientRect(),style=getComputedStyle(el);
      if(box.width<=0||box.height<=0||style.visibility==='hidden'||style.display==='none'||el.disabled||el.readOnly||el.closest('[inert],[aria-hidden="true"]'))continue;
      if(['password','file','hidden'].includes(el.type))continue;
      const x=Math.max(0,Math.min(innerWidth-1,box.x+box.width/2)),y=Math.max(0,Math.min(innerHeight-1,box.y+box.height/2));
      if(box.bottom<=0||box.right<=0||box.top>=innerHeight||box.left>=innerWidth)continue;
      const top=document.elementFromPoint(x,y);if(!top||!(el===top||el.contains(top)))continue;
      eligible++;if(elements.length>=120)continue;
      if(!registry.ids.has(el)){const id='e'+(++registry.counter);registry.ids.set(el,id);registry.nodes.set(id,el);}
      const id=registry.ids.get(el),tag=el.tagName.toLowerCase();
      const label=el.labels?.[0]?.cloneNode(true);
      if(label)for(const control of label.querySelectorAll('input,select,textarea,button'))control.remove();
      const labelled=(el.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ').trim();
      const name=(el.getAttribute('aria-label')||labelled||label?.textContent||el.innerText||el.getAttribute('placeholder')||el.getAttribute('title')||el.name||tag).trim().replace(/\s+/g,' ').slice(0,240);
      elements.push({id,tag,name,type:el.type||null,value:typeof el.value==='string'?el.value.slice(0,500):null,
        editable:tag==='textarea'||tag==='input'&&!['button','submit','checkbox','radio','reset'].includes(el.type)||el.isContentEditable,
        href:tag==='a'?el.href:null,rect:[box.x,box.y,box.width,box.height].map(v=>Math.round(v)),
        options:tag==='select'?Array.from(el.options).filter(o=>!o.disabled).slice(0,80).map(o=>({value:o.value,label:o.label})):null});
    }
    return {document:registry.document,url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,18000),
      scroll:[Math.round(scrollX),Math.round(scrollY)],viewport:[innerWidth,innerHeight],elements,truncated:eligible>elements.length};
  }
  registry.observe=observe;
  return observe();
}
;
