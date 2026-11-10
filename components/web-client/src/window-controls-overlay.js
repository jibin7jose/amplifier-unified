// Geometry belongs to the browser, not to an appearance or display-mode guess.
// This controller owns only attributes/variables; it never remounts a viewer.
const properties=['--wco-x','--wco-y','--wco-width','--wco-height','--wco-safe-top'];

export function validTitlebarRect(rect,width,height){
  if(!rect||![width,height,rect.x,rect.y,rect.width,rect.height].every(Number.isFinite))return false;
  return width>0&&height>0&&rect.x>=0&&rect.y>=0&&rect.width>=1&&
    rect.height>=24&&rect.height<=160&&rect.y<=160&&
    rect.x+rect.width<=width&&rect.y+rect.height<=height;
}

export function bindWindowControlsOverlay(root){
  if(!root)return()=>{};
  const win=root.ownerDocument?.defaultView;
  const overlay=win?.navigator?.windowControlsOverlay;
  let frame=null,disposed=false;
  const reset=()=>{
    root.removeAttribute('data-window-controls-overlay');
    root.removeAttribute('data-window-controls-overlay-stacked');
    for(const name of properties)root.style.removeProperty(name);
  };
  reset();
  if(!overlay||typeof overlay.getTitlebarAreaRect!=='function')return reset;
  const update=()=>{
    frame=null;
    if(disposed)return;
    let rect;
    try{if(overlay.visible!==true){reset();return}rect=overlay.getTitlebarAreaRect()}catch{reset();return}
    if(!validTitlebarRect(rect,win.innerWidth,win.innerHeight)){reset();return}
    for(const [name,value] of [['x',rect.x],['y',rect.y],['width',rect.width],['height',rect.height],['safe-top',Math.max(48,rect.y+rect.height)]]){
      root.style.setProperty('--wco-'+name,value+'px');
    }
    // A very small usable titlebar cannot hold all app controls. Keep a blank
    // native strip and place the ordinary header below it instead of clipping.
    // A nonzero y must not shrink the 48px control row below its target size.
    root.setAttribute('data-window-controls-overlay-stacked',String(win.innerWidth<=760||rect.width<560||rect.height<32||(rect.y>0&&rect.height<48)));
    root.setAttribute('data-window-controls-overlay','true');
  };
  const schedule=()=>{
    if(disposed||frame!==null)return;
    if(typeof win.requestAnimationFrame==='function')frame=win.requestAnimationFrame(update);
    else update();
  };
  update();
  overlay.addEventListener?.('geometrychange',schedule);
  win.addEventListener('resize',schedule);
  return()=>{
    disposed=true;
    if(frame!==null)win.cancelAnimationFrame?.(frame);
    overlay.removeEventListener?.('geometrychange',schedule);
    win.removeEventListener('resize',schedule);
    reset();
  };
}
