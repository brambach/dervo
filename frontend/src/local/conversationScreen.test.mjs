import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event','ResizeObserver'])if(dom.window[key])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,cleanup,screen}=await import('@testing-library/react');
const {ConversationIndicator,statusLabel}=await import('./TurnControls.tsx');
const {TaskFooter}=await import('./CodingTasks.tsx');
const {TaskGuide,taskGuide}=await import('./TaskGuide.tsx');
afterEach(cleanup);after(()=>dom.window.close());
const binding={provider:'claude',identity:'me@example.test',model:'claude-fable-5-1',cwd:'/approved',visibility:'private'};
const error='Assistant message used <synthetic> instead of the selected model claude-fable-5-1';
const task={id:'t',state:'disconnected',origin:'conversation',session_id:'s',scope_version:1,files:['a.py'],argv:[],binding,error,check_profile:'python-unittest',
 messages:[{role:'user',text:'Archive channels',created_at:1}],tokens:[],runs:[{id:'r',state:'uncertain'}],
 collaboration:{review:{enabled:true,reviewer:'review-2f70353c3edfea781a387dca',effort:'low',phase:'skipped',error:`The owner ended disconnected: ${error}. No review was sent.`}}};
const status={state:'disconnected',active:false,outcome_uncertain:true,detail:error,coding:{state:'granted',detail:'d',files:['a.py'],test_file:'a.py'}};
const count=(text,needle)=>text.split(needle).length-1;

test('the composer indicator yields to an open Task guide for a task, and keeps every other case',()=>{
 const view=render(React.createElement(ConversationIndicator,{binding,status,task,guideVisible:true,onSettings(){}}));
 assert.equal(screen.queryByTestId('conversation-status'),null);
 view.rerender(React.createElement(ConversationIndicator,{binding,status,task,guideVisible:false,onSettings(){}}));
 assert.match(screen.getByTestId('conversation-status').textContent,/Outcome unknown/);
 assert.equal(screen.queryByText(error),null,'the detail that repeats the task error is not printed here');
 view.rerender(React.createElement(ConversationIndicator,{binding,status:{...status,detail:'Dervo lost confirmation of a run or tool result.'},task:{...task,error:null},guideVisible:false,onSettings(){}}));
 assert.ok(screen.getByText('Dervo lost confirmation of a run or tool result.'),'a detail with no other home still shows');
 view.rerender(React.createElement(ConversationIndicator,{binding,status,task,error:true,guideVisible:true,onSettings(){}}));
 assert.match(screen.getByTestId('conversation-status').textContent,/Status unavailable/,'a status error is never hidden behind the guide');
 view.rerender(React.createElement(ConversationIndicator,{binding,status,guideVisible:true,onSettings(){}}));
 assert.ok(screen.getByTestId('conversation-status'),'no task means the guide has nothing to own');
});

const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {decisionLine}=await import('./CodingTasks.tsx');
const {panelState}=await import('./TaskGuide.tsx');
const wrap=(...children)=>{const qc=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});qc.setQueryData(['local-conversations','p'],[{id:'c',name:'Archive channels'}]);return React.createElement(QueryClientProvider,{client:qc},...children);};
const footer=(extra={})=>React.createElement(TaskFooter,{task,projectId:'p',channelId:'c',onInspect(){},...extra});
const guide=()=>React.createElement(TaskGuide,{task,projectId:'p',channelId:'c',refresh:async()=>{},onClose(){}});

test('the task error appears exactly once on the screen, in the decision bar, whether or not the panel is open',()=>{
 window.__TAURI_INTERNALS__={invoke:async()=>({task,retry:{available:false},evidence:{attempts:[]}})};
 const closed=render(wrap(footer(),React.createElement(ConversationIndicator,{binding,status,task,guideVisible:false,onSettings(){}})));
 assert.equal(count(closed.container.textContent,error),1);
 assert.ok(closed.container.querySelector('.dv-decision'),'closed: the decision bar under the report is the home');
 cleanup();
 const open=render(wrap(footer({guideVisible:true}),guide()));
 assert.equal(count(open.container.textContent,error),1);
 assert.equal(count(screen.getByTestId('task-guide').textContent,error),0,'open: the panel says how the run went, not why');
 assert.equal(open.container.querySelector('[data-testid="task-footer"] [role="alert"]'),null);
 cleanup();
 const working=render(wrap(footer({task:{...task,state:'working',error:null,runs:[{id:'r',state:'running'}]}})));
 assert.equal(working.container.querySelector('[data-testid="task-footer"]'),null,'nothing under a message while the owner works');
});

test('the panel header names the thread, and its status line says how the run went without the error or a red step',()=>{
 render(wrap(guide()));
 const panel=screen.getByTestId('task-guide');
 assert.equal(panel.querySelector('.dervo-character'),null);
 assert.equal(panel.querySelector('.dv-panel-kicker').textContent,'Progress');
 assert.equal(panel.querySelector('.dv-panel-title').textContent,'Archive channels');
 assert.equal(screen.getByRole('status').textContent,'Not confirmed');
 assert.doesNotMatch(panel.textContent,/Claude Code · Fable|synthetic/,'the byline in the thread names the agent, and the error stays out');
 assert.equal(panel.querySelectorAll('[aria-current="step"]').length,0);
 assert.equal(panel.querySelectorAll('.dv-step [data-state="failed"]').length,0);
});

test('the decision bar and the panel never say the same thing: the bar decides, the panel reports the run',()=>{
 const cases=[
  task,
  {...task,state:'ready_for_review',error:null,runs:[{id:'r',state:'completed'}],review:'rejected',check_profile:'manual-review',collaboration:undefined},
  {...task,state:'ready_for_review',error:null,runs:[{id:'r',state:'completed'}],collaboration:undefined,acceptance:{required:true}},
  {...task,state:'blocked',runs:[{id:'r',state:'failed'}],collaboration:undefined},
  {...task,state:'idle',error:null,runs:[{id:'r',state:'completed'}],review:'approved',collaboration:undefined,commit:{sha:'abc1234def',branch:'main'}},
 ];
 for(const record of cases){
  const decision=decisionLine(record),state=panelState(record);
  assert.ok(decision,JSON.stringify(record.state));
  assert.notEqual(decision.lead.replace(/\.$/,''),state.word);
  if(record.error){assert.match(`${decision.lead} ${decision.clause}`,new RegExp(record.error.slice(0,20)));assert.doesNotMatch(JSON.stringify(state),new RegExp(record.error.slice(0,20)));}
 }
 assert.equal(decisionLine({...task,state:'blocked',runs:[{id:'r',state:'failed'}],suggest:{reason:'Claude hit its usage limit.',kind:'usage_limit',other:null,automatic:false,taken:null,error:null}}),null,'a handoff row is the home for a failure on the provider side');
 assert.equal(decisionLine({...task,state:'answered',error:null,runs:[{id:'r',state:'completed'}],collaboration:undefined}),null,'nothing under a text answer');
});

test('with the task footer in the conversation, a running task’s status isn’t repeated in the bar above the composer',async()=>{
 const {render:mount,cleanup:clear}=await import('@testing-library/react');
 const binding={provider:'claude',identity:'me@example.test',model:'claude-opus-5'};
 const running={id:'t',state:'working',origin:'conversation',tokens:[],runs:[{id:'r',state:'running'}],messages:[],binding};
 const status={state:'working',active:true,detail:'Working'};
 const shown=mount(React.createElement(ConversationIndicator,{binding,status,task:running,onSettings(){}}));
 assert.ok(shown.container.querySelector('[data-testid="conversation-status"]'),'without the footer the bar still reports it');
 shown.unmount();
 const hidden=mount(React.createElement(ConversationIndicator,{binding,status,task:running,taskFooterShown:true,onSettings(){}}));
 assert.equal(hidden.container.querySelector('[data-testid="conversation-status"]'),null);
 hidden.rerender(React.createElement(ConversationIndicator,{binding,status:{...status,outcome_uncertain:true},task:running,taskFooterShown:true,onSettings(){}}));
 assert.ok(hidden.container.querySelector('[data-testid="conversation-status"]'),'an unconfirmed outcome still shows');
 clear();
});

test('a result ready for review is said once, by the decision bar, even with Preview in the second column',async()=>{
 const {render:mount,cleanup:clear}=await import('@testing-library/react');
 const binding={provider:'claude',identity:'me@example.test',model:'claude-opus-5'};
 const ready={id:'t',state:'ready_for_review',origin:'conversation',tokens:[],runs:[{id:'r',state:'completed'}],messages:[],binding,collaboration:{review:{phase:'skipped'}}};
 const status={state:'ready_for_review',active:false,detail:'Ready for review',coding:{state:'granted'}};
 assert.equal(decisionLine(ready)?.lead,'Ready for review.','the decision bar under the report says it');
 const view=mount(React.createElement(ConversationIndicator,{binding,status,task:ready,taskFooterShown:true,guideVisible:false,onSettings(){}}));
 assert.equal(view.container.querySelector('[data-testid="conversation-status"]'),null,'the bar above the composer stays quiet');
 view.rerender(React.createElement(ConversationIndicator,{binding,status,task:ready,guideVisible:false,onSettings(){}}));
 assert.ok(view.container.querySelector('[data-testid="conversation-status"]'),'without the footer in the chat the bar still reports it');
 const failed={...ready,state:'blocked',error:'The server is overloaded.'};
 view.rerender(React.createElement(ConversationIndicator,{binding,status:{...status,state:'blocked',detail:'The server is overloaded.'},task:failed,taskFooterShown:true,onSettings(){}}));
 assert.ok(view.container.querySelector('[data-testid="conversation-status"]'),'a failure keeps its line');
 view.rerender(React.createElement(ConversationIndicator,{binding,status,task:ready,taskFooterShown:true,error:true,onSettings(){}}));
 assert.match(view.container.textContent,/Status unavailable/,'a status error is never hidden');
 clear();
});

test('a filed thread’s head reads its workstream, then its own name, so the name is on screen with the sidebar folded',async()=>{
 const {render:mount,cleanup:clear}=await import('@testing-library/react');
 const {DervoMainHead}=await import('./DervoShell.tsx');
 const view=mount(React.createElement(DervoMainHead,{title:'dervo app',crumb:'Orchestrator',here:'Ideas storage',detail:'Ideas storage'}));
 const crumb=view.container.querySelector('.dv-crumb');
 assert.equal(crumb.textContent,'Orchestrator/Ideas storage');
 assert.equal(view.container.querySelector('[aria-current="page"]').textContent,'Ideas storage');
 view.rerender(React.createElement(DervoMainHead,{title:'dervo app',crumb:'Home'}));
 assert.equal(view.container.querySelector('.dv-crumb').textContent,'Home','other heads keep one crumb');
 const {readFileSync}=await import('node:fs');
 assert.match(readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8'),/here=\{current\.organisationGroup\?current\.name:undefined\}/,'an unfiled thread already shows its name as the crumb');
 clear();
});
