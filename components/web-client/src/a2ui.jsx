import React from 'react';
export function A2UISurface({surface,act}){
 const rows=new Map((surface?.components||[]).map(row=>[row.id,row]));
 const render=(id,ancestors=[])=>{
  if(ancestors.length>20||ancestors.includes(id))return null;
  const row=rows.get(id);if(!row)return null;
  const [kind,props]=Object.entries(row.component||{})[0]||[];if(!props)return null;
  const children=()=> (props.children?.explicitList||[]).map(child=><React.Fragment key={child}>{render(child,[...ancestors,id])}</React.Fragment>);
  if(kind==='Text'){const Tag=['h1','h2','h3','h4','h5'].includes(props.usageHint)?props.usageHint:'p';return <Tag className="a-canvas-text">{props.text?.literalString||''}</Tag>}
  if(kind==='Row'||kind==='Column')return <div className={`a-canvas-${kind.toLowerCase()}`}>{children()}</div>;
  if(kind==='Card')return <div className="a-canvas-card">{render(props.child,[...ancestors,id])}</div>;
  if(kind==='Divider')return <hr/>;
  if(kind==='Button')return <button type="button" className="a-soft" data-action="canvas.event" onClick={()=>act('canvas.event',{surfaceId:surface.surfaceId,componentId:id,name:props.action.name})}>{render(props.child,[...ancestors,id])}</button>;
  return null;
 };
 return <div className="a-canvas-surface" data-part="agent-surface">{render(surface?.root)}</div>;
}
