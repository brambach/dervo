// Polisher harness: the production frontend (frontend/dist) in Chromium, talking to the real host through a
// Tauri-shaped shim. Requests are serialized like src/bridge.rs (one host, one request at a time) and timed, so slow
// operations show up. Native-only commands get a logged stub. Nothing here calls a model.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createServer} from 'node:http';
import {readFile,mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const here=path.dirname(fileURLToPath(import.meta.url));
export const root=path.resolve(here,'../..');
const require=createRequire(path.join(root,'frontend/package.json'));
const {chromium}=require('@playwright/test');

const TYPES={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2','.woff':'font/woff','.png':'image/png','.json':'application/json','.wasm':'application/wasm','.onnx':'application/octet-stream'};
// Native commands with no host behind them, and what the shim answers.
const NATIVE={startup_status:{ready:true,kind:'ready',detail:''},startup_retry:{ready:true,kind:'ready',detail:''},
  app_icon_get:{id:'default'},notification_permission:'granted',draft_guard_ready:null,dock_badge:null,notification_settings:null,
  // Registered in src/main.rs since mvp1 8294a09 (voice, phone link). Only commands main.rs registers belong here:
  // an inherited Buzz call to anything else must fail like it does on the Mac.
  phone_link_status:{relay:null,connected:false,problem:null,phones:[],waiting:null},voice_start:null,voice_stop:null,
  voice_command:null,voice_install:null,acknowledge_startup:null,
  'plugin:event|listen':0,'plugin:event|unlisten':null,'plugin:app|version':'0.1.0-polish','plugin:app|name':'Dervo',
  'plugin:window|get_all_windows':['main'],'plugin:webview|get_all_webviews':[]};

// seed:false starts a fresh install: no profile, no project, nothing remembered (first run and onboarding). native
// overrides or adds shim answers, e.g. {'plugin:dialog|open':'/path'} for a folder picker; a function gets the args.
export async function start({out,viewport={width:1280,height:820},colorScheme='light',seed={},reducedMotion='no-preference',native={}}={}){
  const data=await mkdtemp(path.join(tmpdir(),'dervo-polish-'));
  out??=path.join(root,'tools/polisher/out');await rm(out,{recursive:true,force:true});await mkdir(out,{recursive:true});
  const host=spawn('python3',['-B',path.join(here,'polish_host.py'),data],{stdio:['pipe','pipe','pipe'],cwd:path.join(root,'backend')});
  let stderr='';host.stderr.on('data',d=>{stderr+=d;});
  const waiting=new Map();let next=1;
  createInterface({input:host.stdout}).on('line',line=>{let r;try{r=JSON.parse(line);}catch{return;}const w=waiting.get(r.id);if(!w)return;waiting.delete(r.id);w(r);});
  const log=[];   // every host request: {op, ms, waited, error}
  const unknown=new Map();
  let chain=Promise.resolve();
  function raw(operation,args={}){return new Promise(resolve=>{const id=next++;waiting.set(id,resolve);host.stdin.write(JSON.stringify({id,operation,args})+'\n');});}
  // Serialized like the bridge: one request at a time; queue wait is recorded too.
  function call(operation,args={}){
    const queued=Date.now();
    const p=chain.then(async()=>{const begun=Date.now();const r=await raw(operation,args);log.push({op:operation,ms:r.ms,total:Date.now()-begun,waited:begun-queued,error:r.error,at:Date.now()});if(r.error)throw new Error(r.error);return r.result;});
    chain=p.catch(()=>{});return p;
  }
  const ids=seed===false?await call('polish_fresh'):await call('polish_seed',seed);
  const server=createServer(async(req,res)=>{try{const dist=path.join(root,'frontend/dist');let file=path.resolve(dist,'.'+decodeURIComponent(new URL(req.url,'http://l').pathname));if(!file.startsWith(dist))throw 0;if(!existsSync(file)||file===dist)file=path.join(dist,'index.html');res.setHeader('content-type',TYPES[path.extname(file)]??'application/octet-stream');res.end(await readFile(file));}catch{res.writeHead(404);res.end();}});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE??'/opt/pw-browsers/chromium'});
  const context=await browser.newContext({viewport,colorScheme,reducedMotion,deviceScaleFactor:2});
  const page=await context.newPage();
  const errors=[],consoleErrors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
  await page.exposeFunction('__polishInvoke',async(command,args)=>{
    if(command==='local_request')return call(args.operation,args.args??{});
    if(command in native)return typeof native[command]==='function'?native[command](args):native[command];
    if(command in NATIVE)return NATIVE[command];
    unknown.set(command,(unknown.get(command)??0)+1);
    if(command.startsWith('plugin:'))return null;
    // Not registered in src/main.rs: Tauri rejects it, so the shim does too.
    throw `Command ${command} not found`;  // Tauri 2's own words (webview/mod.rs)
  });
  await page.addInitScript(({ids})=>{
    if(ids.project){localStorage.setItem('dervo-project',ids.project);localStorage.setItem('dervo-channel',ids.thread);}
    let cb=0;window.__TAURI_INTERNALS__={invoke:(c,a)=>window.__polishInvoke(c,a),transformCallback:()=>++cb,unregisterCallback:()=>{},convertFileSrc:s=>s,metadata:{currentWindow:{label:'main'},currentWebview:{label:'main',windowLabel:'main'}}};
    window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
  },{ids});
  const url=`http://127.0.0.1:${server.address().port}/`;
  let shotN=0;
  async function shot(name,opts={}){const file=path.join(out,`${String(++shotN).padStart(2,'0')}-${name}.png`);await page.screenshot({path:file,...opts});return file;}
  async function stop(){await browser.close();server.close();host.stdin.end();await new Promise(r=>{host.once('exit',r);setTimeout(r,15000);});
    await writeFile(path.join(out,'host-log.json'),JSON.stringify({log,unknown:[...unknown],errors,consoleErrors},null,1));}
  return {page,context,browser,call,ids,url,shot,stop,log,unknown,errors,consoleErrors,data,out,stderr:()=>stderr};
}

export function slowest(log,n=10){return [...log].sort((a,b)=>b.total-a.total).slice(0,n);}
