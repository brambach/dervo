import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLSelectElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {AccountTool,ComposerNotes,ComposerTools}=await import('./ComposerTools.tsx');
const {ContinuityTool,shortReason}=await import('./SessionContinuity.tsx');
const {ProviderSuggestion,RouteWhy}=await import('./ProviderSuggestion.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
function host(handler=()=>({})){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const frame=element=>React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},element);
const claude={profile_id:'q',model:'claude-fable-5-1',provider:'claude',identity:'me',method:'subscription',label:'Claude Code'};

test('the account and model are one button that opens the picker, and Auto sits beside them',async()=>{
 const calls=host(()=>({state:'granted'}));let changed=0;const opened=[];
 const props={projectId:'p',channelId:'c',route:{mode:'fixed',alternative:claude},provider:'codex',model:'gpt-5.5-codex',onChangeAccount:t=>opened.push(t),onChanged:async()=>{changed++;}};
 const view=render(React.createElement(AccountTool,props));
 const account=screen.getByRole('button',{name:'Account and model: Codex · gpt-5.5-codex'});
 fireEvent.click(account);assert.equal(opened[0],account,'the button opens the account and model picker');
 const select=screen.getByLabelText('When this account is low or fails');
 assert.deepEqual([...select.options].map(o=>[o.value,o.textContent]),[['auto','Auto'],['fixed','Stay on it']]);
 assert.equal(select.value,'fixed');
 assert.match(screen.getByRole('note').textContent,/Every request here runs on Codex · gpt-5.5-codex/);
 fireEvent.change(select,{target:{value:'fixed'}});
 assert.equal(calls.length,0,'choosing the current value sends nothing');
 fireEvent.change(select,{target:{value:'auto'}});
 await waitFor(()=>assert.equal(changed,1));
 assert.deepEqual(calls,[{operation:'conversation_route_set',args:{projectId:'p',channelId:'c',mode:'auto'}}]);
 view.rerender(React.createElement(AccountTool,{...props,route:{mode:'auto',alternative:claude},method:'api_key'}));
 assert.match(screen.getByRole('note').textContent,/Auto uses Codex API key\. When it’s low or fails, Dervo suggests Claude Code, or moves there if this project switches by itself\./);
 view.rerender(React.createElement(AccountTool,{...props,route:{mode:'auto',alternative:null}}));
 assert.match(screen.getByRole('note').textContent,/Add an account on the other provider so Auto has somewhere to move/);
 view.rerender(React.createElement(AccountTool,{...props,route:undefined}));
 assert.equal(screen.getByLabelText('When this account is low or fails').disabled,true);
});

test('a refused route change shows the host’s reason and refreshes nothing',async()=>{
 host(()=>{throw new Error("An agent's conversation keeps the account the agent was created with.");});let changed=0;
 render(React.createElement(AccountTool,{projectId:'p',channelId:'c',route:{mode:'fixed',alternative:null},provider:'claude',model:'m',onChangeAccount(){},onChanged:async()=>{changed++;}}));
 fireEvent.change(screen.getByLabelText('When this account is low or fails'),{target:{value:'auto'}});
 assert.match((await screen.findByRole('alert')).textContent,/agent's conversation/);
 assert.equal(changed,0);
});

test('the tools row shows the queue count while this conversation runs, and nothing when the queue is empty',()=>{
 const view=render(React.createElement(ComposerNotes,{busy:false,queued:0},React.createElement('span',null,'tools')));
 assert.equal(screen.queryByRole('status'),null);
 // The placeholder already says a message adds to the running task, so an empty queue adds no second line.
 view.rerender(React.createElement(ComposerNotes,{busy:true,queued:0},React.createElement('span',null,'tools')));
 assert.equal(screen.queryByRole('status'),null);
 assert.equal(screen.getByTestId('composer-notes').textContent,'tools');
 view.rerender(React.createElement(ComposerNotes,{busy:true,queued:2},React.createElement('span',null,'tools')));
 assert.equal(screen.getByRole('status').textContent,'2 queued');
 // A message sent mid-run: the composer says which way it went, in place of the general note.
 view.rerender(React.createElement(ComposerNotes,{busy:true,queued:0,interruption:{kind:'steered',text:'Added to the run · Codex reads it at its next step'}},React.createElement('span',null,'tools')));
 assert.equal(screen.getByRole('status').textContent,'Added to the run · Codex reads it at its next step');
 assert.equal(screen.getByRole('status').dataset.interruption,'steered');
 view.rerender(React.createElement(ComposerNotes,{busy:false,queued:0,interruption:{kind:'steered',text:'Added to the run'}},React.createElement('span',null,'tools')));
 assert.equal(screen.queryByRole('status'),null,'nothing to say once the run has ended');
});

test('the continuity note keeps the reason short and says when full access is on, with the whole reason in the tooltip',()=>{
 // Every reason session_continuity.decide gives for a fresh session, and what the composer foot shows for it.
 assert.deepEqual(Object.fromEntries([
  'Full access was turned on.','Full access was turned off.',
  'Codex gets a summary of this chat, not the full session.','Moved from Codex. Claude gets a summary of this chat.',
  'Switched to another account. Its sessions are kept separately.','The project folder changed.',
  'Switched between asking about the project and coding in it.','The last message is still running.','The last message never started.',
  'The last run ended unexpectedly, so this starts a new session with a summary of the chat.',
  'The last run failed partway, so this starts a new session with a summary of the chat.',
 ].map(reason=>[reason,shortReason(reason)])),{
  'Full access was turned on.':'Full access on','Full access was turned off.':'Full access off',
  'Codex gets a summary of this chat, not the full session.':'Codex gets a summary of this chat, not the full session',
  'Moved from Codex. Claude gets a summary of this chat.':'Moved from Codex',
  'Switched to another account. Its sessions are kept separately.':'Switched to another account','The project folder changed.':'The project folder changed',
  'Switched between asking about the project and coding in it.':'Switched between asking about the project and coding in it',
  'The last message is still running.':'The last message is still running','The last message never started.':'The last message never started',
  'The last run ended unexpectedly, so this starts a new session with a summary of the chat.':'The last run ended unexpectedly',
  'The last run failed partway, so this starts a new session with a summary of the chat.':'The last run failed partway',
 });
 const view=render(React.createElement(ContinuityTool,{projectId:'p',channelId:'c',view:{state:'fresh',reason:'Full access was turned on.'},onChanged:async()=>{}}));
 assert.equal(screen.getByTestId('continuity').textContent,'New session · Full access on');
 assert.equal(screen.getByTestId('continuity').title,'Full access was turned on.');
 view.rerender(React.createElement(ContinuityTool,{projectId:'p',channelId:'c',view:{state:'fresh',reason:'You chose to start fresh.',fresh_chosen:true},onChanged:async()=>{}}));
 assert.equal(screen.getByTestId('continuity').textContent,'New session');
 view.rerender(React.createElement(ContinuityTool,{projectId:'p',channelId:'c',view:{state:'continues',reason:null},onChanged:async()=>{}}));
 assert.equal(screen.queryByTestId('continuity'),null);
});

const route=(extra={})=>({mode:'auto',provider:'codex',profile_id:'p',identity:'me',method:'subscription',model:'gpt',label:'Codex',why:'Auto: Codex',suggest:null,switched_from:null,retry_of:null,automatic:false,...extra});
test('the route reason is not a line under every task: the byline names the agent and the handover is one line under the stopped task',()=>{
 for(const task of [{route:route()},{route:route({mode:'fixed',why:'Retried on Claude Code after Codex was overloaded',retry_of:'t0'})},{}]){
  const view=render(React.createElement(RouteWhy,{task}));
  assert.equal(view.container.textContent,'');cleanup();
 }
});

const failed=(suggest,extra={})=>({id:'t1',state:'blocked',origin:'conversation',route:route(),suggest:{reason:'Codex was overloaded.',kind:'overloaded',other:claude,automatic:false,taken:null,error:null,...suggest},...extra});
const policy={auto_switch:false,source:'default',detail:'x'};
// What coding_handoff_preview answers for a clean stop: nothing left in the folder, so no snapshot.
const brief={id:'h1',taskId:'t1',here:'Codex',to:'Claude',asked:'Fix the upload test',files:[],changes:{},cleared:[],also:[],added:0,removed:0,
 check:'None ran.',stopped:'Codex was overloaded.',kind:'overloaded',step:null,next:null,snapshot:null,full:false};
function suggestion(task,extra={},detail={retry:{available:true},rebound:false}){
 const calls=host(op=>{if(op==='task_detail')return detail;if(op==='coding_route_set')return {...policy,auto_switch:true};if(op==='coding_handoff_preview')return brief;return {id:'r2',taskId:'t2',state:'working',detail:''};});
 let refreshed=0;
 const view=render(frame(React.createElement(ProviderSuggestion,{task,projectId:'p',channelId:'c',policy,latest:true,conversationProfileId:'p',refresh:async()=>{refreshed++;},...extra})));
 return {calls,view,refreshed:()=>refreshed};
}

test('after a provider failure it offers the other account, retry here when the host allows it, and the project switch',async()=>{
 const s=suggestion(failed());
 assert.equal(screen.getByTestId('provider-suggestion').dataset.state,'failure');
 assert.equal(screen.getByText('Codex was overloaded').className,'dv-row-name');
 assert.match(screen.getByTestId('provider-suggestion').textContent,/Claude can carry on from here\.AskedYour request, as you wrote it\./);
 await screen.findByRole('button',{name:'Retry here'});
 // Continue opens the confirm first (the handoff that carries the work, 23 Sep): Send carries the confirm's id.
 fireEvent.click(screen.getByRole('button',{name:'Continue in Claude'}));
 await screen.findByTestId('handoff-confirm');
 assert.equal(s.calls.some(c=>c.operation==='coding_retry_on'),false,'opening the confirm sends nothing');
 fireEvent.click(await screen.findByRole('button',{name:'Send'}));
 await waitFor(()=>assert.ok(s.calls.some(c=>c.operation==='coding_retry_on')));
 assert.deepEqual(s.calls.find(c=>c.operation==='coding_retry_on').args,{projectId:'p',channelId:'c',taskId:'t1',handoffId:'h1'});
 await waitFor(()=>assert.equal(s.refreshed(),1));
 fireEvent.click(await screen.findByRole('button',{name:'Retry here'}));
 await waitFor(()=>assert.ok(s.calls.some(c=>c.operation==='task_continue')));
 assert.deepEqual(s.calls.find(c=>c.operation==='task_continue').args,{projectId:'p',channelId:'c',taskId:'t1',text:'Please try that again.'});
 const box=screen.getByRole('checkbox',{name:'Always switch automatically in this project'});
 assert.equal(box.checked,false);
 fireEvent.click(box);
 await waitFor(()=>assert.ok(s.calls.some(c=>c.operation==='coding_route_set')));
 assert.deepEqual(s.calls.find(c=>c.operation==='coding_route_set').args,{projectId:'p',channelId:'c',autoSwitch:true});
});

test('retry here is left out when the host says it can’t run, or the conversation moved to another account',async()=>{
 suggestion(failed(),{}, {retry:{available:false},rebound:false});
 await screen.findByRole('button',{name:'Continue in Claude'});
 await new Promise(r=>setTimeout(r,20));
 assert.equal(screen.queryByRole('button',{name:'Retry here'}),null);
 cleanup();
 suggestion(failed(),{}, {retry:{available:true},rebound:true});
 await new Promise(r=>setTimeout(r,20));
 assert.equal(screen.queryByRole('button',{name:'Retry here'}),null);
});

test('taken, automatic, failed-automatic and no-other-provider states say what happened',async()=>{
 suggestion(failed({taken:'t2'}));
 assert.equal(screen.getByTestId('provider-suggestion').dataset.state,'taken');
 assert.match(screen.getByTestId('provider-suggestion').textContent,/^Claude took over, after Codex was overloaded\.$/);
 assert.equal(screen.queryAllByRole('button').length,0);
 cleanup();
 suggestion(failed({automatic:true}));
 assert.match(screen.getByTestId('provider-suggestion').textContent,/Trying Claude by itself/);
 assert.ok(screen.getByTestId('provider-suggestion').querySelector('.dv-st[data-state="running"]'));
 cleanup();
 suggestion(failed({error:'This run left file changes or is still settling.'}));
 assert.match(screen.getByRole('note').textContent,/Dervo didn’t retry by itself: This run left file changes/);
 cleanup();
 const s=suggestion(failed({other:null}));
 assert.match(screen.getByTestId('provider-suggestion').textContent,/Add an account on the other provider to carry on there/);
 await screen.findByRole('button',{name:'Retry here'});
 assert.equal(screen.queryByRole('button',{name:/Continue in/}),null);
 assert.equal(screen.queryByRole('checkbox'),null,'nothing to switch to');
 assert.equal(s.calls.filter(c=>c.operation!=='task_detail').length,0);
});

test('one account at its usage limit: no Retry into the same limit, a way to add an account, and the reset said even when unknown (UX M1)',async()=>{
 const limit={reason:'Claude hit its usage limit.',kind:'usage_limit',other:null};
 const opened=[];
 const s=suggestion(failed(limit),{onAccounts:trigger=>opened.push(trigger.textContent)});
 await new Promise(r=>setTimeout(r,20));
 assert.equal(screen.queryByRole('button',{name:'Retry here'}),null,'retrying on an account that ran out runs into the same limit');
 assert.match(screen.getByTestId('provider-suggestion').textContent,/Codex hasn’t said when this resets\. Add an account on the other provider to carry on there\./);
 const add=screen.getByRole('button',{name:'Add an account'});
 assert.ok(add.classList.contains('dv-btn-primary'),'the one way through');
 fireEvent.click(add);
 assert.deepEqual(opened,['Add an account'],'it opens Connected accounts');
 assert.equal(s.calls.filter(c=>c.operation!=='task_detail').length,0,'nothing sent to the host');
 cleanup();
 // A known reset: Wait is offered and the unknown line isn't said.
 suggestion(failed(limit),{resetsAt:Date.now()/1000+3600});
 await screen.findByRole('button',{name:/^Wait for Codex/});
 assert.doesNotMatch(screen.getByTestId('provider-suggestion').textContent,/hasn’t said when/);
 assert.equal(screen.queryByRole('button',{name:'Retry here'}),null);
 assert.ok(screen.getByRole('button',{name:'Add an account'}).classList.contains('dv-btn-ghost'),'Wait comes first; adding an account is the quieter choice');
 cleanup();
 // Negative control: an overload isn't a usage limit, so Retry here stays and nothing is said about a reset.
 suggestion(failed({other:null}));
 await screen.findByRole('button',{name:'Retry here'});
 assert.doesNotMatch(screen.getByTestId('provider-suggestion').textContent,/hasn’t said when/);
 assert.ok(screen.getByRole('button',{name:'Add an account'}));
});

test('a low account on the latest request offers the other account for the next one, only while the conversation is still on it',async()=>{
 const low={...route({suggest:{...claude,reason:'Codex is at 90% of its usage window.'}})};
 const s=suggestion({id:'t1',state:'completed',origin:'conversation',route:low});
 assert.equal(screen.getByTestId('provider-suggestion').dataset.state,'low');
 assert.match(screen.getByTestId('provider-suggestion').textContent,/Codex is at 90% of its usage window\. Your next request here can use Claude\./);
 fireEvent.click(screen.getByRole('button',{name:'Use Claude next'}));
 await waitFor(()=>assert.ok(s.calls.some(c=>c.operation==='conversation_profile_set')));
 assert.deepEqual(s.calls.find(c=>c.operation==='conversation_profile_set').args,{projectId:'p',channelId:'c',expectedProfileId:'p',profileId:'q',expectedModel:'gpt',model:'claude-fable-5-1'});
 assert.equal(s.calls.some(c=>c.operation==='task_detail'),false,'a low-usage note reads nothing');
 cleanup();
 suggestion({id:'t1',state:'completed',origin:'conversation',route:low},{latest:false});
 assert.equal(screen.queryByTestId('provider-suggestion'),null);
 cleanup();
 suggestion({id:'t1',state:'completed',origin:'conversation',route:low},{conversationProfileId:'q'});
 assert.equal(screen.queryByTestId('provider-suggestion'),null);
});

test('the shell puts the model chip in the composer foot with the check and review inside the picker, Start fresh in the thread menu, and the reason and suggestion on each task',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 // The foot is built once and handed to the composer as its leading tools, so the pill holds the chip and the send button.
 // The picker shows on every thread with an account, reading only or allowed to code (design sweep, 19 Sep).
 assert.match(shell,/const composerFoot=current&&activity\.data\?\.coding&&activity\.data\.coding\.state!=='unsupported'&&binding\.data\s*\?<ComposerTools>\s*<ModelPicker /);
 assert.match(shell,/<LocalImageComposer onStop=\{running\?\(\)=>void stopHere\(\):undefined\} channelSuggestionNote=\{conversationMentionNote\} onInsertReady=\{insertReady\} formClassName="dv-composer" containerClassName="dv-composer-dock" toolbarLeadingActions=\{composerFoot\}/);
 // Bryce, 18 Sep: the foot carried the model, Start fresh, Review and Set up a reviewer. Only the model chip stays; this
 // message's check and second review are inside the picker, and "Set up a reviewer" opens Checks and review.
 assert.match(shell,/checks=\{granted\?activity\.data\.coding\.checks:undefined\} check=\{check\.value\} onCheck=\{granted\?check\.setValue:undefined\}/,'the check lives in the model picker');
 assert.match(shell,/review=\{activity\.data\.collaboration\?\.review\} reviewValue=\{review\.value\} onReview=\{granted\?review\.setValue:undefined\}/,'the second review lives in the model picker');
 assert.match(shell,/page==='checks'\?'review-setup'/,'reviewer setup opens from the picker');
 assert.match(shell,/<ContinuityTool key=\{`continuity:\$\{channelId\}`\}[^]*<\/ComposerNotes>/,'the note above the box still says when the next message starts a new session');
 assert.match(shell,/<ContinuityMenuItems projectId=\{projectId\} channelId=\{channelId\} view=\{activity\.data\.coding\.continuity\}/,'Start fresh moved to the ⋯ menu');
 assert.equal(shell.includes('<ComposerTools busy={!!tasks.data?.some(task=>task.state===\'working\')} queued={(queue.data??[]).filter(item=>item.state===\'queued\').length}><ModelPicker key'),false,'no second tools row under the composer');
 assert.match(shell,/<RouteWhy task=\{task\}\/>\{!\(guideVisible&&guideTask\?\.id===task\.id\)&&<ProviderSuggestion task=\{task\} projectId=\{projectId\} channelId=\{channelId\} policy=\{activity\.data\?\.coding\?\.route\} latest=\{task\.id===latestConversationTask\} conversationProfileId=\{binding\.data\?\.profile_id\}/);
 assert.match(shell,/suggestion=\{\(guideTask\.suggest\|\|guideTask\.route\?\.suggest\)&&<ProviderSuggestion task=\{guideTask\}/,'with the guide open, the suggestion is in the guide instead');
 const api=readFileSync(new URL('./api.ts',import.meta.url),'utf8');
 assert.match(api,/local<CodingPermission>\('conversation_route_set',\{projectId,channelId,mode\}\)/);
 assert.match(api,/local<CodingRequestResult>\('coding_retry_on',\{projectId,channelId,taskId\}\)/);
});

test('an account that ran out can wait for its reported reset, and the waiting request says when',async()=>{
 const later=Math.floor(Date.now()/1000)+3600;
 const limited=failed({reason:'Codex reached its usage limit.',kind:'usage_limit'});
 const s=suggestion(limited,{resetsAt:later});
 const wait=await screen.findByRole('button',{name:/^Wait for Codex · /});
 fireEvent.click(wait);
 await waitFor(()=>assert.ok(s.calls.some(c=>c.operation==='coding_wait_for_reset')));
 assert.deepEqual(s.calls.find(c=>c.operation==='coding_wait_for_reset').args,{projectId:'p',channelId:'c',taskId:'t1'});
 cleanup();
 suggestion(failed({reason:'Codex reached its usage limit.',kind:'usage_limit'}),{resetsAt:null});
 await screen.findByRole('button',{name:'Continue in Claude'});
 assert.equal(screen.queryByRole('button',{name:/^Wait for/}),null,'no reported reset, nothing to wait for');
 cleanup();
 suggestion(failed({reason:'Codex reached its usage limit.',kind:'usage_limit'}),{resetsAt:Math.floor(Date.now()/1000)-10});
 await screen.findByRole('button',{name:'Continue in Claude'});
 assert.equal(screen.queryByRole('button',{name:/^Wait for/}),null,'a reset already past is not a wait');
 cleanup();
 suggestion(failed({reason:'Codex was overloaded.',kind:'overloaded'}),{resetsAt:later});
 await screen.findByRole('button',{name:'Continue in Claude'});
 assert.equal(screen.queryByRole('button',{name:/^Wait for/}),null,'an overload has no reset to wait for');
 cleanup();
 suggestion({...limited,waiting_for_reset:{queue_id:'q1',until:later}},{resetsAt:later});
 assert.equal(screen.getByTestId('provider-suggestion').dataset.state,'waiting');
 assert.match(screen.getByTestId('provider-suggestion').textContent,/Waiting for the reset at .*Then the same request starts again/);
 assert.equal(screen.queryAllByRole('button').length,0);
});

test('composer selects are as wide as the chosen option, with the native select laid over the text',()=>{
 const css=readFileSync(new URL('./ComposerTools.css',import.meta.url),'utf8');
 assert.match(css,/\.dervo-composer-tool \.dervo-fit-select>select \{ position:absolute; inset:0; width:100%;[^}]*opacity:0;/);
 render(React.createElement(AccountTool,{projectId:'p',channelId:'c',route:{mode:'auto',alternative:null},provider:'codex',model:'m',onChangeAccount(){},onChanged:async()=>{}}));
 const select=screen.getByLabelText('When this account is low or fails');
 assert.equal(select.closest('.dervo-fit-select').firstElementChild.textContent,'Auto');
 assert.equal(select.closest('.dervo-fit-select').firstElementChild.getAttribute('aria-hidden'),'true');
});
