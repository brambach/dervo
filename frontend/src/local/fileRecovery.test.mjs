import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,cleanup,fireEvent}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {TaskDialog}=await import('./CodingTasks.tsx');
after(()=>dom.window.close());

const binding={provider:'codex',identity:'synthetic-account',model:'model',cwd:'/disposable',visibility:'private'};
const task={id:'task',state:'ready_for_review',session_id:'recorded-session',scope_version:1,files:['test_sample.py'],argv:['python','test_sample.py'],binding,messages:[],tokens:[],runs:[]};
const attempt={number:1,state:'prepared',kind:'change',diff:{'test_sample.py':'-old\n+new'},before:{},after:{},argv:task.argv};
const option={attempt:1,path:'test_sample.py',available:true,reason:null,scope_version:1,expected_hash:'recorded-after-hash',expected_attempts:1};
const mount=(client,currentTask=task)=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client},React.createElement(TaskDialog,{task:currentTask,projectId:'p',channelId:'c',open:true,onOpenChange:()=>{},refresh:async()=>{}}))));

test('restoration stays unavailable even with a stale available response; historical evidence is readable',async()=>{
 const calls=[];
 const historical={...attempt,number:2,kind:'restoration',state:'restored',diff:{'test_sample.py':'historical restoration diff'}};
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push({operation,args});
  if(operation==='task_detail')return {task,evidence:{attempts:[attempt,historical]},restoration:[option]};
  throw Error(operation);
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 try{
  const view=mount(client);
  fireEvent.click(view.getByRole('tab',{name:'Changes and checks'}));
  await view.findByText(/won’t put this file back on its own/);
  assert.match(document.body.textContent,/historical restoration diff/);
  assert.match(document.body.textContent,/-old\s*\+new/);
  assert.match(document.body.textContent,/Attempt 1/);
  assert.match(document.body.textContent,/Attempt 2/);
  assert.equal(view.queryByRole('button',{name:'Restore this file'}),null);
  assert.equal(view.queryByRole('button',{name:'Confirm file restoration'}),null);
  assert.equal(view.queryByTestId('restore-confirmation'),null);
  assert.equal(calls.some(c=>['task_restore_file','task_continue','task_approve','task_review','coding_request'].includes(c.operation)),false);
 }finally{cleanup();client.clear();}
});

test('created-file refusal remains visible alongside retained diff inspection',async()=>{
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation})=>{
  if(operation==='task_detail')return {task,evidence:{attempts:[attempt]},restoration:[{...option,available:false,reason:'Created-file restoration is unsupported. No file will be deleted'}]};
  throw Error(operation);
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 try{
  const view=mount(client);
  fireEvent.click(view.getByRole('tab',{name:'Changes and checks'}));
  await view.findByText(/No file will be deleted/);
  assert.equal(view.queryByRole('button',{name:'Restore this file'}),null);
  assert.match(document.body.textContent,/-old\s*\+new/);
 }finally{cleanup();client.clear();}
});

const plainRestoration='Dervo won’t put this file back on its own, in case something else changed it since. The changes above show what to undo by hand.';
const genericRestorationReason="File restoration is unavailable because Dervo can't guarantee preservation of concurrent external edits. Inspect the recorded diff and snapshots.";
for(const reason of [
 genericRestorationReason,
 'The file changed after this edit. Later edits are kept',
 'An uncertain write or active check needs reconciliation before restoration',
])test(`restoration shows one generic explanation and retains the diagnostic: ${reason}`,async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push({operation,args});
  if(operation==='task_detail')return {task,evidence:{attempts:[attempt]},restoration:[{...option,available:false,reason}]};
  throw Error(operation);
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 try{
  const view=mount(client);
  fireEvent.click(view.getByRole('tab',{name:'Changes and checks'}));
  await view.findByText('Attempt 1 · not checked');
  // The host's generic reason shows once, in plain words (polish review, 9 Oct).
  assert.equal(view.getAllByText(plainRestoration,{exact:true}).length,1);
  assert.equal(view.queryByText(genericRestorationReason,{exact:true}),null);
  if(reason!==genericRestorationReason)assert.equal(view.getAllByText(reason,{exact:true}).length,1);
  assert.match(document.body.textContent,/-old\s*\+new/);
  // The raw snapshots moved behind "Full record, for support" (surfaces inventory 11); the diff stays readable here.
  assert.equal(view.queryByText('File snapshots'),null);
  fireEvent.click(view.getByRole('tab',{name:'Scope and activity'}));
  assert.ok(view.getByText('Full record, for support'));
  assert.equal(view.queryByRole('button',{name:'Restore this file'}),null);
  assert.equal(view.queryByRole('button',{name:'Confirm file restoration'}),null);
  assert.equal(view.queryByTestId('restore-confirmation'),null);
  assert.deepEqual(calls.map(c=>c.operation),['task_detail']);
 }finally{cleanup();client.clear();}
});

test('bridge maps only the bounded restoration operation',async()=>{
 const {readFileSync}=await import('node:fs');
 const source=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 assert.match(source,/TaskRestoreFile/);
 assert.match(source,/Self::TaskRestoreFile=>"task_restore_file"/);
 assert.doesNotMatch(source,/Self::TaskRestoreAll|Self::TaskDeleteFile|Self::ProviderRestoreFile/);
});

test('frontend recovery allowlist rejects bulk and provider rollback before invoking native code',async()=>{
 const {fileRecoveryRequest,restoreRecordedFile}=await import('./api.ts');
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,request)=>{calls.push(request);return {state:'restored',number:2};}};
 for(const operation of ['task_restore_all','task_delete_file','provider_restore_file'])await assert.rejects(()=>fileRecoveryRequest(operation,{}),/Unsupported/);
 assert.equal(calls.length,0);
 await restoreRecordedFile('p','c','task',{...option,content:'untrusted replacement',actor:'another actor',root:'/elsewhere'});
 assert.deepEqual(Object.keys(calls[0].args).sort(),['projectId','channelId','taskId','attemptNumber','scopeVersion','path','expectedHash','expectedAttempts'].sort());
});


test('an uncertain restoration offers inspection and cannot continue or approve the stale completion',async()=>{
 const uncertain={...attempt,number:2,kind:'restoration',state:'uncertain',diff:{},after:null};
 const stale={...task,completion:{attempt:1,stale:true,changed_files:['test_sample.py']}};
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation})=>{
  if(operation==='task_detail')return {task:stale,evidence:{attempts:[attempt,uncertain]},restoration:[{...option,available:false,reason:'An uncertain write needs reconciliation'}],reconcile:{kind:'restoration',available:true,attempt:2,path:option.path,reason:'Unknown outcome'}};
  throw Error(operation);
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 try{
  const view=mount(client,stale);
  await view.findByRole('button',{name:'Inspect interrupted restoration'});
  assert.equal(view.queryByRole('button',{name:'Approve'}),null);
  assert.equal(view.queryByRole('button',{name:'Send'}),null);
  assert.equal(view.queryByRole('button',{name:'Send'}),null);
  assert.match(view.getByTestId('reconcile').textContent,/Nothing is replayed or overwritten/);
 }finally{cleanup();client.clear();}
});
