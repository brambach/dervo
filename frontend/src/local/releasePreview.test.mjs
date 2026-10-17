import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {ReleasePreview}=await import('./ReleasePreview.tsx');
const {TaskDialog,checkProfileLabel,checkExitDetail}=await import('./CodingTasks.tsx');
const {grantPayload,CHECK_TEST_NAMES}=await import('./api.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element)=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:client()},element)));

const frame=(component,props)=>React.createElement(component,props);

test('preview runs only after an explicit click and opens only a built candidate',async()=>{
 let job=null;
 const calls=host(op=>{if(op==='preview_status')return {supported:true,tools:{},job};if(op==='preview_start'){job={id:'preview-1',state:'built',directory:'/releases/preview-1',bundle:'/releases/preview-1/Dervo Local.app'};return job;}if(op==='preview_open')return job;throw new Error(op);});
 mount(frame(ReleasePreview,{projectId:'p',open:true,onClose(){}}));
 const build=await screen.findByRole('button',{name:'Build preview'});
 assert.equal(calls.filter(c=>c.operation==='preview_start').length,0);
 assert.equal(screen.queryByRole('button',{name:'Open preview'}),null);
 fireEvent.click(build);
 const preview=await screen.findByRole('button',{name:'Open preview'});
 assert.deepEqual(calls.find(c=>c.operation==='preview_start').args,{projectId:'p',tools:{}});
 fireEvent.click(preview);
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='preview_open').length,1));
 assert.match(document.body.textContent,/separate test workspace/);
});

test('saved-version limit explains the block and refresh enables building after cleanup',async()=>{
 let count=8;
 const calls=host(op=>{
  if(op==='preview_status')return {supported:true,tools:{},job:null,storage:{retainedApps:count,limit:8,canBuild:count<8}};
  if(op==='preview_storage_folder')return {opened:true};
  throw Error('No build expected');
 });
 mount(frame(ReleasePreview,{projectId:'p',open:true,onClose(){}}));
 await screen.findByText('8 of 8 saved app slots used');
 assert.equal(screen.getByRole('button',{name:'Build preview'}).disabled,true);
 fireEvent.click(screen.getByRole('button',{name:'Build preview'}));
 assert.equal(calls.filter(c=>c.operation==='preview_start').length,0);
 fireEvent.click(screen.getByRole('button',{name:'Open saved versions'}));
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='preview_storage_folder').length,1));
 assert.deepEqual(calls.find(c=>c.operation==='preview_storage_folder').args,{projectId:'p'});
 count=3;
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'Check storage again'}).disabled,false));
 fireEvent.click(screen.getByRole('button',{name:'Check storage again'}));
 await screen.findByText('3 of 8 saved app slots used');
 assert.equal(screen.getByRole('button',{name:'Build preview'}).disabled,false);
});

test('using a version needs a separate confirmation and sends no app or workspace paths',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
   calls.push({command,args});
   if(command==='local_request')return {supported:true,tools:{},job:{id:'preview-2',state:'built',directory:'/releases/preview-2'}};
   if(command==='use_preview_version')throw new Error('Finish active work before switching.');
   throw new Error(command);
 }};
 mount(frame(ReleasePreview,{projectId:'project-2',open:true,onClose(){}}));
 fireEvent.click(await screen.findByRole('button',{name:'Use this version'}));
 assert.equal(calls.filter(c=>c.command==='use_preview_version').length,0);
 fireEvent.click(screen.getByRole('button',{name:'Stay here'}));
 assert.equal(screen.queryByRole('button',{name:'Back up and switch'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Use this version'}));
 fireEvent.click(screen.getByRole('button',{name:'Back up and switch'}));
 await screen.findByRole('alert');
 assert.deepEqual(calls.find(c=>c.command==='use_preview_version').args,{projectId:'project-2'});
 assert.match(screen.getByRole('alert').textContent,/Finish active work/);
 assert.equal(screen.getByRole('button',{name:'Use this version'}).disabled,false);
});

test('rollback explains newer-work preservation and requires its own confirmation',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
   calls.push({command,args});
   if(command==='local_request')return {supported:true,tools:{},job:null,running_release:{id:'new'},rollback:{switchId:'a'.repeat(32),version:'old'}};
   if(command==='restore_previous_version')throw new Error('Finish active work before restoring.');
   throw new Error(command);
 }};
 mount(frame(ReleasePreview,{projectId:'project-2',open:true,onClose(){}}));
 fireEvent.click(await screen.findByRole('button',{name:'Return to previous version'}));
 assert.equal(calls.filter(c=>c.command==='restore_previous_version').length,0);
 assert.match(document.body.textContent,/newer conversations.*separate backup/);
 assert.match(document.body.textContent,/Project source files won’t be rolled back/);
 fireEvent.click(screen.getByRole('button',{name:'Keep this version'}));
 assert.equal(screen.queryByRole('button',{name:'Back up and restore previous version'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Return to previous version'}));
 fireEvent.click(screen.getByRole('button',{name:'Back up and restore previous version'}));
 await screen.findByRole('alert');
 assert.deepEqual(calls.find(c=>c.command==='restore_previous_version').args,{switchId:'a'.repeat(32)});
 assert.equal(screen.getByRole('button',{name:'Return to previous version'}).disabled,false);
});

test('a refused version switch lists exact cross-project tasks and only navigates on click',async()=>{
 const calls=[],opened=[];
 const blocker={projectId:'other-project',projectName:'Website',channelId:'thread-7',threadName:'Invitation fixes',taskId:'task-42',taskName:'Fix the expired invite',kind:'run',instruction:'Wait for the run to finish, or stop it.'};
 const terminal={projectId:'terminal-project',projectName:'Terminal project',channelId:null,threadName:'Project terminal',taskId:null,taskName:'',kind:'terminal',instruction:'Close this project’s terminal before switching versions.'};
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  calls.push({command,args});
  if(command==='use_preview_version')throw Error('Website still has work running.');
  if(command==='local_request'&&args.operation==='lifecycle_blockers')return [blocker,{...blocker,channelId:null,taskId:null,taskName:'',threadName:'a private thread'},terminal];
  if(command==='local_request'&&args.operation==='preview_status')return {supported:true,tools:{},job:{id:'preview',state:'built',directory:'/release'}};
  throw Error(command);
 }};
 mount(frame(ReleasePreview,{projectId:'app',open:true,onClose:()=>opened.push('closed'),onOpenBlocker:value=>opened.push(value)}));
 fireEvent.click(await screen.findByRole('button',{name:'Use this version'}));
 fireEvent.click(screen.getByRole('button',{name:'Back up and switch'}));
 const open=await screen.findByRole('button',{name:'Open task: Fix the expired invite'});
 assert.match(document.body.textContent,/Website · Invitation fixes/);
 assert.match(document.body.textContent,/Task task-42/);
 assert.deepEqual(opened,[]);
 assert.equal(screen.queryByRole('button',{name:'Open thread: a private thread'}),null);
 const openTerminal=screen.getByRole('button',{name:'Open terminal: Terminal project'});
 fireEvent.click(openTerminal);
 assert.deepEqual(opened,['closed',terminal]);
 opened.length=0;
 assert.deepEqual(calls.find(c=>c.args?.operation==='lifecycle_blockers').args.args,{scope:'release',projectId:null,channelId:null});
 fireEvent.click(open);
 assert.deepEqual(opened,['closed',blocker]);
 assert.equal(calls.filter(c=>/task_stop|task_settle|task_approve|terminal_open|terminal_write|terminal_close/.test(c.args?.operation??'')).length,0);
});

test('a refused project archive links the older task without retrying archive or approving work',async()=>{
 const {GeneralPage}=await import('./SettingsGeneral.tsx');
 const opened=[],blocker={projectId:'p',projectName:'Dervo',channelId:'old',threadName:'Earlier work',taskId:'lost',taskName:'Previous build',kind:'lost',instruction:'Mark the lost run as stopped.'};
 const calls=host(op=>{if(op==='preview_status')return {supported:false};if(op==='project_archive')throw Error('Earlier work needs attention.');if(op==='lifecycle_blockers')return [blocker];throw Error(op);});
 mount(frame(GeneralPage,{project:{id:'p',name:'Dervo',cwd:'/disposable',profile_id:'p',model:'m'},onChanged:async()=>{},onPreview(){},onOpenBlocker:b=>opened.push(b)}));
 fireEvent.click(screen.getByRole('button',{name:'Archive'}));
 fireEvent.click(screen.getByRole('button',{name:'Archive'}));
 fireEvent.click(await screen.findByRole('button',{name:'Open task: Previous build'}));
 assert.deepEqual(opened,[blocker]);
 assert.deepEqual(calls.find(c=>c.operation==='lifecycle_blockers').args,{scope:'project',projectId:'p',channelId:null});
 assert.equal(calls.filter(c=>c.operation==='project_archive').length,1);
 assert.equal(calls.filter(c=>c.operation==='task_settle').length,0);
});
