import {Schema,Slice} from 'prosemirror-model';
import {EditorState,TextSelection,Plugin} from 'prosemirror-state';
import {schema as markdownSchema,MarkdownParser,MarkdownSerializer,defaultMarkdownParser,defaultMarkdownSerializer} from 'prosemirror-markdown';
import {InputRule,inputRules,wrappingInputRule,textblockTypeInputRule,undoInputRule} from 'prosemirror-inputrules';
import {keymap} from 'prosemirror-keymap';
import {baseKeymap,chainCommands,newlineInCode,createParagraphNear,liftEmptyBlock,splitBlock,toggleMark,setBlockType} from 'prosemirror-commands';
import {history,undo,redo,closeHistory} from 'prosemirror-history';
import {splitListItem,liftListItem,sinkListItem,wrapInList} from 'prosemirror-schema-list';

// Markdown image references remain editable without fetching remote images.
// Actual images use the existing attachment upload path.
export const composerSchema=new Schema({nodes:markdownSchema.spec.nodes.update('image',{
 ...markdownSchema.spec.nodes.get('image'),
 toDOM:node=>['span',{'class':'a-composer-image-reference',title:node.attrs.src},node.attrs.alt||'Image reference'],
}).addToEnd('reference',{
 group:'block',atom:true,selectable:false,draggable:false,isolating:true,
 attrs:{instanceId:{},quote:{}},
 // Never parse pasted HTML into an attributed quote.
 toDOM:node=>['blockquote',{'data-quote-instance':node.attrs.instanceId},node.attrs.quote.excerpt],
}),marks:markdownSchema.spec.marks});
// Give the composer its own tokenizer; raw web links and labeled Markdown
// links share the parser's URL validation without changing other consumers.
const tokenizer=new defaultMarkdownParser.tokenizer.constructor('commonmark',{html:false,linkify:true}).enable('linkify');
export const composerParser=new MarkdownParser(composerSchema,tokenizer,defaultMarkdownParser.tokens);
const serializer=new MarkdownSerializer({...defaultMarkdownSerializer.nodes,
 reference(state,node){state.write('From '+node.attrs.quote.speaker+' (historical reference):\n');for(const line of node.attrs.quote.excerpt.split('\n'))state.write('> '+line+'\n');state.closeBlock(node);}
},defaultMarkdownSerializer.marks);
export const composerMarkdown=doc=>serializer.serialize(doc);
export const parseComposer=text=>composerParser.parse(text||'');
export function moveComposerBoundary(view,event){
 if(view.composing||event.isComposing||event.keyCode===229||event.altKey||!(event.ctrlKey||event.metaKey)||!['Home','End'].includes(event.key))return false;
 const edge=event.key==='Home'?TextSelection.atStart(view.state.doc):TextSelection.atEnd(view.state.doc);
 view.dispatch(view.state.tr.setSelection(event.shiftKey?TextSelection.create(view.state.doc,view.state.selection.anchor,edge.head):edge).scrollIntoView());
 // A browser selection can remain in a noneditable quote/control even when
 // the editor owns focus. Explicit keyboard navigation must synchronize it.
 view.focus();
 return true;
}
export function composerDocument(doc,preserveCommentary=false){
 const segments=[];let quoted=false;
 doc.forEach(node=>{
  if(node.type.name==='reference'){quoted=true;segments.push({type:'quote',instanceId:node.attrs.instanceId,quote:node.attrs.quote});}
  else segments.push({type:'text',text:composerMarkdown(composerSchema.nodes.doc.create(null,node)),content:node.toJSON()});
 });
 // Markdown is the readable projection, not an exact editor checkpoint. Keep
 // structured commentary even after the last quote is removed if reparsing
 // would change it (for example, a split paragraph's trailing space).
 return quoted||preserveCommentary&&!parseComposer(composerMarkdown(doc)).eq(doc)?{version:1,segments}:null;
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
export function composerRules(){
 const {nodes,marks}=composerSchema;
 return inputRules({rules:[
  markRule(/(?:^|[\s(])(\*\*([^*\s](?:[^*]*[^*\s])?)\*\*)$/,marks.strong.create()),
  markRule(/(?:^|[\s(])(__([^_\s](?:[^_]*[^_\s])?)__)$/,marks.strong.create()),
  markRule(/(?:^|[\s(])(\*([^*\s](?:[^*]*[^*\s])?)\*)$/,marks.em.create()),
  markRule(/(?:^|[\s(])(_([^_\s](?:[^_]*[^_\s])?)_)$/,marks.em.create()),
  markRule(/(`([^`]+)`)$/,marks.code.create()),
  new InputRule(/\[([^\]\n]+)\]\(([^\s)]+)\)$/,(state,match,start,end)=>{
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
  textblockTypeInputRule(/^```([\w-]*)\s$/,nodes.code_block,match=>({params:match[1]})),
 ]});
}

export function composerFormat(state){
 const {$from,from,to}=state.selection;
 let block='text';
 for(let depth=$from.depth;depth>0;depth--){
  const node=$from.node(depth);
  if(node.type.name==='bullet_list'||node.type.name==='ordered_list'){block=node.type.name;break;}
  if(node.type.name==='heading')block='h'+node.attrs.level;
 }
 const active=name=>state.doc.rangeHasMark(from,to,composerSchema.marks[name]);
 return {block,bold:active('strong'),italic:active('em'),link:active('link')};
}

export function formatComposerBlock(view,kind){
 const {nodes}=composerSchema;
 if(!['text','h1','h2','h3','bullet_list','ordered_list'].includes(kind))return;
 let quoted=false;view.state.doc.nodesBetween(view.state.selection.from,view.state.selection.to,node=>{if(node.type.name==='reference')quoted=true;});
 if(quoted)return;
 if(!(view.state.selection instanceof TextSelection))view.dispatch(view.state.tr.setSelection(TextSelection.between(view.state.selection.$from,view.state.selection.$to)));
 // Lift just the selected items before converting, leaving their siblings intact.
 while(liftListItem(nodes.list_item)(view.state,view.dispatch)){}
 setBlockType(kind.startsWith('h')?nodes.heading:nodes.paragraph,kind.startsWith('h')?{level:Number(kind[1])}:null)(view.state,view.dispatch);
 if(kind.endsWith('_list'))wrapInList(nodes[kind],{tight:true})(view.state,view.dispatch);
 view.focus();
}

export function setComposerLink(view,value){
 const href=value.trim(),{from,to}=view.state.selection;
 if(from===to)return false;
 if(href&&(!/^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(href)||/[\s\u0000-\u001f\u007f]/.test(href)||!tokenizer.validateLink(href)))return false;
 const tr=view.state.tr.removeMark(from,to,composerSchema.marks.link);
 if(href)tr.addMark(from,to,composerSchema.marks.link.create({href}));
 view.dispatch(tr);view.focus();return true;
}
export const composerNewline=chainCommands(newlineInCode,splitListItem(composerSchema.nodes.list_item),createParagraphNear,liftEmptyBlock,splitBlock);
function besideQuote(state,direction){
 const {empty,$from}=state.selection;if(!empty||$from.depth!==1)return false;
 if(direction<0&&$from.parentOffset!==0||direction>0&&$from.parentOffset!==$from.parent.content.size)return false;
 const boundary=state.doc.resolve(direction<0?$from.before():$from.after());
 return (direction<0?boundary.nodeBefore:boundary.nodeAfter)?.type.name==='reference';
}
export function composerState(text,document){
 const {marks,nodes}=composerSchema;
 return EditorState.create({schema:composerSchema,doc:parseDocument(text,document),plugins:[
  new Plugin({filterTransaction(tr){let valid=true;tr.doc.descendants((node,pos,parent)=>{if(node.type.name==='reference'&&parent.type.name!=='doc')valid=false;});return valid;}}),
  composerRules(),history(),keymap({
   'Mod-z':undo,'Shift-Mod-z':redo,'Mod-y':redo,
   'Mod-b':toggleMark(marks.strong),'Mod-i':toggleMark(marks.em),'Mod-`':toggleMark(marks.code),
   'Backspace':chainCommands(state=>besideQuote(state,-1),undoInputRule,(state,dispatch)=>state.selection.empty&&state.selection.$from.parentOffset===0&&liftListItem(nodes.list_item)(state,dispatch)),
   'Delete':state=>besideQuote(state,1),
   'Tab':sinkListItem(nodes.list_item),'Shift-Tab':liftListItem(nodes.list_item),
  }),keymap(baseKeymap),
 ]});
}
export function pasteMarkdown(view,text){
 if(view.state.selection.$from.parent.type.spec.code){view.dispatch(view.state.tr.insertText(text).scrollIntoView());return;}
 const doc=parseComposer(text);
 view.dispatch(view.state.tr.replaceSelection(Slice.maxOpen(doc.content)).scrollIntoView());
}
