import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
const React=await import('react');
const {render,fireEvent,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {RunRecordPage}=await import('./RunRecord.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const at=(hour,minute)=>new Date(2026,8,23,hour,minute).getTime()/1000;
const clock=seconds=>new Date(seconds*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
const call=(tool,target,out='',extra={})=>({tool,target,out,failed:false,at:at(1,40),...extra});
// run_history.run_record for Ideas storage, the full-access run Home started (backend/test_run_history.py shapes).
const ideas={taskId:'t-ideas',runId:'r-ideas',runIndex:1,runCount:1,channelId:'c-ideas',name:'Ideas storage',private:false,
 provider:'claude',providerLabel:'Claude Code',model:'claude-opus-5',account:'Personal',access:'Full access',
 startedAt:at(1,38),endedAt:at(1,52),glyph:'needs',ended:'Waiting for your review',state:'ready_for_review',canReview:true,
 why:{you:{text:'Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped.',at:at(1,37),where:'home'},
  home:{text:'Build step 1 of PLAN-orchestrator.md: ideas stored per project.',at:at(1,37)},steers:[]},
 told:{recorded:true,repository:[{path:'CLAUDE.md',bytes:4198,cut:false}],rules:0,notes:['Tests run with python3 -m unittest from backend/'],skills:[],
  recent:['2 hours ago: Connector picker (running)'],workstream:null,earlier:null},
 steps:[
  {at:at(1,38),text:'Reading the plan and the modules the ideas store sits beside.',why:null,calls:[call('Read','PLAN-orchestrator.md'),call('Read','backend/conversation_organisation.py')]},
  {at:at(1,40),text:'Writing the ideas store and its tests.',why:'Ideas are the group key for conversations.',calls:[call('Write','backend/project_ideas.py','+252')]},
  {at:at(1,46),text:'Running the backend tests.',why:null,calls:[call('Bash','python3 -m unittest test_project_ideas','3 s · no error'),call('Bash','python3 -m unittest test_project_ideas','4 s · error',{failed:true})]},
 ],
 checks:{dervo:[],own:[{command:'python3 -m unittest test_project_ideas',prose:false,at:at(1,46),error:false,seconds:3},{command:'python3 -m unittest test_project_ideas',prose:false,at:at(1,48),error:true,seconds:4}],
  note:'Dervo ran no check of its own, so it can’t see a test count.'},
 said:[{text:'44 backend tests pass.',source:'reply'},{text:'Not checked: the frontend.',source:'limits'}],
 files:[{path:'backend/project_ideas.py',added:252,removed:0},{path:'backend/test_project_ideas.py',added:187,removed:0}],commit:null,
 links:[{kind:'home',name:'Home',at:at(1,37),text:'Started by Home from your message'},
  {kind:'later',taskId:'t-board',channelId:'c-board',name:'Live board',provider:'codex',at:at(2,57),path:'backend/project_ideas.py',text:'Codex edited project_ideas.py after this run'}]};

function mount(record=ideas){
 const calls=[],done=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push([operation,args]);if(record instanceof Error)throw record;return record;}};
 const actions={onRecord:(...a)=>done.push(['record',...a]),onTrace:path=>done.push(['trace',path]),onOpenThread:id=>done.push(['thread',id]),onOpenTask:(id,task)=>done.push(['task',id,task])};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(React.createElement(QueryClientProvider,{client},React.createElement(RunRecordPage,{scope:{projectId:'p',profileId:'cs'},taskId:'t-ideas',runId:'r-ideas',actions})));
 return {calls,done};
}

test('why it ran puts your words first and keeps what Home wrote behind a button, labelled as Home’s',async()=>{
 const {calls}=mount();
 await screen.findByRole('heading',{name:'Ideas storage'});
 assert.deepEqual(calls[0],['run_record',{projectId:'p',profileId:'cs',taskId:'t-ideas',runId:'r-ideas'}]);
 assert.match(document.querySelector('.dervo-record-head .dv-caption').textContent,/^Run 1 of 1 · .* · 14 min$/);
 assert.match(document.querySelector('.dervo-record-head').textContent,/Opus 5 · Personal account · Full access/);
 const asked=document.querySelector('.dervo-record-asked');
 assert.deepEqual([...asked.querySelectorAll('dt')].map(node=>node.textContent),['You','Home']);
 assert.equal(asked.querySelector('q').textContent,'Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped.');
 assert.match(asked.textContent,new RegExp(`On Home, ${clock(at(1,37))}\\.`));
 assert.ok(!document.body.textContent.includes('Build step 1 of PLAN-orchestrator.md'));
 fireEvent.click(screen.getByRole('button',{name:/Show what Home wrote/}));
 assert.equal(document.querySelector('.dervo-record-home p').textContent,'Build step 1 of PLAN-orchestrator.md: ideas stored per project.');
 assert.equal(document.querySelector('.dervo-record-home .dv-label').textContent,'Home wrote');
});

test('checked puts what Dervo saw beside what it said, and only the second column is quoted',async()=>{
 mount();
 await screen.findByRole('heading',{name:'Ideas storage'});
 const [saw,said]=document.querySelectorAll('.dervo-record-compare > div');
 assert.equal(saw.querySelector('h3').textContent,'What Dervo saw');
 assert.deepEqual([...saw.querySelectorAll('li')].map(node=>node.textContent),[
  `Ran python3 -m unittest test_project_ideas itself at ${clock(at(1,46))}, no error.`,
  `Ran python3 -m unittest test_project_ideas itself at ${clock(at(1,48))}, which ended in an error.`,
  'Dervo ran no check of its own, so it can’t see a test count.']);
 assert.equal(saw.querySelectorAll('q').length,0);
 assert.deepEqual([...said.querySelectorAll('q')].map(node=>node.textContent),['44 backend tests pass.','Not checked: the frontend.']);
});

test('what it did lists each step it reported over the calls Dervo saw',async()=>{
 mount();
 await screen.findByRole('heading',{name:'Ideas storage'});
 const steps=[...document.querySelectorAll('.dervo-record-trail > li')];
 assert.deepEqual(steps.map(step=>step.querySelector('.dervo-record-step').textContent),
  ['Reading the plan and the modules the ideas store sits beside.','Writing the ideas store and its tests.','Running the backend tests.']);
 assert.deepEqual([...steps[1].querySelectorAll('.dervo-record-calls li')].map(li=>[...li.children].map(node=>node.textContent)),[['Write','backend/project_ideas.py','+252']]);
 assert.equal(steps[1].querySelector('.dervo-record-why q').textContent,'Ideas are the group key for conversations.');
 assert.ok(steps[2].querySelector('li[data-failed]'));
});

test('the panel says the state once and sends review to the thread',async()=>{
 const {done}=mount();
 await screen.findByRole('heading',{name:'Ideas storage'});
 const panel=screen.getByRole('complementary',{name:'This run'});
 assert.equal(panel.querySelectorAll('.dv-st[data-accent]').length,1);
 assert.equal(within(panel).getByRole('status').textContent,'Waiting for your review');
 fireEvent.click(within(panel).getByRole('button',{name:'Review in the thread'}));
 assert.deepEqual(done.at(-1),['task','c-ideas','t-ideas']);
 const links=[...panel.querySelectorAll('.dervo-record-links li')].map(li=>li.textContent);
 assert.deepEqual(links,[`HomeStarted by Home from your message`.replace('Started',`${clock(at(1,37))} · Started`),
  `Live board${clock(at(2,57))} · Codex edited project_ideas.py after this run`,'Nothing has continued from this run yet.']);
 fireEvent.click(within(panel).getByRole('button',{name:'Live board'}));
 assert.deepEqual(done.at(-1),['record','t-board']);
 assert.deepEqual([...panel.querySelectorAll('.dervo-record-told p')].map(p=>[p.querySelector('span').textContent,p.querySelector('small').textContent]),[
  ['CLAUDE.md','From the repository, 4.1 KB'],['Tests run with python3 -m unittest from backend/','A note an earlier run saved'],
  ['2 hours ago: Connector picker (running)','Recent work, from Dervo’s records']]);
 fireEvent.click(within(panel).getByRole('button',{name:'project_ideas.py'}));
 assert.deepEqual(done.at(-1),['trace','backend/project_ideas.py']);
 assert.deepEqual([...panel.querySelectorAll('.dv-delta')].map(node=>node.textContent),['+252','+187']);
 fireEvent.click(within(panel).getByRole('button',{name:/Trace a file/}));
 assert.deepEqual(done.at(-1),['trace','backend/project_ideas.py']);
});

test('a settled run with a Dervo check and a commit reads from its record',async()=>{
 mount({...ideas,glyph:'done',ended:'Approved by you',canReview:false,access:'Chosen files',why:{you:{text:'fix it',at:at(1,0),where:'thread'},home:null,steers:[]},
  checks:{dervo:[{file:'test_sample.py',profile:'python-unittest',exit:0,stopped:null,at:at(1,2),text:'Dervo’s check ran test_sample.py: exit 0.'}],own:[],note:null},
  commit:{sha:'b17dfb3aa',short:'b17dfb3',at:at(1,10),reverted:false},links:[{kind:'continued_by',taskId:'t-next',name:'Timezone fix',text:'Timezone fix carried on from this'}],
  told:{...ideas.told,recorded:false}});
 await screen.findByRole('heading',{name:'Ideas storage'});
 assert.equal(document.querySelector('.dervo-record-compare li').textContent,`Dervo’s check ran test_sample.py at ${clock(at(1,2))}: exit 0.`);
 assert.equal(document.querySelector('.dervo-record-commit').textContent,`Committed as b17dfb3 at ${clock(at(1,10))}.`);
 assert.equal(document.querySelectorAll('.dv-st[data-accent]').length,0);
 assert.ok(screen.getByRole('button',{name:'Open the thread'}));
 assert.ok(!document.body.textContent.includes('Nothing has continued from this run yet.'));
 assert.match(screen.getByRole('region',{name:'What it was told'}).textContent,/Not recorded for this run/);
 assert.equal(document.querySelector('.dervo-record-asked dd').textContent,`fix it ${clock(at(1,0))}.`);
});

test('a record that can’t be read says why',async()=>{
 mount(new Error('That run isn’t in this project’s records.'));
 const alert=await screen.findByRole('alert');
 assert.match(alert.textContent,/This record couldn’t load\. That run isn’t in this project’s records\./);
});

// Merge security pass, 23 Sep, F2: the agent's words that started a run are quoted under It said, never beside You.
const PROPOSED='Purpose: Group the board by day\n\nAdd parse_day to utils/days.py so the board can group runs by day.';
const QUESTION='Should parse_date keep naive datetimes for the old callers?';
const why=node=>[...node.querySelectorAll('dt')].map(dt=>[dt.textContent,dt.nextElementSibling.textContent]);

test('a run started from a proposal says so, and the proposal is the other thread’s words',async()=>{
 const {done}=mount({...ideas,why:{you:null,home:null,steers:[],
  origin:{kind:'proposal',line:'Another thread proposed this, and you started it.',name:'Live board',taskId:'t-board',channelId:'c-board',quote:PROPOSED,quoteFrom:'Proposal from Live board',at:at(3,0)}}});
 await screen.findByRole('heading',{name:'Ideas storage'});
 const asked=document.querySelector('.dervo-record-asked');
 assert.deepEqual(why(asked),[['Started','Another thread proposed this, and you started it. From Live board.'],['It said',`${PROPOSED} Proposal from Live board`]]);
 assert.equal(asked.querySelector('dd.dervo-record-quote q').textContent,PROPOSED);
 assert.ok(!document.body.textContent.includes('wrote its own instruction'));
 fireEvent.click(within(asked).getByRole('button',{name:'Live board'}));
 assert.deepEqual(done.at(-1),['record','t-board']);
});

test('a run your answer started keeps your answer and its question apart',async()=>{
 mount({...ideas,why:{you:{text:'Keep the timezone',at:at(3,5),where:'answer',picked:true},home:null,
  origin:{kind:'answer',line:'You answered a question from Timezone fix.',name:'Timezone fix',taskId:null,channelId:'c-fix',quote:QUESTION,quoteFrom:'Its question',at:at(3,5)},
  steers:[{text:'No.',at:at(3,20),question:'Old callers too?',picked:false}]}});
 await screen.findByRole('heading',{name:'Ideas storage'});
 assert.deepEqual(why(document.querySelector('.dervo-record-asked')),[
  ['Started','You answered a question from Timezone fix.'],
  ['You',`Keep the timezone You picked one of its options, ${clock(at(3,5))}.`],
  ['It said',`${QUESTION} Its question`],
  ['Then',`You answered its question: No. at ${clock(at(3,20))}, while it worked.`],
  ['It said','Old callers too? Its question']]);
});

test('Home with no message of yours on record is Home’s words, not yours',async()=>{
 mount({...ideas,why:{you:null,home:null,steers:[],
  origin:{kind:'home',line:'Started by Home. Dervo’s records don’t hold the message it came from.',name:'Home',taskId:null,channelId:null,quote:'Build the ideas store.',quoteFrom:'Home wrote',at:at(1,37)}}});
 await screen.findByRole('heading',{name:'Ideas storage'});
 assert.deepEqual(why(document.querySelector('.dervo-record-asked')),[
  ['Started','Started by Home. Dervo’s records don’t hold the message it came from.'],['Home wrote','Build the ideas store.']]);
});
