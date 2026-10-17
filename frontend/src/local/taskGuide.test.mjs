import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
const React=await import('react');
const {render,fireEvent,cleanup,act,waitFor,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {TaskGuide,selectGuideTask,panelState,panelSteps,panelFiles}=await import('./TaskGuide.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const NOW=new Date(2026,8,19,1,52).getTime()/1000;
const at=(hour,minute)=>new Date(2026,8,19,hour,minute).getTime()/1000;
const binding={provider:'claude',identity:'bryce@trydervo.com',model:'claude-opus-5',cwd:'/Users/bryce/dervo/app',visibility:'shared'};
const tok=(tool,extra={})=>({run_id:'run',tool,state:'delivered',success:true,...extra});
const request={role:'user',text:'Read PLAN-orchestrator.md first. Start with step 1 only.',created_at:at(1,38)};
const report={role:'assistant',text:'### Changed\n- Ideas are stored per project.',created_at:at(1,52),run_id:'run'};
/** Ideas storage, finished and waiting for review: Claude Code ran its own commands, one of which returned an error. */
const finished={id:'t-ideas',state:'ready_for_review',origin:'conversation',full_access:true,check_profile:'manual-review',binding,files:[],
 messages:[request,report],runs:[{id:'run',state:'completed',started_at:at(1,38),ended_at:at(1,52)}],
 tokens:[tok('native:Read',{path:'PLAN-orchestrator.md'}),tok('native:Write',{path:'backend/project_ideas.py'}),
  tok('native:Bash',{prose:true,command:'Ran the backend tests',started_at:at(1,46),ended_at:at(1,46)+3}),
  tok('native:Bash',{prose:true,command:'Looked up the plan files in git',success:false,started_at:at(1,50),ended_at:at(1,50)+1})]};
const running={...finished,id:'t-board',state:'working',binding:{...binding,provider:'codex',model:'gpt-5.5'},check_profile:'node-test',messages:[request],
 runs:[{id:'run',state:'running',started_at:NOW-41*60,guide:{run_id:'run',goal:'A live warning',step:'Running the frontend tests',why:'w',next:'Write the report',test_steps:'',limits:''}}],
 tokens:[tok('dervo_write_file',{path:'backend/project_ideas.py'}),tok('dervo_write_file',{path:'frontend/src/local/ProjectRoom.tsx'}),tok('dervo_run_tests',{state:'running',started_at:NOW-3*60})]};
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
function mount(props,{name='Ideas storage',qc=client()}={}){
 qc.setQueryData(['local-conversations','p'],[{id:'c',name}]);
 const element=extra=>React.createElement(QueryClientProvider,{client:qc},React.createElement(TaskGuide,{projectId:'p',channelId:'c',refresh:async()=>{},onClose(){},...props,...extra}));
 const view=render(element({}));
 return {view,qc,rerender:extra=>view.rerender(element(extra))};
}

test('the status line is the run’s state with when it ended and how long it took, never the decision or the error',()=>{
 assert.deepEqual(panelState(finished,NOW),{glyph:'done',word:'Finished',running:false,caption:`${new Date(at(1,52)*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})} · 14 min`});
 assert.deepEqual(panelState(running,NOW),{glyph:'running',word:'Running',running:true,caption:'41 min'});
 assert.equal(panelState(running,running.runs[0].started_at+3).caption,'','3 s in: no "1 min" yet (reviewer, 8 Oct)');
 assert.doesNotMatch(panelState({...finished,runs:[{...finished.runs.at(-1),started_at:at(1,52)-2,ended_at:at(1,52)}]},NOW).caption,/min/,'a 2 s run carries no duration');
 const limit={...finished,state:'blocked',error:'Claude hit its usage limit.',runs:[{id:'run',state:'failed',started_at:at(1,43),ended_at:at(2,14),provider_failure:'usage_limit'}]};
 assert.equal(panelState(limit,NOW).word,'Stopped');assert.equal(panelState(limit,NOW).glyph,'failed');
 assert.doesNotMatch(JSON.stringify(panelState(limit,NOW)),/usage limit/,'the error has one home, the decision bar');
 assert.equal(panelState({...limit,runs:[{id:'run',state:'failed'}]},NOW).word,'Failed');
 assert.equal(panelState({...limit,runs:[{id:'run',state:'interrupted'}]},NOW).glyph,'stopped');
 assert.deepEqual([panelState({...finished,state:'disconnected',runs:[{id:'run',state:'uncertain'}]},NOW).glyph,panelState({...finished,state:'disconnected',runs:[{id:'run',state:'uncertain'}]},NOW).word],['unknown','Not confirmed']);  // beta.17.1: lost track isn't failed
 assert.equal(panelState(running,NOW,true).word,'Last saved record');
 assert.equal(panelState({...finished,state:'ready_for_review',collaboration:{review:{phase:'initial'}}},NOW).word,'Reviewing');
 // A handoff names the provider that took over.
 assert.match(panelState({...running,route:{switched_from:{provider:'claude'}}},NOW).caption,/^Codex · 41 min$/);
 // Beta.16 (b16-thread question frames): while its question waits in the tray, the panel says Needs you, not Finished.
 assert.deepEqual(panelState({...finished,state:'answered'},NOW,false,NOW-120),{glyph:'needs',word:'Needs you',running:false,caption:'Asked 2 min ago'});
 // Bryce, 26 Sep: it says Needs you while the agent carries on working too, like the strip, and Stop stays (working).
 assert.deepEqual(panelState(running,NOW,false,NOW-120),{glyph:'needs',word:'Needs you',running:false,caption:'Asked 2 min ago',working:true});
 assert.equal(panelState(running,NOW,false,null).word,'Running','no question, still Running');
 assert.equal(panelState(finished,NOW,false,null).word,'Finished');
});

test('a command that returned an error is a done step with a caption, never a red step; only the live step is now',()=>{
 const steps=panelSteps(finished,NOW);
 const errored=steps.find(step=>step.text==='Looked up the plan files in git');
 assert.equal(errored.glyph,'done');assert.match(errored.caption,/Returned an error/);
 assert.deepEqual(steps.filter(step=>step.glyph==='failed'),[],'a finished task has no failure step');
 assert.deepEqual(steps.at(-1).text,'Wrote the report');assert.match(steps.at(-1).caption,/1:52/);
 assert.match(steps.find(step=>step.text==='Ran the backend tests').caption,/1:46.* · 3 s$/,'checks and commands carry their time and duration');
 assert.equal(steps.find(step=>step.text==='Wrote').caption,undefined,'a file edit needs no caption');
 // Negative control: the old rule made an errored command a problem row. The failure now belongs to the run's end only.
 const failedRun={...finished,state:'blocked',error:'Stopped',runs:[{id:'run',state:'failed'}]};
 assert.deepEqual(panelSteps(failedRun,NOW).filter(step=>step.glyph==='failed'),[],'how the run ended is the status line, not a step');
 const live=panelSteps(running,NOW);
 assert.deepEqual(live.filter(step=>step.now).map(step=>[step.text,step.caption]),[['Running the Node check','Now · 3 min']]);
 assert.deepEqual(live.at(-1),{id:'next',glyph:'idle',text:'Write the report',files:[],caption:'Next',later:true});
 assert.equal(panelSteps({...running,state:'ready_for_review',runs:[{id:'run',state:'completed'}],tokens:running.tokens.slice(0,2)},NOW).some(step=>step.later),false,'nothing is next once it stopped working');
});

test('changed files carry line deltas from the recorded diff, and only names while the run is still writing',()=>{
 const diffs=[{path:'backend/project_ideas.py',added:252,removed:0,lines:[]},{path:'backend/test_project_ideas.py',added:14,removed:2,lines:[]}];
 const completed={...finished,state:'completed',completion:{run_id:'run',changed_files:['backend/project_ideas.py','backend/test_project_ideas.py'],test_exit_status:0,review_phase:null,at:at(1,52),attempt:1}};
 assert.deepEqual(panelFiles(completed,diffs).map(f=>[f.name,f.delta]),[['project_ideas.py','+252'],['test_project_ideas.py','+14 −2']]);
 const detail={task:finished,evidence:{attempts:[{number:1,kind:'manual-review',diff:{'backend/project_ideas.py':'+a\n+b\n-c'}}]}};
 assert.deepEqual(panelFiles(finished,undefined,detail).map(f=>[f.name,f.delta]),[['project_ideas.py','+2 −1']]);
 // A granted file the attempt left alone isn't listed as changed (polish review, 9 Oct).
 const untouched={...detail,evidence:{attempts:[{...detail.evidence.attempts[0],diff:{...detail.evidence.attempts[0].diff,'backend/README.md':''}}]}};
 assert.deepEqual(panelFiles(finished,undefined,untouched).map(f=>f.name),['project_ideas.py']);
 assert.deepEqual(panelFiles(finished,undefined,{...detail,task:{...finished,id:'other'}}).map(f=>f.name),['project_ideas.py'],'another task’s record lends nothing but the write calls stand');
 assert.deepEqual(panelFiles(running).map(f=>[f.name,f.delta]),[['project_ideas.py',undefined],['ProjectRoom.tsx',undefined]]);
});

test('a file the run deleted reads as Deleted, not as its removed line count (design review S7)',()=>{
 const gone={task:finished,evidence:{attempts:[{number:1,kind:'manual-review',deleted:{'backend/ideas_legacy.py':{sha256:'x'}},
  diff:{'backend/project_ideas.py':'+a\n-c','backend/ideas_legacy.py':'-1\n-2\n-3\n-4'}}]}};
 assert.deepEqual(panelFiles(finished,undefined,gone).map(f=>[f.name,f.delta]),[['project_ideas.py','+1 −1'],['ideas_legacy.py','Deleted']]);
 const completed={...finished,state:'completed',completion:{run_id:'run',changed_files:['backend/ideas_legacy.py'],test_exit_status:0,review_phase:null,at:at(1,52),attempt:1}};
 assert.deepEqual(panelFiles(completed,[{path:'backend/ideas_legacy.py',added:0,removed:4,lines:[],deleted:true}]).map(f=>f.delta),['Deleted']);
 assert.deepEqual(panelFiles(completed,[{path:'backend/ideas_legacy.py',added:0,removed:4,lines:[]}]).map(f=>f.delta),['−4'],'control: emptied, not deleted');
});

test('resumed older owner wins over a newer settled task and helper work',()=>{
 const task={...running,id:'owner',messages:[{role:'user',text:'Highlight search matches.',created_at:1}]};
 const newer={...task,id:'new',state:'completed',messages:[{role:'user',text:'Other request',created_at:5}]};
 assert.equal(selectGuideTask([task,newer,{...newer,id:'helper',origin:'collaboration',state:'working'}]).id,'owner');
 assert.equal(selectGuideTask([{...task,state:'completed',messages:[{role:'user',text:'Correction',created_at:8}]},newer]).id,'owner');
 assert.equal(selectGuideTask([{...task,origin:'collaboration'}]),undefined);
});

test('the panel is the kicker, the thread name, one status line, the folded steps, the files and a way to the record',async()=>{
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{if(operation==='task_detail')return {task:finished,retry:{available:false},evidence:{attempts:[]}};throw Error(operation);}};
 const many={...finished,tokens:[...Array.from({length:8},(_,i)=>tok('native:Edit',{path:`file${i}.py`})),...finished.tokens]};
 const {view}=mount({task:many});
 const guide=screen.getByTestId('task-guide');
 assert.equal(guide.querySelector('.dv-panel-kicker').textContent,'Progress');
 assert.equal(guide.querySelector('.dv-panel-title').textContent,'Ideas storage','the thread’s short name, from the conversations already loaded');
 assert.equal(screen.getByRole('status').textContent,'Finished');
 assert.doesNotMatch(guide.textContent,/Ready for review|No checks ran|Claude Code · /,'the decision and the eyebrow left the panel');
 assert.equal(guide.querySelectorAll('[aria-current="step"]').length,0);
 const steps=()=>[...screen.getByRole('list',{name:'Recorded steps'}).querySelectorAll('li')];
 assert.equal(steps().length,5,'the last five steps show');
 const fold=screen.getByRole('button',{name:/earlier steps/});
 fireEvent.click(fold);
 assert.ok(steps().length>5);assert.equal(screen.queryByRole('button',{name:/earlier steps/}),null);
 assert.equal(guide.querySelectorAll('.dv-step [data-state="failed"]').length,0,'the errored command is not red');
 assert.equal(view.container.querySelector('button[aria-label="Hide progress"]'),null,'the header owns Hide');
 assert.equal(screen.queryByRole('tab'),null,'no tabs');
 fireEvent.click(screen.getByRole('button',{name:'Open the record'}));
 assert.ok(screen.getByTestId('task-record'));assert.ok(screen.getByRole('button',{name:'Result'}));
 fireEvent.click(screen.getByRole('button',{name:'Back to progress'}));
 assert.equal(screen.queryByTestId('task-record'),null);
});

test('a request from outside opens the record, and a new task returns the panel to progress',async()=>{
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{if(operation==='task_detail')return {task:finished,retry:{available:false},evidence:{attempts:[]}};throw Error(operation);}};
 const {rerender}=mount({task:finished,recordRequest:0});
 assert.equal(screen.queryByTestId('task-record'),null);
 rerender({task:finished,recordRequest:1});
 assert.ok(screen.getByTestId('task-record'));
 rerender({task:{...finished,id:'another'},recordRequest:1});
 assert.equal(screen.queryByTestId('task-record'),null);
});

test('while it runs, Stop is scoped to this task, disabled while pending, and its failure stays in the panel',async()=>{
 const calls=[];let rejectStop;
 window.__TAURI_INTERNALS__={invoke:async(_cmd,args)=>{calls.push(args);return new Promise((_resolve,reject)=>rejectStop=reject);}};
 let refreshed=0;
 mount({task:running,refresh:async()=>{refreshed++;}},{name:'Live board'});
 const guide=screen.getByTestId('task-guide');
 assert.equal(guide.dataset.running,'true');
 assert.equal(screen.getByRole('status').textContent,'Running');
 assert.equal(guide.querySelectorAll('[aria-current="step"]').length,1);
 assert.match(guide.querySelector('[aria-current="step"]').textContent,/^Running the Node check/);
 assert.equal(guide.querySelectorAll('.dv-st[data-state="running"]').length,1,'the current step spins, nothing else');
 // app #14, round 2 (Bryce, 23 Sep): the status line's mark is the pebble thinking, beside the word, only while it runs.
 assert.equal(guide.querySelectorAll('.dv-panel-state [data-testid="working-mark"][data-working]').length,1);
 assert.equal(guide.querySelector('.dv-panel-state [data-testid="working-mark"]').getAttribute('aria-hidden'),'true','the word says Running; the mark is decoration');
 assert.equal(guide.querySelector('[data-testid="running-orb"]'),null,'the green orb is gone');
 assert.equal(screen.getByRole('heading',{level:3,name:'Files so far'}).tagName,'H3');
 fireEvent.click(screen.getByRole('button',{name:'Pause'}));
 assert.equal(screen.getByRole('button',{name:'Pausing…'}).disabled,true);
 assert.deepEqual(calls,[{operation:'task_stop',args:{projectId:'p',channelId:'c',taskId:'t-board'}}]);
 await act(async()=>rejectStop(Error('Stop not confirmed')));
 assert.match(screen.getByRole('alert').textContent,/Stop not confirmed/);assert.equal(refreshed,0);
});

test('while a question waits and the agent keeps working, the panel says Needs you and Stop stays',()=>{
 mount({task:running,askedAt:NOW-120},{name:'Live board'});
 const guide=screen.getByTestId('task-guide');
 assert.equal(screen.getByRole('status').textContent,'Needs you');
 assert.equal(guide.querySelector('.dv-panel-state [data-testid="working-mark"]'),null,'the working mark stops while it waits');
 assert.ok(screen.getByRole('button',{name:'Pause'}),'the run can still be stopped');
 assert.ok(screen.getByRole('heading',{level:3,name:'Files so far'}));
});

test('a stopped task’s error is not in the panel, and updates unavailable is said once in the status line',()=>{
 const limit={...finished,state:'blocked',error:'Claude hit its usage limit.',runs:[{id:'run',state:'failed',provider_failure:'usage_limit'}]};
 mount({task:limit});
 const guide=screen.getByTestId('task-guide');
 assert.equal(screen.getByRole('status').textContent,'Stopped');
 assert.doesNotMatch(guide.textContent,/usage limit|stopped before finishing/);
 cleanup();
 mount({task:running,unavailable:true},{name:'Live board'});
 assert.equal(screen.getByRole('status').textContent,'Last saved record');
 assert.equal(screen.queryByRole('button',{name:'Pause'}),null);
});

test('the handoff offer goes back to its slot under the report when the decision bar leaves one, else stays in the panel',async()=>{
 const slot=document.createElement('div');slot.setAttribute('data-dervo-handoff-slot','t-ideas');document.body.appendChild(slot);
 const offer=React.createElement('p',{'data-testid':'offer'},'Continue in Codex');
 try{
  mount({task:finished,suggestion:offer});
  await waitFor(()=>assert.equal(slot.querySelector('[data-testid="offer"]')?.textContent,'Continue in Codex'));
  assert.equal(screen.getByTestId('task-guide').querySelector('[data-testid="offer"]'),null);
 }finally{cleanup();slot.remove();}
 mount({task:finished,suggestion:offer});
 await waitFor(()=>assert.ok(screen.getByTestId('task-guide').querySelector('[data-testid="offer"]')));
});

test('What it was told opens from the Progress panel, reads the run’s own record, and links back to Knowledge',async()=>{
 const run={...finished.runs[0],knowledge_snapshot:{captured_at:new Date(at(1,38)*1000).toISOString(),text:'RULES',omitted:0,profile_id:'me',
  rules:[{rule_id:'r1',version_id:'v1',text:'Run backend tests with the framework Python 3.13.'}],
  repository:{files:[{path:'AGENTS.md',sha256:'a',bytes:4403,sent_bytes:4403,cut:false,modified:at(1,0)}],left_out:[],text:'REPO',limit:32768}},
  told:{notes:[{id:'n',text:'Bryce wants plain words.'}],skills:[]}};
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='task_detail')return {task:{...finished,runs:[run]}};
  if(operation==='knowledge_list')return {rules:[{id:'r1',revision:2,active_version_id:'v1',versions:[{id:'v1',text:'Run backend tests with the framework Python 3.13.'}],events:[]}]};
  if(operation==='knowledge_repo_list')return {files:[{path:'AGENTS.md',sha256:'a'}],missing:[],looked_for:[],limit:32768};
  throw Error(operation);}};
 const opened=[];
 mount({task:finished,onOpenKnowledge:()=>opened.push(true)});
 const row=screen.getByRole('button',{name:'What it was told'});
 assert.ok(row.compareDocumentPosition(screen.getByRole('button',{name:'Open the record'}))&Node.DOCUMENT_POSITION_FOLLOWING,'it sits above Open the record');
 fireEvent.click(row);
 assert.equal(document.querySelector('.dv-panel-kicker').textContent,'What it was told');
 await screen.findByText('Bryce wants plain words.');
 await waitFor(()=>assert.match(document.querySelector('.dv-told').textContent,/AGENTS\.md.*the whole file · still current/));
 const view=document.querySelector('.dv-told').textContent;
 assert.match(view,/before Claude Code started/);
 assert.match(view,/Your rules · 1 of 1.*1Run backend tests with the framework Python 3\.13\./);
 assert.deepEqual(calls.find(([op])=>op==='knowledge_list')[1],{projectId:'p',channelId:'',profileId:'me'},'Knowledge as it is now, for the version notes');
 fireEvent.click(screen.getByRole('button',{name:'Open Knowledge'}));
 assert.equal(opened.length,1);
 fireEvent.click(screen.getByRole('button',{name:'Back to progress'}));
 assert.ok(screen.getByRole('button',{name:'Open the record'}));
});

// Since beta.17.1 (#41, A) the panel says what the run did to memory in one line, the same counts as the reply's
// Remembered line. Up to beta.17 it quoted every note in full, 334 to 461 px at 1052 for one to three saves. Each note's
// Undo, Edit, Keep and Remove are under the reply (rememberedLine.test.mjs).
test('the run’s memory is one line of counts, with what waits for you, and no buttons',()=>{
 const remembered=[{action:'dropped',entry_id:'old1',text:'The header was blue.',for_entry_id:'new1',reason:'merged'},{action:'add',entry_id:'new1',text:'Uploads retry three times.'},
  {action:'add',entry_id:'l1',text:'Run the frontend tests one file at a time.',kind:'lesson'},
  {action:'add',entry_id:'d1',text:'Bryce decided the header stays blue.',flag:{kind:'decision'}}];
 mount({task:{...finished,binding:{...binding,profile_id:'claude-1'},runs:[{...finished.runs[0],remembered}]}});
 const section=screen.getByRole('region',{name:'Remembered'});
 assert.equal(section.querySelectorAll('li').length,1);
 assert.equal(section.textContent,'Kept 3 notes, retired 1, 1 waits for youIn Knowledge, newest first');
 assert.equal(section.querySelector('button'),null);
 assert.doesNotMatch(section.textContent,/Uploads retry/,'the words are under the reply and in Knowledge');
});

test('a correction, a forgotten note and a refusal are counted, and a run that saved nothing has no memory block',()=>{
 const remembered=[{action:'supersede',entry_id:'new1',text:'The web tests need Node 22.',superseded_id:'old1',previous:'The web tests need Node 20.'},
  {action:'remove',entry_id:'x1',text:'The header was blue.'},{action:'duplicate',entry_id:'n1',text:'Tests run one file at a time.'},
  {action:'refused',text:'Use this writing rule for future replies.',reason:'This instruction needs your approval.'}];
 const {view}=mount({task:{...finished,runs:[{...finished.runs[0],remembered}]}});
 const section=screen.getByRole('region',{name:'Remembered'});
 assert.equal(section.textContent,'Updated 1 note, forgot 1, didn’t save 1In Knowledge, newest first','a duplicate changes nothing, so it isn’t counted');
 assert.equal(section.querySelector('[data-glyph]').dataset.glyph,'done');
 view.unmount();
 const {view:refused}=mount({task:{...finished,runs:[{...finished.runs[0],remembered:remembered.slice(3)}]}});
 assert.equal(screen.getByRole('region',{name:'Remembered'}).querySelector('[data-glyph]').dataset.glyph,'failed','only a refusal');
 refused.unmount();
 mount({task:finished});
 assert.equal(screen.queryByRole('region',{name:'Remembered'}),null);
});

test('after Stop is sent the button stays Stopping… until the run ends, so a second press can’t go out (b11/visible)',async()=>{
 const {isStopping,forgetStopping}=await import('./stopping.ts');
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_cmd,args)=>{calls.push(args);return {};}};
 const task={...running,id:'t-stop-1'};
 const {rerender}=mount({task},{name:'Live board'});
 fireEvent.click(screen.getByRole('button',{name:'Pause'}));
 await waitFor(()=>assert.equal(calls.length,1));
 await act(async()=>{});
 const button=screen.getByRole('button',{name:'Pausing…'});
 assert.equal(button.disabled,true,'still ending: no second Stop');
 fireEvent.click(button);
 assert.equal(calls.length,1);
 assert.equal(isStopping(task),true);
 // Negative controls: another running task still offers Stop, and once this one ends it isn't stopping any more.
 rerender({task:{...running,id:'t-stop-2'}});
 assert.equal(screen.getByRole('button',{name:'Pause'}).disabled,false);
 assert.equal(isStopping({...task,state:'blocked'}),false);
 assert.equal(isStopping({...running,id:'t-host',runs:[{state:'stopping'}]}),true,'the host saying so counts too');
 forgetStopping('t-stop-1');
});

test('under the Progress / Preview switch the panel starts at its status line: the tab says Progress and the head names the thread',()=>{
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{if(operation==='task_detail')return {task:finished,retry:{available:false},evidence:{attempts:[]}};throw Error(operation);}};
 mount({task:finished,switcher:React.createElement('div',{role:'tablist','aria-label':'Second column'},'Progress Preview')});
 const guide=screen.getByTestId('task-guide');
 assert.equal(guide.querySelector('.dv-panel-kicker'),null);
 assert.equal(guide.querySelector('.dv-panel-title'),null);
 assert.equal(screen.getByRole('status').textContent,'Finished','the status line leads');
 cleanup();
});

test('the pebble thinks only while a run is going; a finished task keeps its glyph (app #14)',()=>{
 mount({task:finished,refresh:async()=>{}},{name:'Ideas storage'});
 const guide=screen.getByTestId('task-guide');
 assert.equal(guide.querySelector('.dv-panel-state [data-testid="working-mark"]'),null);
 assert.ok(guide.querySelector('.dv-panel-state .dv-st'));
 assert.equal(guide.querySelector('.dv-panel-state .dv-st').hasAttribute('data-arrive'),false,'opened on a finished task: nothing to animate');
});
