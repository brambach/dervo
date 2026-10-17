import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,act}=await import('@testing-library/react');
const {renderToStaticMarkup}=await import('react-dom/server');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {TaskFooter,TaskDialog,resultLabel,completionDetail,staleCompleted}=await import('./CodingTasks.tsx');
const {CodingPermissionDialog}=await import('./CodingPermission.tsx');
const {ConversationIndicator,statusLabel}=await import('./TurnControls.tsx');
after(()=>dom.window.close());

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const binding={provider:'codex',identity:'synthetic@example.test',model:'synthetic-model',cwd:'/approved',visibility:'private'};
const DETAIL='Result approval is separate from the coding permission. It does not authorise file access, destructive actions, deployment or spending. ';
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const wrap=(qc,element)=>React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:qc},element));

const task={id:'task',state:'completed',origin:'conversation',session_id:'session',scope_version:1,files:['src/search.ts'],argv:['node','check.mjs'],check_profile:'node-test',binding,messages:[{role:'assistant',text:'Kept **your search** when you return.\n\n- Added regression coverage.\n- Browser interaction remains unchecked.',created_at:1}],tokens:[],runs:[],acceptance:{required:false,source:'default'},completion:{changed_files:['src/search.ts'],test_exit_status:0,check_profile:'node-test',review_phase:null}};
const attempt={number:1,state:'verified',diff:{'src/search.ts':'-before\n+after\n'+'x'.repeat(9000)+'\nDIFF END'},before:{'src/search.ts':'before-sha'},after:{'src/search.ts':'after-sha'},argv:task.argv,test:{argv:task.argv,stdout:'TAP version 13\n# tests 1\n# pass 1\nOUTPUT END',stderr:'diagnostic retained',exit_status:0,stopped:null,check_profile:'node-test'}};
const detail={task,evidence:{attempts:[attempt]},retry:{available:false}};
const mount=(record=task)=>{const qc=client();return {qc,view:render(wrap(qc,React.createElement(TaskDialog,{task:record,projectId:'p',channelId:'c',refresh:async()=>{},open:true,onOpenChange(){},records:React.createElement('p',null,'Saved guidance version 7')})))};};

test('result reading separates evidence and scope, preserves exact records and keeps a correction through navigation',async()=>{
 const calls=host(op=>{if(op==='task_detail')return detail;throw Error(op);});const {view,qc}=mount();
 try{
  const correction=await view.findByRole('textbox',{name:'Correction request'});
  assert.match(view.getByTestId('task-response').textContent,/Kept your search when you return/);
  assert.equal(view.getByTestId('task-response').querySelector('strong').textContent,'your search');
  assert.match(view.getByTestId('task-check-summary').textContent,/don’t prove it works/);
  assert.equal(view.queryByText(/^\/approved/),null,'the folder sits on Scope and activity');
  assert.equal(view.queryByText(/DIFF END/),null);
  fireEvent.change(correction,{target:{value:'Keep this unsent correction.'}});
  const changes=view.getByRole('tab',{name:'Changes and checks'});fireEvent.click(changes);
  assert.equal(document.activeElement===changes,true);
  // Every line of the recorded diff, in order, one per row (the newlines become the rows).
  assert.equal(view.getByText(/DIFF END/).closest('pre').textContent,attempt.diff['src/search.ts'].replace(/\n/g,''));
  assert.match(view.getByText(/OUTPUT END/).textContent,/diagnostic retained/);
  assert.equal(view.queryByText(/before-sha/),null,'raw snapshots stay out of view');
  fireEvent.click(view.getByRole('tab',{name:'Scope and activity'}));
  assert.ok(view.getAllByText(/^\/approved/).length>0);
  assert.match(document.querySelector('.dv-kv').textContent,/The thread is private, but its edits still change these files/);
  // The exact record, snapshots included, is kept for support behind one disclosure, and copying it ticks the button.
  let copied='';Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{copied=text;}}});
  fireEvent.click(view.getByRole('button',{name:'Copy the full record'}));
  await waitFor(()=>assert.match(copied,/before-sha[\s\S]*after-sha/));
  await waitFor(()=>assert.ok(view.getByRole('button',{name:'Copied'})));
  assert.ok(view.getByText('Saved guidance version 7'));
  assert.match(view.getByTestId('completion-check').textContent,/Node test/);
  // The correction field sits above the sheet's foot, so it's there on every tab.
  assert.equal(view.getAllByRole('textbox',{name:'Correction request'}).length,1);
  const result=view.getByRole('tab',{name:'Result'});fireEvent.click(result);
  assert.equal(document.activeElement===result,true);
  assert.equal(view.getByRole('textbox',{name:'Correction request'}).value,'Keep this unsent correction.');
  assert.ok(calls.every(call=>call.operation==='task_detail'));
 }finally{cleanup();qc.clear();}
});

test('an unchecked latest attempt does not erase or impersonate a prior check',async()=>{
 for(const completion of [undefined,task.completion]){
  const current={...task,state:'blocked',completion};
  host(op=>{if(op==='task_detail')return {...detail,task:current,evidence:{attempts:[attempt,{...attempt,number:2,state:'blocked',diff:{},test:undefined,blocker:'The latest attempt stopped before checking.'}]}};throw Error(op);});
  const {view,qc}=mount(current);
  try{
   const summary=await view.findByTestId('task-check-summary');
   assert.match(summary.textContent,/No check is recorded for the latest attempt/);
   assert.doesNotMatch(summary.textContent,/No check is recorded for this task/);
   assert.equal(summary.textContent.includes('Saved completion record:'),!!completion);
   fireEvent.click(view.getByRole('tab',{name:'Changes and checks'}));
   assert.ok(view.getByRole('heading',{name:/Attempt 1 · check passed/}));
   assert.ok(view.getByRole('heading',{name:/Attempt 2 · not checked/}));
  }finally{cleanup();qc.clear();}
 }
});

test('a stopped zero-test check stays visible across views and cannot be approved',async()=>{
 const pending={...task,state:'ready_for_review',completion:undefined,acceptance:{required:true,source:'explicit'},error:'Result needs attention.'};
 host(op=>{if(op==='task_detail')return {...detail,task:pending,evidence:{attempts:[{...attempt,blocker:'No tests were reported.',test:{...attempt.test,stopped:'no_tests_reported'}}]}};throw Error(op);});
 const {view,qc}=mount(pending);
 try{
  await view.findByTestId('task-check-summary');
  assert.match(view.getByTestId('task-check-summary').textContent,/doesn’t count as passing/);
  assert.equal(view.getByRole('button',{name:'Approve'}).disabled,true);
  for(const name of ['Changes and checks','Scope and activity','Result']){
   fireEvent.click(view.getByRole('tab',{name}));
   assert.deepEqual(view.getAllByRole('alert').map(node=>node.textContent),['Result needs attention.','No tests were reported.','no_tests_reported']);
  }
 }finally{cleanup();qc.clear();}
});

test('#27: the sheet foot says Approve and commit inside a git repository and Approve outside one',async()=>{
 const pending={...task,state:'ready_for_review',completion:undefined,acceptance:{required:true,source:'explicit'}};
 for(const [git,name] of [[true,'Approve and commit'],[false,'Approve'],[undefined,'Approve']]){
  host(op=>{if(op==='task_detail')return {...detail,task:pending,...(git===undefined?{}:{git})};throw Error(op);});
  const {view,qc}=mount(pending);
  try{
   const button=await view.findByRole('button',{name});
   assert.equal(button.disabled,false,String(git));
   if(git!==true)assert.equal(view.queryByRole('button',{name:/commit/i}),null,'no commit promised outside a repository');
  }finally{cleanup();qc.clear();}
 }
});

test('loading evidence is explicit and does not present a correction until its task details arrive',async()=>{
 let resolve;host(op=>{if(op==='task_detail')return new Promise(done=>{resolve=done;});throw Error(op);});const {view,qc}=mount();
 try{
  assert.ok(view.getByText('Reading the saved changes and checks…'));
  assert.equal(view.queryByRole('textbox',{name:'Correction request'}),null);
  await act(async()=>resolve(detail));
  assert.ok(await view.findByRole('textbox',{name:'Correction request'}));
  assert.equal(view.queryByText('Reading the saved changes and checks…'),null);
 }finally{cleanup();qc.clear();}
});

test('execution scope opens with the account, project and explicit start action, not result acceptance',async()=>{
 const pending={...task,state:'needs_permission',completion:undefined};host(op=>{if(op==='task_detail')return {...detail,task:pending,evidence:{attempts:[]}};throw Error(op);});const {view,qc}=mount(pending);
 try{
  assert.equal(view.getByRole('tab',{name:'Scope and activity'}).getAttribute('aria-selected'),'true');
  assert.ok(view.getAllByText(/^\/approved/).length>0);
  assert.ok(view.getByRole('button',{name:'Approve and start'}));
  assert.equal(view.queryByRole('button',{name:'Approve'}),null);
 }finally{cleanup();qc.clear();}
});

test('interrupted labels preserve failure, uncertainty and later runs',()=>{
 const cases=[['blocked',[{state:'interrupted'}],[],true],['blocked',[{state:'failed'}],[],false],['blocked',[],[],false],['blocked',[null],[],false],['blocked',[{state:'interrupted'},{state:'failed'}],[],false],['blocked',[{state:'uncertain'},{state:'interrupted'}],[],false],['blocked',[{state:'interrupted'}],[{state:'uncertain'}],false],['disconnected',[{state:'uncertain'}],[],false],['working',[{state:'interrupted'}],[],false]];
 for(const [state,runs,tokens,interrupted] of cases){
  const record={...task,state,runs,tokens,completion:undefined};
  assert.equal(resultLabel(record)==='Paused',interrupted);
  assert.equal(statusLabel({state,interrupted})==='Paused',interrupted);
 }
 const record={...task,state:'blocked',runs:[{state:'interrupted'}],completion:undefined,error:'Stopped before provider acknowledgement',collaboration:{review:{phase:'initial'}}};
 assert.equal(resultLabel(record),'Owner paused, review running');
 assert.equal(statusLabel({state:'blocked',interrupted:true,review_active:true}),'Owner paused, review running');
 assert.equal(record.state,'blocked');
});

test('inspector Stop is scoped, pending-disabled and available on every active page',async()=>{
 for(const state of ['working','ready_for_review']){
  const record={...task,state,completion:undefined,collaboration:state==='ready_for_review'?{review:{phase:'initial',enabled:true}}:undefined};
  let rejectStop;
  const calls=host(op=>{if(op==='task_detail')return {...detail,task:record};if(op==='task_stop')return new Promise((_resolve,reject)=>{rejectStop=reject;});throw Error(op);});
  const {view,qc}=mount(record);
  try{
   await view.findByTestId('task-check-summary');
   for(const page of ['Result','Changes and checks','Scope and activity']){
    fireEvent.click(view.getByRole('tab',{name:page}));
    assert.equal(view.getAllByRole('button',{name:'Pause'}).length,1);
   }
   fireEvent.click(view.getByRole('button',{name:'Pause'}));
   assert.equal(view.getByRole('button',{name:'Pausing…'}).disabled,true);
   assert.deepEqual(calls.filter(call=>call.operation==='task_stop'),[{operation:'task_stop',args:{projectId:'p',channelId:'c',taskId:'task'}}]);
   await act(async()=>rejectStop(Error('Stop could not be confirmed.')));
   assert.match(view.getByRole('alert').textContent,/Stop could not be confirmed/);
   assert.equal(view.getByRole('button',{name:'Pause'}).disabled,false);
  }finally{cleanup();qc.clear();}
 }
});

test('stopping in the inspector preserves feedback and shows the returned stopped state',async()=>{
 const qc=client();let record=task,view;
 const element=()=>wrap(qc,React.createElement(TaskDialog,{task:record,projectId:'p',channelId:'c',open:true,onOpenChange(){},refresh:async()=>{view.rerender(element());}}));
 host(op=>{if(op==='task_detail')return {...detail,task:record,retry:{available:record.state==='blocked'}};if(op==='task_stop'){record={...record,state:'blocked',runs:[{state:'interrupted'}],completion:undefined};return record;}throw Error(op);});
 view=render(element());
 try{
  fireEvent.change(await view.findByRole('textbox',{name:'Correction request'}),{target:{value:'Keep this feedback.'}});
  record={...task,state:'working',completion:undefined};view.rerender(element());
  fireEvent.click(view.getByRole('button',{name:'Pause'}));
  await waitFor(()=>assert.equal(view.getByRole('textbox',{name:'Retry task feedback'}).value,'Keep this feedback.'));
  assert.match(view.getByTestId('task-outcome').textContent,/Paused/);
  assert.equal(view.queryByRole('button',{name:'Pause'}),null);
 }finally{cleanup();qc.clear();}
});

test('correction acknowledgment clears only its submitted draft, including when refresh fails',async()=>{
 for(const scenario of ['accepted','new-draft','rejected','refresh-failed']){
  const qc=client();let resolveSend,rejectSend;
  const calls=host(op=>{
   if(op==='task_detail')return detail;
   if(op==='task_continue')return new Promise((resolve,reject)=>{resolveSend=resolve;rejectSend=reject;});
   throw Error(op);
  });
  const view=render(wrap(qc,React.createElement(TaskDialog,{task,projectId:'p',channelId:'c',open:true,onOpenChange(){},refresh:async()=>{if(scenario==='refresh-failed')throw Error('Refresh failed after acknowledgment');}})));
  try{
   const field=await view.findByRole('textbox',{name:'Correction request'});
   fireEvent.change(field,{target:{value:'Check the query again.'}});
   fireEvent.click(view.getByRole('button',{name:'Send'}));
   await waitFor(()=>assert.equal(typeof resolveSend,'function'));
   if(scenario==='new-draft'){
    fireEvent.change(field,{target:{value:'A different draft.'}});
    fireEvent.change(field,{target:{value:'Check the query again.'}});
   }
   await act(async()=>{if(scenario==='rejected')rejectSend(Error('Host refused this continuation'));else resolveSend({state:'working'});});
   await waitFor(()=>assert.equal(field.value,['new-draft','rejected'].includes(scenario)?'Check the query again.':''));
   assert.equal(calls.filter(call=>call.operation==='task_continue').length,1);
   assert.deepEqual(calls.find(call=>call.operation==='task_continue').args,{projectId:'p',channelId:'c',taskId:'task',text:'Check the query again.'});
   if(scenario==='refresh-failed')assert.ok(view.getByText(/Refresh failed after acknowledgment/));
  }finally{cleanup();qc.clear();}
 }
});

test('requesting a correction focuses feedback and stops asking for approval',async()=>{
 let current={...task,state:'ready_for_review',completion:undefined,acceptance:{required:true,source:'project'}};
 const calls=host((operation,args)=>{
  if(operation==='task_detail')return {...detail,task:current,evidence:{attempts:[attempt],review:current.review==='rejected'?{decision:'rejected'}:null}};
  if(operation==='task_review'){assert.equal(args.decision,'rejected');current={...current,review:'rejected'};return {state:'ready_for_review'};}
  if(operation==='task_continue')return {state:'working'};
  throw Error(operation);
 });
 const qc=client();let view;
 const element=()=>wrap(qc,React.createElement(TaskDialog,{task:current,projectId:'p',channelId:'c',refresh:async()=>view.rerender(element()),open:true,onOpenChange(){}}));
 view=render(element());
 try{
  await waitFor(()=>assert.ok(view.getByRole('button',{name:'Ask for changes'})));
  fireEvent.click(view.getByRole('button',{name:'Ask for changes'}));
  await waitFor(()=>assert.ok(document.activeElement===view.getByRole('textbox',{name:'Continue task feedback'}),'Correction feedback should have focus'));
  assert.match(view.getByTestId('task-outcome').textContent,/Waiting for your feedback/);
  assert.equal(view.queryByRole('button',{name:'Approve'}),null);
  assert.equal(view.queryByRole('button',{name:'Ask for changes'}),null);
  assert.equal(calls.filter(call=>call.operation==='task_continue').length,0);
  fireEvent.change(view.getByRole('textbox',{name:'Continue task feedback'}),{target:{value:'Use Fieldnotes as the title.'}});
  fireEvent.click(view.getByRole('button',{name:'Send'}));
  await waitFor(()=>assert.equal(calls.filter(call=>call.operation==='task_continue').length,1));
  assert.deepEqual(calls.find(call=>call.operation==='task_continue').args,{projectId:'p',channelId:'c',taskId:'task',text:'Use Fieldnotes as the title.'});
 }finally{cleanup();qc.clear();}
});
