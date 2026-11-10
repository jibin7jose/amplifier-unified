// Preserve npm entrypoints on Windows and Unix; the distribution owns assembly.
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const [operation,...args]=process.argv.slice(2);
const development=['prepare-view','verify-view','dev','build-development'].includes(operation);
const helper=fileURLToPath(new URL(`../../scripts/web_client_${development?'development':'build'}.py`,import.meta.url));
const python=process.env.PYTHON||(process.platform==='win32'?'python':'python3');
const result=spawnSync(python,[helper,operation,...args],{stdio:'inherit'});
if(result.error)console.error(result.error.message);
process.exit(result.status??1);
