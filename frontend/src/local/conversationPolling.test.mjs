import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {renderHook,act,cleanup,waitFor}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider,focusManager}=await import('@tanstack/react-query');
const {useConversationReads,ACTIVE_INTERVAL,SHOWN_ACTIVE_INTERVAL,LOST_AFTER,LOST_MESSAGE,REMEMBERED_GRACE,REMEMBERED_MAX,forgetViews,remember,remembered}=await import('./useConversationReads.ts');
const clients=[];
focusManager.setFocused(undefined);
// Each test is a fresh session: what a thread last showed (remembered) isn't carried from one test to the next.
afterEach(()=>{cleanup();forgetViews();for(const client of clients.splice(0))client.clear();delete window.__TAURI_INTERNALS__;Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});});
after(()=>{focusManager.setFocused(undefined);dom.window.close();});
const pause=ms=>act(()=>new Promise(resolve=>setTimeout(resolve,ms)));
// Long enough for three active polls, whatever the active interval is.
const THREE_POLLS=ACTIVE_INTERVAL*3+200;
function mount(handler,initial={projectId:'p',channelId:'c',enabled:true}){
  const calls=[];
  window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};
  const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0,refetchOnWindowFocus:false}}});clients.push(client);
  const hook=renderHook(p=>useConversationReads(p.projectId,p.channelId,p.enabled),{initialProps:initial,wrapper:({children})=>React.createElement(QueryClientProvider,{client},children)});
  return {...hook,calls};
}
const status=(active=false,extra={})=>({state:active?'working':'completed',active,detail:'Recorded fixture status',...extra});

const cursor=n=>n.toString(16).padStart(32,'0');
const snapshot=(active=false,reply='Earlier reply',extra={})=>({activity:status(active),history:[{id:'reply',content:reply}],tasks:[],requests:[],...extra});
const full=(args,data,n=1,active=data.activity.active)=>({base:args.cursor,cursor:cursor(n),active,reset:true,snapshot:data});
function responder(get){let revision=0,last;return (operation,args)=>{
 assert.equal(operation,'conversation_feed');const data=get(),encoded=JSON.stringify(data);
 const active=data.activity.active||!!data.activity.review_active||data.requests.length>0;
 if(!args.force&&args.cursor&&encoded===last)return {base:args.cursor,cursor:args.cursor,active,reset:false,patches:[]};
 last=encoded;return full(args,data,++revision,active);
};}

test('idle is quiet, refresh starts updates, and completion delivers final text and status together',async()=>{
 let active=false,reply='Earlier reply';const view=mount(responder(()=>snapshot(active,reply)));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,reply));
 await pause(1200);assert.equal(view.calls.length,1);
 active=true;await act(()=>view.result.current.refresh());
 const before=view.calls.length;await pause(THREE_POLLS);assert.ok(view.calls.length>=before+3);
 assert.ok(view.calls.slice(before).every(call=>call.args.cursor&&!call.args.force));
 active=false;reply='Final reply';await waitFor(()=>assert.equal(view.result.current.history.data[0].content,reply));
 assert.equal(view.result.current.activity.data.active,false);
 const settled=view.calls.length;await pause(300);assert.equal(view.calls.length,settled);
});
test('helper work and review keep updates running while owner is inactive',async()=>{
 let helpers=[{id:'helper',state:'running'}],reviewing=false;
 const view=mount(responder(()=>snapshot(false,'Answer',{activity:status(false,{review_active:reviewing}),requests:helpers})));
 await waitFor(()=>assert.equal(view.result.current.requests.data?.length,1));
 let count=view.calls.length;await pause(THREE_POLLS);assert.ok(view.calls.length>=count+3);
 helpers=[];reviewing=true;await act(()=>view.result.current.refresh());
 count=view.calls.length;await pause(THREE_POLLS);assert.ok(view.calls.length>=count+3);
});
test('disabled and changed conversations reject previous scope late responses',async()=>{
 let release;const view=mount((_,args)=>args.channelId==='old'?new Promise(resolve=>{release=()=>resolve(full(args,snapshot(false,'Old text')));}):full(args,snapshot(false,'New text')),{projectId:'p',channelId:'old',enabled:false});
 await pause(100);assert.equal(view.calls.length,0);
 view.rerender({projectId:'p',channelId:'old',enabled:true});await waitFor(()=>assert.equal(typeof release,'function'));
 view.rerender({projectId:'new-project',channelId:'new',enabled:true});await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'New text'));
 await act(async()=>release());assert.equal(view.result.current.history.data[0].content,'New text');
 assert.ok(view.calls.filter(call=>call.args.channelId==='new').every(call=>call.args.projectId==='new-project'));
});
test('settled polling discovers work started outside local send action',async()=>{
 let active=false;const view=mount(responder(()=>snapshot(active)));
 await waitFor(()=>assert.equal(view.result.current.activity.data?.active,false));active=true;
 await waitFor(()=>assert.equal(view.result.current.activity.data?.active,true),{timeout:32000});
 const count=view.calls.length;await pause(ACTIVE_INTERVAL+100);assert.ok(view.calls.length>count);
});
test('focus refreshes an idle conversation without repeated file checks',async()=>{
 let active=false;const view=mount(responder(()=>snapshot(active)));
 await waitFor(()=>assert.equal(view.calls.length,1));await pause(1200);assert.equal(view.calls.length,1);
 active=true;await act(async()=>window.dispatchEvent(new Event('focus')));
 await waitFor(()=>assert.equal(view.result.current.activity.data?.active,true));
});
test('hidden windows pause polling but load newly selected conversations',async()=>{
 const view=mount(responder(()=>snapshot(true)));await waitFor(()=>assert.ok(view.result.current.history.data));
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
 await pause(100);const hidden=view.calls.length;await pause(200);assert.equal(view.calls.length,hidden);
 view.rerender({projectId:'other',channelId:'other',enabled:true});await waitFor(()=>assert.equal(view.calls.length,hidden+1));
 await pause(150);assert.equal(view.calls.length,hidden+1,'selection loads once without background polling');
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 await act(async()=>document.dispatchEvent(new Event('visibilitychange')));await waitFor(()=>assert.ok(view.result.current.history.data));
 assert.ok(view.calls.slice(hidden).every(call=>call.args.channelId==='other'));
 view.rerender({projectId:'other',channelId:'other',enabled:false});const disabled=view.calls.length;
 await act(async()=>window.dispatchEvent(new Event('focus')));await pause(100);assert.equal(view.calls.length,disabled);
 view.unmount();await act(async()=>document.dispatchEvent(new Event('visibilitychange')));assert.equal(view.calls.length,disabled);
});
test('late actions cannot refresh a newer conversation or later visit to the same one',async()=>{
 const view=mount(responder(()=>snapshot()));await waitFor(()=>assert.equal(view.calls.length,1));const old=view.result.current.refresh;
 view.rerender({projectId:'other',channelId:'other',enabled:true});await waitFor(()=>assert.equal(view.calls.length,2));
 await act(async()=>assert.equal(await old(),false));assert.equal(view.calls.length,2);
 view.rerender({projectId:'p',channelId:'c',enabled:true});await waitFor(()=>assert.equal(view.calls.length,3));
 await act(async()=>assert.equal(await old(),false));assert.equal(view.calls.length,3);
 await act(async()=>assert.equal(await view.result.current.refresh(),true));assert.equal(view.calls.length,4);
 const refresh=view.result.current.refresh;view.unmount();assert.equal(await refresh(),false);
});
test('broken patch stops reads until explicit retry obtains fresh snapshot',async()=>{
 let recovered=false;const view=mount((_,args)=>!args.cursor?full(args,snapshot(!recovered,recovered?'Recovered':'Partial')):{base:cursor(99),cursor:cursor(2),active:true,reset:false,patches:[]});
 await waitFor(()=>assert.ok(view.result.current.history.error));assert.equal(view.result.current.history.data[0].content,'Partial');
 const failed=view.calls.length;await pause(200);assert.equal(view.calls.length,failed);
 recovered=true;await act(async()=>assert.equal(await view.result.current.refresh(),true));
 assert.equal(view.calls.at(-1).args.cursor,null);assert.equal(view.calls.at(-1).args.force,true);
 await waitFor(()=>assert.equal(view.result.current.history.data[0].content,'Recovered'));assert.equal(view.result.current.history.error,null);
});
test('refresh during pending update stays sequential and carries updated cursor',async()=>{
 let release,inFlight=0,max=0,count=0;
 const view=mount(async(_,args)=>{inFlight++;max=Math.max(max,inFlight);count++;
 if(count===2)await new Promise(resolve=>{release=resolve;});
 inFlight--;return full(args,snapshot(count<3,'Reply '+count),count);});
 await waitFor(()=>assert.equal(typeof release,'function'));let refreshing;
 await act(async()=>{refreshing=view.result.current.refresh();});assert.equal(count,2);
 await act(async()=>{release();assert.equal(await refreshing,true);});
 await waitFor(()=>assert.equal(view.result.current.history.data[0].content,'Reply 3'));
 assert.equal(max,1);assert.equal(view.calls[2].args.cursor,cursor(2));assert.equal(view.calls[2].args.force,true);
});
test('a live run polls about four times a second, not twenty',async()=>{
 // Efficiency review, 23 Sep: 50 ms polls cost 8 to 16% of a core in the host during every watched run.
 const view=mount(responder(()=>snapshot(true)));await waitFor(()=>assert.ok(view.result.current.history.data));
 const count=view.calls.length;await pause(1000);const polls=view.calls.length-count;
 assert.ok(polls>=3,`still updates while the run streams (${polls} polls)`);
 assert.ok(polls<=5,`${polls} polls in a second is the old 50 ms pace`);
});
test('unchanged updates preserve data identity without rendering every poll',async()=>{
 const view=mount(responder(()=>snapshot(true)));await waitFor(()=>assert.ok(view.result.current.history.data));
 const before=view.result.current,count=view.calls.length;await pause(THREE_POLLS);
 assert.ok(view.calls.length>count+2);assert.equal(view.result.current,before);
});

test('refresh during initial read follows it with fresh data instead of waiting for idle poll',async()=>{
 let release,count=0;
 const view=mount(async(_,args)=>{count++;if(count===1){await new Promise(resolve=>{release=resolve;});return full(args,snapshot(false,'Before local action'));}return full(args,snapshot(false,'After local action'),2);});
 await waitFor(()=>assert.equal(typeof release,'function'));let refreshing;
 await act(async()=>{refreshing=view.result.current.refresh();});
 await act(async()=>{release();assert.equal(await refreshing,true);});
 await waitFor(()=>assert.equal(view.result.current.history.data[0].content,'After local action'));
 assert.equal(count,2);assert.equal(view.calls[1].args.force,true);assert.equal(view.calls[1].args.cursor,cursor(1));
});

test('hiding during an explicit refresh still finishes that bounded request',async()=>{
 let release,count=0;const view=mount(async(_,args)=>{count++;if(count===1)await new Promise(resolve=>{release=resolve;});return full(args,snapshot(false,'Reply '+count),count);});
 await waitFor(()=>assert.equal(typeof release,'function'));let refreshing;
 await act(async()=>{refreshing=view.result.current.refresh();});
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
 await act(async()=>{release();assert.equal(await refreshing,true);});
 await pause(100);assert.equal(count,2);
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Reply 3'));
});

test('refresh stays stable for stream patches but changes with conversation ownership',async()=>{
 let text='first';let version=1;
 const view=mount((_,args)=>full(args,snapshot(true,text),version++));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'first'));
 const firstRefresh=view.result.current.refresh;
 text='second';
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'second'));
 assert.equal(view.result.current.refresh,firstRefresh);
 view.rerender({projectId:'other',channelId:'other',enabled:true});
 await waitFor(()=>assert.ok(view.calls.some(call=>call.args.projectId==='other')));
 assert.notEqual(view.result.current.refresh,firstRefresh);
 await act(async()=>assert.equal(await firstRefresh(),false));
});


test('re-enabling the same conversation keeps its last snapshot until the fresh read lands, another conversation starts empty',async()=>{
 // #11: the guide reads tasks from this hook; a new activation of the same thread used to start with no data for a frame.
 let release;const tasks=[{id:'t1',state:'working'}];
 const view=mount((_,args)=>args.channelId==='c'?full(args,snapshot(true,'Reply',{tasks})):new Promise(resolve=>{release=()=>resolve(full(args,snapshot(false,'Other text')));}));
 await waitFor(()=>assert.equal(view.result.current.tasks.data?.[0]?.id,'t1'));
 view.rerender({projectId:'p',channelId:'c',enabled:false});
 assert.equal(view.result.current.tasks.data,undefined,'a disabled conversation shows nothing');
 view.rerender({projectId:'p',channelId:'c',enabled:true});
 assert.equal(view.result.current.tasks.data?.[0]?.id,'t1','the same conversation keeps its tasks while the fresh read is pending');
 assert.equal(view.result.current.tasks.isPlaceholderData,true);
 await waitFor(()=>assert.equal(view.result.current.tasks.isPlaceholderData,false));
 assert.equal(view.result.current.tasks.data?.[0]?.id,'t1');
 // Negative control: another conversation never shows this one's tasks, before or after its own read.
 view.rerender({projectId:'p',channelId:'other',enabled:true});
 assert.equal(view.result.current.tasks.data,undefined,'a changed conversation starts empty');
 await waitFor(()=>assert.equal(typeof release,'function'));
 assert.equal(view.result.current.tasks.data,undefined);
 await act(async()=>release());
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Other text'));
 assert.deepEqual(view.result.current.tasks.data,[]);
});

test('initial hidden selection and explicit refresh load history, then focus starts polling despite stale DOM visibility',async()=>{
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 assert.equal(focusManager.isFocused(),false,'use the production DOM focus manager');
 let text='Saved reply';
 const view=mount(responder(()=>snapshot(true,text)));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Saved reply'));
 await pause(180);assert.equal(view.calls.length,1);
 text='Explicitly refreshed';
 await act(async()=>assert.equal(await view.result.current.refresh(),true));
 await waitFor(()=>assert.equal(view.result.current.history.data[0].content,text));
 await pause(180);assert.equal(view.calls.length,2);
 await act(async()=>window.dispatchEvent(new Event('focus')));
 const focused=view.calls.length;await pause(THREE_POLLS);assert.ok(view.calls.length>=focused+3);
 await act(async()=>window.dispatchEvent(new Event('blur')));
 await pause(100);const hidden=view.calls.length;await pause(180);assert.equal(view.calls.length,hidden);
});

// 16.4 item 1: paging, and a size refusal that no longer stops the thread.
test('a size refusal keeps the last snapshot, reports no error and keeps reading',async()=>{
 let refuse=false,active=true;
 const view=mount((_,args)=>{if(refuse)throw new Error('This conversation is too large to update at once. Its saved work is retained.');return full(args,snapshot(active,'Last good'),Math.floor(Math.random()*1e9)+1);});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Last good'));
 refuse=true;const before=view.calls.length;
 await pause(THREE_POLLS);
 assert.ok(view.calls.length>=before+2,`kept reading after the refusal (${view.calls.length-before} reads)`);
 assert.equal(view.result.current.activity.error,null,'never "Status unavailable" for size');
 assert.equal(view.result.current.history.error,null);
 assert.equal(view.result.current.history.data?.[0].content,'Last good');
 refuse=false;active=false;
 await waitFor(()=>assert.equal(view.result.current.activity.data?.active,false));
});
test('other read failures still stop reads until a retry',async()=>{
 let fail=false;const view=mount((_,args)=>{if(fail)throw new Error('Conversation belongs to another project');return full(args,snapshot(true),1);});
 await waitFor(()=>assert.ok(view.result.current.history.data));
 fail=true;await waitFor(()=>assert.ok(view.result.current.history.error),{timeout:2000});
 const stopped=view.calls.length;await pause(THREE_POLLS);assert.equal(view.calls.length,stopped);
});
test('scrolling back loads older pages once each, in order, and a changed conversation drops a late page',async()=>{
 const rows=n=>({id:`m${n}`,content:`Message ${n}`,created_at:n,state:'completed'});
 const newest={activity:status(false),history:[rows(30),rows(31)],tasks:[],requests:[],page:{before:30,has_older:true}};
 const pages={30:{history:[rows(20),rows(21)],tasks:[{id:'t22',state:'completed',messages:[{role:'user',text:'Ask',created_at:22}]}],before:20,has_older:true},
  20:{history:[rows(10)],tasks:[],before:10,has_older:false}};
 let hold;
 const view=mount((operation,args)=>{
  if(operation==='conversation_older'){assert.deepEqual(Object.keys(args).sort(),['before','channelId','projectId']);
   if(hold)return new Promise(resolve=>{hold=()=>resolve(pages[args.before]);});return pages[args.before];}
  return full(args,newest);
 });
 await waitFor(()=>assert.equal(view.result.current.older.hasOlder,true));
 await act(async()=>{await Promise.all([view.result.current.older.fetchOlder(),view.result.current.older.fetchOlder()]);});
 assert.equal(view.calls.filter(call=>call.operation==='conversation_older').length,1,'one page at a time');
 assert.deepEqual(view.result.current.history.data.map(row=>row.id),['m20','m21','m30','m31']);
 assert.deepEqual(view.result.current.tasks.data.map(task=>task.id),['t22']);
 await act(async()=>{await view.result.current.older.fetchOlder();});
 assert.deepEqual(view.result.current.history.data.map(row=>row.id),['m10','m20','m21','m30','m31']);
 assert.equal(view.result.current.older.hasOlder,false);
 await act(async()=>{await view.result.current.older.fetchOlder();});
 assert.equal(view.calls.filter(call=>call.operation==='conversation_older').length,2,'nothing older left to ask for');
 // A page that lands after you've moved to another conversation isn't shown there.
 view.rerender({projectId:'p',channelId:'other',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.older.hasOlder,true));
 hold=true;let pending;
 await act(async()=>{pending=view.result.current.older.fetchOlder();});
 view.rerender({projectId:'p',channelId:'third',enabled:true});
 await waitFor(()=>assert.ok(view.calls.some(call=>call.args.channelId==='third')));
 await act(async()=>{hold();await pending;});
 assert.deepEqual(view.result.current.history.data.map(row=>row.id),['m30','m31']);
});
test('unchanged reads keep the same rows while older pages are loaded',async()=>{
 const newest={activity:status(true),history:[{id:'m30',content:'Newest',created_at:30,state:'completed'}],tasks:[],requests:[],page:{before:30,has_older:true}};
 const feed=responder(()=>newest);
 const view=mount((operation,args)=>operation==='conversation_older'?{history:[{id:'m1',content:'Old',created_at:1,state:'completed'}],tasks:[],before:1,has_older:false}:feed(operation,args));
 await waitFor(()=>assert.equal(view.result.current.older.hasOlder,true));
 await act(async()=>{await view.result.current.older.fetchOlder();});
 const rows=view.result.current.history.data,before=view.result.current,count=view.calls.length;
 assert.deepEqual(rows.map(row=>row.id),['m1','m30']);
 await pause(THREE_POLLS);
 assert.ok(view.calls.length>count+2);
 assert.equal(view.result.current,before,'no re-render for reads that changed nothing');
 assert.equal(view.result.current.history.data,rows);
});

// dd #24: clicking another app stopped reads, so a run on screen looked like it had stopped.
test('a run still on screen keeps reading, more slowly, while another app is in front',async()=>{
 let step='Reading notes';const view=mount(responder(()=>snapshot(true,step)));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,step));
 await act(async()=>window.dispatchEvent(new Event('blur')));
 const blurred=view.calls.length;await pause(SHOWN_ACTIVE_INTERVAL*2+300);const polls=view.calls.length-blurred;
 assert.ok(polls>=1&&polls<=3,`${polls} reads in ${SHOWN_ACTIVE_INTERVAL*2+300} ms while blurred`);
 step='Editing notes';await waitFor(()=>assert.equal(view.result.current.history.data[0].content,step),{timeout:SHOWN_ACTIVE_INTERVAL*2});
 assert.equal(view.result.current.activity.error,null);
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
 await pause(100);const hidden=view.calls.length;await pause(SHOWN_ACTIVE_INTERVAL+300);assert.equal(view.calls.length,hidden,'a hidden window doesn\'t read');
 step='Back on screen';Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
 await waitFor(()=>assert.equal(view.result.current.history.data[0].content,step),{timeout:300});
 await act(async()=>window.dispatchEvent(new Event('focus')));
 const focused=view.calls.length;await pause(THREE_POLLS);assert.ok(view.calls.length>=focused+3,'the front window reads at the full pace again');
});
test('a run on screen with no answer from the host reads as unknown, and the next answer clears it',{timeout:40000},async()=>{
 let hang=false,release;
 const answer=responder(()=>snapshot(true,'Reading notes'));
 const view=mount((operation,args)=>hang?new Promise(resolve=>{release=()=>{hang=false;resolve(answer(operation,args));};}):answer(operation,args));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Reading notes'));
 hang=true;
 await pause(LOST_AFTER/2);assert.equal(view.result.current.activity.error,null,'a slow read isn\'t lost yet');
 await waitFor(()=>assert.equal(view.result.current.activity.error?.message,LOST_MESSAGE),{timeout:LOST_AFTER});
 assert.equal(view.result.current.history.isError,true,'every part of the thread says unknown, not the last step');
 assert.equal(view.result.current.history.data?.[0].content,'Reading notes','the last saved record stays readable');
 await act(async()=>release());
 await waitFor(()=>assert.equal(view.result.current.activity.error,null),{timeout:3000});
});
test('a finished run never reads as lost, however long the host is quiet',async()=>{
 const view=mount(responder(()=>snapshot(false,'Done')));
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Done'));
 await pause(2500);assert.equal(view.result.current.activity.error,null);assert.equal(view.calls.length,1);
});

// Polish pass 2 (7 Oct 2026): clicking back to a thread seen earlier this session showed placeholder bars until its read
// landed (a full snapshot, every time), and the host answers one request at a time, so with work ahead of it that was long.
test('a thread seen earlier this session starts from what it last showed, until the first fresh read lands',async()=>{
 let hold=false,release;const tasks=[{id:'t1',state:'idle'}];
 const view=mount((_,args)=>{
  if(args.channelId!=='a')return full(args,snapshot(false,'Thread B'));
  if(!hold)return full(args,snapshot(false,'First look',{tasks}),1);
  return new Promise(resolve=>{release=()=>resolve(full(args,snapshot(false,'Second look',{tasks}),9));});
 },{projectId:'p',channelId:'a',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'First look'));
 view.rerender({projectId:'p',channelId:'b',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Thread B'));
 // Back to a: its read is held, and the screen already has the first look and its tasks.
 hold=true;
 view.rerender({projectId:'p',channelId:'a',enabled:true});
 assert.equal(view.result.current.history.data?.[0].content,'First look','shown at once, before the read lands');
 assert.equal(view.result.current.tasks.data?.[0]?.id,'t1');
 assert.equal(view.result.current.history.isPlaceholderData,true,'and marked as not yet confirmed');
 await waitFor(()=>assert.equal(typeof release,'function'));
 await act(async()=>release());
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Second look'));
 assert.equal(view.result.current.history.isPlaceholderData,false);
 // Negative control: a thread that wasn't opened this session starts empty, as before.
 view.rerender({projectId:'p',channelId:'never-seen',enabled:true});
 assert.equal(view.result.current.history.data,undefined);
});
test('a remembered view gives way to the empty state if the fresh read is slow',{timeout:15000},async()=>{
 let hold=false;
 const view=mount((_,args)=>args.channelId==='a'?(hold?new Promise(()=>{}):full(args,snapshot(false,'Settled reply'),1)):full(args,snapshot(false,'Thread B')),{projectId:'p',channelId:'a',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Settled reply'));
 view.rerender({projectId:'p',channelId:'b',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Thread B'));
 hold=true;
 view.rerender({projectId:'p',channelId:'a',enabled:true});
 assert.equal(view.result.current.history.data?.[0].content,'Settled reply');
 await pause(REMEMBERED_GRACE+300);
 assert.equal(view.result.current.history.data,undefined,'after the grace time the thread reads as not yet loaded again');
});
// Beta.18 review (7 Oct 2026): a view remembered while a run was going showed that run as live again for up to the grace
// time, though it may have ended (AGENTS.md: no fabricated live activity). It starts from nothing instead.
test('a thread whose remembered view had a run going starts empty, so an old run never shows as live',async()=>{
 let hold=false;
 const working=[{id:'t1',state:'working'}];
 const view=mount((_,args)=>{
  if(args.channelId==='b')return full(args,snapshot(false,'Thread B'));
  if(hold)return new Promise(()=>{});
  return args.channelId==='a'?full(args,snapshot(true,'Running reply'),1):full(args,snapshot(false,'Working task',{tasks:working}),2);
 },{projectId:'p',channelId:'a',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Running reply'));
 view.rerender({projectId:'p',channelId:'c',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Working task'));
 view.rerender({projectId:'p',channelId:'b',enabled:true});
 await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Thread B'));
 hold=true;
 view.rerender({projectId:'p',channelId:'a',enabled:true});
 assert.equal(view.result.current.history.data,undefined,'a remembered active view isn’t shown');
 assert.equal(view.result.current.tasks.data,undefined);
 view.rerender({projectId:'p',channelId:'c',enabled:true});
 assert.equal(view.result.current.tasks.data,undefined,'nor one with a working task');
});
test('the remembered views are a short list that forgets the oldest',()=>{
 forgetViews();
 for(let n=0;n<REMEMBERED_MAX+3;n++)remember(`k${n}`,{cursor:String(n),snapshot:{}});
 assert.equal(remembered('k0'),undefined);
 assert.equal(remembered('k2'),undefined);
 assert.equal(remembered('k3')?.cursor,'3');
 assert.equal(remembered(`k${REMEMBERED_MAX+2}`)?.cursor,String(REMEMBERED_MAX+2));
 remember('k3',{cursor:'again',snapshot:{}});
 remember('extra',{cursor:'x',snapshot:{}});
 assert.equal(remembered('k3')?.cursor,'again','opening a thread again moves it to the newest');
 assert.equal(remembered('k4'),undefined,'so the one not opened for longest goes first');
 assert.equal(remembered('k3',Date.now()+11*60000),undefined,'and a view older than ten minutes isn’t used');
});
test('a busy answer keeps the last snapshot, reports no error and keeps reading, in English and Portuguese',async()=>{
 const {useLanguageForTests}=await import('../i18n/index.ts');
 const lines={en:'Dervo is busy with conversation feed, so this wasn’t done. Try again in a moment.',
  'pt-BR':'O Dervo está ocupado com conversation feed, então isto não foi feito. Tente de novo daqui a pouco.'};
 for(const language of ['en','pt-BR']){
  useLanguageForTests(language);
  try{
   let busy=false;
   const view=mount((_,args)=>{if(busy)throw new Error(lines[language]);return full(args,snapshot(true,'Last good'),Math.floor(Math.random()*1e9)+1);});
   await waitFor(()=>assert.equal(view.result.current.history.data?.[0].content,'Last good'));
   busy=true;const before=view.calls.length;
   await pause(THREE_POLLS);
   assert.ok(view.calls.length>=before+2,language+': kept reading after the busy answer');
   assert.equal(view.result.current.activity.error,null,language+': a busy host isn’t "Status unavailable"');
   assert.equal(view.result.current.history.data?.[0].content,'Last good');
   busy=false;
   const resumed=view.calls.length;await pause(THREE_POLLS);assert.ok(view.calls.length>resumed,language+': and goes on reading when it clears');
  }finally{useLanguageForTests('en');cleanup();}
 }
});
test('a send right after a thread opens waits for its first read instead of guessing its mode',async()=>{
 const waits=[];const release=()=>{for(const go of waits.splice(0))go();};
 const view=mount((_,args)=>new Promise(resolve=>{waits.push(()=>resolve(full(args,snapshot(false,'Hi',{activity:status(false,{coding:{state:'granted'}})}))));}));
 await pause(50);assert.equal(view.result.current.activity.data,undefined);
 let got;const waiting=view.result.current.loaded().then(value=>{got=value;});
 await pause(50);assert.equal(got,undefined,'nothing yet: the first read hasn’t landed');
 await act(async()=>{release();await waiting;});
 assert.equal(got?.activity?.coding?.state,'granted');
 await waitFor(()=>assert.equal(view.result.current.activity.data?.coding?.state,'granted'));
 const before=view.calls.length;
 assert.equal((await view.result.current.loaded()).activity.coding.state,'granted','once loaded, it answers at once');
 assert.equal(view.calls.length,before,'without another read');
 assert.equal(before,1,'the send joined the first read');
});
