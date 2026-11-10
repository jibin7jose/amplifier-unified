// Session-authored example used only by acceptance tests, never installed UI.
export function drawingSurface(refined=false){
 const properties={tab:{type:'string',enum:['draw','clock']},strokes:{type:'array',maxItems:40,items:{type:'object'}},color:{type:'string'},speed:{type:'number'}};
 const events=Object.fromEntries(['tab','color','speed','strokes'].map(key=>['set'+key,{schema:{type:'object',properties:{value:properties[key]},required:['value']},updates:{[key]:'value'}}]));
 return {title:'Shared sketch',manifest:{version:1,stateSchema:{type:'object',properties,required:Object.keys(properties),additionalProperties:false},events,requests:{}},initialState:{tab:'draw',strokes:[],color:'#224466',speed:1},content:`<!doctype html><html><head><style>
 body{font:14px system-ui;margin:0;padding:16px;background:var(--host-surface);color:var(--host-ink)}canvas{display:block;width:100%;height:260px;border:1px solid #bbb}section[hidden]{display:none}button,input{margin:6px}#error{color:#a00}
 </style></head><body><h1>${refined?'Sketch, refined':'Shared sketch'}</h1><button id="drawTab">Sketch</button><button id="clockTab">Clock</button>
 <label>Pen color <input id="color" type="color"></label><label>Clock speed <input id="speed" type="range" min="1" max="3"></label>
 <section id="draw"><canvas id="sketch" aria-label="Shared drawing"></canvas><button id="save">Save drawing</button><p id="count"></p></section>
 <section id="clock" hidden><canvas id="clockFace" aria-label="Clock"></canvas></section><p id="error" role="status"></p><script>
 window.fixtureMount=Math.random();let current,local=null,active=false,clockTicks=0;const sketch=document.querySelector('#sketch'),error=document.querySelector('#error');
 const fail=e=>{error.textContent=e.message};
 const painter=canvasApp.observeCanvas(sketch,({context:c,width:w,height:h})=>{c.clearRect(0,0,w,h);c.lineWidth=3;c.lineCap='round';for(const stroke of local??current?.strokes??[]){c.strokeStyle=stroke.color;c.beginPath();stroke.points.forEach((p,i)=>i?c.lineTo(p[0]*w,p[1]*h):c.moveTo(p[0]*w,p[1]*h));c.stroke()}});
 const clock=canvasApp.observeCanvas(document.querySelector('#clockFace'),({context:c,width:w,height:h})=>{if(Math.min(w,h)<16)return;c.clearRect(0,0,w,h);c.beginPath();c.arc(w/2,h/2,Math.min(w,h)/2-8,0,Math.PI*2);c.stroke();c.fillText(String(++clockTicks),w/2,h/2)});
 function render(s){current=s.app.state;document.querySelector('#draw').hidden=current.tab!=='draw';document.querySelector('#clock').hidden=current.tab!=='clock';document.querySelector('#color').value=current.color;document.querySelector('#speed').value=current.speed;document.querySelector('#count').textContent=current.strokes.length+' saved strokes';painter.redraw();clock.redraw()}
 canvasApp.ready.then(render);canvasApp.subscribe(render);
 const point=e=>{const r=sketch.getBoundingClientRect();return [(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height]};
 sketch.onpointerdown=e=>{canvasApp.beginEdit();local=structuredClone(local??current.strokes);local.push({color:current.color,points:[point(e)]});active=true;sketch.setPointerCapture(e.pointerId)};
 sketch.onpointermove=e=>{if(active){local.at(-1).points.push(point(e));painter.redraw()}};
 async function save(){if(!local)return;const commit=canvasApp.getEditVersion(),value=structuredClone(local);try{await canvasApp.emit('setstrokes',{value},{commit});if(commit===canvasApp.getEditVersion())local=null;error.textContent=''}catch(e){fail(e)}painter.redraw()}
 sketch.onpointerup=()=>{active=false;save()};sketch.onpointercancel=()=>{active=false};document.querySelector('#save').onclick=save;
 for(const tab of ['draw','clock'])document.querySelector('#'+tab+'Tab').onclick=()=>canvasApp.emit('settab',{value:tab}).catch(fail);
 for(const field of ['color','speed'])document.querySelector('#'+field).oninput=e=>{const value=field==='speed'?Number(e.target.value):e.target.value;canvasApp.emit('set'+field,{value},local?{}:{commit:canvasApp.getEditVersion()}).catch(fail)};
 setInterval(()=>clock.redraw(),1000);addEventListener('pagehide',()=>{painter.disconnect();clock.disconnect()});
 </script></body></html>`};
}
