import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;
const React=await import('react');
const {render,fireEvent,cleanup,screen,act,within,waitFor}=await import('@testing-library/react');
const {WorkspaceSearch,savedStatus,savedNextAction}=await import('./WorkspaceSearch.tsx');
const {statusLabel,ConversationIndicator}=await import('./TurnControls.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();sessionStorage.clear();delete window.__TAURI_INTERNALS__;});
const props={projectId:'p',channelId:'c',profileId:'a',mode:'catchup',open:true,onClose(){}};
function row(id,extra={}){return {id,kind:'task',channelId:'c',channelName:'Prepare the launch',title:'Prepare a launch plan',status:'answered',snippet:`Answer ${id}`,hasAnswer:true,source:{kind:'task',id,channelId:'c',revision:'r',offset:0},...extra};}
function mount(handler,extra={}){
 const calls=[];window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};
 return {calls,...render(React.createElement(WorkspaceSearch,{...props,...extra}))};
}
test('briefing leads with latest owner work and keeps older and helper activity reachable',async()=>{
 const rows=[row('helper',{helper:true,channelId:'helper-room',title:'Review by @Robin',status:'blocked'}),row('latest'),row('old',{status:'blocked',stale:true})];
 mount(()=>({results:rows}));
 await screen.findByText('Answer latest');
 assert.equal(screen.queryByText('Answer old'),null);
 assert.equal(screen.queryByText('Answer helper'),null);
 assert.equal(screen.queryByText('Needs a fresh check'),null,'older stale work must not displace the newer answer');
 fireEvent.click(screen.getByRole('button',{name:'All activity'}));
 assert.ok(screen.getByText('Answer old'));assert.ok(screen.getByText('Answer helper'));
 fireEvent.click(screen.getByRole('button',{name:'Latest by conversation'}));
 assert.ok(screen.getByText('Answer latest'));assert.equal(screen.queryByText('Answer helper'),null);
});
test('result preview retains the list and continues only to the validated source conversation',async()=>{
 const saved=row('current');const opened=[];
 const view=mount(operation=>operation==='workspace_source'?{...saved,channelId:'validated-channel',text:'The complete answer',excerptStart:0}:{results:[saved]}, {onOpenConversation:id=>opened.push(id)});
 await screen.findByText('Answer current');
 const read=screen.getByRole('button',{name:'Read answer'});read.focus();fireEvent.click(read);
 await screen.findByText('The complete answer');
 assert.ok(screen.getByText('Answer current'));
 assert.equal(document.activeElement,screen.getByRole('button',{name:'Back to briefing'}));
 assert.equal(document.querySelectorAll('[aria-label="Project briefing"] details').length,0,'the overview uses direct actions, not a disclosure per row');
 fireEvent.click(screen.getByRole('button',{name:'Continue conversation'}));
 assert.deepEqual(opened,['validated-channel']);
 assert.deepEqual(view.calls.map(c=>c.operation),['project_catchup','workspace_source']);
 fireEvent.click(screen.getByRole('button',{name:'Back to briefing'}));
 assert.equal(document.activeElement,screen.getByRole('button',{name:'Read answer'}));
});
test('generated reviewer labels stay readable while source bindings and named helpers stay exact',async()=>{
 const internal='review-1c1a901443a487ca7696c171';
 const saved=row('auto-review',{helper:true,title:`Review by @${internal}`,owner:`claude · ${internal}`,channelId:'review-room'});
 const named=row('named-review',{helper:true,title:'Review by @Robin',channelId:'named-room'});
 const opened=[];
 const view=mount(operation=>operation==='workspace_source'?{...saved,text:'The recorded verdict.',excerptStart:0}:{results:[saved,named]}, {onOpenConversation:id=>opened.push(id)});
 await screen.findByText('Only helper activity in these results');
 fireEvent.click(screen.getByRole('button',{name:'All activity'}));
 assert.ok(screen.getByRole('heading',{name:'Independent review'}));
 assert.ok(screen.getByRole('heading',{name:'Review by @Robin'}));
 assert.equal(screen.queryByRole('heading',{name:`Review by @${internal}`}),null);
 fireEvent.click(screen.getAllByRole('button',{name:'Read answer'})[0]);
 await screen.findByText('The recorded verdict.');
 assert.ok(screen.getByText('Claude Code · Reviewer'));
 assert.deepEqual(view.calls.find(c=>c.operation==='workspace_source').args.source,saved.source);
 fireEvent.click(screen.getByRole('button',{name:'Continue conversation'}));
 assert.deepEqual(opened,['review-room']);
});
test('saved status distinguishes stale files, approval policy and uncertain observation',()=>{
 assert.equal(savedStatus(row('stale',{status:'ready_for_review',stale:true,humanApprovalRequired:true})),'Needs a fresh check');
 assert.equal(savedStatus(row('off',{status:'ready_for_review',humanApprovalRequired:false})),'Needs another look');
 assert.equal(savedStatus(row('on',{status:'ready_for_review',humanApprovalRequired:true})),'Waiting for your review');
 assert.equal(savedStatus(row('error',{status:'ready_for_review',humanApprovalRequired:true,blocker:'Checks failed'})),'Needs another look');
 assert.equal(savedStatus(row('lost',{status:'disconnected'})),'Outcome unconfirmed');
 assert.equal(savedStatus(row('active',{status:'working'})),'Was in progress');
});
test('failed source inspection never enables continuation with a guessed destination',async()=>{
 let resolve;const opened=[];
 mount(operation=>operation==='workspace_source'?new Promise((_,reject)=>{resolve=reject;}):{results:[row('current')]},{onOpenConversation:id=>opened.push(id)});
 fireEvent.click(await screen.findByRole('button',{name:'Read answer'}));
 assert.equal(screen.queryByRole('button',{name:'Continue conversation'}),null);
 await act(async()=>resolve(new Error('The saved source changed. Search again.')));
 assert.ok(await screen.findByRole('alert'));
 assert.equal(screen.queryByRole('button',{name:'Continue conversation'}),null);
 assert.deepEqual(opened,[]);
});
test('a newer answer never hides an older explicit decision awaiting the user',async()=>{
 mount(()=>({results:[row('newer'),row('pending',{status:'ready_for_review',humanApprovalRequired:true})]}));
 await screen.findByText('Answer newer');
 assert.ok(screen.getByText('Answer pending'));
 assert.ok(screen.getByRole('heading',{name:/Waiting for you/}));
 assert.ok(screen.getByText('Waiting for your review'));
});
test('load failure does not fabricate a helper-only or empty briefing',async()=>{
 mount(()=>Promise.reject(new Error('Read failed')));
 await screen.findByRole('alert');
 assert.equal(screen.queryByText('Only helper activity in these results'),null);
 assert.equal(screen.queryByText('No saved project tasks yet'),null);
});
test('back from pending or failed source restores focus and rejects its late reply',async()=>{
 let reply;
 mount(operation=>operation==='workspace_source'?new Promise(resolve=>{reply=resolve;}):{results:[row('current')]});
 const read=await screen.findByRole('button',{name:'Read answer'});read.focus();fireEvent.click(read);
 const back=screen.getByRole('button',{name:'Back to briefing'});back.focus();fireEvent.click(back);
 assert.equal(document.activeElement,read);
 await act(async()=>reply({...row('current'),text:'A late answer after back'}));
 assert.equal(screen.queryByText('A late answer after back'),null);
 assert.equal(screen.queryByRole('complementary',{name:'Saved answer preview'}),null);
});
test('an open independent review never asks for a human decision',async()=>{
 mount(()=>({results:[row('reviewing',{status:'ready_for_review',humanApprovalRequired:true,reviewPending:true})]}));
 await screen.findByText('Review was in progress');
 assert.equal(screen.queryByText('Waiting for your review'),null);
 assert.equal(screen.queryByRole('heading',{name:/Waiting for you/}),null);
});
test('native pointer clicks without button focus move into the answer and preserve later focus changes',async()=>{
 let reply;
 mount(operation=>operation==='workspace_source'?new Promise(resolve=>{reply=resolve;}):{results:[row('current')]});
 const read=await screen.findByRole('button',{name:'Read answer'});
 assert.equal(document.activeElement,document.body);
 fireEvent.click(read);
 await act(async()=>reply({...row('current'),text:'The saved answer'}));
 const back=screen.getByRole('button',{name:'Back to briefing'});
 assert.equal(document.activeElement,back);
 fireEvent.click(back);
 read.blur();
 fireEvent.click(read);
 // A newer focus choice must win even when the original native click had no focus.
 const external=document.createElement('button');document.body.append(external);external.focus();
 await act(async()=>reply({...row('current'),text:'The answer after another focus choice'}));
 assert.equal(document.activeElement,external);
 external.remove();
});
test('search distinguishes a matching request from an answer even when the task has both',async()=>{
 mount(()=>({results:[row('request',{matchRole:'user'}),row('answer',{matchRole:'assistant'})]}),{mode:'project'});
 fireEvent.change(screen.getByRole('textbox',{name:'Search saved text'}),{target:{value:'launch'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByText('Your request');
 assert.ok(screen.getByRole('button',{name:'Read request'}));
 assert.ok(screen.getByRole('button',{name:'Read answer'}));
 assert.equal(screen.getAllByText('Prepare a launch plan').length,1,'the matching request already has a snippet');
});
test('search leads with answers and preserves helper-only requests and their exact source',async()=>{
 const helper=row('helper',{helper:true,hasAnswer:false,matchRole:'user',title:'Review by @Robin',channelId:'helper-room',snippet:'Generated review request'});
 helper.source={...helper.source,channelId:'helper-room'};
 const rows=[helper,row('request',{matchRole:'user'}),row('answer',{matchRole:'assistant'}),row('legacy')];
 const view=mount(operation=>operation==='workspace_source'?{...helper,text:'Exact generated request',excerptStart:0}:{results:rows},{mode:'project'});
 fireEvent.change(screen.getByRole('textbox',{name:'Search saved text'}),{target:{value:'launch'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByText('Generated review request');
 const results=screen.getByLabelText('Search results');
 assert.deepEqual(within(results).getAllByRole('heading',{level:3}).map(node=>node.textContent),['Answers 1','Matching requests 1','Conversation excerpts 1','Helper work 1']);
 assert.equal(within(results).getAllByRole('article').length,4);
 const section=within(results).getByRole('heading',{name:'Helper work 1'}).closest('section');
 assert.ok(within(section).getByText('Helper request'));
 assert.equal(within(section).queryByText('Helper answer'),null);
 const open=within(section).getByRole('button',{name:'Read request'});open.focus();fireEvent.click(open);
 await screen.findByText('Exact generated request');
 assert.deepEqual(view.calls.at(-1).args.source,helper.source);
 fireEvent.click(screen.getByRole('button',{name:'Return to search'}));
 assert.equal(document.activeElement,open);
 assert.equal(screen.getByRole('textbox',{name:'Search saved text'}).value,'launch');
});
test('clipped Markdown previews stay readable while the reader retains the original list',async()=>{
 const text='- **Storage** stays local.\n- [Read details](https://example.invalid) ![diagram](https://example.invalid/image.png)';
 mount(operation=>operation==='workspace_source'?{...row('answer'),text,excerptStart:0}:{results:[row('answer',{matchRole:'assistant',snippet:`…${text}…`})]},{mode:'project'});
 fireEvent.change(screen.getByRole('textbox',{name:'Search saved text'}),{target:{value:'storage'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByText('Storage');
 const preview=document.querySelector('.dervo-result-excerpt');
 assert.match(preview.textContent,/^…\s*Storage stays local/);
 assert.ok(preview.textContent.endsWith('…'));
 assert.equal(preview.querySelectorAll('li,ul,ol,a,img').length,0);
 assert.ok(preview.querySelector('strong'));
 fireEvent.click(screen.getByRole('button',{name:'Read answer'}));
 const reader=await screen.findByLabelText('Saved answer preview');
 await within(reader).findByRole('list');
 assert.equal(reader.querySelectorAll('li').length,2);
 assert.equal(reader.querySelectorAll('a,img').length,0);
});
test('legacy activity preparation retries while open and stops when the view closes',async()=>{
 let prepared=false;
 let passes=0;
 const view=mount(()=>prepared&&++passes>=3?{results:[row('current')]}:{results:[],cursor:null,incomplete:true,indexing:true});
 await screen.findByText(/Preparing your saved activity/);
 assert.equal(screen.queryByText('No saved project tasks yet'),null);
 prepared=true;
 await screen.findByText('Answer current',{}, {timeout:4500});
 assert.equal(view.calls.length,4);
 prepared=false;
 fireEvent.click(screen.getByRole('button',{name:'Refresh catch-up'}));
 await screen.findByText(/Preparing your saved activity/);
 const count=view.calls.length;
 view.rerender(React.createElement(WorkspaceSearch,{...props,open:false}));
 await act(async()=>new Promise(resolve=>setTimeout(resolve,850)));
 assert.equal(view.calls.length,count);
});

test('saved decisions match composer labels without making an old approval current',()=>{
 for(const [status,humanDecision,expected] of [['ready_for_review','rejected','Waiting for your feedback'],['ready_for_review',null,'Waiting for your review'],['idle','approved','Result approved']]){
  const saved=row('decision',{status,humanDecision,humanApprovalRequired:true});
  assert.equal(savedStatus(saved),expected);
  const composer=statusLabel({state:status,human_decision:humanDecision,acceptance_required:true});
  assert.equal(composer,status==='ready_for_review'&&humanDecision===null?'Ready for review':expected);
 }
 assert.equal(statusLabel({state:'working',human_decision:'approved'}),'Working');
 assert.equal(savedStatus(row('running',{status:'working',humanDecision:'approved'})),'Was in progress');
 for(const facts of [{outcomeUncertain:true},{stale:true},{reviewPending:true,status:'ready_for_review'}]){
  const saved=row('old',{status:'ready_for_review',humanDecision:'approved',nextAction:'Approve this now',...facts});
  assert.notEqual(savedStatus(saved),'Result approved');
  assert.doesNotMatch(savedNextAction(saved),/Approve this now/);
 }
});

test('correction feedback stays in Waiting for you while uncertain activity cannot ask for a decision',async()=>{
 const correction=row('fix',{status:'ready_for_review',humanDecision:'rejected',humanApprovalRequired:false,blocker:'The check failed'});
 const unknown=row('unknown',{channelId:'other',status:'ready_for_review',humanDecision:'rejected',humanApprovalRequired:true,outcomeUncertain:true});
 mount(()=>({results:[row('newer'),correction,unknown]}));
 await screen.findByText('Answer fix');
 const waiting=screen.getByRole('heading',{name:/Waiting for you/}).closest('section');
 assert.ok(within(waiting).getByText('Waiting for your feedback'));
 assert.equal(within(waiting).queryByText('Answer unknown'),null);
 assert.ok(screen.getByText('Outcome unconfirmed'));
});

test('approved preview gives a useful next action and keeps raw status inside Source record',async()=>{
 const saved=row('approved',{status:'idle',humanDecision:'approved',owner:'Codex',blocker:'Unknown: no blocker recorded',nextAction:'Unknown: no next action recorded'});
 const view=mount(operation=>operation==='workspace_source'?{...saved,text:'The revised description.',excerptStart:0}:{results:[saved]});
 fireEvent.click(await screen.findByRole('button',{name:'Read answer'}));
 const reader=await screen.findByRole('complementary',{name:'Saved answer preview'});
 await within(reader).findByText('The revised description.');
 assert.ok(within(reader).getByText('Result approved'));
 assert.ok(within(reader).getByText('Continue this conversation if you need a follow-up.'));
 assert.equal(within(reader).queryByText('Not recorded'),null);
 assert.equal(within(reader).getByText('idle').closest('details').querySelector('summary').textContent,'Source record');
 assert.deepEqual(view.calls.map(call=>call.operation),['project_catchup','workspace_source']);
});

test('uncertain outcome wins over a configured coordination mode',()=>{
 const view=render(React.createElement(ConversationIndicator,{binding:{provider:'codex',identity:'synthetic@example.test',model:'synthetic-model'},status:{state:'blocked',active:false,detail:'Check the saved activity.',outcome_uncertain:true,harness:{state:'available',helper:'coordinator',provider:'codex'}},onSettings(){}}));
 assert.equal(view.getByRole('status').textContent,'Outcome unknown');
 assert.equal(view.queryByText('Coordination configured'),null);
 assert.ok(view.getByText('Check the saved activity.'));
});
test('the briefing leads with what waits and what is new, each card says where it is, and actions open the conversation or task',async()=>{
 const opened=[],inspected=[],knowledge=[];
 const done=row('done',{status:'completed',isNew:true,completedAt:1758000000,changedFiles:['a.py'],changedFileCount:1,summary:'Changed a.py. Host tests exit 0. No independent review was configured. Not marked as approved by you.'});
 const waiting=row('waiting',{channelId:'w',channelName:'Ship it',status:'ready_for_review',humanApprovalRequired:true,askedAt:1758000100,summary:'Waiting for your review.'});
 const running=row('running',{channelId:'r',channelName:'Refactor',status:'working',askedAt:1758000200,summary:'Still working: editing auth.py.',lastTool:'dervo_write_file',lastPath:'auth.py'});
 mount(()=>({results:[done,waiting,running],since:1757990000,lessonsPending:2}),{onOpenConversation:id=>opened.push(id),onOpenTask:(id,taskId)=>inspected.push([id,taskId]),onOpenKnowledge:()=>knowledge.push(1)});
 await screen.findByTestId('brief-summary');
 const stats=[...screen.getByTestId('brief-summary').querySelectorAll('.dervo-brief-stat')].map(e=>e.textContent);
 assert.deepEqual(stats,['1 waiting for you','0 need a look','1 running'],'the completed and running rows need no look; the waiting one is counted once');
 assert.match(screen.getByTestId('brief-summary').querySelector('.dervo-brief-since').textContent,/^1 new since you looked at \S.*\. Updated \S.*\.$/);
 assert.match(screen.getByTestId('brief-summary').textContent,/2 lesson suggestions in Knowledge\./);
 assert.ok(screen.getByRole('heading',{name:/Running/}),'running work has its own group');
 assert.equal(document.querySelectorAll('.dervo-result-new').length,1,'only the record saved since the last visit is marked');
 assert.ok(screen.getByText('Changed a.py. Host tests exit 0. No independent review was configured. Not marked as approved by you.'));
 assert.ok(screen.getByText('Still working: editing auth.py.'));
 const times=[...document.querySelectorAll('.dervo-result-time')].map(e=>e.textContent).join(' | ');
 assert.match(times,/Completed \S/);assert.match(times,/Asked \S/);
 assert.equal(screen.queryByText(/^Next:/),null,'the dead next-action line is gone');
 fireEvent.click(screen.getAllByRole('button',{name:'Open conversation'})[0]);
 fireEvent.click(screen.getAllByRole('button',{name:'See the record'})[0]);
 fireEvent.click(screen.getByRole('button',{name:'Open Knowledge'}));
 assert.deepEqual([opened,inspected,knowledge],[['w'],[['w','waiting']],[1]]);
 assert.equal(document.querySelector('.dervo-find-heading .dervo-character'),null);
});
test('a first visit says nothing about newness and a blocker is not repeated by the summary',async()=>{
 const blocked=row('blocked',{status:'blocked',blocker:'API Error: Overloaded',summary:'Couldn’t finish: API Error: Overloaded'});
 mount(()=>({results:[blocked],since:null,lessonsPending:null}));
 await screen.findByText('API Error: Overloaded');
 assert.doesNotMatch(screen.getByTestId('brief-summary').textContent,/since/);
 assert.match(screen.getByTestId('brief-summary').textContent,/This is your first look at this project\./);
 assert.equal(screen.getByTestId('brief-summary').querySelector('.is-attention b').textContent,'1');
 assert.equal(screen.queryByTestId('result-summary'),null,'the blocker line already says it');
 assert.equal(document.querySelectorAll('.dervo-result-new').length,0);
});

test('A15: refreshing Catch up keeps the cards until the new briefing arrives',async()=>{
 let release;const first=row('first',{channelId:'a',channelName:'Alpha',status:'completed',summary:'First briefing.'});
 const second=row('second',{channelId:'b',channelName:'Beta',status:'completed',summary:'Second briefing.'});
 let calls=0;
 mount(()=>{calls++;if(calls===1)return {results:[first],since:null,lessonsPending:null};return new Promise(resolve=>{release=()=>resolve({results:[second],since:null,lessonsPending:null});});});
 await screen.findByText('First briefing.');
 fireEvent.click(screen.getByRole('button',{name:'Refresh catch-up'}));
 await waitFor(()=>assert.equal(calls,2));
 assert.ok(screen.getByText('First briefing.'),'the old cards stay while the refresh is pending');
 assert.equal(screen.queryByText('Gathering your recent work…'),null,'no loading text replaces the list');
 release();
 await screen.findByText('Second briefing.');
 assert.equal(screen.queryByText('First briefing.'),null);
});
