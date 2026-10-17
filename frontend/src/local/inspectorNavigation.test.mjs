import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {within,render,fireEvent,waitFor,cleanup,act}=await import('@testing-library/react');
const {renderToStaticMarkup}=await import('react-dom/server');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {routeSubmit,packetLabel,roleLabel,decodeSnapshot}=await import('./collaboration.ts');
const {PacketRows,PacketInspector,HelperSource,CollaborationSettings}=await import('./CollaborationUI.tsx');
const {TaskFooter,TaskDialog,helperLabel}=await import('./CodingTasks.tsx');
const {composerMode}=await import('./TurnControls.tsx');
after(()=>dom.window.close());

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element,c=client())=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:c},element)));
const conversations=[{id:'main',name:'Engineering',visibility:'shared',project_id:'p',profile_id:'owner',model:'m'},{id:'review-room',name:'Review room',visibility:'shared',project_id:'p',profile_id:'cx',model:'m'},{id:'work-room',name:'Work room',visibility:'shared',project_id:'p',profile_id:'cl',model:'claude-fable-5-1'},{id:'priv',name:'Notes',visibility:'private',project_id:'p',profile_id:'owner',model:'m'}];
const granted={state:'granted'},available=(...names)=>({state:'automatic',available:names.map(n=>({name:n}))});
const packet=(over={})=>({id:'pk1',owner_task_id:'t1',root_task_id:'t1',parent_packet_id:null,depth:1,agent:'reviewer',role:'reviewer',kind:'review',
 source:{project_id:'p',thread_id:'main',task_id:'t1',run_id:'r',token:'tok',profile_id:'owner',model:'m',provider:'codex'},
 destination:{project_id:'p',thread_id:'review-room',profile_id:'cx',model:'m',provider:'codex',agent:'reviewer'},
 reason:'Review greeting.py against test_sample.py',instructions:'Say if greet() matches the test.',files:[{path:'greeting.py',sha256:'aa'},{path:'test_sample.py',sha256:'bb'}],
 payload_text:'Development task. Use only dervo host tools.\nRole: reviewer\nPurpose: Review greeting.py',payload_sha256:'cc',child_task_id:'c1',status:'returned',refusal:null,
 result:{child_state:'answered',summary:'REVIEW: line 1 is fine.',attempt:null,verified:false,test:null,diff:null,after:null,stale:false,changed_since:[]},late_result:null,error:null,
 created_at:1,queued_at:null,started_at:2,returned_at:3,stale:false,changed_since:[],...over});

const {useInspectorNavigation}=await import('./useInspectorNavigation.ts');
const task={id:'t1',state:'ready_for_review',origin:'conversation',session_id:'s',scope_version:1,files:[],argv:[],binding:{provider:'codex',identity:'synthetic@example.test',model:'m',cwd:'/disposable',visibility:'shared'},messages:[],tokens:[],runs:[{id:'r',state:'completed'}]};
function Journey({taskRecord=task}){
 const [channel,setChannel]=React.useState('main');
 const menu=React.useRef(null);
 const inspect=useInspectorNavigation('p',channel,()=>menu.current);
 const navigate={openChannel:id=>{inspect.navigateAway();setChannel(id);},openTask:(id,taskId,trigger)=>{inspect.openTask(id,taskId,trigger);setChannel(id);},inspectPacket:inspect.openPacket};
 const source=React.createElement(HelperSource,{task:{...task,packet_id:'pk1'},projectId:'p',channelId:channel,conversations,navigate});
 return React.createElement(React.Fragment,null,
  React.createElement('button',{ref:menu},channel+' options'),
  React.createElement(TaskFooter,{task,projectId:'p',channelId:channel,onInspect:trigger=>inspect.openTask(channel,'t1',trigger)}),source,
  inspect.task&&React.createElement(TaskDialog,{key:inspect.task.id,task:taskRecord,projectId:'p',channelId:channel,refresh:async()=>{},open:true,onOpenChange:open=>{if(!open)inspect.closeTask();},activation:inspect.task.activation,onCloseAutoFocus:inspect.task.onCloseAutoFocus,extra:source}),
  inspect.packet&&React.createElement(PacketInspector,{key:inspect.packet.id,packetId:inspect.packet.id,projectId:'p',channelId:channel,conversations,navigate,onOpenChange:open=>{if(!open)inspect.closePacket();},onCloseAutoFocus:inspect.packet.onCloseAutoFocus})
 );
}
const setupHost=()=>host(op=>{if(op==='collab_packet')return packet();if(op==='task_detail')return {task,evidence:{attempts:[]}};throw Error(op);});
const escape=()=>fireEvent.keyDown(document.activeElement,{key:'Escape',code:'Escape'});
const settles=async()=>{await act(async()=>{await new Promise(resolve=>setTimeout(resolve,40));});};

test('pointer-opened task and packet inspectors return to their actual buttons on Escape and Close',async()=>{
 setupHost();const view=mount(React.createElement(Journey));
 try{
  const taskButton=await view.findByRole('button',{name:'Inspect'});
  assert.equal(document.activeElement===taskButton,false);
  fireEvent.click(taskButton);
  await waitFor(()=>assert.equal(document.activeElement?.textContent,'Task result'));
  escape();await settles();assert.equal(document.activeElement===taskButton,true);
  const packetButton=view.getByRole('button',{name:'Request details'});
  fireEvent.click(packetButton);await view.findByTestId('packet-result');
  escape();await settles();assert.equal(document.activeElement===packetButton,true);
  fireEvent.click(packetButton);await view.findByTestId('packet-result');
  fireEvent.click(view.getByRole('button',{name:'Close'}));await settles();assert.equal(document.activeElement===packetButton,true);
 }finally{cleanup();await settles();}
});

test('a nested packet returns to its task button, then the task returns to the timeline opener',async()=>{
 setupHost();const view=mount(React.createElement(Journey));
 try{
  const taskButton=view.getByRole('button',{name:'Inspect'});fireEvent.click(taskButton);
  // The task record is a sheet beside the thread, so the thread's own copy of the button stays reachable too.
  const packetButton=await within(await view.findByRole('dialog')).findByRole('button',{name:'Request details'});fireEvent.click(packetButton);
  await view.findByTestId('packet-result');escape();await settles();assert.equal(document.activeElement===packetButton,true);
  escape();await settles();assert.equal(document.activeElement===taskButton,true);
 }finally{cleanup();await settles();}
});

test('following a packet into another conversation keeps the new task focus, then returns to destination options',async()=>{
 setupHost();const view=mount(React.createElement(Journey));
 try{
  fireEvent.click(await view.findByRole('button',{name:'Request details'}));await view.findByTestId('packet-result');
  fireEvent.click(view.getByRole('button',{name:'Open helper task'}));
  await settles();assert.equal(document.activeElement?.textContent,'Task result');
  escape();await settles();assert.equal(document.activeElement?.textContent,'review-room options');
 }finally{cleanup();await settles();}
});

test('returning from a nested packet to the same mounted task focuses its heading and preserves correction text',async()=>{
 setupHost();const view=mount(React.createElement(Journey,{taskRecord:{...task,state:'completed',completion:{changed_files:[],test_exit_status:0,review_phase:null}}}));
 try{
  fireEvent.click(view.getByRole('button',{name:'Inspect'}));
  fireEvent.change(await view.findByRole('textbox',{name:'Correction request'}),{target:{value:'Keep my unsent correction.'}});
  fireEvent.click(await within(await view.findByRole('dialog')).findByRole('button',{name:'Request details'}));
  await view.findByTestId('packet-result');fireEvent.click(view.getByRole('button',{name:'Open source task'}));
  await settles();assert.equal(document.activeElement?.textContent,'Task result');
  assert.equal(view.getByRole('textbox',{name:'Correction request'}).value,'Keep my unsent correction.');
  escape();await settles();assert.equal(document.activeElement?.textContent,'main options');
 }finally{cleanup();await settles();}
});

test('opening an exact task across projects keeps its inspector and uses destination scope',async()=>{
 const calls=setupHost();
 function RecoveryJourney(){
  const [place,setPlace]=React.useState({project:'app',channel:'main'}),fallback=React.useRef(null);
  const inspect=useInspectorNavigation(place.project,place.channel,()=>fallback.current);
  return React.createElement(React.Fragment,null,
   React.createElement('button',{ref:fallback},`${place.project} options`),
   React.createElement('button',{onClick:()=>{setPlace({project:'website',channel:'invite-thread'});inspect.openTask('invite-thread','t1',undefined,'website');}},'Open blocker'),
   React.createElement('button',{onClick:()=>setPlace({project:'app',channel:'main'})},'Leave'),
   inspect.task&&React.createElement(TaskDialog,{task,projectId:place.project,channelId:place.channel,refresh:async()=>{},open:true,onOpenChange:open=>{if(!open)inspect.closeTask();},onCloseAutoFocus:inspect.task.onCloseAutoFocus}));
 }
 const view=mount(React.createElement(RecoveryJourney));
 try{
  fireEvent.click(view.getByRole('button',{name:'Open blocker'}));
  await view.findByRole('dialog');await settles();
  assert.deepEqual(calls.find(c=>c.operation==='task_detail').args,{projectId:'website',channelId:'invite-thread',taskId:'t1'});
  escape();await settles();assert.equal(document.activeElement?.textContent,'website options');
  fireEvent.click(view.getByRole('button',{name:'Open blocker'}));await view.findByRole('dialog');
  fireEvent.click(view.getByRole('button',{name:'Leave'}));await settles();assert.equal(view.queryByRole('dialog'),null);
 }finally{cleanup();await settles();}
});

test('an older lost task offers Leave it stopped (Mark as stopped until beta.18) for its own exact identity',async()=>{
 const lost={...task,id:'older-task',state:'disconnected',runs:[{id:'lost-run',state:'uncertain'}]};
 const calls=host(op=>{if(op==='task_detail')return {task:lost,evidence:{attempts:[]}};if(op==='task_settle')return {...lost,state:'blocked'};throw Error(op);});
 const cache=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity,staleTime:60000}}});
 cache.setQueryData(['local-status'],{lostRuns:[{taskId:'older-task'}]});
 const view=mount(React.createElement(TaskDialog,{task:lost,projectId:'p',channelId:'old-thread',refresh:async()=>{},open:true,onOpenChange(){}}),cache);
 try{
  await view.findByText(/Your project files stay as they are/);
  fireEvent.click(view.getByRole('button',{name:'Leave it stopped'}));
  await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='task_settle').args,{projectId:'p',channelId:'old-thread',taskId:'older-task'}));
  assert.equal(calls.filter(c=>c.operation==='task_continue').length,0);
  await waitFor(()=>assert.equal(cache.getQueryState(['local-status']).isInvalidated,true));
  assert.deepEqual(await cache.fetchQuery({queryKey:['local-status'],queryFn:async()=>({lostRuns:[]})}),{lostRuns:[]});
 }finally{cleanup();cache.clear();await settles();}
});
