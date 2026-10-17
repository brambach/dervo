import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLButtonElement','HTMLDetailsElement','MutationObserver','Node','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent'])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ImportHistory,useHistoryOffer,sortingSteps,pace}=await import('./ImportHistory.tsx');
const {blankState,foundLede,sourcesLabel,sortingLead,archivedLine,setAsideLine,collapseLine,agoLabel,historyDeclined,rememberHistoryDeclined}=await import('./historyImport.ts');
const {historyFixture,historyLookFixture}=await import('./historyImportFixture.ts');
const {Home,showArchived}=await import('./Home.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();});

// Playback in a few milliseconds so the tests read the screens, not the clock.
Object.assign(pace,{stream:40,step:2,count:20,settle:10,workstream:3,thread:2,gap:2,answered:10});
const NOW=Math.floor(Date.now()/1000);
const look=historyLookFixture(NOW);
const found=historyFixture(NOW);

/** Nothing on screen matches. Never assert.equal(element,null): when it fails, node:assert inspects the element, and a
 * rendered one carries React's fiber, so the message walks the whole app and runs to gigabytes instead of failing. A
 * waitFor's first, failing try did that here and crashed the test runner. */
function absent(element,message){assert.ok(element===null,message??`expected nothing, found ${element?.outerHTML.slice(0,200)}`);}

/** The host as the app sees it: every local_request lands here and is logged. */
function host(answers){
 answers={history_preview:()=>({candidates:[{sessionId:'session-a',name:'Recent work',source:'claude',last_active:NOW,reason:'recent',recommended:true,excerpt:'Fix the login page'}],limit:50}),...answers};
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  if(command!=='local_request')throw new Error(`${command} isn't available here`);
  const {operation,args:a}=args;calls.push([operation,a]);
  const answer=answers[operation];
  if(answer===undefined)throw new Error(`Unknown operation ${operation}`);
  const result=typeof answer==='function'?answer(a):answer;
  if(result instanceof Error)throw result;
  return result;
 },transformCallback:()=>0};
 return calls;
}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(props={})=>{
 const calls={onDecline:0,onDone:0};
 render(React.createElement(QueryClientProvider,{client:client()},React.createElement(ImportHistory,{projectId:'proj',projectName:'dervo app',look,
  onDecline(){calls.onDecline++;},onDone(){calls.onDone++;},...props})));
 return calls;
};
async function chooseAndImport(){
 fireEvent.click(screen.getByRole('button',{name:'Choose conversations'}));
 fireEvent.click(await screen.findByRole('button',{name:'Import 1 conversation'}));
}

/** Read them, wait for the sorting to finish, and take the one thing that needs you. */
async function toAsk(){
 await chooseAndImport();
 const next=await screen.findByRole('button',{name:'One thing needs you'},{timeout:3000});
 await waitFor(()=>assert.equal(next.disabled,false));
 fireEvent.click(next);
}

test('a workspace is blank only when the live list and the archived list are both empty',()=>{
 const live=[{id:'c1'}],archived=[{id:'a1',archived_at:1}];
 assert.equal(blankState(undefined,undefined),'loading','no live list yet');
 assert.equal(blankState([],undefined),'loading','live is empty but the archived list hasn’t answered');
 assert.equal(blankState([],[]),'blank');
 assert.equal(blankState([],archived),'archived','forty archived threads is not a new project');
 assert.equal(blankState(live,undefined),'live','a live thread settles it without waiting for the archived list');
 assert.equal(blankState(live,archived),'live');
});

test('the copy names the folder, the count and how far back it goes, and stays plain',()=>{
 const lede=foundLede(look,NOW);
 assert.equal(lede.count,'25 conversations');
 assert.equal(lede.where,'~/dervo/app');
 assert.match(lede.span,/^the first from [\w ]+ and the last 16 min ago$/);
 assert.equal(sourcesLabel(['claude','codex']),'Claude Code and Codex');
 assert.equal(sourcesLabel(['claude']),'Claude Code');
 assert.equal(sourcesLabel([]),'Claude Code and Codex');
 assert.equal(agoLabel(NOW-30,NOW),'just now');
 assert.equal(agoLabel(NOW-86400,NOW),'yesterday');
 assert.deepEqual(sortingLead(found),{bold:'25 threads, 4 workstreams.',rest:'One was made because nothing existing fitted. Everything else went to a group by name or topic.'});
 // The third number is the workstream count, never the open-thread count: on a real folder nearly every recent
 // session reads as open, and 22 of 22 is no collapse at all.
 assert.equal(collapseLine(found.counts),'25 conversations · 41 sessions · 4 things you’re actually working on');
 assert.equal(setAsideLine(found),'Set aside 16 sessions with nothing to pick up');
 assert.equal(setAsideLine({...found,set_aside:0}),'Kept every session');
 // The host's own rung names (project_ideas.file_by_rules): the topic step can make a workstream too, and a second
 // read of the same folder makes nothing and says so.
 const real={...found,workstreams:[{...found.workstreams[0],rule:'topic, new workstream'},{...found.workstreams[1],rule:'at the cap, most recent'},{...found.workstreams[3],rule:'new workstream'}]};
 assert.equal(sortingLead(real).rest,'2 were made because nothing existing fitted. Everything else went to a group by name or topic.');
 assert.deepEqual(sortingLead({...found,workstreams:[],already:22}),{bold:'Nothing new.',rest:'22 threads were already here.'});
 // A first import: every workstream is new, so the lead says so once and doesn't speak of "everything else".
 const first={...found,workstreams:found.workstreams.map(ws=>({...ws,rule:'new workstream'}))};
 assert.equal(sortingLead(first).rest,'All 4 were made because nothing existing fitted.');
 assert.equal(sortingLead({...first,workstreams:first.workstreams.slice(0,1)}).rest,'It was made because nothing existing fitted.');
 assert.equal(setAsideLine({...found,set_aside:22,already:22}),'Set aside 22 sessions with nothing to pick up, 22 already here');
 assert.deepEqual(archivedLine(12),{what:'Every thread here is archived, 12 of them.',then:'Restore one from the sidebar’s Archived list, or start a new thread.'});
 assert.equal(archivedLine(1).what,'Every thread here is archived, one of them.');
 for(const file of ['historyImport.ts','ImportHistory.tsx','historyImportFixture.ts','ImportHistory.css'])
  assert.equal(readFileSync(new URL(`./${file}`,import.meta.url),'utf8').includes('\u2014'),false,`${file}: no em dashes`);
});

test('sorting lands each workstream’s head, then its threads, and folds the one before',()=>{
 const steps=sortingSteps(found);
 assert.deepEqual(steps.slice(0,3).map(s=>s.kind),['head','thread','thread']);
 const firstFold=steps.findIndex(s=>s.kind==='fold');
 assert.equal(steps[firstFold].ws,0);
 assert.equal(steps[firstFold+1].kind,'head');
 assert.equal(steps[firstFold+1].ws,1);
 assert.equal(steps.at(-1).kind,'fold');
 assert.equal(steps.at(-1).ws,3,'the last workstream folds too');
 assert.equal(steps.filter(s=>s.kind==='thread').length,25);
 for(let i=1;i<steps.length;i++)assert.ok(steps[i].at>=steps[i-1].at,'in time order');
});

test('Found is the offer: nothing is read until you say so, and declining reads nothing at all',async()=>{
 const calls=host({history_read:()=>found});
 const outcome=mount();
 assert.ok(screen.getByRole('heading',{name:'You’ve been here before'}));
 const lede=screen.getByText(/from Claude Code and Codex in/);
 assert.match(lede.textContent,/^There are 25 conversations from Claude Code and Codex in ~\/dervo\/app, the first from/);
 assert.match(screen.getByText(/Nothing is uploaded and no model is called/).textContent,/already wrote to this Mac/);
 assert.equal(calls.length,0,'no host call before the offer is taken');
 fireEvent.click(screen.getByRole('button',{name:'Start empty instead'}));
 assert.equal(outcome.onDecline,1);
 assert.equal(calls.length,0);
});

test('Read them: the reader’s result plays back, workstreams land with their rule and fold, and the question ends on Home',async()=>{
 const calls=host({history_read:()=>found,coding_request:a=>({id:'task-1',state:'working'})});
 const outcome=mount();
 await chooseAndImport();
 await waitFor(()=>assert.equal(calls.filter(([op])=>op==='history_read').length,1));
 assert.deepEqual(calls.find(([op])=>op==='history_read')[1],{projectId:'proj',sessionIds:['session-a']});
 // Reading: the stream, with what was set aside struck through and what was kept beside it.
 await screen.findByText('Set aside 16 sessions with nothing to pick up');
 assert.equal(document.querySelectorAll('.dv-import-l[data-drop]').length,16);
 assert.equal(document.querySelectorAll('.dv-import-k').length,8,'the newest eight kept; the rest gave way at the top');
 assert.equal(document.querySelector('.dv-import-k:last-child').textContent,'X launch posts');
 assert.ok(screen.getByText('being worked on'));
 // Sorting: every workstream with its rule, then folded to a line.
 await screen.findByRole('heading',{name:'Sorting'});
 await screen.findByText('25 threads, 4 workstreams.');
 const rules=[...document.querySelectorAll('.dv-import-rule')].map(node=>node.textContent);
 assert.deepEqual(rules,['name overlap','name overlap','topic','new workstream']);
 assert.equal(document.querySelector('.dv-import-rule[data-new]').textContent,'new workstream');
 assert.equal(document.querySelectorAll('.dv-import-ws[data-folded]').length,4,'each workstream folds to a line');
 assert.deepEqual([...document.querySelectorAll('.dv-import-fold')].map(node=>node.textContent),['9 threads, 1 open','6 threads, 1 open','7 threads','3 threads, 1 open']);
 assert.match(screen.getByText(/made by a rule, not a model/).textContent,/Move anything and it stays moved/);
 assert.equal(document.querySelector('.dv-import-proj-head code').textContent,'~/dervo/app');
 fireEvent.click(screen.getByRole('button',{name:'One thing needs you'}));
 // Answer: the app's own row, the facts, one keystroke.
 await screen.findByRole('heading',{name:'One thing was left unanswered'});
 assert.match(screen.getByText(/It stopped here and waited/).textContent,/^From Releases and previews, [\w ]+ at \d/);
 assert.ok(screen.getByText('Preview builds and the plan'));
 assert.equal(document.querySelector('.dv-st[data-state="needs"][data-accent]')!==null,true,'the one accent');
 assert.equal(screen.getByText('Needs you').getAttribute('data-accent'),'true');
 assert.equal(screen.getAllByRole('button',{name:/^\d/}).length,3);
 fireEvent.keyDown(window,{key:'2'});
 await screen.findByText('Answered. Picking the thread back up where it stopped.');
 const sent=calls.find(([op])=>op==='coding_request');
 assert.equal(sent[1].channelId,found.question.thread_id);
 assert.equal(sent[1].text,'Yes, but mark them as previews in the list.');
 assert.equal(sent[1].projectId,'proj');
 assert.equal(screen.getByText('Running').getAttribute('data-state'),'running');
 assert.equal(document.querySelector('.dv-import-choice[data-picked] kbd').textContent,'2');
 await waitFor(()=>assert.equal(outcome.onDone,1));
 absent(screen.queryByText(/Congratulations|All set|You’re ready/),'no congratulations screen');
});

test('with nothing left unanswered the sorting screen goes straight to Home',async()=>{
 host({history_read:()=>({...found,question:null})});
 const outcome=mount();
 await chooseAndImport();
 const open=await screen.findByRole('button',{name:'Open Home'});
 await waitFor(()=>assert.equal(open.disabled,false));
 fireEvent.click(open);
 assert.equal(outcome.onDone,1);
});

test('Not now leaves the question in its thread and still lands on Home',async()=>{
 const calls=host({history_read:()=>found});
 const outcome=mount();
 await toAsk();
 fireEvent.click(await screen.findByRole('button',{name:'Not now'}));
 assert.equal(outcome.onDone,1);
 assert.equal(calls.some(([op])=>op==='coding_request'),false);
});

test('a question without choices takes a typed answer',async()=>{
 const calls=host({history_read:()=>({...found,question:{...found.question,choices:[]}}),coding_request:()=>({id:'t',state:'working'})});
 mount();
 await toAsk();
 const field=await screen.findByRole('textbox',{name:'Your answer'});
 fireEvent.change(field,{target:{value:'No, never.'}});
 fireEvent.click(screen.getByRole('button',{name:'Answer'}));
 await screen.findByText(/Picking the thread back up/);
 assert.equal(calls.find(([op])=>op==='coding_request')[1].text,'No, never.');
});

test('a read that fails says what happened, then what to do, and Start empty still works',async()=>{
 host({history_read:()=>new Error('the transcript folder couldn’t be opened')});
 const outcome=mount();
 await chooseAndImport();
 const alert=await screen.findByRole('alert');
 assert.equal(alert.textContent.startsWith('Couldn’t read the history in ~/dervo/app. the transcript folder couldn’t be opened'),true);
 assert.ok(within(alert).getByRole('button',{name:'Try again'}));
 fireEvent.click(within(alert).getByRole('button',{name:'Start empty instead'}));
 assert.equal(outcome.onDecline,1);
});

test('an answer the host refuses stays on the question with the reason',async()=>{
 host({history_read:()=>found,coding_request:()=>new Error('No agent is connected for this project.')});
 const outcome=mount();
 await toAsk();
 fireEvent.click(await screen.findByRole('button',{name:/^1/}));
 const alert=await screen.findByRole('alert');
 assert.equal(alert.textContent,'Couldn’t send your answer. No agent is connected for this project.');
 assert.equal(outcome.onDone,0);
 assert.ok(screen.getByText('Needs you'));
});

// ---------------------------------------------------------------- the entry point
function Probe({projectId,live,seen}){
 const offer=useHistoryOffer(projectId,live);
 seen.push(offer);
 return React.createElement('div',{'data-testid':'probe','data-state':offer.state,'data-look':offer.look?'yes':'no','data-archived':offer.archived});
}
const probe=(live,answers)=>{
 const calls=host(answers),seen=[];
 render(React.createElement(QueryClientProvider,{client:client()},React.createElement(Probe,{projectId:'proj',live,seen})));
 return {calls,seen,node:()=>screen.getByTestId('probe')};
};

test('the offer shows only for a project with no threads and none archived, once the host has said what’s on disk',async()=>{
 const {calls,node}=probe([],{conversations_archived:[],history_look:look});
 await waitFor(()=>assert.equal(node().dataset.look,'yes'));
 assert.equal(node().dataset.state,'blank');
 assert.deepEqual(calls.map(([op])=>op),['conversations_archived','history_look'],'the archived list is read before anything is looked at');
});

test('an offer that was taken holds while the reader fills the live list, and lets go when the flow finishes',async()=>{
 const calls=host({conversations_archived:[],history_look:look});
 const seen=[];
 const view=render(React.createElement(QueryClientProvider,{client:client()},React.createElement(Probe,{projectId:'proj',live:[],seen})));
 await waitFor(()=>assert.equal(screen.getByTestId('probe').dataset.look,'yes'));
 // The reader made the first conversation: the live list is no longer empty, and the offer stays.
 const made=[{id:'c1',name:'Catch up as a queue',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'}];
 view.rerender(React.createElement(QueryClientProvider,{client:client()},React.createElement(Probe,{projectId:'proj',live:made,seen})));
 assert.equal(screen.getByTestId('probe').dataset.look,'yes');
 assert.equal(screen.getByTestId('probe').dataset.state,'live');
 // The flow finished: the offer is gone and stays gone, with the live list full.
 seen.at(-1).finish();
 await waitFor(()=>assert.equal(screen.getByTestId('probe').dataset.look,'no'));
 assert.equal(calls.filter(([op])=>op==='history_look').length,1,'looked once');
});

test('a project whose threads are all archived is not offered a read: it says how many are archived instead',async()=>{
 const {calls,node}=probe([],{conversations_archived:[{id:'a1',archived_at:1},{id:'a2',archived_at:2},{id:'a3',archived_at:3}],history_look:look});
 await waitFor(()=>assert.equal(node().dataset.state,'archived'));
 assert.equal(node().dataset.look,'no');
 assert.equal(node().dataset.archived,'3');
 assert.equal(calls.some(([op])=>op==='history_look'),false,'nothing on disk is even looked at');
});

test('a project with live threads asks the host nothing',async()=>{
 const {calls,node}=probe([{id:'c1',name:'Overview',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'}],{});
 assert.equal(node().dataset.state,'live');
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.length,0);
});

test('a host without the reader, or one that finds nothing, leaves a normal empty project',async()=>{
 const refused=probe([],{conversations_archived:[],history_look:()=>new Error('Unknown operation history_look')});
 await waitFor(()=>assert.equal(refused.calls.filter(([op])=>op==='history_look').length,1));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(refused.node().dataset.look,'no');
 assert.equal(refused.node().dataset.state,'blank');
 cleanup();
 const nothing=probe([],{conversations_archived:[],history_look:null});
 await waitFor(()=>assert.equal(nothing.calls.filter(([op])=>op==='history_look').length,1));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(nothing.node().dataset.look,'no');
});

test('saying no is remembered for that project and the offer stays away',async()=>{
 rememberHistoryDeclined('proj');
 assert.equal(historyDeclined('proj'),true);
 assert.equal(historyDeclined('other'),false);
 const {calls,node}=probe([],{conversations_archived:[],history_look:look});
 await waitFor(()=>assert.equal(node().dataset.state,'blank'));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.some(([op])=>op==='history_look'),false);
 assert.equal(node().dataset.look,'no');
});

// ---------------------------------------------------------------- Home, when everything is archived
const empty={since:null,lead:'',middle:null,items:[],more:0,at:NOW,counts:{threads:0,running:0,moved:0,finished:0,stopped:0,asking:0}};
const quietHome={project_pickup:empty,orchestrator_list:{entries:[],thinking:false,statuses:{}},orchestrator_arrive:{entries:[],thinking:false,statuses:{}},
 dervo_account_status:{method:'local',state:'signed_in'},status:{},push_preview:()=>new Error('Unknown operation push_preview'),image_drafts:()=>new Error('no images')};
const homeAt=(props)=>render(React.createElement(QueryClientProvider,{client:client()},
 React.createElement(Home,{projectId:'proj',profileId:'p1',projectName:'dervo app',conversations:[],onOpen(){},onHandOff(){},onChanged(){},...props})));

test('Home’s pane says every thread is archived, how many, and opens the sidebar’s list on Show archived',async()=>{
 host(quietHome);
 document.body.insertAdjacentHTML('afterbegin','<details data-testid="archived-conversations"><summary tabindex="0">Archived</summary><ul></ul></details>');
 homeAt({archived:12});
 const quiet=await screen.findByTestId('home-archived');
 assert.equal(quiet.querySelector('.dv-now-lead').textContent,'Every thread here is archived, 12 of them.');
 assert.equal(screen.getByRole('region',{name:'Catch up'}).querySelector('.dv-now-head .dv-caption').textContent,'12 archived','the caption agrees with the line');
 assert.equal(quiet.querySelector('.dv-caption').textContent,'Restore one from the sidebar’s Archived list, or start a new thread.');
 const details=document.querySelector('[data-testid="archived-conversations"]');
 assert.equal(details.open,false);
 fireEvent.click(within(quiet).getByRole('button',{name:'Show archived'}));
 assert.equal(details.open,true);
 assert.equal(document.activeElement,details.querySelector('summary'));
 details.remove();
});

test('Home’s pane is the plain "Nothing yet" for a blank project, and only the first visit after a read carries the arrival mark',async()=>{
 host(quietHome);
 homeAt({archived:0});
 await screen.findByText('Nothing yet.');
 absent(screen.queryByTestId('home-archived'));
 assert.equal(screen.getByRole('region',{name:'Home'}).hasAttribute('data-arrived'),false);
 cleanup();
 host(quietHome);
 homeAt({arrived:true});
 assert.equal(screen.getByRole('region',{name:'Home'}).getAttribute('data-arrived'),'true');
 assert.equal(showArchived(),false,'with no sidebar list there is nothing to open, and nothing breaks');
});

test('a first import makes every workstream new, so the rule column stays away and the lead says it once',async()=>{
 const first={...found,workstreams:found.workstreams.map(ws=>({...ws,rule:'new workstream'})),question:null};
 host({history_read:()=>first});
 mount();
 await chooseAndImport();
 await screen.findByRole('heading',{name:'Sorting'});
 await screen.findByText('25 threads, 4 workstreams.');
 assert.equal(document.querySelectorAll('.dv-import-rule').length,0,'four rows saying "new workstream" explain nothing');
 assert.match(screen.getByText(/All 4 were made/).textContent,/All 4 were made because nothing existing fitted\.$/);
 assert.doesNotMatch(document.body.textContent,/Everything else/);
 // Mixed rules keep the column: the second import is where it earns its place.
 cleanup();
 host({history_read:()=>({...found,question:null})});
 mount();
 await chooseAndImport();
 await screen.findByText('25 threads, 4 workstreams.');
 assert.equal(document.querySelectorAll('.dv-import-rule').length,4);
});

test('a project holding only the threads Dervo made for itself is still blank', () => {
  // first_run makes an overview thread and a starter thread for every new project. Before the host marked them,
  // a project made the normal way read as live and was never offered its own history (Bryce, 21 Sep, on ~/dd).
  const made = [{id: 'a', origin: 'overview'}, {id: 'b', origin: 'starter'}];
  assert.equal(blankState(made, []), 'blank');
  assert.equal(blankState([...made, {id: 'c'}], []), 'live', 'one thread of your own makes it live');
  assert.equal(blankState([], made), 'blank', 'archived overview is not work you put away');
  assert.equal(blankState([], [...made, {id: 'c'}]), 'archived');
});

 test('choosing history creates nothing until the selected list is confirmed',async()=>{
 const calls=host({history_read:()=>found});mount();
 fireEvent.click(screen.getByRole('button',{name:'Choose conversations'}));
 const checkbox=await screen.findByRole('checkbox');
 assert.equal(calls.some(([op])=>op==='history_read'),false);
 fireEvent.click(checkbox);
 assert.equal(screen.getByRole('button',{name:'Import 0 conversations'}).disabled,true);
 fireEvent.click(checkbox);
 fireEvent.click(screen.getByRole('button',{name:'Import 1 conversation'}));
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='history_read')[1].sessionIds,['session-a']));
 });

// NEXT-UP #37: Found counted one conversation and the picker said there were none, because the rules couldn't name it.
const UNNAMED={sessionId:'session-u',name:'',unnamed:true,source:'claude',last_active:NOW,reason:'recent',recommended:true,excerpt:'Look at ~/code/app/src/main.ts and tell me why it fails',toolRun:false,ranBy:null};
test('a conversation the rules couldn’t name is in the picker, says so, and comes in with the name you give it',async()=>{
 const calls=host({history_preview:()=>({candidates:[UNNAMED],limit:50,toolRuns:0}),history_read:()=>found});
 mount();
 fireEvent.click(screen.getByRole('button',{name:'Choose conversations'}));
 const box=await screen.findByRole('checkbox',{name:/Couldn’t safely generate a name/});
 assert.equal(box.checked,true,'open, so ticked like any other');
 absent(screen.queryByText('There aren’t any new conversations to bring in.'));
 assert.ok(screen.getByText(/main\.ts and tell me why/),'its first message says what it is');
 absent(screen.queryByRole('checkbox',{name:/run by a tool/i}),'no tool runs here, so no option');
 const name=screen.getByRole('textbox',{name:'Name for this conversation'});
 assert.match(name.placeholder,/^Unnamed conversation, /);
 fireEvent.change(name,{target:{value:'  Main crash  '}});
 assert.equal(box.checked,true,'typing a name doesn’t untick it');
 fireEvent.click(screen.getByRole('button',{name:'Import 1 conversation'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='history_read')));
 assert.deepEqual(calls.find(([op])=>op==='history_read')[1],{projectId:'proj',sessionIds:['session-u'],names:{'session-u':'Main crash'}});
});

test('an unnamed conversation you don’t name goes with a plain dated name',async()=>{
 const calls=host({history_preview:()=>({candidates:[UNNAMED],limit:50,toolRuns:0}),history_read:()=>found});
 mount();
 fireEvent.click(screen.getByRole('button',{name:'Choose conversations'}));
 fireEvent.click(await screen.findByRole('button',{name:'Import 1 conversation'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='history_read')));
 assert.match(calls.find(([op])=>op==='history_read')[1].names['session-u'],/^Unnamed conversation, \S/,'a plain name with its date, in the window’s date format');
});

// NEXT-UP #38: tool-run sessions, off by default, an explicit choice here, and labelled as the tool's.
const TOOL={sessionId:'session-t',name:'Nightly invoice reconciliation',source:'codex',last_active:NOW,reason:'recent',recommended:true,excerpt:'Run the nightly invoice reconciliation',toolRun:true,ranBy:'exec'};
test('tool-run sessions stay out until you tick the option, then come in labelled as the tool’s',async()=>{
 const own={sessionId:'session-a',name:'Recent work',source:'claude',last_active:NOW,reason:'recent',recommended:true,excerpt:'Fix the login page',toolRun:false,ranBy:null};
 const calls=host({history_preview:a=>({candidates:a.includeToolRuns?[own,TOOL]:[own],limit:50,toolRuns:1}),history_read:()=>found});
 mount();
 fireEvent.click(screen.getByRole('button',{name:'Choose conversations'}));
 const option=await screen.findByRole('checkbox',{name:/Include sessions run by a tool \(1\)/});
 assert.equal(option.checked,false,'off by default');
 assert.deepEqual(calls.filter(([op])=>op==='history_preview').map(([,a])=>a),[{projectId:'proj'}]);
 absent(screen.queryByRole('checkbox',{name:/Nightly invoice/}));
 fireEvent.click(option);
 const tool=await screen.findByRole('checkbox',{name:/Nightly invoice reconciliation/});
 assert.deepEqual(calls.filter(([op])=>op==='history_preview').map(([,a])=>a).at(-1),{projectId:'proj',includeToolRuns:true});
 assert.match(tool.closest('label').textContent,/Codex · Run by a tool \(exec\), not you · recent/);
 assert.equal(screen.getByRole('checkbox',{name:/Recent work/}).checked,true,'what you had ticked stays ticked');
 assert.equal(tool.checked,true,'open, so ticked once you ask for them, like any other open thread');
 fireEvent.click(screen.getByRole('button',{name:'Import 2 conversations'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='history_read')));
 assert.deepEqual(calls.find(([op])=>op==='history_read')[1],{projectId:'proj',sessionIds:['session-a','session-t'],includeToolRuns:true});
});

test('turning tool runs back off drops them from the list and the selection',async()=>{
 const own={sessionId:'session-a',name:'Recent work',source:'claude',last_active:NOW,reason:'recent',recommended:true,excerpt:'',toolRun:false,ranBy:null};
 host({history_preview:a=>({candidates:a.includeToolRuns?[own,TOOL]:[own],limit:50,toolRuns:1})});
 mount();
 fireEvent.click(screen.getByRole('button',{name:'Choose conversations'}));
 fireEvent.click(await screen.findByRole('checkbox',{name:/Include sessions run by a tool/}));
 await screen.findByRole('checkbox',{name:/Nightly invoice/});
 assert.ok(screen.getByRole('button',{name:'Import 2 conversations'}));
 fireEvent.click(screen.getByRole('checkbox',{name:/Include sessions run by a tool/}));
 await waitFor(()=>absent(screen.queryByRole('checkbox',{name:/Nightly invoice/})));
 assert.ok(screen.getByRole('button',{name:'Import 1 conversation'}));
});
