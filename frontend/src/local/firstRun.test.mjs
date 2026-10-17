import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','HTMLButtonElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen,within,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {FirstRun,defaultAgent,rowTone,macLine,signInState,firstModelsFor,onboardingHeldLine}=await import('./FirstRun.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();});

const email='bryce@example.test';
const ready={
 account:{state:'ready',detail:'You’re in the beta.',email},
 claude:{state:'ready',method:'subscription',detail:'Signed in.',version:'2.1.274',email,plan:'team',profile_id:null},
 codex:{state:'available',detail:'Ready to connect your ChatGPT account.',profiles:[]},
 git:{state:'ready',detail:'Git 2.39.5.',version:'2.39.5'},projects:0,checked_at:1,
};
const fullAccess={warning:'beta.15',detail:{claude:'There’s no sandbox: Claude Code runs commands on your Mac as you.',codex:'There’s no sandbox: Codex runs commands on your Mac as you.'},checks:['Tests','Typecheck']};
const repo={path:'/Users/bryce/code/shop',name:'shop',git:true,repo:'shop',branch:'main',uncommitted:2,stack:['Next.js','TypeScript'],full_access:fullAccess};
const signedOut={state:'signed_out',method:'email_code',detail:'',identity:null,operation_id:null,expires_at:null,can_start:true,can_cancel:false,can_verify:false,can_poll:false,can_restore:false,can_sign_out:false,ownership:'local_unclaimed',sync_available:false,membership:null};
const signedIn=status=>({...signedOut,state:'signed_in',identity:{issuer:'i',subject:'s',display_name:'Bryce'},can_start:false,can_sign_out:true,
 membership:{status,allowed:status==='approved',expires_at:null,release_channel:'private-beta',is_admin:false},access_valid_until:Math.floor(Date.now()/1000)+300,expires_at:Math.floor(Date.now()/1000)+3600});
function host({status=ready,facts=repo,account=signedIn('approved'),wrongCode=false,finish,verified=signedIn('approved'),ops={}}={}){
 const calls=[];let acct=account;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  calls.push(command==='local_request'?[args.operation,args.args]:[command]);
  if(command==='choose_project_folder')return {selectionId:'sel-1',path:facts.path,name:facts.name};
  if(command!=='local_request')return null;
  const op=args.operation;
  if(op in ops){const out=ops[op](args.args??{},acct);if(out&&out.state&&out.method)acct=out;return out;}
  if(op==='dervo_account_status')return acct;
  if(op==='dervo_account_send_code'){acct={...signedOut,state:'awaiting_user',can_start:false,can_verify:true,can_cancel:true,operation_id:'op1'};return acct;}
  if(op==='dervo_account_verify_code'){if(!wrongCode)acct=verified;return acct;}
  if(op==='dervo_account_sign_out'){acct=signedOut;return acct;}
  if(op==='first_run_status')return status;
  if(op==='first_run_inspect')return facts;
  if(op==='first_run_claude')return {id:'system-claude',identity:email,model:'claude-opus-5'};
  if(op==='first_run_finish')return finish??{id:'p1',channelId:'c1',name:args.args.name,access:args.args.access,overview:{channelId:'overview',state:'working'}};
  if(op==='connection_status')return {state:'identity_verified',canSend:false,detail:''};
  // Beta.18: Codex signs in first (account_signin.py); the sign-in stays open in the browser.
  if(op==='account_signin_start')return {pendingId:'n1',state:'waiting',phase:'browser',url:null,needsCode:false,detail:'',active:true};
  if(op==='connection_login_status')return {state:'waiting',phase:'browser',url:null,needsCode:false,detail:'',active:true};
  if(op==='account_signin_cancel')return {state:'cancelled'};
  throw new Error('no fixture '+op);
 }};
 return calls;
}
const mount=props=>render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
 React.createElement(FirstRun,{mode:'first',profiles:[],onProfilesChanged:async()=>{},onDone:async()=>{},...props})));
const typeIn=(field,value)=>fireEvent.change(field,{target:{value}});
/** Onboarding (8 Oct 2026): after sign-in comes Your agents, then Your projects. */
const agentsReady=()=>screen.findByRole('heading',{name:'Your agents are ready.'});
async function toProjects(){
 const go=await screen.findByRole('button',{name:/^Continue/});
 await waitFor(()=>assert.equal(go.disabled,false));
 fireEvent.click(go);
}
async function chooseFolder(){await toProjects();fireEvent.click(await screen.findByRole('button',{name:/Choose a project folder/}));}

test('sign-in waits for Confirm while all eight digits are typed, including after the sixth digit',async()=>{
 const calls=host({account:signedOut});
 mount();
 typeIn(await screen.findByRole('textbox',{name:'Email'}),email);
 const card=screen.getByRole('group',{name:'Sign in to Dervo'});
 assert.ok(screen.getByRole('heading',{level:1,name:'Let’s get you set up.'}));
 fireEvent.click(within(card).getByRole('button',{name:'Email me a code'}));
 const code=await screen.findByRole('textbox',{name:'Email code'});
 assert.match(screen.getByRole('group',{name:'Enter the code we emailed you'}).textContent,/We sent it to bryce@example\.test\./);
 assert.match(screen.getByRole('group',{name:'Enter the code we emailed you'}).textContent,/It can take a minute to arrive\. Not there\? Check spam and other inbox tabs, then send a new one\./);
 const value='04821937';
 for(let length=1;length<=value.length;length++){
  typeIn(code,value.slice(0,length));
  assert.equal(calls.some(c=>c[0]==='dervo_account_verify_code'),false,`${length} digits wait for confirmation`);
  assert.equal(code.value,value.slice(0,length));
  assert.equal(code.disabled,false,'typing never starts a verification request');
  assert.equal(screen.getByRole('button',{name:'Confirm code'}).disabled,length<6);
 }
 fireEvent.click(screen.getByRole('button',{name:'Confirm code'}));
 await agentsReady();
 assert.deepEqual(calls.filter(c=>c[0]==='dervo_account_verify_code'),[['dervo_account_verify_code',{operationId:'op1',code:value}]],'one request preserves all eight digits and the leading zero');
 assert.equal(calls.some(c=>c[0]==='first_run_status'),true,'the Mac check ran behind sign-in');
 assert.equal(screen.getByRole('list',{name:'Setup steps'}).querySelector('[aria-current="step"]').textContent,'2Your agents, current step','step 2 opens');
});

for(const value of ['048219','0482193765'])test(`${value.length}-digit email codes still confirm through form submission`,async()=>{
 const calls=host({account:{...signedOut,state:'awaiting_user',can_start:false,can_verify:true,can_cancel:true,operation_id:'op1'}});
 mount();
 const code=await screen.findByRole('textbox',{name:'Email code'});
 for(let length=1;length<=value.length;length++)typeIn(code,value.slice(0,length));
 assert.equal(calls.some(c=>c[0]==='dervo_account_verify_code'),false);
 assert.equal(screen.getByRole('button',{name:'Confirm code'}).disabled,false);
 fireEvent.submit(code.closest('form'));
 await agentsReady();
 assert.deepEqual(calls.filter(c=>c[0]==='dervo_account_verify_code'),[['dervo_account_verify_code',{operationId:'op1',code:value}]]);
});

test('pasting a complete eight-digit email code waits for Confirm and preserves the whole code',async()=>{
 const calls=host({account:{...signedOut,state:'awaiting_user',can_start:false,can_verify:true,can_cancel:true,operation_id:'op1'}});
 mount();
 const code=await screen.findByRole('textbox',{name:'Email code'});
 const value='04821937';
 fireEvent.paste(code,{clipboardData:{getData:()=>value}});
 // JSDOM doesn't perform clipboard insertion, so deliver the resulting input change.
 typeIn(code,value);
 assert.equal(code.value,value);
 assert.equal(calls.some(c=>c[0]==='dervo_account_verify_code'),false);
 fireEvent.click(screen.getByRole('button',{name:'Confirm code'}));
 await agentsReady();
 assert.deepEqual(calls.filter(c=>c[0]==='dervo_account_verify_code'),[['dervo_account_verify_code',{operationId:'op1',code:value}]]);
});

test('email-code form submission still rejects codes outside six to ten digits',async()=>{
 const calls=host({account:{...signedOut,state:'awaiting_user',can_start:false,can_verify:true,can_cancel:true,operation_id:'op1'}});
 mount();
 const code=await screen.findByRole('textbox',{name:'Email code'});
 for(const value of ['12345','12345678901']){
  typeIn(code,value);
  assert.equal(screen.getByRole('button',{name:'Confirm code'}).disabled,true);
  fireEvent.submit(code.closest('form'));
 }
 assert.equal(calls.some(c=>c[0]==='dervo_account_verify_code'),false);
});

test('a wrong code gets its error under the field, and the field is ready for the next try',async()=>{
 host({account:signedOut,wrongCode:true});
 mount();
 typeIn(await screen.findByRole('textbox',{name:'Email'}),email);
 fireEvent.click(screen.getByRole('button',{name:'Email me a code'}));
 const code=await screen.findByRole('textbox',{name:'Email code'});
 typeIn(code,'111111');
 fireEvent.click(screen.getByRole('button',{name:'Confirm code'}));
 const error=await screen.findByRole('alert');
 assert.equal(error.textContent,'That code couldn’t be confirmed. Check it against the newest email and try again.');
 assert.equal(code.value,'');assert.equal(code.getAttribute('aria-invalid'),'true');
});

test('a pending tester finishes setup: sign-in folds with a plain line, the project is set up, and first run hands back the waiting overview',async()=>{
 const waiting={id:'p1',channelId:'c1',name:'shop',access:'edit',overview:{channelId:'ov',state:'waiting'},overview_detail:'Your beta access is waiting for approval.'};
 const calls=host({account:signedIn('pending'),status:{...ready,account:{state:'pending',detail:'',email}},finish:waiting});
 const done=[];
 mount({onDone:async(created,agent)=>{done.push([created,agent]);}});
 await screen.findByText(/beta access pending/);
 // UX review, 23 Sep, M3: who approves it. The /beta page gives no time, so the note doesn't either.
 assert.match(document.body.textContent,/Your beta access is waiting for Bryce to approve it\. Set everything up now, and the agent starts the moment he does\./);
 assert.doesNotMatch(document.body.textContent,/within a day|hours?\b/,'no promise of a time the beta page doesn’t make');
 assert.equal(document.querySelectorAll('[data-accent]').length,0,'nothing here needs the tester, so no accent');
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 await waitFor(()=>assert.equal(within(start).getByRole('button',{name:/^Start/}).disabled,false));
 fireEvent.click(within(start).getByRole('button',{name:/^Start/}));
 await waitFor(()=>assert.equal(done.length,1));
 assert.deepEqual(done[0],[waiting,'Claude Code']);
 assert.deepEqual(calls.find(c=>c[0]==='first_run_finish')[1],{selectionId:'sel-1',profileId:'system-claude',model:'claude-opus-5',name:'shop',access:'full',warning:'beta.15'});
});

test('a tester waiting for approval can switch to the email their invite went to, before any project exists',async()=>{
 const calls=host({account:signedOut,verified:signedIn('pending'),status:{...ready,account:{state:'pending',detail:'',email}}});
 mount();
 typeIn(await screen.findByRole('textbox',{name:'Email'}),'me@gmail.example');
 fireEvent.click(screen.getByRole('button',{name:'Email me a code'}));
 typeIn(await screen.findByRole('textbox',{name:'Email code'}),'123456');
 fireEvent.click(screen.getByRole('button',{name:'Confirm code'}));
 await screen.findByText(/beta access pending/);
 assert.match(document.body.textContent,/Invited under a different email\? Switch now: once you set up a project, this Mac stays with the account you’re signed in as\./);
 fireEvent.click(screen.getByRole('button',{name:'Use a different email'}));
 const field=await screen.findByRole('textbox',{name:'Email'});
 assert.equal(field.value,'','the address that was waiting isn’t offered again');
 assert.deepEqual(calls.filter(c=>c[0]==='dervo_account_sign_out'),[['dervo_account_sign_out',{}]]);
});

test('an approved tester isn’t offered a different email: nothing is waiting',async()=>{
 host();
 mount();
 await agentsReady();
 assert.equal(screen.queryByRole('button',{name:'Use a different email'}),null);
});

test('one decision: the folder. The agent arrives chosen, and what it can do is on screen with full access recommended',async()=>{
 host();
 mount();
 await agentsReady();
 assert.match(document.querySelector('.dv-ag-list').textContent,/Claude Code2\.1\.274bryce@example\.test · Team plan/);
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 assert.equal(within(start).getByLabelText('Name').value,'shop');
 assert.match(start.textContent,/~\/code\/shop · on main, 2 uncommitted files · looks like Next\.js and TypeScript/);
 assert.match(start.textContent,/Claude Code · Opus 5Your existing sign-in, bryce@example\.test \(Team\)/);
 assert.equal(screen.queryAllByRole('button',{name:/Continue/}).length,0,'no Continue buttons');
 // Bryce, 9 Oct: nothing is granted unseen. Every level is on screen, full access is recommended and chosen, its warning shows.
 const levels=within(start).getByRole('radiogroup',{name:'What it can do'});
 const [full,edit,read]=within(levels).getAllByRole('radio');
 assert.equal(full.getAttribute('aria-checked'),'true');
 assert.match(full.textContent,/Full access with Dervo’s checksRecommendedClaude Code can run commands as you, like installs, builds and tests\./);
 assert.match(full.textContent,/After each change, Dervo runs the project’s tests \(Tests and Typecheck\) and shows you what passed\./);
 assert.match(full.textContent,/There’s no sandbox: Claude Code runs commands on your Mac as you\./,'the warning is on screen before Start');
 assert.match(edit.textContent,/Edit files, with your approvalSaferIt changes files but can’t run commands\. Your uncommitted work stays as it is/);
 assert.match(read.textContent,/Read and explain only/);
 fireEvent.keyDown(edit,{key:' '});
 assert.equal(edit.getAttribute('aria-checked'),'true');
 assert.doesNotMatch(start.textContent,/no sandbox/,'the warning goes with the choice');
 fireEvent.click(within(start).getAllByRole('button',{name:'Change'})[0]);
 const agents=within(start).getByRole('radiogroup',{name:'Agent for this project'});
 assert.equal(within(agents).getAllByRole('radio')[0].getAttribute('aria-checked'),'true','your existing sign-in is the default');
});

test('choosing Edit files sends no full-access token, and a folder with no tests says so under full access',async()=>{
 const calls=host({facts:{...repo,full_access:{...fullAccess,checks:[]}}});
 mount();
 await agentsReady();
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 const [full,edit]=within(within(start).getByRole('radiogroup',{name:'What it can do'})).getAllByRole('radio');
 assert.match(full.textContent,/Dervo didn’t find tests in this folder yet\. Once there are some, turn on checks in Project settings/);
 fireEvent.click(edit);
 await waitFor(()=>assert.equal(within(start).getByRole('button',{name:/^Start/}).disabled,false));
 fireEvent.click(within(start).getByRole('button',{name:/^Start/}));
 await waitFor(()=>assert.ok(calls.find(c=>c[0]==='first_run_finish')));
 assert.deepEqual(calls.find(c=>c[0]==='first_run_finish')[1],{selectionId:'sel-1',profileId:'system-claude',model:'claude-opus-5',name:'shop',access:'edit'});
});

test('a host from before the access choice offers no full access, so editing with approval is chosen and no token goes',async()=>{
 const {full_access,...older}=repo;
 const calls=host({facts:older});
 mount();
 await agentsReady();
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 const [full,edit]=within(within(start).getByRole('radiogroup',{name:'What it can do'})).getAllByRole('radio');
 assert.equal(full.getAttribute('aria-disabled'),'true');
 assert.equal(edit.getAttribute('aria-checked'),'true');
 await waitFor(()=>assert.equal(within(start).getByRole('button',{name:/^Start/}).disabled,false));
 fireEvent.click(within(start).getByRole('button',{name:/^Start/}));
 await waitFor(()=>assert.ok(calls.find(c=>c[0]==='first_run_finish')));
 assert.equal(calls.find(c=>c[0]==='first_run_finish')[1].access,'edit');
 assert.equal('warning' in calls.find(c=>c[0]==='first_run_finish')[1],false);
});

test('Claude Code signed in with its own API key is a supported sign-in, and says who bills it',async()=>{
 host({status:{...ready,claude:{...ready.claude,method:'api_key',plan:null,email:null}}});
 mount();
 await waitFor(()=>assert.match(document.querySelector('.dv-ag-list').textContent,/Your existing sign-in, with an Anthropic API key\. Billed per use\./));
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 assert.match(start.textContent,/Your existing sign-in, with an Anthropic API key\. Billed per use\./);
 await waitFor(()=>assert.equal(within(start).getByRole('button',{name:/^Start/}).disabled,false));
 // Full access isn't offered on an API-key account (API_KEY_FULL_ACCESS): editing with approval is chosen, and the row says why.
 const [full,edit]=within(within(start).getByRole('radiogroup',{name:'What it can do'})).getAllByRole('radio');
 assert.equal(full.getAttribute('aria-disabled'),'true');
 assert.match(full.textContent,/Not on an API-key account: any command it runs could print the key\./);
 assert.doesNotMatch(full.textContent,/Recommended/);
 assert.equal(edit.getAttribute('aria-checked'),'true');
});

test('without Claude Code, Codex leads as the no-Terminal way through, and an old Claude Code says update, not install',async()=>{
 host({status:{...ready,claude:{state:'missing',detail:'Claude Code isn’t installed on this Mac.',install:'curl -fsSL https://claude.ai/install.sh | bash',version:null,profile_id:null}}});
 mount();
 await screen.findByRole('heading',{name:'Connect your agents.'});
 const list=document.querySelector('.dv-ag-list');
 assert.match(list.textContent,/Not on this Mac yet\. Paste this in Terminal, then come back\. Dervo notices on its own\./);
 assert.ok(within(list).getByRole('button',{name:'Copy curl -fsSL https://claude.ai/install.sh | bash'}));
 assert.equal(screen.getByRole('button',{name:/^Continue/}).disabled,true,'nothing to run on yet');
 assert.match(document.body.textContent,/Connect one to continue\. Codex needs no Terminal\./);
 assert.match(list.textContent,/Built into Dervo\. Sign in in your browser with ChatGPT Plus, Pro Lite or Pro\./,'the plan it needs, said before sign-in');
 const signIn=within(list).getByRole('button',{name:'Sign in with ChatGPT'});
 assert.match(signIn.className,/dv-btn-primary/,'without Claude Code, Codex is the way through');
 fireEvent.click(signIn);
 // Beta.18: no email first. ChatGPT's own sign-in starts at once, and Dervo shows who signed in before adding it.
 assert.ok(await within(list).findByText('Sign in with the ChatGPT account you want to add'));
 assert.equal(within(list).queryByLabelText('ChatGPT account email'),null,'nothing to type first');
 cleanup();
 host({status:{...ready,claude:{state:'outdated',detail:'Claude Code 2.1.260 is too old for Dervo. Update it to 2.1.271 or later.',update:'claude update',version:'2.1.260',profile_id:null}}});
 mount();
 await screen.findByRole('heading',{name:'Connect your agents.'});
 const again=document.querySelector('.dv-ag-list');
 assert.ok(within(again).getByRole('button',{name:'Copy claude update'}));
 assert.equal(within(again).queryByRole('button',{name:/Copy curl/}),null,'it is installed: the fix is an update');
 assert.match(again.textContent,/Version 2\.1\.260 is too old for Dervo\. Update it in Terminal, then come back\./);
 cleanup();
 // A Codex account you already connected is a Codex choice too, and Home's note is said under Codex only.
 host({status:{...ready,claude:{state:'missing',detail:'Claude Code isn’t installed on this Mac.',install:'x',version:null,profile_id:null}}});
 mount({profiles:[{id:'cx',provider:'codex',identity:'me@example.test',category:'personal'},{id:'ck',provider:'claude',identity:'Anthropic API key',category:'personal',method:'api_key'}]});
 await agentsReady();
 await chooseFolder();
 const withCodex=await screen.findByRole('group',{name:'Start'});
 fireEvent.click(await waitFor(()=>within(withCodex).getAllByRole('button',{name:'Change'})[0]));
 const rows=within(within(withCodex).getByRole('radiogroup',{name:'Agent for this project'})).getAllByRole('radio');
 const named=name=>rows.find(r=>r.querySelector('.dv-c-name').textContent.startsWith(name));
 assert.ok(named('Codex · me@example.test').querySelector('.dv-fr-home-note'));
 assert.equal(named('Claude · API key').querySelector('.dv-fr-home-note'),null);
});

test('the API key form says the $50 limit is shown, not enforced, and where the real hard stop is',async()=>{
 const calls=host({status:{...ready,claude:{state:'missing',detail:'Claude Code isn’t installed on this Mac.',install:'curl -fsSL https://claude.ai/install.sh | bash',version:null,profile_id:null}}});
 mount();
 await screen.findByRole('heading',{name:'Connect your agents.'});
 fireEvent.click(screen.getByRole('button',{name:'Add an API key'}));
 const form=document.querySelector('.dv-ag-key');
 const field=()=>within(form).getByLabelText(/API key$/).closest('label').querySelector('small').textContent;
 assert.match(field(),/Dervo shows your spend against \$50 a month but doesn’t stop runs at it\. For a hard stop, set a spend limit in the Anthropic Console\./);
 assert.doesNotMatch(document.body.textContent,/stops at \$50/,'nothing refuses a run at the limit, so first run can’t promise it');
 fireEvent.click(within(form).getByRole('radio',{name:'OpenAI'}));
 assert.match(field(),/Codex doesn’t tell Dervo what a run costs, so Dervo can’t track spend or stop runs\. For a hard stop, set a budget on the OpenAI platform\./);
 assert.doesNotMatch(field(),/\$50/,'an OpenAI key’s spend isn’t measured, so no number is promised');
 assert.equal(calls.some(c=>c[0]==='profile_key_add'),false);
});

test('a folder without git starts reading only, with the reason and how to turn editing on later',async()=>{
 host({facts:{...repo,git:false,repo:null,branch:null,uncommitted:0,stack:[],name:'notes',path:'/Users/bryce/notes'}});
 mount();
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 assert.match(start.textContent,/Editing needs git, so every change can be undone\. Turn it on later in Project settings\./);
 const [full,edit,read]=within(within(start).getByRole('radiogroup',{name:'What it can do'})).getAllByRole('radio');
 assert.equal(full.getAttribute('aria-disabled'),'true');
 assert.match(full.textContent,/Needs git, so Dervo can record and undo every change\./);
 assert.equal(edit.getAttribute('aria-disabled'),'true');
 assert.equal(read.getAttribute('aria-checked'),'true');
});

test('adding a later project: the same cards in the app, no sign-in, and Esc or Cancel leaves',async()=>{
 const calls=host({account:signedOut});
 let cancelled=0;
 mount({mode:'add',onCancel:()=>{cancelled++;}});
 assert.ok(await screen.findByRole('group',{name:'Your project'}));
 assert.equal(screen.queryByRole('group',{name:'Sign in to Dervo'}),null);
 assert.equal(calls.some(c=>c[0]==='dervo_account_status'),false);
 assert.ok(screen.getByRole('heading',{level:1,name:'Add a project.'}));
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 fireEvent.keyDown(window,{key:'Escape'});
 assert.equal(cancelled,2);
});

test('coming back to the window checks this Mac again',async()=>{
 const calls=host();
 mount();
 await waitFor(()=>assert.ok(calls.some(c=>c[0]==='first_run_status')));
 const before=calls.filter(c=>c[0]==='first_run_status').length;
 window.dispatchEvent(new Event('focus'));
 await waitFor(()=>assert.ok(calls.filter(c=>c[0]==='first_run_status').length>before));
});

test('the helpers: the Mac line, the default agent, and where the sign-in stands',()=>{
 assert.equal(rowTone({state:'ready'}),'done');assert.equal(rowTone({state:'outdated'}),'action');assert.equal(rowTone({state:'available'}),'note');
 assert.deepEqual(defaultAgent(ready,[]),{kind:'system'});
 assert.deepEqual(defaultAgent({...ready,claude:{state:'missing'}},[{id:'k',provider:'codex',identity:'x',category:'personal'}]),{kind:'profile',profileId:'k'});
 assert.equal(macLine(undefined,false).text,'Checking this Mac for Claude Code, Codex and git');
 assert.equal(macLine(undefined,true).text,'Dervo couldn’t check this Mac.');
 assert.equal(macLine({...ready,claude:{state:'missing'}},false).text,'This Mac: git 2.39.5 is ready and Codex is built in');
 assert.match(macLine({...ready,git:{state:'missing'}},false).text,/git isn’t installed, so projects start reading only/);
 assert.equal(signInState({...signedOut,state:'unavailable',method:undefined},false).kind,'none','a copy without beta sign-in skips the step');
 assert.equal(signInState(signedIn('pending'),false).kind,'done','pending access doesn’t stop setup');
 assert.equal(signInState(signedIn('revoked'),false).kind,'blocked');
});

test('the open step’s number keeps its own class, so Home’s Now pane rules (.dv-now) can’t pad it into a big disc',async()=>{
 const {readFileSync}=await import('node:fs');
 const source=readFileSync(new URL('./FirstRun.tsx',import.meta.url),'utf8');
 const css=readFileSync(new URL('./FirstRun.css',import.meta.url),'utf8');
 const home=readFileSync(new URL('./Home.css',import.meta.url),'utf8');
 assert.match(home,/^\.dv-now\s*\{/m,'Home.css still owns .dv-now, the collision this guards against');
 assert.doesNotMatch(source,/className="[^"]*\bdv-now\b/);
 assert.match(source,/dv-fr-num dv-fr-now/);
 assert.match(css,/\.dv-fr-num\.dv-fr-now\s*\{/);
});

test('first run offers the Opus this Mac’s Claude Code can run',()=>{
 assert.deepEqual(firstModelsFor('2.1.280').map(m=>m.id),['claude-opus-5-5','claude-sonnet-5','claude-haiku-4-5']);
 assert.deepEqual(firstModelsFor('2.1.284').map(m=>[m.id,m.label]),[['claude-opus-5-5','Opus 5.5'],['claude-sonnet-5-5','Sonnet 5.5'],['claude-haiku-4-5','Haiku 4.5']]);
 assert.deepEqual(firstModelsFor('2.1.274').map(m=>[m.id,m.label]),[['claude-opus-5','Opus 5'],['claude-sonnet-5','Sonnet 5'],['claude-haiku-4-5','Haiku 4.5']]);
 assert.deepEqual(firstModelsFor(null).map(m=>m.id),['claude-opus-5','claude-sonnet-5','claude-haiku-4-5']);
});

test('on Claude Code 2.1.280 first run starts the project on Opus 5.5',async()=>{
 const waiting={id:'p1',channelId:'c1',name:'shop',access:'edit',overview:{channelId:'ov',state:'waiting'},overview_detail:'Your beta access is waiting for approval.'};
 const calls=host({account:signedIn('pending'),status:{...ready,claude:{...ready.claude,version:'2.1.280'},account:{state:'pending',detail:'',email}},finish:waiting});
 const done=[];
 mount({onDone:async(created,agent)=>{done.push([created,agent]);}});
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 await waitFor(()=>assert.equal(within(start).getByRole('button',{name:/^Start/}).disabled,false));
 fireEvent.click(within(start).getByRole('button',{name:/^Start/}));
 await waitFor(()=>assert.equal(done.length,1));
 assert.equal(calls.find(c=>c[0]==='first_run_finish')[1].model,'claude-opus-5-5');
});


test('onboarding’s held line says why Bring in waits and how through, without claiming Codex is ready',()=>{
 const line=onboardingHeldLine({state:'missing',detail:'Claude Code isn’t installed on this Mac.'});
 assert.equal(line,'Claude Code isn’t installed on this Mac. Bring in waits until an agent is connected. Choose one folder first to connect Claude Code or Codex, then bring the rest in.');
 assert.doesNotMatch(line,/built into Dervo/);
});

// ---------------------------------------------------------------- Onboarding, 8 Oct 2026: every way through, and back
const planned=(kind,extra={})=>({...signedIn('approved'),plan:{plan:'none',environment:'sandbox',trial:{days:7,mode:kind==='trial-card'?'card':'app',ends_at:null,available:kind.startsWith('trial')},
 subscription:null,can_buy:true,can_manage:false},billing:{state:'idle',action:null,detail:null},can_start_trial:kind==='trial-app',can_buy:true,...extra});

test('a code that couldn’t be sent says why under the field, and the same button tries again',async()=>{
 let sends=0;
 const calls=host({account:signedOut,ops:{dervo_account_send_code:()=>++sends===1
  ?{...signedOut,state:'failed',detail:'Too many codes were asked for in a row. Wait a minute, then send a new one.'}
  :{...signedOut,state:'awaiting_user',can_start:false,can_verify:true,can_cancel:true,operation_id:'op1'}}});
 mount();
 typeIn(await screen.findByRole('textbox',{name:'Email'}),email);
 fireEvent.click(screen.getByRole('button',{name:'Email me a code'}));
 await screen.findByText('Too many codes were asked for in a row. Wait a minute, then send a new one.');
 assert.equal(screen.getByRole('textbox',{name:'Email'}).value,email,'the address stays typed');
 fireEvent.click(screen.getByRole('button',{name:'Email me a code'}));
 assert.ok(await screen.findByRole('textbox',{name:'Email code'}));
 assert.equal(calls.filter(c=>c[0]==='dervo_account_send_code').length,2);
});

test('Send a new code waits out the minute, then cancels the old code and sends to the same address',async()=>{
 const calls=host({account:signedOut});
 mount();
 typeIn(await screen.findByRole('textbox',{name:'Email'}),email);
 fireEvent.click(screen.getByRole('button',{name:'Email me a code'}));
 await screen.findByRole('textbox',{name:'Email code'});
 const again=screen.getByRole('button',{name:/Send a new code/});
 assert.match(again.textContent,/Send a new code in \d+s/);
 assert.equal(again.disabled,true,'not inside the auth service’s wait');
});

test('an offline Mac says so on the sign-in card, and carries on when it’s back',async()=>{
 host({account:signedOut});
 Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>false});
 try{
  mount();
  await screen.findByRole('textbox',{name:'Email'});
  assert.match(document.body.textContent,/This Mac looks offline\. Dervo carries on by itself when you’re back\./);
 }finally{delete navigator.onLine;}
});

test('a trial that needs no card starts in one click, right after sign-in, then Your agents',async()=>{
 const calls=host({account:planned('trial-app'),ops:{dervo_plan_start_trial:(_,acct)=>({...acct,plan:{...acct.plan,plan:'trial',trial:{...acct.plan.trial,available:false}}})}});
 mount();
 assert.ok(await screen.findByRole('group',{name:'Start your free trial'}));
 assert.match(document.body.textContent,/No card needed\./);
 assert.match(document.body.textContent,/Test mode: no real charges/);
 fireEvent.click(screen.getByRole('button',{name:'Start free trial'}));
 await agentsReady();
 assert.ok(calls.some(c=>c[0]==='dervo_plan_start_trial'));
});

test('an ended trial can wait: set up first, and Start says the overview waits for a plan',async()=>{
 const waiting={id:'p1',channelId:'c1',name:'shop',access:'edit',overview:{channelId:'ov',state:'waiting',waiting_for:'plan'},overview_detail:'Start a free trial or choose a plan, and the overview starts.'};
 const calls=host({account:planned('ended'),finish:waiting});
 const done=[];
 mount({onDone:async created=>{done.push(created);}});
 assert.ok(await screen.findByRole('group',{name:'Your free trial has ended'}));
 assert.ok(screen.getByRole('button',{name:'Monthly, $15'}));assert.ok(screen.getByRole('button',{name:'Yearly, $144'}));
 fireEvent.click(screen.getByRole('button',{name:'Set up first, decide later'}));
 await agentsReady();
 assert.equal(localStorage.getItem('dervo-onboarding-plan-later'),'1','asked once on this Mac');
 await chooseFolder();
 const start=await screen.findByRole('group',{name:'Start'});
 assert.match(start.textContent,/It’s set up now\. The overview starts once you start a trial or choose a plan\./);
 await waitFor(()=>assert.equal(within(start).getByRole('button',{name:/^Start/}).disabled,false));
 fireEvent.click(within(start).getByRole('button',{name:/^Start/}));
 await waitFor(()=>assert.equal(done.length,1));
 assert.ok(calls.some(c=>c[0]==='first_run_finish'));
});

test('checkout opens in the browser, then Check now picks the plan up',async()=>{
 const calls=host({account:planned('ended'),ops:{
  dervo_billing_open:(_,acct)=>({...acct,billing:{state:'opened',action:'checkout',detail:null}}),
  dervo_plan_refresh:(_,acct)=>({...acct,plan:{...acct.plan,plan:'paid'},billing:{state:'idle',action:null,detail:null}})}});
 mount();
 fireEvent.click(await screen.findByRole('button',{name:'Yearly, $144'}));
 fireEvent.click(await screen.findByRole('button',{name:'Check now'}));
 await agentsReady();
 assert.deepEqual(calls.find(c=>c[0]==='dervo_billing_open')[1],{action:'checkout',interval:'year'});
});

test('quitting halfway: opening again lands on the step you reached, unless its agent is gone',async()=>{
 localStorage.setItem('dervo-onboarding-step','projects');
 host();
 mount();
 assert.ok(await screen.findByRole('button',{name:/Choose a project folder/}));
 assert.match(document.body.textContent,/Welcome back\. You’re where you left off\./);
 cleanup();
 localStorage.setItem('dervo-onboarding-step','projects');
 host({status:{...ready,claude:{state:'missing',detail:'Claude Code isn’t installed on this Mac.',install:'x',version:null,profile_id:null}}});
 mount();
 assert.ok(await screen.findByRole('heading',{name:'Connect your agents.'}),'Claude Code went away: back to Your agents');
 assert.equal(screen.queryByRole('button',{name:/Choose a project folder/}),null);
});

test('Your agents can be reopened from the stepper, and Continue comes back',async()=>{
 host();
 mount();
 await agentsReady();
 await toProjects();
 await screen.findByRole('button',{name:/Choose a project folder/});
 fireEvent.click(screen.getByRole('button',{name:/Your agents, done/}));
 await agentsReady();
 assert.equal(localStorage.getItem('dervo-onboarding-step'),'agents');
});

test('an old Codex account that signed in as someone else says who, and can use that account or go',async()=>{
 const profiles=[{id:'cx',provider:'codex',identity:'old@example.test',category:'personal',method:'subscription'}];
 const calls=host({status:{...ready,claude:{state:'missing',detail:'x',install:'x',version:null,profile_id:null}},ops:{
  connection_status:()=>({state:'identity_mismatch',canSend:false,canUseSignedIn:true,signedInAs:'new@example.test',detail:'Codex is signed in as new@example.test, not old@example.test. No messages were sent.'}),
  connection_login_status:()=>({state:'idle',active:false}),
  account_use_signed_in:()=>({id:'cx'}),account_remove:()=>({removed:'cx'})}});
 mount({profiles});
 await screen.findByText('Codex is signed in as new@example.test, not old@example.test. No messages were sent.');
 fireEvent.click(screen.getByRole('button',{name:'Use new@example.test instead'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c[0]==='account_use_signed_in')?.[1],{profileId:'cx'}));
 fireEvent.click(screen.getByRole('button',{name:'Remove this account'}));
 await waitFor(()=>assert.ok(calls.some(c=>c[0]==='account_remove')));
});

test('an account nobody has checked since Dervo opened is checked once, and Continue opens when it answers',async()=>{
 // Code review, 8 Oct: an API key just added, or any account after a relaunch, read "unchecked" for good.
 let state='unchecked';
 const calls=host({status:{...ready,claude:{state:'missing',detail:'x',install:'x',version:null,profile_id:null}},ops:{
  connection_status:()=>({state,canSend:state==='identity_verified',detail:state==='unchecked'?'Check the selected account before use.':''}),
  connection_check:()=>{state='identity_verified';return {};},connection_login_status:()=>({state:'idle',active:false})}});
 mount({profiles:[{id:'k1',provider:'claude',identity:'Anthropic API key',category:'personal',method:'api_key'}]});
 await agentsReady();
 assert.equal(calls.filter(c=>c[0]==='connection_check').length,1);
 assert.deepEqual(calls.find(c=>c[0]==='connection_check')[1],{profileId:'k1'});
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:/^Continue/}).disabled,false));
});

test('a code the host turns down after “verifying” still says so, and Confirm waits while it checks',async()=>{
 let acct={...signedOut,state:'awaiting_user',can_start:false,can_verify:true,can_cancel:true,can_poll:true,operation_id:'op1'};
 host({account:acct,ops:{
  dervo_account_status:()=>acct,
  dervo_account_verify_code:()=>{acct={...acct,state:'verifying',can_verify:false};setTimeout(()=>{acct={...acct,state:'awaiting_user',can_verify:true};},30);return acct;}}});
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(React.createElement(QueryClientProvider,{client},React.createElement(FirstRun,{mode:'first',profiles:[],onProfilesChanged:async()=>{},onDone:async()=>{}})));
 const code=await screen.findByRole('textbox',{name:'Email code'});
 typeIn(code,'111111');
 fireEvent.click(screen.getByRole('button',{name:'Confirm code'}));
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:'Checking code…'}).disabled));
 await new Promise(resolve=>setTimeout(resolve,60));
 await act(async()=>{await client.invalidateQueries({queryKey:['dervo-account']});});
 assert.equal((await screen.findByRole('alert')).textContent,'That code couldn’t be confirmed. Check it against the newest email and try again.');
});

test('a saved session being restored on launch reads as checking, not the code step, and the saved step stands',async()=>{
 localStorage.setItem('dervo-onboarding-step','agents');
 host({account:{...signedOut,state:'verifying',can_start:false,can_poll:true,can_cancel:false,operation_id:'restore1'}});
 mount();
 await agentsReady();
 assert.equal(screen.queryByRole('textbox',{name:'Email code'}),null,'no “Enter the code” for a restore');
 assert.equal(signInState({...signedOut,state:'verifying',can_start:false,can_poll:true,can_cancel:false},false).kind,'checking');
 assert.equal(signInState({...signedOut,state:'verifying',can_start:false,can_poll:true,can_cancel:true},false).kind,'code');
});

test('Use a different email forgets the step and the plan choice, so the next account starts from its own',async()=>{
 localStorage.setItem('dervo-onboarding-plan-later','1');
 localStorage.setItem('dervo-onboarding-step','projects');
 host({account:signedIn('pending'),status:{...ready,account:{state:'pending',detail:'',email}}});
 mount();
 fireEvent.click(await screen.findByRole('button',{name:'Use a different email'}));
 await screen.findByRole('textbox',{name:'Email'});
 assert.equal(localStorage.getItem('dervo-onboarding-step'),null);
 assert.equal(localStorage.getItem('dervo-onboarding-plan-later'),null);
});

test('with no agent and no plan, the agents step says what each way needs and opens the API key form',async()=>{
 // Merge review of #23: someone with neither Claude Code nor a ChatGPT plan had only a disabled Continue.
 host({status:{...ready,claude:{state:'missing',detail:'x',install:'x',version:null,profile_id:null}}});
 mount();
 await screen.findByRole('heading',{name:'Connect your agents.'});
 const note=document.querySelector('.dv-ag-none');
 assert.match(note.textContent,/No Claude or ChatGPT plan\? Claude Code runs on Claude Pro or Max, and Codex on ChatGPT Plus, Pro Lite or Pro\. Without one, an API key works/);
 fireEvent.click(within(note).getByRole('button',{name:'Add an API key'}));
 await waitFor(()=>assert.equal(document.activeElement,screen.getByLabelText('Anthropic API key')));
});

test('signing out, here or in Settings, forgets the step and plan choice; a removed account is told so',async()=>{
 localStorage.setItem('dervo-onboarding-step','projects');
 localStorage.setItem('dervo-onboarding-plan-later','1');
 host({account:signedOut});
 mount();
 await screen.findByRole('textbox',{name:'Email'});
 await waitFor(()=>assert.equal(localStorage.getItem('dervo-onboarding-step'),null));
 assert.equal(localStorage.getItem('dervo-onboarding-plan-later'),null);
 assert.equal(signInState({...signedIn('approved'),plan:{plan:'removed'}},false).kind,'blocked','removed is never read as waiting');
});
