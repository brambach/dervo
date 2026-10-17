import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLButtonElement','HTMLDetailsElement','MutationObserver','Node','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent'])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
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
const {MacOnboarding}=await import('./MacOnboarding.tsx');
const {collapseNumbers,quieterLine,bringLabel,broughtLine,groupGuess,restorePick,rowGuess,initialPicks,nothingFound,personalOnly,sourceLine,suggestPlan,threadsQuestion,unreadLine,waitingLine}=await import('./onboarding.ts');
const {surveyFixture,workLookingFixture,personalFixture}=await import('./onboardingFixture.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

function host(answers){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command,a=args.args??{};
  calls.push([op,a]);
  if(!(op in answers))throw new Error('no fixture '+op);
  const answer=answers[op];
  return typeof answer==='function'?answer(a):answer;
 }};
 return calls;
}
const agent=(overrides={})=>({ready:true,name:'Claude Code · Opus 5.5',who:'Claude Code · me@example.test',line:'',prepare:async()=>({profileId:'me',model:'claude-opus-5-5'}),...overrides});
const ACME={id:'w1',provider:'claude',identity:'you@acme.test',category:'work',plan:'team'};
/** The work side as useWorkSide reports it: none, or a verified work account. */
const workSide=(ready,overrides={})=>({side:ready?{state:'ready',profile:ACME,model:'claude-opus-5-5',who:'Claude Code · you@acme.test',line:'Claude Code · you@acme.test',prepare:async()=>({profileId:'w1',model:'claude-opus-5-5'})}
 :{state:'none',who:'',line:'No work account yet',prepare:async()=>{throw new Error('none');}},profiles:ready?[ACME]:[],onChanged:async()=>{},...overrides});
// Each render's query cache is cleared after its test, so its garbage-collection timers don't hold the run open.
const clients=[];
afterEach(()=>{for(const client of clients.splice(0))client.clear();});
function show(props){
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 clients.push(client);
 const element=props=>React.createElement(QueryClientProvider,{client},React.createElement(MacOnboarding,{agent:agent(),survey:surveyFixture,still:true,onDone(){},onBack(){},onStartEmpty(){},...props}));
 const view=render(element(props));
 return {...view,again:next=>view.rerender(element({...props,...next}))};
}
async function suggest(props){const view=show(props);fireEvent.click(await screen.findByRole('button',{name:'See what Dervo suggests'}));return view;}
const groups=()=>screen.getAllByRole('group').filter(g=>g.classList.contains('dv-ob-group')).map(g=>g.getAttribute('aria-label'));

test('the copy comes from the host’s numbers, said plainly',()=>{
 assert.deepEqual(collapseNumbers(surveyFixture.counts),['2,318 conversation files.','184 folders.','5 things you’re working on.']);
 assert.equal(quieterLine(surveyFixture.counts),'And 3 quieter ones, plus 14 conversations that never had a project folder.');
 assert.equal(quieterLine({files:1,folders:1,working:1,quieter:0,loose:0}),null);
 assert.deepEqual([bringLabel(5),bringLabel(1,1),bringLabel(0)],['Bring in 5 projects','Bring in 1 project and 1 thread','Bring in']);
 assert.deepEqual(sourceLine({tool:'codex',path:'~/.codex',present:false}),{name:'Codex',path:'~/.codex thread list',state:'Not on this Mac'});
 assert.match(threadsQuestion(5),/in the 5 projects you picked/);
 assert.equal(nothingFound({...surveyFixture,projects:[],loose_ends:null,counts:{files:0,folders:0,working:0,quieter:0,loose:0}}),true);
 // Behaviour review S4: a list that's there but unreadable is said, never shown as "nothing on this Mac".
 const empty={...surveyFixture,projects:[],loose_ends:null,counts:{files:0,folders:0,working:0,quieter:0,loose:0}};
 const locked=[{tool:'claude',path:'~/.claude/projects',present:true,readable:true},{tool:'codex',path:'~/.codex',present:true,readable:false}];
 assert.equal(unreadLine({sources:locked}),'Dervo couldn’t read Codex’s list, so its folders aren’t counted here.');
 assert.equal(nothingFound({...empty,sources:locked}),false);
 assert.equal(unreadLine({sources:locked.map(s=>({...s,readable:false}))}),'Dervo couldn’t read Claude Code’s or Codex’s list, so their folders aren’t counted here.');
 assert.equal(unreadLine({sources:[{tool:'codex',path:'~/.codex',present:false,readable:true}]}),null,'a tool that isn’t here isn’t unreadable');
 const batch={batchId:'b',at:Date.UTC(2026,8,24,9,41)/1000,undone:false,projects:[{id:'p1',name:'tidepool',threads:9},{id:'p2',name:'recipe-box',threads:5}],threads:14,
  problems:[],undoable:true,blocked:null};
 assert.match(broughtLine(batch,batch.at+60),/^Brought in 2 projects and 14 threads from this Mac at .+\. No agent has run\.$/);
 // Work folders that waited for a work account are said by name, and that nothing of theirs was read.
 assert.equal(waitingLine(['billing-api','checkout-web']),'billing-api and checkout-web wait for a work account. Nothing in them has been read or sent.');
 assert.equal(waitingLine(['a','b','c','d']),'a, b and 2 others wait for a work account. Nothing in them has been read or sent.');
 assert.equal(groupGuess('work',3,false),'Guessed from folder names and git remotes. Each row says which.');
 assert.match(groupGuess(null,1,false),/^Nothing in its folder name or git remote says which/);
 assert.match(groupGuess('personal',3,true),/If one is your employer’s, hover it and choose Move to Work\./);
});

test('Suggest plans each side on its own account and never lets a waiting row hold the rest',()=>{
 const ready={ready:true,line:''},missing={ready:false,line:'Claude Code isn’t installed on this Mac. Bring in waits until an agent is connected.'};
 const picks=initialPicks(surveyFixture);
 // No work account: the 2 Personal come in, the 3 Work wait and say so, and Bring in goes.
 let plan=suggestPlan(picks,ready,{ready:false});
 assert.deepEqual([plan.now.map(p=>p.name),plan.waiting.length,plan.go,plan.status,plan.aside,plan.addWork],
  [['tidepool','recipe-box'],3,true,null,'3 work projects wait for a work account.',false]);
 assert.equal(suggestPlan(picks,ready,{ready:false,adding:true}).aside,'3 work projects wait for the account.');
 // A work account: all 5 come in.
 plan=suggestPlan(picks,ready,{ready:true});
 assert.deepEqual([plan.now.length,plan.waiting.length,plan.aside,plan.go],[5,0,null,true]);
 // An unsure row waits for its pick; the others still come in (the old screen greyed everything out).
 const blog=picks.map(p=>p.key==='k-blog'?{...p,ticked:true}:p);
 plan=suggestPlan(blog,ready,{ready:true});
 assert.deepEqual([plan.go,plan.status],[true,'blog waits until you pick Work or Personal. The other 5 can come in now.']);
 plan=suggestPlan(blog.map(p=>p.key==='k-blog'?p:{...p,ticked:false}),ready,{ready:true});
 assert.deepEqual([plan.go,plan.status],[false,'Pick Work or Personal for blog to bring it in.']);
 // Every ticked one is Work and there's no work account: the main button becomes Add a work account.
 plan=suggestPlan(initialPicks(workLookingFixture),ready,{ready:false});
 assert.deepEqual([plan.now.length,plan.addWork,plan.aside,plan.status,plan.go],[0,true,'These all look like work.',null,false]);
 // The held reasons that stay: nothing ticked, no agent for the Personal ones, an empty name.
 assert.equal(suggestPlan(picks.map(p=>({...p,ticked:false})),ready,{ready:true}).status,'Tick a project to bring it in.');
 plan=suggestPlan(picks,missing,{ready:true});
 assert.deepEqual([plan.status,plan.go],[missing.line,false]);
 assert.equal(suggestPlan(picks,{ready:false,line:''},{ready:false}).status,'Bring in waits until an agent is connected.');
 assert.equal(suggestPlan(picks.map((p,n)=>n===0?{...p,name:'  '}:p),ready,{ready:true}).status,'Give the ticked project a name to bring it in.');
 assert.equal(suggestPlan(picks.map((p,n)=>n<2?{...p,name:''}:p),ready,{ready:true}).status,'Give the 2 ticked projects a name to bring them in.');
});

test('picks start from the host’s ticks and guesses, loose ends off, and a folder comes back from its pile unticked and unsure',()=>{
 const picks=initialPicks(surveyFixture);
 assert.deepEqual(picks.filter(p=>p.ticked).map(p=>p.name),['billing-api','checkout-web','data-exports','tidepool','recipe-box']);
 assert.deepEqual(picks.map(p=>p.context),['work','work','work','personal','personal','personal',null,null]);
 assert.equal(picks.some(p=>p.name==='Loose ends'),false,'Loose ends are said, never a pick (Bryce, 24 Sep)');
 const back=restorePick(picks,surveyFixture.set_aside.find(p=>p.rule==='one_off').folders[0]);
 assert.deepEqual(back.at(-1),{key:'k-weekend',name:'weekend-idea',ticked:false,context:null,restored:true});
 assert.equal(restorePick(back,surveyFixture.set_aside.find(p=>p.rule==='one_off').folders[0]).length,back.length,'once');
 assert.equal(restorePick(picks,surveyFixture.set_aside[0].folders[0]),picks,'a pile without a key can’t come back');
 const checkout=surveyFixture.projects[1];
 assert.equal(rowGuess(picks[1],checkout),'guessed from its remote, git.acme.io');
 assert.equal(rowGuess({...picks[1],context:'personal',picked:true},checkout),'you picked Personal');
 assert.equal(personalOnly(initialPicks(personalFixture)),true);
 assert.equal(personalOnly(picks),false);
});

test('Suggest says the why once, groups Work and Personal with the account each runs on, and Move is one click',async()=>{
 await suggest({work:workSide(true)});
 assert.deepEqual(groups(),['Work or personal?','Work','Personal']);
 assert.equal(screen.getAllByRole('note').filter(n=>n.classList.contains('dv-ob-why-split')).length,1,'the why, once');
 assert.match(screen.getByText(/Work code only ever goes to a work account/).closest('p').textContent,/your employer’s code never lands on your personal subscription/);
 const work=screen.getByRole('group',{name:'Work'}),mine=screen.getByRole('group',{name:'Personal'});
 assert.equal(within(work).getByText(/^Runs on/).textContent,'Runs on Claude Code · you@acme.test');
 assert.equal(within(mine).getByText(/^Runs on/).textContent,'Runs on Claude Code · me@example.test');
 assert.ok(within(work).getByText('guessed from its remote, git.acme.io'));
 assert.ok(within(work).getByText(/44 conversations, 12 in the last 14 days/));
 assert.equal(screen.getByRole('button',{name:'Bring in 5 projects'}).disabled,false);
 // Unsure rows wait for a pick, and never hold the others.
 fireEvent.click(screen.getByRole('checkbox',{name:'blog'}));
 assert.equal(screen.getByRole('status').textContent,'blog waits until you pick Work or Personal. The other 5 can come in now.');
 assert.equal(screen.getByRole('button',{name:'Bring in 5 projects'}).disabled,false);
 fireEvent.click(screen.getByRole('button',{name:'Put blog under Personal'}));
 assert.ok(within(screen.getByRole('group',{name:'Personal'})).getByText('you picked Personal'));
 assert.ok(screen.getByRole('button',{name:'Bring in 6 projects'}));
 fireEvent.click(screen.getByRole('button',{name:'Move checkout-web to Personal'}));
 assert.ok(within(screen.getByRole('group',{name:'Personal'})).getByText('checkout-web'));
 assert.ok(screen.getByRole('button',{name:'Move checkout-web to Work'}),'and back, one click');
 assert.ok(screen.getByText('14 conversations started in your home folder, not in a project. They stay where they are.'));
 fireEvent.click(screen.getByRole('button',{name:'Show them'}));
 fireEvent.click(screen.getAllByRole('button',{name:'Bring back'})[0]);
 assert.ok(within(screen.getByRole('group',{name:'Work or personal?'})).getByText('weekend-idea'),'a folder brought back asks Work or Personal');
 fireEvent.click(screen.getByRole('button',{name:'Rename recipe-box'}));
 fireEvent.change(screen.getByRole('textbox',{name:/Name for/}),{target:{value:'Recipes'}});
 fireEvent.blur(screen.getByRole('textbox',{name:/Name for/}));
 assert.ok(screen.getByText('Recipes'));
});

test('with no work account the Work card says what to do, and the personal ones still come in',async()=>{
 await suggest({work:workSide(false)});
 const work=screen.getByRole('group',{name:'Work'});
 assert.equal(within(work).getByText('No work account yet').dataset.held,'true');
 assert.match(within(work).getByRole('note').querySelector('p').textContent,/^These need a work account\. Add one now, move any that are really yours to Personal, or leave them out\. Until then nothing in them is read or sent, and they never use your personal account\.$/);
 assert.ok(screen.getByText('3 work projects wait for a work account.'));
 assert.equal(screen.getByRole('button',{name:'Bring in 2 projects'}).disabled,false,'never a greyed button');
 fireEvent.click(within(work).getByRole('button',{name:'Leave them out'}));
 assert.deepEqual(['billing-api','checkout-web','data-exports'].map(name=>screen.getByRole('checkbox',{name}).checked),[false,false,false]);
 assert.equal(screen.queryByText(/wait for a work account/),null);
 assert.equal(within(work).queryByRole('note'),null);
});

test('the tester’s all-work Mac gets Add a work account as its main button, never a dead one',async()=>{
 // The tester's case (25 Sep): dd and dd-portal-v2 left Bring in disabled with nothing saying why.
 const calls=host({account_signin_start:{pendingId:'w9',state:'waiting',phase:null,url:null,needsCode:false,detail:'',active:true},
  connection_login_status:{state:'running',phase:null,url:'https://claude.ai/x',needsCode:true,detail:'',active:true},account_signin_cancel:{state:'cancelled'}});
 await suggest({survey:workLookingFixture,work:workSide(false)});
 assert.deepEqual(groups(),['Work']);
 assert.ok(screen.getByText('These all look like work.'));
 assert.equal(screen.queryByRole('button',{name:/^Bring in/}),null);
 fireEvent.click(screen.getByRole('button',{name:'Add a work account'}));
 const card=screen.getByRole('group',{name:'Add a work account'});
 assert.ok(within(card).getByText('Only ever runs projects under Work'));
 assert.equal(screen.getByRole('status').textContent,'Bring in once your work account is signed in.');
 // Beta.18: no work email to type first. The sign-in starts, and Dervo shows who signed in before adding it.
 assert.equal(within(card).queryByRole('textbox',{name:'Work email'}),null);
 fireEvent.click(within(card).getByRole('button',{name:'Sign in with Claude'}));
 await within(card).findByText('Started Claude Code’s own sign-in');
 assert.ok(within(card).getByText('In a folder of its own, apart from your personal sign-in'));
 assert.deepEqual(calls.find(([op])=>op==='account_signin_start')[1],{provider:'claude',category:'work'});
 assert.equal(calls.some(([op])=>op==='add_profile'),false,'no account exists until you confirm who signed in');
 assert.ok(await within(card).findByRole('textbox',{name:'Sign-in code'}));
 assert.ok(within(card).getByText('Sign in with the Claude account you want to add'));
});

test('a work sign-in that comes back as your personal account is said by name and never accepted',async()=>{
 let answer={state:'succeeded',phase:null,url:null,needsCode:false,detail:'',active:false,
  identity:{state:'other_side',canSend:false,detail:'…',otherSide:'personal',signedInAs:'me@example.test'}};
 const calls=host({account_signin_start:{pendingId:'w9',state:'waiting',active:true,needsCode:false,detail:'',url:null,phase:null},connection_login_status:()=>answer,
  account_signin_cancel:{state:'cancelled'},account_signin_add:{id:'w9',state:'added',identity:'you@acme.test'}});
 const view=await suggest({work:workSide(false,{onChanged:async()=>{}})});
 fireEvent.click(within(screen.getByRole('group',{name:'Work'})).getByRole('button',{name:'Add a work account'}));
 assert.equal(screen.getByRole('button',{name:'Bring in 2 projects'}).disabled,false,'Bring in still works while the card is open');
 fireEvent.click(screen.getByRole('button',{name:'Sign in with Claude'}));
 assert.ok(await screen.findByText('That’s your personal account'));
 assert.ok(screen.getByText('me@example.test already runs your Personal projects, so it can’t be a work account too. Nothing was added.'));
 assert.ok(screen.getByText('Signing in as someone else? Sign out of claude.ai in your browser first, or the same account signs in again.'));
 assert.equal(screen.queryByRole('button',{name:'Add this account'}),null,'never accepted');
 answer={...answer,identity:{state:'signed_in',canSend:false,detail:'',signedInAs:'you@acme.test',plan:'team'}};
 fireEvent.click(screen.getByRole('button',{name:'Use a different account'}));
 await waitFor(()=>assert.equal(calls.filter(([op])=>op==='account_signin_start').length,2));
 assert.deepEqual(calls.find(([op])=>op==='account_signin_cancel')[1],{pendingId:'w9'},'the refused sign-in’s folder goes');
 fireEvent.click(await screen.findByRole('button',{name:'Add this account'}));
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='account_signin_add')[1],{pendingId:'w9'}));
 // Added: the host now reports it ready, and the card becomes what it's for.
 view.again({work:workSide(true)});
 assert.ok(await screen.findByText('Work account added.'));
 assert.match(screen.getByText('Work account added.').closest('p').textContent,/Claude Code · you@acme\.test · Team plan/);
 assert.ok(screen.getByText('Runs billing-api, checkout-web, data-exports and anything else under Work. It never runs a Personal project.'));
 assert.ok(screen.getByRole('button',{name:'Bring in 5 projects'}));
});

test('someone with only personal projects sees one card, no sides and no why',async()=>{
 await suggest({survey:personalFixture,work:workSide(false)});
 assert.deepEqual(groups(),['Projects']);
 assert.equal(screen.queryByText(/Work code only ever goes/),null);
 assert.equal(screen.queryByText(/No work account yet|wait for a work account/),null);
 assert.ok(screen.getByText('All of these look personal. If one is your employer’s, hover it and choose Move to Work.'));
 assert.ok(screen.getByRole('button',{name:'Bring in 2 projects'}));
 // Move to Work is there on hover, and using it brings the sides in.
 fireEvent.click(screen.getByRole('button',{name:'Move recipe-box to Work'}));
 assert.deepEqual(groups(),['Work','Personal']);
 assert.ok(screen.getByText(/Work code only ever goes/));
});

test('Bring in is held only when the Personal ones have no agent or a name is empty, and says why',async()=>{
 await suggest({agent:agent({ready:false,line:'Claude Code isn’t installed on this Mac. Bring in waits until an agent is connected.'}),work:workSide(true)});
 const bring=()=>screen.getByRole('button',{name:/^Bring in/});
 assert.equal(bring().disabled,true);
 assert.equal(screen.getByRole('status').textContent,'Claude Code isn’t installed on this Mac. Bring in waits until an agent is connected.');
 assert.equal(bring().getAttribute('aria-describedby'),screen.getByRole('status').id,'the reason is tied to the button');
 assert.equal(within(screen.getByRole('group',{name:'Personal'})).queryByText(/^Runs on/),null,'no account to name until one is ready');
 for(const name of ['tidepool','recipe-box'])fireEvent.click(screen.getByRole('checkbox',{name}));
 assert.equal(bring().textContent,'Bring in 3 projects','the Work ones come in on the work account anyway');
 assert.equal(bring().disabled,false);
 cleanup();
 await suggest({work:workSide(true)});
 fireEvent.click(screen.getByRole('button',{name:'Rename tidepool'}));
 fireEvent.change(screen.getByRole('textbox',{name:/Name for/}),{target:{value:'  '}});
 assert.equal(screen.getByRole('button',{name:'Bring in 5 projects'}).disabled,true);
 assert.equal(screen.getByRole('status').textContent,'Give the ticked project a name to bring it in.');
});

test('the second yes reads each folder with its own side’s account, and Bring in sends each side its account',async()=>{
 const candidates=[{sessionId:'s1',name:'Tide sync retries',source:'claude',last_active:null,reason:'recent',recommended:true,excerpt:''},
  {sessionId:'s2',name:'Chart colours',source:'claude',last_active:null,reason:'idle 20 days',recommended:false,excerpt:''}];
 const calls=host({
  onboarding_threads:a=>({projects:[{key:a.projects[0].key,candidates:a.projects[0].key==='k-tidepool'?candidates:[],limit:50,detail:null}]}),
  onboarding_bring_in:a=>({batchId:'b1',at:0,undone:false,projects:a.projects.map((p,n)=>({id:'p'+n,key:p.key,name:p.name,threads:0,context:p.context})),
   threads:0,problems:[],undoable:true,blocked:null,waiting:[]}),
  history_read:()=>({folder:'',counts:{conversations:1,sessions:2,working:1},set_aside:1,read:[],workstreams:[{id:'w',name:'Sync',rule:'new workstream',threads:[{id:'t1'}]}],question:null,at:0}),
 });
 const done=[];let prepared=0;
 await suggest({agent:agent({prepare:async()=>{prepared++;return {profileId:'me',model:'claude-opus-5-5'};}}),work:workSide(true),onDone:id=>done.push(id)});
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects'}));
 assert.ok(await screen.findByRole('heading',{name:'Name the threads too?'}));
 assert.equal(calls.length,0,'no message text is read before the second yes');
 assert.equal(screen.getAllByText('Work').filter(e=>e.closest('.dv-work-tag')).length,3,'Work ones carry the mark');
 fireEvent.click(screen.getByRole('button',{name:'Read and name them'}));
 await screen.findByRole('checkbox',{name:/Tide sync retries/});
 assert.deepEqual(calls.filter(([op])=>op==='onboarding_threads').map(([,a])=>a.projects),
  [[{key:'k-tidepool',context:'personal',profileId:'me'}],[{key:'k-recipes',context:'personal',profileId:'me'}],
   [{key:'k-billing',context:'work',profileId:'w1'}],[{key:'k-checkout',context:'work',profileId:'w1'}],[{key:'k-exports',context:'work',profileId:'w1'}]],
  'every ticked folder, one per call, keys only, each with its own side’s account');
 assert.equal(screen.getByRole('checkbox',{name:/Tide sync retries/}).checked,true);
 assert.equal(screen.getByRole('checkbox',{name:/Chart colours/}).checked,false,'finished ones listed, unticked');
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects and 1 thread'}));
 await waitFor(()=>assert.deepEqual(done,['p0']));
 assert.equal(prepared,1,'the personal account is asked for once');
 const sent=calls.find(([op])=>op==='onboarding_bring_in')[1];
 assert.deepEqual(sent.projects.map(p=>[p.key,p.context]),[['k-tidepool','personal'],['k-recipes','personal'],['k-billing','work'],['k-checkout','work'],['k-exports','work']]);
 assert.deepEqual([sent.profileId,sent.model,sent.work],['me','claude-opus-5-5',{profileId:'w1',model:'claude-opus-5-5'}]);
 assert.ok(!JSON.stringify(sent).includes('/'),'keys, never paths');
 assert.deepEqual(calls.filter(([op])=>op==='history_read').map(([,a])=>[a.projectId,a.sessionIds]),[['p0',['s1']]]);
});

test('with no work account the Work folders go as waiting, never on the personal account, and nothing of theirs is read',async()=>{
 const calls=host({
  onboarding_threads:a=>({projects:[{key:a.projects[0].key,candidates:[],limit:50,detail:null}]}),
  onboarding_bring_in:a=>({batchId:'b1',at:0,undone:false,projects:a.projects.filter(p=>p.context==='personal').map((p,n)=>({id:'p'+n,key:p.key,name:p.name,threads:0,context:'personal'})),
   threads:0,problems:[],undoable:true,blocked:null,waiting:a.projects.filter(p=>p.context==='work').map(p=>({name:p.name,path:'x'}))}),
 });
 const done=[];
 await suggest({work:workSide(false),onDone:id=>done.push(id)});
 fireEvent.click(screen.getByRole('button',{name:'Bring in 2 projects'}));
 fireEvent.click(await screen.findByRole('button',{name:'Read and name them'}));
 await screen.findByRole('button',{name:'Bring in 2 projects'});
 assert.deepEqual(calls.filter(([op])=>op==='onboarding_threads').map(([,a])=>a.projects[0].key),['k-tidepool','k-recipes'],'no Work folder is read');
 fireEvent.click(screen.getByRole('button',{name:'Bring in 2 projects'}));
 await waitFor(()=>assert.deepEqual(done,['p0']));
 const sent=calls.find(([op])=>op==='onboarding_bring_in')[1];
 assert.deepEqual(sent.projects.map(p=>p.context),['personal','personal','work','work','work']);
 assert.equal('work' in sent,false,'no work account is sent, so the Work ones wait');
});

test('a Mac with nothing on it says so and offers one folder',async()=>{
 let back=0;
 const empty={...surveyFixture,projects:[],loose_ends:null,set_aside:[],counts:{files:0,folders:0,working:0,quieter:0,loose:0}};
 show({survey:empty,onBack:()=>back++});
 assert.ok(await screen.findByRole('heading',{name:'Nothing from Claude Code or Codex on this Mac yet.'}));
 fireEvent.click(screen.getByRole('button',{name:'Choose one folder'}));
 assert.equal(back,1);
});

// ---------------------------------------------------------------- beta.17.1 import fixes
const {carryPicks,LOOK_MS}=await import('./onboarding.ts');
const STALE='That look is more than an hour old. Look at this Mac again before bringing anything in.';
const batchOf=a=>({batchId:'b1',at:0,undone:false,projects:a.projects.map((p,n)=>({id:'p'+n,key:p.key,name:p.name,threads:0,context:p.context})),threads:0,problems:[],undoable:true,blocked:null,waiting:[]});
const READ={folder:'',counts:{conversations:1,sessions:1,working:1},set_aside:0,read:[],workstreams:[],question:null,at:0};

test('looking again carries your picks over by key, keeps a brought-back folder the look still lists, and names what’s gone',()=>{
 const before=initialPicks(surveyFixture).map(p=>p.key==='k-tidepool'?{...p,name:'Tides',context:'personal',picked:true}:p.key==='k-recipes'?{...p,ticked:false}:p);
 const restored=surveyFixture.set_aside.flatMap(pile=>pile.folders).find(folder=>folder.key);
 const withRestored=restored?restorePick(before,restored):before;
 const next={...surveyFixture,surveyId:'survey-2',projects:surveyFixture.projects.filter(p=>p.key!=='k-exports')};
 const {picks,gone}=carryPicks(withRestored,next);
 assert.equal(picks.find(p=>p.key==='k-tidepool').name,'Tides','a rename carries over');
 assert.equal(picks.find(p=>p.key==='k-tidepool').picked,true);
 assert.equal(picks.find(p=>p.key==='k-recipes').ticked,false,'an untick carries over');
 assert.equal(picks.some(p=>p.key==='k-exports'),false);
 assert.deepEqual(gone,[surveyFixture.projects.find(p=>p.key==='k-exports').name]);
 if(restored)assert.ok(picks.some(p=>p.key===restored.key&&p.restored),'a folder you brought back stays while the look lists it');
});

test('NEXT-UP #36: an hour-old look says so on Threads, and Scan this Mac again replaces it in place',async()=>{
 const calls=host({onboarding_look:{...surveyFixture,surveyId:'survey-2'},
  onboarding_threads:a=>({projects:[{key:a.projects[0].key,candidates:[],limit:50,detail:null,toolRuns:0}]})});
 await suggest({work:workSide(true),lookedAt:Date.now()-LOOK_MS-1000});
 assert.match(screen.getByText(/more than an hour old/).textContent,/needs a new one/);
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects'}));
 await screen.findByRole('heading',{name:'Name the threads too?'});
 assert.equal(screen.getByRole('button',{name:'Read and name them'}).disabled,true,'nothing is sent with a look the host would refuse');
 assert.equal(calls.length,0);
 fireEvent.click(screen.getByRole('button',{name:'Scan this Mac again'}));
 await screen.findByText('Looked at this Mac again. Your picks are as you left them.');
 assert.deepEqual(calls.map(([op])=>op),['onboarding_look']);
 assert.equal(screen.queryByText(/more than an hour old/),null);
 assert.ok(screen.getByRole('heading',{name:'Name the threads too?'}),'still on Threads, not back at the start');
 fireEvent.click(screen.getByRole('button',{name:'Read and name them'}));
 await waitFor(()=>assert.equal(calls.filter(([op])=>op==='onboarding_threads').length,5));
 assert.deepEqual([...new Set(calls.filter(([op])=>op==='onboarding_threads').map(([,a])=>a.surveyId))],['survey-2'],'the new look, never the stale one');
});

test('NEXT-UP #36: when the host refuses the look, the same action shows instead of a dead button',async()=>{
 let looks=0;
 const calls=host({onboarding_look:()=>({...surveyFixture,surveyId:`survey-${++looks+1}`}),
  onboarding_threads:a=>{if(a.surveyId==='survey-fixture')throw STALE;return {projects:[{key:a.projects[0].key,candidates:[],limit:50,detail:null,toolRuns:0}]};}});
 await suggest({work:workSide(true)});
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects'}));
 fireEvent.click(await screen.findByRole('button',{name:'Read and name them'}));
 await screen.findByRole('button',{name:'Scan this Mac again'});
 assert.equal(screen.queryByRole('alert'),null,'said once, in the box with its action, not as a raw error');
 assert.equal(screen.getByRole('button',{name:'Read and name them'}).disabled,true);
 fireEvent.click(screen.getByRole('button',{name:'Scan this Mac again'}));
 await screen.findByText('Looked at this Mac again. Your picks are as you left them.');
 fireEvent.click(screen.getByRole('button',{name:'Read and name them'}));
 await screen.findByRole('button',{name:'Bring in 5 projects'});
 assert.deepEqual(calls.filter(([op])=>op==='onboarding_threads').map(([,a])=>a.surveyId),['survey-fixture','survey-2','survey-2','survey-2','survey-2','survey-2']);
});

test('NEXT-UP #36: a look the host no longer has (it restarted) gets its own words and the same way on',async()=>{
 host({onboarding_look:{...surveyFixture,surveyId:'survey-2'},
  onboarding_threads:()=>{throw 'Look at this Mac again. The last look isn’t available any more.';}});
 await suggest({work:workSide(true)});
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects'}));
 fireEvent.click(await screen.findByRole('button',{name:'Read and name them'}));
 assert.ok(await screen.findByText(/doesn’t have this look at your Mac any more/));
 assert.ok(screen.getByRole('button',{name:'Scan this Mac again'}));
});

test('NEXT-UP #37 and #38 on Threads: an unnamed conversation shows and takes a name, and tool runs are an opt-in sent with the read',async()=>{
 const own=[{sessionId:'u1',name:'',unnamed:true,source:'claude',last_active:null,reason:'recent',recommended:true,excerpt:'Look at ~/code/tidepool/src/sync.ts',toolRun:false,ranBy:null}];
 const tool={sessionId:'x1',name:'Nightly tide import',source:'codex',last_active:null,reason:'recent',recommended:true,excerpt:'',toolRun:true,ranBy:'exec'};
 const calls=host({
  onboarding_threads:a=>({projects:[{key:a.projects[0].key,candidates:a.projects[0].key==='k-tidepool'?(a.includeToolRuns?[...own,tool]:own):[],limit:50,detail:null,toolRuns:a.projects[0].key==='k-tidepool'?1:0}]}),
  onboarding_bring_in:batchOf,history_read:()=>READ});
 const done=[];
 await suggest({work:workSide(true),onDone:id=>done.push(id)});
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects'}));
 fireEvent.click(await screen.findByRole('button',{name:'Read and name them'}));
 const unnamed=await screen.findByRole('checkbox',{name:/Couldn’t safely generate a name/});
 assert.equal(unnamed.checked,true);
 assert.ok(screen.getByText('Look at ~/code/tidepool/src/sync.ts'));
 const option=screen.getByRole('checkbox',{name:/Include sessions run by a tool \(1\)/});
 assert.equal(option.checked,false,'off by default');
 assert.ok(calls.filter(([op])=>op==='onboarding_threads').every(([,a])=>!('includeToolRuns' in a)));
 fireEvent.click(option);
 const toolBox=await screen.findByRole('checkbox',{name:/Nightly tide import/});
 assert.match(toolBox.closest('label').textContent,/Run by a tool \(exec\), not you/);
 assert.equal(screen.getByRole('checkbox',{name:/Couldn’t safely generate a name/}).checked,true,'reading again keeps your ticks');
 assert.equal(toolBox.checked,true,'open, so ticked once you ask for them');
 fireEvent.click(toolBox);
 assert.equal(toolBox.checked,false);
 fireEvent.click(toolBox);
 fireEvent.change(screen.getByRole('textbox',{name:'Name for this conversation'}),{target:{value:'Sync crash'}});
 fireEvent.click(screen.getByRole('button',{name:'Bring in 5 projects and 2 threads'}));
 await waitFor(()=>assert.deepEqual(done,['p0']));
 assert.deepEqual(calls.find(([op])=>op==='history_read')[1],{projectId:'p0',sessionIds:['u1','x1'],names:{u1:'Sync crash'},includeToolRuns:true});
});
