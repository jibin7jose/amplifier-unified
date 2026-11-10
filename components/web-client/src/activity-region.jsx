import React,{useRef,useEffect} from 'react';
import {beginRegionActivity} from './activity-feedback';

export function useRegionActivity(ref,busy){
 useEffect(()=>busy?beginRegionActivity(ref.current):undefined,[busy]);
}
export function ActivityRegion({busy=false,name,children,className='',as:Tag='div',...props}){
 const ref=useRef(null);useRegionActivity(ref,busy);
 return <Tag {...props} ref={ref} className={className} data-activity-region={name||''}>{children}</Tag>;
}

// Retain results only for the same source while it refreshes. An empty
// successful response replaces the previous result; switching sources never
// borrows another source's options or output.
export function useRefreshValue(key,value,refreshing){
 const previous=useRef(null);
 if(!refreshing)previous.current={key,value};
 return refreshing&&previous.current?.key===key?previous.current.value:value;
}
