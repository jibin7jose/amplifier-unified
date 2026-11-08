import {Schema,Slice} from 'prosemirror-model';
import {EditorState,TextSelection,Plugin} from 'prosemirror-state';
import {schema as markdownSchema,MarkdownParser,MarkdownSerializer,defaultMarkdownParser,defaultMarkdownSerializer} from 'prosemirror-markdown';
import {InputRule,inputRules,wrappingInputRule,textblockTypeInputRule,undoInputRule} from 'prosemirror-inputrules';
import {keymap} from 'prosemirror-keymap';
import {baseKeymap,chainCommands,newlineInCode,exitCode,createParagraphNear,liftEmptyBlock,splitBlock,toggleMark,setBlockType,wrapIn,lift} from 'prosemirror-commands';
import {history,undo,redo,closeHistory} from 'prosemirror-history';
import {splitListItem,liftListItem,sinkListItem,wrapInList} from 'prosemirror-schema-list';
import {unified} from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';

// Markdown image references remain editable without fetching remote images.
// Actual images use the existing attachment upload path.
export const composerSchema=new Schema({nodes:markdownSchema.spec.nodes.update('image',{
 ...markdownSchema.spec.nodes.get('image'),
 toDOM:node=>['span',{'class':'a-composer-image-reference',title:node.attrs.src},node.attrs.alt||'Image reference'],
}).update('list_item',{
 ...markdownSchema.spec.nodes.get('list_item'),
 attrs:{checked:{default:null}},
 toDOM:node=>['li',node.attrs.checked===null?{}:{'data-task':String(node.attrs.checked)},0],
}).append({
 table:{group:'block',content:'table_row+',isolating:true,toDOM:()=>['table',{class:'a-composer-table'},['tbody',0]]},
 table_row:{content:'table_cell+',toDOM:()=>['tr',0]},
 table_cell:{content:'inline*',attrs:{header:{default:false},align:{default:null}},toDOM:node=>[node.attrs.header?'th':'td',node.attrs.align?{style:'text-align:'+node.attrs.align}:{},0]},
 // Unsupported Markdown stays editable source, never escaped prose or HTML.
 // No DOM parse rule: pasted markup cannot manufacture editor/quote nodes.
 markdown_source:{group:'block',content:'text*',marks:'',code:true,defining:true,isolating:true,toDOM:()=>['pre',{class:'a-composer-markdown-source','aria-label':'Markdown source (preserved literally)'},['code',0]]},
}).addToEnd('reference',{
 group:'block',atom:true,selectable:false,draggable:false,isolating:true,
 attrs:{instanceId:{},quote:{}},
 // Never parse pasted HTML into an attributed quote.
 toDOM:node=>['blockquote',{'data-quote-instance':node.attrs.instanceId},node.attrs.quote.excerpt],
}),marks:markdownSchema.spec.marks.addBefore('code','strike',{parseDOM:[{tag:'s'},{tag:'del'}],toDOM:()=>['s',0]})});
// Give the composer its own tokenizer; raw web links and labeled Markdown
// links share the parser's URL validation without changing other consumers.
const tokenizer=new defaultMarkdownParser.tokenizer.constructor('commonmark',{html:false,linkify:true}).enable(['linkify','strikethrough','table']);
// Match the bounded draft checkpoint policy. Unsupported URL schemes remain
// literal source rather than producing a rich node that autosave cannot accept.
const checkpointUrl=value=>typeof value==='string'&&!/[\u0000-\u001f\u007f\\]/.test(value)&&(!/^[^/#?]*:/.test(value)||/^(?:https?|mailto|tel|ftp):/i.test(value.trimStart()));
const markdownLinkAllowed=tokenizer.validateLink.bind(tokenizer);
tokenizer.validateLink=value=>checkpointUrl(value)&&markdownLinkAllowed(value);
// Task markers are a small GFM addition to the existing tokenizer, not another
// Markdown parser. Only the first inline token of a list item is eligible.
tokenizer.core.ruler.after('inline','composer_tasks',state=>{
 for(let i=0;i<state.tokens.length-2;i++){
  const item=state.tokens[i],paragraph=state.tokens[i+1],inline=state.tokens[i+2];
  if(item.type!=='list_item_open'||paragraph.type!=='paragraph_open'||inline.type!=='inline')continue;
  const first=inline.children?.[0],match=first?.type==='text'&&/^\[([ xX])\](?:[ \t]+|$)/.exec(first.content);
  // Escaped markers must remain literal, not turn into checkboxes.
  if(!match||!inline.content.startsWith(match[0]))continue;
  item.meta={checked:match[1]!==' '};first.content=first.content.slice(match[0].length);
 }
});
export const composerParser=new MarkdownParser(composerSchema,tokenizer,{
 ...defaultMarkdownParser.tokens,
 s:{mark:'strike'},
 list_item:{block:'list_item',getAttrs:token=>({checked:token.meta?.checked??null})},
 table:{block:'table'},thead:{ignore:true},tbody:{ignore:true},tr:{block:'table_row'},
 th:{block:'table_cell',getAttrs:token=>({header:true,align:token.attrGet('style')?.replace('text-align:','')||null})},
 td:{block:'table_cell',getAttrs:token=>({header:false,align:token.attrGet('style')?.replace('text-align:','')||null})},
 image:{node:'image',getAttrs:token=>({src:token.attrGet('src'),title:token.attrGet('title')||null,alt:token.children?.map(child=>child.content).join('')||''})},
});
// CommonMark soft breaks are newlines, not collapsed spaces in a draft.
composerParser.tokenHandlers.softbreak=state=>state.addText('\n');
const serializer=new MarkdownSerializer({...defaultMarkdownSerializer.nodes,
 markdown_source(state,node){state.write(node.textContent);state.closeBlock(node);},
 list_item(state,node){if(node.attrs.checked!==null)state.write(node.attrs.checked?'[x] ':'[ ] ');state.renderContent(node);},
 table(state,node){
  node.forEach((row,_,index)=>{
   const cells=[];row.forEach(cell=>{
    // Use the same inline serializer, then GFM's escaped pipe convention.
    const paragraph=composerSchema.nodes.paragraph.create(null,cell.content);
    cells.push(serializer.serialize(composerSchema.nodes.doc.create(null,paragraph)).replace(/\|/g,'\\|'));
   });
   state.write('| '+cells.join(' | ')+' |\n');
   if(index===0){const align=[];row.forEach(cell=>align.push(cell.attrs.align==='center'?':---:':cell.attrs.align==='right'?'---:':cell.attrs.align==='left'?':---':'---'));state.write('| '+align.join(' | ')+' |\n');}
  });state.closeBlock(node);
 },
 reference(state,node){state.write('From '+node.attrs.quote.speaker+' (historical reference):\n');for(const line of node.attrs.quote.excerpt.split('\n'))state.write('> '+line+'\n');state.closeBlock(node);}
},{...defaultMarkdownSerializer.marks,strike:{open:'~~',close:'~~',mixable:true,expelEnclosingWhitespace:true}});
export const composerMarkdown=doc=>serializer.serialize(doc);
const sourceDocument=text=>composerSchema.nodes.doc.create(null,composerSchema.nodes.markdown_source.create(null,text?composerSchema.text(text):null));
// The reader already owns the CommonMark/GFM grammar. Compare semantic trees
// before importing rich content, so dialect differences (for example one-tilde
// deletion or extended autolinks) fall back to source instead of changing meaning.
const gfm=unified().use(remarkParse).use(remarkGfm);
const gfmMeaning=text=>JSON.stringify(gfm.parse(text),(key,value)=>key==='position'?undefined:value);
export function parseComposer(text){
 text=text||'';
 const env={};
 try{
  const doc=composerParser.parse(text,env);
  // Reference definitions and website extensions have no lossless rich editor
  // representation. Keep the complete input together (definitions may be remote
  // from their uses). Raw HTML is literal, never executable.
  const extension=/\[\^[^\]]+\]|\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]|\$\$|(?<!\\)\$[^\n$]+\$|<\/?[A-Za-z][\w-]*(?:\s[^>]*|\/?)>|<!--/;
  let unsupported=false;
  doc.descendants(node=>{if(node.type.spec.code||node.marks.some(mark=>mark.type.spec.code))return false;if(node.isText&&extension.test(node.text))unsupported=true;});
  if(Object.keys(env.references||{}).length||unsupported||text.includes('\r')||/^---\n[^\n]+:[^\n]*(?:\n[\s\S]*?)?\n---(?:\n|$)/.test(text))return sourceDocument(text);
  // Unknown syntax must never disappear because a serializer cannot express it.
  // Preserve instead of silently accepting a lossy parse/serialize cycle.
  const markdown=composerMarkdown(doc);
  if(!composerParser.parse(markdown).eq(doc)||gfmMeaning(text)!==gfmMeaning(markdown))return sourceDocument(text);
  return doc;
 }catch{return sourceDocument(text);}
}
export function moveComposerBoundary(view,event){
 if(view.composing||event.isComposing||event.keyCode===229||event.altKey||!(event.ctrlKey||event.metaKey)||!['Home','End'].includes(event.key))return false;
 const edge=event.key==='Home'?TextSelection.atStart(view.state.doc):TextSelection.atEnd(view.state.doc);
 view.dispatch(view.state.tr.setSelection(event.shiftKey?TextSelection.create(view.state.doc,view.state.selection.anchor,edge.head):edge).scrollIntoView());
 // A browser selection can remain in a noneditable quote/control even when
 // the editor owns focus. Explicit keyboard navigation must synchronize it.
 view.focus();
 return true;
}
function commentaryContent(node){
 if(['markdown_source','table','table_row','table_cell'].includes(node.type)||node.marks?.some(mark=>mark.type==='strike'))return null;
 const attrs={...node.attrs};delete attrs.checked;
 if(node.attrs?.checked!==null&&node.attrs?.checked!==undefined)return null;
 const content=node.content?.map(commentaryContent);
 if(content?.some(child=>!child))return null;
 return {...node,...(node.attrs?{attrs}:{}),...(content?{content}:{})};
}
export function composerDocument(doc,preserveCommentary=false){
 let checkpoint=false;
 doc.forEach(node=>{if(node.type.name==='reference'||node.type.name==='markdown_source')checkpoint=true;});
 // Markdown is the readable projection, not an exact editor checkpoint. Keep
 // structured commentary even after the last quote is removed if reparsing
 // would change it (for example, a split paragraph's trailing space).
 // Decide before serializing segments that ordinary drafts would discard.
 if(!checkpoint&&(!preserveCommentary||parseComposer(composerMarkdown(doc)).eq(doc)))return null;
 const segments=[];
 doc.forEach(node=>{
  if(node.type.name==='reference')segments.push({type:'quote',instanceId:node.attrs.instanceId,quote:node.attrs.quote});
  else {
   segments.push({type:'text',text:composerMarkdown(composerSchema.nodes.doc.create(null,node)),content:node.toJSON()});
  }
 });
 return {version:1,segments};
}
// Private draft checkpoints are lossless editor state. Send keeps the existing
// attributed-document contract: new syntax travels as literal commentary text,
// never as a different kind of reference or a widened send-time schema.
export function composerSendDocument(doc,preserveCommentary=false){
 const document=composerDocument(doc,preserveCommentary);
 return document?{...document,segments:document.segments.map(part=>{
  if(part.type!=='text')return part;
  const content=commentaryContent(part.content);
  return {type:'text',text:part.text,...(content?{content}:{})};
 })}:null;
}
export function parseDocument(text,document){
 if(!document?.segments)return parseComposer(text);
 const nodes=[];
 for(const part of document.segments){
  if(part.type==='quote')nodes.push(composerSchema.nodes.reference.create({instanceId:part.instanceId,quote:part.quote}));
  else if(part.content){
   const node=composerSchema.nodeFromJSON(part.content);node.check();
   let valid=node.isBlock&&node.type.name!=='doc';
   const check=item=>{
    if(item.type.name==='reference')valid=false;
    for(const mark of item.marks)if(mark.type.name==='link'&&!tokenizer.validateLink(mark.attrs.href))valid=false;
   };
   check(node);node.descendants(check);
   if(!valid||composerMarkdown(composerSchema.nodes.doc.create(null,node))!==part.text)throw Error('The saved commentary does not match its editor content.');
   nodes.push(node);
  }else parseComposer(part.text).forEach(node=>nodes.push(node));
 }
 return composerSchema.nodes.doc.create(null,nodes.length?nodes:[composerSchema.nodes.paragraph.create()]);
}
export function insertQuote(view,quote,target){
 if(view.composing||view.state.doc!==target.doc)throw Error('The draft changed. Your text was kept; add the quote again.');
 const existing=composerDocument(view.state.doc)?.segments.filter(part=>part.type==='quote')||[];
 if(existing.length>=12||existing.reduce((n,part)=>n+[...part.quote.excerpt].length,0)+[...quote.excerpt].length>24000)throw Error('Keep at most 12 quotes and 24,000 quoted characters. Nothing was shortened.');
 const node=composerSchema.nodes.reference.create({instanceId:crypto.randomUUID(),quote});
 if(target.pos===view.state.doc.content.size){
  const tr=closeHistory(view.state.tr).insert(target.pos,[node,composerSchema.nodes.paragraph.create()]);
  view.dispatch(tr.setSelection(TextSelection.create(tr.doc,target.pos+node.nodeSize+1)));
  return;
 }
 if(view.state.doc.resolve(target.pos).depth>1)throw Error('Place the quote between paragraphs or choose Add quotes at end. Your draft was kept.');
 // Collapse at the captured end: never replace selected commentary.
 let tr=closeHistory(view.state.tr);
 if(view.state.doc.resolve(target.pos).depth===0)tr.insert(target.pos,node);
 else tr.setSelection(TextSelection.create(view.state.doc,target.pos)).replaceSelectionWith(node);
 let after;
 tr.doc.forEach((child,pos)=>{if(child.attrs.instanceId===node.attrs.instanceId)after=pos+child.nodeSize;});
 if(!tr.doc.nodeAt(after)?.isTextblock)tr.insert(after,composerSchema.nodes.paragraph.create());
 // Never leave NodeSelection on the immutable block: the next keystroke
 // would replace it. This updates the bookmark without focusing or scrolling.
 view.dispatch(tr.setSelection(TextSelection.create(tr.doc,after+1)));
}
export function changeQuote(view,id,direction){
 const positions=[];view.state.doc.forEach((node,pos)=>{if(node.type.name==='reference')positions.push({node,pos});});
 const index=positions.findIndex(row=>row.node.attrs.instanceId===id),here=positions[index];if(!here)return;
 let tr=closeHistory(view.state.tr);
 if(direction===0)tr=tr.delete(here.pos,here.pos+here.node.nodeSize);
 else{
  const other=positions[index+direction];if(!other)return;
  // Equal-sized atomic nodes swap; every commentary node stays untouched.
  tr=tr.replaceWith(here.pos,here.pos+here.node.nodeSize,other.node).replaceWith(other.pos,other.pos+other.node.nodeSize,here.node);
 }
 view.dispatch(tr);
}
export function composerClipboard(slice){
 const content=slice.content.firstChild?.isInline?composerSchema.nodes.paragraph.create(null,slice.content):slice.content;
 return composerMarkdown(composerSchema.nodes.doc.create(null,content));
}

function markRule(pattern,mark){
 return new InputRule(pattern,(state,match,start,end)=>{
  const from=start+match[0].indexOf(match[1]),text=match[2];
  const marks=mark.addToSet(state.doc.resolve(from).marks());
  const tr=state.tr.replaceWith(from,end,composerSchema.text(text,marks));
  return tr.setSelection(TextSelection.create(tr.doc,from+text.length)).removeStoredMark(mark);
 },{inCodeMark:false});
}
function fenceTransaction(state,params){
 const {$from}=state.selection,pos=$from.before(),code=composerSchema.nodes.code_block.create({params});
 const firstListParagraph=$from.depth>1&&$from.node(-1).type===composerSchema.nodes.list_item&&$from.index(-1)===0;
 const replacement=firstListParagraph?[composerSchema.nodes.paragraph.create(),code]:code;
 const tr=state.tr.replaceWith(pos,$from.after(),replacement);
 if(firstListParagraph){
  const list=$from.node(-2);
  tr.setNodeMarkup($from.before(-2),null,{...list.attrs,tight:false});
 }
 return tr.setSelection(TextSelection.create(tr.doc,pos+(firstListParagraph?3:1)));
}
export function composerRules(){
 const {nodes,marks}=composerSchema;
 return inputRules({rules:[
  markRule(/(?:^|[\s(])(\*\*([^*\s](?:[^*]*[^*\s])?)\*\*)$/,marks.strong.create()),
  markRule(/(?:^|[\s(])(__([^_\s](?:[^_]*[^_\s])?)__)$/,marks.strong.create()),
  markRule(/(?:^|[\s(])(\*([^*\s](?:[^*]*[^*\s])?)\*)$/,marks.em.create()),
  markRule(/(?:^|[\s(])(_([^_\s](?:[^_]*[^_\s])?)_)$/,marks.em.create()),
  markRule(/(?:^|[\s(])(~~([^~\s](?:[^~]*[^~\s])?)~~)$/,marks.strike.create()),
  markRule(/(`([^`]+)`)$/,marks.code.create()),
  new InputRule(/\[([^\]\n]+)\]\(([^\s)]+)\)$/,(state,match,start,end)=>{
   if(start>0&&state.doc.textBetween(start-1,start)==='!')return null;
   const parsed=parseComposer(match[0]).firstChild;
   if(!parsed?.firstChild?.marks.some(mark=>mark.type===marks.link))return null;
   const tr=state.tr.replaceWith(start,end,parsed.content);
   return tr.setSelection(TextSelection.create(tr.doc,start+parsed.content.size)).removeStoredMark(marks.link);
  },{inCodeMark:false}),
  new InputRule(/(?:^|\s)(https?:\/\/\S+)\s$/,(state,match,start,end)=>{
   const parsed=parseComposer(match[1]).firstChild;
   if(!parsed?.firstChild?.marks.some(mark=>mark.type===marks.link))return null;
   const from=start+match[0].indexOf(match[1]);
   return state.tr.replaceWith(from,end,parsed.content).insertText(' ',from+parsed.content.size).removeStoredMark(marks.link);
  },{inCodeMark:false}),
  textblockTypeInputRule(/^(#{1,6})\s$/,nodes.heading,match=>({level:match[1].length})),
  wrappingInputRule(/^\s*([-+*])\s$/,nodes.bullet_list,{tight:true}),
  wrappingInputRule(/^(\d+)\.\s$/,nodes.ordered_list,match=>({order:+match[1],tight:true}),(match,node)=>node.childCount+node.attrs.order===+match[1]),
  wrappingInputRule(/^>\s$/,nodes.blockquote),
  new InputRule(/^ {0,3}(?:`{3,}|~{3,})([\w+-]*)[ \t]$/,(state,match)=>state.selection.$from.parent.type===nodes.paragraph?fenceTransaction(state,match[1]):null),
  new InputRule(/^\[([ xX])\] $/,(state,match,start,end)=>{
   const {$from}=state.selection;if($from.depth<2||$from.node(-1).type!==nodes.list_item)return null;
   return state.tr.delete(start,end).setNodeMarkup($from.before(-1),null,{checked:match[1]!==' '});
  }),
 ]});
}

export function composerFormat(state){
 const {$from,from,to}=state.selection;
 let block='text';
 for(let depth=$from.depth;depth>0;depth--){
  const node=$from.node(depth);
  if(node.type.name==='bullet_list'||node.type.name==='ordered_list'){block=node.type.name;break;}
  if(node.type.name==='heading')block='h'+node.attrs.level;
  if(['code_block','markdown_source','blockquote'].includes(node.type.name))block=node.type.name;
  if(node.type.name==='table'){block='table';break;}
  if(node.type.name==='list_item'&&node.attrs.checked!==null){block='task_list';break;}
 }
 const active=name=>state.doc.rangeHasMark(from,to,composerSchema.marks[name]);
 return {block,bold:active('strong'),italic:active('em'),link:active('link'),code:active('code'),strike:active('strike'),literal:$from.parent.type.spec.code===true,protected:!composerSelectionEditable(state)};
}

export function composerSelectionEditable(state){
 let valid=true;
 state.doc.nodesBetween(state.selection.from,state.selection.to,node=>{if(node.type.name==='reference')valid=false;});
 return valid;
}
export function toggleComposerMark(view,name){
 if(!composerSelectionEditable(view.state)||view.composing)return false;
 const command=toggleMark(composerSchema.marks[name]);
 const changed=command(view.state,tr=>view.dispatch(closeHistory(tr)),view);
 view.focus();return changed;
}
export function formatComposerBlock(view,kind){
 const {nodes}=composerSchema;
 if(!['text','h1','h2','h3','h4','h5','h6','bullet_list','ordered_list','task_list','blockquote','code_block','markdown_source'].includes(kind)||view.composing||!composerSelectionEditable(view.state))return;
 // Apply the existing commands to a temporary state, dispatching one transaction
 // so a single Undo reverses the complete format operation.
 const original=view.state,steps=[];
 let state=original;
 const dispatch=tr=>{steps.push(...tr.steps);state=state.apply(tr);};
 const run=command=>command(state,dispatch,view);
 if(kind==='markdown_source'){
  // Source mode preserves the selected blocks' current Markdown, including marks.
  const {$from,$to}=state.selection,from=$from.depth?$from.before(1):0,to=$to.depth?$to.after(1):state.doc.content.size;
  const content=state.doc.slice(from,to).content;
  const text=composerMarkdown(nodes.doc.create(null,content));
  const tr=closeHistory(state.tr).replaceWith(from,to,nodes.markdown_source.create(null,text?composerSchema.text(text):null));
  view.dispatch(tr.setSelection(TextSelection.create(tr.doc,from+1)));view.focus();return;
 }
 // Do not flatten tables or silently discard their cells through a block command.
 let table=false;state.doc.nodesBetween(state.selection.from,state.selection.to,node=>{if(node.type.name==='table'||node.type.name==='table_cell')table=true;});
 if(table||state.selection.$from.parent.type.name==='table_cell')return;
 if(state.selection.$from.parent.type.name==='markdown_source'&&kind!=='text')return;
 if(!(state.selection instanceof TextSelection))dispatch(state.tr.setSelection(TextSelection.between(state.selection.$from,state.selection.$to)));
 // Lift just the selected items before converting, leaving their siblings intact.
 while(run(liftListItem(nodes.list_item))){}
 // Unwrap selected quote paragraphs before reformatting (not attributed quotes).
 while(state.selection.$from.depth>1&&state.selection.$from.node(-1).type===nodes.blockquote&&run(lift)){}
 run(setBlockType(kind==='code_block'?nodes.code_block:kind.startsWith('h')?nodes.heading:nodes.paragraph,kind.startsWith('h')?{level:Number(kind[1])}:null));
 if(kind.endsWith('_list'))run(wrapInList(kind==='task_list'?nodes.bullet_list:nodes[kind],{tight:true}));
 if(kind==='task_list'){
  const tr=state.tr;state.doc.nodesBetween(state.selection.from,state.selection.to,(node,pos)=>{if(node.type===nodes.list_item)tr.setNodeMarkup(pos,null,{checked:false});});dispatch(tr);
 }
 if(kind==='blockquote')run(wrapIn(nodes.blockquote));
 if(steps.length){const tr=closeHistory(original.tr);for(const step of steps)tr.step(step);tr.setSelection(state.selection.getBookmark().resolve(tr.doc));view.dispatch(tr.scrollIntoView());}
 view.focus();
}

export function setComposerLink(view,value){
 const href=value.trim(),{from,to}=view.state.selection;
 if(from===to||view.composing||!composerSelectionEditable(view.state))return false;
 if(href&&(/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(href)&&!/^(https?:\/\/|mailto:)/i.test(href)||/[\s\u0000-\u001f\u007f]/.test(href)||!tokenizer.validateLink(href)))return false;
 const tr=view.state.tr.removeMark(from,to,composerSchema.marks.link);
 if(href)tr.addMark(from,to,composerSchema.marks.link.create({href}));
 view.dispatch(closeHistory(tr));view.focus();return true;
}
const splitComposerListItem=(state,dispatch)=>{
 const {$from}=state.selection,item=$from.depth>1?$from.node(-1):null;
 return splitListItem(composerSchema.nodes.list_item,item?.attrs.checked!=null?{checked:false}:undefined)(state,dispatch);
};
function exitComposerTable(state,dispatch){
 const {$from}=state.selection;let depth=$from.depth;
 while(depth&&$from.node(depth).type!==composerSchema.nodes.table)depth--;
 if(!depth)return false;
 if(dispatch){
  const pos=$from.after(depth),tr=closeHistory(state.tr);
  if(tr.doc.nodeAt(pos)?.type!==composerSchema.nodes.paragraph)tr.insert(pos,composerSchema.nodes.paragraph.create());
  dispatch(tr.setSelection(TextSelection.create(tr.doc,pos+1)).scrollIntoView());
 }
 return true;
}
export const composerNewline=chainCommands(exitComposerTable,newlineInCode,splitComposerListItem,createParagraphNear,liftEmptyBlock,splitBlock);
export function composerEnter(view,{continueWriting=false,exit=false}={}){
 if(view.composing)return false;
 const {state}=view,{$from,empty}=state.selection;
 if($from.parent.type.spec.code){
  if(exit){exitCode(state,tr=>view.dispatch(closeHistory(tr)));return true;}
  const line=$from.parent.textContent.slice(0,$from.parentOffset).split('\n').at(-1);
  view.dispatch(state.tr.insertText('\n'+(empty&&$from.parent.type.name==='code_block'?/^[ \t]*/.exec(line)[0]:'')).scrollIntoView());
  return true;
 }
 if(empty&&$from.parent.type===composerSchema.nodes.paragraph&&$from.parentOffset===$from.parent.content.size){
  const match=/^ {0,3}(`{3,}|~{3,})([^\n]*)$/.exec($from.parent.textContent);
  if(match&&!(match[1][0]==='`'&&match[2].includes('`'))){
   view.dispatch(closeHistory(fenceTransaction(state,match[2].trim())));return true;
  }
 }
 if(continueWriting){composerNewline(state,view.dispatch,view);return true;}
 return false;
}
function besideQuote(state,direction){
 const {empty,$from}=state.selection;if(!empty||$from.depth!==1)return false;
 if(direction<0&&$from.parentOffset!==0||direction>0&&$from.parentOffset!==$from.parent.content.size)return false;
 const boundary=state.doc.resolve(direction<0?$from.before():$from.after());
 return (direction<0?boundary.nodeBefore:boundary.nodeAfter)?.type.name==='reference';
}
export function emptyComposerCode(state,dispatch){
 const {empty,$from}=state.selection;
 if(!empty||$from.parent.content.size||!$from.parent.type.spec.code)return false;
 return setBlockType(composerSchema.nodes.paragraph)(state,tr=>dispatch?.(closeHistory(tr)));
}
export function composerState(text,document){
 const {marks,nodes}=composerSchema;
 return EditorState.create({schema:composerSchema,doc:parseDocument(text,document),plugins:[
  new Plugin({filterTransaction(tr){
   let valid=true;
   tr.doc.descendants((node,pos,parent)=>{
    if(node.type.name==='reference'&&parent.type.name!=='doc')valid=false;
    if(node.type===nodes.table){
     const width=node.firstChild.childCount;
     node.forEach(row=>{if(row.childCount!==width)valid=false;});
    }
    if(node.type===nodes.table_cell){
     if(/[\r\n]/.test(node.textContent))valid=false;
     node.forEach(child=>{if(child.type===nodes.hard_break)valid=false;});
    }
   });
   return valid;
  }}),
  composerRules(),history(),keymap({
   'Mod-z':undo,'Shift-Mod-z':redo,'Mod-y':redo,
   'Mod-b':(state,dispatch,view)=>toggleComposerMark(view,'strong'),'Mod-i':(state,dispatch,view)=>toggleComposerMark(view,'em'),'Mod-`':(state,dispatch,view)=>toggleComposerMark(view,'code'),
   'Mod-Shift-x':(state,dispatch,view)=>toggleComposerMark(view,'strike'),
   'Backspace':chainCommands(state=>besideQuote(state,-1),undoInputRule,emptyComposerCode,(state,dispatch)=>state.selection.empty&&state.selection.$from.parentOffset===0&&liftListItem(nodes.list_item)(state,dispatch)),
   'Delete':state=>besideQuote(state,1),
   'Tab':sinkListItem(nodes.list_item),'Shift-Tab':liftListItem(nodes.list_item),
  }),keymap(baseKeymap),
 ]});
}
export function pasteMarkdown(view,text){
 if(view.state.selection.$from.parent.type.spec.code){view.dispatch(view.state.tr.insertText(text).scrollIntoView());return;}
 if(view.state.selection.$from.parent.type===composerSchema.nodes.table_cell){
  // GFM cells are single-line; move a multi-line paste to the following paragraph
  // instead of creating ragged rows or silently flattening its source.
  if(/[\r\n]/.test(text))exitComposerTable(view.state,view.dispatch);
  else {view.dispatch(view.state.tr.insertText(text).scrollIntoView());return;}
 }
 const doc=parseComposer(text);
 if(view.state.doc.childCount===1&&view.state.doc.firstChild.type===composerSchema.nodes.paragraph&&!view.state.doc.firstChild.content.size){
  const tr=view.state.tr.replaceWith(0,view.state.doc.content.size,doc.content);
  view.dispatch(tr.setSelection(TextSelection.atEnd(tr.doc)).scrollIntoView());return;
 }
 if(doc.firstChild.type.name==='markdown_source'){
  view.dispatch(view.state.tr.replaceSelection(new Slice(doc.content,0,0)).scrollIntoView());return;
 }
 view.dispatch(view.state.tr.replaceSelection(Slice.maxOpen(doc.content)).scrollIntoView());
}
