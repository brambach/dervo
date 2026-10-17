// F1, 20 Sep: Catch up is one destination at two depths. Home's right pane is the glance, and "Go through these" in
// its header opens the sit-down - ProjectCatchup, the screen that shipped on 17 Sep and that nothing in the app could
// reach. There is no sidebar row: Home's whole job is catching you up, so a second entry would leave a new person
// asking what the difference is.
//
// The two depths are not the same thing at two sizes. The pane withholds actions; the screen is a queue of decisions,
// because a 328 px column is a bad place to approve a commit. These tests hold both halves of that.
//
// They drive the real Home and the real ProjectCatchup through the real button, because the questions tray let six
// bugs through last week behind tests that only ever called the reducer.
import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLTextAreaElement','HTMLButtonElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {useState}=React;
const {render,fireEvent,waitFor,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {Home,PANE_TITLE}=await import('./Home.tsx');
const {ProjectCatchup}=await import('./ProjectCatchup.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const NOW=Math.floor(Date.now()/1000);
const conversation={id:'c1',name:'Ideas storage',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'};
// A second decision and one run, so the screen has a queue to order and something that asks nothing of you below it.
const stopped={id:'c2',name:'Beta plan screen',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'};
const working={id:'c3',name:'Connector picker',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',
 activity:{status:'running',running:true,unread:0,since:NOW-1300,step:'Adding the picker to Project settings',
  goal:'Let a project choose its connectors',why:'The choices are hard-coded today',next:'Run the checks'}};
// And one that landed while you were away, so "What changed" has something in it.
const landed={id:'c4',name:'Live board',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'};
const conversations=[conversation,stopped,working,landed];
const pickupItem={id:'c1',name:'Ideas storage',private:false,seen:NOW-4000,moved:NOW-2400,kind:'review',status:'finished, needs your review',
 needs:true,asked:'Store ideas per project.',line:'Finished. 2 files changed.',did:'Finished. 2 files changed.',now:null,
 next:'Should an archived idea keep its threads?',next_by:'agent',running_since:null,changed:true,question:true,failed:false,checks:'passed',handoff:null};
const pickup={since:NOW-2400,lead:'**Ideas storage** has a question for you.',middle:null,more:0,at:NOW,items:[pickupItem],
 counts:{threads:3,running:1,moved:1,finished:1,stopped:1,asking:1}};
const catchupRows=[
 {id:'t1',kind:'task',channelId:'c1',channelName:'Ideas storage',snippet:'',source:{},title:'Store ideas per project',
  status:'ready_for_review',humanApprovalRequired:true,isNew:true,completedAt:NOW-2400},
 {id:'t2',kind:'task',channelId:'c2',channelName:'Beta plan screen',snippet:'',source:{},title:'Make the Releases page update itself',
  status:'blocked',runState:'failed',blocker:'The Releases feed timed out twice.',isNew:true,askedAt:NOW-5200},
 {id:'t3',kind:'task',channelId:'c4',channelName:'Live board',snippet:'',source:{},title:'Warn when two runs edit the same file',
  status:'completed',isNew:true,completedAt:NOW-900,changedFiles:['board.ts'],changedFileCount:1,testExitStatus:0},
];

/** One host for both depths. Anything neither screen needs throws, the way the real host refuses an op it doesn't have. */
function host(){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push([operation,args]);
  if(operation==='project_pickup')return pickup;
  if(operation==='project_catchup')return {results:catchupRows,cursor:null,incomplete:false,since:NOW-3600,lessonsPending:0,skillsPending:0};
  throw new Error('no fixture '+operation);
 }};
 return calls;
}

/** Home and Catch up wired the way ConnectedApp wires them: one panel at a time, and the pane's control opens the
 * screen over it. The shell's own wiring is held to this by the last test in this file. */
function TwoDepths({onOpenTask=()=>{}}={}){
 const [open,setOpen]=useState(false);
 return React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  open
   ?React.createElement(ProjectCatchup,{projectId:'proj',profileId:'p1',projectName:'dervo app',conversations,
     lastConversation:null,open:true,returnLabel:'Back to Home',onClose:()=>setOpen(false),onOpenConversation(){},onOpenTask,
     onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}})
   :React.createElement(Home,{projectId:'proj',profileId:'p1',projectName:'dervo app',conversations,
     onOpen(){},onHandOff(){},onChanged(){},onCatchUp:()=>setOpen(true)}));
}
const before=(first,second)=>!!(first.compareDocumentPosition(second)&Node.DOCUMENT_POSITION_FOLLOWING);
// Home's pane is the section Home draws (.dv-now); the full screen is a region with the same name, so the pane is found
// by its own class once both could be in the tree.
const homePane=()=>document.querySelector('section.dv-now');

test('the pane carries one name, Catch up, whatever the moment is',async()=>{
 host();
 render(React.createElement(TwoDepths));
 const pane=await screen.findByRole('region',{name:'Catch up'});
 assert.equal(PANE_TITLE,'Catch up');
 assert.equal(pane.querySelector('.dv-now-head .dv-display-2').textContent,'Catch up');
 assert.equal(screen.queryByRole('region',{name:'Where you left off'}),null,'the old name is gone from the pane');
 assert.equal(screen.queryByRole('region',{name:'Happening now'}),null,'and so is the name it used to take mid-run');
 // Status once: the moment still gets said, in the caption under the same name.
 assert.equal(pane.querySelector('.dv-now-head .dv-caption').textContent,'1 running · 1 finished · 1 stopped');
});

test('the control sits on Waiting on you, names the payoff rather than a size, and adds no second accent',async()=>{
 host();
 render(React.createElement(TwoDepths));
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const head=pane.querySelector('.dv-now-head');
 // "Go through these" left the page head (Bryce, 8 Oct). The queue opens from the bucket it walks.
 const expand=within(pane).getByRole('button',{name:'Open in Catch up'});
 assert.ok(within(pane).getByRole('region',{name:'Waiting on you'}).querySelector('.dv-bucket-bar').contains(expand),'on Waiting on you’s head, not buried in the rows');
 assert.equal(head.contains(expand),false,'and not in the page head');
 // "Open in full" promised the same thing bigger, which is not what is on the other side. It names the payoff.
 assert.equal(expand.textContent,'Open in Catch up');
 assert.doesNotMatch(expand.textContent,/full|expand|bigger/i,'nothing that promises a wider pane');
 assert.ok(expand.classList.contains('dv-btn-ghost'),'ghost like every other quiet control');
 assert.equal(pane.querySelectorAll('[data-accent]').length,1,'the one accent is still the open card’s glyph');
 assert.equal(head.querySelectorAll('[data-accent]').length,0,'the control takes none of it');
});

test('the control opens the full Catch up screen, and closing it gives the pane back',async()=>{
 const calls=host();
 render(React.createElement(TwoDepths));
 const pane=await screen.findByRole('region',{name:'Catch up'});
 fireEvent.click(within(pane).getByRole('button',{name:'Open in Catch up'}));

 // The real ProjectCatchup, reading the real catch-up op: its title, the project it is about as the kicker, and its
 // briefing (design review 23 Sep, item 5: the title is Catch up in the display serif, like every other page).
 await waitFor(()=>assert.match(screen.getByTestId('catchup-stand').textContent,/need you/));
 assert.equal(screen.getByRole('heading',{level:1}).textContent,'Catch up');
 assert.equal(document.querySelector('.dervo-catchup-kicker').textContent,'dervo app');
 assert.ok(screen.getByTestId('catchup-next'),'the one thing to do next');
 assert.deepEqual(calls.filter(([operation])=>operation==='project_catchup'),
  [['project_catchup',{projectId:'proj',channelId:'',profileId:'p1',scope:'project',limit:30}]],'one read, for this project');
 assert.equal(homePane(),null,'one screen at a time: the pane steps aside');

 fireEvent.click(screen.getByRole('button',{name:'Back to Home'}));
 await waitFor(()=>assert.ok(homePane(),'and closing it gives the pane back'));
});

test('the screen is a queue of decisions: the next step is the one lifted row, the decisions keep their actions, and what runs reads lighter below',async()=>{
 host();
 render(React.createElement(TwoDepths));
 const pane=await screen.findByRole('region',{name:'Catch up'});
 fireEvent.click(within(pane).getByRole('button',{name:'Open in Catch up'}));
 await waitFor(()=>assert.equal(document.querySelectorAll('.dervo-catchup-section').length,3));

 const next=screen.getByTestId('catchup-next');
 const needs=screen.getByRole('region',{name:/Needs you/}),running=screen.getByRole('region',{name:/Running now/});
 const changed=screen.getByRole('region',{name:/What changed|Latest results/});
 const foot=document.querySelector('.dervo-catchup-foot');
 assert.ok(before(needs,running)&&before(running,changed)&&before(changed,foot),
  'the queue, then context, then what landed, then the way out');

 // The queue is Home's sheet of rows. Only the first decision is lifted onto a card, and it holds the next step: the
 // page's one pine pill. The rest are rows that keep their own action and Dismiss (design review 23 Sep, item 5).
 const queue=needs.querySelector('.dv-sheet');
 const rows=[...queue.querySelectorAll('[data-kind="needs"]')];
 assert.equal(rows.length,2);
 assert.deepEqual(rows.map(row=>row.classList.contains('dv-open')),[true,false],'one lifted row, no stack of look-alike cards');
 assert.ok(rows[0].contains(next)&&rows[0].hasAttribute('data-lead'),'the next step lives on the row it names');
 assert.ok(next.classList.contains('dv-btn-primary'));
 assert.match(rows[0].querySelector('.dv-row-name').textContent,/Ideas storage/);
 for(const row of rows)assert.ok(within(row).getByRole('button',{name:'Dismiss'}),'each decision can be cleared from here');
 assert.ok(within(rows[0]).getByRole('button',{name:'Approve result'}),'and acted on without leaving');
 assert.ok(within(rows[1]).getByRole('button',{name:'See what stopped it'}));

 // Rust means "this needs you" and nothing else, so it never lands on a check or a cross. A result waiting for review
 // is a done glyph, as on Home and in the sidebar, which leaves the page with no accent at all here.
 assert.deepEqual(rows.map(row=>row.querySelector('.dv-st').dataset.state),['done','failed']);
 assert.equal(document.querySelectorAll('[data-testid="project-catchup"] .dv-st[data-accent]').length,0);

 // A run asks nothing of you, so it is a compact row you read, with the agent's own words and no decision to make.
 const runs=[...running.querySelectorAll('.dervo-catchup-item')];
 assert.equal(runs.length,1);
 assert.equal(runs[0].querySelectorAll('dl').length,0,'the definition list went: the step is just a line now');
 assert.equal(runs[0].querySelectorAll('.dervo-catchup-actions').length,0,'nothing to decide, so no action row');
 assert.equal(runs[0].querySelectorAll('button').length,1,'the row itself opens the thread');
 assert.match(runs[0].textContent,/Adding the picker to Project settings/,'what it is doing now');
 assert.match(runs[0].textContent,/WhyThe choices are hard-coded today/,'and its own words, which the pane never has');
 assert.doesNotMatch(runs[0].textContent,/Let a project choose its connectors/,'the goal went: the request already says it');
});

test('without a host that can read the project’s records, no control is drawn',async()=>{
 host();
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(Home,{projectId:'proj',profileId:'p1',projectName:'dervo app',conversations:[conversation],
   onOpen(){},onHandOff(){},onChanged(){}})));
 const pane=await screen.findByRole('region',{name:'Catch up'});
 assert.equal(within(pane).queryByRole('button',{name:'Go through these'}),null,'nothing on the other side, so nothing to click');
});

test('the shell hands Home the control and opens Catch up with it, and adds no sidebar row',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/onCatchUp=\{projectRecordsReady\?trigger=>projectAction\('catchup',trigger\):undefined\}/,
  'Home gets the control only when the host can read this project’s records');
 assert.match(shell,/setSearchMode\(action==='search'\?'project':'catchup'\)/,'and projectAction(catchup) is what opens the screen');
 assert.match(shell,/searchMode==='catchup'&&!catchupAll&&projectRecordsReady&&selectedProject&&<ProjectCatchup/,
  'which is the condition ProjectCatchup mounts under');
 const sidebar=readFileSync(new URL('./DervoSidebar.tsx',import.meta.url),'utf8');
 assert.equal(/onProjectAction\('catchup'|action:'catchup'|=>action\('catchup'/.test(sidebar),false,
  'no sidebar row: Home is the one way in (decisions.md, 20 Sep)');
 // And the chrome says where you are: Catch up is Home's second depth, so Home's row stays current under it.
 assert.match(shell,/searchOpen\?\(searchMode==='catchup'\?'orchestrator':'search'\)/,
  'the sidebar keeps Home current while Catch up is open, instead of naming nowhere');
 assert.match(shell,/returnLabel=\{current\?'Back to thread':'Back to Home'\}/,'and the way out names where it goes');
});
