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
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
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

test('the result approval toggle loads from the host, saves at once, shows the host value on reopen and surfaces a refusal',async()=>{
 let acceptance={required:false,source:'default',detail:DETAIL+'A finished result counts as Completed.'};
 let refuse=false;
 const permission=()=>({state:'granted',detail:'d',files:['greeting.py','test_sample.py'],test_file:'test_sample.py',candidates:['greeting.py','test_sample.py'],test_candidates:['test_sample.py'],created_at:1,owner_effort:null,owner_effort_detail:'',acceptance});
 const calls=host((op,args)=>{
  if(op==='coding_permission')return permission();
  if(op==='coding_acceptance_set'){if(refuse)throw new Error('Result approval is exactly on (true) or off (false)');acceptance={...acceptance,required:args.required,source:'explicit'};return permission();}
  throw new Error(op);
 });
 const qc=client();let refreshed=0;
 const mount=()=>render(wrap(qc,React.createElement(CodingPermissionDialog,{open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{refreshed++;}})));
 try{
  const view=mount();
  const box=view.getByLabelText('Require your approval of each result');
  assert.equal(box.disabled,true,'disabled until the host value is loaded');
  assert.ok(document.body.textContent.includes('Loading the saved result approval setting.'));
  // The dialog header states the permission scope and the independently optional result approval, never a review promise
  // the policy does not make. While the host value loads, it makes no claim either way.
  const header=()=>view.getByTestId('approval-summary').textContent;
  assert.ok(!header().includes('Every change still needs your review'),header());
  assert.ok(!/approval is (on|off)/i.test(header()),'no approval claim before the host value is loaded');
  assert.ok(document.body.textContent.includes('Choose where Dervo can work'));
  assert.equal(view.getByTestId('coding-permission-summary').textContent,`${binding.cwd.split('/').filter(Boolean).pop()} · ${binding.model}`);
  await waitFor(()=>assert.equal(view.getByTestId('result-approval').dataset.ready,'true'));
  assert.equal(box.checked,false,'a new project defaults to off');
  assert.ok(header().includes('Result approval is off'),header());
  assert.ok(!header().includes('needs your review'),header());
  assert.ok(header().includes('counts as Completed without your approval'),header());
  // #28: off still says why full-access tasks, and changes you review yourself, wait for Approve.
  assert.ok(header().includes('Full-access tasks and changes you review yourself always wait for your approval, because no checks run in Dervo for them.'),header());
  assert.ok(view.getByTestId('result-approval-detail').textContent.includes('does not authorise file access, destructive actions, deployment or spending'));
  assert.ok(document.body.textContent.includes('separate from Independent review'));
  fireEvent.click(box);
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_acceptance_set').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='coding_acceptance_set').args,{projectId:'p',channelId:'c',required:true});
  await waitFor(()=>assert.equal(view.getByLabelText('Require your approval of each result').checked,true));
  await waitFor(()=>assert.equal(refreshed,1));
  assert.ok(view.getByTestId('result-approval-detail').textContent.startsWith('On.'));
  assert.ok(header().includes('Result approval is on')&&header().includes('waits for your approval'),header());
  assert.ok(!header().includes('without your approval'),header());
  assert.ok(!header().includes('Full-access tasks'),'with approval on there is no exception to explain');
  cleanup();
  const again=mount();
  await waitFor(()=>assert.equal(again.getByLabelText('Require your approval of each result').checked,true,'reopen shows the host value'));
  refuse=true;
  fireEvent.click(again.getByLabelText('Require your approval of each result'));
  await waitFor(()=>assert.ok(again.getByRole('alert').textContent.includes('exactly on (true) or off (false)')));
  assert.equal(again.getByLabelText('Require your approval of each result').checked,true,'a refused save keeps the host value');
  assert.ok(again.getByTestId('approval-summary').textContent.includes('Result approval is on'),'a refused save keeps the header on the host value');
  assert.ok(!document.body.textContent.includes('—'),'no em dash in the copy');
  // Technical boundaries stay available behind a disclosure, not in the primary text.
  const details=document.querySelector('details[data-testid="coding-permission-details"]');
  assert.ok(details,'a details disclosure holds the technical boundaries');
  const primaryText=()=>[...document.querySelectorAll('[role="dialog"] p')].filter(p=>!details.contains(p)).map(p=>p.textContent).join('\n');
  assert.ok(primaryText().includes('Not granted: the project root'),'permission exclusions stay in the primary text');
  assert.ok(details.textContent.includes('applies to new requests only'),'the new-requests-only rule is kept');
  assert.ok(details.contains(again.getByTestId('result-approval-detail')));
  assert.ok(details.contains(again.getByTestId('owner-effort-detail')));
  const primary=[...document.querySelectorAll('[role="dialog"] p')].filter(p=>!details.contains(p)).map(p=>p.textContent);
  assert.ok(primary.every(t=>t.length<=220),`primary text is short: ${JSON.stringify(primary)}`);
  assert.ok(document.body.textContent.includes('synthetic@example.test')&&document.body.textContent.includes('synthetic-model')&&document.body.textContent.includes('/approved'),'identity, model and project stay visible');
  assert.ok(!document.body.textContent.includes('Unmodified Claude Code'),'a Codex binding gets no Claude prose');
 }finally{cleanup();qc.clear();}
});

test('a legacy result approval policy is named as kept from before the setting existed, beside the approval control and in the detail',async()=>{
 const acceptance={required:true,source:'legacy',detail:DETAIL+'Each finished result waits for your explicit approval or correction request.'};
 host(op=>{if(op==='coding_permission')return {state:'granted',detail:'d',files:['greeting.py'],test_file:'test_sample.py',candidates:['greeting.py'],test_candidates:['test_sample.py'],created_at:1,owner_effort:null,owner_effort_detail:'',acceptance};throw new Error(op);});
 const qc=client();
 try{
  const view=render(wrap(qc,React.createElement(CodingPermissionDialog,{open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{}})));
  await waitFor(()=>assert.equal(view.getByTestId('result-approval').dataset.ready,'true'));
  assert.equal(view.getByLabelText('Require your approval of each result').checked,true);
  const header=view.getByTestId('approval-summary').textContent;
  assert.ok(header.includes('Result approval is on')&&header.includes('kept from before this setting existed'),header);
  assert.ok(view.getByTestId('result-approval-detail').textContent.includes('kept from before this setting existed'));
 }finally{cleanup();qc.clear();}
});

test('result footers say Completed or Needs your review, show review activity, and keep Stop while the reviewer is active after the owner turn',async()=>{
 const task=(state,extra={})=>({id:'t',state,origin:'conversation',session_id:'s',scope_version:1,files:['greeting.py'],argv:[],binding,messages:[{role:'user',text:'Fix it',created_at:1}],tokens:[],runs:[],acceptance:{required:false,source:'default'},...extra});
 const completion={at:1,attempt:2,run_id:'r',review_phase:'passed',changed_files:['greeting.py'],test_exit_status:0,human_approved:false};
 assert.equal(resultLabel({state:'completed',completion}),'Completed');
 assert.equal(resultLabel({state:'completed',completion:{...completion,changed_files:[]}}),'Completed, checks passed, no files changed');
 assert.equal(resultLabel({state:'ready_for_review'}),'Needs your review');
 assert.equal(resultLabel({state:'answered'}),'');
 assert.equal(completionDetail({state:'completed',completion}),'Changed greeting.py. Host tests exit 0. Independent review passed. Not marked as approved by you.');
 assert.equal(completionDetail({state:'completed',completion:{...completion,review_phase:null,changed_files:[]}}),'No file changed. Host tests exit 0. No independent review was configured. Not marked as approved by you.');
 const review=phase=>({enabled:true,reviewer:'reviewer',effort:'low',provider:'codex',model:'m',profile_id:'cx',channel_id:'review-room',source:'policy',phase,packets:[],findings:null,unresolved:null,snapshot:null,error:null});
 const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
 const qc=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const footer=props=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client:qc},React.createElement(TaskFooter,props)));
 const completed=footer({task:task('completed',{completion,collaboration:{mode:'off',agents:[],depth:0,root_task_id:'t',chain:[],limits:{max_parallel:3,max_depth:1,max_calls:6},review:review('passed')}}),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(completed.includes('Finished.'),completed);assert.ok(completed.includes('Review passed.'),'the bar keeps the review verdict as its clause');assert.ok(completed.includes('>Inspect<'),completed);
 assert.ok(!completed.includes('>Stop<'),completed);assert.ok(!completed.includes('Approved.'),'Completed is a host fact, never your approval');
 assert.ok(!completed.includes('passed, no open findings'),completed);
 // A human review is asked only where the request's snapshot says approval is on; a legacy task without a snapshot keeps it.
 const needs=footer({task:task('ready_for_review',{acceptance:{required:true,source:'explicit'}}),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(needs.includes('Ready for review.'),needs);assert.ok(!needs.includes('>Stop<'),needs);
 assert.ok(footer({task:task('ready_for_review',{acceptance:undefined}),projectId:'p',channelId:'c',onInspect(){}}).includes('Ready for review.'),'legacy task without a snapshot');
 const plainOff=footer({task:task('ready_for_review'),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(plainOff.includes('Needs attention.')&&!plainOff.includes('Ready for review.'),plainOff);
 const reviewing=footer({task:task('ready_for_review',{collaboration:{mode:'off',agents:[],depth:0,root_task_id:'t',chain:[],limits:{max_parallel:3,max_depth:1,max_calls:6},review:review('initial')}}),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(reviewing.includes('Independent review running.')&&reviewing.includes('>Stop<'),reviewing);
 // Approval off and the reviewer ended without a pass: the human never asked to approve, so the result needs attention, never
 // 'Ready for review'. With approval on, the same phases are the human's review. The correction button needs the record loaded.
 for(const phase of ['unresolved','stale','failed','interrupted']){
  const html=footer({task:task('ready_for_review',{collaboration:{mode:'off',agents:[],depth:0,root_task_id:'t',chain:[],limits:{max_parallel:3,max_depth:1,max_calls:6},review:review(phase)}}),projectId:'p',channelId:'c',onInspect(){}});
  assert.ok(html.includes('Needs attention.')&&html.includes(`Independent review ended ${phase}.`)&&!html.includes('Ready for review.')&&!html.includes('>Stop<')&&!html.includes('Finished.'),phase+': '+html);
  const on=footer({task:task('ready_for_review',{acceptance:{required:true,source:'explicit'},collaboration:{mode:'off',agents:[],depth:0,root_task_id:'t',chain:[],limits:{max_parallel:3,max_depth:1,max_calls:6},review:review(phase)}}),projectId:'p',channelId:'c',onInspect(){}});
  assert.ok(on.includes('Ready for review.')&&!on.includes('Needs attention')&&!on.includes('>Stop<'),phase+' with approval on');
 }
 assert.equal(resultLabel({state:'ready_for_review',acceptance:{required:false,source:'default'}}),'Needs attention');
 assert.equal(resultLabel({state:'ready_for_review',acceptance:{required:true,source:'explicit'}}),'Needs your review');
 assert.ok(!reviewing.includes('Ready for review.'),'the human is not asked while agents still review');
 // Approval off and the host checks failed: attention, not an approval the human never configured. The error is the bar's clause.
 const failed=footer({task:task('ready_for_review',{error:'Host tests failed or stopped. Inspect the failure and request a correction. Approval is unavailable.'}),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(failed.includes('Needs attention.')&&failed.includes('Host tests failed or stopped.')&&!failed.includes('Ready for review.'),failed);
 assert.equal((failed.match(/approved by you/g)??[]).length,0,'no repeated approval disclaimers on a failed result');
 assert.equal((completed.match(/approved by you/g)??[]).length,0,'a finished bar is one sentence and one clause');
 // A completed result whose files changed afterwards: no approval offered, the correction route is.
 const stale=footer({task:task('ready_for_review',{completion:{...completion,stale:true}}),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(staleCompleted({state:'ready_for_review',completion:{...completion,stale:true}})&&!staleCompleted({state:'completed',completion:{...completion,stale:true}}));
 assert.ok(stale.includes('The files changed after this result.')&&stale.includes('Ask for changes')&&!stale.includes('Ready for review.')&&!stale.includes('Approve<'),stale);
 // Stop from the bar while the reviewer is active reaches task_stop for this task.
 const calls=host(op=>{if(op==='task_stop')return task('ready_for_review');throw new Error(op);});
 try{
  const view=render(React.createElement(QueryClientProvider,{client:qc},React.createElement(TaskFooter,{task:task('ready_for_review',{collaboration:{mode:'off',agents:[],depth:0,root_task_id:'t',chain:[],limits:{max_parallel:3,max_depth:1,max_calls:6},review:review('final')}}),projectId:'p',channelId:'c',onInspect(){}})));
  fireEvent.click(view.getByText('Stop'));
  await waitFor(()=>assert.deepEqual(calls.filter(c=>c.operation==='task_stop').map(c=>[c.operation,c.args]),[['task_stop',{projectId:'p',channelId:'c',taskId:'t'}]]));
 }finally{cleanup();}
});

test('the conversation indicator names Completed, an active review, attention and a stale completion truthfully',()=>{
 const indicator=status=>renderToStaticMarkup(React.createElement(ConversationIndicator,{binding:{provider:'codex',identity:'me@example.test',model:'m'},status:{active:false,detail:'d',...status},onSettings(){}}));
 assert.equal(statusLabel({state:'completed'}),'Completed');
 assert.equal(statusLabel({state:'idle',interrupted:true}),'Paused');
 assert.equal(statusLabel({state:'unknown',interrupted:true}),'Delivery unknown');
 assert.equal(statusLabel({state:'ready_for_review',review_active:true}),'Independent review running');
 assert.equal(statusLabel({state:'ready_for_review',acceptance_required:false}),'Needs attention');
 assert.equal(statusLabel({state:'ready_for_review',acceptance_required:true}),'Ready for review');
 assert.equal(statusLabel({state:'ready_for_review',stale_completion:true,acceptance_required:false}),'Completed result no longer current');
 assert.equal(statusLabel({state:'ready_for_review'}),'Ready for review','a legacy status without the new fields keeps its label');
 const completed=indicator({state:'completed'});
 assert.equal(completed,'','a settled result is shown with the answer, not above the composer');
 assert.ok(indicator({state:'ready_for_review',stale_completion:true,acceptance_required:false}).includes('Completed result no longer current'));
 assert.ok(indicator({state:'ready_for_review',review_active:true,review_phase:'initial'}).includes('Independent review running'));
 assert.ok(indicator({state:'ready_for_review',acceptance_required:false}).includes('Needs attention'));
});

test('a completed result offers a correction through the same task and no approval button; the bridge allows exactly the policy operation',async()=>{
 const task={id:'t',state:'completed',origin:'conversation',session_id:'s',scope_version:1,files:['greeting.py'],argv:[],binding,messages:[{role:'user',text:'Fix it',created_at:1}],tokens:[],runs:[{prior_attempts:0}],acceptance:{required:false,source:'default'},completion:{at:1,attempt:1,run_id:'r',review_phase:null,changed_files:['greeting.py'],test_exit_status:0,human_approved:false}};
 const detail={task,retry:{available:false},evidence:{attempts:[{number:1,state:'verified',diff:{'greeting.py':'+# v1'},before:{},after:{},argv:[],test:{argv:[],stdout:'',stderr:'',exit_status:0,stopped:null}}],review:null},broker_audit:[],collaboration:{}};
 const calls=host((op,args)=>{if(op==='task_detail')return detail;if(op==='task_continue')return {...task,state:'working'};throw new Error(op);});
 const qc=client();
 try{
  const view=render(wrap(qc,React.createElement(TaskDialog,{task,projectId:'p',channelId:'c',refresh:async()=>{},open:true,onOpenChange(){}})));
  const box=await view.findByLabelText('Correction request');
  assert.equal(view.queryByRole('button',{name:'Approve'}),null,'nothing to approve: approval was off for this request');
  assert.ok(document.body.textContent.includes('nothing about the completed result is recorded as rejected or approved'));
  fireEvent.change(box,{target:{value:'Add one more comment line.'}});
  fireEvent.click(view.getByRole('button',{name:'Send'}));
  await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='task_continue').args,{projectId:'p',channelId:'c',taskId:'t',text:'Add one more comment line.'}));
  assert.equal(calls.filter(c=>c.operation==='task_review').length,0,'no rejection is recorded for past work');
 }finally{cleanup();qc.clear();}
 // The same result after the files changed outside: approval controls are gone; the correction continues the task.
 const staleTask={...task,state:'ready_for_review',error:'Files changed after this result was completed.',completion:{...task.completion,stale:true}};
 const staleDetail={...detail,task:staleTask};
 const staleCalls=host((op)=>{if(op==='task_detail')return staleDetail;if(op==='task_continue')return {...staleTask,state:'working'};throw new Error(op);});
 const qc2=client();
 try{
  const view=render(wrap(qc2,React.createElement(TaskDialog,{task:staleTask,projectId:'p',channelId:'c',refresh:async()=>{},open:true,onOpenChange(){}})));
  const box=await view.findByLabelText('Correction request');
  await waitFor(()=>assert.ok(staleCalls.some(c=>c.operation==='task_detail')));
  assert.equal(view.queryByRole('button',{name:'Approve'}),null,'approval is unavailable for a stale completed result');
  assert.equal(view.queryByRole('button',{name:'Ask for changes'}),null,'the rejected-review route would fail; it is not offered');
  assert.ok(document.body.textContent.includes('approval is unavailable for the old result'));
  fireEvent.change(box,{target:{value:'Redo from the current files.'}});
  fireEvent.click(view.getByRole('button',{name:'Send'}));
  await waitFor(()=>assert.deepEqual(staleCalls.find(c=>c.operation==='task_continue').args,{projectId:'p',channelId:'c',taskId:'t',text:'Redo from the current files.'}));
  assert.equal(staleCalls.filter(c=>c.operation==='task_review').length,0);
 }finally{cleanup();qc2.clear();}
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 assert.equal((bridge.match(/\bCodingAcceptanceSet\b/g)??[]).length,2,'one enum variant and one name mapping');
 assert.ok(bridge.includes('Self::CodingAcceptanceSet=>"coding_acceptance_set"'));
 for(const name of ['CodingAcceptanceGet','TaskComplete','TaskAutoApprove'])assert.ok(!bridge.includes(name),name);
 const api=readFileSync(new URL('./api.ts',import.meta.url),'utf8');
 assert.match(api,/coding_acceptance_set/);
});
