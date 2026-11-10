// Quotes retain raw source bytes. Canvas references intentionally have a different
// (readable/normalized) contract; never use their trimmed excerpt as a snapshot.
const sources=new WeakMap();
export function bindQuoteSource(node,map){if(node)sources.set(node,map);}
const wrappers=new Set(['root','paragraph','heading','strong','emphasis','delete','link','list','listItem','blockquote']);
const blocks=new Set(['root','list','listItem','blockquote']);
export function quoteSourceSpans(source,base,map){
 return ()=>tree=>{
  const barrier=()=>map.flow.push({barrier:true});
  const visit=(node,parent)=>{
   const start=node.position?.start?.offset,end=node.position?.end?.offset;
   if(['text','inlineCode','code'].includes(node.type)){
    if(!Number.isInteger(start)||!Number.isInteger(end)){barrier();return node;}
    let at=start,value=node.value;
    if(node.type==='inlineCode'){
     if(/[\r\n]/.test(value)){barrier();return node;}
     const raw=source.slice(start,end),ticks=/^`+/.exec(raw)?.[0].length||0;at+=ticks;
     if(source.slice(at,end-ticks).startsWith(' ')&&source.slice(at,end-ticks).endsWith(' ')&&value.trim())at++;
    }else if(node.type==='code'){
     const raw=source.slice(start,end);
     at+=/^ {0,3}(`{3,}|~{3,})/.test(raw)?raw.indexOf('\n')+1:/^ */.exec(raw)[0].length;
     // react-markdown emits a final newline for code. It must exist in source.
     value+='\n';
    }
    if(source.slice(at,node.type==='text'?end:at+value.length)!==value){barrier();return node;}
    const index=map.leaves.length,leaf={start:base+at,end:base+at+value.length,value};
    map.leaves.push(leaf);map.flow.push({leaf:index});
    const properties={'data-message-quote-leaf':index};
    if(node.type==='text'){
     // Reuse the sourceSpans wrapper instead of adding another rendered span.
     if(parent?.data?.hName==='span'&&parent.children.length===1){
      parent.data.hProperties={...parent.data.hProperties,...properties};return node;
     }
     return {type:'emphasis',data:{hName:'span',hProperties:properties},children:[node]};
    }
    node.data={...node.data,hProperties:{...properties,...node.data?.hProperties}};return node;
   }
   if(!wrappers.has(node.type)||node.type==='listItem'&&node.checked!==null&&node.checked!==undefined){barrier();return node;}
   let previous;
   node.children=node.children?.map(child=>{
    const a=previous?.position?.end?.offset,b=child.position?.start?.offset;
    if(Number.isInteger(a)&&Number.isInteger(b)&&b>a){
     const gap=source.slice(a,b);
     if(/^\s+$/.test(gap))map.flow.push({start:base+a,end:base+b});
     else if(blocks.has(node.type))barrier();
    }
    previous=child;return visit(child,node);
   });return node;
  };
  // Each separately rendered writing part is a boundary, not permission to
  // silently omit its controls or markup when selecting across parts.
  if(map.flow.length)barrier();visit(tree);
 };
}
const splitSurrogate=(text,at)=>at>0&&at<text.length&&/[\uD800-\uDBFF]/.test(text[at-1])&&/[\uDC00-\uDFFF]/.test(text[at]);
export function quoteSpans(map,selected){
 if(!selected.length)return null;
 const first=map.flow.findIndex(row=>row.leaf===selected[0].leaf),last=map.flow.findIndex(row=>row.leaf===selected.at(-1).leaf);
 if(first<0||last<first)return {error:'This selection cannot be mapped exactly.'};
 const spans=[];let index=0;
 for(const row of map.flow.slice(first,last+1)){
  if(row.barrier)return {error:'This selection crosses content without an exact text mapping.'};
  let start=row.start,end=row.end;
  if(row.leaf!==undefined){
   const part=selected[index++],leaf=map.leaves[row.leaf];
   if(!part||part.leaf!==row.leaf||!Number.isInteger(part.from)||!Number.isInteger(part.to)||part.from<0||part.from>=part.to||part.to>leaf.value.length)return {error:'This selection cannot be mapped exactly.'};
   start=leaf.start+part.from;end=leaf.start+part.to;
  }
  if(splitSurrogate(map.source,start)||splitSurrogate(map.source,end))return {error:'Select the complete Unicode character.'};
  const prior=spans.at(-1);
  if(prior&&start<prior.end)return {error:'This selection is not in source order.'};
  if(prior?.end===start)prior.end=end;else spans.push({start,end});
 }
 if(index!==selected.length||spans.length>128)return {error:'Select a smaller exact passage.'};
 if(spans.reduce((n,s)=>n+[...map.source.slice(s.start,s.end)].length,0)>12000)return {error:'Select at most 12,000 characters. Nothing was shortened.'};
 let cursor=0,characters=0;
 const offset=target=>{while(cursor<target){cursor+=map.source.codePointAt(cursor)>0xffff?2:1;characters++;}return characters;};
 return {source:map.source,spans:spans.map(({start,end})=>({start:offset(start),end:offset(end)}))};
}
export function selectedQuote(article,selection){
 if(!article||!selection||selection.isCollapsed||selection.rangeCount!==1)return null;
 const range=selection.getRangeAt(0);
 if(!article.contains(range.startContainer)||!article.contains(range.endContainer))return null;
 const root=[...article.querySelectorAll('.a-detail-text [data-message-quote-source]')].find(node=>node.contains(range.startContainer)&&node.contains(range.endContainer));
 if(!root)return {error:'Select text within one message.'};
 const map=sources.get(root);if(!map)return {error:'The rendered source is not available.'};
 const selected=[],edgeSeparators=[],walker=root.ownerDocument.createTreeWalker(root,4);let node;
 while((node=walker.nextNode())){
  if(!range.intersectsNode(node))continue;
  const leafRange=root.ownerDocument.createRange();leafRange.selectNodeContents(node);
  if(range.compareBoundaryPoints(0,leafRange)>0)leafRange.setStart(range.startContainer,range.startOffset);
  if(range.compareBoundaryPoints(2,leafRange)<0)leafRange.setEnd(range.endContainer,range.endOffset);
  if(leafRange.collapsed)continue;
  const owner=node.parentElement?.closest('[data-message-quote-leaf]');
  if(!owner){
   // Markdown renderers put newline-only nodes between block elements. Their
   // exact source separators are supplied by the AST flow, never this DOM text.
   if(node.textContent!=='\n'||!['DIV','UL','OL','LI','BLOCKQUOTE'].includes(node.parentElement?.tagName))return {error:'This selection includes controls or unmapped text.',source:map.source};
   // A separator selected at an endpoint has no pair of selected leaves to
   // establish its exact raw interval. Never silently shorten that selection.
   // Leading/trailing list-renderer padding has no two sibling blocks.
   if(node.previousSibling?.nodeType===1&&node.nextSibling?.nodeType===1)edgeSeparators.push(selected.length);
   continue;
  }
  const index=Number(owner.dataset.messageQuoteLeaf),leaf=map.leaves[index];
  if(!leaf||owner.textContent!==node.textContent||node.textContent!==leaf.value)return {error:'This transformed text cannot be quoted exactly.',source:map.source};
  const prefix=root.ownerDocument.createRange();prefix.selectNodeContents(node);prefix.setEnd(leafRange.startContainer,leafRange.startOffset);
  const from=prefix.toString().length,to=from+leafRange.toString().length;
  selected.push({leaf:index,from,to});
 }
 if(edgeSeparators.some(at=>at===0||at===selected.length))return {error:'This boundary whitespace cannot be quoted exactly.',source:map.source};
 const result=quoteSpans(map,selected);
 return result&&{...result,source:map.source,root,range:range.cloneRange()};
}
