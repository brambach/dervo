import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
globalThis.window=dom.window;globalThis.document=dom.window.document;for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);dom.window.HTMLElement.prototype.scrollIntoView=()=>{};dom.window.HTMLElement.prototype.hasPointerCapture=()=>false;dom.window.HTMLElement.prototype.releasePointerCapture=()=>{};
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {AccountAndModelPage}=await import('./SettingsModel.tsx');
const {HelpersPage}=await import('./SettingsHelpers.tsx');
const {AccessPage}=await import('./SettingsAccess.tsx');
const {ChecksPage}=await import('./SettingsChecks.tsx');
const {GeneralPage,deleteProject}=await import('./SettingsGeneral.tsx');
const {LifecycleBlockers,blockerRequest}=await import('./LifecycleBlockers.tsx');
const {grantArgs}=await import('./projectSettingsData.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();});

const PROFILES=[
 {id:'cs',provider:'claude',identity:'bryce@trydervo.com',category:'personal',method:'subscription',plan:'pro',fable_confirmed:false,name:'Personal'},
 {id:'cd',provider:'claude',identity:'bryce@acme.com',category:'personal',method:'subscription',plan:'team',fable_confirmed:false,name:'Acme'},
 {id:'cx',provider:'codex',identity:'bryce@trydervo.com',category:'personal',method:'subscription',plan:null,fable_confirmed:false,name:'ChatGPT Plus'},
];
const PROJECT={id:'p1',name:'dervo app',cwd:'/Users/bryce/dervo/app',profile_id:'cs',model:'claude-opus-5'};
const CHECKS=[{profile:'python-unittest',test_file:'test_upload.py',label:'Python unittest'}];
const CATALOGUE=[{id:'manual-review',label:'Review changes myself',test_pattern:'',available:true,detail:''},{id:'python-unittest',label:'Python unittest',test_pattern:'test_*.py',available:true,detail:'Runs unittest.'},
 {id:'typescript-check',label:'TypeScript check',test_pattern:'tsconfig.json',available:true,detail:'Type-checks.'},{id:'node-test',label:'Node test',test_pattern:'',available:true,detail:''}];
const granted=(extra={})=>({state:'granted',detail:'',provider:'claude',project_access:true,files:['test_upload.py'],folders:[],test_file:'test_upload.py',check_profile:'python-unittest',checks:CHECKS,
 check_timeout_seconds:30,check_profiles:CATALOGUE,candidates:['test_upload.py','tsconfig.json','app.py'],test_candidates:['test_upload.py'],unusable_files:[],unusable_folders:[],created_at:1789000000,
 acceptance:{required:true,source:'explicit',detail:''},route:{auto_switch:false,source:'default',detail:''},owner_effort:'high',
 full_access:{enabled:false,active:false,available:true,unavailable:null,detail:'',warning:'beta.15',connectors:{names:['claude.ai Notion'],allowed:null}},...extra});

function host(permission,extra={}){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation in extra)return typeof extra[operation]==='function'?extra[operation](args):extra[operation];
  if(operation==='coding_permission')return permission;
  if(operation==='collab_agents')return [];
  if(operation==='collab_policy')return {state:'off',detail:'',mode:'off',agents:[],channels:[],limits:{max_parallel:3,max_depth:1,max_calls:6},available:[],unavailable:[],review:{enabled:false,reviewer:null,effort:null}};
  if(operation==='preview_status')return {supported:false};
  if(operation.startsWith('coding_'))return permission;
  return {};}};
 return calls;
}
const writes=calls=>calls.filter(([op])=>!['coding_permission','collab_agents','collab_policy','preview_status','model_status','model_check','claude_version','outside_sessions_consent','thread_folder_settings'].includes(op));
function mount(Page,props){
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 return render(React.createElement(QueryClientProvider,{client},React.createElement(Page,props)));
}
const choice=name=>[...document.querySelectorAll('.dv-choice')].find(c=>c.querySelector('.dv-c-name')?.textContent.startsWith(name));
const click=async el=>{await act(async()=>{fireEvent.click(el);});};
const button=name=>screen.getAllByRole('button').find(b=>b.textContent===name);

test('settings read the project itself, with no thread: every coding read and write names channelId null',async()=>{
 const calls=host(granted());
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.ok(choice('Edit files')));
 assert.deepEqual(calls.find(([op])=>op==='coding_permission')[1],{projectId:'p1',channelId:null});
 await click(choice('Read and explain'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['coding_permission_revoke',{projectId:'p1',channelId:null}]);
});

test('Account and model: another account becomes the project default in one write; no thread moves',async()=>{
 const calls=host(granted());
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 await waitFor(()=>assert.ok(choice('Opus 5')));
 const trigger=screen.getByRole('button',{name:'Account for new threads'});
 assert.match(trigger.textContent,/Personal\s*Claude Pro/);
 await act(async()=>{fireEvent.keyDown(trigger,{key:'Enter'});});
 await waitFor(()=>assert.ok(document.querySelector('.dv-acct-item')));
 assert.deepEqual([...document.querySelectorAll('.dv-acct-item b')].map(b=>b.textContent),['Personal','Acme','ChatGPT Plus']);
 await click([...document.querySelectorAll('.dv-acct-item')].find(i=>i.textContent.startsWith('Acme')));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['project_profile_set',{projectId:'p1',expectedProfileId:'cs',profileId:'cd',expectedModel:'claude-opus-5',model:'claude-opus-5'}],'the same model on another Claude account');
 assert.ok(!writes(calls).some(([op])=>op.startsWith('conversation_')),'no thread moves from project settings');
});

// 16.4 item 3: the project's Thinking is optional and says it uses your default; your default is its own row, per account.
test('Account and model: the project uses your default until it sets its own, and your default saves per account',async()=>{
 const efforts={thread:null,project:null,personal:'xhigh',effective:'xhigh',source:'personal'};
 const calls=host(granted({efforts}));
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 const row=name=>[...document.querySelectorAll('.dv-set')].find(r=>r.querySelector('.dv-set-name')?.textContent===name);
 await waitFor(()=>assert.equal(row('Thinking')?.querySelector('.dv-set-line').textContent,'Using your default (Extra high).'));
 assert.equal(row('Thinking').querySelector('[aria-checked="true"]').textContent,'Default');
 assert.equal([...row('Thinking').querySelectorAll('button')].some(b=>b.textContent==='Use my default'),false,'nothing to go back to');
 assert.match(row('Your default').querySelector('.dv-set-line').textContent,/^Extra high: .* for Opus 5 on Personal\. Every project that doesn’t set its own uses it\. Work and personal accounts keep separate defaults\.$/);
 await click([...row('Your default').querySelectorAll('[role="radio"]')].find(b=>b.textContent==='Low'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['coding_effort_set',{projectId:'p1',channelId:null,effort:'low',level:'personal'}]);
 cleanup();
 const own=host(granted({efforts:{...efforts,project:'high',effective:'high',source:'project'}}));
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 await waitFor(()=>assert.ok(row('Thinking')?.querySelector('[aria-checked="true"]')?.textContent==='High'));
 await click([...row('Thinking').querySelectorAll('button')].find(b=>b.textContent==='Use my default'));
 await waitFor(()=>assert.ok(writes(own).length));
 assert.deepEqual(writes(own)[0],['coding_effort_set',{projectId:'p1',channelId:null,effort:null,level:'project'}]);
});

test('Account and model: with no level anywhere, Claude says it’s Claude Code’s own default and names no level',async()=>{
 host(granted({efforts:{thread:null,project:null,personal:null,effective:null,source:null}}));
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 const row=name=>[...document.querySelectorAll('.dv-set')].find(r=>r.querySelector('.dv-set-name')?.textContent===name);
 await waitFor(()=>assert.ok(row('Your default')?.querySelector('[role="radio"]:not([disabled])')));
 assert.equal(row('Thinking').querySelector('.dv-set-line').textContent,'No level here or as your default. Claude Code’s own default for Opus 5. Dervo can’t see which level that is, and a level set in Claude Code itself doesn’t apply here.');
 assert.doesNotMatch(row('Your default').querySelector('.dv-set-line').textContent,/Medium|High/);
});

test('Account and model: a model saves for new threads; Fable on Pro asks once, and Cancel saves nothing',async()=>{
 const calls=host(granted());
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 await waitFor(()=>assert.ok(choice('Sonnet 5.5')));
 assert.equal(choice('Fable 5.1').querySelector('.dv-tag').textContent,'Paid credits');
 await click(choice('Fable 5.1'));
 assert.match(choice('Fable 5.1').textContent,/Use Fable 5\.1 for new threads\?Each message costs money on top of your Pro plan\. Dervo asks once for Personal\./);
 assert.deepEqual(writes(calls),[],'asking saves nothing');
 await click(button('Cancel'));
 assert.equal(document.querySelector('.dv-confirm'),null);
 assert.deepEqual(writes(calls),[]);
 await click(choice('Fable 5.1'));
 await click(button('Use Fable 5.1'));
 await waitFor(()=>assert.equal(writes(calls).length,2));
 assert.deepEqual(writes(calls),[['profile_fable_ack',{profileId:'cs'}],['project_model_set',{projectId:'p1',expectedModel:'claude-opus-5',model:'claude-fable-5-1'}]]);
 await click(choice('Sonnet 5.5'));
 await waitFor(()=>assert.equal(writes(calls).length,3));
 assert.deepEqual(writes(calls)[2],['project_model_set',{projectId:'p1',expectedModel:'claude-opus-5',model:'claude-sonnet-5-5'}]);
});

test('Account and model: Team explains premium inclusion without claiming every seat pays',async()=>{
 const calls=host(granted());
 mount(AccountAndModelPage,{project:{...PROJECT,profile_id:'cd'},profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 await waitFor(()=>assert.ok(choice('Fable 5.1')));
 assert.equal(choice('Fable 5.1').querySelector('.dv-tag').textContent,'Credits may apply');
 await click(choice('Fable 5.1'));
 assert.match(choice('Fable 5.1').textContent,/Premium seats include Fable for up to 50% of your weekly usage limits/);
 assert.match(choice('Fable 5.1').textContent,/Standard seats use paid credits from the first message/);
 assert.match(choice('Fable 5.1').textContent,/Dervo can’t detect your seat type/);
 assert.deepEqual(writes(calls),[]);
 await click(button('Cancel'));
 assert.deepEqual(writes(calls),[]);
});

test('Account and model: a thread on another account can come back to the project default',async()=>{
 const calls=host(granted());
 const conversations=[{id:'t1',name:'Live board',visibility:'shared',project_id:'p1',profile_id:'cx',model:'gpt-5.5'},{id:'t2',name:'Ideas storage',visibility:'shared',project_id:'p1',profile_id:'cs',model:'claude-opus-5'}];
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations,onOpenThread:()=>{},onAddAccount:()=>{}});
 await waitFor(()=>assert.ok(document.querySelector('.dv-other-threads')));
 assert.deepEqual([...document.querySelectorAll('.dv-other-threads .dv-row-name')].map(n=>n.textContent),['Live board'],'only threads on another account');
 await click(button('Use project default'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['conversation_profile_set',{projectId:'p1',channelId:'t1',expectedProfileId:'cx',profileId:'cs',expectedModel:'gpt-5.5',model:'claude-opus-5'}]);
});

// GPT-5.5 leaves ChatGPT and Codex on 14 Oct 2026, with GPT-6 Sol named in its place (learn.chatgpt.com/docs/models).
test('Account and model: a Codex project and its threads on GPT-5.5 say it’s leaving, and move to GPT-6 Sol only on a click',async()=>{
 const catalog={state:'catalog',detail:'',models:[{model:'gpt-6-sol',displayName:'GPT-6-Sol',supportedReasoningEfforts:[{reasoningEffort:'low'}]},
  {model:'gpt-5.5',displayName:'GPT-5.5',upgradeInfo:{model:'gpt-6-sol'},supportedReasoningEfforts:[{reasoningEffort:'low'}]}]};
 const calls=host(granted({provider:'codex'}),{model_status:catalog,collab_agents:[{name:'helper',channel_id:'t4'}]});
 const conversations=[{id:'t1',name:'Live board',visibility:'shared',project_id:'p1',profile_id:'cx',model:'gpt-5.5'},
  {id:'t2',name:'Release notes',visibility:'shared',project_id:'p1',profile_id:'cx',model:'gpt-5.5'},
  {id:'t3',name:'Ideas storage',visibility:'shared',project_id:'p1',profile_id:'cx',model:'gpt-6-sol'},
  {id:'t4',name:'Helper',visibility:'shared',project_id:'p1',profile_id:'cx',model:'gpt-5.5'}];
 mount(AccountAndModelPage,{project:{...PROJECT,profile_id:'cx',model:'gpt-5.5'},profiles:PROFILES,usage:[],conversations,onOpenThread:()=>{},onAddAccount:()=>{}});
 const note=await waitFor(()=>{const found=document.querySelector('.dv-set-below > .dv-pk-retire');assert.ok(found);return found;});
 assert.match(note.textContent,/GPT-5\.5 (leaves|left) Codex on (14 October|October 14)\. New threads here/);
 assert.match(choice('GPT-5.5').textContent,/Previous generation\. (Leaves|Left) Codex/);
 const section=await waitFor(()=>{const found=document.querySelector('section[aria-labelledby="leaving"]');assert.ok(found);return found;});
 assert.equal(section.querySelector('h2').textContent,'Threads on a model leaving Codex');
 assert.equal(section.querySelector('.dv-caption').textContent,'2 threads');
 assert.deepEqual([...section.querySelectorAll('.dv-row-name')].map(n=>n.textContent),['Live board','Release notes'],'a helper agent keeps its model, and GPT-6 Sol isn’t leaving');
 assert.deepEqual(writes(calls),[],'nothing moves by itself');
 await click(button('Use GPT-6-Sol for new threads'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['project_model_set',{projectId:'p1',expectedModel:'gpt-5.5',model:'gpt-6-sol'}]);
 await click(button('Move all 2'));
 await waitFor(()=>assert.equal(writes(calls).length,3));
 assert.deepEqual(writes(calls).slice(1),[['conversation_model_set',{projectId:'p1',channelId:'t1',expectedModel:'gpt-5.5',model:'gpt-6-sol'}],
  ['conversation_model_set',{projectId:'p1',channelId:'t2',expectedModel:'gpt-5.5',model:'gpt-6-sol'}]]);
});

test('Access: full access states its risks before anything turns on; Edit files from reading grants the whole folder',async()=>{
 const calls=host(granted());
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.ok(choice('Full access')));
 await click(choice('Full access'));
 const card=choice('Full access');
 assert.ok(card.classList.contains('dv-open'),'the confirm opens in place');
 assert.equal(card.getAttribute('aria-checked'),'false','nothing is chosen yet');
 assert.equal(card.querySelectorAll('.dv-points li').length,5);
 assert.match(card.textContent,/A web page the agent reads can try to steer it\./,'design review S9: "it" has something to point at');
 assert.match(card.textContent,/Home’s own jobs here, the ones you didn’t click, can use this project’s allowed connectors too/,'security review N2');
 assert.match(card.textContent,/Commands run as you, without a sandbox/);
 assert.match(card.textContent,/read the API keys and sign-ins saved on this Mac, Dervo’s included/,'says what full access can read on this Mac (security review, 19 Sep)');
 assert.match(card.textContent,/without a sandbox, on your own Mac/,'beta.15: testers turn it on too, so it names the risk to their own Mac');
 assert.deepEqual(writes(calls),[]);
 await click(button('Turn on full access'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['coding_full_access_set',{projectId:'p1',channelId:null,enabled:true,warning:'beta.15'}],'the confirm sends the warning’s token (security N4)');
 cleanup();
 const reading=host({...granted(),state:'missing',checks:[],project_access:false,files:[],created_at:null,full_access:{...granted().full_access,available:false,unavailable:'Allow coding for the whole project folder first. Full access builds on that permission.'}});
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.ok(choice('Edit files')));
 assert.equal(choice('Full access').getAttribute('aria-disabled'),'true');
 assert.match(choice('Full access').textContent,/Allow coding for the whole project folder first/,'a disabled level says why');
 await click(choice('Edit files'));
 await waitFor(()=>assert.ok(writes(reading).length));
 assert.deepEqual(writes(reading)[0],['coding_permission_grant',{projectId:'p1',channelId:null,files:[],testFile:null,projectAccess:true,checkTimeoutSeconds:5,checkProfile:'manual-review'}],'your review until a check is chosen');
});

test('Access: a stale grant names what is gone, with both fixes beside it',async()=>{
 const stale=granted({state:'stale',project_access:false,files:['test_upload.py','upload_client.py'],folders:['backend/fixtures'],unusable_folders:['backend/fixtures'],detail:'Granted folders are no longer usable: backend/fixtures. Allow coding again.'});
 const calls=host(stale);
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.ok(document.querySelector('.dv-problem')));
 assert.match(document.querySelector('.dv-problem').textContent,/Agents can’t start here right nowbackend\/fixtures isn’t in the project any more, and it’s on the list below\./);
 assert.ok(document.querySelector('.dv-problem [data-accent]'),'the page’s one rust glyph');
 await click(button('Take it off the list'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['coding_permission_grant',{projectId:'p1',channelId:null,files:['test_upload.py','upload_client.py'],testFile:'test_upload.py',checkTimeoutSeconds:30}],'the folder leaves; the files and the check stay');
 await click(button('Use the whole folder'));
 await waitFor(()=>assert.equal(writes(calls).length,2));
 assert.equal(writes(calls)[1][1].projectAccess,true);
 // Picking Read and explain with a list asks first; the list would be forgotten.
 await click(choice('Read and explain'));
 assert.match(choice('Read and explain').textContent,/Stop editing here\?Your list of 2 files and 1 folder is forgotten\./);
 assert.equal(writes(calls).length,2);
});

test('Checks and review: a check with one file saves at once and keeps where agents work; approval saves on its own',async()=>{
 const calls=host(granted({project_access:false,files:['app.py','test_upload.py'],folders:['src']}));
 mount(ChecksPage,{project:PROJECT,profiles:PROFILES,usage:[],onAccess:()=>{}});
 await waitFor(()=>assert.ok(choice('TypeScript check')));
 assert.equal(choice('Python unittest').getAttribute('aria-checked'),'true');
 await click(choice('TypeScript check'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['coding_permission_grant',{projectId:'p1',channelId:null,files:['app.py','test_upload.py','tsconfig.json'],testFile:'tsconfig.json',folders:['src'],checkTimeoutSeconds:30,checkProfile:'typescript-check'}]);
 const approval=screen.getByRole('switch',{name:'Wait for my approval'});
 await click(approval);
 await waitFor(()=>assert.ok(writes(calls).some(([op])=>op==='coding_acceptance_set')));
 assert.deepEqual(writes(calls).find(([op])=>op==='coding_acceptance_set')[1],{projectId:'p1',channelId:null,required:false});
});

test('#28 Checks and review: with approval off and full access on, the approval row says why full-access tasks still wait',async()=>{
 const WHY='Full-access tasks still wait for your approval, because no checks run in Dervo for them.';
 const FULL={enabled:true,active:true,available:true,unavailable:null,detail:'',connectors:{names:['claude.ai Notion'],allowed:null}};
 const row=()=>screen.getByRole('switch',{name:'Wait for my approval'}).closest('.dv-set').textContent;
 host(granted({acceptance:{required:false,source:'explicit',detail:''},full_access:FULL}));
 mount(ChecksPage,{project:PROJECT,profiles:PROFILES,usage:[],onAccess:()=>{}});
 await waitFor(()=>assert.ok(row().includes(WHY),row()));
 cleanup();
 // Negative controls: approval on, or full access off, has no exception to explain.
 for(const extra of [{acceptance:{required:true,source:'explicit',detail:''},full_access:FULL},{acceptance:{required:false,source:'explicit',detail:''}}]){
  host(granted(extra));
  mount(ChecksPage,{project:PROJECT,profiles:PROFILES,usage:[],onAccess:()=>{}});
  await waitFor(()=>assert.ok(screen.getByRole('switch',{name:'Wait for my approval'})));
  assert.ok(!row().includes('Full-access tasks'),JSON.stringify(extra));
  cleanup();
 }
});

test('Checks and review: turning the second review on with no reviewer sets one up in place on the project’s own account',async()=>{
 const calls=host(granted());
 mount(ChecksPage,{project:PROJECT,profiles:PROFILES,usage:[],onAccess:()=>{}});
 const toggle=await waitFor(()=>{const s=screen.getByRole('switch',{name:'Second review'});assert.equal(s.disabled,false);return s;});
 await click(toggle);
 assert.match(document.querySelector('.dv-rev').textContent,/Reviewer uses Personal · Opus 5 in its own read-only thread/);
 assert.deepEqual(writes(calls),[]);
 await click(button('Set up reviewer'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['collab_reviewer_setup',{projectId:'p1',channelId:null,profileId:'cs',model:'claude-opus-5',effort:'low'}]);
});

test('General: delete waits for the typed name and names this exact project; Show in Finder sends no path',async()=>{
 const calls=host(granted());
 const changes=[];
 mount(GeneralPage,{project:PROJECT,onChanged:async(...args)=>{changes.push(args);},onPreview:()=>{}});
 await click(button('Show in Finder'));
 await waitFor(()=>assert.ok(writes(calls).length));
 assert.deepEqual(writes(calls)[0],['project_reveal',{projectId:'p1'}]);
 await click(button('Delete project…'));
 const go=button('Delete dervo app');
 assert.equal(go.disabled,true);
 const field=screen.getByLabelText('Type the project name to confirm');
 await act(async()=>{fireEvent.change(field,{target:{value:'dervo'}});});
 assert.equal(button('Delete dervo app').disabled,true,'a partial name isn’t enough');
 await act(async()=>{fireEvent.change(field,{target:{value:'dervo app'}});});
 assert.equal(button('Delete dervo app').disabled,false);
 await click(button('Delete dervo app'));
 await waitFor(()=>assert.equal(changes.length,1));
 assert.deepEqual(writes(calls)[1],['project_delete',{projectId:'p1',confirmation:'DELETE p1'}]);
});

test('General: delete stops what’s running first, asks again while runs close, then removes the project (app #12)',async()=>{
 let answers=[{id:'p1',stopping:2},{id:'p1',stopping:1},{id:'p1',channelIds:['c1','c2'],sourceFolderPreserved:true}];
 const calls=host(granted(),{project_delete:()=>answers.shift()});
 const seen=[],waits=[];
 const result=await deleteProject('p1',count=>seen.push(count),{wait:async ms=>{waits.push(ms);},now:()=>0});
 assert.deepEqual(result,{channelIds:['c1','c2']});
 assert.deepEqual(seen,[2,1],'the button says how many runs are still stopping');
 assert.equal(waits.length,2);
 assert.deepEqual(writes(calls).map(([op,args])=>[op,args.confirmation]),[['project_delete','DELETE p1'],['project_delete','DELETE p1'],['project_delete','DELETE p1']]);
 // A run that never closes is named, and nothing is deleted.
 answers=Array.from({length:5},()=>({id:'p1',stopping:1}));
 let clock=0;
 await assert.rejects(deleteProject('p1',()=>{},{wait:async()=>{clock+=20_000;},now:()=>clock}),/A run is still stopping, so the project wasn’t deleted yet\. What’s stopped stays stopped\./);
});

test('General: Keep it while runs are still stopping keeps the project; nothing is deleted after the confirm closes (design review, 23 Sep)',async()=>{
 const answers=[{id:'p1',stopping:1},{id:'p1',channelIds:['c1'],sourceFolderPreserved:true}];
 const calls=host(granted(),{project_delete:()=>answers.shift()});
 let kept=false;
 const result=await deleteProject('p1',()=>{kept=true;},{wait:async()=>{},now:()=>0,cancelled:()=>kept});
 assert.equal(result,null,'the wait ends without the delete');
 assert.equal(writes(calls).filter(([op])=>op==='project_delete').length,1,'no second ask, which would have deleted it');
 // In the page: the confirm's Keep it, pressed while the button says it's stopping runs.
 const later=[{id:'p1',stopping:2},{id:'p1',channelIds:['c1'],sourceFolderPreserved:true}];
 const pageCalls=host(granted(),{project_delete:()=>later.shift()});
 const changed=[];
 mount(GeneralPage,{project:PROJECT,onChanged:async(removed)=>{changed.push(removed);},onPreview:()=>{},onOpenBlocker:()=>{}});
 await click(button('Delete project…'));
 await act(async()=>{fireEvent.change(screen.getByLabelText('Type the project name to confirm'),{target:{value:'dervo app'}});});
 await click(button('Delete dervo app'));
 await waitFor(()=>assert.ok(button('Stopping 2 runs…')));
 await click(button('Keep it'));
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,1200));});
 assert.equal(writes(pageCalls).filter(([op])=>op==='project_delete').length,1);
 assert.deepEqual(changed,[],'the project is still here');
});

test('General: the delete confirm says running work is stopped; the work list shows only when a delete is refused',async()=>{
 host(granted(),{project_delete:{id:'p1',channelIds:[],sourceFolderPreserved:true},lifecycle_blockers:[{projectId:'p1',projectName:'dervo app',channelId:'c1',threadName:'Jev research',taskId:'t1',taskName:'Research Jev',kind:'lost',instruction:'Mark the lost run in Jev research as stopped.',actions:['settle'],actionChannelId:'c1',actionTaskId:'t1'}]});
 mount(GeneralPage,{project:PROJECT,onChanged:async()=>{},onPreview:()=>{},onOpenBlocker:()=>{}});
 await click(button('Delete project…'));
 assert.match(document.querySelector('.dv-delete').textContent,/Anything still running here is stopped first\. Its edits so far stay in your folder\./);
 await act(async()=>{fireEvent.change(screen.getByLabelText('Type the project name to confirm'),{target:{value:'dervo app'}});});
 await click(button('Delete dervo app'));
 assert.equal(document.querySelector('[aria-label="Work that needs attention"]'),null,'a delete that went through lists nothing');
});

test('General: a refused delete names what held it, with the blocker list',async()=>{
 host(granted(),{project_delete:()=>{throw 'This project can’t be deleted yet. Wait for coordination in Releases to finish, or cancel it. Then try again.';},
  lifecycle_blockers:[{projectId:'p1',projectName:'dervo app',channelId:'c1',threadName:'Releases',taskId:null,taskName:'',kind:'coordination',instruction:'Wait for coordination in Releases to finish, or cancel it.',actions:[]}]});
 mount(GeneralPage,{project:PROJECT,onChanged:async()=>{},onPreview:()=>{},onOpenBlocker:()=>{}});
 await click(button('Delete project…'));
 await act(async()=>{fireEvent.change(screen.getByLabelText('Type the project name to confirm'),{target:{value:'dervo app'}});});
 await click(button('Delete dervo app'));
 await waitFor(()=>assert.ok(document.querySelector('[aria-label="Work that needs attention"]')));
 assert.match(document.querySelector('.dv-delete').textContent,/can’t be deleted yet\. Wait for coordination in Releases/);
});

test('blockers name the task and clear it in place with the task sheet’s own operations (app #9)',async()=>{
 const rows=[
  {projectId:'p1',projectName:'dd',channelId:'c1',threadName:'Billi',taskId:'t-run',taskName:'Build the payslip export',kind:'run',instruction:'Wait for the run in Billi to finish, or stop it.',taskState:'working',actions:['stop'],actionChannelId:'c1',actionTaskId:'t-run'},
  {projectId:'p1',projectName:'dd',channelId:null,threadName:'APNIC client',taskId:null,taskName:'Check the APNIC renewal',kind:'lost',instruction:'It’s in an archived conversation. Mark the lost run in APNIC client as stopped.',taskState:'disconnected',actions:['settle'],actionChannelId:'c2',actionTaskId:'t-lost'},
  {projectId:'p1',projectName:'dd',channelId:'c3',threadName:'Portal',taskId:'t-ask',taskName:'Portal makeover',kind:'permission',instruction:'Approve or deny the permission request in Portal.',taskState:'needs_permission',actions:['dismiss'],actionChannelId:'c3',actionTaskId:'t-ask'},
  {projectId:'p1',projectName:'dd',channelId:'c1',threadName:'Billi',taskId:null,taskName:'',kind:'queued',instruction:'Cancel this conversation’s queued request first.',actions:['cancel'],actionChannelId:'c1',actionTaskId:null,queueId:'q1'},
 ];
 const calls=host(granted(),{lifecycle_blockers:()=>rows});
 mount(LifecycleBlockers,{scope:{scope:'project',projectId:'p1',channelId:null},onOpen:()=>{}});
 await waitFor(()=>assert.equal(document.querySelectorAll('[data-testid="lifecycle-blocker"]').length,4));
 const items=[...document.querySelectorAll('[data-testid="lifecycle-blocker"]')];
 assert.match(items[1].textContent,/Check the APNIC renewal/);
 assert.match(items[1].textContent,/Dervo lost track of this run · Task t-lost/,'an archived thread’s task is still named');
 assert.deepEqual(items.map(item=>[...item.querySelectorAll('button')].map(b=>b.textContent)),[['Stop','Open task'],['Leave it stopped'],['Don’t run','Open task'],['Cancel request','Open thread']]);
 for(const label of ['Stop','Leave it stopped','Don’t run','Cancel request'])await click(button(label));
 await waitFor(()=>assert.equal(writes(calls).filter(([op])=>op!=='lifecycle_blockers').length,4));
 assert.deepEqual(writes(calls).filter(([op])=>op!=='lifecycle_blockers'),[
  ['task_stop',{projectId:'p1',channelId:'c1',taskId:'t-run'}],['task_settle',{projectId:'p1',channelId:'c2',taskId:'t-lost'}],
  ['task_deny',{projectId:'p1',channelId:'c3',taskId:'t-ask'}],['coding_queue_cancel',{projectId:'p1',channelId:'c1',queueId:'q1'}]]);
 assert.equal(blockerRequest({...rows[0],actionChannelId:null},'stop'),null,'no ids, no request');
});

test('a refused blocker action shows the host’s words where it was pressed',async()=>{
 host(granted(),{lifecycle_blockers:[{projectId:'p1',projectName:'dd',channelId:'c1',threadName:'Billi',taskId:'t1',taskName:'Build it',kind:'lost',instruction:'Mark the lost run in Billi as stopped.',actions:['settle'],actionChannelId:'c1',actionTaskId:'t1'}],
  task_settle:()=>{throw 'A task or check is still running. Wait for it to finish before marking this stopped.';}});
 mount(LifecycleBlockers,{scope:{scope:'project',projectId:'p1',channelId:null},onOpen:()=>{}});
 await waitFor(()=>assert.ok(button('Leave it stopped')));
 await click(button('Leave it stopped'));
 await waitFor(()=>assert.equal(screen.getByRole('alert').textContent,'A task or check is still running. Wait for it to finish before marking this stopped.'));
});

test('the grant write keeps the other page’s half: where for Checks, the checks for Access',()=>{
 const permission=granted({project_access:false,files:['app.py','test_upload.py'],folders:['src'],checks:[...CHECKS,{profile:'typescript-check',test_file:'tsconfig.json',label:'TypeScript check'}]});
 assert.deepEqual(grantArgs(permission,{projectAccess:true}),{checks:[{checkProfile:'typescript-check',testFile:'tsconfig.json'}],files:['test_upload.py','tsconfig.json'],testFile:'test_upload.py',projectAccess:true,checkTimeoutSeconds:30});
 assert.deepEqual(grantArgs(permission,{projectAccess:false,files:['app.py'],folders:[]}).files,['app.py','test_upload.py','tsconfig.json'],'a list always names the checks’ own files');
});

test('computer files need a separate confirmation and turning them off sends an explicit revoke',async()=>{
 const permission=granted({full_access:{enabled:true,active:true,available:true,unavailable:null,detail:'',computer_access:false,connectors:{names:[],allowed:[]}}});
 const calls=host(permission,{'coding_computer_access_set':args=>({...permission,full_access:{...permission.full_access,computer_access:args.enabled}})});
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.ok(screen.getByRole('switch',{name:'Files across this Mac'})));
 await click(screen.getByRole('switch',{name:'Files across this Mac'}));
 assert.equal(writes(calls).length,0);
 assert.ok(screen.getByText(/Dervo can’t undo changes outside this project/));
 await click(button('Cancel'));
 assert.equal(writes(calls).length,0);
 await click(screen.getByRole('switch',{name:'Files across this Mac'}));
 await click(button('Allow computer files'));
 await waitFor(()=>assert.deepEqual(writes(calls)[0],['coding_computer_access_set',{projectId:'p1',channelId:null,enabled:true}]));
 await waitFor(()=>assert.equal(screen.getByRole('switch',{name:'Files across this Mac'}).getAttribute('aria-checked'),'true'));
 await click(screen.getByRole('switch',{name:'Files across this Mac'}));
 await waitFor(()=>assert.deepEqual(writes(calls)[1],['coding_computer_access_set',{projectId:'p1',channelId:null,enabled:false}]));
});

test('agents run commands is on with full access, and turning it off saves at once and says what changes',async()=>{
 const permission=granted({full_access:{enabled:true,active:true,available:true,unavailable:null,detail:'',computer_access:false,commands:true,connectors:{names:[],allowed:[]}}});
 const calls=host(permission,{'coding_commands_set':args=>({...permission,full_access:{...permission.full_access,commands:args.enabled}})});
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.equal(screen.getByRole('switch',{name:'Agents run commands'}).getAttribute('aria-checked'),'true'));
 assert.ok(screen.getByText(/run without asking/));
 await click(screen.getByRole('switch',{name:'Agents run commands'}));
 await waitFor(()=>assert.deepEqual(writes(calls)[0],['coding_commands_set',{projectId:'p1',channelId:null,enabled:false}]));
 await waitFor(()=>assert.equal(screen.getByRole('switch',{name:'Agents run commands'}).getAttribute('aria-checked'),'false'));
 assert.ok(screen.getByText(/Claude Code edits files without running commands/));
 // Design review, 23 Sep: with commands off, the card no longer says commands run as you; the other three points stay.
 assert.equal(screen.queryByText(/Commands run as you, without a sandbox/),null);
 assert.ok(screen.getByText(/A web page the agent reads can try to steer it/));
});

test('16.2: commit results automatically is off with full access, and turning it on saves at once and says Undo reverts',async()=>{
 const permission=granted({full_access:{enabled:true,active:true,available:true,unavailable:null,detail:'',computer_access:false,commands:true,auto_commit:false,connectors:{names:[],allowed:[]}}});
 const calls=host(permission,{'coding_auto_commit_set':args=>({...permission,full_access:{...permission.full_access,auto_commit:args.enabled}})});
 mount(AccessPage,{project:PROJECT,onChecks:()=>{}});
 await waitFor(()=>assert.equal(screen.getByRole('switch',{name:'Commit results automatically'}).getAttribute('aria-checked'),'false'));
 assert.ok(screen.getByText(/each finished result waits for you to approve and commit it/));
 await click(screen.getByRole('switch',{name:'Commit results automatically'}));
 await waitFor(()=>assert.deepEqual(writes(calls)[0],['coding_auto_commit_set',{projectId:'p1',channelId:null,enabled:true}]));
 await waitFor(()=>assert.equal(screen.getByRole('switch',{name:'Commit results automatically'}).getAttribute('aria-checked'),'true'));
 assert.ok(screen.getByText(/approved and committed without waiting for you.*Undo reverts it/));
});

test('thread access uses its own account binding for every read and write',async()=>{
 const calls=host(granted({provider:'codex',full_access:{provider:'codex',enabled:false,active:false,available:true,unavailable:null,detail:'',warning:'beta.15'}}));
 mount(AccessPage,{project:{...PROJECT,profile_id:'cx',model:'gpt-5.4'},channelId:'thread-on-codex'});
 await waitFor(()=>assert.ok(choice('Edit files')));
 assert.deepEqual(calls.find(([op])=>op==='coding_permission')[1],{projectId:'p1',channelId:'thread-on-codex'});
 assert.match(choice('Full access').textContent,/Codex/);
 await click(choice('Full access'));
 assert.doesNotMatch(choice('Full access').textContent,/Home’s own jobs/,'Codex gets no connectors, so its confirm doesn’t say Home’s jobs use them');
 await click(button('Turn on full access'));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='coding_full_access_set')));
 assert.deepEqual(calls.find(([op])=>op==='coding_full_access_set')[1],{projectId:'p1',channelId:'thread-on-codex',enabled:true,warning:'beta.15'});
 assert.equal(calls.filter(([op])=>op.startsWith('coding_')).some(([,args])=>args.channelId!== 'thread-on-codex'),false);
});

test('thread access closes back to the account control that opened it',async()=>{
 const {ThreadAccessDialog}=await import('./SettingsAccess.tsx');
 const {ThemeProvider}=await import('../shared/theme/ThemeProvider.tsx');
 window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});host(granted());
 function Trial(){const [open,setOpen]=React.useState(false);return React.createElement(React.Fragment,null,
  React.createElement('button',{onClick:()=>setOpen(true)},'Edit files'),
  open&&React.createElement(ThreadAccessDialog,{project:PROJECT,channelId:'c1',accountName:'Personal',open:true,onOpenChange:setOpen}));}
 mount(()=>React.createElement(ThemeProvider,null,React.createElement(Trial)),{});const trigger=screen.getByRole('button',{name:'Edit files'});trigger.focus();await click(trigger);
 await screen.findByRole('dialog',{name:'Thread access'});await click(screen.getByRole('button',{name:'Close'}));
 await waitFor(()=>assert.equal(screen.queryByRole('dialog',{name:'Thread access'}),null));
 await waitFor(()=>assert.equal(document.activeElement,trigger));
});

// The label is the name's first text node; a tag, when there is one, follows it inside the same element.
const exactChoice=name=>[...document.querySelectorAll('.dv-choice')].find(c=>c.querySelector('.dv-c-name')?.firstChild?.textContent===name);
test('Other models: Opus 5 carries no credit tag, and an older Claude Code sees it in the main list instead of Opus 5.5',async()=>{
 host(granted());
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 await waitFor(()=>assert.ok(exactChoice('Opus 5.5')));
 assert.equal(!!exactChoice('Opus 5').querySelector('.dv-tag'),false,'Opus 5 isn’t billed like Fable');
 assert.match(exactChoice('Fable 5').textContent,/Paid credits/,'Fable 5 keeps its tag on Pro');
 cleanup();
 host(granted(),{claude_version:{state:'ok',version:'2.1.278'}});
 mount(AccountAndModelPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onOpenThread:()=>{},onAddAccount:()=>{}});
 // Opus 5 is already under Other models before the version arrives, so wait for Opus 5.5 to leave the main list.
 await waitFor(()=>assert.ok(!exactChoice('Opus 5.5')&&!!exactChoice('Opus 5')));
 assert.equal([...document.querySelectorAll('.dv-choice .dv-c-name')].filter(n=>n.firstChild?.textContent==='Opus 5').length,1,'listed once');
});

test('a new agent on a project still on Opus 5 shows Opus 5 as its model, not an empty choice',async()=>{
 host(granted());
 mount(HelpersPage,{project:PROJECT,profiles:PROFILES,usage:[],conversations:[],onAccounts:()=>{}});
 await click(await screen.findByText('Add an agent'));
 await waitFor(()=>assert.equal(screen.getByLabelText('Agent model').textContent,'Opus 5'));
});

// Windows tester's host-errors.log, 26 Sep: a fresh install has no project, and the app read its permission anyway, so
// the host refused "Unknown project" every time Dervo opened. The Mac did the same.
test('with no project chosen the permission read waits, and the app shell passes whether one is chosen',async()=>{
 const {useProjectPermission}=await import('./projectSettingsData.ts');
 const calls=host(granted());
 function Reader({projectId,enabled}){const permission=useProjectPermission(projectId,null,enabled);return React.createElement('p',null,permission.data?.state??'none');}
 const view=mount(Reader,{projectId:'',enabled:false});
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.filter(([op])=>op==='coding_permission').length,0,'nothing is asked before a project exists');
 view.unmount();
 mount(Reader,{projectId:'p1',enabled:true});
 await screen.findByText('granted');
 assert.deepEqual(calls.find(([op])=>op==='coding_permission')[1],{projectId:'p1',channelId:null});
 const {readFileSync}=await import('node:fs');
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/useProjectPermission\(projectId,null,hasSelectedProject\)/);
});
