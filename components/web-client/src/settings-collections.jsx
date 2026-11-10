import {SettingsActions,SettingsLayoutContext} from './settings-layout';
import React,{useEffect,useRef,useContext,useState} from 'react';
import {ArrowLeft,ArrowUp,ArrowDown,ChevronRight} from 'lucide-react';
import './settings-collections.css';
import {ReorderList} from './reorder-list';

export function CollectionRow({id,label,description,selected,onSelect,checked,onCheck,disabled,badge,checkLabel,checkAction='view.update'}){
 return <div className={'a-collection-row'+(selected?' selected':'')} data-collection-id={id}>
  {onCheck&&<label className="a-collection-check"><input type="checkbox" aria-label={checkLabel||'Select '+label} checked={!!checked} disabled={disabled} data-action={checkAction} onChange={e=>onCheck(e.target.checked)}/></label>}
  <button type="button" aria-current={selected?'true':undefined} data-action="view.update" onClick={onSelect}><span><strong>{label}</strong>{description&&<small>{description}</small>}</span>{badge&&<small className="a-collection-badge">{badge}</small>}<ChevronRight aria-hidden="true"/></button>
 </div>;
}
export function Collection({list,children,detailOpen=true,onBack,label='items'}){
 const listRef=useRef(null),detailRef=useRef(null),layout=useContext(SettingsLayoutContext);
 useEffect(()=>{if(detailOpen&&layout.compact){detailRef.current?.focus({preventScroll:true});}},[detailOpen,layout.compact]);
 const back=()=>{onBack?.();requestAnimationFrame(()=>listRef.current?.querySelector('button[aria-current=true],button')?.focus({preventScroll:true}));};
 return <div className={'a-collection'+(detailOpen?' detail-open':'')}><div className="a-collection-list" ref={listRef}>{list}</div><div className="a-collection-detail" ref={detailRef} tabIndex={-1}>
 {onBack&&!layout.compact&&<button type="button" className="a-link a-collection-back" data-action="view.update" onClick={back}><ArrowLeft/>Back to {label}</button>}{children}</div></div>;
}
export {moveItem} from './reorder-list';

export function OrderEditor({title,description,items,ids,onChange,onSave,onCancel,busy=false,error,notice,action='view.update'}){
 const [dragging,setDragging]=useState(false);
 return <section className="a-order-editor">
  <h4>{title}</h4>{notice&&<p role="status">{notice}</p>}<p>{description}</p><p className="a-caption">Drag the handle or choose a position. Changes apply when you save.</p>
  <ReorderList items={items} ids={ids} onChange={onChange} onDraggingChange={setDragging} busy={busy} className="a-order-list" rowClassName="a-order-item" floatingClassName="a-order-item" savedHint="Save order to apply." renderItem={(item,{handle,index,move})=><>
   {handle}<span className="a-order-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
   <span className="a-order-arrows"><button type="button" className="a-icon" disabled={busy||index===0} aria-label={'Move '+item.label+' up'} data-action="view.update" onClick={()=>move(index-1)}><ArrowUp/></button><button type="button" className="a-icon" disabled={busy||index===ids.length-1} aria-label={'Move '+item.label+' down'} data-action="view.update" onClick={()=>move(index+1)}><ArrowDown/></button></span>
   <select aria-label={'Position of '+item.label} value={index} disabled={busy} data-action="view.update" onChange={e=>move(Number(e.target.value))}>{ids.map((_,i)=><option key={i} value={i}>{i+1}</option>)}</select>
  </>}/>
  {error&&<p role="alert" className="a-danger">{error}</p>}
  <SettingsActions><button type="button" className="a-primary" disabled={busy||dragging} data-action={action} onClick={onSave}>{busy?'Saving order…':'Save order'}</button><button type="button" className="a-soft" disabled={busy} data-action="view.update" onClick={onCancel}>Cancel</button></SettingsActions>
 </section>;
}
