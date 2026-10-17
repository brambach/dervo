import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLSelectElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {HandoffConfirm,RestoreSnapshot,handoffNote,filesLine,linesLine,stoppedLine,sendLabel}=await import('./HandoffConfirm.tsx');
const {ProviderSuggestion}=await import('./ProviderSuggestion.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const frame=element=>React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},element);

// Claude stopped at its usage limit with greeting.py changed: the brief the host builds from that run's records.
const brief={id:'h1',taskId:'t1',here:'Claude',to:'Codex',asked:'Add a farewell to greeting.py',files:['greeting.py','test_greeting.py'],
 changes:{'greeting.py':'changed','test_greeting.py':'new'},cleared:[],also:['notes.md'],added:12,removed:3,check:'test_greeting.py failed (exit 1).',
 stopped:'Claude Code reached its usage limit.',kind:'usage_limit',step:'Writing the farewell test',next:'Run the tests, then fix what fails.',
 snapshot:{untracked:1},full:false};

test('the confirm shows what the next agent gets, from records, and sends only on Send with the confirm’s id',async()=>{
 const calls=host(op=>op==='coding_handoff_preview'?brief:{id:'r2',taskId:'t2',state:'working',detail:''});
 let sent=0,cancelled=0;
 render(frame(React.createElement(HandoffConfirm,{projectId:'p',channelId:'c',taskId:'t1',to:'Codex',onSent:async()=>{sent++;},onCancel:()=>{cancelled++;}})));
 const card=await screen.findByTestId('handoff-confirm');
 await screen.findByRole('button',{name:'Send'});
 assert.equal(card.querySelector('.dv-carry-title').textContent,'Carry on in Codex');
 assert.equal(card.querySelector('.dv-carry-note').textContent,'Codex picks up from the files Claude left. Send saves a snapshot first, so you can go back.');
 const facts=[...card.querySelectorAll('.dv-facts-row')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd').textContent]);
 assert.deepEqual(facts,[['Asked','Add a farewell to greeting.py'],
  ['Files','greeting.py, test_greeting.py.12 lines added, 3 removed.Also uncommitted, not from this run: notes.md.'],
  ['Last check','test_greeting.py failed (exit 1).'],
  ['Stopped','At Claude’s usage limit. It was on “Writing the farewell test”.'],
  ['Next','“Run the tests, then fix what fails.”']]);
 assert.equal(document.activeElement.textContent,'Cancel','focus starts on the way out');
 assert.deepEqual(calls.map(c=>c.operation),['coding_handoff_preview'],'opening it sends nothing');
 assert.equal(card.textContent.includes('—'),false,'no em dash');
 fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.equal(sent,1));
 assert.deepEqual(calls[1],{operation:'coding_retry_on',args:{projectId:'p',channelId:'c',taskId:'t1',handoffId:'h1'}});
 assert.equal(cancelled,0);
});

test('Cancel and Escape send nothing, and a refusal from the host is said with no Send',async()=>{
 let calls=host(op=>op==='coding_handoff_preview'?brief:{});
 let cancelled=0;
 render(frame(React.createElement(HandoffConfirm,{projectId:'p',channelId:'c',taskId:'t1',to:'Codex',onSent:()=>{},onCancel:()=>{cancelled++;}})));
 await screen.findByRole('button',{name:'Send'});
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 fireEvent.keyDown(screen.getByTestId('handoff-confirm'),{key:'Escape'});
 assert.equal(cancelled,2);
 assert.deepEqual(calls.map(c=>c.operation),['coding_handoff_preview']);
 cleanup();
 const refusal='Claude left changed files, and this folder isn’t a git repository, so Dervo can’t save a snapshot first. Review the changes, then try again.';
 calls=host(op=>{if(op==='coding_handoff_preview')throw new Error(refusal);return {};});
 render(frame(React.createElement(HandoffConfirm,{projectId:'p',channelId:'c',taskId:'t1',to:'Codex',onSent:()=>{},onCancel:()=>{}})));
 assert.equal((await screen.findByRole('alert')).textContent,refusal);
 assert.equal(screen.queryByRole('button',{name:'Send'}),null);
 assert.ok(screen.getByRole('button',{name:'Cancel'}));
});

test('the words: a clean stop has no snapshot line, a rate limit and other stops read plainly, full access is on the button',()=>{
 assert.equal(handoffNote({here:'Codex',to:'Claude',snapshot:null}),'Claude gets your request and where Codex stopped.');
 assert.equal(filesLine({files:[]}),null);
 assert.equal(filesLine({files:['a.py','b.py','c.py','d.py']}),'a.py, b.py, c.py and 1 more.');
 assert.equal(linesLine({added:1,removed:0}),'1 line added, 0 removed.');
 assert.equal(stoppedLine({here:'Codex',kind:'rate_limit',stopped:'Codex was rate limited.',step:null}),'At Codex’s rate limit.');
 assert.equal(stoppedLine({here:'Codex',kind:'overloaded',stopped:'Codex was overloaded.',step:'Reading the tests.'}),'Codex was overloaded. It was on “Reading the tests”.');
 assert.equal(sendLabel({full:true}),'Send with full access');
 assert.equal(sendLabel({full:false}),'Send');
});

test('Continue in the handoff row opens the confirm in place of the actions, and the taken row offers Restore',async()=>{
 const claude={profile_id:'q',model:'claude-fable-5-1',provider:'claude',identity:'me',method:'subscription',label:'Claude Code'};
 const task={id:'t1',state:'blocked',origin:'conversation',binding:{provider:'codex'},route:{provider:'codex',profile_id:'p',model:'gpt'},
  suggest:{reason:'Codex was overloaded.',kind:'overloaded',other:claude,automatic:false,taken:null,error:null},runs:[],tokens:[],messages:[]};
 const calls=host(op=>op==='task_detail'?{retry:{available:false},rebound:false}:op==='coding_handoff_preview'?{...brief,here:'Codex',to:'Claude'}:{});
 const view=render(frame(React.createElement(ProviderSuggestion,{task,projectId:'p',channelId:'c',latest:true,conversationProfileId:'p',refresh:async()=>{}})));
 fireEvent.click(await screen.findByRole('button',{name:'Continue in Claude'}));
 await screen.findByRole('button',{name:'Send'});
 assert.equal(screen.queryByRole('button',{name:'Continue in Claude'}),null,'the row’s actions give way to the confirm');
 assert.equal(screen.getByTestId('provider-suggestion').querySelectorAll('.dv-facts').length,1,'and its facts: the confirm says them once');
 assert.equal(calls.some(c=>c.operation==='coding_retry_on'),false);
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 await screen.findByRole('button',{name:'Continue in Claude'});
 view.rerender(frame(React.createElement(ProviderSuggestion,{task:{...task,suggest:{...task.suggest,taken:'t2',
  snapshot:{handoff_id:'h1',ref:'refs/dervo/handoff/t1',commit:'0123',at:1,restored_at:null}}},projectId:'p',channelId:'c',latest:true,conversationProfileId:'p',refresh:async()=>{}})));
 assert.ok(await screen.findByRole('button',{name:'Restore the snapshot'}));
 view.rerender(frame(React.createElement(ProviderSuggestion,{task:{...task,suggest:{...task.suggest,taken:'t2'}},projectId:'p',channelId:'c',latest:true,conversationProfileId:'p',refresh:async()=>{}})));
 assert.equal(screen.queryByRole('button',{name:'Restore the snapshot'}),null,'no snapshot, nothing to restore');
 view.rerender(frame(React.createElement(ProviderSuggestion,{task:{...task,suggest:{...task.suggest,taken:'t2',
  snapshot:{handoff_id:'h1',ref:'refs/dervo/handoff/t1',commit:'0123',at:1,restored_at:null,removed_at:2}}},projectId:'p',channelId:'c',latest:true,conversationProfileId:'p',refresh:async()=>{}})));
 assert.equal(screen.queryByRole('button',{name:'Restore the snapshot'}),null,'a snapshot Dervo removed isn’t offered');
 assert.ok(screen.getByText('Dervo removed the snapshot a week after the work was settled.'));
});

test('beta.16: the confirm shows the end of what the stopped agent said, and a host before it shows no row',async()=>{
 host(op=>op==='coding_handoff_preview'?{...brief,said:'…so next I was going to write the test for the empty string case.',steps:['Wrote greeting.py','Ran the check']}:{});
 render(frame(React.createElement(HandoffConfirm,{projectId:'p',channelId:'c',taskId:'t1',to:'Codex',onSent:()=>{},onCancel:()=>{}})));
 const card=await screen.findByTestId('handoff-confirm');
 await screen.findByRole('button',{name:'Send'});
 const rows=[...card.querySelectorAll('.dv-facts-row')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd').textContent]);
 assert.deepEqual(rows.map(([name])=>name),['Asked','Files','Last check','Stopped','Said last','Last steps','Next'],'after how it stopped, before what comes next');
 assert.equal(rows[4][1],'“…so next I was going to write the test for the empty string case.”');
 assert.equal(rows[5][1],'Wrote greeting.py; Ran the check.','the steps the next agent is told, shown before Send');
 cleanup();
 host(op=>op==='coding_handoff_preview'?brief:{});
 render(frame(React.createElement(HandoffConfirm,{projectId:'p',channelId:'c',taskId:'t1',to:'Codex',onSent:()=>{},onCancel:()=>{}})));
 await screen.findByRole('button',{name:'Send'});
 assert.equal(screen.queryByText('Said last'),null);
 assert.equal(screen.queryByText('Last steps'),null);
});

test('beta.16: a stopped task whose files another agent carried on says it picked them up; a clean handoff doesn’t',async()=>{
 const codex={profile_id:'r',model:'gpt',provider:'codex',identity:'me',method:'chatgpt',label:'Codex'};
 const task={id:'t1',state:'blocked',origin:'conversation',binding:{provider:'claude'},route:{provider:'claude',profile_id:'q',model:'claude-fable-5-1'},
  suggest:{reason:'Claude Code reached its usage limit.',kind:'usage_limit',other:codex,automatic:false,taken:'t2',error:null,
   picked_up:{line:'Picked up from a run that stopped mid-change',files:['greeting.py','test_greeting.py','notes.md','app.py']}},runs:[],tokens:[],messages:[]};
 host(()=>({}));
 const view=render(frame(React.createElement(ProviderSuggestion,{task,projectId:'p',channelId:'c',latest:true,conversationProfileId:'r',refresh:async()=>{}})));
 const row=await screen.findByTestId('provider-suggestion');
 assert.equal(row.textContent,'Picked up from a run that stopped mid-change. Codex carried on with greeting.py, test_greeting.py, notes.md and 1 more, after Claude hit its usage limit.');
 view.rerender(frame(React.createElement(ProviderSuggestion,{task:{...task,suggest:{...task.suggest,picked_up:undefined}},projectId:'p',channelId:'c',latest:true,conversationProfileId:'r',refresh:async()=>{}})));
 assert.equal(screen.getByTestId('provider-suggestion').textContent,'Codex took over, after Claude hit its usage limit.');
});

// Behaviour review N1 (Bryce, 26 Sep): after your own Stop the row offers the other provider too, quietly: nothing failed,
// Retry stays in the Progress panel, and there's no switch-by-itself box.
test('after your own Stop the row offers Continue in the other provider on the newest request only',async()=>{
 const {handoffHoldsRetry}=await import('./CodingTasks.tsx');
 const codex={profile_id:'r',model:'gpt',provider:'codex',identity:'me',method:'chatgpt',label:'Codex'};
 const task={id:'t1',state:'blocked',origin:'conversation',binding:{provider:'claude'},route:{provider:'claude',profile_id:'q',model:'claude-fable-5-1'},
  suggest:{reason:'The user stopped it.',kind:'stopped',other:codex,automatic:false,taken:null,error:null},runs:[{id:'run-1',state:'interrupted'}],tokens:[],messages:[]};
 const calls=host(op=>op==='task_detail'?{retry:{available:true},rebound:false}:op==='coding_handoff_preview'?{...brief,here:'Claude',to:'Codex',kind:'stopped',stopped:'The user stopped it.',snapshot:null}:{});
 const props={projectId:'p',channelId:'c',latest:true,conversationProfileId:'q',policy:{auto_switch:false},refresh:async()=>{}};
 const view=render(frame(React.createElement(ProviderSuggestion,{task,...props})));
 const row=await screen.findByTestId('provider-suggestion');
 assert.equal(row.dataset.state,'stopped');
 assert.equal(row.querySelector('.dv-row-name').textContent,'You paused Claude');
 assert.equal(row.querySelector('.dv-row-line').textContent,'Codex can carry on from here.');
 assert.equal(row.querySelector('.dv-facts'),null,'the stopped card above already says what was done; the confirm shows what Codex gets');
 assert.equal(screen.queryByRole('button',{name:'Retry here'}),null,'Retry stays where it was');
 assert.equal(screen.queryByRole('checkbox'),null,'no switch-by-itself box after a Stop');
 assert.equal(handoffHoldsRetry(task),false);
 fireEvent.click(screen.getByRole('button',{name:'Continue in Codex'}));
 const card=await screen.findByTestId('handoff-confirm');
 await screen.findByRole('button',{name:'Send'});
 assert.match([...card.querySelectorAll('.dv-facts-row')].find(r=>r.querySelector('dt').textContent==='Stopped').querySelector('dd').textContent,/^You paused it\./);
 assert.equal(calls.some(c=>c.operation==='coding_retry_on'),false,'opening the confirm sends nothing');
 // Taken: the thread says who carried on, and Retry goes with it.
 view.rerender(frame(React.createElement(ProviderSuggestion,{task:{...task,suggest:{...task.suggest,taken:'t2'}},...props})));
 assert.equal(screen.getByTestId('provider-suggestion').textContent,'Codex took over, after you stopped Claude.');
 assert.equal(handoffHoldsRetry({suggest:{...task.suggest,taken:'t2'}}),true);
 // Negative controls: an older request, or one whose newest run isn't the stopped one, shows no row.
 view.rerender(frame(React.createElement(ProviderSuggestion,{task,...props,latest:false})));
 assert.equal(screen.queryByTestId('provider-suggestion'),null);
 view.rerender(frame(React.createElement(ProviderSuggestion,{task:{...task,state:'ready_for_review',runs:[{id:'run-1',state:'interrupted'},{id:'run-2',state:'completed'}]},...props})));
 assert.equal(screen.queryByTestId('provider-suggestion'),null);
 assert.equal(stoppedLine({here:'Claude',kind:'stopped',stopped:'The user stopped it.',step:'Writing the farewell'}),'You paused it. It was on “Writing the farewell”.');
});

const snapshot={handoff_id:'h1',ref:'refs/dervo/handoff/t1',commit:'0123',at:1,restored_at:null};
test('Restore asks first, names what goes back and what goes, and sends the plan’s id',async()=>{
 const plan={id:'r1',available:true,reason:null,put:['greeting.py'],remove:['extra.txt'],foreign:[],at:1,here:'Claude',to:'Codex'};
 const calls=host(op=>op==='coding_handoff_restore_preview'?plan:{restored:['greeting.py'],removed:['extra.txt'],detail:'Restored 2 files. Dervo kept the folder as it was just before, too.'});
 let refreshed=0;
 render(frame(React.createElement(RestoreSnapshot,{projectId:'p',channelId:'c',taskId:'t1',snapshot,refresh:async()=>{refreshed++;}})));
 assert.equal(calls.length,0,'nothing is read until you ask');
 fireEvent.click(screen.getByRole('button',{name:'Restore the snapshot'}));
 const card=await screen.findByTestId('handoff-restore');
 await screen.findByRole('button',{name:'Restore'});
 assert.equal(card.querySelector('.dv-carry-title').textContent,'Restore the snapshot?');
 assert.equal(card.querySelector('.dv-carry-note').textContent,'greeting.py goes back to how Claude left it. extra.txt wasn’t there then, so it goes. Dervo saves the folder as it is now first.');
 assert.deepEqual(calls.map(c=>c.operation),['coding_handoff_restore_preview']);
 fireEvent.click(screen.getByRole('button',{name:'Restore'}));
 await waitFor(()=>assert.equal(refreshed,1));
 assert.deepEqual(calls[1],{operation:'coding_handoff_restore',args:{projectId:'p',channelId:'c',taskId:'t1',restoreId:'r1'}});
 assert.equal(screen.getByRole('status').textContent,'Restored 2 files. Dervo kept the folder as it was just before, too.');
});

test('Restore that would overwrite someone else’s change says which, and offers only Close',async()=>{
 const reason='notes.md changed after the snapshot, and not by the run that carried on. Restoring would overwrite that, so Dervo won’t. Commit those changes or move them out of the folder, then try again.';
 const calls=host(()=>({id:'r1',available:false,reason,put:['greeting.py'],remove:[],foreign:['notes.md'],at:1,here:'Claude',to:'Codex'}));
 render(frame(React.createElement(RestoreSnapshot,{projectId:'p',channelId:'c',taskId:'t1',snapshot,refresh:async()=>{}})));
 fireEvent.click(screen.getByRole('button',{name:'Restore the snapshot'}));
 assert.equal((await screen.findByRole('note')).textContent,reason);
 assert.equal(screen.queryByRole('button',{name:'Restore'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Close'}));
 await screen.findByRole('button',{name:'Restore the snapshot'});
 assert.deepEqual(calls.map(c=>c.operation),['coding_handoff_restore_preview'],'nothing was restored');
});
