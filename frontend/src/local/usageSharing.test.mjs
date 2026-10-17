import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','FocusEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {usageScreen,useScreenView,track,reportCrash}=await import('./usage.ts');
const {ModelPicker}=await import('./ModelPicker.tsx');
const {UsageSharingSettings,useUsageNotice,TestersUsage,TestersPeople,TestersErrors,peopleSummary,seenLine,doneLine,errorPlace,errorDuring,errorCounts,topScreens,taskLines,duration,dayLine,screenLabel,SENT,SHARING_LINE,NOTICE_LINE}=await import('./UsageSharing.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const h=React.createElement;

/** Usage sharing (backend/usage_events.py): the renderer's screen views, the Preferences switch, the one-time notice and
 * the admins' Usage section on Testers. The host decides what's sent; these check what the app asks it for. */
function host(answers={}){
  const calls=[];
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
    if(command!=='local_request')return null;
    calls.push({operation:args.operation,args:args.args});
    const answer=answers[args.operation];
    if(answer instanceof Error)throw answer;
    return typeof answer==='function'?answer(args.args):answer??null;
  }};
  return calls;
}
const mount=node=>render(h(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},node));
const base={firstRun:false,settingsPage:null,knowledge:false,search:false,catchUp:false,releases:false,thread:false,home:false};

test('the screen name: Settings covers what is under it, then panels, then a thread, then Home; first run is not a screen', ()=>{
  assert.equal(usageScreen({...base,home:true}),'home');
  assert.equal(usageScreen({...base,home:true,thread:true}),'thread');
  assert.equal(usageScreen({...base,home:true,thread:true,knowledge:true}),'knowledge');
  assert.equal(usageScreen({...base,search:true,catchUp:true}),'catch_up');
  assert.equal(usageScreen({...base,search:true}),'search');
  assert.equal(usageScreen({...base,thread:true,knowledge:true,settingsPage:'preferences'}),'settings.preferences');
  assert.equal(usageScreen({...base,firstRun:true,home:true}),null);
  assert.equal(usageScreen(base),null);
});

test('a screen view goes once per screen change and once per thread opened, with only the screen name', async ()=>{
  const calls=host();
  function Screen({name,thread}){useScreenView(name,thread);return null;}
  const view=mount(h(Screen,{name:'home'}));
  view.rerender(h(QueryClientProvider,{client:new QueryClient()},h(Screen,{name:'home'})));
  view.rerender(h(QueryClientProvider,{client:new QueryClient()},h(Screen,{name:'thread',thread:'t1'})));
  view.rerender(h(QueryClientProvider,{client:new QueryClient()},h(Screen,{name:'thread',thread:'t2'})));
  view.rerender(h(QueryClientProvider,{client:new QueryClient()},h(Screen,{name:null,thread:'t2'})));
  await waitFor(()=>assert.equal(calls.length,3));
  assert.deepEqual(calls.map(call=>call.args),[{event:'screen_viewed',props:{screen:'home'}},{event:'screen_viewed',props:{screen:'thread'}},{event:'screen_viewed',props:{screen:'thread'}}]);
  assert.ok(calls.every(call=>call.operation==='usage_event'));
  assert.ok(!JSON.stringify(calls).includes('t1'),'the thread id never leaves');
});

test('sending never throws, with no host or a host that refuses', async ()=>{
  delete window.__TAURI_INTERNALS__;
  assert.doesNotThrow(()=>track('model_picker_opened'));
  host({usage_event:new Error('Usage sharing is unavailable in this session.')});
  assert.doesNotThrow(()=>track('screen_viewed',{screen:'home'}));
  await new Promise(resolve=>setTimeout(resolve,10));
});

test('Preferences: the switch shows the saved choice, turning it off saves at once, and the line says what never goes', async ()=>{
  let enabled=true;
  const calls=host({usage_sharing:()=>({enabled,notice:false}),usage_sharing_set:args=>{enabled=args.enabled;return {enabled,notice:false};}});
  mount(h(UsageSharingSettings));
  const toggle=await screen.findByRole('switch',{name:'Share usage and error reports with Dervo'});
  await waitFor(()=>assert.equal(toggle.getAttribute('aria-checked'),'true'));
  assert.ok(document.body.textContent.includes(SHARING_LINE));
  assert.match(SHARING_LINE,/Never your prompts, code, files, names or messages/);
  fireEvent.click(toggle);
  await waitFor(()=>assert.equal(toggle.getAttribute('aria-checked'),'false'));
  assert.deepEqual(calls.filter(call=>call.operation==='usage_sharing_set').map(call=>call.args),[{enabled:false}]);
  const listed=document.querySelector('.dv-disclose').textContent;
  for(const [name] of SENT)assert.ok(listed.includes(name),name);
  assert.match(listed,/Never.*Prompts, messages, replies, your file names and paths/);
  assert.match(listed,/Errors.*KeyError/);
});

test('Preferences: a refused save says so and leaves the switch as the host has it', async ()=>{
  host({usage_sharing:{enabled:true,notice:false},usage_sharing_set:new Error('Share usage is exactly on (true) or off (false)')});
  mount(h(UsageSharingSettings));
  const toggle=await screen.findByRole('switch',{name:'Share usage and error reports with Dervo'});
  await waitFor(()=>assert.equal(toggle.getAttribute('aria-checked'),'true'));
  fireEvent.click(toggle);
  await screen.findByText('Share usage is exactly on (true) or off (false)');
  assert.equal(toggle.getAttribute('aria-checked'),'true');
});

test('the notice shows once: it is marked seen as it shows, and never when it is not due or sharing is off', async ()=>{
  function Notice(){useUsageNotice(()=>{});return null;}
  let calls=host({usage_sharing:{enabled:true,notice:true},usage_notice_seen:{enabled:true,notice:false}});
  mount(h(Notice));
  await waitFor(()=>assert.equal(calls.filter(call=>call.operation==='usage_notice_seen').length,1));
  cleanup();
  calls=host({usage_sharing:{enabled:true,notice:false}});
  mount(h(Notice));
  await waitFor(()=>assert.ok(calls.some(call=>call.operation==='usage_sharing')));
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(calls.filter(call=>call.operation==='usage_notice_seen').length,0);
  cleanup();
  calls=host({usage_sharing:{enabled:false,notice:true}});
  mount(h(Notice));
  await waitFor(()=>assert.ok(calls.some(call=>call.operation==='usage_sharing')));
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(calls.filter(call=>call.operation==='usage_notice_seen').length,0);
  assert.match(NOTICE_LINE,/where its errors happen.*never what’s in them/);
});

const REPORT={since:0,days:7,active:3,
  daily:[{day:'2026-09-17',users:2,events:40},{day:'2026-09-19',users:3,events:61}],
  events:[],
  screens:[{day:'2026-09-17',screen:'thread',users:2,count:20},{day:'2026-09-19',screen:'thread',users:3,count:22},
    {day:'2026-09-19',screen:'home',users:3,count:12},{day:'2026-09-19',screen:'settings.preferences',users:1,count:2}],
  funnel:[{step:'sign_in',reached:4,completed:3},{step:'pending',reached:2,completed:2},{step:'folder',reached:3,completed:3},
    {step:'start',reached:3,completed:2},{step:'project_created',reached:0,completed:2}],
  tasks:[{provider:'claude',outcome:'completed',count:12,users:3,median_seconds:80},{provider:'claude',outcome:'failed',count:2,users:1,median_seconds:null},
    {provider:'codex',outcome:'stopped',count:1,users:1,median_seconds:5}]};

test('the report reads plainly: screens by views, tasks by provider, active testers by day', ()=>{
  assert.deepEqual(topScreens(REPORT).map(row=>[row.label,row.count]),[['A thread',42],['Home',12],['Settings, Preferences',2]]);
  assert.equal(screenLabel('settings.accounts'),'Settings, Connected accounts');
  assert.deepEqual(taskLines(REPORT),[
    {provider:'claude',label:'Claude',line:'12 completed · 2 failed · 0 stopped. Median 1 min 20 s to complete.'},
    {provider:'codex',label:'Codex',line:'0 completed · 0 failed · 1 stopped'}]);
  assert.equal(duration(45),'45 s');assert.equal(duration(3600),'1 h 0 min');assert.equal(duration(120),'2 min');
  assert.equal(dayLine(REPORT),'Sat 3 · Thu 2');
});

test('Testers, Usage: an admin sees active testers, top screens, the funnel and task outcomes', async ()=>{
  const calls=host({usage_report:{state:'ready',report:REPORT}});
  mount(h(TestersUsage));
  await screen.findByText('3 testers active');
  const text=document.body.textContent;
  assert.ok(text.includes('By day: Sat 3 · Thu 2. Days are UTC.'));
  assert.ok(text.includes('A thread42 views'));
  assert.ok(text.includes('Sign in4 reached · 3 finished'));
  // 30 Sep: Approval showed "1 reached · 0 finished" because only people who had to wait for it pass through it, and an
  // approval that landed after their project didn't count. It says who waited and how many of them got in.
  assert.ok(text.includes('Approval2 waited · 2 approved'));
  assert.ok(!text.includes('Approval2 reached'));
  assert.ok(text.includes('Project created2 created'));
  assert.ok(text.includes('Claude12 completed · 2 failed · 0 stopped'));
  assert.deepEqual(calls.map(call=>[call.operation,call.args]),[['usage_report',{reload:false}]]);
});

test('Testers, Usage: a failed load says so, and Try again asks again', async ()=>{
  const calls=host({usage_report:args=>args.reload?{state:'ready',report:{...REPORT,daily:[],screens:[],tasks:[]}}:{state:'error',report:null}});
  mount(h(TestersUsage));
  fireEvent.click(await screen.findByRole('button',{name:'Try again'}));
  await screen.findByText(/Nothing yet/);
  assert.deepEqual(calls.map(call=>call.args),[{reload:false},{reload:true}]);
});

const PERSON={user_id:'p1',email:'maya@example.test',status:'approved',is_admin:false,last_seen:Date.UTC(2026,8,28,1,5)/1000,app_version:'0.1.0-beta.16.3',
  active_days:2,opens:3,threads:1,home_requests:1,tasks_started:3,tasks_completed:1,tasks_failed:1,claude_tasks:1,codex_tasks:2,knowledge:0,feedback_opened:0};
const QUIET={...PERSON,user_id:'p2',email:'jordan@example.test',last_seen:null,app_version:null,active_days:0,opens:0,threads:0,home_requests:0,
  tasks_started:0,tasks_completed:0,tasks_failed:0,claude_tasks:0,codex_tasks:0};
const ADMIN={...PERSON,user_id:'p0',email:'admin@example.test',is_admin:true};
const PEOPLE={since:0,days:7,people:[PERSON,ADMIN,QUIET,{...QUIET,user_id:'p3',email:'sam@example.test',status:'pending'}]};

test('People reads plainly: admins aren\'t counted, only what someone did shows, and never-seen says why it might be', ()=>{
  assert.equal(peopleSummary(PEOPLE),'1 of 3 testers used Dervo');
  assert.equal(peopleSummary({...PEOPLE,people:[ADMIN]}),'0 of 0 testers used Dervo');
  assert.equal(doneLine(PERSON),'2 days · 3 opens · 1 thread · 1 Home request · 3 tasks (1 Claude, 2 Codex), 1 completed, 1 failed');
  assert.equal(doneLine({...PERSON,active_days:1,opens:1,threads:0,home_requests:0,tasks_started:0,knowledge:2,feedback_opened:1}),
    '1 day · 1 open · 2 kept in Knowledge · feedback opened 1 time');
  assert.match(seenLine(PERSON),/^Last seen Sep 2[78], \d{1,2}:05 [AP]M on 0\.1\.0-beta\.16\.3$/);
  assert.match(seenLine(QUIET),/^Never seen\. Sharing may be off/);
});

test('Testers, People: an admin sees who used Dervo, then who didn\'t, and asks the host once', async ()=>{
  const calls=host({usage_people:{state:'ready',report:PEOPLE}});
  mount(h(TestersPeople));
  await screen.findByText('1 of 3 testers used Dervo');
  const text=document.body.textContent;
  assert.ok(text.includes('maya@example.test'));
  assert.ok(text.includes('admin@example.test (admin)'));
  assert.ok(text.includes('3 tasks (1 Claude, 2 Codex)'));
  assert.ok(text.includes('2 testers not seen this week'));
  assert.ok(text.includes('sam@example.testWaiting for approval, so nothing can be sent yet.'));
  assert.ok(text.indexOf('maya@example.test')<text.indexOf('jordan@example.test'),'active first');
  assert.deepEqual(calls.map(call=>[call.operation,call.args]),[['usage_people',{reload:false}]]);
});

test('Testers, People: a failed load says so, and Try again asks again', async ()=>{
  const calls=host({usage_people:args=>args.reload?{state:'ready',report:{...PEOPLE,people:[]}}:{state:'error',report:null}});
  mount(h(TestersPeople));
  fireEvent.click(await screen.findByRole('button',{name:'Try again'}));
  await screen.findByText('0 of 0 testers used Dervo');
  assert.deepEqual(calls.map(call=>call.args),[{reload:false},{reload:true}]);
});

test('the copy is in house style: no em dashes, and the disclosure matches the host allowlist', ()=>{
  const source=readFileSync(new URL('./UsageSharing.tsx',import.meta.url),'utf8');
  assert.ok(!source.includes(String.fromCharCode(0x2014)));
  const allowlist=readFileSync(new URL('../../../backend/usage_events.py',import.meta.url),'utf8');
  for(const event of ['screen_viewed','first_run_step','model_picker_opened','account_switched','model_changed','home_request','thread_created',
    'task_started','task_ended','continue_in_other','knowledge_rule_added','lesson_approved','lesson_dismissed','feedback_opened','app_opened','update_installed'])
    assert.ok(allowlist.includes(`'${event}':`),`${event} is on the allowlist the disclosure describes`);
  const block=allowlist.slice(allowlist.indexOf('\nEVENTS = {'),allowlist.indexOf('\n}\n',allowlist.indexOf('\nEVENTS = {')));
  const events=[...block.matchAll(/^    '([a-z_]+)': \{/gm)].map(match=>match[1]);
  assert.equal(events.length,16,'a new event needs a line in the disclosure');
});

test('the model picker says when it opens, and a picker with no listener sends nothing', async ()=>{
  const calls=host({model_status:{state:'catalog',detail:'',models:[]}});
  let opened=0;
  const props={projectId:'p',channelId:'c',profiles:[{id:'cs',provider:'claude',identity:'maya@example.test',category:'personal',method:'subscription'}],usage:[],
    binding:{profile_id:'cs',provider:'claude',model:'claude-opus-5'},project:{profile_id:'cs',model:'claude-opus-5'},effort:'high',onChanged:async()=>{}};
  const view=mount(h(ModelPicker,{...props,onOpen:()=>{opened++;}}));
  const trigger=view.getByTestId('model-picker');
  await act(async()=>{fireEvent.pointerDown(trigger,{button:0,ctrlKey:false});fireEvent.click(trigger);});
  await waitFor(()=>assert.ok(document.querySelector('.dv-picker')));
  assert.equal(opened,1);
  assert.ok(!calls.some(call=>call.operation==='usage_event'),'the picker itself sends nothing; the app decides');
});

test('the app wires it: screen views from what is showing, the picker, the notice, Preferences and the admins\' Usage section', ()=>{
  const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
  for(const wire of [/useScreenView\(usageScreen\(\{firstRun:!!firstRunMode,settingsPage:settings\.page,knowledge:knowledgeOpen,search:searchOpen,catchUp:searchMode==='catchup',\s*releases:releasesOpen,thread:!!current,home:hasSelectedProject\}\),current\?\.id\);/,
    /<ModelPicker key=\{`model:\$\{channelId\}`\} onOpen=\{\(\)=>track\('model_picker_opened'\)\}/,/useUsageNotice\(\(\)=>settings\.go\('preferences'\),firstRunMode==='first'&&!welcomed\);/,
    /\{settingsPage==='preferences'&&<UsageSharingSettings\/>\}/,/\{settingsPage==='testers'&&accountData\?\.membership\?\.is_admin===true&&<TestersUsage\/>\}/,
    /\{settingsPage==='testers'&&accountData\?\.membership\?\.is_admin===true&&<TestersPeople\/>\}/,
    /\{settingsPage==='testers'&&accountData\?\.membership\?\.is_admin===true&&<TestersErrors\/>\}/])
    assert.match(shell,wire);
});

test('a crash reports its built-in name and the screen showing, never its message or stack', async ()=>{
  const calls=host();
  function Screen(){useScreenView('settings.model');return null;}
  mount(h(Screen));
  await waitFor(()=>assert.equal(calls.length,1));
  const error=new TypeError('Cannot read properties of /Users/maya/secret-project/plan.md');
  reportCrash(error);
  await waitFor(()=>assert.equal(calls.length,2));
  assert.deepEqual(calls[1],{operation:'error_event',args:{errorType:'TypeError',screen:'settings.model'}});
  assert.ok(!JSON.stringify(calls).includes('maya'));
  delete window.__TAURI_INTERNALS__;
  assert.doesNotThrow(()=>reportCrash('not an error'));
});

test('the root error boundary reports the crash it catches', ()=>{
  const boundary=readFileSync(new URL('../app/RootErrorBoundary.tsx',import.meta.url),'utf8');
  assert.match(boundary,/componentDidCatch\([^)]*\)[^{]*\{[^}]*reportCrash\(error\);/s);
});

const ERRORS={since:0,days:7,errors:[
  {source:'host',error_type:'UnboundLocalError',file:'conversation_coding.py',function:'ConversationCoding.permission',line:224,operation:'coding_permission',
   provider:null,count:5,users:2,first_seen:1789779600,last_seen:1789786800,app_version:'0.1.0-beta.11'},
  {source:'renderer',error_type:'TypeError',file:null,function:null,line:null,operation:'settings.model',provider:null,count:1,users:1,
   first_seen:1789779600,last_seen:1789779600,app_version:null},
  {source:'task',error_type:'KeyError',file:'claude_coding.py',function:'ClaudeCodingTurn.tick',line:790,operation:'coding_turn',provider:'claude',
   count:1,users:1,first_seen:1789779600,last_seen:1789779600,app_version:'0.1.0-beta.11'}]};

test('an error group reads as its place, what was running and how often', ()=>{
  const [host_,renderer,task]=ERRORS.errors;
  assert.equal(errorPlace(host_),'conversation_coding.py, ConversationCoding.permission(), line 224');
  assert.equal(errorDuring(host_),'During coding_permission');
  assert.equal(errorPlace(renderer),'The app’s crash screen');
  assert.equal(errorDuring(renderer),'On Settings, Account and model');
  assert.equal(errorDuring(task),'Claude task');
  assert.equal(errorDuring({...task,operation:'task_start',provider:'codex'}),'Codex task, starting');
  assert.match(errorCounts(host_),/^5 times · 2 testers · last Sep \d+, \d+:\d\d [AP]M on 0\.1\.0-beta\.11$/);
  assert.match(errorCounts(renderer),/^1 time · 1 tester · last /);
});

test('Testers, Errors: an admin sees each kind of error with its place, and a failed load can be retried', async ()=>{
  let calls=host({error_report:{state:'ready',report:ERRORS}});
  mount(h(TestersErrors));
  await screen.findByText('UnboundLocalError');
  const text=document.body.textContent;
  assert.ok(text.includes('conversation_coding.py, ConversationCoding.permission(), line 224. During coding_permission.'));
  assert.ok(text.includes('The app’s crash screen. On Settings, Account and model.'));
  assert.deepEqual(calls.map(call=>[call.operation,call.args]),[['error_report',{reload:false}]]);
  cleanup();
  calls=host({error_report:args=>args.reload?{state:'ready',report:{since:0,days:7,errors:[]}}:{state:'error',report:null}});
  mount(h(TestersErrors));
  fireEvent.click(await screen.findByRole('button',{name:'Try again'}));
  await screen.findByText(/No errors reported/);
});

test('Testers, Errors: a long list shows the newest kinds first, then all on request', async ()=>{
  const many=Array.from({length:15},(_,n)=>({...ERRORS.errors[0],line:n+1,error_type:`Kind${n}`}));
  host({error_report:{state:'ready',report:{since:0,days:7,errors:many}}});
  mount(h(TestersErrors));
  await screen.findByText('Kind0');
  assert.equal(screen.queryByText('Kind14'),null);
  assert.ok(document.body.textContent.includes('3 older kinds not shown.'));
  fireEvent.click(screen.getByRole('button',{name:'Show all'}));
  await screen.findByText('Kind14');
});
