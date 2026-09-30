// Run only in a disposable Linux container. No credentials or real user data.
// Compare the same image with and without: setpriv --no-new-privs -- node ...
import {mkdtemp,mkdir,writeFile,rm,readFile} from 'node:fs/promises';
import {join,dirname,resolve} from 'node:path';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {createRequire} from 'node:module';
const appPackage=resolve(process.argv[2] || '/app/package.json');
const require=createRequire(appPackage);
const piRoot=join(dirname(appPackage),'node_modules/@earendil-works/pi-coding-agent');
const pkg=JSON.parse(await readFile(join(piRoot,'package.json'),'utf8'));
const originalSpawn=childProcess.spawn;
childProcess.spawn=function(command,args,...rest) {
 if (typeof command==='string' && command.endsWith('/bwrap')) console.log(JSON.stringify({executable:command,args}));
 return originalSpawn.call(this,command,args,...rest);
};
syncBuiltinESMExports();
const pi=await import(join(piRoot,pkg.exports['.'].import));
const root=await mkdtemp('/tmp/dano495-');
const workspace=join(root,'workspace'),agentDir=join(root,'agent');
try {
 await mkdir(workspace); await mkdir(agentDir);
 process.env.PI_CODING_AGENT_DIR=agentDir;
 process.env.HEIMDALL_BWRAP_BIND_ROOT=workspace;
 await writeFile(join(agentDir,'heimdall.json'),JSON.stringify({sandbox:{enabled:true,userNamespace:false}}));
 const loader=new pi.DefaultResourceLoader({cwd:workspace,agentDir,settingsManager:pi.SettingsManager.inMemory(),noExtensions:true,noSkills:true,noPromptTemplates:true,noThemes:true,noContextFiles:true,additionalExtensionPaths:[require.resolve('@josephyoung/pi-heimdall/extensions/heimdall.ts')]});
 await loader.reload();
 const loaded=loader.getExtensions();
 if(loaded.errors.length) throw Error(JSON.stringify(loaded.errors));
 const ext=loaded.extensions[0];
 const ctx={cwd:workspace,hasUI:false,ui:{notify(){},setStatus(){},theme:{fg:(_,s)=>s}}};
 for(const handler of ext.handlers.get('session_start')??[]) await handler({type:'session_start'},ctx);
 const bash=ext.tools.get('bash');
 console.log('tools', [...ext.tools.keys()]);
 const tool=bash.definition??bash;
 const status=await readFile('/proc/self/status','utf8');
 console.log(status.split('\n').filter(x=>/^(Uid|Gid|Cap|NoNewPrivs)/.test(x)).join('\n'));
 try {const result=await tool.execute('probe',{command:'printf DANO495_BASH_OK'},undefined,()=>{},ctx); console.log(JSON.stringify(result)); if(!JSON.stringify(result).includes('DANO495_BASH_OK'))process.exitCode=1;} catch(e){console.log(String(e));process.exitCode=1;}
}finally{await rm(root,{recursive:true,force:true});}
