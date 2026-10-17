import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','CustomEvent','Event','KeyboardEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup,waitFor,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThreadPullRequest,withCode}=await import('./PullRequest.tsx');
const {NotPushedLine}=await import('./GuideActions.tsx');
const G=await import('./github.ts');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const at=(hour,minute)=>new Date(2026,8,25,hour,minute).getTime()/1000;
const clock=seconds=>new Date(seconds*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
// Shaped from github_host's answers (backend/test_github_host.py).
const READY={state:'ready',host:'github.com',login:'brambach',version:'2.101.0',detail:'Signed in to github.com as brambach.',installCommand:null,
 signInCommand:'gh auth login --hostname github.com --web',checkedAt:at(2,30),repo:'brambach/dervo'};
const MINE={sha:'4e1c9a2f00',short:'4e1c9a2',subject:'Ideas stored per project',channelId:'c',thread:'Ideas storage',outside:false};
const THREAD={id:'pv-thread',mode:'thread',repo:'brambach/dervo',host:'github.com',account:'brambach',base:'main',head:'dervo/ideas-storage',title:'Ideas storage',draft:true,
 commits:[MINE],pushNeeded:true,fetched:false,available:true,reason:null,branch:'mvp1',dependsOn:[],others:0,outside:0,previousAccount:null};
const BRANCH={...THREAD,id:'pv-branch',mode:'branch',head:'mvp1',commits:[MINE,{...MINE,sha:'b',short:'7c2e1d4',channelId:'c2',thread:'Live board'},{...MINE,sha:'d',short:'0b3d7e2',channelId:null,thread:null,outside:true}],
 others:1,outside:1};
const PR={number:215,url:'https://github.com/brambach/dervo/pull/215',repo:'brambach/dervo',base:'main',head:'dervo/ideas-storage',draft:true,state:'open',review:'changes_requested',
 reviewers:{changes:['ana-l']},checks:{passing:2,failing:0,pending:0},comments:[{author:'ana-l',text:'Should an idea with no threads be kept at all?',path:'backend/project_ideas.py',line:41,at:at(2,20)}],
 checkedAt:at(2,31),checkedAs:'brambach'};
const attempt=(state,extra)=>({id:'a1',state,step:null,detail:null,head:'dervo/ideas-storage',repo:'brambach/dervo',base:'main',draft:true,mode:'thread',startedAt:at(2,29),finishedAt:null,dependsOn:[],...extra});

function mount(answers,element=props=>React.createElement(ThreadPullRequest,{projectId:'p',channelId:'c',notOnGitHub:true,...props})){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push([operation,args]);const answer=answers[operation];
  if(answer===undefined)throw new Error(`no answer for ${operation}`);return typeof answer==='function'?answer(args):answer;}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(React.createElement(QueryClientProvider,{client},element({})));
 return calls;
}

test('the words: chips, titles, checks, decisions and when Dervo last looked',()=>{
 assert.equal(G.prChip({number:214,draft:true,state:'open',review:'changes_requested'}),'PR #214 · Draft · Changes requested');
 assert.equal(G.prChip({number:209,draft:false,state:'merged',review:'approved'}),'PR #209 · Merged');
 assert.equal(G.prChip({number:7,draft:false,state:'closed',review:null}),'PR #7 · Closed');
 assert.equal(G.prTitle({number:215,draft:true,state:'open'}),'Draft pull request #215');
 assert.equal(G.prTitle({number:215,draft:false,state:'merged'}),'Pull request #215, merged');
 assert.equal(G.checksWords({passing:2,failing:0,pending:0}),'2 of 2 passing');
 assert.equal(G.checksWords({passing:1,failing:1,pending:1}),'1 failing, 1 of 3 passing');
 assert.equal(G.checksWords({passing:1,failing:0,pending:2}),'2 still running, 1 of 3 passing');
 assert.equal(G.checksWords({passing:0,failing:0,pending:0}),'None on GitHub');
 assert.equal(G.checksWords(null),null);
 assert.equal(G.decisionWords(PR),'Changes requested by ana-l.');
 assert.equal(G.decisionWords({review:'review_required',reviewers:{requested:['ana-l','jo']},state:'open'}),'Waiting for review by ana-l, jo.');
 assert.equal(G.checkedLine(PR),`Checked on GitHub at ${clock(at(2,31))}, as brambach.`);
 // A read that failed keeps the last time and says why, never the old answer as if it were new.
 assert.equal(G.checkedLine({...PR,checkError:'GitHub didn’t answer in time.'}),`Not checked since ${clock(at(2,31))}. GitHub didn’t answer in time.`);
 assert.equal(G.checkedLine({checkedAt:null,checkedAs:null}),'Not checked on GitHub yet.');
 assert.equal(G.othersLine(BRANCH,'c'),'Also 1 commit from Live board, and 1 made outside Dervo.');
 assert.equal(G.othersLine(THREAD,'c'),'Only this thread’s commits are on it.');
 assert.match(G.clickWords(THREAD),/^This fetches main from GitHub to build dervo\/ideas-storage, pushes dervo\/ideas-storage to GitHub, then opens the pull request\./);
 assert.match(G.clickWords({...BRANCH,pushNeeded:false}),/^mvp1 is on GitHub already\. This opens the pull request/);
});

test('branch names in a sentence are set as code, the longest first',()=>{
 const {container}=render(React.createElement('p',null,withCode('dervo/main-fix goes into main.',['main','dervo/main-fix'])));
 assert.deepEqual([...container.querySelectorAll('code')].map(node=>node.textContent),['dervo/main-fix','main']);
 assert.equal(container.textContent,'dervo/main-fix goes into main.');
});

test('a committed thread offers Open pull request; the confirm names the repo, the account and what goes, and one click pushes and opens it',async()=>{
 let status={pr:null,attempt:null,pullOffer:null};
 const calls=mount({github_status:READY,pr_status:()=>status,pr_preview:({mode})=>mode==='thread'?THREAD:BRANCH,
  pr_create:args=>{status={pr:null,attempt:attempt('running',{detail:'Checking your GitHub account.'}),pullOffer:null};return status.attempt;}});
 const offer=await screen.findByRole('button',{name:'Open pull request'});
 assert.equal(screen.getByTestId('pr-not-on-github').textContent,'Not on GitHub yet.');
 // Reading the thread's PR asks GitHub again, as you.
 assert.ok(calls.some(([operation,args])=>operation==='pr_status'&&args.refresh===true));
 fireEvent.click(offer);
 const confirm=await screen.findByRole('group',{name:'Open a pull request'});
 await waitFor(()=>assert.equal(confirm.querySelector('.dervo-prc-q').textContent,'Open a pull request on brambach/dervo as brambach?'));
 // This thread only is the default choice, and its line sets the branch names as code.
 const choices=within(confirm).getAllByRole('radio');
 assert.deepEqual(choices.map(node=>[node.querySelector('.dv-c-name').textContent,node.getAttribute('aria-checked')]),[['This thread only','true'],['Everything on mvp1','false']]);
 assert.deepEqual([...choices[0].querySelectorAll('code')].map(node=>node.textContent),['dervo/ideas-storage','main','mvp1']);
 assert.equal(choices[1].querySelector('.dv-c-line').textContent,'Also 1 commit from Live board, and 1 made outside Dervo.');
 assert.equal(within(confirm).getByRole('switch',{name:'Open as a draft'}).getAttribute('aria-checked'),'true');
 // One click names both outward steps.
 const go=within(confirm).getByRole('button',{name:'Push and open pull request'});
 fireEvent.click(within(confirm).getByRole('switch',{name:'Open as a draft'}));
 fireEvent.click(go);
 await screen.findByTestId('pr-running');
 assert.deepEqual(calls.find(([operation])=>operation==='pr_create'),['pr_create',{projectId:'p',channelId:'c',previewId:'pv-thread',draft:false}]);
 assert.equal(screen.getByTestId('pr-running').textContent,'Checking your GitHub account.');
 assert.equal(screen.queryByRole('button',{name:'Open pull request'}),null);
});

test('picking Everything on the branch sends that confirm’s id, and Esc closes it without a call',async()=>{
 const calls=mount({github_status:READY,pr_status:{pr:null,attempt:null,pullOffer:null},pr_preview:({mode})=>mode==='thread'?THREAD:BRANCH,pr_create:attempt('running')});
 fireEvent.click(await screen.findByRole('button',{name:'Open pull request'}));
 const confirm=await screen.findByRole('group',{name:'Open a pull request'});
 fireEvent.keyDown(confirm,{key:'Escape'});
 assert.equal(screen.queryByRole('group',{name:'Open a pull request'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Open pull request'}));
 const again=await screen.findByRole('group',{name:'Open a pull request'});
 await waitFor(()=>assert.equal(within(again).getAllByRole('radio')[1].querySelector('.dv-c-end').textContent,'3 commits'));
 fireEvent.click(within(again).getAllByRole('radio')[1]);
 fireEvent.click(within(again).getByRole('button',{name:'Push and open pull request'}));
 await waitFor(()=>assert.ok(calls.some(([operation,args])=>operation==='pr_create'&&args.previewId==='pv-branch'&&args.draft===true)));
});

test('a choice GitHub can’t take says why and can’t be picked',async()=>{
 const calls=mount({github_status:READY,pr_status:{pr:null,attempt:null,pullOffer:null},pr_create:attempt('running'),
  pr_preview:({mode})=>mode==='thread'?{...THREAD,id:null,available:false,reason:'This thread’s 4e1c9a2 builds on 1 commit that isn’t on main (Live board), so it can’t go on its own branch.'}:BRANCH});
 fireEvent.click(await screen.findByRole('button',{name:'Open pull request'}));
 const confirm=await screen.findByRole('group',{name:'Open a pull request'});
 await waitFor(()=>assert.equal(within(confirm).getAllByRole('radio')[1].getAttribute('aria-checked'),'true'));
 const off=within(confirm).getAllByRole('radio')[0];
 assert.equal(off.getAttribute('aria-disabled'),'true');
 assert.match(off.textContent,/builds on 1 commit that isn’t on main \(Live board\)/);
 fireEvent.click(off);
 assert.equal(off.getAttribute('aria-checked'),'false');
 assert.ok(!calls.some(([operation])=>operation==='pr_create'));
});

test('signed out: one line and Sign in to GitHub; Check again only once there is something to check',async()=>{
 mount({github_status:{...READY,state:'signed_out',login:null,detail:'gh isn’t signed in to github.com.'},pr_status:{pr:null,attempt:null,pullOffer:null}});
 const line=await screen.findByTestId('pr-signin');
 assert.match(line.textContent,/sign in to GitHub on this Mac\. Dervo uses the GitHub CLI’s own sign-in and never sees the token\./);
 assert.equal(screen.queryByRole('button',{name:'Open pull request'}),null);
 // No project terminal here (no ReplyActionsContext), so it says the command and offers Check again at once.
 assert.match(line.textContent,/In Terminal, run gh auth login --hostname github\.com --web\./);
 assert.ok(within(line).getByRole('button',{name:'Check again'}));
});

test('the open PR shows GitHub’s answer with when and as whom, reviewers quoted',async()=>{
 mount({github_status:READY,pr_status:{pr:PR,attempt:attempt('opened'),pullOffer:null}});
 const box=await screen.findByRole('region',{name:'Pull request'});
 assert.equal(box.querySelector('.dervo-pr-head b').textContent,'Draft pull request #215');
 assert.deepEqual([...box.querySelectorAll('q')].map(node=>node.textContent),['Should an idea with no threads be kept at all?']);
 assert.match(box.textContent,/ana-l, on backend\/project_ideas\.py:41/);
 assert.equal(box.querySelector('.dervo-pr-checked').textContent,`Checked on GitHub at ${clock(at(2,31))}, as brambach.`);
 assert.ok(within(box).getByRole('button',{name:'Open on GitHub'}));
 assert.equal(screen.queryByRole('button',{name:'Open pull request'}),null);
});

test('a lost result says so and offers only to check GitHub, never to open another',async()=>{
 let answer={pr:null,attempt:attempt('unknown',{detail:'Dervo lost track of this before GitHub answered.'}),pullOffer:null};
 const calls=mount({github_status:READY,pr_status:()=>answer});
 const lost=await screen.findByTestId('pr-unknown');
 assert.match(lost.textContent,/Dervo lost track of this before GitHub answered\./);
 assert.equal(screen.queryByRole('button',{name:/Open pull request/}),null);
 answer={pr:PR,attempt:attempt('opened'),pullOffer:null};
 fireEvent.click(within(lost).getByRole('button',{name:'Check GitHub'}));
 await screen.findByRole('region',{name:'Pull request'});
 assert.ok(calls.filter(([operation,args])=>operation==='pr_status'&&args.refresh===true).length>=2);
});

test('a refused push says why and offers to open it again',async()=>{
 mount({github_status:READY,pr_status:{pr:null,attempt:attempt('failed',{detail:'GitHub refused the push.'}),pullOffer:null}});
 assert.equal((await screen.findByTestId('pr-failed')).textContent,'GitHub refused the push.');
 assert.ok(screen.getByRole('button',{name:'Open pull request again'}));
});

test('after a one-thread PR merges, it offers the pull the local branch needs, and Dervo never runs it itself',async()=>{
 const calls=mount({github_status:READY,pr_status:{pr:{...PR,state:'merged'},attempt:attempt('opened'),pullOffer:{command:'git pull --rebase',count:1,base:'main'}}});
 await screen.findByText(/Your local main still has this commit under its old id\./);
 assert.match(document.body.textContent,/In Terminal, run git pull --rebase\./);
 assert.ok(calls.every(([operation])=>['github_status','pr_status'].includes(operation)));
});

test('the thread’s commit line becomes the PR pieces given the thread; without it, it stays Not pushed yet',async()=>{
 const task={id:'t',state:'idle',commit:{sha:'4e1c9a2f00',branch:'mvp1'}};
 mount({github_status:READY,pr_status:{pr:null,attempt:null,pullOffer:null},push_preview:{commits:[{sha:'4e1c9a2f00'}]}},
  ()=>React.createElement(NotPushedLine,{task,projectId:'p',channelId:'c'}));
 await screen.findByRole('button',{name:'Open pull request'});
 await screen.findByTestId('pr-not-on-github');
 cleanup();
 mount({push_preview:{commits:[{sha:'4e1c9a2f00'}]}},()=>React.createElement(NotPushedLine,{task,projectId:'p'}));
 await screen.findByText('Not pushed yet.');
 assert.equal(screen.queryByRole('button',{name:'Open pull request'}),null);
});
