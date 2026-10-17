import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
const React=await import('react');
const {render,fireEvent,cleanup,screen,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {TraceFilePage,defaultLine}=await import('./TraceFile.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const at=(hour,minute)=>new Date(2026,8,23,hour,minute).getTime()/1000;
// The commit's author time is on the day the test runs, so "today" holds after midnight too.
const todayAt=(hour,minute)=>{const now=new Date();return new Date(now.getFullYear(),now.getMonth(),now.getDate(),hour,minute).getTime()/1000;};
const clock=seconds=>new Date(seconds*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
// run_history.trace_file and trace_line on utils/dates.py, as backend/test_run_history.py builds the repository: your
// first commit, Dervo's commit of the fix, and Live board's change on disk, not committed.
const LINES=['from datetime import datetime','','','def parse_date(text):','    """Parse an ISO date, keeping the timezone if present."""','    return datetime.fromisoformat(text)','','','def parse_day(text):','    return parse_date(text).date()'];
const file={path:'utils/dates.py',name:'dates.py',folder:'utils',total:10,first:1,last:10,cut:false,lines:LINES.map((text,index)=>({n:index+1,text})),
 blocks:[{key:'git:a1',label:'Bryce',taskId:null,provider:null,committed:true,sha:'a176506',first:1,last:4},
  {key:'t-fix:0',label:'Timezone fix',taskId:'t-fix',runIndex:1,provider:'claude',committed:true,sha:'b17dfb3',first:5,last:6},
  {key:'t-board:0',label:'Live board',taskId:'t-board',runIndex:1,provider:'codex',committed:false,sha:null,first:7,last:10}],
 runs:[{taskId:'t-board',runId:'r-board',runIndex:1,channelId:'c-board',name:'Live board',provider:'codex',at:at(2,57),what:'wrote lines 7 to 10',committed:false},
  {taskId:'t-fix',runId:'r-fix',runIndex:1,channelId:'c-fix',name:'Timezone fix',provider:'claude',at:at(1,41),what:'wrote lines 5 to 6',committed:true}],
 changes:2,uncommitted:1,note:null};
const board={path:'utils/dates.py',line:7,text:'',committed:false,commit:null,
 run:{taskId:'t-board',runId:'r-board',runIndex:1,channelId:'c-board',name:'Live board',provider:'codex',providerLabel:'Codex',model:'gpt-5.5',account:'ChatGPT Plus',access:'Chosen files'},
 change:'Not committed. Part of Live board’s result, waiting for your review.',written:{at:at(2,57),how:'through Dervo’s write tool'},
 step:{text:'Adding overlaps to the ideas listing.',at:at(2,52)},before:'It read this file and chat_links.py.',
 asked:{text:'When two running threads in one idea edit the same file, show it on the board.',at:at(2,36),where:'thread'},
 checked:{text:'Dervo’s check ran test_board.py: exit 0. It can’t tell which lines the check ran.',at:at(3,9)},nobody:null,author:null,note:null};
const bryce={path:'utils/dates.py',line:1,text:'from datetime import datetime',committed:true,
 commit:{sha:'a176506aaaa',short:'a176506',author:'Bryce',at:todayAt(0,5),subject:'Start the sample',taskId:null},run:null,change:'Committed as a176506.',
 written:null,step:null,before:null,asked:null,checked:null,nobody:'Nobody wrote this in Dervo.',author:{name:'Bryce',at:todayAt(0,5),subject:'Start the sample'},note:null};

function mount({line,view=file,lines={7:board,1:bryce},path='utils/dates.py'}={}){
 const calls=[],done=[],paths=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push([operation,args]);
  if(operation==='trace_file'){if(view instanceof Error)throw view;return view;}
  return lines[args.line]??{...bryce,line:args.line};}};
 const actions={onRecord:(...a)=>done.push(['record',...a]),onTrace:p=>done.push(['trace',p]),onOpenThread:id=>done.push(['thread',id]),onOpenTask:(id,task)=>done.push(['task',id,task])};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(React.createElement(QueryClientProvider,{client},React.createElement(TraceFilePage,{scope:{projectId:'p',profileId:'cs'},path,line,actions,onPath:(...a)=>paths.push(a)})));
 return {calls,done,paths};
}

test('with no line named, the trace opens on the newest change that isn’t committed',()=>{
 assert.equal(defaultLine(file),7);
 assert.equal(defaultLine({...file,blocks:file.blocks.slice(0,2)}),5);
 assert.equal(defaultLine({...file,blocks:file.blocks.slice(0,1)}),0);
 assert.equal(defaultLine({...file,focus:9}),9);
 assert.equal(defaultLine({...file,focus:90}),7);
});

test('each block names who wrote it once, beside its first line',async()=>{
 const {calls}=mount();
 await screen.findByRole('heading',{name:'dates.py'});
 assert.deepEqual(calls[0],['trace_file',{projectId:'p',profileId:'cs',path:'utils/dates.py',line:0}]);
 assert.equal(document.querySelector('.dervo-trace-head .dv-caption').textContent,'utils · 2 changes in Dervo’s records · 1 not committed');
 const labels=[...document.querySelectorAll('.dervo-trace-code td.blame')].map(cell=>[cell.textContent,cell.parentElement.querySelector('.ln').textContent,cell.rowSpan]);
 assert.deepEqual(labels,[['Bryce','1',4],['Timezone fix','5',2],['Live board','7',4]]);
 assert.equal(document.querySelectorAll('tr[data-selected]').length,1);
 assert.equal(document.querySelector('tr[data-selected] .ln').textContent,'7');
});

test('why a line is here: the change, who wrote it, its step quoted, your words and the check',async()=>{
 const {calls,done}=mount();
 await screen.findByText('Why line 7 is here');
 await screen.findByText('Not committed. Part of Live board’s result, waiting for your review.');
 assert.deepEqual(calls.find(([operation])=>operation==='trace_line'),['trace_line',{projectId:'p',profileId:'cs',path:'utils/dates.py',line:7}]);
 const facts=[...document.querySelectorAll('.dervo-trace-why > div')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd').textContent]);
 assert.deepEqual(facts,[
  ['Change','Not committed. Part of Live board’s result, waiting for your review.'],
  ['Written',`${clock(at(2,57))} by Codex, GPT-5.5 on ChatGPT Plus, through Dervo’s write tool.`],
  ['Its step',`Adding overlaps to the ideas listing. Reported at ${clock(at(2,52))}`],
  ['Before that','It read this file and chat_links.py.'],
  ['You asked',`When two running threads in one idea edit the same file, show it on the board. ${clock(at(2,36))}`],
  ['Checked',`Dervo’s check ran test_board.py at ${clock(at(3,9))}: exit 0. It can’t tell which lines the check ran.`]]);
 assert.equal(document.querySelector('.dervo-trace-why q').textContent,'Adding overlaps to the ideas listing.');
 // The run that wrote it shares the line's highlight in Runs on this file.
 assert.deepEqual([...document.querySelectorAll('.dervo-trace-runs li[data-linked] b')].map(node=>node.textContent),['Live board']);
 fireEvent.click(screen.getByRole('button',{name:'Open the record'}));
 assert.deepEqual(done.at(-1),['record','t-board','r-board']);
});

test('a line nobody wrote in Dervo says so and shows git’s author',async()=>{
 mount();
 await screen.findByRole('heading',{name:'dates.py'});
 fireEvent.click(screen.getByRole('button',{name:'Why line 1 is here'}));
 await screen.findByText('Committed as a176506.');
 const written=[...document.querySelectorAll('.dervo-trace-why > div')].find(row=>row.querySelector('dt').textContent==='Written');
 assert.equal(written.querySelector('dd').textContent,`Nobody wrote this in Dervo. Git says Bryce wrote it, today at ${clock(todayAt(0,5))}.`);
 assert.equal(document.querySelector('tr[data-selected] .ln').textContent,'1');
 await waitFor(()=>assert.equal(document.querySelectorAll('.dervo-trace-runs li[data-linked]').length,0));
});

test('the Runs view lists every run on the file, and a refused path says why and asks again',async()=>{
 const {done}=mount();
 await screen.findByRole('heading',{name:'dates.py'});
 fireEvent.click(screen.getByRole('radio',{name:'Runs'}));
 assert.equal(document.querySelector('.dervo-trace-code'),null);
 const runs=[...document.querySelectorAll('.dervo-trace-runs li')].map(li=>li.textContent);
 assert.deepEqual(runs,[`Live board${clock(at(2,57))} · wrote lines 7 to 10`,`Timezone fix${clock(at(1,41))} · wrote lines 5 to 6`]);
 fireEvent.click(within(document.querySelector('.dervo-trace-runs')).getByRole('button',{name:/Timezone fix/}));
 assert.deepEqual(done.at(-1),['record','t-fix','r-fix']);
 cleanup();
 const {paths}=mount({view:new Error('That path goes through a link. Dervo traces only files inside the project folder.'),path:'link.txt'});
 await screen.findByText('That path goes through a link. Dervo traces only files inside the project folder.');
 const input=screen.getByLabelText('File to trace');
 fireEvent.change(input,{target:{value:'backend/host.py:120'}});
 fireEvent.submit(input.closest('form'));
 assert.deepEqual(paths,[['backend/host.py',120]]);
});

test('a long file moves its window from a line number',async()=>{
 const {paths}=mount({view:{...file,total:600,first:1,last:80}});
 await screen.findByRole('heading',{name:'dates.py'});
 const input=screen.getByLabelText('Go to line');
 fireEvent.change(input,{target:{value:'42a0'}});
 assert.equal(input.value,'420');
 fireEvent.submit(input.closest('form'));
 assert.deepEqual(paths,[['utils/dates.py',420]]);
});

// Merge security pass, 23 Sep, F2: "You asked" holds only your words.
test('a line from a proposed thread puts the proposal under It said, and one from your answer keeps the question apart',async()=>{
 const PROPOSED='Purpose: Group the board by day\n\nAdd parse_day to utils/days.py.';
 const proposal={...board,asked:null,origin:{kind:'proposal',line:'Another thread proposed this, and you started it.',name:'Live board',taskId:'t-board',channelId:'c-board',quote:PROPOSED,quoteFrom:'Proposal from Live board',at:at(3,0)}};
 mount({lines:{7:proposal,1:bryce}});
 await screen.findByText('Another thread proposed this, and you started it.');
 const facts=()=>[...document.querySelectorAll('.dervo-trace-why > div')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd').textContent]);
 assert.ok(!facts().some(([label])=>label==='You asked'));
 assert.deepEqual(facts().find(([label])=>label==='It said'),['It said',`${PROPOSED} Proposal from Live board`]);
 cleanup();
 const answer={...board,asked:{text:'No, always keep the timezone.',at:at(3,5),where:'answer',picked:false},
  origin:{kind:'answer',line:'You answered a question from Timezone fix.',name:'Timezone fix',taskId:null,channelId:'c-fix',quote:'Keep naive datetimes?',quoteFrom:'Its question',at:at(3,5)}};
 mount({lines:{7:answer,1:bryce}});
 await screen.findByText('You answered a question from Timezone fix.');
 assert.deepEqual(facts().filter(([label])=>['You answered','It said','You asked'].includes(label)),[
  ['You answered',`No, always keep the timezone. Your answer, ${clock(at(3,5))}`],['It said','Keep naive datetimes? Its question']]);
});

test('a line git ran out of time on says so, plainly',async()=>{
 mount({lines:{7:{...board,committed:false,run:null,change:null,written:null,step:null,before:null,asked:null,checked:null,note:'Git took too long on this file. Try a smaller range.'},1:bryce}});
 await screen.findByText('Git took too long on this file. Try a smaller range.');
 assert.equal(document.querySelectorAll('.dervo-trace-why > div').length,0);
});
