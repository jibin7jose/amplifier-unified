// Focused production-host journey, not an aesthetic/font qualification.
// Run ONLY in an already prepared browser-test executionContract, after the
// owner's frontend build. This script builds/installs nothing and uses no Git.
// AMPLIFIER_TEST_CONTRACT supplies Python/source/artifact identity; the actual
// Node executable is process.execPath (the contract does not advertise Node).
import {spawn,spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {appendFileSync,mkdirSync,mkdtempSync,readFileSync,readdirSync,realpathSync,writeFileSync} from 'node:fs';
import {isAbsolute,join,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {createAppearanceRequestObserver} from './appearance-request-observer.mjs';

const root=realpathSync(fileURLToPath(new URL('../../',import.meta.url)));
const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const within=(parent,path)=>{const rel=relative(parent,path);return rel===''||(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith('..'+sep))};
assert.ok(process.env.AMPLIFIER_TEST_CONTRACT,'An owned prepared executionContract is required; no local interpreter fallback.');
const contractPath=realpathSync(process.env.AMPLIFIER_TEST_CONTRACT);
const contractBytes=readFileSync(contractPath),executionContract=JSON.parse(contractBytes);
assert.equal(executionContract.profileId,'browser-test');
assert.equal(realpathSync(executionContract.workspace),root,'Run the admitted source, not another checkout.');
assert.ok(isAbsolute(executionContract.python),'Use the contract’s explicit Python executable.');
const python=executionContract.python;
if(process.env.AMPLIFIER_TEST_PYTHON)assert.equal(realpathSync(process.env.AMPLIFIER_TEST_PYTHON),realpathSync(python));
const artifactRoot=realpathSync(executionContract.artifacts);
if(process.env.AMPLIFIER_TEST_ARTIFACTS)assert.equal(realpathSync(process.env.AMPLIFIER_TEST_ARTIFACTS),artifactRoot);
assert.ok(!within(root,artifactRoot),'Synthetic archives and retained evidence must be outside the repository.');
const artifacts=mkdtempSync(join(artifactRoot,'appearance-packages-'));
const report={status:'running',executionContract,contractSha256:sha256(contractBytes),
 tools:{python,node:process.execPath,nodeRealpath:realpathSync(process.execPath),nodeVersion:process.version},
 limits:{compiledCssBytes:1_000_000,packageBytes:4_000_000,packageBase64Characters:5_333_336,uncompressedBytes:8_000_000,members:128,successScreenshots:5},
 phases:[],downloads:[],screenshots:[],fixture:{events:[]},browser:{},servedAssets:[],
 limitations:['Synthetic PNG and license only; no external assets, fonts or aesthetic acceptance.',
  'Source and served-build hashes are observed separately; the release owner must attest the build came from this source.',
  'One extra success screenshot retains the no-art control and actual artwork separately, alongside the three existing narrow preference states.']};
writeFileSync(join(artifacts,'execution-contract.json'),contractBytes);
const writeReport=()=>writeFileSync(join(artifacts,'report.json'),JSON.stringify(report,null,2)+'\n');
function treeHashes(paths,{source=false}={}){
 const result={};
 function visit(path){
  assert.ok(within(root,realpathSync(path)),'Identity inputs must stay in the admitted source.');
  for(const entry of readdirSync(path,{withFileTypes:true})){
   const child=join(path,entry.name);
   if(source&&(entry.name==='__pycache__'||relative(root,child)==='amplifier_web'+sep+'static'))continue;
   assert.ok(!entry.isSymbolicLink(),'Identity inputs may not be symlinks: '+child);
   if(entry.isDirectory())visit(child);
   else if(entry.isFile())result[relative(root,child).split(sep).join('/')]=sha256(readFileSync(child));
  }
 }
 for(const name of paths)visit(join(root,name));
 return result;
}
const sourcePaths=['frontend/src','frontend/tests','amplifier_web','tests/fixtures'];
// Static outputs and Python cache files are identified separately, not source.
const sourceHashes=()=>treeHashes(sourcePaths,{source:true});
const staticHashes=()=>treeHashes(['amplifier_web/static']);
report.sourceFiles=sourceHashes();report.buildFiles=staticHashes();writeReport();

// Python stdlib alone writes and inspects REAL ZIP bytes. Never extract archives,
// synthesize a download Blob, or ask the backend under test to verify itself.
const zipProgram=String.raw`
import base64, binascii, hashlib, json, pathlib, re, struct, sys, zipfile, zlib
mode, directory = sys.argv[1], pathlib.Path(sys.argv[2])
# Deterministic opaque RGB quadrants, not a transparent/black placeholder.
# Generate with stdlib; no image assets, image library or production helper.
colors = ((229, 37, 117), (18, 190, 213), (242, 173, 31), (94, 62, 205))
def png_chunk(kind, data):
 return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
rows = b"".join(b"\0" + b"".join(bytes(colors[(y // 16) * 2 + x // 16]) for x in range(32)) for y in range(32))
pixel = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
         + png_chunk(b"IDAT", zlib.compress(rows)) + png_chunk(b"IEND", b""))
notice = "Synthetic example asset license.\nPermission is granted to use this test pixel for qualification.\n"
manifest = {"type":"appearance","formatVersion":1,"name":"Package studio",
 "description":"Synthetic packaged palette and embedded pixel.","author":"Synthetic author",
 "stylesheet":"theme.css","assets":[{"path":"assets/pixel.png","mediaType":"image/png","license":"licenses/EXAMPLE.txt"}],
 "licenses":["licenses/EXAMPLE.txt"]}
css = '''#amp-one {
 --a-bg:#f4f6f8; --a-surface:#ffffff; --a-ink:#203040;
 --a-muted:#536475; --a-line:#cbd5df; --a-accent:#365a83;
 background-color:var(--a-bg); color:var(--a-ink);
 background-image:url("assets/pixel.png");
 --a-conversation-art:url("assets/pixel.png");
}
#amp-one[data-theme-scheme="dark"] {
 --a-bg:#16212d; --a-surface:#1d2c3b; --a-ink:#e8eef5;
 --a-muted:#b8c7d6; --a-line:#4d6278; --a-accent:#93b8dc;
}
'''
if mode == "create":
 files = {"appearance.json":json.dumps(manifest).encode(), "theme.css":css.encode(),
          "assets/pixel.png":pixel, "licenses/EXAMPLE.txt":notice.encode()}
 with zipfile.ZipFile(directory/"synthetic.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
  for name, body in files.items(): archive.writestr(name, body)
 with zipfile.ZipFile(directory/"wrong-type.zip", "w") as archive:
  archive.writestr("appearance.json", json.dumps({**manifest, "type":"unrelated"}))
  for name, body in files.items():
   if name != "appearance.json": archive.writestr(name, body)
 (directory/"broken.zip").write_bytes(b"PK\x03\x04not-a-valid-archive")
 (directory/"pixel.png").write_bytes(pixel)
 (directory/"EXAMPLE.txt").write_text(notice)
 print(json.dumps({"manifest":manifest,"python":{"executable":sys.executable,"version":sys.version},
  "pixelBase64":base64.b64encode(pixel).decode(),
  "pixelSha256":hashlib.sha256(pixel).hexdigest(),"license":notice}))
else:
 path, expected = pathlib.Path(sys.argv[3]), json.loads(sys.argv[4])
 raw = path.read_bytes()
 assert raw[:4] == b"PK\x03\x04", "Download is not a standard ZIP"
 assert len(raw) <= 4_000_000 and len(base64.b64encode(raw)) <= 5_333_336
 with zipfile.ZipFile(path) as archive:
  members = archive.infolist()
  assert len(members) <= 128
  assert len({m.filename for m in members}) == len(members), "Duplicate members"
  assert sum(m.file_size for m in members) <= 8_000_000
  assert archive.testzip() is None, "ZIP checksum failure"
  value = json.loads(archive.read("appearance.json"))
  assert value["type"] == "appearance" and type(value["formatVersion"]) is int and value["formatVersion"] == 1
  assert value["name"] == expected["name"], (value, expected)
  if "author" in expected: assert value.get("author") == expected["author"]
  if "description" in expected: assert value.get("description") == expected["description"]
  body = archive.read(value["stylesheet"]).decode("utf-8")
  declared = {"appearance.json", value["stylesheet"], *value.get("licenses", []),
              *(a["path"] for a in value.get("assets", []))}
  assert {m.filename for m in members if not m.is_dir()} == declared
  if expected.get("asset"):
   asset = next(a for a in value["assets"] if a["path"] == "assets/pixel.png")
   assert asset == manifest["assets"][0]
   assert archive.read(asset["path"]) == pixel, "Asset bytes changed"
  if expected.get("license"):
   assert "licenses/EXAMPLE.txt" in value["licenses"]
   assert archive.read("licenses/EXAMPLE.txt").decode() == notice, "License text changed"
  if expected.get("embedded"):
   assert "data:image/png;base64," + base64.b64encode(pixel).decode() in body
   assert 'url("assets/pixel.png")' not in body and "url(assets/pixel.png)" not in body
  if expected.get("marker"): assert expected["marker"] in body
  for marker in expected.get("presentMarkers", []): assert marker in body, "ZIP omitted committed CSS: " + marker
  if "exactCss" in expected:
   def split_css(source):
    header = re.match(r'^/\* amplifier-appearance: (.*?) \*/(?:\r?\n|$)', source, re.S)
    assert header, "Expected one portable metadata header"
    metadata = json.loads(header[1])
    assert isinstance(metadata, dict)
    return metadata, source[header.end():]
   committed_metadata, committed_body = split_css(expected["exactCss"])
   exported_metadata, exported_body = split_css(body)
   committed_metadata["name"] = expected["name"]
   committed_metadata["description"] = str(committed_metadata.get("description") or "")[:240]
   assert exported_body == committed_body, "ZIP stylesheet body differs from exact committed CSS"
   assert exported_metadata == committed_metadata, "ZIP changed metadata beyond export name/description normalization"
  for marker in expected.get("absentMarkers", []):
   assert marker not in body, "ZIP exported an unapplied draft: " + marker
  print(json.dumps({"manifest":value,"sha256":hashlib.sha256(raw).hexdigest(),
   "bytes":len(raw),"members":[{"path":m.filename,"bytes":m.file_size} for m in members],
   "stylesheetSha256":hashlib.sha256(body.encode()).hexdigest()}))
`;
writeFileSync(join(artifacts,'zip-fixture.py'),zipProgram);
function zip(mode,...args){
 const result=spawnSync(python,['-c',zipProgram,mode,artifacts,...args],{
  encoding:'utf8',timeout:10000,killSignal:'SIGKILL',maxBuffer:2_000_000,
 });
 appendFileSync(join(artifacts,'zip-helper.stderr.log'),result.stderr||'');
 appendFileSync(join(artifacts,'zip-helper.stdout.log'),result.stdout||'');
 if(result.error)throw result.error;
 assert.equal(result.status,0,'stdlib ZIP helper failed: '+result.stderr);
 return JSON.parse(result.stdout);
}
let browser,page,fixture,stop,failure,releaseImport,heldImport,importCalls=0,holdFinishedImport=false,finishedImportReceipt,finishedImportReleased=false,importRouteError;
const pageErrors=[],assetFailures=[],assetChecks=[],requestObserver=createAppearanceRequestObserver(),actions=requestObserver.actions;
const state=()=>page.evaluate(()=>window.amplifier.getState());
const action=async(name,args={})=>{
 const receipt=await page.evaluate(async([name,args])=>{
  const receipt=await window.amplifier.dispatch(name,args);
  if(receipt.accepted!==true)throw Error(receipt.error||name+' rejected');
  return receipt;
 },[name,args]);
 requestObserver.assertHealthy();
 return receipt;
};
const list=async()=>(await action('theme.list')).result.items;
const read=async id=>(await action('theme.read',{id})).result;
const button=name=>page.getByRole('button',{name,exact:true});
const field=name=>page.locator('.a-settings-page-content:not([hidden]) .a-appearance-advanced').getByLabel(name,{exact:true});
const alert=()=>page.locator('.a-settings-page-content:not([hidden])').getByRole('alert');
const notice=()=>page.locator('.a-settings-page-content:not([hidden]) .a-appearance-notice');
const libraryIdentity=async()=>JSON.stringify((await list()).map(({id,fingerprint})=>({id,fingerprint})).sort((a,b)=>a.id.localeCompare(b.id)));
const appearanceSnapshot=()=>page.evaluate(()=>{
 const value=window.amplifier.getState(),presentation=window.amplifier.getShellState()?.effectiveComposition?.presentation;
 if(!presentation||typeof presentation!=='object')throw Error('The actual shell presentation must be accessible.');
 return {presentation,preview:{active:value.view?.themePreview===true,
  snapshot:value.view?.themePreview?{name:value.view.themeDraftName,css:value.view.themeDraft}:null}};
});
async function preserved(){
 const value=await state();
 return {theme:value.theme,selection:value.selectedSessionId,conversationDraft:value.view?.draft,library:await libraryIdentity(),...await appearanceSnapshot(),
  editor:await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-editor.v1')),
  selected:await page.getByRole('button',{name:/^Preview /,pressed:true}).getAttribute('aria-label')};
}
function actionResponse(name){
 return page.waitForResponse(response=>{
  try{return requestObserver.matches(response.request(),name)}
  catch(error){requestObserver.record(error,{stage:'response-predicate'});return false}
 });
}
function assertImportPayload(payload,file,expectedKey){
 assert.equal(Object.hasOwn(payload.args,'packageBase64'),expectedKey==='packageBase64');
 assert.equal(Object.hasOwn(payload.args,'css'),expectedKey==='css','The wire payload must have exactly one content representation.');
 assert.deepEqual(Object.keys(payload.args).sort(),[expectedKey,'fallbackName'].sort(),'Only one content field and the frontend fallback name may be sent.');
 assert.equal(payload.args.fallbackName,file.name.replace(/\.amplifier\.css$|\.css$|\.zip$/i,'').slice(0,100)||'Imported appearance');
 if(expectedKey==='packageBase64'){
  assert.equal(payload.args.packageBase64,file.buffer.toString('base64'));
 }
 else assert.equal(payload.args.css,file.buffer.toString('utf8'));
}
async function upload(file,expectedKey,{rejected=false}={}){
 const pending=actionResponse('theme.import');
 await page.locator('#theme-import').setInputFiles(file);
 const response=await pending,payload=requestObserver.inspect(response.request()),receipt=await response.json();
 requestObserver.assertHealthy();
 assertImportPayload(payload,file,expectedKey);
 if(rejected){assert.equal(receipt.accepted,false);await expect(alert()).toContainText(/Invalid appearance package/i)}
 else{assert.ok(response.ok());assert.equal(receipt.accepted,true);await expect(notice()).toContainText('added to your library. Select it to preview.')}
 await expect(button('Upload appearance')).toBeEnabled();
 return receipt;
}
async function download(name,args,filename){
 const pending=page.waitForEvent('download'),response=actionResponse('theme.export');
 await button(name).click();
 const [file,result]=await Promise.all([pending,response]);
 assert.equal((await result.json()).accepted,true);
 requestObserver.assertHealthy();
 assert.deepEqual(requestObserver.inspect(result.request()).args,args,'Export must target the selected entry, or committed current appearance, exactly.');
 assert.equal(await file.failure(),null);
 const path=join(artifacts,filename);await file.saveAs(path);
 const bytes=readFileSync(path);
 report.downloads.push({control:name,filename:file.suggestedFilename(),path,bytes:bytes.length,sha256:sha256(bytes)});
 if(args.format==='zip')assert.match(file.suggestedFilename(),/\.zip$/i);
 else assert.match(file.suggestedFilename(),/\.css$/i);
 return {path,bytes};
}
async function screenshot(name){
 assert.ok(report.screenshots.length<report.limits.successScreenshots,'At most five success screenshots: paired paint proof and three narrow states.');
 const path=join(artifacts,name+'.png');
 const bytes=await page.screenshot({path,scale:'css',animations:'disabled',timeout:10000});report.screenshots.push(path);
 return {path,bytes};
}
async function embeddedPaint(pixelBase64){
 await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).backgroundImage)).toContain('data:image/png;base64,'+pixelBase64);
 const actual=await page.locator('#amp-one').evaluate(async node=>{
  const image=getComputedStyle(node).backgroundImage;
  const match=image.match(/^url\(["']?(data:image\/png;base64,[A-Za-z0-9+/=]+)["']?\)$/);
  if(!match)throw Error('Expected one embedded PNG on the actual root: '+image);
  const pixel=new Image();pixel.src=match[1];await pixel.decode();
  return {url:match[1],width:pixel.naturalWidth,height:pixel.naturalHeight};
 });
 assert.deepEqual(actual,{url:'data:image/png;base64,'+pixelBase64,width:32,height:32});
}
// This is additional to embeddedPaint: computed style + decoding the asset alone
// says nothing about occlusion. Only pixels of a real PAGE screenshot can pass.
// No visible canvas, injected style, cover removal or mocked backend is used.
const paintColors=[[229,37,117],[18,190,213],[242,173,31],[94,62,205]];
async function exposedConversationGeometry(previous=null){
 return page.evaluate(previous=>{
  const root=document.querySelector('#amp-one'),conversation=root?.querySelector('[data-part="conversation"]');
  if(!root?.classList.contains('a-approachable-shell')||!conversation)throw Error('Pixel proof unavailable: expected production conversation surface.');
  const box=conversation.getBoundingClientRect(),style=getComputedStyle(conversation);
  if(style.backgroundOrigin!=='padding-box'||style.backgroundClip!=='border-box'||style.backgroundPosition!=='0% 0%'||
   style.backgroundSize!=='100% 240px'||style.backgroundRepeat!=='no-repeat'||style.opacity!=='1'||style.transform!=='none')
   throw Error('Pixel proof unavailable: conversation artwork cascade differs from the documented 100% × 240px padding-box contract.');
  const left=box.left+parseFloat(style.borderLeftWidth),top=box.top+parseFloat(style.borderTopWidth);
  const width=box.width-parseFloat(style.borderLeftWidth)-parseFloat(style.borderRightWidth);
  // Choose BEFORE inspecting colored pixels. Every pixel in each 5×5 patch
  // must hit the actual conversation element, not a child or covering surface.
  const exposed=(x,y)=>{
   for(let dy=-2;dy<=2;dy++)for(let dx=-2;dx<=2;dx++){
    if(x+dx<0||y+dy<0||x+dx>=innerWidth||y+dy>=innerHeight||
     document.elementFromPoint(x+dx+.5,y+dy+.5)!==conversation)return false;
   }
   return true;
  };
  const geometry={box:{x:box.x,y:box.y,width:box.width,height:box.height},viewport:{width:innerWidth,height:innerHeight},points:[]};
  if(previous){
   if(JSON.stringify(geometry.box)!==JSON.stringify(previous.box)||JSON.stringify(geometry.viewport)!==JSON.stringify(previous.viewport))
    throw Error('Pixel proof unavailable: conversation geometry changed between control and artwork screenshots.');
   for(const point of previous.points){
    if(!exposed(point.x,point.y))throw Error('Pixel proof unavailable: a real surface now covers the selected conversation patch.');
    geometry.points.push(point);
   }
  }else{
   for(let quadrant=0;quadrant<4;quadrant++){
    const xs=quadrant%2?[.9,.8,.7,.6]:[.1,.2,.3,.4],ys=quadrant<2?[30,50,70,90]:[150,170,190,210];
    let found;
    for(const yOffset of ys){
     for(const fraction of xs){
      const x=Math.floor(left+width*fraction),y=Math.floor(top+yOffset);
      if(exposed(x,y)){found={quadrant,x,y};break}
     }
     if(found)break;
    }
    if(!found)throw Error('Pixel proof unavailable: no uncovered 5×5 conversation patch in artwork quadrant '+quadrant+'. No covers were changed.');
    geometry.points.push(found);
   }
  }
  return {...geometry,backgroundImage:style.backgroundImage,backgroundColor:style.backgroundColor};
 },previous);
}
async function screenshotPixels(bytes,points){
 // The PNG is the saved Playwright PAGE screenshot, never the original asset.
 // OffscreenCanvas reads that immutable screenshot; it is not added to the DOM.
 // package-lock has no PNG decoder; locked Pillow is an optional artifacts
 // group, not a browser-test prerequisite. Use the already-required Chromium.
 return page.evaluate(async({base64,points})=>{
  if(typeof createImageBitmap!=='function'||typeof OffscreenCanvas!=='function')
   throw Error('Pixel proof unavailable: this Chromium lacks screenshot PNG decode/readback.');
  const bytes=Uint8Array.from(atob(base64),value=>value.charCodeAt(0));
  const image=await createImageBitmap(new Blob([bytes],{type:'image/png'}));
  try{
   const canvas=new OffscreenCanvas(image.width,image.height),context=canvas.getContext('2d',{willReadFrequently:true});
   if(!context)throw Error('Pixel proof unavailable: screenshot PNG decoder/readback is unavailable.');
   context.drawImage(image,0,0);
   return {width:image.width,height:image.height,patches:points.map(point=>({
    ...point,rgba:Array.from(context.getImageData(point.x-2,point.y-2,5,5).data),
   }))};
  }finally{image.close()}
 },{base64:bytes.toString('base64'),points});
}
async function actualConversationPaint(pixelBase64){
 const committed={...(await state()).theme},before=await appearanceSnapshot();
 const editorBefore=await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-editor.v1'));
 assert.equal(before.preview.active,false,'Pixel proof starts from the actual applied/reloaded package.');
 assert.notEqual(before.presentation.decorations,false,'Artwork must not be disabled for the paint proof.');
 report.pixelProof={status:'checking',colors:paintColors,
  decoder:'Chromium createImageBitmap + OffscreenCanvas over saved page screenshot PNG bytes',
  contract:'docs/shell/themes.md --a-conversation-art; frontend/src/main-refresh.css .a-conversation'};
 await expect(page.locator('#amp-one')).not.toHaveAttribute('data-decorations','off');
 const controlCss=committed.css+'\n#amp-one{--a-conversation-art:none}\n';
 await action('theme.preview',{name:'Synthetic no-art control',css:controlCss});
 await expect.poll(async()=>(await state()).view.themeDraft).toBe(controlCss);
 await expect.poll(()=>page.locator('[data-part="conversation"]').evaluate(node=>getComputedStyle(node).backgroundImage)).toBe('none');
 const controlGeometry=await exposedConversationGeometry();
 report.pixelProof.controlGeometry=controlGeometry;
 const control=await screenshot('desktop-package-no-art-control');
 await exposedConversationGeometry(controlGeometry);
 const controlPixels=await screenshotPixels(control.bytes,controlGeometry.points);
 await action('theme.preview',{name:committed.name,css:committed.css});
 await expect.poll(async()=>(await state()).view.themeDraft).toBe(committed.css);
 await embeddedPaint(pixelBase64);
 await expect.poll(()=>page.locator('[data-part="conversation"]').evaluate(node=>getComputedStyle(node).backgroundImage)).toContain('data:image/png;base64,'+pixelBase64);
 const artworkGeometry=await exposedConversationGeometry(controlGeometry);
 const artwork=await screenshot('desktop-package-preview');
 await exposedConversationGeometry(controlGeometry);
 const artworkPixels=await screenshotPixels(artwork.bytes,artworkGeometry.points);
 Object.assign(report.pixelProof,{control:{path:control.path,sha256:sha256(control.bytes),pixels:controlPixels},
  artwork:{path:artwork.path,sha256:sha256(artwork.bytes),pixels:artworkPixels},artworkGeometry});
 assert.deepEqual([controlPixels.width,controlPixels.height],[controlGeometry.viewport.width,controlGeometry.viewport.height],'Screenshot pixels must use CSS-pixel geometry.');
 assert.deepEqual([artworkPixels.width,artworkPixels.height],[controlPixels.width,controlPixels.height]);
 const background=controlGeometry.backgroundColor.match(/^rgb\((\d+), (\d+), (\d+)\)$/);
 assert.ok(background,'Pixel proof requires an opaque, flat non-image conversation control.');
 const baselineRgb=background.slice(1).map(Number),near=(a,b)=>a.every((channel,index)=>Math.abs(channel-b[index])<=1);
 for(let quadrant=0;quadrant<4;quadrant++){
  const expected=paintColors[quadrant],baseline=controlPixels.patches[quadrant].rgba,painted=artworkPixels.patches[quadrant].rgba;
  for(let offset=0;offset<painted.length;offset+=4){
   assert.equal(baseline[offset+3],255);assert.equal(painted[offset+3],255);
   assert.ok(near(baseline.slice(offset,offset+3),baselineRgb),'The no-image control patch must actually show the opaque conversation surface.');
   assert.ok(!near(baseline.slice(offset,offset+3),expected),'The no-image control must reject the artwork color.');
   assert.ok(near(painted.slice(offset,offset+3),expected),'Packaged PNG is absent, hidden or mispainted in real conversation quadrant '+quadrant);
  }
 }
 await action('view.update',{patch:{themePreview:false}});
 await expect.poll(async()=>(await state()).view.themePreview).toBe(false);
 assert.deepEqual((await state()).theme,committed,'The paint/control previews cannot apply another theme.');
 assert.deepEqual(await appearanceSnapshot(),before,'Paint proof must restore presentation and preview state.');
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-editor.v1')),editorBefore,'Paint proof cannot replace the editor.');
 report.pixelProof.status='passed';
 report.phases.push('actual-conversation-pixels-versus-same-geometry-no-art-control');
}
async function visibleControl(control){
 await control.scrollIntoViewIfNeeded();await expect(control).toBeVisible();await expect(control).toBeEnabled();
 const box=await control.boundingBox(),viewport=page.viewportSize();
 assert.ok(box&&box.width>0&&box.height>0&&box.x>=-1&&box.x+box.width<=viewport.width+1,'Control must fit the narrow viewport.');
}
try{
 const synthetic=zip('create');
 report.tools.pythonRuntime=synthetic.python;
 const packageFile={name:'unrelated-name.pkg',mimeType:'application/octet-stream',buffer:readFileSync(join(artifacts,'synthetic.zip'))};
 report.fixture.manifest=synthetic.manifest;
 assert.ok(packageFile.buffer.toString('base64').length<=report.limits.packageBase64Characters);
 const fixtureTemp=join(artifacts,'tmp'),fixtureHome=join(artifacts,'home');
 mkdirSync(fixtureTemp);mkdirSync(fixtureHome);
 const env={...process.env,TMPDIR:fixtureTemp,HOME:fixtureHome,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_TEST_INSTALLED:'0'};
 for(const name of ['AMPLIFIER_TEST_SHARED_WORK_HOME','AMPLIFIER_TEST_PACKAGE_ROOT','AMPLIFIER_TEST_INSTALLED_IDENTITY'])delete env[name];
 writeFileSync(join(artifacts,'fixture.stderr.log'),'');writeFileSync(join(artifacts,'fixture.stdout.log'),'');
 fixture=spawn(python,['-u',join(root,'tests/fixtures/empty_host_ui_server.py')],{cwd:root,env,stdio:['ignore','pipe','pipe']});
 stop=ownFixture(fixture,{label:'Appearance packages fixture',timeoutMs:10000});
 fixture.once('exit',(code,signal)=>report.fixture.events.push({event:'exit',pid:fixture.pid,code,signal}));
 fixture.once('close',(code,signal)=>report.fixture.events.push({event:'close',pid:fixture.pid,code,signal}));
 fixture.stderr.on('data',bytes=>appendFileSync(join(artifacts,'fixture.stderr.log'),bytes));
 const url=await new Promise((resolveUrl,reject)=>{
  let output='',settled=false;
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolveUrl(value)};
  const timer=setTimeout(()=>finish(Error('Appearance fixture startup timed out')),20000);
  fixture.once('error',error=>finish(error));
  fixture.once('exit',(code,signal)=>finish(Error('Appearance fixture exited before readiness: '+(code??signal))));
  fixture.stdout.on('data',bytes=>{
   appendFileSync(join(artifacts,'fixture.stdout.log'),bytes);output+=bytes;
   let newline;
   while((newline=output.indexOf('\n'))>=0){
    const line=output.slice(0,newline);output=output.slice(newline+1);
    try{const value=JSON.parse(line);if(value.url)finish(null,value.url)}catch{}
   }
  });
 });
 report.fixture.url=url;report.fixture.pid=fixture.pid;
 browser=await chromium.launch({headless:true});
 browser.once('disconnected',()=>report.browser.disconnected=true);
 page=await browser.newPage({viewport:{width:1360,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('request',request=>{
  requestObserver.observe(request);
  try{
   if(new URL(request.url()).pathname.includes('/assets/pixel.png'))assetFailures.push('Dangling local asset request: '+request.url());
  }catch(error){requestObserver.record(error,{stage:'request-asset-projection'})}
 });
 page.on('response',response=>{
  try{
   const address=new URL(response.url());
   if(address.origin!==url||!address.pathname.startsWith('/assets/')||!['script','stylesheet'].includes(response.request().resourceType()))return;
   assetChecks.push((async()=>{
    assert.ok(response.ok(),'Production asset HTTP status');
    const name='amplifier_web/static'+decodeURIComponent(address.pathname),bytes=await response.body(),digest=sha256(bytes);
    assert.equal(digest,report.buildFiles[name],'Served bytes must equal the retained build hash.');
    report.servedAssets.push({path:name,sha256:digest,type:response.request().resourceType()});
   })().catch(error=>assetFailures.push(error.message)));
  }catch(error){requestObserver.record(error,{stage:'response-asset-projection'})}
 });
 await page.goto(url);await showFixtureConversation(page);
 requestObserver.assertHealthy();
 await action('session.create');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep this synthetic conversation draft.');
 const selected=(await state()).selectedSessionId;
 await openSettingsPage(page,'custom-appearance');

 // 1. Legacy CSS still round-trips; ZIP magic and manifest, not the generic
 // filename, identify the package. Hold one request to inspect duplicate locks.
 const legacyMeta={name:'Legacy studio',description:'Synthetic CSS-only appearance',author:'Synthetic author'};
 const committedBeforeMarker='synthetic-committed-before-apply',committedAfterMarker='synthetic-committed-after-apply',
  draftBeforeMarker='synthetic-unapplied-before-apply',draftAfterMarker='synthetic-unapplied-after-apply';
 const legacy='/* amplifier-appearance: '+JSON.stringify(legacyMeta)+' */\n#amp-one{--a-accent:#426b57}\n/* '+committedBeforeMarker+' */\n';
 await upload({name:'unrelated.css',mimeType:'text/css',buffer:Buffer.from(legacy)},'css');
 await button('Preview Legacy studio').click();
 const legacyEntry=(await list()).find(row=>row.source==='saved'&&row.name===legacyMeta.name);assert.ok(legacyEntry);
 const legacyExport=await download('Export Legacy studio',{id:legacyEntry.id},'legacy.css');
 const legacyText=legacyExport.bytes.toString('utf8'),legacyHeader=legacyText.match(/^\/\* amplifier-appearance: (.+) \*\//);
 assert.ok(legacyHeader,'CSS exports retain portable metadata.');
 assert.deepEqual(JSON.parse(legacyHeader[1]),legacyMeta);
 assert.ok(legacyText.includes('#amp-one{--a-accent:#426b57}'));
 await button('Use Legacy studio').click();
 await expect.poll(async()=>(await state()).theme.name).toBe(legacyMeta.name);
 await button('Edit Legacy studio').click();
 await field('Appearance name').fill('Keep my unsaved name');
 await field('Description').fill('Keep my unsaved description');
 await field('CSS').fill('#amp-one{--a-accent:#704d82}\n/* '+draftBeforeMarker+' */\n');
 await expect.poll(async()=>(await state()).view.themeDraftName).toBe('Keep my unsaved name');
 const beforeImport=await preserved();
 let hold=true;
 await page.route('**/api/actions',async route=>{
  try{
   const body=requestObserver.inspect(route.request());
   if(body?.action!=='theme.import')return await route.continue();
   importCalls++;
   if(holdFinishedImport){
    holdFinishedImport=false;
    const response=await route.fetch({timeout:10000}),receipt=await response.json();
    assert.ok(response.ok(),'Hold only an actual successful import response.');
    assert.equal(receipt.accepted,true);
    finishedImportReceipt=receipt;
    await new Promise(resolveGate=>releaseImport=resolveGate);
    await route.fulfill({response});finishedImportReleased=true;
    return;
   }
   if(hold){heldImport=body;await new Promise(resolveGate=>releaseImport=resolveGate)}
   await route.continue();
  }catch(error){
   importRouteError=error;requestObserver.record(error,{stage:'import-route'});
   await route.abort().catch(()=>{});
  }
 });
 const pendingImport=upload(packageFile,'packageBase64');
 // Attach rejection handling immediately while the original request is held.
 pendingImport.catch(()=>{});
 await expect.poll(()=>heldImport?.action).toBe('theme.import');
 const uploading=button('Uploading appearance…');
 await expect(uploading).toBeVisible();await expect(uploading).toBeDisabled();
 await expect(page.locator('#theme-import')).toBeDisabled();
 await expect(field('CSS')).toBeDisabled();
 await expect(button('Export Legacy studio as ZIP')).toBeDisabled();
 await page.locator('.a-appearance-import .a-file-drop').evaluate((node,base64)=>{
  const transfer=new DataTransfer(),bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0));
  transfer.items.add(new File([bytes],'duplicate.zip',{type:'application/zip'}));
  node.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer}));
 },packageFile.buffer.toString('base64'));
 assert.equal(importCalls,1,'Disabled/drop controls must not dispatch a duplicate import.');
 hold=false;releaseImport();releaseImport=null;await pendingImport;
 await list(); // Drain the production command queue after async file reading and import settlement.
 assert.equal(importCalls,1,'The dropped duplicate must still be suppressed after the real import settles.');
 assert.equal(actions.filter(row=>row.action==='theme.import').length,2,'Only legacy CSS and the intentional ZIP import were dispatched.');
 const packageEntry=(await list()).find(row=>row.source==='saved'&&row.name===synthetic.manifest.name);assert.ok(packageEntry);
 assert.notEqual(packageEntry.name,heldImport.args.fallbackName,'The root manifest name wins over the generic upload filename.');
 const afterImport=await preserved();
 for(const key of ['theme','selection','conversationDraft','editor','selected','presentation','preview'])assert.deepEqual(afterImport[key],beforeImport[key],'Import must preserve '+key);
 assert.deepEqual(JSON.parse(afterImport.library).filter(row=>row.id!==packageEntry.id),JSON.parse(beforeImport.library),'Import cannot overwrite any other library entry.');
 assert.equal((await read(legacyEntry.id)).fingerprint,legacyEntry.fingerprint,'Import cannot overwrite another entry.');
 // Reimport is idempotent even when its REAL completed receipt arrives after
 // close/reopen and newer editor input. Keep the original editor A target.
 holdFinishedImport=true;
 const lateResponse=actionResponse('theme.import');
 lateResponse.catch(()=>{});
 const lateFile={...packageFile,name:'ordinary.zip',mimeType:'application/zip'};
 await page.locator('#theme-import').setInputFiles(lateFile);
 await expect.poll(()=>!!finishedImportReceipt||!!importRouteError).toBe(true);
 if(importRouteError)throw importRouteError;
 assert.equal(finishedImportReceipt.result.id,packageEntry.id);
 await expect(field('CSS')).toBeDisabled();
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await openSettingsPage(page,'custom-appearance');
 const advanced=page.locator('.a-settings-page-content:not([hidden]) .a-appearance-advanced');
 if(!await field('CSS').isVisible())await advanced.locator('summary').click();
 await expect(field('CSS')).toBeEnabled();
 await field('Appearance name').fill('Newer retained editor A');
 await field('Description').fill('Newer description after the import completed.');
 await field('CSS').fill('#amp-one{--a-accent:#704d82}\n/* '+draftBeforeMarker+' */\n');
 const newerEditor=await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-editor.v1'));
 assert.equal(JSON.parse(newerEditor).entry.id,legacyEntry.id);
 releaseImport();releaseImport=null;
 const lateResult=await lateResponse;
 requestObserver.assertHealthy();
 assertImportPayload(requestObserver.inspect(lateResult.request()),lateFile,'packageBase64');
 assert.equal((await lateResult.json()).accepted,true);
 await list();
 assert.equal(finishedImportReleased,true);if(importRouteError)throw importRouteError;
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-editor.v1')),newerEditor,'Late import cannot retarget or replace newer editor A.');
 await expect(field('Appearance name')).toHaveValue('Newer retained editor A');
 await expect(field('CSS')).toBeEnabled();await expect(button('Upload appearance')).toBeEnabled();
 await expect(notice()).toHaveCount(0);
 assert.deepEqual((await state()).theme,beforeImport.theme);
 assert.equal((await state()).selectedSessionId,beforeImport.selection);
 assert.equal((await state()).view.draft,beforeImport.conversationDraft);
 assert.deepEqual(await appearanceSnapshot(),{presentation:beforeImport.presentation,preview:beforeImport.preview});
 await expect(button('Preview Legacy studio')).toHaveAttribute('aria-pressed','true');
 assert.equal(importCalls,2,'Close/reopen must not replay the completed import.');
 assert.equal(await libraryIdentity(),afterImport.library,'Identical ZIP reimport is idempotent.');
 report.phases.push('finished-import-late-receipt-preserves-newer-editor-and-controls');
 report.phases.push('legacy-css-and-nonapplying-idempotent-zip-import');

 // 2. Both a malformed ZIP and a valid ZIP with the wrong manifest type must
 // report failure without replacing editor A, preferences, or independent
 // preview B. The shared preview action deliberately does not choose an entry.
 const previewBeforeInvalid={name:'Independent import preview B',css:'#amp-one{--a-accent:#8163a5}'};
 await action('theme.preview',previewBeforeInvalid);
 await expect.poll(async()=>(await state()).view.themePreview).toBe(true);
 await expect(page.locator('.a-appearance-preview')).toContainText('Previewing '+previewBeforeInvalid.name);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-editor.v1')),newerEditor);
 const beforeInvalid=await preserved();
 assert.deepEqual(beforeInvalid.preview,{active:true,snapshot:previewBeforeInvalid});
 for(const name of ['broken.zip','wrong-type.zip']){
  await upload({name,mimeType:'application/zip',buffer:readFileSync(join(artifacts,name))},'packageBase64',{rejected:true});
  assert.deepEqual(await preserved(),beforeInvalid,'Rejected ZIP must have no selection/draft/library effect.');
  await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).getPropertyValue('--a-accent').trim())).toBe('#8163a5');
 }
 report.phases.push('invalid-zip-ui-feedback-and-state-preservation');

 // 3. Real renderer preview + selected CSS/ZIP exports. A CSS editor update of
 // canonical ZIP storage must retain original asset and license members.
 await button('Preview Package studio').click();
 await expect(button('Preview Package studio')).toHaveAttribute('aria-pressed','true');
 await embeddedPaint(synthetic.pixelBase64);
 const previewScheme=await page.locator('#amp-one').getAttribute('data-theme-scheme');
 await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).getPropertyValue('--a-accent').trim())).toBe(previewScheme==='dark'?'#93b8dc':'#365a83');
 assert.deepEqual((await state()).theme,beforeImport.theme,'Preview is not Apply.');
 const compiled=await read(packageEntry.id);
 assert.ok(Buffer.byteLength(compiled.css)<=report.limits.compiledCssBytes);
 assert.ok(compiled.css.includes('data:image/png;base64,'+synthetic.pixelBase64));
 const originalZip=await download('Export Package studio as ZIP',{id:packageEntry.id,format:'zip'},'selected.zip');
 report.downloads.at(-1).zip=zip('verify',originalZip.path,JSON.stringify({...synthetic.manifest,asset:true,license:true}));
 const compiledExport=await download('Export Package studio',{id:packageEntry.id},'selected.css');
 assert.ok(compiledExport.bytes.toString('utf8').includes('data:image/png;base64,'+synthetic.pixelBase64));
 assert.ok(compiledExport.bytes.toString('utf8').includes('packageNotices'));
 await button('Edit Package studio').click();
 await field('CSS').fill(compiled.css+'\n/* '+draftBeforeMarker+' */\n');
 const committedBeforeExport={...(await state()).theme};
 const currentZip=await download('Export current appearance as ZIP',{format:'zip'},'current-before-apply.zip');
 report.downloads.at(-1).zip=zip('verify',currentZip.path,JSON.stringify({name:legacyMeta.name,exactCss:committedBeforeExport.css,marker:committedBeforeMarker,absentMarkers:[draftBeforeMarker]}));
 const renamed='Package studio revised',description='Updated synthetic package; retained asset and license.';
 const edited=compiled.css+'\n/* synthetic-package-edit */\n/* '+committedAfterMarker+' */\n#amp-one,#amp-one[data-theme-scheme]{--a-accent:#587a9b}\n';
 await field('Appearance name').fill(renamed);await field('Description').fill(description);await field('CSS').fill(edited);
 await button('Update saved appearance').click();
 await expect(notice()).toContainText('Library entry updated. Apply separately');
 const updated=await read(packageEntry.id);
 assert.equal(updated.name,renamed);assert.equal(updated.description,description);
 assert.notEqual(updated.fingerprint,packageEntry.fingerprint);
 assert.deepEqual((await state()).theme,beforeImport.theme,'A library update cannot apply.');
 const editedZip=await download('Export '+renamed+' as ZIP',{id:packageEntry.id,format:'zip'},'selected-updated.zip');
 report.downloads.at(-1).zip=zip('verify',editedZip.path,JSON.stringify({name:renamed,description,author:'Synthetic author',asset:true,license:true,embedded:true,marker:'synthetic-package-edit'}));
 await button('Preview').click();await embeddedPaint(synthetic.pixelBase64);
 await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).getPropertyValue('--a-accent').trim())).toBe('#587a9b');
 report.phases.push('preview-real-paint-and-byte-verified-selected-exports-update');

 // 4. Apply/reload must paint embedded CSS with no local URL. Current ZIP is
 // deliberately exported from committed CSS, not the retained editor draft.
 await button('Apply appearance').click();
 await expect.poll(async()=>(await state()).theme.name).toBe(renamed);
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await page.reload();await showFixtureConversation(page);
 assert.equal((await state()).selectedSessionId,selected);
 await expect(composer).toHaveDraft('Keep this synthetic conversation draft.');
 await embeddedPaint(synthetic.pixelBase64);
 await actualConversationPaint(synthetic.pixelBase64);
 await openSettingsPage(page,'custom-appearance');
 await button('Edit '+renamed).click();
 const committedAfterExport={...(await state()).theme};
 await field('Appearance name').fill('Unapplied export trap');
 await field('CSS').fill(committedAfterExport.css+'\n/* '+draftAfterMarker+' */\n');
 const appliedZip=await download('Export current appearance as ZIP',{format:'zip'},'current-applied.zip');
 report.downloads.at(-1).zip=zip('verify',appliedZip.path,JSON.stringify({name:renamed,author:'Synthetic author',license:true,embedded:true,marker:'synthetic-package-edit',presentMarkers:[committedAfterMarker],exactCss:committedAfterExport.css,absentMarkers:[draftBeforeMarker,draftAfterMarker]}));
 assert.equal((await state()).theme.name,renamed);
 await page.setViewportSize({width:390,height:844});
 await page.emulateMedia({reducedMotion:'reduce'});
 for(const scheme of ['light','dark']){
  await openSettingsPage(page,'appearance');
  const appearancePage=page.locator('.a-settings-experience[data-settings-page="appearance"] .a-settings-page-content:not([hidden])');
  await expect(appearancePage).toBeVisible();
  await expect(appearancePage.locator('.a-appearance-settings')).toHaveAttribute('data-appearance-simple','true');
  await appearancePage.getByRole('combobox',{name:'Color mode',exact:true}).selectOption(scheme);
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  const decorations=appearancePage.getByRole('checkbox',{name:'Show decorative theme background',exact:true});
  if(!await decorations.isVisible()){
   const summary=appearancePage.locator('summary').filter({hasText:/^Layout & background$/});
   await expect(summary).toBeVisible();await summary.click();
  }
  await expect(decorations).toBeVisible();
  await decorations.check();await embeddedPaint(synthetic.pixelBase64);
  await decorations.uncheck();
  await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations','off');
  await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).backgroundImage)).toBe('none');
  await appearancePage.getByRole('button',{name:'Save, import & export themes',exact:true}).click();
  const customPage=page.locator('.a-settings-experience[data-settings-page="custom-appearance"] .a-settings-page-content:not([hidden])');
  await expect(customPage).toBeVisible();
  await expect(customPage.locator('.a-appearance-settings')).toHaveAttribute('data-appearance-advanced','true');
  const beforeNarrowImport=await preserved();
  await upload({...packageFile,name:'repeat.zip'},'packageBase64');
  const afterNarrowImport=await preserved();
  for(const key of ['theme','selection','conversationDraft','editor','selected','presentation','preview'])assert.deepEqual(afterNarrowImport[key],beforeNarrowImport[key],'Narrow import must preserve '+key);
  assert.equal((await state()).theme.name,renamed,'Narrow import cannot apply the original package.');
  for(const name of ['Export '+renamed,'Export '+renamed+' as ZIP','Upload appearance'])await visibleControl(button(name));
  await expect(notice()).toContainText('added to your library');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  await screenshot('narrow-'+scheme+'-reduced-decoration-off');
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
 await visibleControl(button('Export '+renamed+' as ZIP'));
 await visibleControl(button('Upload appearance'));await expect(notice()).toBeVisible();
 await screenshot('narrow-forced-colors-feedback');
 report.phases.push('apply-reload-current-export-and-narrow-preference-controls');
 await list(); // Final real command-queue barrier, not a pre-dispatch count.
 assert.equal(importCalls,6,'Exactly six intentional imports reached the route after the legacy import.');
 assert.equal(actions.filter(row=>row.action==='theme.import').length,7,'Exactly seven intentional imports overall; no delayed duplicate or replay.');
 if(importRouteError)throw importRouteError;
 await Promise.all(assetChecks);
 requestObserver.assertHealthy();
 assert.ok(report.servedAssets.some(row=>row.type==='script'),'Observe a production script, not a mock page.');
 assert.ok(report.servedAssets.some(row=>row.type==='stylesheet'),'Observe production styles.');
 assert.deepEqual(pageErrors,[]);assert.deepEqual(assetFailures,[]);
 const observation=await (await page.request.get(url+'/fixture')).json();
 assert.deepEqual(observation.sent,[],'No model/provider message may be sent.');
 assert.deepEqual(observation.started,[],'No conversation runtime may be started.');
 const buildResponse=await page.request.get(url+'/build.json');
 assert.ok(buildResponse.ok());const buildBytes=await buildResponse.body();
 assert.equal(sha256(buildBytes),report.buildFiles['amplifier_web/static/build.json']);
 report.buildIdentity=JSON.parse(buildBytes);
 assert.deepEqual(sourceHashes(),report.sourceFiles,'Authored source changed during qualification.');
 assert.deepEqual(staticHashes(),report.buildFiles,'Build changed during qualification.');
 report.status='checks-passed-awaiting-cleanup';
}catch(error){
 failure=error;report.status='failed';report.error=error.stack||String(error);
 if(report.pixelProof?.status==='checking')report.pixelProof.status='failed';
 if(page&&!page.isClosed())try{
  const path=join(artifacts,'failure.png');await page.screenshot({path,timeout:2000,animations:'disabled'});report.failureScreenshot=path;
 }catch(captureError){report.failureScreenshotError=captureError.message}
}finally{
 releaseImport?.();releaseImport=null;
 report.pageErrors=pageErrors;report.assetFailures=assetFailures;report.actions=actions;report.importCalls=importCalls;
 try{
  await finishFixture({label:'Appearance packages fixture',originalError:failure,
   close:[['Appearance packages browser',async()=>{await browser?.close();report.browser.closeResolved=true}]],
   stop:async()=>{
    if(!stop)return;
    try{report.fixture.shutdown=await stop()}
    catch(error){report.fixture.shutdownError=error.stack||String(error);throw error}
   },
  });
 }catch(error){report.cleanupError=error.stack||String(error);failure??=error}
 if(fixture&&!report.fixture.events.some(row=>row.event==='close')){
  report.cleanupError??='Fixture close was not observed.';failure??=Error(report.cleanupError);
 }
 if(browser&&!report.browser.closeResolved){
  report.cleanupError??='Browser close did not resolve within the fixture lifecycle budget.';failure??=Error(report.cleanupError);
 }
 report.requestObserver=requestObserver.evidence();
 try{requestObserver.assertHealthy()}catch(error){failure??=error;report.observerError=error.message}
 report.status=failure?'failed':'passed';writeReport();
 console.log(JSON.stringify({status:report.status,artifacts,report:join(artifacts,'report.json'),phases:report.phases}));
}
if(failure)throw failure;