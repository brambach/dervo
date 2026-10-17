import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','CustomEvent','Event','KeyboardEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
const React=await import('react');
const {render,fireEvent,cleanup,waitFor,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {HistoryScreen}=await import('./History.tsx');
const H=await import('./runHistory.ts');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const at=(hour,minute,day=0)=>new Date(2026,8,23-day,hour,minute).getTime()/1000;
const NOW=at(9,0);
const clock=seconds=>new Date(seconds*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
const who=(provider,model,account,access='Chosen files')=>({provider,providerLabel:provider==='codex'?'Codex':'Claude Code',model,account,access});
const run=(key,name,extra)=>({kind:'run',key,taskId:key.split(':')[0],runId:key.split(':')[1],runIndex:1,runCount:1,channelId:`c-${name}`,name,private:false,
 startedAt:at(1,38),endedAt:at(1,52),glyph:'done',ended:'Finished',did:'',line:'Finished.',files:[],checks:[],own:[],asked:null,told:null,said:[],startedBy:'you',links:[],linked:[],commit:null,
 ...who('claude','claude-opus-5','Personal'),...extra});
// Shaped from run_history.history_list on the Ideas storage, Live board and Home records (backend/test_run_history.py).
const board=run('t-board:r-board','Live board',{...who('codex','gpt-5.5','ChatGPT Plus'),startedAt:at(2,36),endedAt:at(3,40),glyph:'done',ended:'Finished',
 line:'Finished. Changed 5 files. Dervo’s check ran test_board.py: exit 0. Not committed.',files:['backend/project_ideas.py'],
 links:[{kind:'after',taskId:'t-ideas',channelId:'c-Ideas storage',name:'Ideas storage',path:'backend/project_ideas.py',text:'Edited project_ideas.py after Ideas storage'}],linked:['t-ideas:r-ideas']});
const picker=run('t-picker:r-picker','Connector picker',{startedAt:at(1,40),endedAt:at(2,14),glyph:'failed',ended:'Stopped at Claude’s usage limit',
 line:'Stopped at Claude’s usage limit. Changed 3 files. No check ran in Dervo. Not committed.',startedBy:'home',
 links:[{kind:'home',name:'Home',at:at(1,37),text:'Started by Home from your message'}]});
const ideas=run('t-ideas:r-ideas','Ideas storage',{...who('claude','claude-opus-5','Personal','Full access'),glyph:'needs',ended:'Waiting for your review',
 line:'Waiting for your review. Changed project_ideas.py and test_project_ideas.py. No check ran in Dervo.',files:['backend/project_ideas.py','backend/test_project_ideas.py'],
 own:[{command:'python3 -m unittest test_project_ideas',prose:false,at:at(1,46),error:false,seconds:3},{command:'python3 -m unittest test_project_ideas',prose:false,at:at(1,48),error:true,seconds:4}],
 asked:{text:'Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped.',at:at(1,37),where:'home'},told:'CLAUDE.md, 2 project notes and recent work in 1 other thread.',
 said:[{text:'44 backend tests pass.',source:'reply'},{text:'With the group mirror off, 5 of the 10 new tests fail, so they bite.',source:'reply'},{text:'Not checked: the frontend.',source:'limits'}],
 startedBy:'home',links:[{kind:'home',name:'Home',at:at(1,37),text:'Started by Home from your message'},{kind:'later',taskId:'t-board',channelId:'c-Live board',name:'Live board',path:'backend/project_ideas.py',text:'Live board edited the same file later, on Codex'}],
 linked:['home:r1','t-board:r-board']});
const home={...run('home:r1','Home',{}),kind:'home',taskId:null,runId:null,channelId:null,provider:null,providerLabel:'Home',model:null,account:null,access:null,
 startedAt:at(1,37),endedAt:null,ended:'Done',line:'Started Ideas storage and Connector picker from your message.',linked:['t-ideas:r-ideas','t-picker:r-picker']};
const sweep=run('t-sweep:r-sweep','Design sweep',{startedAt:at(22,12,1),endedAt:at(23,2,1),glyph:'stopped',ended:'You stopped it',line:'You stopped it. Changed 2 files. Not committed.'});
// Pieces of work (beta.16, b16/git), shaped from run_history._work on the same runs (backend/test_run_history.py).
const piece=(name,runs,extra)=>({key:`work:c-${name}`,channelId:`c-${name}`,name,private:false,archived:false,tidied:null,glyph:'done',state:'Finished',agents:['Claude Code'],
 runCount:runs.filter(key=>!key.startsWith('home:')).length,files:[],summary:'1 run',startedAt:at(1,38),endedAt:at(1,52),at:at(1,52),commits:[],where:null,pr:null,reviewTaskId:null,runs,...extra});
const PR={number:214,url:'https://github.com/brambach/dervo/pull/214',repo:'brambach/dervo',head:'dervo/live-board',base:'main',mode:'thread',attempt:'opened',detail:null,
 state:'open',draft:true,review:'changes_requested',reviewers:{changes:['ana-l']},checks:{passing:2,failing:0,pending:0},
 comments:[{author:'ana-l',text:'This warns on a rename too. Should it?',path:'backend/live_board.py',line:88,at:at(3,50)}],checkedAt:at(3,52),checkedAs:'brambach'};
const boardWork=piece('Live board',['t-board:r-board'],{agents:['Codex'],glyph:'done',state:'Approved and committed',summary:'1 run, changed 1 file, check passed',
 files:['backend/project_ideas.py'],startedAt:at(2,36),endedAt:at(3,40),at:at(3,40),commits:[{sha:'4e1c9a2f00',short:'4e1c9a2',at:at(3,41),reverted:false,onGitHub:true}],where:'github',pr:PR});
const pickerWork=piece('Connector picker',['t-picker:r-picker'],{glyph:'failed',state:'Stopped at Claude’s usage limit',summary:'1 run',startedAt:at(1,40),endedAt:at(2,14),at:at(2,14)});
const ideasWork=piece('Ideas storage',['home:r1','t-ideas:r-ideas'],{glyph:'needs',state:'Ready for review',summary:'1 run, changed 2 files, no check ran in Dervo',
 files:['backend/project_ideas.py','backend/test_project_ideas.py'],reviewTaskId:'t-ideas'});
const sweepWork=piece('Design sweep',['t-sweep:r-sweep'],{glyph:'stopped',state:'You stopped it',startedAt:at(22,12,1),endedAt:at(23,2,1),at:at(23,2,1)});
const listing={runs:[board,picker,ideas,home,sweep],work:[boardWork,pickerWork,ideasWork,sweepWork],more:0,
 counts:{all:5,changed:4,home:3,work:4,waiting:1,local:0,prs:1},providers:['claude','codex'],incomplete:false};
const card=name=>screen.getAllByTestId('history-work').find(node=>node.querySelector('.dv-row-name').textContent===name);

function mount(answer=()=>listing,props={}){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push([operation,args]);return answer(operation,args);}};
 const opened=[];
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(HistoryScreen,{projectId:'p',profileId:'cs',projectName:'dervo app',open:true,now:NOW,
  onClose:()=>opened.push(['close']),onOpenThread:id=>opened.push(['thread',id]),onOpenTask:(id,task)=>opened.push(['task',id,task]),...props})));
 return {view,calls,opened};
}

test('times read the way the mockups say them',()=>{
 assert.equal(H.span(at(1,38),at(1,52)),`${clock(at(1,38)).replace(/\s?AM$/,'')} to ${clock(at(1,52))}`);
 assert.equal(H.span(at(23,40,1),at(0,10)),`${clock(at(23,40,1))} to ${clock(at(0,10))}`);
 assert.equal(H.span(at(1,37),null),clock(at(1,37)));
 assert.equal(H.took(at(1,38),at(1,52)),'14 min');
 assert.equal(H.took(at(1,0),at(3,5)),'2 h 5 min');
 assert.equal(H.dayLabel(at(1,0),NOW),'Today');
 assert.equal(H.dayLabel(at(22,0,1),NOW),'Yesterday');
 assert.deepEqual(H.byDay(listing.runs,NOW).map(day=>[day.day,day.rows.length]),[['Today',4],['Yesterday',1]]);
 assert.equal(H.sinceLine(listing.runs,NOW),'4 runs since yesterday, from what each one did');
 assert.equal(H.sinceLine([],NOW),'Nothing has run in this project yet.');
});

test('the byline says the model, the account and only an access worth naming',()=>{
 assert.deepEqual(H.bylineParts(ideas),['Opus 5','Personal','Full access']);
 assert.deepEqual(H.bylineParts(board),['GPT-5.5','ChatGPT Plus']);
 assert.equal(H.ownLine(ideas.own[1]),`Ran \`python3 -m unittest test_project_ideas\` itself at ${clock(at(1,48))}, which ended in an error.`);
 assert.equal(H.ownLine({command:'Ran the backend tests',prose:true,at:null,error:false,seconds:null}),'Ran a command it described as “Ran the backend tests” itself, no error.');
 assert.deepEqual(H.codeParts('Changed `a.py` and b'),[{code:false,text:'Changed '},{code:true,text:'a.py'},{code:false,text:' and b'}]);
 assert.equal(H.checkLine({at:at(1,40),text:'Dervo’s check ran test_sample.py: exit 0.'}),`Dervo’s check ran test_sample.py at ${clock(at(1,40))}: exit 0.`);
 assert.deepEqual(H.didLines(ideas),['Changed `project_ideas.py` and `test_project_ideas.py`.',H.ownLine(ideas.own[0]),H.ownLine(ideas.own[1])]);
 assert.equal(H.accentKey(listing.runs),'t-ideas:r-ideas');
});

test('the log shows one card per thread by day, says each state once and puts the accent on the one waiting on you',async()=>{
 const {calls}=mount();
 await screen.findByText('Ideas storage');
 assert.deepEqual(calls[0],['history_list',{projectId:'p',profileId:'cs',filter:'all',provider:'',file:''}]);
 assert.equal(screen.getByTestId('history-since').textContent,'4 pieces of work, 4 runs, since yesterday');
 assert.deepEqual([...document.querySelectorAll('.dervo-history-day > .dv-label')].map(node=>node.textContent),['Today','Yesterday']);
 assert.deepEqual(screen.getAllByTestId('history-work').map(node=>node.querySelector('.dv-row-name').textContent),['Live board','Connector picker','Ideas storage','Design sweep']);
 // One accent in the whole view, on the piece waiting for you. Nothing opens by itself.
 assert.equal(document.querySelectorAll('.dv-st[data-accent]').length,1);
 assert.ok(card('Ideas storage').querySelector('.dv-st[data-accent]'));
 assert.equal(document.querySelectorAll('.dv-open').length,0);
 // What it added up to, and the git line: shape and words, not colour.
 assert.equal(card('Ideas storage').querySelector('.dervo-work-sum').textContent,'Claude Code· 1 run, changed 2 files, no check ran in Dervo.');
 assert.equal(card('Ideas storage').querySelector('.dervo-work-git').textContent,'Nothing committed yet');
 assert.equal(card('Connector picker').querySelector('.dervo-work-git').textContent,'Nothing committed');
 assert.deepEqual([...card('Live board').querySelectorAll('.dervo-git-chip')].map(node=>[node.dataset.kind,node.textContent]),
  [['commit','4e1c9a2'],['where','On GitHub'],['pr','PR #214 · Draft · Changes requested']]);
 // A closed card with one run doesn't list it; the one with Home's reply does.
 assert.equal(card('Live board').querySelector('.dervo-work-runs'),null);
 assert.deepEqual([...card('Ideas storage').querySelectorAll('.dervo-work-run')].map(node=>node.textContent),['Started this thread from your message','Run 1 · Opus 5 · waiting for your review']);
 // Open it: a run's line opens its facts, quoted where an agent said them.
 fireEvent.click(within(card('Ideas storage')).getByRole('button',{name:/Ideas storage/,expanded:false}));
 const open=document.querySelector('.dv-open');
 fireEvent.click(within(open).getByRole('button',{name:/Run 1/}));
 const facts=[...open.querySelectorAll('dt')].map(node=>node.textContent);
 assert.deepEqual(facts,['Asked','Told','Did','It said','Linked']);
 const said=open.querySelector('.dervo-history-said');
 assert.deepEqual([...said.querySelectorAll('q')].map(node=>node.textContent),['44 backend tests pass.','With the group mirror off, 5 of the 10 new tests fail, so they bite.','Not checked: the frontend.']);
 assert.ok(!open.querySelector('.dervo-work-sum').textContent.includes('44 backend tests'));
 assert.match(open.textContent,/You, on Home/);
 // The other pieces its runs are linked to take the shared highlight, and nothing else does: Live board edited the same
 // file, and Home's reply that started this one started Connector picker too.
 assert.deepEqual(screen.getAllByTestId('history-work').filter(node=>node.hasAttribute('data-linked')).map(node=>node.querySelector('.dv-row-name').textContent),['Live board','Connector picker']);
});

test('filters ask the host again, and the counts sit in the control',async()=>{
 const {calls}=mount();
 await screen.findByText('Ideas storage');
 assert.deepEqual(screen.getAllByRole('radio').map(node=>node.textContent),['All4','Waiting on you1','Not on GitHub0','Pull requests1']);
 // One piece of work often has both agents, so the agent picker is gone (approved mock, 25 Sep).
 assert.equal(screen.queryByLabelText('Agent'),null);
 fireEvent.click(screen.getByRole('radio',{name:/Waiting on you/}));
 await waitFor(()=>assert.ok(calls.some(([operation,args])=>operation==='history_list'&&args.filter==='waiting')));
 fireEvent.click(screen.getByRole('radio',{name:/Pull requests/}));
 await waitFor(()=>assert.ok(calls.some(([operation,args])=>operation==='history_list'&&args.filter==='prs')));
 fireEvent.change(screen.getByLabelText('Filter by file'),{target:{value:'project_ideas'}});
 await waitFor(()=>assert.ok(calls.some(([,args])=>args.file==='project_ideas')));
});

test('a filter with nothing in it says what would put something there',async()=>{
 mount((operation,args)=>({...listing,runs:[],work:args.filter==='all'?listing.work:[]}));
 await screen.findByText('Ideas storage');
 fireEvent.click(screen.getByRole('radio',{name:/Pull requests/}));
 await screen.findByText('Open a pull request from a finished thread and it shows here.');
 assert.equal(screen.getByRole('heading',{level:2}).textContent,'Nothing matches');
});

test('a card opens and closes, and its actions lead to the thread, the record, GitHub and the trace',async()=>{
 const answer=(operation,args)=>operation==='history_list'?listing:operation==='run_record'?Promise.reject(new Error('That run isn’t in this project’s records.'))
  :operation==='trace_file'?Promise.reject(new Error(`${args.path} isn’t in this project folder.`)):operation==='pr_status'?{pr:{...PR,checkedAt:at(8,55)},attempt:null,pullOffer:null}:null;
 const {calls,opened}=mount(answer);
 await screen.findByText('Ideas storage');
 fireEvent.click(screen.getByRole('button',{name:'Review'}));
 assert.deepEqual(opened.at(-1),['task','c-Ideas storage','t-ideas']);
 fireEvent.click(screen.getByRole('button',{name:/Live board/,expanded:false}));
 assert.equal(document.querySelectorAll('.dv-open').length,1);
 const open=document.querySelector('.dv-open');
 assert.equal(open.querySelector('.dv-row-name').textContent,'Live board');
 // The open card reads the PR again from GitHub, as you, and says when; the reviewer's words stay quoted.
 await waitFor(()=>assert.ok(calls.some(([operation,args])=>operation==='pr_status'&&args.channelId==='c-Live board'&&args.refresh===true)));
 await waitFor(()=>assert.match(open.querySelector('.dervo-pr-checked').textContent,new RegExp(`Checked on GitHub at ${clock(at(8,55))}, as brambach\\.`)));
 const box=open.querySelector('.dervo-pr-box');
 assert.equal(box.querySelector('.dervo-pr-head b').textContent,'Draft pull request #214');
 assert.deepEqual([...box.querySelectorAll('q')].map(node=>node.textContent),['This warns on a rename too. Should it?']);
 assert.match(box.textContent,/Changes requested by ana-l\./);
 assert.match(box.textContent,/2 of 2 passing/);
 assert.ok(within(open).getByRole('button',{name:'Open on GitHub'}));
 fireEvent.click(within(open).getByRole('button',{name:'Open the thread'}));
 assert.deepEqual(opened.at(-1),['thread','c-Live board']);
 fireEvent.click(within(open).getByRole('button',{name:'Record'}));
 await screen.findByText(/This record couldn’t load/);
 assert.deepEqual(calls.at(-1),['run_record',{projectId:'p',profileId:'cs',taskId:'t-board',runId:'r-board'}]);
 assert.equal(document.querySelector('.dv-here').textContent,'Record');
 fireEvent.click(screen.getByRole('button',{name:'History'}));
 await screen.findByText('Ideas storage');
 fireEvent.click(screen.getByRole('button',{name:/Live board/,expanded:false}));
 // Clicking the head again closes it.
 fireEvent.click(screen.getByRole('button',{name:/Live board/,expanded:true}));
 assert.equal(document.querySelectorAll('.dv-open').length,0);
 fireEvent.click(screen.getByRole('button',{name:/Live board/,expanded:false}));
 fireEvent.click(screen.getByRole('button',{name:'Trace a file'}));
 await screen.findByText('backend/project_ideas.py isn’t in this project folder.');
 assert.deepEqual(calls.at(-1),['trace_file',{projectId:'p',profileId:'cs',path:'backend/project_ideas.py',line:0}]);
 // Esc goes back to the log first, then closes History.
 fireEvent.keyDown(document.querySelector('[data-testid="history"]'),{key:'Escape'});
 await screen.findByText('Ideas storage');
 fireEvent.keyDown(document.querySelector('[data-testid="history"]'),{key:'Escape'});
 assert.deepEqual(opened.at(-1),['close']);
});

test('an empty project and a failed read each say what happened',async()=>{
 mount(()=>({runs:[],more:0,counts:{all:0,changed:0,home:0},providers:[],incomplete:false}));
 await screen.findByText('Nothing has run yet');
 cleanup();
 mount(()=>Promise.reject(new Error('Restore this project before opening its saved work.')));
 const alert=await screen.findByRole('alert');
 assert.match(alert.textContent,/History couldn’t load\. Restore this project/);
});

test('a place asked for from outside opens there',async()=>{
 const answer=operation=>operation==='run_record'?Promise.reject(new Error('nope')):listing;
 const {calls}=mount(answer,{place:{view:'record',taskId:'t-ideas',runId:'r-ideas'},placeRequest:1});
 await screen.findByText(/This record couldn’t load/);
 assert.deepEqual(calls[0],['run_record',{projectId:'p',profileId:'cs',taskId:'t-ideas',runId:'r-ideas'}]);
});

test('the Progress panel opens the run’s record and a changed file’s trace in History when the shell wires them',async()=>{
 const {TaskGuide}=await import('./TaskGuide.tsx');
 const task={id:'t-ideas',state:'ready_for_review',origin:'conversation',binding:{provider:'claude',model:'claude-opus-5'},files:[],
  messages:[{role:'user',text:'Store ideas per project.',created_at:at(1,38)}],runs:[{id:'run',state:'completed',started_at:at(1,38),ended_at:at(1,52)}],
  tokens:[{run_id:'run',tool:'dervo_write_file',state:'delivered',success:true,path:'backend/project_ideas.py'}]};
 const opened=[];
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 client.setQueryData(['local-conversations','p'],[{id:'c',name:'Ideas storage'}]);
 render(React.createElement(QueryClientProvider,{client},React.createElement(TaskGuide,{task,projectId:'p',channelId:'c',refresh:async()=>{},onClose(){},
  onOpenRecord:()=>opened.push(['record']),onTraceFile:path=>opened.push(['trace',path])})));
 fireEvent.click(screen.getByRole('button',{name:'Trace backend/project_ideas.py'}));
 fireEvent.click(screen.getByRole('button',{name:/Open the record/}));
 assert.deepEqual(opened,[['trace','backend/project_ideas.py'],['record']]);
 assert.equal(document.querySelector('[data-testid="task-guide"]').getAttribute('data-view'),'progress');
});

test('⌘K offers History under Go to when the shell gives it an action',async()=>{
 const {paletteItems}=await import('./CommandPalette.tsx');
 const noop=()=>{};
 const actions={openConversation:noop,catchUp:noop,knowledge:noop,projectSettings:noop,create:noop,toggleGuide:noop,stopCurrent:noop,stopConversation:noop,changeModel:noop,usage:noop,addGuidance:noop,toggleTheme:noop,feedback:noop,workspaceSettings:noop,search:noop};
 const state={conversations:[],currentId:'',hasProject:true,ready:true,guide:null,canStopCurrent:false,dark:false};
 assert.equal(paletteItems(state,actions).some(item=>item.id==='go:history'),false);
 let ran=0;
 const item=paletteItems(state,{...actions,history:()=>ran++}).find(entry=>entry.id==='go:history');
 assert.deepEqual([item.group,item.label,item.detail],['Go to','History','What each run did']);
 item.run();
 assert.equal(ran,1);
});

// Merge security pass, 23 Sep, F2: a started proposal and an answer sent after a run arrive as Home jobs. The log says
// where each came from and quotes the agent's words as theirs, never under Asked.
const PROPOSED='Purpose: Group the board by day\n\nAdd parse_day to utils/days.py so the board can group runs by day.';
const QUESTION='Should parse_date keep naive datetimes for the old callers?';
const proposed=run('t-days:r-days','Day parser',{startedBy:'proposal',asked:null,
 origin:{kind:'proposal',line:'Another thread proposed this, and you started it.',name:'Live board',taskId:'t-board',channelId:'c-Live board',quote:PROPOSED,quoteFrom:'Proposal from Live board',at:at(3,0)},
 said:[{text:PROPOSED,source:'proposal',from:'Proposal from Live board'},{text:'Added parse_day.',source:'reply'}],
 links:[{kind:'proposed',taskId:'t-board',channelId:'c-Live board',name:'Live board',text:'Proposed by Live board, started by you'}]});
const answered=run('t-answer:r-answer','Timezone fix',{startedBy:'answer',asked:{text:'No, always keep the timezone.',at:at(3,5),where:'answer',picked:false},
 origin:{kind:'answer',line:'You answered a question from Timezone fix.',name:'Timezone fix',taskId:null,channelId:'c-Timezone fix',quote:QUESTION,quoteFrom:'Its question',at:at(3,5)},
 said:[{text:QUESTION,source:'question',from:'Its question'}]});

/** One card of one run, opened, with that run's facts open. */
async function openOnly(row){
 mount(()=>({...listing,runs:[row],work:[piece(row.name,[row.key],{channelId:row.channelId,agents:[row.providerLabel]})]}));
 fireEvent.click(await screen.findByRole('button',{name:new RegExp(row.name),expanded:false}));
 fireEvent.click(within(document.querySelector('.dv-open')).getByRole('button',{name:/Run 1/}));
 return document.querySelector('.dv-open');
}

test('a started proposal says another thread proposed it, and its words sit only under It said',async()=>{
 const open=await openOnly(proposed);
 const facts=[...open.querySelectorAll('.dv-facts-row')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd')]);
 assert.deepEqual(facts.map(([label])=>label),['Started','Told','Did','It said','Linked']);
 assert.equal(facts[0][1].textContent,'Another thread proposed this, and you started it.');
 const said=facts.find(([label])=>label==='It said')[1];
 assert.equal(said.querySelector('q').textContent,PROPOSED);
 assert.match(said.textContent,/Proposal from Live board/);
 assert.ok(!facts.some(([label])=>label==='Asked'));
 assert.equal(open.querySelector('.dervo-work-agents').textContent,'Claude Code');
});

test('an answer is yours under Answered, and the question is the agent’s under It said',async()=>{
 const open=await openOnly(answered);
 const facts=Object.fromEntries([...open.querySelectorAll('.dv-facts-row')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd')]));
 assert.equal(facts.Started.textContent,'You answered a question from Timezone fix.');
 assert.equal(facts.Answered.querySelector('q').textContent,'No, always keep the timezone.');
 assert.match(facts.Answered.textContent,/Your answer/);
 assert.ok(!facts.Answered.textContent.includes(QUESTION));
 assert.equal(facts['It said'].querySelector('q').textContent,QUESTION);
 assert.equal(facts.Asked,undefined);
});

test('a History cut short by its time budget says so',async()=>{
 mount(()=>({...listing,incomplete:true,note:'Dervo stopped reading older records so this could answer in time. Some older runs aren’t included.'}));
 await screen.findByText('Ideas storage');
 assert.equal(document.querySelector('.dervo-history-more').textContent,'Dervo stopped reading older records so this could answer in time. Some older runs aren’t included.');
});

// b16/git and b16/lifecycle agreed (25 Sep): archived threads stay, marked, and say when Dervo put them away itself.
test('an archived thread keeps its place, marked, and a merged PR and an undone commit read by shape and words',async()=>{
 const canary=piece('Keychain canary',['t-sweep:r-sweep'],{archived:true,tidied:{at:at(9,0,2),quietSince:at(9,0,16)},startedAt:at(21,5,16),endedAt:at(21,40,16),at:at(21,40,16),
  state:'Approved and committed',commits:[{sha:'5a8e0f3aa',short:'5a8e0f3',at:at(21,41,16),reverted:false,onGitHub:true}],where:'github',
  pr:{...PR,number:188,state:'merged',draft:false,review:'approved',comments:[]}});
 const restart=piece('Update and restart',['t-picker:r-picker'],{commits:[{sha:'c03a9d5bb',short:'c03a9d5',at:at(1,50),reverted:false,onGitHub:false},{sha:'1111111cc',short:'1111111',at:at(1,51),reverted:true,onGitHub:null}],where:'local'});
 mount(()=>({...listing,work:[restart,canary]}));
 await screen.findByText('Keychain canary');
 const archived=card('Keychain canary');
 assert.ok(archived.hasAttribute('data-archived'));
 assert.equal(archived.querySelector('.dervo-work-archived').textContent,'Archived');
 assert.equal(archived.querySelector('.dervo-work-note').textContent,'Archived after 14 quiet days. Opening it brings it back.');
 assert.deepEqual([...archived.querySelectorAll('.dervo-git-chip')].map(node=>[node.dataset.kind,node.textContent]),[['commit','5a8e0f3'],['where','On GitHub'],['merged','PR #188 · Merged']]);
 const local=card('Update and restart');
 assert.deepEqual([...local.querySelectorAll('.dervo-git-chip')].map(node=>[node.dataset.kind,node.textContent,node.hasAttribute('data-reverted')]),
  [['commit','c03a9d5',false],['commit','1111111 (undone)',true],['where','Not on GitHub',false]]);
});
