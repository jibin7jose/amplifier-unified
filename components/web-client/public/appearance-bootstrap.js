// Runs synchronously before the app stylesheet or first paint. This cache only
// colors the loading screen; the host remains authoritative for presentation.
(()=>{
 const root=document.documentElement;
 const color=value=>typeof value==='string'&&value.length<160&&(
  /^#[0-9a-f]{6}$/i.test(value)||
  /^(?:rgba?|hsla?|oklab|oklch|lab|lch|color)\([\w\d\s.,%/+*-]+\)$/i.test(value)
 )&&CSS.supports('color',value);
 try{
  const saved=JSON.parse(sessionStorage.getItem('amplifier.appearance')||'null');
  if(saved?.version!==1||!['light','dark','system'].includes(saved.scheme))return;
  const mode=saved.scheme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):saved.scheme;
  root.style.colorScheme=mode;root.dataset.bootScheme=mode;
  const colors=saved.colors?.[mode];
  root.style.setProperty('--boot-bg',color(colors?.background)?colors.background:mode==='dark'?'#151b31':'#e8eeff');
  root.style.setProperty('--boot-ink',color(colors?.foreground)?colors.foreground:mode==='dark'?'#eef2ff':'#263459');
  const chrome=document.querySelector('meta[name="theme-color"]');
  if(chrome)chrome.content=color(colors?.chrome)?colors.chrome:color(colors?.background)?colors.background:mode==='dark'?'#151b31':'#e8eeff';
 }catch{/* Unavailable or stale storage must never prevent the app from loading. */}
})();
