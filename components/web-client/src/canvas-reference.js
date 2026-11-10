export const QUOTE_LIMIT=4000;

// One source scan per definition; each diagnostic lookup is logarithmic.
export function sourceLineLookup(source){
 const starts=[0];
 for(const match of source.matchAll(/\r\n|\r|\n/g))starts.push(match.index+match[0].length);
 return offset=>{
  let low=0,high=starts.length;
  while(low<high){const mid=(low+high)>>>1;if(starts[mid]<=offset)low=mid+1;else high=mid}
  return low;
 };
}

// Keep source locations on the rendered text, including repeated phrases. This
// plugin is opt-in for Canvas; it does not alter ordinary chat Markdown.
export function sourceSpans(source,base=0){
 return ()=>tree=>{
  const visit=parent=>{parent.children=parent.children?.map(node=>{
   const start=node.position?.start?.offset,end=node.position?.end?.offset;
   if(node.type==='text'&&Number.isInteger(start)&&Number.isInteger(end))return {type:'emphasis',data:{hName:'span',hProperties:{'data-canvas-source-start':base+start,'data-canvas-source-end':base+end}},children:[node]};
   if(['inlineCode','code'].includes(node.type)&&Number.isInteger(start)&&Number.isInteger(end)){
    if(node.type==='code')node.data={...node.data,hProperties:{...node.data?.hProperties,'data-canvas-fence-offset':base+start}};
    const raw=source.slice(start,end),ticks=/^`+/.exec(raw)?.[0].length||0;
    let at=start;
    if(node.type==='inlineCode'){
     at+=ticks;
     const body=source.slice(at,end-ticks);
     if(body.startsWith(' ')&&body.endsWith(' ')&&body.trim())at++;
    }else if(/^ {0,3}(`{3,}|~{3,})/.test(raw))at+=raw.indexOf('\n')+1;
    else at+=/^ */.exec(raw)[0].length;
    if(at+node.value.length<=end&&source.slice(at,at+node.value.length)===node.value)node.data={...node.data,hProperties:{...node.data?.hProperties,'data-canvas-source-start':base+at,'data-canvas-source-end':base+at+node.value.length,'data-canvas-literal':'true'}};
   }
   if(node.children)visit(node);
   return node;
  })};visit(tree);
 };
}

const token=/\\[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]|&(?:#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z][A-Za-z0-9]+);|\r\n/g;
function decodedOffsets(raw,literal,decode){
 const offsets=[0];let text='',cursor=0;
 const append=(value,start,end)=>{text+=value;for(let i=1;i<=value.length;i++)offsets.push(i===value.length?end:start)};
 if(!literal)for(const match of raw.matchAll(token)){
  for(let i=cursor;i<match.index;i++)append(raw[i],i,i+1);
  const value=match[0];append(value.startsWith('\\')?value.slice(1):value==='\r\n'?'\n':decode(value),match.index,match.index+value.length);cursor=match.index+value.length;
 }
 for(let i=cursor;i<raw.length;i++)append(raw[i],i,i+1);
 return {text,offsets};
}

export function selectedReference(container,selection,source){
 if(!container||!selection||selection.rangeCount!==1||selection.isCollapsed)return null;
 const range=selection.getRangeAt(0);
 if(!container.contains(range.startContainer)||!container.contains(range.endContainer))return null;
 const excerpt=selection.toString().trim();
 if(!excerpt)return null;
 if([...excerpt].length>QUOTE_LIMIT)return {error:`Select at most ${QUOTE_LIMIT.toLocaleString()} characters to reference.`};
 const doc=container.ownerDocument,decoder=doc.createElement('textarea'),decode=value=>{decoder.innerHTML=value;return decoder.value};
 const walker=doc.createTreeWalker(container,4),spans=[];
 let node;
 while((node=walker.nextNode())){
  if(!range.intersectsNode(node))continue;
  const from=node===range.startContainer?range.startOffset:0,to=node===range.endContainer?range.endOffset:node.length;
  if(to<=from)continue;
  const owner=node.parentElement?.closest('[data-canvas-source-start]');
  if(!owner||!container.contains(owner)||owner.textContent!==node.textContent){if(node.textContent.slice(from,to).trim())return {error:'Select document text without embedded controls or diagrams.'};continue}
  const start=Number(owner.dataset.canvasSourceStart),end=Number(owner.dataset.canvasSourceEnd),literal=owner.dataset.canvasLiteral==='true';
  const raw=source.slice(start,end),direct=literal||raw===node.textContent,mapped=direct?null:decodedOffsets(raw,false,decode);
  if((direct?raw:mapped.text)!==node.textContent)return {error:'This selection cannot be mapped to the saved source. Select a smaller text passage.'};
  spans.push({start:start+(direct?from:mapped.offsets[from]),end:start+(direct?to:mapped.offsets[to]),...(literal?{literal:true}:{})});
 }
 if(!spans.length)return null;
 if(spans.length>128)return {error:'Select a smaller passage to reference.'};
 // Convert browser UTF-16 positions to Unicode offsets in one source pass,
 // rather than copying the entire prefix for every selected leaf.
 let cursor=0,characters=0;
 const unicodeOffset=target=>{while(cursor<target){cursor+=source.codePointAt(cursor)>0xffff?2:1;characters++}return characters};
 for(const span of spans){span.start=unicodeOffset(span.start);span.end=unicodeOffset(span.end)}
 return {excerpt,spans};
}
