import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {GuideActions,NotPushedLine,notPushed,commitLine}=await import('./GuideActions.tsx');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
function host(handler=()=>({})){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const base={id:'t1',origin:'conversation',state:'ready_for_review',files:['a.py'],messages:[{role:'user',text:'x',created_at:1}],tokens:[],runs:[{id:'r',state:'completed',prior_attempts:0}],binding:{provider:'codex',identity:'me',model:'m',cwd:'/p',visibility:'shared'},check_profile:'python-unittest',scope_version:1,argv:[],session_id:'s'};
const attempt={number:1,state:'verified',kind:'change',diff:{'a.py':'+x'},before:{},after:{},argv:[],test:{argv:[],stdout:'',stderr:'',exit_status:0,stopped:null}};
const detail=(extra={})=>({task:base,retry:{available:false},evidence:{attempts:[attempt]},...extra});
function mount(task,extra={},client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})){
 const events=[];
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(GuideActions,{task:{...base,...task},projectId:'p',channelId:'c',detail:detail(),onChanged:async()=>{events.push('changed');},onCorrection:kind=>events.push(['correction',kind]),onRunIt:()=>events.push('run'),...extra})));
 return {events,view};
}
const buttons=()=>screen.queryAllByRole('button').map(b=>b.textContent);

test('a result waiting for review offers Approve, Run it and Ask for changes, and approving is one click that commits',async()=>{
 const calls=host();
 const {events}=mount({completion:{changed_files:['a.py']}});
 assert.deepEqual(buttons(),['Run it','Ask for changes','Approve']);
 assert.match(screen.getByRole('button',{name:'Approve'}).title,/Commits exactly this task’s files\. The agent never runs git/);
 fireEvent.click(screen.getByRole('button',{name:'Approve'}));
 await waitFor(()=>assert.deepEqual(events,['changed']));
 assert.deepEqual(calls,[{operation:'task_review',args:{projectId:'p',channelId:'c',taskId:'t1',attemptNumber:1,decision:'approved'}}]);
 fireEvent.click(screen.getByRole('button',{name:'Run it'}));
 assert.equal(events.at(-1),'run');
 fireEvent.click(screen.getByRole('button',{name:'Ask for changes'}));
 await waitFor(()=>assert.deepEqual(events.at(-1),['correction','feedback']));
 assert.deepEqual(calls.at(-1),{operation:'task_review',args:{projectId:'p',channelId:'c',taskId:'t1',attemptNumber:1,decision:'rejected'}});
});

test('a result that carried on from a handoff lists the files from before it, and Approve and Commit send the list’s id',async()=>{
 const calls=host();
 const carried={id:'plan-1',from_task_id:'t0',here:'Claude',to:'Codex',files:['greeting.py','notes.md'],flagged:[{path:'todo.md',why:'changed after the handoff, and not by either run'}]};
 const {events}=mount({completion:{changed_files:['greeting.py']}},{detail:detail({carried,git:true})});
 const note=screen.getByTestId('carried-note');
 assert.equal(note.textContent,'Includes 2 files Claude changed before the handoff: greeting.py, notes.md.Left out: todo.md, changed after the handoff, and not by either run.');
 fireEvent.click(screen.getByRole('button',{name:'Approve and commit'}));
 await waitFor(()=>assert.deepEqual(events,['changed']));
 assert.deepEqual(calls,[{operation:'task_review',args:{projectId:'p',channelId:'c',taskId:'t1',attemptNumber:1,decision:'approved',carriedId:'plan-1'}}]);
 cleanup();
 const failed=host();
 mount({state:'idle',review:'approved',commit:{sha:null,error:'Git could not run.',at:1,how:'approved'}},{detail:detail({carried,git:true})});
 assert.ok(screen.getByTestId('carried-note'),'the list stays beside Try committing again');
 fireEvent.click(screen.getByRole('button',{name:'Try committing again'}));
 await waitFor(()=>assert.equal(failed.length,1));
 assert.deepEqual(failed[0],{operation:'task_commit',args:{projectId:'p',channelId:'c',taskId:'t1',carriedId:'plan-1'}});
});

test('#27: inside a git repository the approve button says it commits; outside one, or before the host says, it stays Approve',async()=>{
 const calls=host();
 mount({completion:{changed_files:['a.py']}},{detail:detail({git:true})});
 assert.deepEqual(buttons(),['Run it','Ask for changes','Approve and commit']);
 const button=screen.getByRole('button',{name:'Approve and commit'});
 assert.match(button.title,/Commits exactly this task’s files/,'the tooltip is unchanged');
 fireEvent.click(button);
 await waitFor(()=>assert.equal(calls.length,1));
 assert.deepEqual(calls[0],{operation:'task_review',args:{projectId:'p',channelId:'c',taskId:'t1',attemptNumber:1,decision:'approved'}},'same action as before, only the words changed');
 cleanup();
 // Negative controls: no repository, or an older host that doesn't send the field, never promises a commit.
 for(const extra of [{git:false},{}]){
  mount({completion:{changed_files:['a.py']}},{detail:detail(extra)});
  assert.deepEqual(buttons(),['Run it','Ask for changes','Approve'],JSON.stringify(extra));
  assert.equal(screen.queryByRole('button',{name:/commit/i}),null,JSON.stringify(extra));
  cleanup();
 }
});

test('nothing is offered while work runs, before the record loads approval, or for a helper; a rebound task says why',()=>{
 host(()=>{throw new Error('no calls');});
 for(const task of [{state:'working'},{state:'needs_permission'},{origin:'collaboration'},{state:'ready_for_review',collaboration:{review:{phase:'initial'}}}]){
  const {view}=mount(task);assert.equal(view.container.textContent,'',JSON.stringify(task));cleanup();
 }
 mount({},{detail:undefined});
 assert.equal(screen.queryByRole('button',{name:'Approve'}),null,'approval waits for the recorded attempt');
 cleanup();
 const {view}=mount({state:'completed',completion:{changed_files:['a.py']}},{detail:detail({rebound:true})});
 assert.match(view.container.textContent,/uses another account now/);
 assert.deepEqual(buttons(),['Run it']);
});

test('a completed result can be committed, then undone after a confirmation, and each outcome is one line',async()=>{
 let state={state:'completed',completion:{changed_files:['a.py']},commit:null};
 const calls=host(op=>op==='task_undo'?{}:{});
 const view=mount(state);
 assert.deepEqual(buttons(),['Run it','Ask for changes','Commit these changes']);
 fireEvent.click(screen.getByRole('button',{name:'Commit these changes'}));
 await waitFor(()=>assert.equal(calls.at(-1).operation,'task_commit'));
 fireEvent.click(screen.getByRole('button',{name:'Ask for changes'}));
 assert.deepEqual(view.events.at(-1),['correction','correction']);
 cleanup();
 const committed={state:'idle',completion:{changed_files:['a.py']},commit:{sha:'abcdef1234567',branch:'main',files:['a.py'],reverted:null}};
 const second=mount(committed);
 assert.deepEqual(buttons(),['Run it','Undo']);
 fireEvent.click(screen.getByRole('button',{name:'Undo'}));
 assert.equal(calls.filter(c=>c.operation==='task_undo').length,0,'undo asks first');
 assert.match(screen.getByRole('group',{name:'Undo this task'}).textContent,/before abcdef1\? Dervo refuses if later changes touch the same files\./);
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 assert.equal(screen.queryByRole('group',{name:'Undo this task'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Undo'}));
 fireEvent.click(screen.getByRole('group',{name:'Undo this task'}).querySelector('button'));
 await waitFor(()=>assert.deepEqual(calls.at(-1),{operation:'task_undo',args:{projectId:'p',channelId:'c',taskId:'t1'}}));
 await waitFor(()=>assert.equal(screen.queryByRole('group',{name:'Undo this task'}),null));
 assert.equal(second.events.at(-1),'changed');
});

test('commit lines cover undone, skipped and failed commits, and a failed commit can be tried again',async()=>{
 assert.equal(commitLine(null),null);
 assert.equal(commitLine({sha:'abcdef1234',reverted:{sha:'9876543210',at:1}}),'Undone in 9876543. This task’s files are back to how they were before it.');
 assert.equal(commitLine({sha:null,skipped:'Not a git repository. Nothing was committed.'}),'Not a git repository. Nothing was committed.');
 assert.equal(commitLine({sha:null,error:'Files changed since the check'}),'Nothing was committed: Files changed since the check');
 assert.equal(commitLine({sha:'abc1234def',branch:null}),'Committed abc1234.');
 // A commit that landed while git held index.lock says so in the same line (reviewer, 20 Sep); without the warning nothing is added.
 assert.equal(commitLine({sha:'abc1234def',branch:'main',index_warning:'Committed as abc1234, but git couldn’t refresh your index afterwards.'}),
  'Committed abc1234 on main. Committed as abc1234, but git couldn’t refresh your index afterwards.');
 assert.equal(commitLine({sha:'abc1234def',branch:'main',index_warning:''}),'Committed abc1234 on main.');
 const calls=host(()=>{throw new Error('Files changed after this result was completed. Nothing was committed.');});
 mount({state:'idle',commit:{sha:null,error:'git could not run'}});
 fireEvent.click(screen.getByRole('button',{name:'Try committing again'}));
 assert.match((await screen.findByRole('alert')).textContent,/Nothing was committed/);
 assert.equal(calls[0].operation,'task_commit');
 cleanup();
 mount({state:'idle',commit:{sha:'abc1234def',files:['a.py'],reverted:{sha:'fff0000aaa',at:2}}});
 assert.equal(screen.queryByRole('button',{name:'Undo'}),null,'an undone task can’t be undone twice');
});

test('retry is offered only when the host allows it and no provider suggestion covers it',async()=>{
 const calls=host();
 const {events}=mount({state:'blocked',runs:[{id:'r',state:'failed'}]},{detail:detail({retry:{available:true},evidence:{attempts:[]}})});
 assert.deepEqual(buttons(),['Retry']);
 fireEvent.click(screen.getByRole('button',{name:'Retry'}));
 await waitFor(()=>assert.deepEqual(events,['changed']));
 assert.deepEqual(calls,[{operation:'task_continue',args:{projectId:'p',channelId:'c',taskId:'t1',text:'Please try that again.'}}]);
 cleanup();
 const {view}=mount({state:'blocked',runs:[{id:'r',state:'failed'}],suggest:{reason:'Codex was overloaded.',other:null}},{detail:detail({retry:{available:true},evidence:{attempts:[]}})});
 assert.equal(screen.queryByRole('button',{name:'Retry'}),null,'the handoff row owns the retry');
 assert.equal(view.container.textContent,'');
});

test('after your own Pause, Retry reads Resume and carries on in the same task; without a retry it sends a message for you (16.4)',async()=>{
 const paused={state:'blocked',error:'Stopped before completion',runs:[{id:'r',state:'interrupted',prior_attempts:0}]};
 let calls=host();
 let {events}=mount(paused,{detail:detail({retry:{available:true},evidence:{attempts:[]}}),onResume:()=>{events.push('resume');}});
 assert.deepEqual(buttons(),['Resume'],'one button, not Retry and Resume side by side');
 fireEvent.click(screen.getByRole('button',{name:'Resume'}));
 await waitFor(()=>assert.deepEqual(calls,[{operation:'task_continue',args:{projectId:'p',channelId:'c',taskId:'t1',text:'Carry on from where you paused.'}}]));
 assert.ok(!events.includes('resume'));
 cleanup();
 calls=host();
 const sent=[];
 ({events}=mount(paused,{onResume:()=>{sent.push('resume');return undefined;}}));
 fireEvent.click(screen.getByRole('button',{name:'Resume'}));
 assert.deepEqual(sent,['resume']);
 assert.deepEqual(calls,[],'the message goes through the composer, not a host call from here');
 cleanup();
 host();
 mount(paused,{onResume:()=>'Your draft is still in the composer. Send it, or clear it and press Resume.'});
 fireEvent.click(screen.getByRole('button',{name:'Resume'}));
 assert.match(screen.getByRole('alert').textContent,/Your draft is still in the composer/);
 cleanup();
 host();
 mount(paused);
 assert.equal(screen.queryByRole('button',{name:'Resume'}),null,'an older request has no Resume');
 cleanup();
 host();
 mount({state:'blocked',runs:[{id:'r',state:'failed'}]},{detail:detail({retry:{available:true},evidence:{attempts:[]}}),onResume:()=>undefined});
 assert.deepEqual(buttons(),['Retry'],'a failure keeps Retry');
});

test('a run Dervo paused when it quit offers Resume beside Leave it stopped',()=>{
 host();
 mount({state:'disconnected',error:'Dervo quit mid-run and paused it before it could say how far it got. Check your folder, then send a message to carry on.',runs:[{id:'r',state:'uncertain',prior_attempts:0}]},{onResume:()=>undefined});
 assert.deepEqual(buttons(),['Resume','Leave it stopped']);
 assert.match(document.body.textContent,/Its last step may not have finished/);
 assert.doesNotMatch(document.body.textContent,/lost track/);
});

test('the shell opens a guide task in the panel’s record, and the panel holds the record itself',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/if\(task\.id===guideTask\?\.id&&!tasks\.error&&!activity\.error\)\{if\(!guideVisible\)toggleGuide\(\);setGuideRecordRequest\(value=>value\+1\);return;\}/);
 assert.match(shell,/onInspect=\{trigger=>inspectTask\(task,trigger\)\}/);
 assert.match(shell,/recordRequest=\{guideRecordRequest\}/);
 assert.match(shell,/queryKey:\['task-detail',projectId,channelId,guideTask\?\.id\?\?''\]/);
 const guide=readFileSync(new URL('./TaskGuide.tsx',import.meta.url),'utf8');
 assert.match(guide,/<TaskRecord task=\{task\} projectId=\{projectId\} channelId=\{channelId\}/);
 assert.equal(guide.includes('onInspect(event.currentTarget)'),false,'no button in the panel opens the separate window');
});

test('a run Dervo lost track of can be marked stopped, and says it claims nothing about your files (18 Sep)',async()=>{
 const calls=host();
 const {events,view}=mount({state:'disconnected',runs:[{id:'r',state:'uncertain',prior_attempts:0}]});
 assert.ok(buttons().includes('Leave it stopped'));
 assert.match(view.container.textContent,/Dervo lost track of this run/);
 assert.match(view.container.textContent,/Your files stay exactly as they are/);
 fireEvent.click(screen.getByRole('button',{name:'Leave it stopped'}));
 await waitFor(()=>assert.deepEqual(calls,[{operation:'task_settle',args:{projectId:'p',channelId:'c',taskId:'t1'}}]));
 await waitFor(()=>assert.deepEqual(events,['changed']));
 cleanup();
 // Negative: a settled task offers nothing of the sort, and neither does one still running.
 mount({state:'blocked',runs:[{id:'r',state:'failed',prior_attempts:0}]});
 assert.equal(buttons().includes('Leave it stopped'),false);
 cleanup();
 mount({state:'working',runs:[{id:'r',state:'uncertain',prior_attempts:0}]});
 assert.equal(buttons().includes('Leave it stopped'),false);
});

test('returning Home after settlement reads current lost runs even while its previous status was fresh',async()=>{
 for(const refused of [false,true]){
  const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity,staleTime:60000}}});
  const lost={lostRuns:[{taskId:'t1',projectId:'p',channelId:'c',name:'Lost task'}]};
  client.setQueryData(['local-status'],lost);
  let finish;
  host(()=>new Promise((resolve,reject)=>{finish=()=>refused?reject(new Error('Still running')):resolve({});}));
  const {events}=mount({state:'disconnected',runs:[{id:'r',state:'uncertain'}]}, {}, client);
  try{
   fireEvent.click(screen.getByRole('button',{name:'Leave it stopped'}));
   assert.deepEqual(await client.fetchQuery({queryKey:['local-status'],queryFn:async()=>({lostRuns:[]})}),lost,'pending settlement keeps the recovery notice');
   await act(async()=>finish());
   if(refused)await screen.findByRole('alert');
   else await waitFor(()=>assert.deepEqual(events,['changed']));
   const returningHome=await client.fetchQuery({queryKey:['local-status'],queryFn:async()=>({lostRuns:[]})});
   assert.deepEqual(returningHome,refused?lost:{lostRuns:[]});
  }finally{cleanup();client.clear();}
 }
});

test('a commit that is not on GitHub yet says so, with a link to Home, and the line goes once it is pushed',async()=>{
 const sha='614cc31aabbccddeeff00112233445566778899';
 const preview=shas=>({branch:'mvp1',upstream:'origin/mvp1',commits:shas.map(s=>({sha:s,subject:'Dervo: x'}))});
 assert.equal(notPushed({sha,branch:'mvp1'},preview([sha,'9f0e1d2'])),true);
 // Negative controls: pushed (not in the list), undone, no commit, and a host that couldn't say.
 assert.equal(notPushed({sha,branch:'mvp1'},preview(['9f0e1d2'])),false);
 assert.equal(notPushed({sha,reverted:{sha:'fff0000aaa',at:2}},preview([sha])),false);
 assert.equal(notPushed({sha:null,skipped:'Not a git repository.'},preview([sha])),false);
 assert.equal(notPushed({sha},undefined),false);
 let commits=[sha];
 const calls=host(operation=>{if(operation!=='push_preview')throw new Error('unexpected '+operation);return preview(commits);});
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const opened=[];
 const task={...base,state:'idle',review:'approved',commit:{sha,branch:'mvp1',files:['a.py']}};
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(NotPushedLine,{task,projectId:'p',onOpenHome:()=>opened.push('home')})));
 await screen.findByText('Not pushed yet.');
 assert.deepEqual(calls,[{operation:'push_preview',args:{projectId:'p'}}]);
 fireEvent.click(screen.getByRole('button',{name:'Open Home to push this commit'}));
 assert.deepEqual(opened,['home']);
 // Pushed from Home: Home refreshes the same query, and the line is gone.
 commits=[];
 await act(async()=>{await client.invalidateQueries({queryKey:['push-preview','p']});});
 // waitFor never settles on a react-query update in this harness, so wait in act() steps instead.
 for(let n=0;n<50&&screen.queryByText('Not pushed yet.');n++)await act(()=>new Promise(resolve=>setTimeout(resolve,10)));
 assert.equal(screen.queryByText('Not pushed yet.'),null);
 view.unmount();
 // Negative controls: an undone commit never asks the host; a host without push ops says nothing.
 const before=calls.length;
 render(React.createElement(QueryClientProvider,{client},React.createElement(NotPushedLine,{task:{...task,commit:{...task.commit,reverted:{sha:'fff0000aaa',at:2}}},projectId:'p'})));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.length,before,'nothing to ask about');
 assert.equal(screen.queryByText('Not pushed yet.'),null);
 cleanup();
 host(()=>{throw new Error('Unsupported operation');});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(NotPushedLine,{task,projectId:'p'})));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(screen.queryByText('Not pushed yet.'),null);
});

test('beta.18: one step that may have reached outside the folder offers Continue, checking first and Leave it stopped',async()=>{
 const calls=host();
 const record={state:'unsure',provider:'claude',step:{tool:'Bash',text:'gh workflow run preview.yml',at:1759625160}};
 const {events}=mount({state:'disconnected',runs:[{id:'r',state:'uncertain',prior_attempts:0,after_close:{record}}]});
 assert.deepEqual(buttons(),['Continue, checking first','Leave it stopped']);
 assert.match(document.body.textContent,/Continue tells the agent that step’s outcome is unknown/);
 fireEvent.click(screen.getByRole('button',{name:'Continue, checking first'}));
 await waitFor(()=>assert.deepEqual(calls,[{operation:'task_settle',args:{projectId:'p',channelId:'c',taskId:'t1',checking:true}}]));
 await waitFor(()=>assert.deepEqual(events,['changed']));
 cleanup();
 // Without a named step it's today's card: Leave it stopped only.
 mount({state:'disconnected',runs:[{id:'r',state:'uncertain',prior_attempts:0,after_close:{record:{state:'unreadable'}}}]});
 assert.deepEqual(buttons(),['Leave it stopped']);
});

test('beta.18: a run Dervo paused when it restarted offers Continue, which resumes it in the same session',async()=>{
 const {RESTART_TEXT}=await import('./runEnding.ts');
 const calls=host();
 const paused={state:'blocked',error:'Paused when Dervo restarted.',ending:{kind:'stopped',by:'restart',confirmed:true},runs:[{id:'r',state:'interrupted',end_cause:'restart_paused',prior_attempts:0}]};
 mount(paused,{detail:detail({retry:{available:true},evidence:{attempts:[]}}),onResume:()=>undefined});
 assert.deepEqual(buttons(),['Continue']);
 fireEvent.click(screen.getByRole('button',{name:'Continue'}));
 await waitFor(()=>assert.deepEqual(calls.map(call=>[call.operation,call.args.text]),[['task_continue',RESTART_TEXT]]));
});

test('beta.18: finished work whose checks didn’t finish offers Run the checks again, sent as your message',async()=>{
 const {RECHECK_TEXT}=await import('./runEnding.ts');
 host();
 const sent=[];
 mount({state:'blocked',error:'Host verification did not finish. Run the configured checks again.',ending:{kind:'finished',snag:'checks'},runs:[{id:'r',state:'completed',prior_attempts:0}]},
  {onResume:text=>{sent.push(text);return undefined;}});
 assert.ok(buttons().includes('Run the checks again'));
 fireEvent.click(screen.getByRole('button',{name:'Run the checks again'}));
 assert.deepEqual(sent,[RECHECK_TEXT]);
 cleanup();
 mount({state:'blocked',ending:{kind:'finished',snag:'record'},runs:[{id:'r',state:'completed',prior_attempts:0}]},{onResume:()=>undefined});
 assert.equal(buttons().includes('Run the checks again'),false,'only when the checks are what didn’t finish');
});

test('beta.18: a commit from outside the run and files written outside the project are named under the card',()=>{
 host();
 mount({completion:{changed_files:['a.py']},moved_head:{branch:'mvp1',commits:[{sha:'1516d13b9f00aa',subject:'Worktrees explore: note and mocks'}]},
  outside:{count:1,files:['~/dervo/app-wt/b171-merge/evidence/b171-merge/merge.zsh']}});
 assert.match(document.body.textContent,/A commit landed on mvp1 during this run from outside it \(1516d13 “Worktrees explore: note and mocks”\), so Dervo recorded the run on top of it\./);
 assert.match(document.body.textContent,/merge\.zsh outside this project, not recorded/);
 cleanup();
 // Answered with nothing to approve: the notes still show.
 mount({state:'answered',outside:{count:3,files:['/tmp/a.txt']}});
 assert.match(document.body.textContent,/and 2 more outside this project/);
});
