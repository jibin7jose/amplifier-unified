import {instance} from '@viz-js/viz';
let viz;
self.onmessage=async({data})=>{
 try{
  viz ||= await instance();
  // Graphviz defaults yield to attributes in the document, including explicit
  // shapes, colors, layout direction and cluster styling.
  const options={engine:data.engine||'dot',
   graphAttributes:{bgcolor:'transparent',overlap:'false',pad:'0.35',nodesep:'0.45',ranksep:'0.65',fontname:'Arial',fontsize:'13'},
   nodeAttributes:{fontname:'Arial',fontsize:'12',shape:'box',style:'rounded,filled',margin:'0.18,0.12',penwidth:'1.2',fillcolor:'#f3f2ff',color:'#aaa1df',fontcolor:'#24213e'},
   edgeAttributes:{fontname:'Arial',fontsize:'10',arrowsize:'0.7',penwidth:'1.2',color:'#8981b5',fontcolor:'#5d5779'}};
  const result=viz.renderFormats(data.source,['svg','json'],options);
  if(result.status!=='success')throw Error(result.errors.map(e=>e.message).join('\n'));
  const graph=JSON.parse(result.output.json),hidden=new Set(['_gvid','pos','width','height','rects','vertices']);
  const nodes=(graph.objects||[]).filter(n=>!n.nodes).slice(0,1000).map(n=>Object.fromEntries(Object.entries(n).filter(([k])=>!k.startsWith('_')&&!hidden.has(k))));
  self.postMessage({svg:result.output.svg,nodes});
 }catch(e){self.postMessage({error:e.message})}
};
