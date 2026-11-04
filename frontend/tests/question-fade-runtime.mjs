import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdirSync,readFileSync,realpathSync} from 'node:fs';
import {isAbsolute,relative,sep} from 'node:path';

const defaultContractPath='/opt/unified-prepared/execution-contract.json';
const within=(parent,path)=>{
 const rel=relative(parent,path);
 return rel===''||(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith('..'+sep));
};
const absolutePath=(value,message)=>{
 assert.ok(typeof value==='string'&&value.length>0&&isAbsolute(value),message);
 return value;
};

export function questionFadeExecution({
 root,env=process.env,read=readFileSync,realpath=realpathSync,ensureDirectory=mkdirSync,
}={}) {
 absolutePath(root,'An absolute admitted repository root is required.');
 const explicitContract=Object.hasOwn(env,'AMPLIFIER_TEST_CONTRACT');
 const contractPath=explicitContract?env.AMPLIFIER_TEST_CONTRACT:defaultContractPath;
 assert.ok(typeof contractPath==='string'&&contractPath.length>0,
  'An explicit executionContract path must not be empty.');
 let contractBytes,background=false;
 try{
  contractBytes=read(realpath(contractPath));
 }catch(error){
  if(explicitContract||error.code!=='ENOENT')throw error;
  // The canonical runner uses uuid.uuid4().hex for cleanup ownership. Its tag
  // classifies this invocation; it grants no managed execution authority.
  assert.ok(typeof env.AMPLIFIER_BROWSER_CHECK_OWNER==='string'
   &&/^[0-9a-f]{32}$/.test(env.AMPLIFIER_BROWSER_CHECK_OWNER),
  'An absent default contract requires the canonical browser-check runner owner tag.');
  background=true;
 }
 const python=absolutePath(env.AMPLIFIER_TEST_PYTHON,
  'Use an explicit absolute Python executable.');
 const artifactEnv=absolutePath(env.AMPLIFIER_TEST_ARTIFACTS,
  'Use an explicit absolute artifact directory.');
 // Validate identity without replacing the selected path: launching the resolved
 // base interpreter instead of a virtualenv's symlink would lose its environment.
 const resolvedPython=realpath(python);
 if(background){
  ensureDirectory(artifactEnv,{recursive:true});
  return {
   python,artifactRoot:realpath(artifactEnv),
   evidence:{executionMode:'background',runnerOwnerTag:env.AMPLIFIER_BROWSER_CHECK_OWNER},
  };
 }
 const contract=JSON.parse(contractBytes);
 assert.ok(contract&&typeof contract==='object'&&!Array.isArray(contract),
  'The executionContract must be an object.');
 assert.equal(contract.profileId,'browser-test');
 assert.equal(realpath(absolutePath(contract.workspace,
  'Use the contract’s absolute workspace.')),root,'Run the admitted source, not another checkout.');
 assert.equal(resolvedPython,realpath(absolutePath(contract.python,
  'Use the contract’s explicit absolute Python executable.')),
 'Use the Python executable supplied by the executionContract.');
 const artifactRoot=realpath(artifactEnv);
 assert.equal(artifactRoot,realpath(absolutePath(contract.artifacts,
  'Use the contract’s absolute artifact directory.')),
 'Use the artifact directory supplied by the executionContract.');
 assert.ok(!within(root,artifactRoot),'Retained evidence must stay outside the repository.');
 return {
  python,artifactRoot,
  evidence:{
   executionMode:'managed',
   executionContract:{version:contract.version,profileId:contract.profileId,source:contract.source},
   contractSha256:createHash('sha256').update(contractBytes).digest('hex'),
  },
 };
}