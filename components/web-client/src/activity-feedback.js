const regions=new WeakMap();
export const activityRegion=element=>element?.closest?.('[data-activity-region],.a-model-popover,.a-location-picker,.a-settings-page-body,.a-card,section,form');
export function beginRegionActivity(region){
 if(!region)return ()=>{};
 const record=regions.get(region)||{tokens:new Set(),busy:region.getAttribute('aria-busy')},token=Symbol();record.tokens.add(token);regions.set(region,record);
 region.setAttribute('data-region-pending','');region.setAttribute('aria-busy','true');
 let done=false;
 return ()=>{if(done)return;done=true;record.tokens.delete(token);if(!record.tokens.size){region.removeAttribute('data-region-pending');if(record.busy===null)region.removeAttribute('aria-busy');else region.setAttribute('aria-busy',record.busy);regions.delete(region)}};
}
