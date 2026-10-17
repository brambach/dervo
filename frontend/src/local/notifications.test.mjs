import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
const React=await import('react');
const {render,renderHook,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {notificationsToSend,goodMorning,loadPrefs,savePrefs,DEFAULT_PREFS,pushNote,HOME_CHANNEL,rememberHomeJob,loadHomeJobs,droppedConnector}=await import('./notifications.ts');
const {useNotifications}=await import('./useNotifications.ts');
const {NotificationSettings}=await import('./NotificationSettings.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();});

const item=(thread,changed,extra={})=>({thread_id:thread,project_id:'p',name:thread,project:'Dervo',count:1,changed,...extra});
const summary=(finished=[],needs=[],since=100)=>({since,finished,needs});
const away={focused:false,projectId:'p',channelId:'c'};
const prefs={...DEFAULT_PREFS,backgroundOnly:false};

test('only what is new since the last look is sent, never for the conversation on screen or a turned-off kind',()=>{
 const before=summary([item('done',10)],[item('stuck',5)]);
 assert.deepEqual(notificationsToSend(null,before,prefs,away),[],'the first summary of a session is the good-morning note’s job');
 assert.deepEqual(notificationsToSend(before,before,prefs,away),[]);
 const after=summary([item('done',10),item('fresh',20,{count:2})],[item('stuck',5),item('blocked',30)]);
 const notes=notificationsToSend(before,after,prefs,away);
 assert.deepEqual(notes.map(n=>[n.title,n.body,n.projectId,n.channelId]),[
  ['#blocked needs you','A task in Dervo is waiting for your decision.','p','blocked'],
  ['#fresh finished','2 results in Dervo are ready to read.','p','fresh']]);
 assert.equal(new Set(notes.map(n=>n.id)).size,2);
 const again=summary([item('done',40)],[item('stuck',5)]);
 assert.deepEqual(notificationsToSend(before,again,prefs,away).map(n=>n.title),['#done finished'],'a newer change in a listed conversation is news');
 const looking={focused:true,projectId:'p',channelId:'blocked'};
 assert.deepEqual(notificationsToSend(before,after,prefs,looking).map(n=>n.channelId),['fresh'],'nothing for the conversation you are looking at');
 assert.deepEqual(notificationsToSend(before,after,{...prefs,needs:false},away).map(n=>n.channelId),['fresh']);
 assert.deepEqual(notificationsToSend(before,after,{...prefs,finished:false},away).map(n=>n.channelId),['blocked']);
 assert.deepEqual(notificationsToSend(before,after,{...prefs,backgroundOnly:true},{...away,focused:true}),[],'background only stays quiet while Dervo is in front');
 assert.equal(notificationsToSend(before,after,{...prefs,backgroundOnly:true},away).length,2);
 assert.equal(notificationsToSend(before,summary([],[item('x',1,{project:null})]),prefs,away)[0].body,'A task is waiting for your decision.');
});

test('the good-morning note counts what waits and opens what needs you first',()=>{
 assert.equal(goodMorning(summary(),DEFAULT_PREFS),null);
 const note=goodMorning(summary([item('a',1),item('b',2)],[item('stuck',3)]),DEFAULT_PREFS);
 assert.deepEqual([note.title,note.body,note.channelId],['While you were away','2 conversations have results you haven’t read, and 1 needs you.','stuck']);
 assert.equal(goodMorning(summary([item('a',1)],[],null),DEFAULT_PREFS).title,'Waiting in Dervo','a first launch has no last session to talk about');
 assert.equal(goodMorning(summary([item('a',1)],[],null),DEFAULT_PREFS).body,'1 conversation has results you haven’t read.');
 assert.equal(goodMorning(summary([item('a',1)],[item('s',2)]),{...DEFAULT_PREFS,needs:false}).channelId,'a');
 assert.equal(goodMorning(summary([item('a',1)],[]),{...DEFAULT_PREFS,finished:false}),null);
});

test('preferences default to on and background only, survive a reload and ignore a broken saved value',()=>{
 assert.deepEqual(loadPrefs(),{finished:true,needs:true,backgroundOnly:true});
 savePrefs({finished:false,needs:true,backgroundOnly:false});
 assert.deepEqual(loadPrefs(),{finished:false,needs:true,backgroundOnly:false});
 localStorage.setItem('dervo-notifications','{not json');
 assert.deepEqual(loadPrefs(),DEFAULT_PREFS);
});

function tauri(summaries,respond=()=>undefined){
 const calls=[],listeners=[];let callback=0;
 window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener(){}};
 window.__TAURI_INTERNALS__={transformCallback:fn=>{const id=++callback;window[`_${id}`]=fn;listeners.push(fn);return id;},unregisterCallback(){},
  invoke:async(command,args)=>{calls.push({command,args});
   if(command==='local_request'&&args.operation==='session_summary')return summaries.length>1?summaries.shift():summaries[0];
   const answer=respond(command,args);if(answer!==undefined)return answer;
   if(command==='plugin:event|listen')return 1;
   if(command==='plugin:event|unlisten')return null;
   if(command==='notification_post')return true;
   return null;}};
 return {calls,listeners};
}

test('the watcher greets once, posts new waits, keeps the Dock badge at what needs you, and opens a clicked note',async()=>{
 localStorage.setItem('dervo-notifications',JSON.stringify({finished:true,needs:true,backgroundOnly:false}));
 const {calls,listeners}=tauri([summary([item('done',10)],[]),summary([item('done',10)],[item('stuck',20)]),summary([item('done',10)],[item('stuck',20)])]);
 const opened=[];
 const view=renderHook(()=>useNotifications({screen:()=>away,onOpen:target=>opened.push(target),interval:15}));
 await waitFor(()=>assert.ok(calls.filter(c=>c.command==='local_request').length>=3));
 const posts=calls.filter(c=>c.command==='notification_post').map(c=>c.args.title);
 assert.deepEqual(posts,['While you were away','#stuck needs you'],'one greeting and one new wait, nothing repeated');
 assert.deepEqual(calls.filter(c=>c.command==='dock_badge').map(c=>c.args.count).slice(0,2),[0,1]);
 assert.equal(calls.find(c=>c.command==='plugin:event|listen').args.event,'dervo-notification-open');
 listeners.at(-1)({event:'dervo-notification-open',id:1,payload:{projectId:'p',channelId:'stuck'}});
 assert.deepEqual(opened,[{projectId:'p',channelId:'stuck'}]);
 view.unmount();
 const count=calls.filter(c=>c.command==='local_request').length;
 await new Promise(r=>setTimeout(r,60));
 assert.equal(calls.filter(c=>c.command==='local_request').length,count,'polling stops when the shell unmounts');
});

test('notification settings save each toggle, ask macOS only on request, and send a test note',async()=>{
 let permission='not_determined';
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});if(command==='notification_permission'){if(args.request)permission='allowed';return permission;}if(command==='notification_post')return true;return null;}};
 render(React.createElement(NotificationSettings));
 await screen.findByText('macOS hasn’t asked yet');
 assert.deepEqual(calls.map(c=>[c.command,c.args.request]),[['notification_permission',false]],'opening settings never prompts');
 assert.equal(screen.queryByRole('button',{name:'Send a test'}),null,'no test before macOS has been asked');
 fireEvent.click(screen.getByRole('switch',{name:'Only when Dervo is in the background'}));
 assert.deepEqual(loadPrefs(),{finished:true,needs:true,backgroundOnly:false});
 assert.equal(screen.getByRole('switch',{name:'Only when Dervo is in the background'}).getAttribute('aria-checked'),'false');
 fireEvent.click(screen.getByRole('button',{name:'Allow notifications'}));
 await screen.findByText('Allowed in macOS');
 assert.deepEqual(calls.at(-1),{command:'notification_permission',args:{request:true}});
 fireEvent.click(screen.getByRole('button',{name:'Send a test'}));
 assert.match((await screen.findByRole('status')).textContent,/^Sent\./);
 const test=calls.find(c=>c.command==='notification_post').args;
 assert.deepEqual([test.projectId,test.channelId,test.title],['','','Dervo notifications work']);
 cleanup();
 window.__TAURI_INTERNALS__={invoke:async command=>command==='notification_permission'?'unavailable':null};
 render(React.createElement(NotificationSettings));
 await screen.findByText('Not available in this build of Dervo');
 assert.equal(screen.getByRole('button',{name:'Send test'}).disabled,true);
 assert.ok(screen.getByText('Not available in this build of Dervo.'),'the disabled test says why');
});

test('turned off in macOS: the one rust item on Preferences, with Open System Settings, and the test disabled with its reason',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});return command==='notification_permission'?'denied':null;}};
 render(React.createElement(NotificationSettings));
 await screen.findByText('Turned off in macOS');
 assert.equal(document.querySelectorAll('[data-accent]').length,1,'one rust glyph');
 assert.equal(screen.getByRole('button',{name:'Send test'}).disabled,true);
 assert.ok(screen.getByText('Allow notifications in macOS first.'));
 fireEvent.click(screen.getByRole('button',{name:'Open System Settings'}));
 await waitFor(()=>assert.ok(calls.some(c=>c.command==='notification_settings')));
 assert.deepEqual(calls.find(c=>c.command==='notification_settings').args??{},{},'a fixed destination: the renderer names nothing');
 cleanup();
});

test('the shell watches notifications, opens a clicked conversation with its guide, and settings have a Notifications section',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/useNotifications\(\{screen:\(\)=>\(\{focused:document\.visibilityState==='visible'&&document\.hasFocus\(\),projectId,channelId:onScreen\?channelId:''\}\),onOpen:openFromNotification(,onOpenHome:openHomeFromNotification)?\}\);/);
 // A thread in this project opens through openThread, so a put-away one comes back first (behaviour review S1, 25 Sep).
 assert.match(shell,/if\(conversation\)\{if\(target===projectId\)void openThread\(conversation\);else setChannel\(conversation\);pendingGuide\.current=conversation;\}/);
 assert.match(shell,/if\(pendingGuide\.current&&pendingGuide\.current===channelId&&guideTask\)\{pendingGuide\.current='';if\(!guideVisible\)toggleGuide\(\);\}/);
 assert.match(shell,/\{settingsPage==='preferences'&&<><ThemeTiles\/><NotificationSettings\/><KeepAwakeSetting\/><PhoneSettings\/><GlassWindow\/><\/>\}/);
 const native=readFileSync(new URL('../../../src/main.rs',import.meta.url),'utf8');
 assert.match(native,/notifications::notification_permission,notifications::notification_settings,notifications::notification_post,notifications::dock_badge/);
 assert.match(native,/notifications::setup\(app\.handle\(\)\);/);
});

// ---------------------------------------------------------------- beta.11: what the host reports since 19 Sep
const ok={focused:false,projectId:'p',channelId:'c'};
const posts=calls=>calls.filter(c=>c.command==='notification_post').map(c=>c.args);
const settle=()=>new Promise(r=>setTimeout(r,80));

test('a run you stopped yourself never notifies; one that finished on its own still does',()=>{
 const before=summary([],[]),after=summary([item('halted',20)],[]);
 assert.deepEqual(notificationsToSend(before,after,prefs,ok,{activity:{halted:{ended:'stopped'}}}),[],'your own Stop');
 assert.deepEqual(notificationsToSend(before,after,prefs,ok,{activity:{halted:{ended:'completed'}}}).map(n=>n.title),['#halted finished'],'control: it ended by itself');
 assert.deepEqual(notificationsToSend(before,after,prefs,ok,{activity:{other:{ended:'stopped'}}}).map(n=>n.title),['#halted finished'],'control: another thread’s Stop');
});

test('a job Home started says so when it finishes or needs you, and only for work after Home sent it',()=>{
 const before=summary([],[]),after=summary([item('job',100)],[item('ask',100)]);
 const notes=notificationsToSend(before,after,prefs,ok,{fromHome:{job:98,ask:99}});
 assert.deepEqual(notes.map(n=>n.body),['Home started this in Dervo. It’s waiting for your decision.','Home started this in Dervo. A result is ready to read.']);
 assert.deepEqual(notificationsToSend(before,after,prefs,ok,{fromHome:{job:500,ask:500}}).map(n=>n.body),
  ['A task in Dervo is waiting for your decision.','A result in Dervo is ready to read.'],'control: Home sent to that thread only after this work');
 assert.deepEqual(notificationsToSend(before,after,prefs,ok,{}).map(n=>n.body),['A task in Dervo is waiting for your decision.','A result in Dervo is ready to read.'],'control: you started it');
});

test('a run a connector dropped names the connector',()=>{
 const drop={summary:'Couldn’t finish: The Linear connector dropped during this run, so the agent couldn’t finish. Reconnect it in Claude Code and send the request again.'};
 assert.equal(droppedConnector(drop),'Linear');
 const before=summary(),after=summary([],[item('sync',9)]);
 assert.equal(notificationsToSend(before,after,prefs,ok,{activity:{sync:drop}})[0].body,'The Linear connector dropped, so it couldn’t finish.');
 assert.equal(notificationsToSend(before,after,prefs,ok,{activity:{sync:drop},fromHome:{sync:1}})[0].body,'Home started this in Dervo. The Linear connector dropped, so it couldn’t finish.');
 assert.equal(notificationsToSend(before,after,prefs,ok,{activity:{sync:{summary:'Couldn’t finish: tests failed.'}}})[0].body,'A task in Dervo is waiting for your decision.','control: another failure');
});

test('a relaunch that lost runs sends one note for all of them, opening Home',()=>{
 const lost=[{taskId:'t1',channelId:'a',projectId:'p',name:'a'},{taskId:'t2',channelId:'b',projectId:'p',name:'b'}];
 const waiting=summary([item('done',5)],[item('a',3),item('b',4),item('ask',6)]);
 const note=goodMorning(waiting,DEFAULT_PREFS,lost);
 assert.deepEqual([note.title,note.body,note.projectId,note.channelId],['2 runs stopped when Dervo closed',
  'Dervo can’t tell how far they got. Check them on Home. Also, 1 conversation has results you haven’t read, and 1 more needs you.','p',HOME_CHANNEL]);
 assert.ok(note.covers.includes('needs:a:3')&&note.covers.includes('needs:ask:6'),'the lost threads and the rest are all covered');
 const one=goodMorning(summary([],[item('a',3)]),DEFAULT_PREFS,lost.slice(0,1));
 assert.deepEqual([one.title,one.body],['#a stopped when Dervo closed','Dervo can’t tell how far it got. Check it on Home.']);
 assert.equal(goodMorning(waiting,DEFAULT_PREFS,[]).title,'While you were away','control: nothing lost, the usual note');
 assert.equal(goodMorning(waiting,{...DEFAULT_PREFS,needs:false},lost).title,'While you were away','control: needs you turned off');
 assert.ok(note.id.length<=200&&goodMorning(summary(Array.from({length:60},(_,i)=>item(`thread-${i}-${'x'.repeat(20)}`,i))),DEFAULT_PREFS).id.length<=200,'ids fit the native bound');
});

test('a push that fails or can’t confirm while Dervo is behind other windows: one note; never for a push you watched',()=>{
 const running={id:'r1',state:'running',remote:'origin',branch:'main'};
 const failed={...running,state:'failed',detail:'GitHub needs you to sign in.'},unknown={...running,state:'unknown',detail:'Dervo lost track of this push.'};
 const note=pushNote('p',running,failed,DEFAULT_PREFS,false);
 assert.deepEqual([note.title,note.body,note.channelId],['Push to origin/main failed','GitHub needs you to sign in.',HOME_CHANNEL]);
 assert.deepEqual([pushNote('p',running,unknown,DEFAULT_PREFS,false).title,pushNote('p',running,unknown,DEFAULT_PREFS,false).body],
  ['Push to origin/main may not have landed','Dervo lost track of this push. Choose Check GitHub on Home.']);
 assert.ok(pushNote('p',{id:'r0',state:'pushed'},failed,DEFAULT_PREFS,false),'a push that started and ended between two looks');
 assert.equal(pushNote('p',running,failed,DEFAULT_PREFS,true),null,'control: Dervo in front, you saw it');
 assert.equal(pushNote('p',running,{...running,state:'pushed'},DEFAULT_PREFS,false),null,'control: it pushed');
 assert.equal(pushNote('p',failed,failed,DEFAULT_PREFS,false),null,'control: already told');
 assert.equal(pushNote('p',undefined,unknown,DEFAULT_PREFS,false),null,'control: a result from before this launch');
 assert.equal(pushNote('p',running,failed,{...DEFAULT_PREFS,needs:false},false),null,'control: needs you turned off');
});

test('the watcher: one lost-runs note opening Home, the Dock badge is exactly the Needs you list, and nothing twice after a relaunch',async()=>{
 localStorage.setItem('dervo-notifications',JSON.stringify({finished:true,needs:true,backgroundOnly:false}));
 const lostRuns=[{taskId:'t1',channelId:'a',projectId:'p',name:'a'},{taskId:'t2',channelId:'b',projectId:'p',name:'b'}];
 const first=summary([],[item('a',3),item('b',4)]),stopped=summary([item('b',9)],[item('a',3)]);
 const respond=(command,args)=>command==='local_request'&&args.operation==='status'?{lostRuns}:
  command==='local_request'&&args.operation==='conversations'?[{id:'b',activity:{ended:'stopped'}}]:undefined;
 const {calls,listeners}=tauri([first,stopped,stopped],respond);
 const opened=[],homes=[];
 const view=renderHook(()=>useNotifications({screen:()=>away,onOpen:t=>opened.push(t),onOpenHome:p=>homes.push(p),interval:15}));
 await waitFor(()=>assert.ok(calls.filter(c=>c.args?.operation==='session_summary').length>=3));
 assert.deepEqual(posts(calls).map(p=>[p.title,p.channelId]),[['2 runs stopped when Dervo closed',HOME_CHANNEL]],'one note for both runs, and nothing for the Stop you pressed on b');
 assert.deepEqual(calls.filter(c=>c.command==='dock_badge').map(c=>c.args.count).slice(0,2),[2,1],'the badge follows the list: a stopped run leaves it');
 assert.equal(calls.filter(c=>c.command==='notification_permission').length,0,'the watcher never asks macOS');
 listeners.at(-1)({event:'dervo-notification-open',id:1,payload:{projectId:'p',channelId:HOME_CHANNEL}});
 assert.deepEqual([homes,opened],[['p'],[]],'a click on it lands on Home');
 view.unmount();
 const again=tauri([first],respond);
 const relaunch=renderHook(()=>useNotifications({screen:()=>away,onOpen:t=>opened.push(t),interval:15}));
 await waitFor(()=>assert.ok(again.calls.filter(c=>c.args?.operation==='session_summary').length>=2));
 assert.deepEqual(posts(again.calls),[],'a relaunch with the same news says nothing');
 again.listeners.at(-1)({event:'dervo-notification-open',id:2,payload:{projectId:'p',channelId:HOME_CHANNEL}});
 assert.deepEqual(opened,[{projectId:'p',channelId:''}],'a shell without Home opening lands on the project’s Catch up');
 relaunch.unmount();
 localStorage.removeItem('dervo-notified');
 const fresh=tauri([first],respond);
 const control=renderHook(()=>useNotifications({screen:()=>away,onOpen:()=>{},interval:15}));
 await waitFor(()=>assert.equal(posts(fresh.calls).length,1,'control: a Mac that never posted it does'));
 control.unmount();
});

test('the watcher: a Home job’s note says Home once, and a push result reaches you only behind other windows, once',async()=>{
 localStorage.setItem('dervo-notifications',JSON.stringify({finished:true,needs:true,backgroundOnly:false}));
 const now=Math.floor(Date.now()/1000);
 rememberHomeJob('job',now);
 const records=[{id:'r1',state:'running',remote:'origin',branch:'main'},{id:'r1',state:'unknown',remote:'origin',branch:'main',detail:'Dervo lost track of this push.'}];
 const respond=(command,args)=>command==='local_request'&&args.operation==='push_status'?(records.length>1?records.shift():records[0]):
  command==='local_request'&&args.operation==='conversations'?[{id:'job',activity:{ended:'completed'}}]:undefined;
 const {calls}=tauri([summary(),summary([item('job',now+10)]),summary([item('job',now+10)]),summary([item('job',now+20)])],respond);
 const view=renderHook(()=>useNotifications({screen:()=>away,onOpen:()=>{},interval:15}));
 await waitFor(()=>assert.ok(calls.filter(c=>c.args?.operation==='session_summary').length>=5));
 view.unmount();
 assert.deepEqual(posts(calls).map(p=>[p.title,p.body]),[
  ['#job finished','Home started this in Dervo. A result is ready to read.'],
  ['Push to origin/main may not have landed','Dervo lost track of this push. Choose Check GitHub on Home.'],
  ['#job finished','A result in Dervo is ready to read.']],'the push once, and the thread’s later result is plainly yours');
 assert.deepEqual(loadHomeJobs(),{},'told once, forgotten');
 const front=[{id:'r2',state:'running'},{id:'r2',state:'failed',detail:'No connection.'}];
 const watched=tauri([summary()],(command,args)=>command==='local_request'&&args.operation==='push_status'?(front.length>1?front.shift():front[0]):undefined);
 const inFront=renderHook(()=>useNotifications({screen:()=>({...away,focused:true}),onOpen:()=>{},interval:15}));
 await waitFor(()=>assert.ok(watched.calls.filter(c=>c.args?.operation==='push_status').length>=3));
 inFront.unmount();
 assert.deepEqual(posts(watched.calls),[],'control: your own push, with Dervo in front');
 await settle();
});

test('beta.16.1: an agent’s reminder gets one note on its day, from 8 am, that opens its thread, in the agent’s words',async()=>{
 const {reminderNote,dueLine}=await import('./reminderState.ts');
 localStorage.setItem('dervo-notifications',JSON.stringify({finished:true,needs:true,backgroundOnly:false}));
 const reminder={id:'a'.repeat(32),projectId:'p',channelId:'trial',taskId:'t1',thread:'Apple trial',project:'Dervo',asker:'Claude',
  text:'Check whether the trial ended, then decide on the paid plan.',on:'2026-09-26',createdAt:1,notified:false};
 assert.deepEqual(reminderNote(reminder),{id:`reminder:${'a'.repeat(32)}`,title:'Reminder from #Apple trial',
  body:'Claude: “Check whether the trial ended, then decide on the paid plan.”',projectId:'p',channelId:'trial'});
 assert.equal(dueLine('2026-09-26',new Date(2026,8,26,9)),'Today');
 assert.match(dueLine('2026-09-24',new Date(2026,8,26,9)),/^Due 24 Sept?$|^Due Sep 24$/);
 let notified=false;
 const respond=(command,args)=>command!=='local_request'?undefined
  :args.operation==='reminders_due'?{due:[{...reminder,notified}]}
  :args.operation==='reminder_notified'?(notified=true,{id:args.args.reminderId,notified:true}):undefined;
 const {calls,listeners}=tauri([summary()],respond);
 const opened=[];
 const morning=()=>new Date(2026,8,26,8);
 const view=renderHook(()=>useNotifications({screen:()=>away,onOpen:t=>opened.push(t),interval:15,clock:morning}));
 await waitFor(()=>assert.ok(calls.filter(c=>c.args?.operation==='session_summary').length>=8));
 assert.deepEqual(posts(calls).map(p=>[p.title,p.body,p.channelId]),[['Reminder from #Apple trial','Claude: “Check whether the trial ended, then decide on the paid plan.”','trial']],'one note, however many polls');
 assert.equal(calls.filter(c=>c.args?.operation==='reminder_notified').length>=1,true,'the host records that it went out');
 assert.ok(calls.every(c=>!['coding_request','conversation_send','task_question_answer'].includes(c.args?.operation)),'a reminder never sends or starts anything');
 listeners.at(-1)({event:'dervo-notification-open',id:1,payload:{projectId:'p',channelId:'trial'}});
 assert.deepEqual(opened,[{projectId:'p',channelId:'trial'}],'clicking it opens the thread');
 view.unmount();
 // Nothing while Dervo is in front when notifications are for the background only; Home shows it meanwhile.
 localStorage.clear();notified=false;
 const front=tauri([summary()],respond);
 const inFront=renderHook(()=>useNotifications({screen:()=>({focused:true,projectId:'p',channelId:'other'}),onOpen:()=>{},interval:15,clock:morning}));
 await settle();
 assert.deepEqual(posts(front.calls),[]);
 inFront.unmount();
 // Nothing before 8 am on its day, so one set for tomorrow doesn't go off at midnight, and it isn't recorded as sent.
 localStorage.clear();notified=false;
 const early=tauri([summary()],respond);
 const night=renderHook(()=>useNotifications({screen:()=>away,onOpen:()=>{},interval:15,clock:()=>new Date(2026,8,26,7,59)}));
 await waitFor(()=>assert.ok(early.calls.filter(c=>c.args?.operation==='session_summary').length>=8));
 assert.deepEqual(posts(early.calls),[]);
 assert.equal(early.calls.some(c=>['reminders_due','reminder_notified'].includes(c.args?.operation)),false);
 night.unmount();
});

test('beta.18: runs Dervo settled as paused from their own record are said as paused, not lost',async()=>{
 const {goodMorning}=await import('./notifications.ts');
 const summary={since:null,needs:[],finished:[]};
 const prefs={finished:true,needs:true,backgroundOnly:true};
 const paused=goodMorning(summary,prefs,[{taskId:'t',channelId:'c',projectId:'p',name:'Sidebar folding',paused:true}]);
 assert.equal(paused.title,'#Sidebar folding paused when Dervo restarted');
 assert.equal(paused.body,'It was between steps. Continue picks it up.');
 const mixed=goodMorning(summary,prefs,[{taskId:'t',channelId:'c',projectId:'p',name:'Sidebar folding',paused:true},{taskId:'u',channelId:'d',projectId:'p',name:'Windows build'}]);
 assert.equal(mixed.title,'#Windows build stopped when Dervo closed','the one still unconfirmed is the news');
});
