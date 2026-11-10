import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {fixturePython} from './fixture-python.mjs';

// Reports/screenshots belong to the journey; only this exact child home is
// disposable. Failed runs keep their state until the run owner consumes it.
export async function startCoordinationFixture({
 root=fileURLToPath(new URL('../../',import.meta.url)),
 python=fixturePython(join(root,'.venv/bin/python')),
 output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'),
}={}) {
 await mkdir(output,{recursive:true});
 const home=await mkdtemp(join(resolve(output),'coordination-fixture-'));
 const retain=reason=>console.error(JSON.stringify({coordinationFixture:{home,status:'retained',reason}}));
 let child;
 try {
  child=await startFixture({command:python,
   args:['-u',join(root,'tests/fixtures/coordination_ui_server.py'),'--home',home],
   env:process.env,tag:'coordination-ui',name:'Coordination'});
 } catch(error) {retain('startup failed');throw error}
 return {...child,home,async finish({close=[],originalError}={}) {
  try {
   await finishFixture({label:'Coordination fixture',close,stop:child.stop,originalError});
   if(originalError){retain('journey failed');return}
   await rm(home,{recursive:true});
   console.log(JSON.stringify({coordinationFixture:{home,status:'removed',pid:child.fixture.pid}}));
  } catch(error) {retain('cleanup failed');throw error}
 }};
}
