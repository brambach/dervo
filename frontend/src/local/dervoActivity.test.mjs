import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
window.matchMedia=()=>({matches:true,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,cleanup,fireEvent,screen}=await import('@testing-library/react');
const {DervoActivity,latestObservableActivity,observableActivity,activityBasename}=await import('./DervoActivity.tsx');
const {ConversationIndicator}=await import('./TurnControls.tsx');
const {TaskFooter}=await import('./CodingTasks.tsx');
const {TaskGuide}=await import('./TaskGuide.tsx');
const {taskFlow,summariseDiff}=await import('./TaskFlow.tsx');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const wrap=(...children)=>{const qc=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});return React.createElement(QueryClientProvider,{client:qc},...children);};
afterEach(cleanup);after(()=>dom.window.close());
const binding={provider:'codex',identity:'fixture@example.test',model:'test',cwd:'/fixture/project',visibility:'shared'};
const task={id:'t',state:'working',binding,runs:[{id:'r',state:'running'}],tokens:[],messages:[{role:'user',text:'Fix the result.',created_at:1}],files:['src/result.ts'],check_profile:'typescript-check'};
const token={run_id:'r',tool:'dervo_write_file',state:'executing',path:'/private/secret/project/src/result.ts'};

test('observable actions use only the current run and file basename, never stale results or arbitrary tool data',()=>{
 assert.equal(latestObservableActivity({...task,tokens:[token]}),'Editing result.ts');
 assert.equal(latestObservableActivity({...task,tokens:[{...token,run_id:'old'}]}),undefined);
 for(const patch of [{state:'delivered'},{state:'uncertain'},{state:'cancelled'},{success:false},{tool:'shell',command:'SECRET'}])assert.equal(latestObservableActivity({...task,tokens:[{...token,...patch}]}),undefined);
 assert.equal(observableActivity({...token,tool:'dervo_run_tests',path:'SECRET'}),'Running configured checks');
 assert.equal(observableActivity({...token,tool:'dervo_read_file',state:'queued'}),'Waiting for a tool request');
 assert.equal(activityBasename('C:\\private\\folder\\file.ts'),'file.ts');
 assert.equal(activityBasename('/project/\u202efile.ts\n'),'file.ts');
 assert.equal(activityBasename('x'.repeat(200)).length,46);
});

test('working lifecycle preserves the button and status container, drops stale tool text, and keeps settings usable',()=>{
 const calls=[];
 const props={binding,task:{...task,tokens:[token]},onSettings:trigger=>calls.push(trigger)};
 const view=render(React.createElement(ConversationIndicator,{...props,status:{state:'working',active:true,detail:'Recorded work'}}));
 const button=screen.getByRole('button'),activity=view.container.querySelector('.dervo-activity');
 assert.ok(screen.getByText('Codex is working'));assert.ok(screen.getByText('Editing result.ts'));
 assert.ok(!button.textContent.includes('/private'));
 const secondary=activity.querySelector('.dervo-activity-secondary');
 assert.equal(secondary.matches(':empty'),false,'a second line shows while it works');
 for(const [state,label] of [['needs_permission','Needs permission'],['unknown','Delivery unknown']]){
  view.rerender(React.createElement(ConversationIndicator,{...props,status:{state,active:false,detail:'Details'}}));
  assert.equal(screen.getByRole('button'),button);assert.equal(view.container.querySelector('.dervo-activity'),activity);
  assert.ok(screen.getByText(label));assert.equal(screen.queryByText('Editing result.ts'),null);
  assert.equal(activity.hasAttribute('data-active'),false);
  // #23: with no second line the span is empty, and the stylesheet drops it so the glyph centres on the label.
  assert.equal(secondary.matches(':empty'),true);
 }
 const css=readFileSync(new URL('./DervoActivity.css',import.meta.url),'utf8');
 assert.match(css,/\.dervo-activity-secondary:empty \{ display:none; \}/);
 assert.match(css,/\.dervo-activity \{[^}]*align-items:center;[^}]*min-height:38px;/,'the row keeps its height whether or not the line shows');
 fireEvent.click(button);assert.equal(calls[0],button);
});

test('pending review overrides a settled owner and cannot announce completion or reuse the owner operation',()=>{
 const props={binding,task:{...task,tokens:[token]},onSettings(){},status:{state:'completed',active:false,review_active:true,review_phase:'pending',detail:'Waiting'}};
 const view=render(React.createElement(ConversationIndicator,props));
 assert.ok(screen.getByText('Waiting for independent review'));
 assert.equal(screen.queryByText('Completed'),null);assert.equal(screen.queryByText('Editing result.ts'),null);
 assert.equal(view.container.querySelector('.dervo-activity').dataset.tone,'neutral');
 view.rerender(React.createElement(ConversationIndicator,{...props,status:{...props.status,state:'blocked',interrupted:true,review_phase:'initial'}}));
 assert.ok(screen.getByText('Owner stopped, review running'));
 view.rerender(React.createElement(ConversationIndicator,{...props,status:{...props.status,state:'unknown',outcome_uncertain:true}}));
 assert.ok(screen.getByText('Outcome unknown'));assert.equal(screen.queryByText('Waiting for independent review'),null);
});

test('the flow rail shows only recorded steps of the current run, in words and file names, never predicted progress',()=>{
 const rows=value=>taskFlow(value).map(row=>[row.state,row.text,row.files.join(','),row.meta??'']);
 const read=path=>({...token,tool:'dervo_read_file',state:'delivered',success:true,path});
 assert.deepEqual(rows({...task,tokens:[read('/private/a.ts'),read('/private/b.ts'),token,{...token,tool:'shell',state:'executing',path:'SECRET'},{...token,run_id:'old',state:'executing'}]}),
  [['done','Read','a.ts,b.ts',''],['live','Editing','result.ts','']]);
 assert.deepEqual(rows(task),[['live','Waiting for Codex','','']],'a working owner with no open tool call is waiting on the provider, not doing an invented step');
 const check={...token,tool:'dervo_run_tests',state:'delivered',success:true,path:'SECRET',started_at:100,ended_at:112.4};
 const completed={...task,state:'completed',runs:[{id:'r',state:'completed'}],completion:{run_id:'r',attempt:1,changed_files:['src/result.ts'],test_exit_status:0,review_phase:null,human_approved:false,at:1}};
 assert.deepEqual(rows({...completed,tokens:[check]}),[['done','TypeScript check passed','','exit 0 · 12 s']]);
 assert.deepEqual(rows({...completed,completion:{...completed.completion,run_id:'old'},tokens:[check]}),[['done','TypeScript check passed','','12 s']],'an older completion never lends its exit code');
 // A check call that errored is a done step with a caption: the agent carried on from it. Only the run's end can fail, and that is the status line's.
 assert.deepEqual(rows({...task,tokens:[{...check,success:false}]}).at(0),['attention','TypeScript check didn’t finish','','Returned an error']);
 assert.equal(taskFlow({...task,tokens:[{...check,state:'running'}]})[0].timed,true);
 assert.deepEqual(rows({...task,state:'blocked',runs:[{id:'r',state:'failed'}],tokens:[read('a.ts')]}),[['done','Read','a.ts','']],'how the run ended is the status line, never a red row');
 assert.deepEqual(rows({...task,state:'blocked',runs:[{id:'r',state:'interrupted'}]}),[]);
 assert.deepEqual(rows({...task,state:'disconnected',runs:[{id:'r',state:'uncertain'}],tokens:[{...token,state:'uncertain'}]}),[['attention','Editing','result.ts','Not confirmed']]);
 assert.deepEqual(rows({...task,state:'disconnected',runs:[{id:'r',state:'uncertain'}]}),[]);
 // A step left open in the record of a run that has ended is the last saved step, never a spinner (design review S2).
 assert.deepEqual(rows({...task,state:'answered',runs:[{id:'r',state:'completed'}],tokens:[{...check,state:'running',ended_at:undefined}]}),
  [['unknown','Running the TypeScript check','','Last saved step'],['done','Answered','','']]);
 const offline=taskFlow({...task,tokens:[token]},true);
 assert.deepEqual(offline.map(row=>row.state),['unknown'],'unavailable updates never animate or claim a halt');
 assert.deepEqual(rows({...task,state:'ready_for_review',runs:[{id:'r',state:'completed'}],collaboration:{review:{phase:'initial'}}}),[['live','Independent review in progress','','']]);
 assert.deepEqual(rows({...task,collaboration:{review:{phase:'pending'}}}),[['live','Waiting for Codex','','']],'pending review while the owner works adds no row');
 assert.deepEqual(rows({...task,state:'completed',runs:[{id:'r',state:'completed'}],collaboration:{review:{phase:'passed'}}}),[['done','Independent review passed','','']]);
});

test('the diff summary counts and caps changed lines',()=>{
 const diff=summariseDiff('src/a.ts',['--- a','+++ b','@@','-one','+two',...Array.from({length:9},(_,i)=>`+line${i}`),' context'].join('\n'));
 assert.equal(diff.added,10);assert.equal(diff.removed,1);assert.equal(diff.lines.length,6);assert.deepEqual(diff.lines[0],{kind:'del',text:'one'});
});

test('the panel marks one current step, opens its record from the foot, and keeps Stop while the reviewer runs',async()=>{
 const requests=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation})=>{requests.push(operation);return {task,retry:{available:false},evidence:{attempts:[]}};}};
 const props={task,projectId:'p',channelId:'c',refresh:async()=>{},onClose(){}};
 const qc=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const element=extra=>React.createElement(QueryClientProvider,{client:qc},React.createElement(TaskGuide,{...props,...extra}));
 const view=render(element({}));
 const guide=screen.getByTestId('task-guide');
 assert.equal(guide.querySelectorAll('[aria-current="step"]').length,1);
 assert.match(guide.querySelector('[aria-current="step"]').textContent,/Waiting for Codex/);
 assert.deepEqual(requests,[]);
 fireEvent.click(screen.getByRole('button',{name:'Open the record'}));
 assert.ok(screen.getByTestId('task-record'));
 fireEvent.click(screen.getByRole('button',{name:'Back to progress'}));
 view.rerender(element({task:{...task,state:'ready_for_review',runs:[{id:'r',state:'completed'}],collaboration:{review:{phase:'initial'}}}}));
 assert.equal(screen.getByTestId('task-guide'),guide);
 assert.equal(guide.querySelectorAll('[aria-current="step"]').length,1);
 assert.match(guide.querySelector('[aria-current="step"]').textContent,/Independent review in progress/);
 assert.equal(screen.getByRole('status').textContent,'Reviewing');
 assert.ok(screen.getByRole('button',{name:'Pause'}));
});

test('unknown and unavailable state never animate or expose old file observations',()=>{
 const view=render(React.createElement(DervoActivity,{label:'Outcome unknown',tone:'attention',active:true}));
 assert.equal(view.container.querySelector('.dervo-activity').hasAttribute('data-active'),false);
 assert.ok(screen.getByRole('status').textContent.includes('Outcome unknown'));
});


test('finished conversation strips disappear, but review, uncertainty and stale results keep their controls',()=>{
 const finished={...task,state:'answered',runs:[{id:'r',state:'completed'}],tokens:[]};
 const props={binding,task:finished,onSettings(){},status:{state:'answered',active:false,detail:'Finished'}};
 const view=render(React.createElement(ConversationIndicator,props));
 assert.equal(screen.queryByTestId('conversation-status'),null);
 for(const patch of [{review_active:true,review_phase:'pending'},{outcome_uncertain:true},{stale_completion:true},{interrupted:true},{review_phase:'failed'}]){
  view.rerender(React.createElement(ConversationIndicator,{...props,status:{...props.status,...patch}}));
  assert.ok(screen.getByTestId('conversation-status'));
 }
 view.rerender(React.createElement(ConversationIndicator,{...props,error:true}));
 assert.ok(screen.getByText('Status unavailable'));
 view.rerender(React.createElement(ConversationIndicator,{...props,task:{...finished,tokens:[{...token,state:'uncertain'}]}}));
 assert.ok(screen.getByTestId('conversation-status'));
});


test('a text answer has no decision bar; a finished result has one, whether or not the panel is open',()=>{
 window.__TAURI_INTERNALS__={invoke:async()=>({task,retry:{available:false},evidence:{attempts:[]}})};
 const finished={...task,state:'answered',runs:[{id:'r',state:'completed'}],tokens:[],collaboration:{review:{phase:'passed'}}};
 const props={task:finished,projectId:'p',channelId:'c',onInspect(){},guideVisible:false};
 const view=render(wrap(React.createElement(TaskFooter,props)));
 assert.equal(view.container.querySelector('[data-testid="task-footer"]'),null);
 view.rerender(wrap(React.createElement(TaskFooter,{...props,task:{...finished,state:'completed',completion:{run_id:'r',changed_files:['src/result.ts'],test_exit_status:0,review_phase:'passed'}}})));
 assert.equal(screen.getByRole('status').textContent,'Finished. TypeScript check passed. Review passed.');
 view.rerender(wrap(React.createElement(TaskFooter,{...props,guideVisible:true,task:{...finished,state:'completed',completion:{run_id:'r',changed_files:['src/result.ts'],test_exit_status:0,review_phase:'passed'}}})));
 assert.ok(screen.getByRole('status'),'the bar stays when the panel opens; the panel never repeats it');
});

test('MCP servers Claude Code couldn’t start are named in the steps, so a missing tool is never silent (9 Oct, parity)',()=>{
 const withConnectors=connectors=>taskFlow({...task,binding:{...binding,provider:'claude'},runs:[{id:'r',state:'running',connectors}]});
 const [row]=withConnectors({state:'ready',pending:[],failed:['linear','claude.ai Gmail']});
 assert.equal(row.state,'problem');
 assert.equal(row.text,'Couldn’t start linear and Gmail');
 assert.match(row.meta,/went on without them/);
 assert.match(withConnectors({state:'ready',pending:[],failed:['linear']})[0].meta,/went on without it\./);
 assert.equal(withConnectors({state:'ready',pending:[],failed:['a','b','c','d','e']})[0].text,'Couldn’t start a, b, c and 2 more');
 // Negative: nothing failed, or a record from before this field, adds no row.
 for(const connectors of [{state:'ready',pending:[],failed:[]},{state:'slow',pending:['x']},undefined,{failed:'nope'},{failed:[3,null,'']}])
  assert.equal(withConnectors(connectors).some(r=>r.id==='r:connectors'),false);
});
