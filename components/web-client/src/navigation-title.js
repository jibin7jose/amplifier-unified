// Share the same bounded hover/focus reveal across chat and workspace rows.
export function revealNavigationTitles(event){
 for(const viewport of event.currentTarget.querySelectorAll('[data-scroll-title]')){
  const text=viewport.firstElementChild;
  if(!text)continue;
  const distance=Math.max(0,text.scrollWidth-viewport.clientWidth);
  viewport.dataset.overflow=String(distance>1);
  viewport.style.setProperty('--title-overflow',`${-distance}px`);
  viewport.style.setProperty('--title-duration',`${Math.max(3,distance/28)}s`);
 }
}
