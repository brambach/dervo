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
const {CodingPermissionDialog}=await import('./CodingPermission.tsx');
const {TaskDialog,checkProfileLabel,checkExitDetail}=await import('./CodingTasks.tsx');
const {grantPayload,CHECK_TEST_NAMES}=await import('./api.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element)=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:client()},element)));
const CATALOGUE=[
 {id:'python-unittest',label:'Python unittest',test_pattern:'test_*.py',available:true,detail:'Runs the audited Python runtime with unittest discover on the task snapshot.'},
 {id:'node-test',label:'Node test',test_pattern:'test_*.mjs|cjs|js or *.test.mjs|cjs|js',available:true,detail:'Runs a trusted Node install on the task snapshot. No shell, no npm scripts, no network.'},
 {id:'rust-check',label:'Rust type check',test_pattern:'root .rs file',available:true,detail:'Metadata only; no Rust tests execute.'},
 {id:'typescript-check',label:'TypeScript check',test_pattern:'tsconfig.json',available:true,detail:'Runs a copy of the project node_modules/typescript compiler with --noEmit -p tsconfig.json on the task snapshot. An external dependency import fails visibly with TS2307.'},
 {id:'future-check',label:'Future check (unavailable)',test_pattern:'x',available:false,detail:'Not available in this build.'},
];
const permission=(check,testFile)=>({state:testFile?'granted':'missing',detail:testFile?`Coding here may run the ${check} check on ${testFile} in confinement.`:'Allow coding first.',files:testFile?['greeting.py',testFile]:[],folders:[],test_file:testFile??null,check_profile:testFile?check:null,check_profiles:CATALOGUE,check_detail:CATALOGUE.find(p=>p.id===check).detail,provider:'codex',candidates:['greeting.py','test_sample.mjs','test_sample.py','tsconfig.json'],test_candidates:['test_sample.mjs','test_sample.py','tsconfig.json'],created_at:testFile?1:null,actor:testFile?'local-user':null,owner_effort:null,owner_effort_detail:'',acceptance:{required:false,source:'default',detail:''}});
const binding={provider:'codex',identity:'me@example.test',model:'synthetic-model',cwd:'/approved',visibility:'private'};
const dialog=()=>React.createElement(CodingPermissionDialog,{open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{}});
const grants=calls=>calls.filter(c=>c.operation==='coding_permission_grant').map(c=>c.args);

test('grant payload: the Python default keeps the older shape; another check is sent as checkProfile',()=>{
 assert.deepEqual(grantPayload(['a.py','test_a.py'],'test_a.py',[]),{files:['a.py','test_a.py'],testFile:'test_a.py'});
 assert.deepEqual(grantPayload(['a.py','test_a.py'],'test_a.py',[],'python-unittest'),{files:['a.py','test_a.py'],testFile:'test_a.py'});
 assert.deepEqual(grantPayload(['a.mjs','test_a.mjs'],'test_a.mjs',['src'],'node-test'),{files:['a.mjs','test_a.mjs'],testFile:'test_a.mjs',folders:['src'],checkProfile:'node-test'});
});

test('the check is chosen once from the host catalogue, filters the test file, and an unavailable entry cannot be chosen',async()=>{
 let saved=permission('python-unittest',null);
 const calls=host((op,args)=>{if(op==='coding_permission')return saved;if(op==='coding_permission_grant'){saved=permission(args.checkProfile??'python-unittest',args.testFile);return saved;}throw new Error(op);});
 mount(dialog());
 const check=await screen.findByLabelText('Check');
 await waitFor(()=>assert.equal(check.options.length,5));
 assert.equal(check.value,'python-unittest','default is the Python unittest check');
 const future=[...check.options].find(o=>o.value==='future-check');
 assert.ok(future.disabled,'an unavailable profile is visible but not selectable');
 const ts=[...check.options].find(o=>o.value==='typescript-check');
 assert.ok(!ts.disabled,'the TypeScript check is selectable when the host lists it as available');
 assert.match(screen.getByTestId('check-detail').textContent,/Python runtime/);
 const testFile=screen.getByLabelText('Test file');
 assert.deepEqual([...testFile.options].map(o=>o.value).filter(Boolean),['test_sample.py'],'a Python check offers only unittest files');
 fireEvent.change(check,{target:{value:'node-test'}});
 assert.match(screen.getByTestId('check-detail').textContent,/No shell, no npm scripts/,'the allowed execution is explained before granting');
 assert.deepEqual([...testFile.options].map(o=>o.value).filter(Boolean),['test_sample.mjs'],'a Node check offers only node:test files');
 fireEvent.click(screen.getByLabelText('Allow greeting.py'));
 fireEvent.change(testFile,{target:{value:'test_sample.mjs'}});
 fireEvent.click(screen.getByRole('button',{name:/^(Allow coding|Save file access)$/}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.deepEqual(grants(calls)[0],{projectId:'p',channelId:'c',files:['greeting.py','test_sample.mjs'],testFile:'test_sample.mjs',checkProfile:'node-test',checkTimeoutSeconds:5});
 assert.ok(!calls.some(c=>c.operation==='coding_request'||c.operation==='task_create'),'granting never starts coding');
 assert.match(document.body.textContent,/never picks an executable, arguments, a shell or an environment/);
 assert.match(document.body.textContent,/project's own node_modules\/typescript compiler/,'the disclosure explains the TypeScript check accurately');
});

test('the central name rules cover every host check, including the TypeScript root tsconfig.json',()=>{
 assert.deepEqual(Object.keys(CHECK_TEST_NAMES).sort(),['node-test','python-unittest','rust-check','rust-test','typescript-check']);
 assert.ok(CHECK_TEST_NAMES['typescript-check'].test('tsconfig.json'));
 for(const name of ['tsconfig.node.json','src/tsconfig.json','test_a.mjs','tsconfig.json '])assert.ok(!CHECK_TEST_NAMES['typescript-check'].test(name),name);
});

test('a TypeScript grant whose compiler or config the host refuses shows the host reason and records nothing',async()=>{
 const saved=permission('python-unittest',null);
 const calls=host((op)=>{if(op==='coding_permission')return saved;if(op==='coding_permission_grant')throw new Error('No TypeScript compiler at node_modules/typescript in the project. Install typescript there yourself or choose another check profile.');throw new Error(op);});
 mount(dialog());
 const check=await screen.findByLabelText('Check');
 await waitFor(()=>assert.equal(check.options.length,5));
 fireEvent.change(check,{target:{value:'typescript-check'}});
 fireEvent.change(screen.getByLabelText('Test file'),{target:{value:'tsconfig.json'}});
 fireEvent.click(screen.getByRole('button',{name:/^(Allow coding|Save file access)$/}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/No TypeScript compiler at node_modules\/typescript/));
 assert.match(screen.getByTestId('coding-permission-state').textContent,/Allow coding first/,'nothing was granted');
});

test('a stale TypeScript grant carries the host reason (compiler gone, config escaping) into the dialog',async()=>{
 const stale={...permission('typescript-check','tsconfig.json'),state:'stale',detail:'The typescript-check check cannot run: No TypeScript compiler at node_modules/typescript in the project. Install typescript there yourself or choose another check profile. Allow coding again with a usable check.'};
 host((op)=>{if(op==='coding_permission')return stale;throw new Error(op);});
 mount(dialog());
 await waitFor(()=>assert.match(screen.getByTestId('coding-permission-state').textContent,/cannot run: No TypeScript compiler/));
 assert.equal((await screen.findByLabelText('Check')).value,'typescript-check','the saved check stays selected for the re-grant');
});

test('the TypeScript check offers only the root tsconfig.json as its file and sends checkProfile',async()=>{
 let saved=permission('python-unittest',null);
 const calls=host((op,args)=>{if(op==='coding_permission')return saved;if(op==='coding_permission_grant'){saved=permission(args.checkProfile??'python-unittest',args.testFile);return saved;}throw new Error(op);});
 mount(dialog());
 const check=await screen.findByLabelText('Check');
 await waitFor(()=>assert.equal(check.options.length,5));
 fireEvent.click(screen.getByLabelText('Allow tsconfig.json'));
 fireEvent.change(check,{target:{value:'typescript-check'}});
 assert.match(screen.getByTestId('check-detail').textContent,/TS2307/,'the detail says external dependencies fail visibly');
 const testFile=screen.getByLabelText('Test file');
 assert.deepEqual([...testFile.options].map(o=>o.value).filter(Boolean),['tsconfig.json'],'a TypeScript check offers only the root tsconfig.json');
 fireEvent.change(testFile,{target:{value:'tsconfig.json'}});
 fireEvent.click(screen.getByRole('button',{name:/^(Allow coding|Save file access)$/}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.deepEqual(grants(calls)[0],{projectId:'p',channelId:'c',files:['tsconfig.json'],testFile:'tsconfig.json',checkProfile:'typescript-check',checkTimeoutSeconds:5});
});

test('a reopened dialog shows the persisted check from the host record, never a default',async()=>{
 host((op)=>{if(op==='coding_permission')return permission('node-test','test_sample.mjs');throw new Error(op);});
 mount(dialog());
 const check=await screen.findByLabelText('Check');
 await waitFor(()=>assert.equal(check.value,'node-test'));
 assert.match(screen.getByTestId('coding-permission-state').textContent,/tests run test_sample.mjs \(node-test\)/);
 assert.equal(screen.getByLabelText('Test file').value,'test_sample.mjs');
});

const nodeArgv=['/usr/local/bin/node','--no-addons','--no-experimental-fetch','test_sample.mjs'];
const completedTask=(overrides={})=>({id:'t',state:'completed',origin:'conversation',session_id:'s',scope_version:1,files:['greeting.mjs','test_sample.mjs'],argv:nodeArgv,check_profile:'node-test',binding,messages:[],tokens:[],runs:[{prior_attempts:0,id:'r',state:'completed'}],acceptance:{required:false,source:'default'},completion:{at:1,attempt:1,run_id:'r',review_phase:null,changed_files:['greeting.mjs'],test_exit_status:0,check_profile:'node-test',human_approved:false},...overrides});
const attempt=(test)=>({number:1,state:'verified',diff:{'greeting.mjs':'+x'},before:{},after:{},argv:nodeArgv,blocker:null,test});
const inspect=(task,attempts)=>{host((op)=>{if(op==='task_detail')return {task,evidence:{attempts}};throw new Error(op);});mount(React.createElement(TaskDialog,{task,projectId:'p',channelId:'c',refresh:async()=>{},open:true,onOpenChange(){}}));};

test('the label copy says which check ran and that exit 0 is process success, never correctness',()=>{
 assert.equal(checkProfileLabel('node-test'),'Node test (node-test)');
 assert.equal(checkProfileLabel('python-unittest'),'Python unittest (python-unittest)');
 assert.equal(checkProfileLabel('typescript-check'),'TypeScript check (typescript-check)');
 assert.equal(checkProfileLabel(undefined),'Not recorded (older record; the argv shows what ran)');
 assert.equal(checkProfileLabel('some-future-profile'),'some-future-profile','an unknown id is shown as recorded, not relabelled');
 assert.equal(checkExitDetail(0,null),'passed');
 assert.equal(checkExitDetail(1,null),'failed (exit 1)');
 assert.match(checkExitDetail(0,'no_tests_reported'),/ran no tests, so it doesn’t count as passing/);
 assert.equal(checkExitDetail(-9,'timeout'),'stopped: timeout');
});

test('the inspector shows the task and completion check profile for a Node task, and the attempt line reads exit 0 as process success',async()=>{
 inspect(completedTask(),[attempt({argv:nodeArgv,stdout:'TAP version 13\n# tests 1\n# pass 1\n',stderr:'',exit_status:0,stopped:null,check_profile:'node-test'})]);
 fireEvent.click(screen.getByRole('tab',{name:'Scope and activity'}));
 assert.equal(screen.getByTestId('task-check-profile').textContent,'Node test','the check in words, never its raw id');
 assert.equal(screen.getByTestId('completion-check').textContent,'Node test, passed.');
 fireEvent.click(screen.getByRole('tab',{name:'Changes and checks'}));
 await waitFor(()=>assert.match(screen.getByRole('heading',{level:3}).textContent,/Attempt 1 · check passed/));
 assert.match(document.body.textContent,/Passing tests don’t prove it works/);
});

test('a legacy record without a profile is shown as not recorded, never guessed, and a zero-test Node run is named on the attempt',async()=>{
 const {check_profile:_task,...legacyTask}=completedTask();
 const {check_profile:_done,...legacyCompletion}=legacyTask.completion;
 inspect({...legacyTask,completion:legacyCompletion},[attempt({argv:nodeArgv,stdout:'',stderr:'',exit_status:0,stopped:'no_tests_reported'})]);
 fireEvent.click(screen.getByRole('tab',{name:'Scope and activity'}));
 assert.equal(screen.getByTestId('task-check-profile').textContent,'Not recorded (older record; the argv shows what ran)');
 assert.match(screen.getByTestId('completion-check').textContent,/^Not recorded \(older record; the argv shows what ran\), passed/);
 fireEvent.click(screen.getByRole('tab',{name:'Changes and checks'}));
 await waitFor(()=>assert.match(screen.getByRole('heading',{level:3}).textContent,/check ran no tests, so it doesn’t count as passing/));
});


test('Rust names and completed checks never claim executed Rust tests',()=>{
 assert.ok(CHECK_TEST_NAMES['rust-check'].test('launch.rs'));
 for(const name of ['src/launch.rs','../check.rs','-check.rs','check.rs --test'])assert.ok(!CHECK_TEST_NAMES['rust-check'].test(name));
 assert.equal(checkProfileLabel('rust-check'),'Rust type check (rust-check)');
 inspect(completedTask({check_profile:'rust-check',completion:{at:1,attempt:1,run_id:'r',review_phase:null,changed_files:[],test_exit_status:0,check_profile:'rust-check',human_approved:false}}),[]);
 assert.match(document.body.textContent,/Host type check exit 0/);
 assert.doesNotMatch(document.body.textContent,/Host tests exit/);
});

test('Rust dropdown grants the selected root source file',async()=>{
 const calls=host(op=>{if(op==='coding_permission')return {...permission('python-unittest',null),candidates:['launch.rs'],test_candidates:['launch.rs']};if(op==='coding_permission_grant')return permission('rust-check','launch.rs');throw new Error(op);});
 mount(dialog());
 const check=await screen.findByLabelText('Check');
 await waitFor(()=>assert.equal(check.options.length,5));
 fireEvent.change(check,{target:{value:'rust-check'}});
 const testFile=screen.getByLabelText('Test file');
 assert.deepEqual([...testFile.options].map(o=>o.value).filter(Boolean),['launch.rs']);
 fireEvent.change(testFile,{target:{value:'launch.rs'}});
 fireEvent.click(screen.getByRole('button',{name:/^(Allow coding|Save file access)$/}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.equal(grants(calls)[0].checkProfile,'rust-check');
 assert.equal(grants(calls)[0].testFile,'launch.rs');
});


test('Rust setup shows the saved compiler, preserves a rejected edit and sends selection only for Rust',async()=>{
 const saved={...permission('rust-check','check.rs'),files:['check.rs'],rust_toolchain:'/trusted/rust'};
 const calls=host((op)=>{if(op==='coding_permission')return saved;if(op==='coding_permission_grant')throw new Error('Rust toolchain is missing');throw new Error(op);});
 mount(dialog());
 const input=await screen.findByLabelText('Rust toolchain folder');
 assert.equal(input.value,'/trusted/rust');
 fireEvent.change(input,{target:{value:'/another/rust'}});
 fireEvent.click(screen.getByRole('button',{name:'Save file access'}));
 await screen.findByText(/Rust toolchain is missing/);
 assert.equal(input.value,'/another/rust');
 assert.equal(grants(calls).at(-1).rustToolchain,'/another/rust');
 assert.ok(!calls.some(c=>c.operation==='task_create'||c.operation==='coding_request'));
 fireEvent.change(screen.getByLabelText('Check'),{target:{value:'python-unittest'}});
 assert.equal(screen.queryByLabelText('Rust toolchain folder'),null);
 assert.equal(grantPayload(['test_ok.py'],'test_ok.py',[],'python-unittest','/another/rust').rustToolchain,undefined);
});


test('fresh Dervo root selects its Node test instead of a Python check with no files',async()=>{
 const data={...permission('python-unittest',null),candidates:['Cargo.toml','build.rs','titlebar.test.mjs'],test_candidates:['build.rs','titlebar.test.mjs']};
 const calls=host(op=>{if(op==='coding_permission')return data;throw new Error(op);});
 mount(dialog());
 await waitFor(()=>assert.equal(screen.getByLabelText('Check').value,'node-test'));
 assert.equal(screen.getByLabelText('Test file').value,'titlebar.test.mjs');
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,false);
 assert.equal(screen.getByLabelText('Allow Cargo.toml').checked,false,'discovery does not select unrelated files');
 assert.equal(grants(calls).length,0,'discovery never grants access');
 fireEvent.change(screen.getByLabelText('Check'),{target:{value:'python-unittest'}});
 assert.equal(screen.getByLabelText('Test file').value,'');
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,true);
 assert.match(screen.getByTestId('coding-setup-next').textContent,/No matching test file/);
 fireEvent.change(screen.getByLabelText('Check'),{target:{value:'node-test'}});
 assert.equal(screen.getByLabelText('Test file').value,'titlebar.test.mjs','returning to a check selects its sole file');
});

test('multiple matching tests require an explicit choice and explain the disabled button',async()=>{
 const data={...permission('python-unittest',null),candidates:['a.test.mjs','b.test.mjs'],test_candidates:['a.test.mjs','b.test.mjs']};
 host(op=>{if(op==='coding_permission')return data;throw new Error(op);});mount(dialog());
 await waitFor(()=>assert.equal(screen.getByLabelText('Check').value,'node-test'));
 assert.equal(screen.getByLabelText('Test file').value,'');
 assert.match(screen.getByTestId('coding-setup-next').textContent,/Choose a test file/);
 fireEvent.change(screen.getByLabelText('Test file'),{target:{value:'b.test.mjs'}});
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,false);
 fireEvent.change(screen.getByLabelText('Maximum check time in seconds'),{target:{value:'0'}});
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,true);
 assert.match(screen.getByTestId('coding-setup-next').textContent,/5 to 120/);
});

test('saved missing check file never switches to a different discovered check',async()=>{
 const data={...permission('python-unittest','test_old.py'),state:'stale',files:['test_old.py'],candidates:['titlebar.test.mjs'],test_candidates:['titlebar.test.mjs']};
 host(op=>{if(op==='coding_permission')return data;throw new Error(op);});mount(dialog());
 await waitFor(()=>assert.equal(screen.getByLabelText('Test file').value,'test_old.py'));
 assert.equal(screen.getByLabelText('Check').value,'python-unittest');
});


test('a missing legacy catalogue keeps the displayed Python fallback and a failed load offers retry',async()=>{
 const data={...permission('python-unittest',null),check_profiles:undefined,candidates:['titlebar.test.mjs'],test_candidates:['titlebar.test.mjs']};
 host(op=>{if(op==='coding_permission')return data;throw new Error(op);});mount(dialog());
 await screen.findByLabelText('Allow titlebar.test.mjs');
 assert.equal(screen.getByLabelText('Check').value,'python-unittest');
 assert.equal(screen.getByLabelText('Test file').value,'');
 cleanup();
 host(()=>{throw new Error('Host disconnected');});mount(dialog());
 await screen.findByRole('button',{name:'Try again'});
 assert.match(screen.getByTestId('coding-setup-next').textContent,/couldn’t be loaded/);
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,true);
});


test('project access defaults to the folder and sends only the check seed after explicit save',async()=>{
 const saved={...permission('node-test',null),project_access_available:true,candidates:['titlebar.test.mjs','main.ts'],test_candidates:['titlebar.test.mjs']};
 const calls=host(op=>{if(op==='coding_permission'||op==='coding_permission_grant')return saved;throw new Error(op);});
 mount(dialog());
 await waitFor(()=>assert.match(screen.getByTestId('project-folder-access').textContent,/This project folder/));
 assert.equal(screen.queryByLabelText('Allow main.ts'),null);
 assert.equal(grants(calls).length,0);
 fireEvent.click(screen.getByRole('button',{name:'Allow coding',exact:true}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.deepEqual(grants(calls)[0],{projectId:'p',channelId:'c',files:['titlebar.test.mjs'],testFile:'titlebar.test.mjs',projectAccess:true,checkProfile:'node-test',checkTimeoutSeconds:5});
});

test('a saved restricted grant stays restricted until project access is explicitly saved',async()=>{
 const saved={...permission('node-test','test_sample.mjs'),project_access_available:true,project_access:false};
 const calls=host(op=>{if(op==='coding_permission'||op==='coding_permission_grant')return saved;throw new Error(op);});
 mount(dialog());
 await waitFor(()=>assert.match(screen.getByTestId('project-folder-access').textContent,/Restricted access/));
 assert.ok(screen.getByLabelText('Allow greeting.py'));
 fireEvent.click(screen.getByRole('button',{name:'Use project folder'}));
 assert.equal(grants(calls).length,0);
 assert.equal(screen.queryByLabelText('Allow greeting.py'),null);
 fireEvent.click(screen.getByRole('button',{name:'Save project access'}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.equal(grants(calls)[0].projectAccess,true);
 assert.deepEqual(grants(calls)[0].files,['test_sample.mjs']);
});


test('an empty project can explicitly save manual review and revoke it without selecting a test file',async()=>{
 let saved={...permission('python-unittest',null),candidates:[],test_candidates:[],project_access_available:true,check_profiles:[...CATALOGUE,{id:'manual-review',label:'Review changes myself',available:true,test_pattern:'No test file needed',detail:'No automated checks run.'}]};
 const calls=host((op,args)=>{
  if(op==='coding_permission')return saved;
  if(op==='coding_permission_grant'){saved={...saved,state:'granted',created_at:2,check_profile:args.checkProfile,project_access:true};return saved;}
  if(op==='coding_permission_revoke'){saved={...saved,state:'missing',created_at:null};return saved;}
  throw new Error(op);
 });
 mount(dialog());
 const choice=await screen.findByRole('button',{name:/Review changes myself/});
 await waitFor(()=>assert.equal(choice.getAttribute('aria-pressed'),'true'));
 assert.equal(screen.queryByLabelText('Test file'),null);
 assert.equal(screen.getByLabelText('Require your approval of each result').disabled,true);
 assert.equal(screen.getByLabelText('Require your approval of each result').checked,true);
 fireEvent.click(screen.getByRole('button',{name:'Allow coding'}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.deepEqual(grants(calls)[0],{projectId:'p',channelId:'c',files:[],testFile:null,projectAccess:true,checkProfile:'manual-review',checkTimeoutSeconds:5});
 assert.ok(!calls.some(call=>call.operation==='result_approval_set'));
 fireEvent.click(await screen.findByRole('button',{name:'Stop allowing coding'}));
 assert.ok(!calls.some(call=>call.operation==='coding_permission_revoke'),'stopping asks first');
 fireEvent.click(screen.getByRole('button',{name:'Stop allowing'}));
 await waitFor(()=>assert.ok(calls.some(call=>call.operation==='coding_permission_revoke')));
});

test('manual approval needs an explicit recorded snapshot, never a missing or failed automated check',async()=>{
 const {canApproveAttempt}=await import('./CodingTasks.tsx');
 const manual={...attempt(null),state:'unverified',kind:'manual-review',argv:[]};
 assert.equal(canApproveAttempt({check_profile:'manual-review'},manual),true);
 for(const changed of [{state:'prepared'},{kind:'verification'},{test:{exit_status:1}},{argv:['node']}])assert.equal(canApproveAttempt({check_profile:'manual-review'},{...manual,...changed}),false);
 assert.equal(canApproveAttempt({check_profile:'node-test'},manual),false);
 const task=completedTask({state:'ready_for_review',check_profile:'manual-review',argv:[],completion:undefined,acceptance:{required:true,source:'manual-review'}});
 inspect(task,[manual]);
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'Approve'}).disabled,false));
 assert.match(screen.getByTestId('task-check-summary').textContent,/No automated checks ran/);
 const {checksClause,decisionLine}=await import('./CodingTasks.tsx');
 assert.equal(checksClause(task),'No checks ran in Dervo.');
 assert.equal(decisionLine(task).lead,'Ready for review.');
});

test('coding access: whole folder or selected files is a visible choice, approving results sits outside Advanced, and the embedded form has one scroll',async()=>{
 host(op=>{if(op==='coding_permission')return {...permission('python-unittest','test_greeting.py'),project_access:true,project_access_available:true,candidates:['greeting.py','test_greeting.py'],acceptance:{required:true,source:'explicit',detail:'On.'},owner_effort:null};throw new Error(op);});
 mount(React.createElement(CodingPermissionDialog,{embedded:true,open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{}}));
 const choice=await screen.findByRole('group',{name:'How much of the project'});
 const [whole,selected]=choice.querySelectorAll('button');
 await waitFor(()=>assert.equal(whole.getAttribute('aria-pressed'),'true'));
 assert.equal(screen.queryByLabelText('Allow greeting.py'),null,'whole folder needs no file picking');
 fireEvent.click(selected);
 assert.equal(selected.getAttribute('aria-pressed'),'true');
 assert.ok(screen.getByLabelText('Allow greeting.py'));
 const approval=screen.getByTestId('result-approval');
 assert.equal(approval.closest('details'),null,'approving results is not hidden under Advanced');
 assert.ok(screen.getByLabelText('Maximum check time in seconds').closest('details'),'the check time is an advanced setting');
 const form=screen.getByRole('region',{name:'Project access'});
 assert.equal(form.querySelector('.overflow-y-auto'),null,'no scroll area inside the settings page');
 assert.ok(form.querySelector('.dervo-save-bar'));
});

test('choosing a folder in the macOS panel adds its path inside the project, once, and refuses anything else',async()=>{
 const {folderInside}=await import('./CodingPermission.tsx');
 assert.equal(folderInside('/approved','/approved/src'),'src');
 assert.equal(folderInside('/approved/','/approved/src/lib/'),'src/lib');
 for(const outside of ['/approved','/approved/','/approvedother/src','/elsewhere/src','/approved/../etc','/approved/./src'])assert.equal(folderInside('/approved',outside),null,outside);
 const answers=['/approved/src','/approved/src','/elsewhere',null];
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  if(command==='choose_folder_in'){calls.push(args);return answers.shift();}
  if(args?.operation==='coding_permission')return {...permission('python-unittest',null),state:'missing',candidates:['greeting.py','test_greeting.py'],project_access:false,project_access_available:false,acceptance:{required:true,source:'explicit',detail:'On.'},owner_effort:null};
  throw new Error(args?.operation??command);
 }};
 mount(React.createElement(CodingPermissionDialog,{embedded:true,open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{}}));
 const choose=await screen.findByRole('button',{name:'Choose a folder…'});
 await waitFor(()=>assert.equal(choose.disabled,false));
 const field=screen.getByLabelText('Granted folders');
 fireEvent.click(choose);await waitFor(()=>assert.equal(field.value,'src'));
 fireEvent.click(choose);await waitFor(()=>assert.equal(calls.length,2));
 assert.equal(field.value,'src','the same folder is added once');
 fireEvent.click(choose);
 assert.match((await screen.findByRole('alert')).textContent,/inside this project/);
 assert.equal(field.value,'src');
 fireEvent.click(choose);await waitFor(()=>assert.equal(calls.length,4));
 assert.equal(field.value,'src','a cancelled panel changes nothing');
 assert.deepEqual(calls[0],{root:'/approved'});
});
