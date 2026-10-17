import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {TaskDialog,reconcileDetail,reconcileOutcome}=await import('./CodingTasks.tsx');
after(()=>dom.window.close());

const binding={provider:'codex',identity:'account',model:'model',cwd:'/approved',visibility:'private'};
const uncertain={number:1,state:'uncertain',kind:'create',created:'src/pkg/new.py',diff:{},before:{'src/pkg/new.py':null},after:null,argv:['python','test.py']};
const mount=(task,client)=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client},React.createElement(TaskDialog,{task,projectId:'p',channelId:'c',open:true,onOpenChange:()=>{},refresh:async()=>{}}))));
const fresh=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});

test('an uncertain creation exposes one Reconcile action: present target stays unknown, absent target settles, nothing replays and history is kept',async()=>{
 const task={id:'task',state:'disconnected',error:'Host restarted before task confirmation. Inspect evidence. No automatic replay.',origin:'conversation',session_id:'exact-provider-uuid',scope_version:1,files:['test.py'],folders:['src'],argv:['python','test.py'],binding,messages:[{role:'user',text:'create it',created_at:1}],tokens:[{tool:'dervo_create_file',state:'uncertain',path:'src/pkg/new.py',attempt:1}],runs:[{id:'run-1',state:'uncertain'}]};
 const calls=[];
 let attempt={...uncertain},reconcile={available:true,attempt:1,path:'src/pkg/new.py',reason:'Creation of src/pkg/new.py was interrupted before confirmation.'};
 let present=true;
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push({operation,args});
  if(operation==='task_detail')return {task,evidence:{attempts:[attempt],review:null},retry:{available:false},reconcile};
  if(operation==='task_reconcile'){
   if(present)return {action:'inspect',attempt:1,path:'src/pkg/new.py',detail:'src/pkg/new.py exists after the interruption. The host cannot tell its own interrupted write from content placed there later, so the attempt stays uncertain and the file is kept untouched. Inspect it; to continue this task, move the file aside yourself and reconcile again.'};
   attempt={...attempt,state:'blocked',blocker:'Creation was interrupted before anything reached disk: the target is absent.'};
   reconcile={available:false,attempt:null,path:null,reason:'No interrupted creation is recorded for this task. Nothing to reconcile.'};
   return {action:'dropped_absent_creation',attempt:1,path:'src/pkg/new.py',detail:'Target absent after the interruption. The phantom created path was removed from scope; the task may continue. Nothing was written.'};
  }
  throw Error(operation);
 }};
 const client=fresh();
 try{
  const view=mount(task,client);
  const button=await view.findByRole('button',{name:'Reconcile interrupted creation'});
  assert.equal(button.disabled,false);
  const explanation=view.getByTestId('reconcile').textContent;
  assert.match(explanation,/outcome is unknown, not a success/);
  assert.match(explanation,/Nothing is retried, replayed or overwritten/);
  assert.equal(view.queryByRole('button',{name:'Retry'}),null,'an uncertain run is never offered a retry');
  assert.equal(view.queryByRole('button',{name:'Approve'}),null);
  assert.doesNotMatch(view.getByTestId('task-outcome').textContent,/Completed/);
  fireEvent.click(button);
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='task_reconcile').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='task_reconcile').args,{projectId:'p',channelId:'c',taskId:'task'});
  await waitFor(()=>assert.match(view.getByTestId('reconcile-report').textContent,/^Still unknown: src\/pkg\/new.py exists after the interruption/));
  assert.match(view.getByTestId('reconcile-report').textContent,/kept untouched/);
  assert.equal(view.getByRole('button',{name:'Reconcile interrupted creation'}).disabled,false,'a present target keeps the action for after the file is moved aside');
  present=false;
  fireEvent.click(view.getByRole('button',{name:'Reconcile interrupted creation'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='task_reconcile').length,2));
  await waitFor(()=>assert.match(view.getByTestId('reconcile-report').textContent,/^Settled: src\/pkg\/new.py is absent/));
  assert.match(view.getByTestId('reconcile-report').textContent,/nothing was written/);
  assert.doesNotMatch(view.getByTestId('reconcile-report').textContent,/success/i);
  await waitFor(()=>assert.equal(view.queryByRole('button',{name:'Reconcile interrupted creation'}),null,'once settled there is nothing left to reconcile; the report stays readable'));
  assert.equal(calls.filter(c=>['task_continue','task_approve','task_review','task_create','coding_request'].includes(c.operation)).length,0,'reconcile never continues, retries or replays anything');
  fireEvent.click(view.getByRole('tab',{name:'Changes and checks'}));
  // The attempt heading names what happened to that attempt, not the state word behind it: no check ran, and the
 // blocker says why. Asserting the word 'blocked' pinned a heading the screen has never drawn.
 await waitFor(()=>assert.match(document.body.textContent,/Attempt 1 · not checked · Creation was interrupted before anything reached disk/));
  fireEvent.click(view.getByRole('tab',{name:'Scope and activity'}));
  assert.match(document.body.textContent,/exact-provider-uuid/,'the exact session and history records remain inspectable');
 }finally{cleanup();client.clear();}
});

test('a refused or unavailable reconcile explains why and changes nothing',async()=>{
 const working={id:'task',state:'working',origin:'conversation',session_id:'exact-provider-uuid',scope_version:1,files:['test.py'],folders:['src'],argv:['python','test.py'],binding,messages:[],tokens:[{tool:'dervo_run_tests',state:'running',attempt:1}],runs:[{id:'run-1',state:'running'}]};
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push({operation,args});
  if(operation==='task_detail')return {task:working,evidence:{attempts:[uncertain],review:null},retry:{available:false},reconcile:{available:false,attempt:1,path:'src/pkg/new.py',reason:'A tool call or host check is still active. Reconcile after it ends.'}};
  if(operation==='task_reconcile')throw new Error('A tool call or host check is still active. Reconcile after it ends.');
  throw Error(operation);
 }};
 const client=fresh();
 try{
  const view=mount(working,client);
  const button=await view.findByRole('button',{name:'Reconcile interrupted creation'});
  assert.equal(button.disabled,true);
  assert.match(view.getByTestId('reconcile').textContent,/Reconcile is unavailable: A tool call or host check is still active/);
  fireEvent.click(button);
  assert.equal(calls.filter(c=>c.operation==='task_reconcile').length,0,'a disabled action sends nothing');
 }finally{cleanup();client.clear();}
 // The host is the authority: a refusal it returns is shown as the error, and no report is claimed.
 const stopped={...working,state:'disconnected',tokens:[{tool:'dervo_create_file',state:'uncertain',attempt:1}]};
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push({operation,args});
  if(operation==='task_detail')return {task:stopped,evidence:{attempts:[uncertain],review:null},retry:{available:false},reconcile:{available:true,attempt:1,path:'src/pkg/new.py',reason:'Creation of src/pkg/new.py was interrupted before confirmation.'}};
  if(operation==='task_reconcile')throw new Error('Independent review is still active. Stop the task before reconciling.');
  throw Error(operation);
 }};
 const again=fresh();
 try{
  const view=mount(stopped,again);
  fireEvent.click(await view.findByRole('button',{name:'Reconcile interrupted creation'}));
  await waitFor(()=>assert.match(view.getByRole('alert').textContent,/Independent review is still active/));
  assert.equal(view.queryByTestId('reconcile-report'),null);
 }finally{cleanup();again.clear();}
 assert.equal(reconcileDetail({available:false,attempt:1,path:'x',reason:'why'}),'Reconcile is unavailable: why');
 assert.equal(reconcileOutcome({action:'none',attempt:null,detail:'No interrupted attempt. Nothing to reconcile.'}),'No interrupted attempt. Nothing to reconcile.');
});
test('no-attempt inspector retries only after explicit same-task feedback',async()=>{
 const task={id:'task',state:'ready_for_review',session_id:'exact-provider-uuid',scope_version:1,files:['test.py'],argv:['python','test.py'],binding:{provider:'codex',identity:'account',model:'model',cwd:'/approved',visibility:'private'},messages:[],tokens:[],runs:[]};
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});if(operation==='task_detail')return {task,evidence:{attempts:[],review:null},retry:{available:true}};if(operation==='task_continue')return {...task,state:'working'};throw Error(operation);}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 try{
  const view=render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client},React.createElement(TaskDialog,{task,projectId:'p',channelId:'c',open:true,onOpenChange:()=>{},refresh:async()=>{}}))));
  const button=await view.findByRole('button',{name:'Retry'});
  assert.equal(button.disabled,true);
  assert.equal(view.queryByRole('button',{name:'Approve'}),null);
  assert.equal(view.queryByRole('button',{name:'Ask for changes'}),null);
  assert.equal(calls.filter(c=>c.operation==='task_continue').length,0);
  fireEvent.change(view.getByLabelText('Retry task feedback'),{target:{value:'Use the approved host tools'}});
  fireEvent.click(button);
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='task_continue').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='task_continue').args,{projectId:'p',channelId:'c',taskId:'task',text:'Use the approved host tools'});
 }finally{cleanup();client.clear();}
});
