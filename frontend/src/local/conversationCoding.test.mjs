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
const {codingRequest,registerConversations,CodingBusyError}=await import('./api.ts');
const {TaskFooter,taskProgress}=await import('./CodingTasks.tsx');
const {CodingPermissionDialog}=await import('./CodingPermission.tsx');
const {ConversationIndicator,composerMode}=await import('./TurnControls.tsx');
after(()=>dom.window.close());

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const binding={provider:'codex',identity:'synthetic@example.test',model:'synthetic-model',cwd:'/approved',visibility:'private'};

test('a composer message becomes one coding request with one request id, retried with the same id, and keeps the draft when nothing was recorded',async()=>{
 localStorage.clear();registerConversations([{id:'chat',project_id:'project'}]);
 const answers=[{id:null,taskId:null,state:'needs_permission',detail:'Allow coding in this project first.'},new Error('Lost acknowledgement'),{id:'r1',taskId:'t1',state:'working',detail:'Working'}];
 const calls=host(op=>{if(op==='coding_request'){const next=answers.shift();if(next instanceof Error)throw next;return next;}throw new Error(op);});
 await assert.rejects(codingRequest('chat','Fix the greeting and run the tests'),/Allow coding in this project/);
 assert.equal(localStorage.getItem('dervo-coding-pending:chat'),null,'nothing was recorded, so no request id lingers');
 await assert.rejects(codingRequest('chat','Fix the greeting and run the tests'),/Lost acknowledgement/);
 assert.ok(localStorage.getItem('dervo-coding-pending:chat'),'a lost acknowledgement keeps the request id');
 await assert.rejects(codingRequest('chat','Different text'),/unknown outcome/);
 const result=await codingRequest('chat','Fix the greeting and run the tests');
 assert.deepEqual([result.id,result.taskId,result.state],['r1','t1','working']);
 assert.equal(localStorage.getItem('dervo-coding-pending:chat'),null);
 const sent=calls.filter(c=>c.operation==='coding_request');
 assert.equal(sent.length,3);
 assert.equal(sent[1].args.clientId,sent[2].args.clientId,'the retry reuses the request id');
 assert.notEqual(sent[0].args.clientId,sent[1].args.clientId,'an unrecorded request does not pin a stale id');
 assert.deepEqual(Object.keys(sent[2].args).sort(),['channelId','clientId','projectId','text']);
});

test('a request refused because another conversation holds the run throws the holder with its ids and keeps no request id',async()=>{
 localStorage.clear();registerConversations([{id:'chat',project_id:'project'}]);
 const active={task_id:'other-task',thread_id:'d',project_id:'project',origin:'conversation',agent:null,name:'Design notes'};
 host(op=>{if(op==='coding_request')return {id:null,taskId:null,state:'busy_elsewhere',detail:'Another coding request is active in #Design notes. Wait for it to finish or stop it first.',active};throw new Error(op);});
 await assert.rejects(codingRequest('chat','Fix the greeting'),error=>error instanceof CodingBusyError&&error.active.task_id==='other-task'&&/#Design notes/.test(error.message));
 assert.equal(localStorage.getItem('dervo-coding-pending:chat'),null,'nothing was recorded, so no request id lingers');
});

test('the composer mode names coding only when the project permission is granted, and coordination otherwise',()=>{
 const granted={state:'blocked',active:false,detail:'x',coding:{state:'granted',detail:'d',files:['test_sample.py'],test_file:'test_sample.py'},harness:{state:'available',helper:'claude-abc',provider:'claude',detail:'d'}};
 assert.equal(composerMode({provider:'claude'},granted),'Coding in this project · Claude Code');
 assert.equal(composerMode({provider:'codex'},{state:'unchecked',active:false,detail:'x',coding:{state:'missing',detail:'d',files:[],test_file:null}}),'Chat only');
 assert.equal(composerMode({provider:'claude'},{state:'blocked',active:false,detail:'x',coding:{state:'stale',detail:'d',files:['a.py'],test_file:'a.py'},harness:{state:'available',helper:'claude-abc',provider:'claude',detail:'d'}}),'Coordination · Claude Code · text only');
 const html=renderToStaticMarkup(React.createElement(ConversationIndicator,{binding:{provider:'codex',identity:'me@example.test',model:'m'},status:{state:'answered',active:false,detail:'Answered in text. No project file was changed.',coding:{state:'granted',detail:'d',files:['test_sample.py'],test_file:'test_sample.py'}},onSettings(){}}));
 assert.equal(html,'','settled conversation metadata is shown with the answer, not above the composer'); 
});

test('the task footer reports progress from the host tool records, labels a text answer as text, and never as executed files',()=>{
 assert.equal(taskProgress({state:'working',tokens:[]}),'Waiting for the provider. No file is changed yet.');
 // Bryce, 17 Sep: "· done, provider continuing" read as jargon. A finished step says what it did and who it's waiting for.
 assert.equal(taskProgress({state:'working',binding:{provider:'claude'},tokens:[{tool:'dervo_read_file',state:'delivered',path:'app/ConnectedApp.tsx'}]}),'Read app/ConnectedApp.tsx. Waiting for Claude Code.');
 assert.equal(taskProgress({state:'working',binding:{provider:'claude'},tokens:[{tool:'dervo_write_file',state:'delivered',path:'a.tsx',attempt:3}]}),'Wrote a.tsx (attempt 3). Waiting for Claude Code.');
 assert.equal(taskProgress({state:'working',tokens:[{tool:'something_new',state:'running'}]}),'Waiting for the provider.','an unknown tool name is never shown');
 assert.equal(taskProgress({state:'working',tokens:[{tool:'dervo_read_file',state:'delivered',path:'test_sample.py'}]}),'Read test_sample.py. Waiting for the provider.');
 assert.equal(taskProgress({state:'working',tokens:[{tool:'dervo_write_file',state:'executing',path:'test_sample.py',attempt:1}]}),'Writing test_sample.py (attempt 1)');
 assert.equal(taskProgress({state:'working',tokens:[{tool:'dervo_run_tests',state:'running'}]}),'Running the check');
 assert.equal(taskProgress({state:'ready_for_review',tokens:[{tool:'dervo_run_tests',state:'delivered'}]}),'');
 const task=state=>({id:'t',state,origin:'conversation',session_id:'s',scope_version:1,files:['test_sample.py'],argv:[],binding,messages:[{role:'user',text:'Fix it',created_at:1}],tokens:[{tool:'dervo_run_tests',state:'running'}],runs:[]});
 const footer=props=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(TaskFooter,props)));
 // While the owner works the live step sits under its reply and the panel spins; the bar says nothing.
 assert.equal(footer({task:task('working'),projectId:'p',channelId:'c',onInspect(){}}),'');
 assert.equal(footer({task:{...task('working'),collaboration:{review:{phase:'pending',reviewer:'r',effort:'low'}}},projectId:'p',channelId:'c',onInspect(){},guideVisible:true}),'');
 assert.equal(footer({task:{...task('answered'),tokens:[]},projectId:'p',channelId:'c',onInspect(){}}),'','nothing under a text answer');
 const reviewable=footer({task:task('ready_for_review'),projectId:'p',channelId:'c',onInspect(){}});
 assert.ok(reviewable.includes('Ready for review.'));assert.ok(reviewable.includes('>Inspect<'));assert.ok(!reviewable.includes('Stop'));
});

test('the one-time project permission is explicit: chosen existing files and one root unittest file, revocable, never the project root',async()=>{
 let permission={state:'missing',detail:'Allow coding in this project first.',files:[],test_file:null,candidates:['greeting.py','secret_notes.py','test_sample.py'],test_candidates:['test_sample.py'],created_at:null};
 const calls=host((op,args)=>{
  if(op==='coding_permission')return permission;
  if(op==='coding_permission_grant'){permission={...permission,state:'granted',files:[...args.files].sort(),test_file:args.testFile,created_at:1};return permission;}
  if(op==='coding_permission_revoke'){permission={...permission,state:'missing',files:[],test_file:null,created_at:null};return permission;}
  throw new Error(op);
 });
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 let refreshed=0,closed=0;
 const mount=()=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client},React.createElement(CodingPermissionDialog,{open:true,onOpenChange:v=>{if(!v)closed++;},projectId:'p',channelId:'c',binding,refresh:async()=>{refreshed++;}}))));
 try{
  const view=mount();
  const allow=await view.findByRole('button',{name:'Allow coding'});
  await view.findByLabelText('Allow greeting.py');
  // The dialog renders through a portal on document.body, outside the render container.
  assert.ok(document.body.textContent.includes('Choose where Dervo can work'));
  assert.ok(document.body.textContent.includes('Not granted: the project root'));
  assert.equal(view.queryByRole('button',{name:'Stop allowing coding'}),null,'nothing to revoke yet');
  await waitFor(()=>assert.equal(view.getByLabelText('Test file').value,'test_sample.py','the only root unittest file is preselected'));
  fireEvent.click(view.getByLabelText('Allow greeting.py'));
  assert.equal(view.getByLabelText('Allow secret_notes.py').checked,false,'nothing is selected on the user\'s behalf');
  assert.equal(allow.disabled,false);
  fireEvent.click(allow);
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_permission_grant').length,1));
  const grant=calls.find(c=>c.operation==='coding_permission_grant').args;
  assert.deepEqual({...grant,files:[...grant.files].sort()},{projectId:'p',channelId:'c',files:['greeting.py','test_sample.py'],testFile:'test_sample.py',checkTimeoutSeconds:5});
  await waitFor(()=>assert.equal(refreshed,1));
  await waitFor(()=>assert.equal(closed,1,'a grant closes the one-time dialog'));
  cleanup();
  const again=mount();
  const revoke=await again.findByRole('button',{name:'Stop allowing coding'});
  await waitFor(()=>assert.ok(again.getByTestId('coding-permission-state').textContent.includes('Allowed: 2 file(s)')));
  fireEvent.click(revoke);
  assert.equal(calls.filter(c=>c.operation==='coding_permission_revoke').length,0,'stopping asks first');
  fireEvent.click(again.getByRole('button',{name:'Keep allowing'}));
  assert.ok(again.getByRole('button',{name:'Stop allowing coding'}));
  fireEvent.click(again.getByRole('button',{name:'Stop allowing coding'}));
  fireEvent.click(again.getByRole('button',{name:'Stop allowing'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_permission_revoke').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='coding_permission_revoke').args,{projectId:'p',channelId:'c'});
 }finally{cleanup();client.clear();}
});

test('Rust tests save the selected toolchain, file and shared check duration',async()=>{
 const permission={state:'missing',detail:'Choose files.',files:[],test_file:null,candidates:['check.rs'],test_candidates:['check.rs'],created_at:null,
  check_profiles:[{id:'python-unittest',label:'Python unittest',available:true,test_pattern:'test_*.py'},{id:'rust-test',label:'Rust tests',available:true,test_pattern:'root .rs file',detail:'Compiles and runs the selected Rust tests.'}]};
 const calls=host(op=>{if(op==='coding_permission'||op==='coding_permission_grant')return permission;throw new Error(op);});
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client},React.createElement(CodingPermissionDialog,{open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{}}))));
 try{
  await view.findByLabelText('Allow check.rs');
  fireEvent.change(view.getByLabelText('Check'),{target:{value:'rust-test'}});
  fireEvent.change(await view.findByLabelText('Rust toolchain folder'),{target:{value:'/trusted/rust'}});
  fireEvent.click(view.getByLabelText('Allow check.rs'));
  fireEvent.change(view.getByLabelText('Test file'),{target:{value:'check.rs'}});
  fireEvent.change(view.getByLabelText('Maximum check time in seconds'),{target:{value:'30'}});
  fireEvent.click(view.getByRole('button',{name:'Allow coding'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_permission_grant').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='coding_permission_grant').args,{projectId:'p',channelId:'c',files:['check.rs'],testFile:'check.rs',checkProfile:'rust-test',rustToolchain:'/trusted/rust',checkTimeoutSeconds:30});
  assert.equal(calls.filter(c=>/coding_request|start_turn/.test(c.operation)).length,0);
 }finally{cleanup();client.clear();}
});

test('the app routes a granted conversation to coding first, keeps the help and chat paths, keeps the old task entry, and the bridge allows exactly the new operations',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 // One route per message: routeSubmit() decides coding, coordination or chat before anything is sent (see collaboration.test.mjs for the exact table).
 // The send reads the conversation it's for (here): the loaded activity, or the snapshot it loads when none is (#59).
 assert.match(source,/routeSubmit\(text,here\.activity\.coding,here\.activity\.collaboration,here\.harness\)/);
 assert.match(source,/const here=activity\.data\?\{activity:activity\.data,harness:coordination,/);
 const codingAt=source.indexOf("route.path==='coding'"),coordinationAt=source.indexOf("route.path==='coordination'&&helper?.state==='available'&&helper.helper");
 assert.ok(codingAt>0&&coordinationAt>codingAt,'coding is decided before coordination');
 assert.equal((source.match(/await codingRequest\(/g)??[]).length,1,'one coding send site');
 assert.equal((source.match(/await coordinate\(/g)??[]).length,1,'one coordination send site');
 // The third argument is the per-request Independent review override, resolved before anything is sent; the host validates it again.
 assert.match(source,/await codingRequest\(target\.id,text,reviewOverridePayload\(review\.value,here\.activity\.collaboration\?\.review\),images,check\.payload\(\)\)/);
 assert.match(source,/if\(await refreshConversation\(\)&&review\.settle\(target\.id\)\)setReply\(null\)/,'a completed send resets composer state only for the conversation it was sent from');
 assert.match(source,/const helper=here\.harness;/);
 assert.match(source,/await coordinate\(target\.id,text,helper\.helper\)/);
 assert.match(source,/send\.mutateAsync/);
 assert.match(source,/settingsPage==='access'&&settingsProject&&<AccessPage /);
 assert.equal(source.includes('<TaskStart '),false);
 assert.doesNotMatch(source,/coding_request'/,'the composer goes through codingRequest(), which owns the request id');
 assert.doesNotMatch(source,/task_propose|proposal_accept/,'no proposal ceremony on the composer path');
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['coding_permission','coding_permission_grant','coding_permission_revoke','coding_request','coding_requests','coding_effort_set'])assert.ok(bridge.includes(`"${op}"`),op);
 // Offline contract for the native allowlist (no cargo build here): the enum variant and its exact wire name both exist, once each.
 assert.equal((bridge.match(/\bCodingEffortSet\b/g)??[]).length,2,'one enum variant and one name mapping for coding_effort_set');
 assert.ok(bridge.includes('Self::CodingEffortSet=>"coding_effort_set"'),'the wire name the frontend sends');
 for(const name of ['CodingShell','CodingRoot','CodingPermissionAll','CodingEffortSetAll','CodingEffortGet'])assert.ok(!bridge.includes(name),`${name} must not be an allowlisted operation`);
});


test('compact answer review comes from this run, never a retained older completion',()=>{
 const task={id:'t',state:'answered',binding,messages:[],tokens:[],runs:[{id:'new',state:'completed'}],completion:{run_id:'old',review_phase:'passed',test_exit_status:0,changed_files:[]},collaboration:{review:{phase:'skipped'}}};
 const footer=value=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(TaskFooter,{task:value,projectId:'p',channelId:'c',onInspect(){}})));
 assert.equal(footer(task),'','a settled answer needs no bar');
 assert.ok(!footer({...task,collaboration:{review:{phase:'passed'}}}).includes('Review passed'),'the report speaks for itself');
 for(const patch of [{error:'Checks failed'},{tokens:[{state:'uncertain',tool:'dervo_create_file'}]},{collaboration:{review:{phase:'pending'}}}])assert.ok(footer({...task,...patch}).includes('dv-decision'),JSON.stringify(patch));
});

test('a message queued behind this conversation\'s own run resolves as queued and the pending draft key is cleared',async()=>{
 localStorage.clear();registerConversations([{id:'chat',project_id:'project'}]);
 const calls=host(op=>{if(op==='coding_request')return {id:'q1',taskId:null,task_id:null,state:'queued',queued:true,position:1,detail:'Queued. Starts when the current task ends.',text:'Then fix the greeting',created_at:1};throw new Error(op);});
 const result=await codingRequest('chat','Then fix the greeting');
 assert.equal(result.state,'queued');assert.equal(result.taskId,null);assert.equal(result.position,1);
 assert.equal(calls.length,1);assert.equal(localStorage.getItem('dervo-coding-pending:chat'),null,'the draft is not held for a retry: the host has the message');
});
