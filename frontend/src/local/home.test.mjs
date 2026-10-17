import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLTextAreaElement','HTMLButtonElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider,focusManager}=await import('@tanstack/react-query');
const {Home,blocks,didLine,partTitle,jobLine,jobState,arrivalEntry,asking,attention,glyphOf,stateWord,openIndex,actionOf,openLine,rowLine,
 sinceClock,heading,compactFor,hostSending,sendJob,spoken,lasted,sections,wantsSignIn,UNSENT_LINE,UNKNOWN_SEND,pushLine,pushQuestion,commitLine,homeImageChannel,composerHint,
 replyLines,filedByChanges,failedArrival,lostRunsLine,ARRIVAL_FAILED,arrivalFailedLine,liveOffers,offerTitle,offerLine,withOutgoing,sendFailure,greeting}=await import('./Home.tsx');
const {requestSummary,plainSummary}=await import('./homeRequests.ts');
const {workingStep,waited,startingLine,receiptLine,failureLine,READ_FOR,STILL_AFTER}=await import('./Presence.tsx');
const {ThreadLive}=await import('./ThreadLive.tsx');
const {ThreadPickup,plainLine,recapRows,recapActivity,workLines,threadSummary,firstSentence,missingSentence,stepSentence,runningFor}=await import('./ThreadPickup.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const NOW=new Date(2026,8,19,9,0).getTime()/1000;
const at=(day,hour,minute=0)=>new Date(2026,8,day,hour,minute).getTime()/1000;

test('times read the way you would say them',()=>{
 assert.match(spoken(at(19,8,52),NOW),/^8:52\sAM$/);
 assert.match(spoken(at(18,23,40),NOW),/^yesterday 11:40\sPM$/);
 assert.match(spoken(at(16,15,2),NOW),/^Wednesday 3:02\sPM$/);
 assert.doesNotMatch(spoken(at(2,15,2),NOW),/PM/,'a week or more ago is a date');
 assert.equal(lasted(NOW-38*60,NOW),'38 min');
 assert.equal(lasted(NOW-3*3600,NOW),'3 h');
 assert.match(sinceClock(at(18,23,20),NOW),/^11:20\sPM$/,'within a day, the clock alone: read in the morning it means last night');
 assert.match(sinceClock(at(17,18,51),NOW),/^Thursday 6:51\sPM$/);
});

test('the chat renders paragraphs and bullet lists and nothing else',()=>{
 assert.deepEqual(blocks('Morning.\n\n- one\n- two\nAfter the list.'),[{kind:'p',text:'Morning.'},{kind:'ul',items:['one','two']},{kind:'p',text:'After the list.'}]);
 assert.deepEqual(blocks('a\nwrapped line'),[{kind:'p',text:'a wrapped line'}]);
 assert.equal(didLine([]),'');
 assert.equal(didLine(['Sidebar polish']),'Checked Sidebar polish');
 assert.equal(didLine(['a','b','c']),'Read 3 threads');
});

// ---------------------------------------------------------------- the pickup's rows, from records
const item=(extra={})=>({id:'c1',name:'Ideas storage',private:false,seen:at(18,22,30),moved:at(19,1,52),kind:'review',status:'finished, needs your review',
 needs:true,asked:'Store ideas per project, with the idea as the group key.',line:'Finished. 2 files changed. Checks passed.',did:'Finished. 2 files changed. Checks passed.',
 now:null,next:'Should an archived idea keep its threads?',next_by:'agent',running_since:null,changed:true,question:true,failed:false,checks:'passed',handoff:null,...extra});
const stopped=item({id:'c2',name:'Connector picker',kind:'stopped',status:'stopped before it finished',question:false,failed:true,checks:null,handoff:'Codex',
 line:'Stopped at Claude’s usage limit. 3 files changed, not committed.',did:'Stopped at Claude’s usage limit. 3 files changed, not committed.',next:'Resume it, or look at what it left.',next_by:'dervo',moved:at(19,2,14)});
const finished=item({id:'c3',name:'Live board',question:false,next:'Review the same-file warning, then commit.',line:'Finished. 1 file changed. Checks passed.',moved:at(19,3,40)});
const running=item({id:'c4',name:'Search index',kind:'running',status:'still running',needs:false,question:false,checks:null,now:'Editing rank.ts',line:'Editing rank.ts.',
 did:'Still working: Editing rank.ts.',next:'Run the ranking tests.',running_since:NOW-22*60,moved:NOW-22*60});
const answered=item({id:'c5',name:'Sidebar polish',kind:'answered',status:'answered',needs:false,question:false,checks:null,line:'Answered: Done.',did:'Answered: Done.',next:null,next_by:null});

test('rust marks what asked you; stopped and finished work keep their own ink and sage glyphs',()=>{
 assert.equal(asking(item()),true,'a question the agent reported as its next step');
 assert.equal(asking(item({kind:'permission',question:false})),true);
 assert.equal(asking(finished),false,'finished work waiting for review is done, not blocked on you');
 assert.equal(asking(stopped),false,'stopped is ink, never the accent');
 assert.deepEqual([item(),stopped,finished,running,answered].map(attention),[true,true,false,false,false]);
 assert.deepEqual([item(),stopped,finished,running,answered].map(glyphOf),['needs','failed','done','running','done']);
 assert.equal(glyphOf(item({...stopped,failed:false})),'stopped','a run you stopped yourself pauses; one that failed crosses');
 assert.equal(glyphOf(item({...finished,checks:'failed'})),'failed','a finished run whose check failed');
 assert.deepEqual(stateWord(item()),{word:'Needs you',tone:null,accent:true});
 assert.deepEqual(stateWord(stopped),{word:'Stopped',tone:null,accent:false});
 assert.deepEqual(stateWord(finished),{word:'Finished',tone:'done',accent:false});
 assert.deepEqual(stateWord(running),{word:'Running',tone:'running',accent:false});
});

test('the pane sorts rows into Needs you, Running and Since you left',()=>{
 const parts=sections([finished,running,stopped,item(),answered]);
 assert.deepEqual(parts.needs.map(row=>row.id),['c1'],'only what asks you');
 assert.deepEqual(parts.running.map(row=>row.id),['c4']);
 assert.deepEqual(parts.since.map(row=>row.id),['c3','c2','c5'],'stopped work is since you left, not needs you; the host’s order holds');
 assert.deepEqual(sections([]),{needs:[],running:[],since:[]});
 assert.deepEqual(sections([item({kind:'permission',question:false})]).needs.length,1,'permission asks you too');
});

test('the first thing blocked on you opens; with nothing blocked, the thread you were in, unless runs are working',()=>{
 assert.equal(openIndex([finished,stopped,item()],null),1,'the first row that asks or stopped, in the order given');
 assert.equal(openIndex([finished,answered],'c5'),1,'nothing blocked and nothing running: the thread you were in the middle of');
 assert.equal(openIndex([finished,answered],'gone'),0,'or the first row when that one isn’t shown');
 assert.equal(openIndex([running,finished],null),-1,'mid-flight: no card, the rows carry Now and Next');
 assert.equal(openIndex([],'c1'),-1);
});

test('each row gets one action at most, and the open card says why it waits',()=>{
 assert.deepEqual(actionOf(item(),true),{label:'Answer',focus:true});
 assert.deepEqual(actionOf(stopped,false),{label:'Continue in Codex',focus:false});
 assert.deepEqual(actionOf(item({...stopped,handoff:null}),false),{label:'Pick it up',focus:false});
 assert.deepEqual(actionOf(finished,false),{label:'Review',focus:false});
 assert.deepEqual(actionOf(finished,true),{label:'Pick it up',focus:false},'open on the card, finished work is picked up');
 assert.equal(actionOf(running,false),null,'running is not something you act on');
 assert.equal(actionOf(answered,false),null);
 assert.equal(openLine(item(),NOW),'Asked you one thing before it carries on.');
 assert.equal(openLine(item({kind:'permission',question:false}),NOW),'Waiting for your permission before it starts.');
 assert.match(openLine(finished,NOW),/^You last opened it yesterday 10:30\sPM\.$/);
 assert.match(openLine(item({...finished,seen:at(19,6,50)}),NOW),/^You last opened it at 6:50\sAM\.$/,'the same day reads with "at"');
 assert.equal(openLine(item({...finished,seen:null}),NOW),'You haven’t opened it yet.');
 assert.equal(rowLine(item()),'“Should an archived idea keep its threads?”','a compact row shows the question itself');
 assert.equal(rowLine(stopped),stopped.line);
});

test('an answered row says Answered once: the word on the right, not again at the start of its line',()=>{
 assert.equal(stateWord(answered).word,'Answered');
 assert.equal(rowLine(item({...answered,line:'Answered: Saved a project memory note.'})),'Saved a project memory note.');
 assert.equal(rowLine(item({...answered,line:'Answered in text.'})),null,'nothing left to say beside the word');
 // Negative controls: other kinds keep their line whole, and a line that only mentions the word keeps it.
 assert.equal(rowLine(finished),finished.line);
 assert.equal(rowLine(item({...stopped,line:'Answered: stays for a stopped row.'})),'Answered: stays for a stopped row.');
 assert.equal(rowLine(item({...answered,line:'Answered questions about the port.'})),'Answered questions about the port.');
});

const counts=(extra={})=>({threads:3,running:0,moved:3,finished:1,stopped:1,asking:1,...extra});
const found=(extra={})=>({since:at(18,23,20),lead:'**Ideas storage** has a question for you.',middle:{id:'c1',name:'Ideas storage',next:null},items:[item(),stopped,finished],more:0,at:NOW,counts:counts(),...extra});

test('a pickup without counts is counted from its rows instead of blanking the window',()=>{
 // 19 Sep: the shell's preview fixture had no counts and the whole window showed "Something went wrong".
 const bare=found({items:[running,finished]});delete bare.counts;
 assert.doesNotThrow(()=>heading(bare,false,NOW));
 assert.equal(heading(bare,false,NOW).title,'Catch up');
 assert.match(heading(bare,false,NOW).caption,/^1 running/);
});

test('the title and caption come from the counts',()=>{
 assert.deepEqual(heading(found(),false,NOW),{title:'Catch up',caption:'Since 11:20 PM · 3 threads moved'});
 assert.deepEqual(heading(found({items:[running,finished],counts:counts({running:2,finished:1,stopped:0,moved:1})}),false,NOW),{title:'Catch up',caption:'2 running · 1 finished'});
 assert.deepEqual(heading(found({items:[running,item()],counts:counts({running:1,moved:1,stopped:0})}),false,NOW),{title:'Catch up',caption:'1 running · 1 finished'},
  'runs working but something asks you: the caption says which, the name never moves');
 assert.deepEqual(heading(found({items:[finished],counts:counts({moved:0,finished:0,stopped:0,asking:0,threads:5})}),false,NOW),{title:'Catch up',caption:'Nothing running · 5 threads'});
 assert.deepEqual(heading(found({items:[],counts:counts({threads:0,moved:0,finished:0,stopped:0,asking:0})}),false,NOW),{title:'Catch up',caption:'No threads yet'});
 // UX2: 44 threads in the sidebar and none written in yet is not "No threads yet".
 assert.deepEqual(heading(found({items:[],counts:counts({threads:0,moved:0,finished:0,stopped:0,asking:0})}),false,NOW,44),{title:'Catch up',caption:'44 threads, none started yet'});
 assert.deepEqual(heading(found({items:[],counts:counts({threads:0,moved:0,finished:0,stopped:0,asking:0})}),false,NOW,1),{title:'Catch up',caption:'1 thread, none started yet'});
 assert.equal(heading(found(),false,NOW,44).caption,'Since 11:20 PM · 3 threads moved','known threads change nothing once the pickup has rows');
 assert.deepEqual(heading(found(),true,NOW),{title:'Catch up',caption:'Since 11:20 PM'},'compact: only when you left');
 assert.equal(compactFor([],false,NOW),false);
 assert.equal(compactFor([],true,NOW),true,'once you write, the conversation gets the room');
 assert.equal(compactFor([{id:'r',kind:'reply',created_at:NOW-120,state:'ready'}],false,NOW),true,'an answer from the last ten minutes keeps it');
 assert.equal(compactFor([{id:'r',kind:'reply',created_at:NOW-22*60,state:'ready'}],false,NOW),false);
 assert.equal(compactFor([{id:'a',kind:'reply',arrival:true,created_at:NOW-60,state:'ready'}],false,NOW),false,'an arrival isn’t you talking');
});

test('Dervo’s arrival summary is its first message, only when it answered after you came back',()=>{
 const since=at(18,23,40);
 const arrival=(extra)=>({id:'a',kind:'reply',arrival:true,created_at:at(19,8,53),state:'ready',text:'Morning. You were in Sidebar polish.',...extra});
 assert.equal(arrivalEntry([arrival()],since)?.id,'a');
 assert.equal(arrivalEntry([arrival({state:'thinking',text:null})],since),null,'while it thinks, nothing shows');
 assert.equal(arrivalEntry([arrival({state:'failed',text:null})],since),null,'when it fails, nothing shows: the records lead stands');
 assert.equal(arrivalEntry([arrival({created_at:at(18,20,0)})],since),null,'a summary from an earlier visit is stale');
 assert.equal(arrivalEntry([arrival(),{id:'b',kind:'reply',arrival:true,created_at:at(19,8,58),state:'thinking'}],since),null,'the newest one decides');
});

// ---------------------------------------------------------------- jobs Dervo started
const startPart=(extra={})=>({index:0,action:'start',text:'Find why the connectors list is slow the first time',title:'Slow connectors list',conversation:null,
 idea:{id:'i2',name:'Performance'},new_idea:true,channel_id:null,state:'pending',detail:null,...extra});
const addPart=(extra={})=>({index:1,action:'add',text:'More room between the groups',title:null,conversation:{id:'c7',name:'Sidebar polish'},idea:null,
 channel_id:'c7',state:'pending',detail:null,...extra});

test('a job row names the thread, its workstream and where it stands',()=>{
 assert.equal(partTitle(startPart()),'Slow connectors list');
 assert.equal(partTitle(addPart({name:'Sidebar v2'})),'Sidebar v2','the thread’s current name wins');
 assert.equal(jobLine(startPart()),'in Performance');
 assert.equal(jobLine(addPart()),'Added to this thread');
 assert.equal(jobLine(addPart({state:'failed',detail:'The thread was deleted.'})),'The thread was deleted.');
 assert.equal(jobLine(addPart({state:'needs_you',detail:'Allow coding first.'})),'Allow coding first.');
 const act=(extra)=>({status:null,running:false,step:null,unread:0,...extra});
 assert.deepEqual(jobState(startPart(),true,null,NOW-5,NOW),{glyph:'running',word:'Starting',tone:'running'},'the one place Starting is right: this screen is sending it now');
 assert.deepEqual(jobState(startPart(),false,null,NOW-5,NOW),{glyph:'idle',word:'Not sent yet',tone:null});
 assert.deepEqual(jobState(startPart({state:'failed'}),false,null,NOW-5,NOW),{glyph:'failed',word:'Not sent',tone:null});
 assert.deepEqual(jobState(startPart({state:'needs_you'}),false,null,NOW-5,NOW),{glyph:'needs',word:'Needs you',tone:null});
 // Direction A: a sent job is a one-line link and the status lives in the pane, so a live thread reads "Started".
 assert.deepEqual(jobState(startPart({state:'sent'}),false,act({running:true,status:'running',since:NOW-22*60}),NOW-30*60,NOW),{glyph:'running',word:'Started',tone:null});
 assert.deepEqual(jobState(addPart({state:'sent'}),false,act({running:true,status:'running',since:NOW-22*60}),NOW-30*60,NOW),{glyph:'running',word:'Added',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent'}),false,act({status:'needs',at:NOW-60}),NOW-30*60,NOW),{glyph:'needs',word:'Needs you',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent'}),false,act({at:NOW-60}),NOW-30*60,NOW),{glyph:'done',word:'Started',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',detail:'queued'}),false,null,NOW-5,NOW),{glyph:'queued',word:'Waits for a slot',tone:null},'queued behind busy slots says so');
 assert.deepEqual(jobState(addPart({state:'sent',detail:'queued'}),false,act({at:NOW-3600}),NOW-5,NOW),{glyph:'queued',word:'Waits for a slot',tone:null},
  'an older result in that thread is not this job finishing');
 // #25: the host's last known state when the sidebar has no activity for the thread; never Starting.
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'archived'}),false,null,NOW-5,NOW),{glyph:'idle',word:'Archived',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'unknown'}),false,null,NOW-5,NOW),{glyph:'stopped',word:'Unknown',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'no_task'}),false,null,NOW-5,NOW),{glyph:'stopped',word:'Unknown',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'stopped'}),false,null,NOW-5,NOW),{glyph:'stopped',word:'Stopped',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'finished'}),false,null,NOW-5,NOW),{glyph:'done',word:'Started',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'running'}),false,null,NOW-5,NOW),{glyph:'running',word:'Started',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent',thread_state:'needs'}),false,null,NOW-5,NOW),{glyph:'needs',word:'Needs you',tone:null});
 assert.deepEqual(jobState(startPart({state:'sent'}),false,null,NOW-5,NOW),{glyph:'stopped',word:'Unknown',tone:null},'no thread_state at all (an older host): Unknown, never Starting');
 assert.equal(jobState(startPart({state:'sent',thread_state:'archived'}),false,act({running:true,status:'running'}),NOW-5,NOW).word,'Started','live activity wins over a stale thread_state');
 assert.equal(jobLine(startPart({state:'sent',thread_state:'unknown',detail:'Dervo lost track of this job. Open the thread to check.'})),'Dervo lost track of this job. Open the thread to check.');
 assert.equal(jobLine(startPart({state:'sent',thread_state:'finished',detail:'Dervo lost track of this job. Open the thread to check.'})),'in Performance','the lost line only when the host says unknown');
 // 16.4, item 7: the host marks a job starting while it sends it, and unknown when Dervo closed mid-send.
 assert.deepEqual(jobState(startPart({state:'starting'}),false,null,NOW-5,NOW),{glyph:'running',word:'Starting',tone:'running'});
 assert.deepEqual(jobState(startPart({state:'unknown',detail:'Dervo closed while this job was starting.'}),false,null,NOW-5,NOW),{glyph:'stopped',word:'Unknown',tone:null});
 assert.equal(jobLine(startPart({state:'unknown',detail:'Dervo closed while this job was starting.'})),'Dervo closed while this job was starting.');
 assert.equal(jobLine(startPart({state:'unknown'})),UNKNOWN_SEND,'an older host that says nothing still gets a line');
 assert.equal(UNSENT_LINE,'Dervo closed before this was sent. Send it now, or leave it.');
});

test('the host sends a reply’s jobs; the page only shows which one it’s on (16.4, item 7)',()=>{
 const reply=(extra={})=>({id:'r1',kind:'reply',state:'ready',created_at:NOW-3600,queued:true,parts:[startPart(),addPart()],...extra});
 assert.equal(hostSending(reply())?.index,0,'however old the reply: the ten-minute rule is gone');
 assert.equal(hostSending(reply({parts:[startPart({state:'sent'}),addPart({state:'starting'})]}))?.index,1,'the one marked starting');
 assert.equal(hostSending(reply({parts:[startPart({state:'sent'}),addPart()]}))?.index,1,'else the next one waiting');
 assert.equal(hostSending(reply({queued:false})),null,'not queued: nobody is sending it, so it waits for your Send');
 assert.equal(hostSending(reply({queued:undefined})),null,'a reply from before 16.4');
 assert.equal(hostSending(reply({state:'thinking'})),null);
 assert.equal(hostSending(reply({parts:[startPart({state:'sent'}),addPart({state:'needs_you'})]})),null,'needs you waits for your click');
});

test('a job starts a named thread, files it, and says when it waits for a slot',async()=>{
 const calls=[];
 const invoke=async(op,args)=>{calls.push([op,args]);if(op==='create_conversation')return {id:'new1'};if(op==='coding_request')return {id:'task1',state:'working'};return {};};
 assert.deepEqual(await sendJob(startPart(),{projectId:'proj',profileId:'p1',clientId:'reply-1'},invoke),{state:'sent',channelId:'new1',detail:null});
 assert.deepEqual(calls.map(([op])=>op),['create_conversation','ideas_assign','coding_request']);
 assert.deepEqual(calls[0][1],{projectId:'proj',name:'Slow connectors list',private:false});
 assert.deepEqual(calls[1][1],{projectId:'proj',profileId:'p1',channelId:'new1',ideaId:'i2'});
 // Home marks its jobs, and one nobody clicked says so: the host never gives it full access (review H1).
 assert.deepEqual(calls[2][1],{clientId:'reply-1-0',text:'Find why the connectors list is slow the first time',projectId:'proj',channelId:'new1',queueIfBusy:true,origin:'home',clicked:false});
 calls.length=0;
 await sendJob(startPart(),{projectId:'proj',profileId:'p1',clientId:'reply-9',clicked:true},invoke);
 assert.equal(calls.at(-1)[1].clicked,true,'Start or Send on the row');
 calls.length=0;
 await sendJob(addPart(),{projectId:'proj',profileId:'p1',clientId:'reply-1'},invoke);
 await sendJob(startPart({title:''}),{projectId:'proj',profileId:'p1',clientId:'reply-2'},invoke);
 assert.deepEqual(calls.map(([op])=>op),['coding_request','create_conversation','ideas_assign','coding_request'],'adding sends the request only');
 assert.deepEqual(calls[1][1],{projectId:'proj',automaticName:true,name:'',private:false},'no title, Dervo names it');
 const refuse=result=>async op=>op==='coding_request'?result:{id:'new1'};
 assert.deepEqual(await sendJob(addPart(),{projectId:'proj',profileId:'p1',clientId:'r'},refuse({id:null,state:'needs_permission',detail:'Allow coding first.'})),
  {state:'needs_you',channelId:'c7',detail:'Allow coding first.'});
 assert.deepEqual(await sendJob(addPart(),{projectId:'proj',profileId:'p1',clientId:'r'},refuse({id:'q1',state:'queued'})),
  {state:'sent',channelId:'c7',detail:'queued'},'every slot busy: the host queued it, and the row will say so');
 const flaky=async(op,args)=>{if(op==='ideas_assign')throw new Error('archived');return invoke(op,args);};
 assert.equal((await sendJob(startPart(),{projectId:'proj',profileId:'p1',clientId:'r'},flaky)).state,'sent','filing failing doesn’t stop the job');
});

// ---------------------------------------------------------------- the screen, against a stand-in host
let replies=0;
let fixtureHasRequest=false;
async function openLatest(){
 fireEvent.click(await screen.findByRole('button',{name:'Recent requests'}));
 await waitFor(()=>assert.ok(document.querySelector('.dv-home-request-row')));
 fireEvent.click(document.querySelector('.dv-home-request-row'));
}

/** One round of the host's own sending, as project_orchestrator.pump does it, on every read of the chat: the job it
 * marked starting is sent, and the next one waiting is marked starting; with none left the reply stops being queued.
 * ``calls.hold`` (reply ids) keeps a reply where it is. Each send is logged as a 'host_sent' call. */
function hostRound(chat,calls){
 return {...chat,entries:chat.entries.map(e=>{
  if(!e.queued||e.state!=='ready'||calls.hold?.has(e.id))return e;
  const parts=(e.parts??[]).map(p=>{
   if(p.state!=='starting')return p;
   calls.push(['host_sent',{entryId:e.id,part:p.index,clicked:!!e.clicked}]);
   return {...p,state:'sent',channel_id:p.channel_id??'new1',sent_at:Date.now()/1000};
  });
  const next=parts.findIndex(p=>(p.state??'pending')==='pending');
  if(next>=0)parts[next]={...parts[next],state:'starting'};
  return {...e,parts,queued:parts.some(p=>(p.state??'pending')==='pending'||p.state==='starting')};
 })};
}
function host({pickup=null,entries=[],arrive=null,answer=null,listFails=false,pickupFails=false,waiting=0,autostart=true,account=null,images=null,push=null,pushRecords=[],status=null,undo=null,itemSet=null,tidied=undefined,offer=null,reminders=null,suggestions=null,moreSuggested=0}={}){
 const calls=[];
 // Reminders and suggestions (9 Oct) keep their own records here, so Remind me tomorrow, Undo, Done and Dismiss change
 // what the next read returns, as the host's do. Without a fixture each op is refused, as an older host refuses it.
 const saved=reminders&&{due:[...(reminders.due??[])],upcoming:[...(reminders.upcoming??[])],done:[]};
 let offered=suggestions&&[...suggestions];
 fixtureHasRequest=entries.some(e=>e.kind==='you');
 entries=entries.map(e=>e.arrival?e:{...e,request_id:e.request_id||'fixture-request'});
 let chat={entries,thinking:false,statuses:{},...(autostart?{}:{autostart:false})};
 const signedIn={state:'signed_in',method:'email_code',detail:'',identity:{issuer:'i',subject:'s',display_name:'Bryce'},operation_id:null,expires_at:null,can_start:false,can_cancel:false,can_poll:false,can_restore:false,can_sign_out:true,ownership:'local_unclaimed',sync_available:false,membership:{status:'approved',allowed:true,expires_at:null,release_channel:'private-beta',is_admin:false},access_valid_until:Date.now()/1000+86400};
 let drafts=images;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command,a=args.args??{};
  calls.push([op,a]);
  if(op==='project_pickup'){if(pickupFails)throw new Error('store busy');return pickup;}
  if(op==='orchestrator_list'){
   if(listFails)throw new Error('no chat');
   // The host queues a ready answer's jobs (16.4, item 7), unless the fixture's answer says otherwise.
   if(chat.thinking&&answer){chat={...chat,thinking:false,entries:chat.entries.map(e=>e.state==='thinking'?{...e,queued:!!answer.parts?.length,...answer,created_at:Date.now()/1000}:e)};}
   chat=hostRound(chat,calls);
   return chat;
  }
  if(op==='orchestrator_arrive'){if(listFails)throw new Error('no chat');if(waiting>0){waiting--;return {...chat,waiting:true};}if(arrive)chat={...chat,thinking:arrive.state==='thinking',entries:[...chat.entries,arrive]};return chat;}
  if(op==='orchestrator_send'){
   // Home remembers started replies for the life of the module, so each fake reply needs its own id.
   const n=++replies;
   chat={...chat,thinking:true,entries:[...chat.entries,{id:'you-'+n,kind:'you',created_at:Date.now()/1000,text:a.text,request_id:a.requestId||('request-'+n),...(a.images?{images:a.images}:{})},
    {id:'reply-'+n,kind:'reply',created_at:Date.now()/1000,request_id:a.requestId||('request-'+n),state:'thinking',arrival:false,parts:[]}]};
   return chat;
  }
  if(op==='orchestrator_result'){
   chat={...chat,entries:chat.entries.map(e=>e.id===a.entryId?{...e,parts:e.parts.map(p=>p.index===a.part?{...p,state:a.state,channel_id:a.channelId,detail:a.detail}:p)}:e)};
   return {part:{}};
  }
  if(op==='ideas_list')return {ideas:[],archived:[],unassigned:[],states:[],limits:{name:60,summary:2000}};
  if(op==='create_conversation')return {id:'new1'};
  if(op==='ideas_assign')return {};
  if(op==='coding_request')return {id:'task-'+a.channelId,state:'working'};
  if(op==='conversation_pickup')return typeof pickup==='function'?pickup():pickup;
  if(op==='ideas_item_set'){if(!itemSet)throw new Error('no fixture ideas_item_set');const result=itemSet(a);if(result instanceof Error)throw result;return result;}
  if(op==='dervo_account_status')return account??signedIn;
  if(op==='image_drafts'){if(drafts===null)throw new Error('Conversation does not belong to this project');return {images:drafts};}
  if(op==='image_get'||op==='image_thumb')return {...(drafts??[]).find(row=>row.id===a.id),dataUrl:'data:image/png;base64,iVBORw0KGgo='};
  if(op==='image_discard'){drafts=(drafts??[]).filter(row=>row.id!==a.id);return {};}
  if(op==='push_preview'){if(!push)throw new Error('Unknown operation push_preview');return typeof push==='function'?push():push;}
  if(op==='push_start')return pushRecords.shift()??{id:'push1',state:'running'};
  if(op==='push_status')return pushRecords.shift()??{id:'push1',state:'running'};
  if(op==='push_recount')return pushRecords.shift()??{id:'push1',state:'unknown',detail:'Still unknown.'};
  if(op==='status'){if(!status)throw new Error('no fixture status');return status;}
  if(op==='conversations_tidied'){if(tidied===undefined)throw new Error('no fixture conversations_tidied');return tidied;}
  if(op==='project_tidy_setting')return {days:14,default:14,choices:[7,14,30]};
  if(op==='orchestrator_offer'){if(!offer)throw new Error('no fixture orchestrator_offer');const result=offer(a,chat);if(result instanceof Error)throw result;chat=result;return chat;}
  if(op==='orchestrator_undo'){if(!undo)throw new Error('no fixture orchestrator_undo');const result=undo(a,chat);if(result instanceof Error)throw result;chat=result;return chat;}
  if(op==='reminders'&&saved)return {due:saved.due,upcoming:saved.upcoming};
  if(op==='reminder_add'&&saved){
   const back=saved.done.find(r=>r.mine&&r.channelId===a.channelId&&r.on===a.on);
   const row={id:back?.id??`${a.channelId}${a.on}`.replace(/[^a-f0-9]/g,'').padEnd(32,'0').slice(0,32),projectId:a.projectId,channelId:a.channelId,taskId:null,
    thread:back?.thread??(typeof pickup==='function'?pickup():pickup)?.items?.find(i=>i.id===a.channelId)?.name??a.channelId,project:null,asker:null,mine:true,text:a.text,on:a.on,createdAt:Date.now()/1000,notified:false};
   saved.done=saved.done.filter(r=>r.id!==row.id);
   const d=new Date(),list=a.on<=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`?'due':'upcoming';
   saved[list]=[...saved[list].filter(r=>r.id!==row.id),row];
   return {...row,added:true};
  }
  if(op==='reminder_done'&&saved){
   saved.done.push(...[...saved.due,...saved.upcoming].filter(r=>r.id===a.reminderId));
   saved.due=saved.due.filter(r=>r.id!==a.reminderId);saved.upcoming=saved.upcoming.filter(r=>r.id!==a.reminderId);return {id:a.reminderId,state:'done'};
  }
  if(op==='home_suggestions'&&offered)return {suggestions:offered,more:moreSuggested};
  if(op==='task_thread_dismiss'&&offered){offered=offered.filter(r=>r.id!==a.proposalId);return {id:a.proposalId,state:'dismissed'};}
  throw new Error('no fixture '+op);
 }};
 calls.saved=saved;
 return calls;
}
const records={since:at(18,23,20),lead:'**Ideas storage** has a question for you. **Connector picker** stopped at Claude’s usage limit. **Live board** finished and passed its checks.',
 middle:{id:'c3',name:'Live board',next:'Review the same-file warning, then commit.'},more:0,at:NOW,counts:counts(),items:[item(),stopped,finished]};
const mount=(props={})=>render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
 React.createElement(Home,{projectId:'proj',profileId:'p1',projectName:'dervo app',conversations:[],onOpen(){},onHandOff(){},onChanged(){},...props})));

test('the Now pane holds where you left off, from records, even when the chat can’t load',async()=>{
 const opened=[];
 // The rendered pane reads the real clock, so "since" is 40 minutes ago, not a fixed date (this failed after midnight, 19 Sep).
 // The stopped row moved 30 minutes ago too: a week after 19 Sep its fixed time showed as a date (26 Sep).
 const recent=Math.floor(Date.now()/1000);
 host({pickup:{...records,since:recent-2400,items:[item(),{...stopped,moved:recent-1800},finished]},listFails:true});
 mount({onOpen:id=>opened.push(id)});
 const pane=await screen.findByRole('region',{name:'Catch up'});
 await within(pane).findByText(/has a question for you/);
 assert.equal(pane.querySelector('.dv-now-lead b').textContent,'Ideas storage','thread names in bold');
 assert.match(pane.querySelector('.dv-now-head .dv-caption').textContent,/^Since \d{1,2}:\d{2}\s[AP]M · 3 threads moved$/);
 // The four buckets (design, 8 Oct): the question, the stopped run and the review all wait on you, question first.
 const waiting=within(pane).getByRole('region',{name:'Waiting on you'});
 assert.equal(within(pane).queryByRole('region',{name:'Working'}),null,'nothing runs, so Working isn’t drawn: an empty bucket read as placeholder text');
 assert.equal(within(pane).queryByRole('region',{name:'Since you left'}),null,'Since you left is gone');
 const rows=pane.querySelectorAll('.dv-row');
 assert.equal(rows.length,3);
 assert.ok([...rows].every(row=>waiting.contains(row)),'the question, then the stopped run and the review, under Waiting on you');
 assert.match(waiting.querySelector('.dv-now-label').textContent,/^Waiting on you 3$/);
 assert.ok(rows[0].classList.contains('dv-open'),'the first thing that asks you is open on the card');
 assert.equal(rows[0].querySelector('.dv-st').dataset.state,'needs');
 assert.equal(rows[0].querySelector('.dv-st').hasAttribute('data-accent'),true,'and carries the one accent');
 assert.match(rows[0].textContent,/Asked you one thing before it carries on\./);
 // 16.2 (#30, summary.html): the card is its sentence, then the question itself, then Answer. No Asked or Next facts.
 assert.equal(rows[0].querySelectorAll('.dv-facts').length,0,'the card keeps to its sentence and the question');
 assert.equal(rows[0].querySelector('.dv-row-question').textContent,'“Should an archived idea keep its threads?”');
 assert.ok(rows[0].querySelector('.dv-row-end .dv-when')?.textContent,'the card keeps its time, as the mock draws it');
 assert.equal(rows[0].querySelector('.dv-row-end .dv-state'),null,'and no state word: the heading and the glyph say Needs you');
 assert.match(rows[1].querySelector('.dv-row-end').textContent,/(?:^|\D)\d{1,2}:\d{2}\s[AP]M$/,'compact rows keep their time (the rendered row reads the real clock, so a day word may lead)');
 assert.equal(pane.querySelectorAll('[data-accent]').length,1,'the one accent is the open card’s glyph');
 assert.equal(pane.querySelectorAll('.dv-state').length,0,'every row here has a heading or a button that says its state');
 assert.ok(!rows[1].classList.contains('dv-open'));
 assert.equal(rows[1].querySelector('.dv-st').dataset.state,'failed');
 // The useful row: a host older than 16.2 sends no result, so the sentence is the status line it had, and the quiet
 // line says what it wants from you. The action is words there now; the panel holds the buttons.
 assert.match(rows[1].textContent,/Connector pickerStopped at Claude’s usage limit\. 3 files changed, not committed\.Continue in Codex(?:\w+ )?\d{1,2}:\d{2}\s[AP]M/);
 assert.equal(rows[1].querySelector('.dv-row-meta').textContent,'Continue in Codex');
 assert.equal(rows[1].querySelectorAll('.dv-facts').length,0,'a compact row has one line, not the facts');
 assert.equal(rows[2].querySelector('.dv-st').dataset.state,'done');
 assert.deepEqual([...rows[2].querySelectorAll('.dv-row-meta span')].map(span=>[span.textContent,span.dataset.tone]),[['Wants your review','ask'],['Checks passed','ok']]);
 assert.deepEqual(within(rows[2]).getAllByRole('button').map(button=>button.textContent),['Live board'],'one button per compact row: the row itself');
 assert.equal(document.querySelectorAll('a, [style*="underline"]').length,0,'no underlined links');
 fireEvent.click(within(rows[0]).getByRole('button',{name:'Open thread'}));
 assert.deepEqual(opened,['c1'],'Open thread still opens the full thread');
 fireEvent.click(within(rows[2]).getByRole('button',{name:'Live board'}));
 assert.ok(await screen.findByRole('region',{name:'Thread: Live board'}),'a row opens its thread in the panel, where Home’s chat was');
 assert.deepEqual(opened,['c1'],'and doesn’t leave Home');
 fireEvent.keyDown(document.body,{key:'Escape'});
 await waitFor(()=>assert.equal(screen.queryByRole('region',{name:'Thread: Live board'}),null));
 await screen.findByText(/Requests couldn’t load\./);
 assert.equal(screen.queryByRole('region',{name:'Ask Dervo'}),null,'catch-up opens without old exchanges');
 assert.equal(screen.getByRole('textbox',{name:/Ask Dervo/}).placeholder,'What would you like to do?');
 assert.equal(document.querySelector('.dv-composer .dv-hint').textContent,'Finds the right thread or starts a new one','a host that doesn’t say what Home runs on gets no model line');
 assert.equal(composerHint(false),'Finds the right thread · starts work when you ask');
 assert.equal(composerHint(true,'Sonnet'),'Finds the right thread or starts a new one · Answers with Sonnet on this project’s Claude account');
 assert.equal(composerHint(true,'Sonnet',true),'Finds the right thread or starts a new one','beside the agent chip it would contradict it (design review S2)');
 assert.equal(document.querySelector('[data-testid="home-push"]'),null,'a host without the push ops shows nothing about git');
 assert.equal(screen.queryByRole('button',{name:'Attach an image'}),null,'a host without an image scope for Home shows no paperclip');
});

test('mid-flight, the pane shows each run’s step under Running; a quiet morning shows what you were in under Since you left',async()=>{
 host({pickup:{...records,lead:'Two runs are working and nothing needs you yet.',middle:null,items:[running,{...running,id:'c9',name:'Live board'},finished],counts:counts({running:2,finished:1,stopped:0,moved:1,asking:0})},listFails:true});
 mount({conversations:[{id:'c4',name:'Search index',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',activity:{status:'running',running:true,step:'Running the check',unread:0,since:Math.floor(Date.now()/1000)-22*60}}]});
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const runs=within(pane).getByRole('region',{name:'Working'});
 assert.match(runs.querySelector('.dv-now-label').textContent,/^Working 2$/);
 const rows=runs.querySelectorAll('.dv-row');
 assert.equal(rows.length,2);
 assert.equal(pane.querySelectorAll('.dv-open').length,0);
 assert.match(rows[0].textContent,/Search indexNow: running the check/,'the sidebar’s fresher step wins over the records’ one');
 assert.doesNotMatch(rows[0].textContent,/Next/,'the pane row is the step only');
 assert.match(rows[0].querySelector('.dv-row-end').textContent,/^\d+ min$/,'status once: the Running heading and the glyph say it, the row keeps the time');
 assert.equal(rows[0].querySelectorAll('button').length,1,'the name only: running is not something you act on');
 assert.match(within(pane).getByRole('region',{name:'Waiting on you'}).querySelector('.dv-row-meta').textContent,/^Wants your review/);
 assert.equal(runs.querySelector('[data-testid="home-awake"]'),null,'a host that isn’t holding the Mac awake says nothing about it');
 cleanup();
 // The host holds the Mac awake while these run (keep_awake.py): one quiet line under Running, with its honest limit.
 host({pickup:{...records,lead:'Two runs are working and nothing needs you yet.',middle:null,items:[running,{...running,id:'c9',name:'Live board'}],counts:counts({running:2,finished:0,stopped:0,moved:1,asking:0}),awake:'Keeping your Mac awake while these run. Closing the lid still puts it to sleep.'},listFails:true});
 mount();
 const awake=await screen.findByTestId('home-awake');
 assert.equal(awake.textContent,'Keeping your Mac awake while these run. Closing the lid still puts it to sleep.');
 assert.ok(within(screen.getByRole('region',{name:'Working'})).getByTestId('home-awake'),'it sits under Working, not as a banner');
 cleanup();
 host({pickup:{...records,lead:'Nothing moved since 6:51 PM. You were in the middle of **Live board**.',items:[finished],counts:counts({moved:0,finished:0,stopped:0,asking:0,threads:5})},listFails:true});
 mount();
 const quiet=await screen.findByRole('region',{name:'Catch up'});
 await within(quiet).findByText(/Nothing moved since/);
 assert.match(quiet.querySelector('.dv-now-head .dv-caption').textContent,/^Nothing running · 5 threads$/);
 assert.equal(quiet.querySelectorAll('.dv-row.dv-open').length,0,'nothing asks you, so nothing is open');
 const row=within(quiet).getByRole('region',{name:'Waiting on you'}).querySelector('.dv-row');
 assert.match(row.textContent,/Live boardFinished\. 1 file changed\. Checks passed\.Wants your review/);
 assert.equal(quiet.querySelectorAll('[data-accent]').length,0,'nothing asks you, so no rust');
});

test('the day Dervo tidies, the catch-up says what it put away once, and See them opens the sidebar’s Archived fold (b16/lifecycle)',async()=>{
 const now=Math.floor(Date.now()/1000);
 host({pickup:{...records,since:now-2400},listFails:true,tidied:{batch:'tidy-1',at:now-600,count:4,threads:[]}});
 document.body.insertAdjacentHTML('afterbegin','<details data-testid="archived-conversations"><summary tabindex="0">Archived</summary><ul></ul></details>');
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const line=await within(pane).findByText('Put away 4 threads you hadn’t touched in two weeks.');
 assert.equal(pane.querySelectorAll('.dv-now-tidied').length,1,'one line');
 assert.ok(pane.querySelector('.dv-now-lead').compareDocumentPosition(line)&4,'under the lead');
 fireEvent.click(within(line.closest('.dv-now-tidied')).getByRole('button',{name:'See them'}));
 const details=document.querySelector('[data-testid="archived-conversations"]');
 assert.equal(details.open,true);
 details.remove();
});

test('no tidy today, or a host without the operation, says nothing about tidying',async()=>{
 for(const tidied of [null,undefined]){
  host({pickup:{...records,since:Math.floor(Date.now()/1000)-2400},listFails:true,tidied});
  const view=mount();
  const pane=await screen.findByRole('region',{name:'Catch up'});
  await within(pane).findByText(/has a question for you/);
  assert.equal(pane.querySelector('.dv-now-tidied'),null);
  assert.doesNotMatch(pane.textContent,/Put away/);
  view.unmount();
 }
});

test('an empty pane says so, and counts threads the pickup left out (UX2)',async()=>{
 const bare={...records,lead:'',items:[],more:0,counts:counts({threads:0,moved:0,finished:0,stopped:0,asking:0})};
 host({pickup:bare,listFails:true});
 mount({conversations:[{id:'t1',name:'One',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'},{id:'t2',name:'Two',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m'}],onCatchUp(){}});
 const pane=await screen.findByRole('region',{name:'Catch up'});
 await within(pane).findByText('Nothing has run in Dervo yet.');
 assert.match(pane.querySelector('.dv-now-head .dv-caption').textContent,/^2 threads, none started yet$/);
 assert.equal(pane.querySelector('.dv-now-quiet .dv-caption').textContent,'Start with something tiny, like “Why is the signup test failing?” Ask below, then carry on. Dervo shows you when it’s done or needs you.','the empty state starts small, not with what to do first');
 assert.equal(pane.querySelectorAll('.dv-row').length,0);
 assert.equal(within(pane).queryByRole('button',{name:'Go through these'}),null,'an empty queue has nothing to go through');
 cleanup();
 host({pickup:bare,listFails:true});
 mount({conversations:[]});
 const none=await screen.findByRole('region',{name:'Catch up'});
 await within(none).findByText('Nothing yet.');
 assert.match(none.querySelector('.dv-now-head .dv-caption').textContent,/^No threads yet$/,'with no threads at all the old caption stands');
});

test('signed out, the composer asks you to sign in and sends nothing (UX7)',async()=>{
 const signedOut={state:'signed_out',method:'email_code',detail:'',identity:null,operation_id:null,expires_at:null,can_start:true,can_cancel:false,can_poll:false,can_restore:false,can_sign_out:false,ownership:'local_unclaimed',sync_available:false,membership:null,access_valid_until:null};
 const calls=host({pickup:records,account:signedOut});
 const asked=[];
 mount({onSignIn:trigger=>asked.push(trigger.textContent)});
 await screen.findByRole('region',{name:'Catch up'});
 const signIn=await screen.findByRole('button',{name:'Sign in'});
 assert.match(document.querySelector('.dv-composer .dv-hint').textContent,/Sign in to ask Dervo/);
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 assert.equal(box.disabled,true);
 assert.equal(box.placeholder,'Sign in to ask Dervo');
 assert.equal(screen.queryByRole('button',{name:'Send'}),null,'no Send while signed out');
 fireEvent.keyDown(box,{key:'Enter'});
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(calls.filter(([op])=>op==='orchestrator_send').length,0,'Enter sends nothing');
 fireEvent.click(signIn);
 assert.deepEqual(asked,['Sign in'],'the button hands the click to the shell');
 assert.ok(document.querySelectorAll('.dv-row').length>=3,'the records still show: they work signed out');
 cleanup();
 host({pickup:records,account:{...signedOut,state:'signed_in',identity:{issuer:'i',subject:'s',display_name:'B'},membership:{status:'pending',allowed:false,expires_at:null,release_channel:'private-beta',is_admin:false}}});
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 assert.equal(screen.queryByRole('button',{name:'Sign in'}),null,'signed in with access pending is not signed out');
 assert.ok(screen.getByRole('button',{name:'Send'}));
 cleanup();
 document.body.insertAdjacentHTML('beforeend','<section aria-label="Dervo account access"><button type="button">Sign in</button></section>');
 host({pickup:records,account:signedOut});
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 await new Promise(resolve=>setTimeout(resolve,400));
 assert.equal(screen.getAllByRole('button',{name:'Sign in'}).length,1,'the shell’s banner already offers Sign in, so Home says it in words only');
 assert.match(document.querySelector('.dv-composer .dv-hint').textContent,/Sign in to ask Dervo/);
 document.querySelector('section[aria-label="Dervo account access"]').remove();
 assert.equal(wantsSignIn({state:'unavailable'}),false,'a local workspace never asks');
 assert.equal(wantsSignIn({state:'signed_out',method:'email_code'}),true);
 assert.equal(wantsSignIn({state:'awaiting_user',method:'email_code'}),false,'a sign-in in progress is not signed out');
});

test('the arrival summary sits with catch-up while the records lead stays visible',async()=>{
 host({pickup:{...records,since:Date.now()/1000-8*3600},arrive:{id:'arr',kind:'reply',arrival:true,created_at:Date.now()/1000,state:'ready',text:'Morning. **Connector picker** is ready for your review.'}});
 mount();
 const region=await screen.findByRole('region',{name:'Catch up'});
 await waitFor(()=>assert.match(region.querySelector('.dv-home-arrival')?.textContent??'',/^Morning\. Connector picker is ready for your review\.$/));
 assert.equal(region.querySelector('.dv-home-arrival b').textContent,'Connector picker');
 assert.match(region.textContent,/has a question for you/,'the records lead stands');
 assert.equal(document.querySelectorAll('.dv-chat[data-live]').length,0,'an arrival is not you talking');
});

test('a summary still being written shows nothing until it lands, even with the window in the background',{timeout:15000},async()=>{
 // 18 Sep, in the preview: Dervo said "reading your threads…" for good. Interval polling pauses while the window isn't
 // focused, and you're likely to look away while Dervo writes.
 focusManager.setFocused(false);
 after(()=>focusManager.setFocused(undefined));
 const calls=host({pickup:{...records,since:Date.now()/1000-8*3600},arrive:{id:'arr',kind:'reply',arrival:true,created_at:Date.now()/1000,state:'thinking',parts:[]},
  answer:{state:'ready',text:'Morning. You were in the middle of Sidebar polish.'}});
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 assert.ok(screen.getByRole('textbox',{name:/Ask Dervo/}));
 assert.equal(document.querySelector('.dv-home-step'),null,'a thinking arrival is silent: the pickup is already on screen');
 await screen.findByText('Morning. You were in the middle of Sidebar polish.',{},{timeout:6000});
 assert.ok(calls.filter(([op])=>op==='orchestrator_list').length>=2,'it asked again while Dervo was writing');
 focusManager.setFocused(undefined);
});

/** A window for the chat: the scroll column is 600 tall from y 0; the newest reply sits at ``top`` and is ``height``
 * tall. JSDOM has no layout, so the rects and sizes are stubbed on the prototypes and taken down by ``done``. */
function layout({top,height}){
 const moved=[];
 const rect=Element.prototype.getBoundingClientRect;
 Object.defineProperty(HTMLElement.prototype,'scrollHeight',{configurable:true,get(){return 900;}});
 Object.defineProperty(HTMLElement.prototype,'clientHeight',{configurable:true,get(){return 600;}});
 Object.defineProperty(HTMLElement.prototype,'scrollTop',{configurable:true,get(){return 0;},set(value){if(this.classList.contains('dv-home-scroll'))moved.push(value);}});
 Element.prototype.getBoundingClientRect=function(){
  if(this.classList.contains('dv-home-scroll'))return {top:0,bottom:600,left:0,right:700,width:700,height:600};
  if(this.classList.contains('dv-msg-dervo')&&this===[...document.querySelectorAll('.dv-msg-dervo')].at(-1))return {top,bottom:top+height,left:0,right:700,width:700,height};
  return {top:0,bottom:0,left:0,right:0,width:0,height:0};
 };
 const done=()=>{delete HTMLElement.prototype.scrollHeight;delete HTMLElement.prototype.clientHeight;delete HTMLElement.prototype.scrollTop;Element.prototype.getBoundingClientRect=rect;};
 return {moved,done};
}

test('the chat opens at its end and follows what you write',{timeout:15000},async()=>{
 // 18 Sep, beta.7 on the real workspace: a long Home chat opened scrolled to its end, with the pickup out of sight.
 const {moved,done}=layout({top:520,height:60});  // the step line near the foot of the window, a short answer in its place
 after(done);
 const earlier=Date.now()/1000-3600;  // the real clock, not the tests' fixed morning: an hour ago is never "just now"
 const said=Array.from({length:6},(_,n)=>[{id:'y'+n,kind:'you',created_at:earlier,text:'question '+n},{id:'r'+n,kind:'reply',created_at:earlier,state:'ready',text:'answer '+n,parts:[]}]).flat();
 host({pickup:records,entries:said,answer:{state:'ready',text:'Here it is.',parts:[]}});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByText('answer 5');
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.ok(moved.includes(900),'the chat opens at its end: the pickup lives in the pane now, so nothing scrolls out of sight');
 assert.equal(document.querySelectorAll('.dv-chat[data-live]').length,0,'an old conversation isn’t live');
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'and the sidebar?'}});
 fireEvent.keyDown(box,{key:'Enter'});
 await waitFor(()=>assert.ok(moved.includes(900),'what you wrote scrolls into view'));
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-chat[data-live]').length,1,'and the chat is the live thing'));
 assert.equal(document.querySelector('.dv-now'),null,'the exchange opens separately from catch-up');
 moved.length=0;
 await screen.findByText('Here it is.',{},{timeout:6000});
 await waitFor(()=>assert.ok(moved.includes(900),'and so does a short answer: you were at the end, and its receipt would otherwise sit below the fold'));
 done();
});
test('opened straight after launch, Home waits for beta access and the summary still arrives',{timeout:15000},async()=>{
 // 18 Sep, beta.7: the summary was asked for before the access check finished, failed, and never showed.
 const calls=host({pickup:{...records,since:Date.now()/1000-8*3600},waiting:1,
  arrive:{id:'arr',kind:'reply',arrival:true,created_at:Date.now()/1000,state:'ready',text:'Morning. Connector picker is ready for your review.'}});
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 await screen.findByText('Morning. Connector picker is ready for your review.',{},{timeout:8000});
 assert.equal(calls.filter(([op])=>op==='orchestrator_arrive').length,2,'asked once, told to wait, asked again');
});

test('a failed pickup read says so and offers to try again',async()=>{
 host({pickupFails:true});
 mount();
 await screen.findByText(/Your threads couldn’t be read just now\./);
 assert.ok(screen.getByRole('button',{name:'Try again'}));
});

test('a message shows Dervo reading, then its answer, then the host sends each job and it shows as a job row',{timeout:15000},async()=>{
 const calls=host({pickup:records,answer:{state:'ready',text:'Started one thread.',looked:['Sidebar polish'],mentions:[],parts:[startPart()]}});
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'the connectors list is slow the first time'}});
 fireEvent.keyDown(box,{key:'Enter'});
 await waitFor(()=>assert.match(document.querySelector('.dv-home-step')?.textContent??'',/^Looking through your project$/),{timeout:6000});
 assert.equal(box.value,'','the composer clears once the message is recorded');
 assert.equal(document.querySelector('.dv-msg-you').textContent,'the connectors list is slow the first time');
 await screen.findByText('Started one thread.',{},{timeout:6000});
 assert.equal(document.querySelector('.dv-byline'),null,'no name row');
 await waitFor(()=>assert.ok(document.querySelector('.dv-jobs .dv-job[data-state="sent"]')),{timeout:6000});
 await waitFor(()=>assert.match(document.querySelector('.dv-home-receipt')?.textContent??'',/^Checked Sidebar polish · \d{1,2}:\d{2}\s[AP]M$/,'what it read and when, once, at the foot, after the job went'));
 const order=calls.map(([op])=>op).filter(op=>['orchestrator_send','create_conversation','ideas_assign','coding_request','orchestrator_result','host_sent'].includes(op));
 assert.deepEqual(order,['orchestrator_send','host_sent'],'the host sends the job (16.4, item 7); the page sends nothing itself');
 const job=document.querySelector('.dv-job');
 assert.equal(job.tagName,'BUTTON','a job row opens its thread');
 assert.match(job.textContent,/^Slow connectors listunknown · in Performance$/,'one line, a link: the action word, then the workstream; status lives in the pane');
 assert.equal(job.querySelector('.dv-st').dataset.state,'stopped','the fixture host sends no thread_state and the sidebar has no activity yet: Unknown, never Starting');
 assert.equal(job.querySelector('.dv-state'),null,'no status word on the right');
 assert.ok(job.querySelector('svg.dv-go'),'a chevron, not an underlined Open');
 assert.equal(document.querySelectorAll('.dv-jobs a').length,0);
});

test('a job nobody is sending waits for your Send, however recent, and nothing starts by itself (16.4, item 7)',{timeout:15000},async()=>{
 // The host sends a reply's jobs while it's queued (the test above). A reply that isn't, from before 16.4 when the app
 // sent jobs and could close mid-send, waits for you: the page never sends a job on its own now.
 const calls=host({pickup:records,answer:{state:'ready',text:'One job.',looked:[],mentions:[],queued:false,parts:[startPart()]}});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'the connectors list is slow the first time'}});
 fireEvent.keyDown(box,{key:'Enter'});
 await screen.findByText('One job.',{},{timeout:6000});
 const start=within(document.querySelector('.dv-jobs')).getByRole('button',{name:'Send'});
 await new Promise(resolve=>setTimeout(resolve,800));  // long enough for a job that would start by itself to have started
 assert.equal(calls.filter(([op])=>op==='coding_request'||op==='create_conversation'||op==='host_sent').length,0,'nothing started by itself');
 assert.equal(document.querySelector('.dv-job small').textContent,UNSENT_LINE);
 fireEvent.click(start);
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='coding_request')),{timeout:6000});
 assert.equal(calls.find(([op])=>op==='coding_request')[1].clicked,true,'your Send is a click');
});
test('a job Dervo lost mid-send says unknown, opens its thread, and Send again uses the same request id (16.4, item 7)',{timeout:15000},async()=>{
 const lost='Dervo closed while this job was starting, and it can’t find it in the thread. Open the thread to check, then send it again or leave it.';
 const calls=host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW-60,text:'more room in the sidebar'},{id:'r',kind:'reply',created_at:NOW-60,state:'ready',text:'Added.',
  parts:[addPart({index:0,state:'unknown',detail:lost})]}]});
 const opened=[];
 mount({onOpen:id=>opened.push(id)});
 if(fixtureHasRequest)await openLatest();
 const again=await screen.findByRole('button',{name:'Send again'});
 const row=again.closest('.dv-job');
 assert.equal(row.dataset.state,'unknown');
 assert.equal(row.querySelector('.dv-st').dataset.state,'stopped');
 assert.match(row.textContent,/unknown · Dervo closed while this job was starting/);
 fireEvent.click(within(row).getByRole('button',{name:/Sidebar polish/}));
 assert.deepEqual(opened,['c7'],'the row opens the thread to check');
 fireEvent.click(again);
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='coding_request')),{timeout:6000});
 const [,request]=calls.find(([op])=>op==='coding_request');
 assert.deepEqual([request.clientId,request.channelId,request.clicked],['r-0','c7',true],'the same request id and thread, so a job that did land isn’t sent twice');
});
test('a job the host queued says it waits for a slot',{timeout:15000},async()=>{
 host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW-60,text:'three jobs'},{id:'r',kind:'reply',created_at:NOW-60,state:'ready',text:'Filed all three in **Connectors**.',
  parts:[startPart({title:'Slow first load',state:'sent',channel_id:'n1',idea:{id:'i9',name:'Connectors'}}),startPart({index:1,title:'Connectors docs',state:'sent',channel_id:'n2',detail:'queued',idea:{id:'i9',name:'Connectors'}})]}]});
 mount({conversations:[{id:'n1',name:'Slow first load',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',activity:{status:'running',running:true,step:'Reading',unread:0,since:Math.floor(Date.now()/1000)-60}}]});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-job').length,2));
 assert.match(document.querySelector('.dv-msg-dervo .dv-read').textContent,/^Filed all three in Connectors\.$/);
 const jobs=document.querySelectorAll('.dv-job');
 // Chat-first report, 23 Sep, #1: a running job says what it's doing and for how long, not "started".
 assert.match(jobs[0].querySelector('.dv-job-open').textContent,/^Slow first loadReading · 1 min · in Connectors$/,'the thread is running, so the line is its live step and clock');
 assert.ok(within(jobs[0]).getByRole('button',{name:'Stop Slow first load'}),'and its Stop sits beside it');
 assert.equal(jobs[0].querySelector('.dv-st').dataset.state,'running');
 assert.match(jobs[1].textContent,/^Connectors docswaits for a slot · in Connectors$/);
 assert.equal(jobs[1].querySelector('.dv-st').dataset.state,'queued');
});
test('a failed answer says why and sends the same words again',{timeout:15000},async()=>{
 const calls=host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW,text:'where are we'},{id:'f',kind:'reply',created_at:NOW,state:'failed',detail:'This project’s Claude account is signed out.',parts:[]}]});
 mount();
 if(fixtureHasRequest)await openLatest();
 const said=await screen.findByRole('alert');
 assert.match(said.textContent,/^This project’s Claude account is signed out\.\s*Send again$/,'the host’s own sentence in the reply’s place, no prefix');
 fireEvent.click(screen.getByRole('button',{name:'Send again'}));
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='orchestrator_send'&&a.text==='where are we')));
});

// ---------------------------------------------------------------- Dervo's presence: the composer never moves, the step is in the stream
const shared=(id,name,visibility='shared')=>({id,name,visibility,project_id:'proj',profile_id:'p1',model:'m',activity:{status:null,running:false,step:null,unread:0}});
const threads=[shared('t1','Sidebar polish'),shared('t2','Connector picker'),shared('t3','my notes','private')];
const thinkingAt=(since,extra={})=>[{id:'y',kind:'you',created_at:since,text:'where are we',...extra},{id:'r',kind:'reply',created_at:since,state:'thinking',parts:[]}];
const composerShape=()=>{
 const box=document.querySelector('.dv-home-composer'),field=box.querySelector('textarea');
 return {placeholder:field.placeholder,rows:field.rows,hint:box.querySelector('.dv-hint').textContent,foot:box.querySelector('.dv-foot').children.length,
  nodes:box.querySelectorAll('*').length,classes:[...box.querySelectorAll('.dv-foot *')].map(node=>node.className).join(' ')};
};

// This one reads Presence.tsx alone, so it can't fail against mvp1's Home.tsx; the rendered tests below do.
test('the step says only what the clock, the images and the job being sent allow',()=>{
 const t=1000;
 assert.deepEqual(workingStep({since:t,now:t+1}),{text:'Looking through your threads',waited:null},'the snapshot reads the threads first; no count, since the snapshot’s own filters and cap aren’t this screen’s');
 assert.deepEqual(workingStep({since:t,now:t+1,hasThreads:false}),{text:'Looking through your project',waited:null});
 assert.deepEqual(workingStep({since:t,now:t+1,images:1}),{text:'Looking at your image',waited:null});
 assert.deepEqual(workingStep({since:t,now:t+1,images:3}),{text:'Looking at your images',waited:null});
 assert.deepEqual(workingStep({since:t,now:t+1,fresh:false}),{text:'Thinking it through',waited:null},'a reply found after a reload was snapshotted before the app came back: never "looking"');
 assert.deepEqual(workingStep({since:t,now:t+1,fresh:false,images:2}),{text:'Thinking it through',waited:null});
 assert.deepEqual(workingStep({since:t,now:t+READ_FOR}),{text:'Thinking it through',waited:null});
 assert.deepEqual(workingStep({since:t,now:t+STILL_AFTER}),{text:'Still thinking',waited:'20 sec'});
 assert.deepEqual(workingStep({since:t,now:t+47}),{text:'Still thinking',waited:'40 sec'},'tens of seconds, so it doesn’t tick');
 assert.deepEqual(workingStep({since:t,now:t+130}),{text:'Still thinking',waited:'2 min'});
 assert.deepEqual(workingStep({since:t+5,now:t}),{text:'Looking through your threads',waited:null},'a clock slightly ahead of the host’s reads as just started');
 assert.deepEqual(workingStep({since:t,now:t+90,starting:'Starting Connectors docs'}),{text:'Starting Connectors docs',waited:null},'the job being sent wins');
 assert.equal(waited(9),'10 sec');assert.equal(waited(59),'50 sec');assert.equal(waited(60),'1 min');assert.equal(waited(125),'2 min');
 assert.equal(startingLine({action:'start',title:'Connectors docs'}),'Starting Connectors docs');
 assert.equal(startingLine({action:'add',conversation:{name:'Live board'}}),'Adding to Live board');
 assert.equal(startingLine({action:'start',name:'Named later',title:'Old title'}),'Starting Named later','the thread’s name as it’s called now');
 assert.equal(startingLine({action:'start'}),'Starting a new thread');
 assert.match(receiptLine(['a','b'],at(19,8,32),NOW),/^Read 2 threads · 8:32\sAM$/);
 assert.match(receiptLine(['Sidebar polish'],at(19,8,32),NOW),/^Checked Sidebar polish · 8:32\sAM$/);
 assert.match(receiptLine([],at(19,8,32),NOW),/^8:32\sAM$/,'nothing read, the time alone');
 assert.equal(failureLine('No answer came back within two minutes. Send it again.',true),'No answer came back within two minutes.','the button says it');
 assert.equal(failureLine('No answer came back within two minutes. Send it again.',false),'No answer came back within two minutes. Send it again.');
 assert.equal(failureLine('',true),'Couldn’t answer just now.');
});

test('the composer doesn’t move while Dervo works: same placeholder, same hint, same parts, Send waiting in its place',async()=>{
 host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW-60,text:'where are we'},{id:'r',kind:'reply',created_at:NOW-60,state:'ready',text:'Quiet.',looked:[],parts:[]}]});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await screen.findByText('Quiet.');
 const idle=composerShape();
 cleanup();
 host({pickup:records,entries:thinkingAt(Math.floor(Date.now()/1000))});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.ok(document.querySelector('.dv-home-step')));
 const working=composerShape();
 assert.deepEqual(working,idle,'nothing in the composer changes: no extra row, no face, no new words');
 assert.equal(working.placeholder,'Follow up on this request…');
 assert.doesNotMatch(working.hint,/answering/,'status is said once, in the chat, never in the foot');
 const send=screen.getByRole('button',{name:'Send'});
 assert.equal(send.disabled,true);
 assert.equal(send.dataset.working,'true','a quiet working state, the same button in the same place');
 assert.equal(screen.queryByRole('button',{name:'Stop'}),null,'the host can’t stop a Home answer, so no Stop is drawn');
 assert.equal(document.querySelector('.dv-msg-dervo .dv-work'),null,'no face in the chat either');
});

const ask=async(words='where are we')=>{
 const box=await screen.findByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:words}});
 fireEvent.keyDown(box,{key:'Enter'});
 await waitFor(()=>assert.ok(document.querySelector('.dv-home-step')));
 return document.querySelector('.dv-home-step');
};
test('the step line sits where the reply will land and moves with the clock: looking through, thinking, still thinking',{timeout:15000},async()=>{
 const now=Math.floor(Date.now()/1000);
 host({pickup:records});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 const step=await ask();
 assert.equal(step.getAttribute('role'),'status');
 assert.equal(step.textContent,'Looking through your threads','a message this screen just sent: the snapshot is being read now; no number, which could only be wrong');
 assert.ok(step.classList.contains('dv-read'),'the reply’s own size, so the answer replaces it without a hop');
 assert.equal(step.closest('.dv-msg-dervo').querySelector('.dv-byline'),null,'no name row');
 assert.equal(document.querySelector('.dv-msg-dervo').firstElementChild,step,'the step is the reply’s first and only line');
 cleanup();
 host({pickup:records,entries:thinkingAt(now+2)});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelector('.dv-home-step')?.textContent,'Thinking it through'),'a thinking reply found on opening (a reload) was read before the app came back: it starts at thinking');
 cleanup();
 host({pickup:records,entries:thinkingAt(now-10)});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelector('.dv-home-step')?.textContent,'Thinking it through'));
 cleanup();
 host({pickup:records,entries:thinkingAt(now-70)});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelector('.dv-home-step')?.textContent,'Still thinking· 1 min'));
 assert.equal(document.querySelector('.dv-home-step-words').textContent,'Still thinking');
 assert.equal(document.querySelector('.dv-home-step-waited').textContent,'· 1 min');
 cleanup();
 host({pickup:records,images:[{id:'img1',name:'Shot.png',state:'ready'}]});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('button',{name:'Attach an image'});
 assert.equal((await ask('what is this')).textContent,'Looking at your image','a message that carried an image');
});
test('the receipt sits at the newest reply’s foot only; older replies keep theirs for hover',async t=>{
 // Both receipts are from today. Only Date is fixed. Polling and UI timers stay real.
 t.mock.timers.enable({apis:['Date'],now:new Date(2026,8,23,12,0)});
 const now=Math.floor(Date.now()/1000);
 host({pickup:records,entries:[
  {id:'y1',kind:'you',created_at:now-600,text:'what moved'},{id:'r1',kind:'reply',created_at:now-600,state:'ready',text:'Two threads finished.',looked:['a','b','c'],parts:[]},
  {id:'y2',kind:'you',created_at:now-60,text:'and the picker?'},{id:'r2',kind:'reply',created_at:now-60,state:'ready',text:'Stopped at the limit.',looked:['Connector picker','Live board'],parts:[]}]});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await screen.findByText('Stopped at the limit.');
 const receipts=[...document.querySelectorAll('.dv-home-receipt')];
 assert.equal(receipts.length,2,'every answered reply has its receipt');
 assert.deepEqual(receipts.map(node=>node.hasAttribute('data-latest')),[false,true],'only the newest shows it without a hover');
 assert.match(receipts[1].textContent,/^Read 2 threads · \d{1,2}:\d{2}\s[AP]M$/);
 assert.match(receipts[0].textContent,/^Read 3 threads · \d{1,2}:\d{2}\s[AP]M$/);
 assert.equal(document.querySelectorAll('.dv-byline').length,0,'no name row anywhere');
 assert.equal(document.querySelectorAll('.dv-msg-dervo').length,2);
 for(const reply of document.querySelectorAll('.dv-msg-dervo'))assert.equal(reply.lastElementChild.className,'dv-home-receipt','the receipt is the reply’s last line');
});
test('with reduced motion the step has no shimmer: the words just change',async()=>{
 const was=window.matchMedia;
 window.matchMedia=query=>({matches:query.includes('reduce'),media:query,addEventListener(){},removeEventListener(){}});
 try{
  host({pickup:records,entries:thinkingAt(Math.floor(Date.now()/1000))});
  mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
  await waitFor(()=>assert.ok(document.querySelector('.dv-home-step')));
  assert.equal(document.querySelector('.dv-home-step').hasAttribute('data-shimmer'),false,'reduced motion: no shimmer');
  assert.equal(document.querySelector('.dv-home-step').textContent,'Thinking it through','the step still says its step');
 }finally{window.matchMedia=was;}
 cleanup();
 host({pickup:records,entries:thinkingAt(Math.floor(Date.now()/1000))});
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.ok(document.querySelector('.dv-home-step')));
 assert.equal(document.querySelector('.dv-home-step').dataset.shimmer,'true','otherwise the soft shimmer is on');
});
test('the shimmer band starts off the text’s left end and finishes off its right, so the loop wraps without a cut (bug #6)',()=>{
 // Read straight from ChatMotion.css: JSDOM runs no animations. The band is the gradient stop pair around --dv-ink,
 // measured in text widths from the image's left edge; background-position p% puts the image at -p·(size-1) widths.
 const css=readFileSync(new URL('./ChatMotion.css',import.meta.url),'utf8');
 const rule=css.slice(css.indexOf('.dv-home-step[data-shimmer] .dv-home-step-words'));
 const size=Number(rule.match(/background-size:\s*(\d+)%/)[1])/100;
 const stops=[...rule.match(/linear-gradient\([^;]*\);/)[0].matchAll(/var\(--dv-presence-(muted|ink)\)\s*(\d+)%/g)].map(m=>[m[1],Number(m[2])/100]);
 const ink=stops.findIndex(([name])=>name==='ink');
 const band=[stops[ink-1][1]*size,stops[ink+1][1]*size];
 const frames=css.slice(css.indexOf('@keyframes dv-home-shimmer'));
 const [from,to]=[...frames.matchAll(/background-position:\s*(-?\d+)%/g)].map(m=>Number(m[1])/100);
 const bandAt=p=>band.map(edge=>edge-p*(size-1));
 assert.ok(bandAt(from)[1]<=0,`at the first frame the band is wholly left of the text (its right edge is at ${bandAt(from)[1]} widths)`);
 assert.ok(bandAt(to)[0]>=1,`at the last frame it is wholly right of the text (its left edge is at ${bandAt(to)[0]} widths)`);
 assert.match(rule.match(/animation:[^;]*/)[0],/dv-home-shimmer .* infinite/,'and it runs until the answer lands');
 assert.ok(css.indexOf('prefers-reduced-motion: no-preference')<css.indexOf('.dv-home-step[data-shimmer]'),'inside the no-preference media block');
});

test('while the host sends a job the reply’s foot says which, and the job row doesn’t say it twice',{timeout:15000},async()=>{
 const calls=host({pickup:records,answer:{state:'ready',text:'Started one thread.',looked:['Sidebar polish'],mentions:[],parts:[startPart({title:'Connectors docs'})]}});
 calls.hold=new Set(['reply-'+(replies+1)]);
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'write the docs page'}});
 fireEvent.keyDown(box,{key:'Enter'});
 await screen.findByText('Started one thread.',{},{timeout:6000});
 await waitFor(()=>assert.equal(document.querySelector('.dv-home-step-foot')?.textContent,'Starting Connectors docs'),{timeout:6000});
 assert.equal(document.querySelector('.dv-home-step-foot').getAttribute('role'),'status');
 assert.equal(document.querySelector('.dv-home-step-foot').hasAttribute('data-shimmer'),false,'a caption at a reply’s foot never shimmers');
 assert.equal(document.querySelector('.dv-home-receipt'),null,'the receipt waits until the job is sent');
 assert.match(document.querySelector('.dv-job').textContent,/^Connectors docsin Performance$/,'no "starting" on the row while the foot says it');
 assert.equal(screen.getByRole('button',{name:'Send'}).dataset.working,'true','Send waits while the job goes');
 calls.hold.clear();
 await waitFor(()=>assert.match(document.querySelector('.dv-home-receipt')?.textContent??'',/^Checked Sidebar polish · /),{timeout:6000});
 assert.equal(document.querySelector('.dv-home-step-foot'),null);
 assert.ok(calls.some(([op])=>op==='host_sent'));
 assert.equal(calls.filter(([op])=>op==='coding_request').length,0,'the page sent nothing itself');
});
test('with three jobs only the row being sent drops its word, and two replies sending at once each name their own job',{timeout:15000},async()=>{
 const now=Math.floor(Date.now()/1000);
 const job=(index,title)=>startPart({index,title,text:`Do ${title}.`});
 const calls=host({pickup:records,entries:[
  {id:'y-a',kind:'you',created_at:now-120,text:'three things'},{id:'r-a',kind:'reply',created_at:now-120,state:'ready',queued:true,text:'Three jobs.',looked:[],parts:[job(0,'Alpha'),job(1,'Beta'),job(2,'Gamma')]},
  {id:'y-b',kind:'you',created_at:now-30,text:'one more'},{id:'r-b',kind:'reply',created_at:now-30,state:'ready',queued:true,text:'One job.',looked:[],parts:[job(0,'Delta')]}]});
 calls.hold=new Set(['r-a','r-b']);
 mount({conversations:threads});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-home-step-foot').length,2),{timeout:6000});
 const feet=[...document.querySelectorAll('.dv-home-step-foot')].map(node=>node.textContent);
 assert.deepEqual(feet,['Starting Alpha','Starting Delta'],'each reply’s foot names the job it is sending, not the other reply’s');
 const rows=[...document.querySelectorAll('.dv-msg-dervo')[0].querySelectorAll('.dv-job')].map(node=>node.textContent);
 assert.deepEqual(rows,['Alphain Performance','Betastarting · in Performance','Gammastarting · in Performance'],'only the row the foot is talking about goes wordless');
 // Alpha goes: reply A moves on to Beta and reply B's foot still says Delta, whose job is still going (the clear path).
 calls.hold.delete('r-a');
 await waitFor(()=>assert.deepEqual([...document.querySelectorAll('.dv-home-step-foot')].map(node=>node.textContent),['Starting Beta','Starting Delta']),{timeout:6000});
 calls.hold.clear();
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-home-step-foot').length,0),{timeout:6000});
 assert.equal(document.querySelectorAll('.dv-home-receipt').length,2);
});
test('a tall answer keeps its top where the step line was, and an answer you scrolled away from moves nothing',{timeout:20000},async()=>{
 // Taller than the window: the top stays put, the way ChatGPT and Claude anchor a reply (20 Sep review, blocking 3).
 let {moved,done}=layout({top:520,height:2400});
 try{
  host({pickup:records,answer:{state:'ready',text:'A long answer.',looked:[],mentions:[],parts:[]}});
  mount({conversations:threads});
  await screen.findByRole('region',{name:'Catch up'});
  const step=await ask();
  assert.ok(moved.includes(900),'sending scrolls the chat to its end, where the step line is');
  moved.length=0;
  await screen.findByText('A long answer.',{},{timeout:6000});
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.equal(step.isConnected,false,'the step line is gone: the answer took its place');
  assert.deepEqual(moved,[],'nothing moves: the answer is read from its top, not its end');
 }finally{done();}
 cleanup();
 // Scrolled up to read something older while it thinks: the answer lands off screen and you aren't yanked to it.
 ({moved,done}=layout({top:1900,height:60}));
 try{
  host({pickup:records,answer:{state:'ready',text:'Another answer.',looked:[],mentions:[],parts:[]}});
  mount({conversations:threads});
  await screen.findByRole('region',{name:'Catch up'});
  await ask();
  moved.length=0;
  await screen.findByText('Another answer.',{},{timeout:6000});
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.deepEqual(moved,[],'you scrolled away: the answer waits where it is');
 }finally{done();}
});

// ---------------------------------------------------------------- push to GitHub
const preview=(extra={})=>({branch:'mvp1',upstream:'origin/mvp1',remote:'origin',url:'https://github.com/brambach/dervo.git',ahead:3,behind:0,head:'abc123',confirm_token:'tok-1',
 commits:[{sha:'614cc31aaaa',short:'614cc31',subject:'Dervo: Connector picker',thread_id:'c2',thread_name:'Connector picker'},{sha:'9f0e1d2bbbb',subject:'Fix typo',made_outside_dervo:true},{sha:'7a7a7a7cccc',short:'7a7a7a7',subject:'Dervo: Live board',thread_id:'c3',thread_name:'Live board'}],...extra});

test('the push line reads the host’s count and the confirm names every commit and the full remote',()=>{
 assert.deepEqual(pushLine(preview()),{branch:'mvp1',ahead:3,behind:0,count:'3 commits not on GitHub',canPush:true,noRemote:false,noUpstream:false,why:''});
 assert.equal(pushLine(preview({behind:2})).why,'GitHub has commits you don’t have. Pull in Terminal first.');
 assert.equal(pushLine(preview({ahead:1})).count,'1 commit not on GitHub');
 assert.equal(pushLine(preview({ahead:0})).canPush,false,'nothing to push, no button');
 assert.equal(pushLine(preview({ahead:undefined})).canPush,false,'a missing count is never a guess');
 assert.equal(pushLine(preview({remote:null,upstream:null})).canPush,false);
 assert.equal(pushLine(preview({behind:2})).canPush,false,'commits GitHub has that you don’t: no push until you pull');
 // The push branch's own call wins when it sends one, with its sentence for why.
 assert.equal(pushLine(preview({can_push:false,detail:'A push is still running.'})).canPush,false);
 assert.equal(pushLine(preview({can_push:false,detail:'A push is still running.'})).why,'A push is still running.');
 assert.equal(pushLine(preview({can_push:true})).canPush,true);
 assert.equal(pushLine(preview({can_push:true,ahead:0})).canPush,false,'never a button with nothing to push, whatever the host says');
 assert.equal(pushLine(preview({git:false,branch:null})),null,'not a git folder: nothing shows');
 assert.equal(pushLine(preview({remote:null})).noRemote,true);
 assert.equal(pushLine(preview({branch:null})),null,'no branch, nothing shows');
 assert.equal(pushLine(undefined),null);
 assert.equal(pushQuestion(preview()),'Push 3 commits to origin/mvp1 on github.com/brambach/dervo?');
 assert.equal(pushQuestion(preview({ahead:1,url:null})),'Push 1 commit to origin/mvp1?');
 assert.deepEqual(commitLine(preview().commits[0]),{short:'614cc31',subject:'Dervo: Connector picker',who:''},'the subject already names the thread, so nothing is said twice');
 assert.deepEqual(commitLine({sha:'abc1234567',subject:'Dervo: Tidy the picker',thread_name:'Connector picker'}),{short:'abc1234',subject:'Dervo: Tidy the picker',who:'Connector picker'},'an older subject gets the thread named');
 assert.deepEqual(commitLine(preview().commits[1]),{short:'9f0e1d2',subject:'Fix typo',who:'made outside Dervo'},'no short id from the host: the first seven of the sha');
});

test('Push sits by the branch, confirms with the commit list, then follows the push to its result',{timeout:15000},async()=>{
 let ahead=3;
 const calls=host({pickup:records,listFails:true,push:()=>preview({ahead}),pushRecords:[{id:'p1',state:'running'},{id:'p1',state:'running'},{id:'p1',state:'pushed',branch:'mvp1',remote:'origin'}]});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const git=await within(pane).findByTestId('home-push');
 assert.match(git.textContent,/mvp13 commits not on GitHubPush/);
 assert.equal(within(pane).queryByRole('group',{name:'Push to GitHub'}),null,'no confirm until you ask');
 fireEvent.click(within(git).getByRole('button',{name:'Push'}));
 const confirm=within(pane).getByRole('group',{name:'Push to GitHub'});
 assert.match(confirm.querySelector('.dv-push-question').textContent,/^Push 3 commits to origin\/mvp1 on github\.com\/brambach\/dervo\?$/);
 const lines=[...confirm.querySelectorAll('.dv-push-commits li')].map(li=>li.textContent);
 assert.deepEqual(lines,['614cc31Dervo: Connector picker','9f0e1d2Fix typomade outside Dervo','7a7a7a7Dervo: Live board']);
 assert.doesNotMatch(confirm.textContent,/straight to main/,'mvp1 is not main');
 assert.equal(within(git).queryAllByRole('button',{name:'Push'}).length,1,'the line’s own Push gives way to the confirm’s');
 fireEvent.click(within(confirm).getByRole('button',{name:'Push'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='push_start')));
 const start=calls.find(([op])=>op==='push_start')[1];
 assert.deepEqual(start,{projectId:'proj',confirmToken:'tok-1',setUpstream:false},'the token the confirm was drawn from, and no -u when the branch has an upstream');
 await within(git).findByText('Pushing…');
 ahead=0;
 await within(git).findByText('Pushed to origin/mvp1.',{},{timeout:8000});
 assert.ok(calls.filter(([op])=>op==='push_status').length>=2,'polled until the host settled it');
 assert.equal(within(git).queryByRole('button',{name:'Push'}),null,'nothing left to push');
});

test('a push the host lost is unknown until a recount settles it; a failed one says why and offers again',{timeout:15000},async()=>{
 const calls=host({pickup:records,listFails:true,push:preview({ahead:2}),pushRecords:[{id:'p1',state:'unknown',detail:'Dervo lost track of this push.'},{id:'p1',state:'not_pushed',detail:'GitHub doesn’t have it.'}]});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const git=await within(pane).findByTestId('home-push');
 fireEvent.click(within(git).getByRole('button',{name:'Push'}));
 fireEvent.click(within(within(pane).getByRole('group',{name:'Push to GitHub'})).getByRole('button',{name:'Push'}));
 await within(git).findByText('Dervo lost track of this push.');
 assert.equal(within(git).queryByRole('button',{name:'Push'}),null,'no retry until the recount');
 fireEvent.click(within(git).getByRole('button',{name:'Check GitHub'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='push_recount')));
 await within(git).findByText(/GitHub doesn’t have it\. You can push again\./);
 fireEvent.click(within(git).getByRole('button',{name:'Push again'}));
 await within(git).findByText('2 commits not on GitHub');
 cleanup();
 host({pickup:records,listFails:true,push:preview({ahead:2}),pushRecords:[{id:'p2',state:'failed',detail:'Git couldn’t sign in to GitHub. Push once from Terminal so your Mac saves the login, then try again.'}]});
 mount();
 const pane2=await screen.findByRole('region',{name:'Catch up'});
 const git2=await within(pane2).findByTestId('home-push');
 fireEvent.click(within(git2).getByRole('button',{name:'Push'}));
 fireEvent.click(within(within(pane2).getByRole('group',{name:'Push to GitHub'})).getByRole('button',{name:'Push'}));
 await within(git2).findByText(/Git couldn’t sign in to GitHub/);
 assert.ok(within(git2).getByRole('button',{name:'Try again'}));
 cleanup();
 // A push Dervo lost track of in an earlier session comes back with the preview's latest record.
 host({pickup:records,listFails:true,push:preview({ahead:2,can_push:false,detail:'Recount before pushing again.',latest:{id:'p9',state:'unknown',detail:'Dervo restarted during this push.'}})});
 mount();
 const pane3=await screen.findByRole('region',{name:'Catch up'});
 const git3=await within(pane3).findByTestId('home-push');
 await within(git3).findByText('Dervo restarted during this push.');
 assert.ok(within(git3).getByRole('button',{name:'Check GitHub'}));
 assert.equal(within(git3).queryByRole('button',{name:'Push'}),null);
 cleanup();
 host({pickup:records,listFails:true,push:preview({ahead:2,latest:{id:'p8',state:'pushed',detail:'Pushed 1 commit to origin/mvp1.'}})});
 mount();
 const pane4=await screen.findByRole('region',{name:'Catch up'});
 const git4=await within(pane4).findByTestId('home-push');
 assert.ok(await within(git4).findByRole('button',{name:'Push'}),'a settled record is history: the count and the button stand');
});

test('main gets its warning, a branch with no upstream is set up, and commits GitHub has that you don’t block the push',async()=>{
 const calls=host({pickup:records,listFails:true,push:preview({branch:'main',upstream:null,ahead:1,commits:[preview().commits[0]]})});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const git=await within(pane).findByTestId('home-push');
 fireEvent.click(within(git).getByRole('button',{name:'Push'}));
 const confirm=within(pane).getByRole('group',{name:'Push to GitHub'});
 assert.match(confirm.textContent,/This goes straight to main on GitHub\./);
 assert.match(confirm.textContent,/main isn’t on GitHub yet\. This sets it up as origin\/main\./);
 fireEvent.click(within(confirm).getByRole('button',{name:'Push'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='push_start')));
 assert.equal(calls.find(([op])=>op==='push_start')[1].setUpstream,true);
 cleanup();
 const calls2=host({pickup:records,listFails:true,push:preview({behind:2})});
 mount();
 const pane2=await screen.findByRole('region',{name:'Catch up'});
 const git2=await within(pane2).findByTestId('home-push');
 await within(git2).findByText('GitHub has commits you don’t have. Pull in Terminal first.');
 assert.equal(within(git2).queryByRole('button',{name:'Push'}),null,'no button, so nothing to click');
 assert.equal(calls2.filter(([op])=>op==='push_start').length,0,'nothing was pushed');
 cleanup();
 host({pickup:records,listFails:true,push:preview({remote:null,upstream:null,ahead:0})});
 mount();
 const pane3=await screen.findByRole('region',{name:'Catch up'});
 const git3=await within(pane3).findByTestId('home-push');
 assert.match(git3.textContent,/^mvp1This project has no GitHub remote\.$/);
 assert.equal(within(git3).queryByRole('button',{name:'Push'}),null);
});

// ---------------------------------------------------------------- images on Home (#3)
test('a host that keeps images for Home shows the paperclip, the drafts left here, and the sent picture under a sent message',{timeout:15000},async()=>{
 const draft={id:'img1',name:'Screenshot.png',state:'ready',size:1200,received:1200,width:800,height:600};
 const calls=host({pickup:records,images:[draft],entries:[{id:'y0',kind:'you',created_at:NOW-600,text:'this button sits left of centre',images:['img0']},{id:'r0',kind:'reply',created_at:NOW-600,state:'ready',text:'I see it.',parts:[]}],
  answer:{state:'ready',text:'Filed it.',looked:[],mentions:[],parts:[]}});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 const clip=await screen.findByRole('button',{name:'Attach an image'});
 assert.ok(clip);
 const tray=await screen.findByLabelText('Attached images');
 await within(tray).findByText('Screenshot.png');
 assert.ok(within(tray).getByRole('button',{name:'Remove Screenshot.png'}));
 assert.equal(calls.filter(([op,a])=>op==='image_drafts'&&a.channelId===homeImageChannel('proj')).length,1,'read once, under Home’s own key');
 // The same thumbnail row the thread shows (SentImages): one item for the one sent picture, under your message.
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-msg-you-wrap .dervo-sent-images > :is(button, span)').length,1,'under your message'));
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'move it to the centre'}});
 fireEvent.keyDown(box,{key:'Enter'});
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='orchestrator_send')));
 assert.deepEqual(calls.find(([op])=>op==='orchestrator_send')[1].images,['img1'],'the attached image goes with the message');
 await waitFor(()=>assert.equal(screen.queryByLabelText('Attached images'),null,'the tray empties once sent'));
 cleanup();
 const calls2=host({pickup:records,images:[],answer:{state:'ready',text:'Ok.',looked:[],mentions:[],parts:[]}});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('button',{name:'Attach an image'});
 const box2=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box2,{target:{value:'no image this time'}});
 fireEvent.keyDown(box2,{key:'Enter'});
 await waitFor(()=>assert.ok(calls2.some(([op])=>op==='orchestrator_send')));
 assert.equal('images' in calls2.find(([op])=>op==='orchestrator_send')[1],false,'no images, no field: the older host’s exact call');
});

test('the thread’s head says its state once, as a pill that toggles the column, and the strip under it keeps only its sentence',async()=>{
 host({pickup:{...item({kind:'stopped',seen:at(18,22,50),asked:'A full design sweep.',did:'It stopped before it finished.',next:null,question:false,failed:false}),lines:[]}});
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const toggles=[];
 render(React.createElement(QueryClientProvider,{client},
  React.createElement(ThreadLive,{projectId:'proj',channelId:'c1',open:false,onToggle:()=>toggles.push(1)}),
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1',headless:true})));
 const live=await screen.findByTestId('thread-live');
 assert.equal(live.tagName,'BUTTON');
 assert.equal(live.querySelector('.dv-live-word').textContent,'Stopped');
 assert.equal(live.getAttribute('aria-expanded'),'false');
 assert.equal(live.querySelector('[data-accent]'),null,'no rust in the head: the sidebar row has the one accent');
 fireEvent.click(live);assert.equal(toggles.length,1,'it shows the column, like the panel button');
 const region=await screen.findByRole('region',{name:'Where this thread is'});
 assert.equal(region.querySelector('.dv-summary-head'),null,'the strip doesn’t say the state a second time');
 assert.equal(region.querySelector('.dv-summary-text').textContent,'It stopped before it finished.');
 fireEvent.click(within(region).getByRole('button',{name:'Hide'}));
 await waitFor(()=>assert.equal(screen.queryByRole('region',{name:'Where this thread is'}),null),'Hide still puts it away');
 cleanup();
 // Without a column to open there's nothing to click: the state is plain text, not a button.
 host({pickup:{...item({kind:'stopped',did:'It stopped.',question:false,failed:false}),lines:[]}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadLive,{projectId:'proj',channelId:'c1',open:false})));
 const plain=await screen.findByTestId('thread-live');
 assert.equal(plain.tagName,'SPAN');assert.equal(plain.getAttribute('role'),null,'not a live region: its time changes on every read');
});

test('under the thread’s head the strip stays out when the thread already says it: done, running or paused',async()=>{
 const paused={state:'blocked',runs:[{id:'r1',state:'interrupted'}],messages:[]};
 const replied={messages:[{role:'assistant',text:'Ideas are stored per project now.'}],runs:[]};
 for(const [fields,task,quiet,kept] of [[{kind:'finished',did:'Finished. 2 files changed.'},replied,true],
  // The host's own line isn't the reply, so nothing lower down says it: the strip stays.
  [{kind:'finished',did:'Finished. 2 files changed.'},undefined,false,'Finished. 2 files changed.'],
  [{kind:'stopped',ending:'stopped',did:'You paused it.'},paused,true],
  // A stop CodingTasks draws no paused card for keeps its sentence, or nothing would say it.
  [{kind:'stopped',ending:'stopped',did:'You paused it.'},{...paused,runs:[{id:'r1',state:'uncertain'}]},false,'You paused it.']]){
  host({pickup:{...item({question:false,failed:false,next:null,...fields}),lines:[]}});
  const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
  render(React.createElement(QueryClientProvider,{client},
   React.createElement(ThreadLive,{projectId:'proj',channelId:'c1',open:false}),
   React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1',headless:true,task})));
  await screen.findByTestId('thread-live');
  const region=screen.queryByRole('region',{name:'Where this thread is'});
  if(quiet)assert.equal(region,null,`${fields.kind}: the reply or the paused card says it`);
  else assert.equal(region?.querySelector('.dv-summary-text')?.textContent,kept,'nothing lower down says it, so the strip does');
  cleanup();
 }
});

test('a thread opens with one sentence on where it is, which you can hide',async()=>{
 assert.equal(plainLine('Read `PLAN-orchestrator.md` first. **Start** with [step 1](x) only.\nA second line.'),'Read PLAN-orchestrator.md first. Start with step 1 only. A second line.');
 assert.deepEqual(recapRows({kind:'running',asked:'Rank matches first.',did:'It’s working on it now: Editing rank.ts',next:null}).map(row=>row.label),['Asked','Now']);
 assert.deepEqual(recapRows({kind:'new',asked:null,did:null,next:null}),[]);
 host({pickup:{...item({kind:'stopped',seen:at(18,22,50),asked:'A full `design` sweep.',did:'It stopped before it finished. It was on: `Home.css`.',next:'Releases after Knowledge.',question:false,failed:false}),lines:['Last time: you asked for a design sweep.','It stopped before it finished.','Next: Releases after Knowledge.']}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 const region=await screen.findByRole('region',{name:'Where this thread is'});
 assert.equal(region.querySelector('.dv-summary .dv-state').textContent,'Stopped');
 assert.equal(region.querySelector('.dv-summary-text').textContent,'It stopped before it finished. It was on: Home.css.','one plain sentence, no raw Markdown');
 assert.equal(region.querySelector('dl'),null,'no Asked, Did and Next rows');
 assert.doesNotMatch(region.textContent,/Last time|You last opened/);
 fireEvent.click(within(region).getByRole('button',{name:'Hide'}));
 assert.equal(screen.queryByRole('region',{name:'Where this thread is'}),null);
});

// Behaviour review S4, 25 Sep: the pickup is read every 15 s, so the orb followed it and ran on after a Stop.
test('the orb and Running follow the live task, not a pickup read before it changed',()=>{
 const now=1_000_000;
 const base={needs:false,did:'Editing Home.css',line:null,now:'Editing Home.css',next:null,next_by:null,question:false,moved:now-120,running_since:now-60,failed:false};
 const stopped={state:'blocked',messages:[],runs:[{id:'r1'}]};
 const ended=threadSummary({...base,kind:'running'},{task:stopped,who:'Codex',now});
 assert.notEqual(ended.mark,'orb');assert.equal(ended.word,'Stopped');
 const review={state:'ready_for_review',messages:[{role:'assistant',text:'The sidebar tidies itself now, with Undo.'}],runs:[{id:'r1'}]};
 assert.equal(threadSummary({...base,kind:'running'},{task:review,who:'Codex',now}).word,'Ready for your review');
 const started={state:'working',messages:[],runs:[{id:'r2',guide:{run_id:'r2',step:'Reading the plan'}}]};
 const going=threadSummary({...base,kind:'finished'},{task:started,who:'Codex',now});
 assert.deepEqual([going.mark,going.word,going.text],['orb','Running','Codex is reading the plan.']);
 assert.equal(threadSummary({...base,kind:'running'},{task:{messages:[],runs:[]},who:'Codex',now}).mark,'orb','no live state: the pickup stands');
});

// App #15 (approved 25 Sep, b16-thread/summary-*): the sentence comes from records, never a model call.
test('the thread’s sentence is the agent’s step while it runs, its question while it asks, and its outcome once it’s done',()=>{
 const now=1_000_000;
 const base={needs:false,did:null,line:null,now:null,next:null,next_by:null,question:false,moved:now-120,running_since:now-41*60,failed:false};
 const guided={messages:[],runs:[{id:'r2',guide:{run_id:'r2',step:'Running the Node check',goal:'A warning on the board'}}]};
 const running=threadSummary({...base,kind:'running',now:'Editing ProjectRoom.tsx'},{task:guided,who:'Codex',now});
 assert.deepEqual(running,{below:true,mark:'orb',accent:false,state:'running',word:'Running',caption:'41 min',text:'Codex is running the Node check.'},
  'below: the live trail says the step too, so the strip under the head stays out (polish, 8 Oct)');
 // A guide written in an earlier run isn't this run's step: the host's own step stands in.
 const staleGuide={messages:[],runs:[{id:'r1',guide:{run_id:'r0',step:'Old step'}}]};
 assert.equal(threadSummary({...base,kind:'running',now:'Editing ProjectRoom.tsx'},{task:staleGuide,who:'Codex',now}).text,'Codex is editing ProjectRoom.tsx.');
 const reply={messages:[{role:'user',text:'Store ideas'},{role:'assistant',text:'## Changed\n- Claude Code now stores ideas per project, grouped by idea, and 44 backend tests pass. More detail follows.'}],runs:[]};
 const review=threadSummary({...base,kind:'review',needs:true,did:'Finished. 2 files changed.',next:'Your review, then commit.',next_by:'dervo'},{task:reply,who:'Claude Code',now});
 assert.equal(review.word,'Ready for your review');assert.equal(review.mark,'needs');assert.equal(review.accent,true);
 assert.equal(review.text,'Claude Code now stores ideas per project, grouped by idea, and 44 backend tests pass.','the reply’s first sentence, past its heading');
 assert.match(review.caption,/^Finished /);
 const asking=threadSummary({...base,kind:'running'},{task:guided,who:'Codex',now,question:{ask:'Should both threads’ rows show it, or only the idea’s board?',createdAt:now-120}});
 assert.deepEqual([asking.word,asking.caption,asking.text],['Needs you','Asked 2 min ago','Codex is waiting on your answer below.'],'the question stays in the tray, not repeated in the strip');
 const progress={messages:[{role:'assistant',text:'The warning works on the board. Should both threads’ rows show it, or only the idea’s board?'}],runs:[]};
 assert.equal(threadSummary({...base,kind:'answered'},{task:progress,who:'Codex',now,question:{ask:'Should both threads’ rows show it, or only the idea’s board?',createdAt:now-120}}).text,
  'The warning works on the board. Codex is waiting on your answer below.','what was done leads, the question isn’t quoted');
 const onlyAsks={messages:[{role:'assistant',text:'Should both threads’ rows show it?'}],runs:[]};
 assert.equal(threadSummary({...base,kind:'answered'},{task:onlyAsks,who:'Codex',now,question:{ask:'Should both threads’ rows show it?',createdAt:now-120}}).text,
  'Codex is waiting on your answer below.','a reply that is only the question adds nothing');
 assert.equal(threadSummary({...base,kind:'answered'},{who:'Codex',now,question:{ask:'Which rows?',createdAt:now-120,saved:true}}).text,'Codex hasn’t picked up your answer yet.');
 assert.equal(threadSummary({...base,kind:'answered'},{who:'Codex',now,question:{ask:'Which rows?',createdAt:now-120,saved:true}}).caption,'Answer saved','your pick is saved, not yet confirmed');
 const ended=threadSummary({...base,kind:'finished',needs:true,question:true,next:'Hi or Hello?',next_by:'agent'},{who:'Claude Code',now});
 assert.equal(ended.text,'Claude Code ended its reply with a question for you.','a reply that ended on a question for you');
 assert.equal(threadSummary({...base,kind:'stopped',failed:true,did:'Stopped at Claude’s 5-hour limit. Resets 3pm.'},{now}).text,'Stopped at Claude’s 5-hour limit. Resets 3pm.');
 assert.equal(threadSummary({...base,kind:'new'},{now}),null);
 assert.equal(stepSentence('Reading the plan','Codex'),'Codex is reading the plan.');
 assert.equal(stepSentence('Tests pass now','Codex'),'Tests pass now.','a step that isn’t a doing word stays as written');
 assert.equal(firstSentence('Changed\n\n1. Ideas are stored per project now. Then more.'),'Ideas are stored per project now.');
 // What's missing follows what was done, from the reply's own "Not checked" heading or line.
 const gaps={messages:[{role:'assistant',text:'## Changed\n- Ideas are stored per project, modelled on project_knowledge.py.\n\n## Not checked\n- The frontend. Beta plan screen has uncommitted work.\n- Ideas aren’t reachable yet.'}],runs:[]};
 assert.equal(threadSummary({...base,kind:'review',needs:true},{task:gaps,who:'Claude Code',now}).text,'Ideas are stored per project, modelled on project_knowledge.py. Not checked: The frontend.');
 assert.equal(missingSentence('Done the thing here now.\nNot checked: VoiceOver or 200% text'),'Not checked: VoiceOver or 200% text.');
 assert.equal(missingSentence('Everything was checked, and all 40 tests pass.'),'','nothing added when the reply doesn’t say');
 assert.equal(runningFor(now-65*60,now),'1 h 5 min');assert.equal(runningFor(null,now),'');assert.equal(runningFor(now-3,now),'','no minute shown before one has passed');assert.equal(runningFor(now-61,now),'1 min');
});

test('a thread with no work has no pickup',async()=>{
 host({pickup:{...item({kind:'new',asked:null,did:null,next:null}),lines:[]}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(screen.queryByRole('region',{name:'Where this thread is'}),null);
});

// ---------------------------------------------------------------- what Dervo did on its own (b11/visible)
const change=(kind,line,extra={})=>({kind,thread:null,idea:{id:'i3',name:'Connectors'},before:null,revision:4,can_undo:true,undone:false,line,...extra});

test('a bug or idea Home logged or noted is a line with Undo, and an undone note says what it removed',()=>{
 const item={id:'b12',kind:'bug',number:12,name:'Send arrow off centre'};
 const entry={id:'r3',kind:'reply',created_at:NOW,state:'ready',changes:[change('noted','Added to bug #12, Send arrow off centre.',{idea:null,item,note:'n1'}),
  change('logged','Logged as idea #13.',{idea:null,item:{...item,id:'i13',kind:'idea',number:13}}),
  change('noted','Removed your note from bug #12.',{idea:null,item,note:'n2',undone:true})],notes:['That sounds like #9, which is done. Reopen it, or log this as new?']};
 assert.deepEqual(replyLines(entry).map(l=>[l.text,l.change,l.undo,l.undone]),[['Added to bug #12, Send arrow off centre.',0,true,false],
  ['Logged as idea #13.',1,true,false],['Removed your note from bug #12.',2,false,true],['That sounds like #9, which is done. Reopen it, or log this as new?',null,false,false]]);
 // Negative control: a bug line never counts as filing a thread, so a job's "in <workstream>" isn't hidden by it.
 assert.equal(filedByChanges(entry).size,0);
});

test('a reply’s own lines are its changes in order, then its notes; Undo only where the host allows it',()=>{
 const entry={id:'r1',kind:'reply',created_at:NOW,state:'ready',changes:[change('made','Started a Connectors workstream.',{can_undo:false}),
  change('moved','Moved Connector picker to Connectors.',{thread:{id:'h2',name:'Connector picker'}}),change('renamed','Renamed Shipping to Releases.',{undone:true})],
  notes:['“Orchestrator” keeps its name: you named it.','  ']};
 assert.deepEqual(replyLines(entry).map(l=>[l.text,l.change,l.undo,l.undone]),[['Started a Connectors workstream.',0,false,false],
  ['Moved Connector picker to Connectors.',1,true,false],['Renamed Shipping to Releases.',2,false,true],['“Orchestrator” keeps its name: you named it.',null,false,false]]);
 // Negative control: an older entry with neither field says nothing, and a stray non-array doesn't crash.
 assert.deepEqual(replyLines({id:'r0',kind:'reply',created_at:NOW}),[]);
 assert.deepEqual(replyLines({id:'r0',kind:'reply',created_at:NOW,changes:null,notes:'x'}),[]);
 // A job whose thread a change line already filed doesn't say "in Connectors" twice; an undone move does again.
 const part={index:0,action:'start',text:'x',conversation:null,idea:{id:'i3',name:'Connectors'},channel_id:'h2',state:'sent'};
 assert.equal(jobLine(part,filedByChanges(entry)),'');
 assert.equal(jobLine(part),'in Connectors');
 assert.equal(jobLine(part,filedByChanges({...entry,changes:[change('moved','m',{thread:{id:'h2',name:'Connector picker'},undone:true})]})),'in Connectors');
});

test('a failed arrival is said once, only when it’s the newest and from this visit',()=>{
 const failed={id:'a1',kind:'reply',created_at:at(19,8,50),arrival:true,state:'failed'};
 assert.equal(failedArrival([failed],at(18,23,20)),true);
 assert.equal(failedArrival([failed],at(19,8,55)),false,'from an earlier visit');
 assert.equal(failedArrival([failed,{...failed,id:'a2',state:'ready',text:'Morning.'}],at(18,23,20)),false,'a later summary that worked replaces it');
 assert.equal(failedArrival([{...failed,arrival:false}],at(18,23,20)),false,'an ordinary failed answer has its own Send again');
});

test('the lost-runs line shows this project’s runs only, in the host’s words when they’re all here',()=>{
 const detail='Dervo closed while a run was going in Connector picker, so it can’t say how it ended. Open that thread to check your folder, then mark it stopped.';
 const one={lostRuns:[{taskId:'t',channelId:'h2',projectId:'proj',name:'Connector picker'}],lostRunsDetail:detail};
 assert.deepEqual(lostRunsLine(one,'proj'),{text:detail,channelId:'h2'});
 assert.equal(lostRunsLine(one,'other'),null,'another project’s run never shows here');
 assert.equal(lostRunsLine({lostRuns:[],lostRunsDetail:null},'proj'),null,'an ordinary start says nothing');
 assert.equal(lostRunsLine(undefined,'proj'),null);
 const mixed={lostRuns:[...one.lostRuns,{taskId:'u',channelId:'z',projectId:'other',name:'Elsewhere'},{taskId:'v',channelId:'h3',projectId:'proj',name:'Live board'}],
  lostRunsDetail:'Dervo closed while 3 runs were going, so it can’t say how they ended. Open each thread to check your folder, then mark them stopped.'};
 assert.deepEqual(lostRunsLine(mixed,'proj'),{text:'Dervo closed while 2 runs were going here, so it can’t say how they ended. Open each thread to see where it got to.',channelId:null});
 // Beta.18: a run Dervo settled as paused from its own record says paused, and opens its thread.
 const paused={lostRuns:[{taskId:'t',channelId:'h2',projectId:'proj',name:'Connector picker',paused:true},{taskId:'u',channelId:'z',projectId:'other',name:'Elsewhere'}],lostRunsDetail:'host words'};
 assert.deepEqual(lostRunsLine(paused,'proj'),{text:'Dervo restarted while a run was going in Connector picker. It was between steps, so it paused there. Continue picks it up.',channelId:'h2'});
 const both={lostRuns:[...paused.lostRuns,{taskId:'v',channelId:'h3',projectId:'proj',name:'Live board'}],lostRunsDetail:'host words'};
 assert.deepEqual(lostRunsLine(both,'proj'),{text:'Dervo closed while a run was going in Live board, so it can’t say how it ended. Open that thread to see where it got to.',channelId:'h3'},'the unsettled one is what needs you');
});

test('Home shows what a reply changed with Undo, its notes, the lost run and the failed summary, each once',async()=>{
 const opened=[];
 const reply={id:'r1',kind:'reply',created_at:NOW,state:'ready',text:'Filed all three in **Connectors**.',parts:[],looked:[],mentions:[],
  changes:[change('made','Started a Connectors workstream.',{can_undo:false}),change('moved','Moved Connector picker to Connectors.',{thread:{id:'h2',name:'Connector picker'}})],
  notes:['“Orchestrator” keeps its name: you named it.']};
 const calls=host({pickup:records,entries:[{id:'a1',kind:'reply',created_at:at(19,8,50),arrival:true,state:'failed',detail:'timed out'},{id:'y1',kind:'you',created_at:NOW,text:'split it'},reply],
  status:{lostRuns:[{taskId:'t',channelId:'h2',projectId:'proj',name:'Connector picker'}],lostRunsDetail:'Dervo closed while a run was going in Connector picker, so it can’t say how it ended. Open that thread to check your folder, then mark it stopped.'},
  undo:(a,chat)=>({...chat,entries:chat.entries.map(e=>e.id===a.entryId?{...e,changes:e.changes.map((c,i)=>i===a.change?{...c,undone:true,can_undo:false}:c)}:e)})});
 mount({onOpen:id=>opened.push(id)});
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const lost=await within(pane).findByText(/Dervo closed while a run was going in Connector picker/);
 assert.equal(lost.closest('.dv-activity').parentElement,pane,'at the top of the pane, not in a row');
 await within(pane).findByText(ARRIVAL_FAILED);
 assert.equal(screen.getAllByText(ARRIVAL_FAILED).length,1,'said once');
 fireEvent.click(within(lost.closest('.dv-activity')).getByRole('button',{name:'Open thread'}));
 assert.deepEqual(opened,['h2']);
 await openLatest();
 const chat=screen.getByRole('region',{name:'Ask Dervo'});
 const lines=[...chat.querySelectorAll('.dv-home-activity .dv-activity-text')].map(n=>n.textContent);
 assert.deepEqual(lines,['Started a Connectors workstream.','Moved Connector picker to Connectors.','“Orchestrator” keeps its name: you named it.']);
 assert.equal(within(chat).getAllByRole('button',{name:/^Undo/}).length,1,'Undo only on the change that can be undone');
 fireEvent.click(within(chat).getByRole('button',{name:'Undo: Moved Connector picker to Connectors.'}));
 await within(chat).findByText('Undone');
 assert.deepEqual(calls.find(c=>c[0]==='orchestrator_undo')[1],{projectId:'proj',profileId:'p1',entryId:'r1',change:1});
 assert.equal(within(chat).queryByRole('button',{name:/^Undo/}),null);
 fireEvent.click(screen.getByRole('button',{name:'Back to Home'}));
 assert.equal(screen.queryByText(/Dervo closed while a run/),null,'gone once you act on it');
 assert.doesNotMatch(document.body.innerHTML,/#[0-9a-f]{6}/i);
});

test('a refused Undo shows the host’s sentence in its place; without a lost run or a failed summary the pane has no activity line',async()=>{
 const reply={id:'r2',kind:'reply',created_at:NOW,state:'ready',text:'Moved it.',parts:[],looked:[],mentions:[],changes:[change('moved','Moved Live board to Design.',{thread:{id:'c3',name:'Live board'}})]};
 host({pickup:records,entries:[{id:'y2',kind:'you',created_at:NOW,text:'move it'},reply],status:{lostRuns:[],lostRunsDetail:null},
  undo:()=>new Error('That thread changed since, so this can’t be undone here. Move it from the sidebar instead.')});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 await within(pane).findByText(/has a question for you/);
 assert.equal(pane.querySelector('.dv-activity'),null,'negative control: nothing to say, no line');
 await openLatest();
 fireEvent.click(await screen.findByRole('button',{name:'Undo: Moved Live board to Design.'}));
 await screen.findByText(/That thread changed since/);
 assert.equal(screen.queryByRole('button',{name:/^Undo/}),null);
});

test('the thread recap adds what the session got and an unread note as muted lines, and nothing when there are none',async()=>{
 assert.deepEqual(recapActivity({continuity:'Codex starts fresh. It gets a summary of the last 6 messages.',note:'Claude stopped before reading your note. It goes with your next message.'}),
  ['Codex starts fresh. It gets a summary of the last 6 messages.','Claude stopped before reading your note. It goes with your next message.']);
 assert.deepEqual(recapActivity({continuity:null,note:'  '}),[]);
 assert.deepEqual(recapActivity({}),[],'an older host sends neither');
 host({pickup:{...item({kind:'stopped',asked:'Pick connectors per project.',did:'It stopped.',next:null}),lines:[],continuity:'Codex starts fresh. It gets a summary of the last 6 messages.',note:null}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 const region=await screen.findByRole('region',{name:'Where this thread is'});
 assert.deepEqual([...region.querySelectorAll('.dv-activity-text')].map(n=>n.textContent),['Codex starts fresh. It gets a summary of the last 6 messages.']);
 assert.equal(region.querySelector('.dv-summary-text').textContent,'It stopped.','the sentence is unchanged');
});

// ---------------------------------------------------------------- the thread's bugs and ideas lines (b11/bugs-auto)
const workItem={id:'b12',kind:'bug',number:12,name:'Send arrow off centre',revision:3,target:null,was:'building'};
const doneLine=(extra={})=>({kind:'done',line:'Marked bug #12 done.',can_undo:true,undone:false,project_id:'proj',profile_id:'p1',items:[workItem],...extra});

test('the thread recap says what the thread is working on and what its approved commit marked done; Undo reopens each item through ideas_item_set',async()=>{
 assert.deepEqual(workLines({work:[doneLine(),{kind:'working',line:'Working on idea #14, Voice notes.',can_undo:false,undone:false,project_id:'proj',profile_id:'p1',items:[]}]}).map(l=>l.line),
  ['Marked bug #12 done.','Working on idea #14, Voice notes.']);
 assert.deepEqual(workLines({work:[null,{kind:'done',line:'  ',items:[]},{kind:'done',line:'no items'}]}),[],'half lines are dropped');
 assert.deepEqual(workLines({}),[],'an older host sends none');
 let marked=true;
 const sets=[];
 const fake=host({pickup:()=>({...item({kind:'approved',asked:'Fix the send arrow.',did:'Approved and committed.',next:null}),lines:[],
   work:marked?[doneLine()]:[doneLine({can_undo:false,undone:true,items:[]}),{kind:'working',line:'Working on bug #12, Send arrow off centre.',can_undo:false,undone:false,project_id:'proj',profile_id:'p1',items:[workItem]}]}),
  itemSet:a=>{sets.push(a);marked=false;return {items:[]};}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 const region=await screen.findByRole('region',{name:'Where this thread is'});
 assert.deepEqual([...region.querySelectorAll('.dv-activity-text')].map(n=>n.textContent),['Marked bug #12 done.']);
 const undo=within(region).getByRole('button',{name:'Undo: Marked bug #12 done.'});
 fireEvent.click(undo);
 await within(region).findByText('Undone');
 assert.deepEqual(sets,[{projectId:'proj',profileId:'p1',itemId:'b12',expectedRevision:3,name:'Send arrow off centre',state:'building',target:null}],'back to the state it was in, with the revision the host gave');
 assert.deepEqual([...region.querySelectorAll('.dv-activity-text')].map(n=>n.textContent),['Marked bug #12 done.','Working on bug #12, Send arrow off centre.'],'reopened, the thread is on it again');
 assert.equal(within(region).queryByRole('button',{name:/^Undo/}),null,'a working line has no Undo, and the undone mark says so instead');
 assert.equal(region.querySelector('.dv-summary .dv-state').textContent,'Approved','the strip is unchanged');
 assert.equal(fake.filter(([op])=>op==='ideas_item_set').length,1);
});

test('a refused Undo shows the host’s sentence in place of the line, and a thread with no run but a linked item still shows its line',async()=>{
 host({pickup:{...item({kind:'approved',asked:'Fix it.',did:'Approved and committed.',next:null}),lines:[],work:[doneLine()]},
  itemSet:()=>new Error('Ideas changed. Reload before saving again.')});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 const region=await screen.findByRole('region',{name:'Where this thread is'});
 fireEvent.click(within(region).getByRole('button',{name:/^Undo/}));
 await within(region).findByText('Ideas changed. Reload before saving again.');
 assert.equal(within(region).queryByRole('button',{name:/^Undo/}),null);
 cleanup();
 host({pickup:{...item({kind:'new',asked:null,did:null,next:null}),lines:[],work:[{kind:'working',line:'Working on bug #12, Send arrow off centre.',can_undo:false,undone:false,project_id:'proj',profile_id:'p1',items:[workItem]}]}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 const fresh=await screen.findByRole('region',{name:'Where this thread is'});
 assert.equal(fresh.querySelector('dl'),null,'no recap rows for a thread that never ran');
 assert.equal(fresh.querySelector('.dv-summary'),null,'and no sentence');
 assert.deepEqual([...fresh.querySelectorAll('.dv-activity-text')].map(n=>n.textContent),['Working on bug #12, Send arrow off centre.']);
 // Negative control: the same thread with nothing on it is still nothing.
 cleanup();
 host({pickup:{...item({kind:'new',asked:null,did:null,next:null}),lines:[],work:[]}});
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(ThreadPickup,{projectId:'proj',channelId:'c1'})));
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(screen.queryByRole('region',{name:'Where this thread is'}),null);
});

// ---------------------------------------------------------------- the choices tray above the composer (#16, direction A)
// The unit tests for the tray's own rules live in questions.test.mjs. These render the real Home, find the real tray it
// mounts, press a real key on a real element and assert what the real screen did with it, because a reducer that is
// right on its own is exactly what let six bypasses through the first review. The question comes down the wire on a
// ready reply, the way project_orchestrator.py writes it.
const askReopen={asker:'Home',to:'home',ask:'Reopen #9, or log this as new?',options:[
 {label:'Reopen #9',detail:'Adds what you said to it',grant:null},
 {label:'Log as a new bug',detail:'A new bug, linked to #9',grant:null}]};
// Code alone ever writes a grant (SCHEMA has no such field, and model_question forces grant to None), so this fixture is
// code's own. It is here because the two-key rule has to hold on the real screen, not only in the reducer.
const askGrant={asker:'Home',to:'home',ask:'Say go to let it use the shell.',options:[
 {label:'Go',detail:'Full access for this thread only',grant:{runs:['pnpm docs:build'],wont:'It won’t commit or push. Those still ask.'}},
 {label:'Keep it read-only',detail:'It proposes the page as a diff you apply',grant:null}]};
const asked=(question,extra={})=>[{id:'y9',kind:'you',created_at:NOW,text:'the login bug is back'},
 {id:'r9',kind:'reply',created_at:NOW,state:'ready',parts:[],looked:[],mentions:[],changes:[],question,...extra}];
const tray=()=>document.querySelector('.dv-qtray');
const trayRow=name=>screen.getByRole('button',{name});
const sends=calls=>calls.filter(([op])=>op==='orchestrator_send').map(([,a])=>a.text);

test('Home keeps a week of requests out of catch-up and reopens only the selected exchange',async()=>{
 const entries=Array.from({length:14},(_,i)=>[
  {id:'history-you-'+i,request_id:'history-'+i,kind:'you',text:'Request '+i,created_at:NOW+i},
  {id:'history-reply-'+i,request_id:'history-'+i,kind:'reply',text:'Result '+i,state:'ready',created_at:NOW+i,parts:[]},
 ]).flat();
 const calls=host({pickup:records,entries,answer:{state:'ready',text:'Follow-up saved',parts:[]}});
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 assert.equal(screen.queryByText('Result 13'),null);
 fireEvent.click(screen.getByRole('button',{name:'Recent requests'}));
 await screen.findByText('Request 13');
 assert.equal(document.querySelectorAll('.dv-home-request-row').length,10);
 fireEvent.click(screen.getByRole('button',{name:'Older'}));
 fireEvent.click(await screen.findByRole('button',{name:/Request 0/}));
 await screen.findByText('Result 0');
 assert.equal(screen.queryByText('Result 13'),null);
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'Follow up on this'}});fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.equal(calls.find(([op])=>op==='orchestrator_send')[1].requestId,'history-0'));
 await screen.findByText('Follow-up saved',{},{timeout:6000});
 fireEvent.click(screen.getByRole('button',{name:'Back to Home'}));
 assert.equal(screen.queryByText('Result 0'),null);
 fireEvent.change(box,{target:{value:'A new request'}});fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.equal(calls.filter(([op])=>op==='orchestrator_send').at(-1)[1].requestId,null));
});

test('an unanswered routing question survives an unrelated request and answers within its own exchange',async()=>{
 const entries=[...asked(askReopen).map(e=>({...e,request_id:'question-request'})),
  {id:'other-user',request_id:'other-request',kind:'you',created_at:NOW+1,text:'Different request'},
  {id:'other-reply',request_id:'other-request',kind:'reply',created_at:NOW+1,state:'ready',text:'Different result',parts:[]}];
 const calls=host({pickup:records,entries,answer:{state:'ready',text:'Answered',question:null,parts:[]}});
 mount();
 // Since 9 Oct Home's own question is a row of Waiting on you, after the threads that wait on you, counted with them.
 const waiting=await screen.findByRole('region',{name:'Waiting on you'});
 const ask=await within(waiting).findByRole('button',{name:/Answer to continue/});
 assert.equal(waiting.querySelector('.dv-now-count').textContent,'4','three threads and Home’s question');
 assert.ok(waiting.querySelector('[data-row="c3"]').compareDocumentPosition(ask)&4,'after the threads');
 assert.match(ask.closest('article').textContent,/Home asks you/);
 assert.equal(tray(),null,'number keys cannot answer an unopened request');
 fireEvent.click(ask);
 await screen.findByRole('group',{name:'Home is asking'});
 fireEvent.click(screen.getByRole('button',{name:'Reopen #9'}));
 await waitFor(()=>assert.equal(calls.find(([op])=>op==='orchestrator_send')[1].requestId,'question-request'));
 await screen.findByText('Answered',{},{timeout:6000});
 fireEvent.click(screen.getByRole('button',{name:'Back to Home'}));
 assert.equal(screen.queryByRole('button',{name:/Answer to continue/})===null,true);
 assert.equal(screen.getByRole('region',{name:'Waiting on you'}).querySelector('.dv-now-count').textContent,'3');
});

test('the tray sits above the composer with the reply’s question, and a number key sends the answer as your own message',async()=>{
 const calls=host({pickup:records,entries:asked(askReopen)});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 const group=await screen.findByRole('group',{name:'Home is asking'});
 assert.equal(group,tray(),'the tray Home mounts is the real QuestionTray');
 assert.equal(group.nextElementSibling.className,'dv-composer dv-home-composer',
  'nothing between the tray and the composer: the tray tells its own composer from every other field by that link');
 assert.deepEqual([...group.querySelectorAll('.dv-qtray-label')].map(node=>node.textContent),['Reopen #9','Log as a new bug']);
 assert.equal(group.querySelector('.dv-qtray-hint').textContent.replace(/\s+/g,' '),'1 – 2 to pick');
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 assert.equal(box.placeholder,'Or answer in words');
 assert.equal(document.querySelector('.dv-composer .dv-hint').textContent,'Esc hides or shows the choices');
 fireEvent.keyDown(document.body,{key:'1'});
 await waitFor(()=>assert.deepEqual(sends(calls),['Reopen #9']));
 assert.equal(calls.some(([op])=>op==='ideas_item_reopen'),false,
  'the tray never calls the reopen op itself: your words go to Home and the orchestrator reopens #9 from them');
});
test('the tray takes the keys only while the composer is empty, and Home’s own Enter still sends what you typed',async()=>{
 const calls=host({pickup:records,entries:asked(askReopen)});
 mount();
 if(fixtureHasRequest)await openLatest();
 const group=await screen.findByRole('group',{name:'Home is asking'});
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 // A digit on the page is the tray's, so it never lands in the composer.
 assert.equal(fireEvent.keyDown(box,{key:'2'}),false,'the key was taken');
 await waitFor(()=>assert.deepEqual(sends(calls),['Log as a new bug']));
 cleanup();
 const typed=host({pickup:records,entries:asked(askReopen)});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('group',{name:'Home is asking'});
 const field=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(field,{target:{value:'it happens on the second login'}});
 assert.equal(tray().dataset.folded,'typing','your own words are the other answer, so the choices fold away');
 assert.equal(document.querySelector('.dv-composer .dv-hint').textContent,'Enter sends it to Home as your answer');
 fireEvent.keyDown(field,{key:'1'});
 fireEvent.keyDown(document.body,{key:'1'});
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.deepEqual(sends(typed),[],'with words in the composer no number key answers');
 // Esc while typing asks Home to empty the composer, and the choices come back.
 fireEvent.keyDown(document.body,{key:'Escape'});
 await waitFor(()=>assert.equal(field.value,''));
 assert.equal(tray().dataset.folded,undefined,'the choices are back');
 // And your own words are still the other answer: Enter is Home's send, as it always was.
 fireEvent.change(field,{target:{value:'it happens on the second login'}});
 fireEvent.keyDown(field,{key:'Enter'});
 await waitFor(()=>assert.deepEqual(sends(typed),['it happens on the second login']));
});
test('the six bypasses stay closed once the tray is inside Home, whatever is focused, held, typed or open',async()=>{
 const calls=host({pickup:records,entries:asked(askGrant)});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('group',{name:'Home is asking'});
 const go=()=>trayRow('Go, then Enter to allow'),readOnly=()=>trayRow('Keep it read-only');
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 const quiet=async()=>{await new Promise(resolve=>setTimeout(resolve,30));};

 // 1. Tab to another row and press Enter: that row's own click, never the row armed earlier.
 fireEvent.keyDown(document.body,{key:'1'});
 assert.equal(go().getAttribute('aria-pressed'),'true');
 readOnly().focus();
 assert.equal(fireEvent.keyDown(readOnly(),{key:'Enter'}),true,'the button keeps its own Enter');
 await quiet();
 assert.deepEqual(sends(calls),[],'Enter on the read-only row grants nothing');

 // 2. A typing detour drops the arm: coming back to an empty composer never finds a grant one Enter away.
 fireEvent.change(box,{target:{value:'hmm'}});
 fireEvent.change(box,{target:{value:''}});
 assert.equal(go().getAttribute('aria-pressed'),'false');
 fireEvent.keyDown(document.body,{key:'Enter'});
 await quiet();
 assert.deepEqual(sends(calls),[]);

 // 3. Another field on the screen is not a confirmation surface, empty or not. An empty search box once was.
 document.body.insertAdjacentHTML('beforeend','<input id="dervo-saved-search" aria-label="Search saved text"/>');
 const search=document.getElementById('dervo-saved-search');
 search.focus();
 assert.equal(fireEvent.keyDown(search,{key:'1'}),true,'the digit reaches the search box');
 fireEvent.keyDown(search,{key:'Enter'});
 await quiet();
 assert.equal(go().getAttribute('aria-pressed'),'false','the search box never arms the grant');
 assert.deepEqual(sends(calls),[],'and its Enter grants nothing');
 // Nor does it confirm an arm made on the page: Enter in that box is that box's, armed or not.
 fireEvent.keyDown(document.body,{key:'1'});
 assert.equal(go().getAttribute('aria-pressed'),'true');
 fireEvent.keyDown(search,{key:'Enter'});
 await quiet();
 assert.deepEqual(sends(calls),[]);
 search.remove();

 // 4. A held key is one key: neither a held Enter on the armed grant nor a held digit.
 for(let n=0;n<8;n++)fireEvent.keyDown(document.body,{key:'Enter',repeat:true});
 await quiet();
 assert.deepEqual(sends(calls),[],'eight repeated Enters grant nothing');
 fireEvent.keyDown(document.body,{key:'Escape'});
 assert.equal(go().getAttribute('aria-pressed'),'false','Esc drops the arm');
 fireEvent.keyDown(document.body,{key:'1',repeat:true});
 assert.equal(go().getAttribute('aria-pressed'),'false','a held digit never arms it');
 fireEvent.keyDown(document.body,{key:'1'});
 assert.equal(go().getAttribute('aria-pressed'),'true');

 // 5. Anything open takes the key first, and so does a handler that already took it.
 document.body.insertAdjacentHTML('beforeend','<div role="dialog" data-state="open"><button>Close</button></div>');
 fireEvent.keyDown(document.body,{key:'Enter'});
 await quiet();
 assert.deepEqual(sends(calls),[],'a dialog is open, so Enter is not the tray’s');
 document.querySelector('[role="dialog"]').remove();

 // 6. Shift+Enter is the composer's newline, never a confirm.
 fireEvent.keyDown(document.body,{key:'Enter',shiftKey:true});
 fireEvent.keyDown(box,{key:'Enter',shiftKey:true});
 await quiet();
 assert.deepEqual(sends(calls),[]);

 // The arm survived all six, and a plain Enter still allows it, with the focus in the empty composer the tray sits on:
 // the rule is two keys, not no keys. Home takes Enter only when there is something to send, so it never swallows the
 // second key and leaves the grant unreachable from the one field you are most likely to be in.
 assert.equal(go().getAttribute('aria-pressed'),'true');
 box.focus();
 fireEvent.keyDown(box,{key:'Enter'});
 await waitFor(()=>assert.deepEqual(sends(calls),['Go']));
});
test('the tray closes with the question: nothing after it asks, a job going out turns it off, and signed out there is none',async()=>{
 host({pickup:records,entries:[...asked(askReopen),{id:'y10',kind:'you',created_at:NOW+1,text:'never mind'}]});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 assert.equal(screen.queryByRole('group',{name:'Home is asking'}),null,'you said something after it, so there is nothing to answer with a key');
 cleanup();
 host({pickup:records,entries:asked(askReopen,{state:'thinking'})});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 assert.equal(tray(),null,'a reply still thinking carries no question yet');
 cleanup();
 // Four options is not a question the tray shows; the reply's own prose still asks.
 host({pickup:records,entries:asked({...askReopen,options:[...askReopen.options,
  {label:'Close it again',detail:null,grant:null},{label:'Leave it',detail:null,grant:null}]})});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 assert.equal(tray(),null,'refused, not clipped');
 cleanup();
 const out=host({pickup:records,entries:asked(askReopen),account:{state:'signed_out',method:'email_code',detail:'',identity:null,operation_id:null,
  expires_at:null,can_start:true,can_cancel:false,can_poll:false,can_restore:false,can_sign_out:false,ownership:'local_unclaimed',
  sync_available:false,membership:null,access_valid_until:null}});
 mount();
 if(fixtureHasRequest)await openLatest();
 await screen.findByRole('region',{name:'Home'});
 assert.equal(tray()?.getAttribute('aria-disabled'),'true','signed out, send() would refuse, so the tray takes no key');
 fireEvent.keyDown(document.body,{key:'1'});
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.deepEqual(sends(out),[],'a pick is never consumed and dropped');
});

test('/feedback opens the existing form without a Home request or clearing its draft',async()=>{
 const calls=host({pickup:records,images:[]});let opened=0;
 const view=mount({onFeedback:()=>opened++});
 const box=await screen.findByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'/feedback'}});fireEvent.click(screen.getByRole('button',{name:'Send'}));
 assert.equal(opened,1);assert.equal(box.value,'/feedback');
 assert.equal(calls.some(([op])=>op==='orchestrator_send'),false);view.unmount();
});


test('Home opens each exact lost task without hiding the other recovery links',async()=>{
 const opened=[];
 host({pickup:records,status:{lostRuns:[{taskId:'lost-one',channelId:'h2',projectId:'proj',name:'Connector picker'},{taskId:'lost-two',channelId:'h2',projectId:'proj',name:'Connector picker'},{taskId:'elsewhere',channelId:'z',projectId:'other',name:'Other project'}]}});
 mount({onOpenTask:(channel,task,trigger)=>opened.push([channel,task,trigger.tagName])});
 const recovery=await screen.findByRole('region',{name:'Tasks needing recovery'});
 fireEvent.click(within(recovery).getByRole('button',{name:'Open lost task lost-one in Connector picker'}));
 fireEvent.click(within(recovery).getByRole('button',{name:'Open lost task lost-two in Connector picker'}));
 assert.deepEqual(opened,[['h2','lost-one','BUTTON'],['h2','lost-two','BUTTON']]);
 assert.equal(within(recovery).getAllByRole('button').length,2);
 assert.equal(within(recovery).queryByText('Other project'),null);
});

// ---------------------------------------------------------------- 23 Sep review: Home on Codex, handoff, Stop, header, entrance
/** The host's listing as a project on this account would read it: Home's own model and whose account it runs on. */
function onAccount(home){
 const invoke=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command,result=await invoke(command,args);
  return op==='orchestrator_list'||op==='orchestrator_arrive'||op==='orchestrator_send'?{...result,home}:result;
 }};
}
const CODEX_HOME={ready:true,provider:'codex',model:'GPT-6-Luna'};

test('a Codex project talks to Home like a Claude one: the hint names its model and account, Send works and the reply lands (beta.17)',{timeout:15000},async()=>{
 // Bryce, 1 Oct: "Home needs a Claude account" with Send off was the likely reason Codex-only testers left after a day.
 const calls=host({pickup:records,answer:{state:'ready',text:'Nothing needs you. Sidebar polish finished an hour ago.',looked:[],mentions:[],parts:[]}});
 onAccount(CODEX_HOME);
 const opened=[];
 mount({onConnectedAccounts:trigger=>opened.push(trigger.textContent)});
 await screen.findByRole('region',{name:'Catch up'});
 await waitFor(()=>assert.equal(document.querySelector('.dv-composer .dv-hint').textContent,'Finds the right thread or starts a new one · Answers with GPT-6-Luna on this project’s Codex account'));
 assert.equal(screen.queryByRole('button',{name:'Connect Claude'}),null,'nothing asks for a Claude account');
 assert.equal(screen.queryByText(/needs a Claude account/),null);
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 assert.equal(box.disabled,false);
 assert.equal(box.placeholder,'What would you like to do?');
 const send=screen.getByRole('button',{name:'Send'});
 assert.equal(send.disabled,true,'nothing typed');
 fireEvent.change(box,{target:{value:'where are we with the picker?'}});
 assert.equal(send.disabled,false,'a question goes to Home’s model, not only a bug: line');
 fireEvent.click(send);
 await waitFor(()=>assert.deepEqual(calls.filter(([op])=>op==='orchestrator_send').map(([,a])=>a.text),['where are we with the picker?']));
 await screen.findByText('Nothing needs you. Sidebar polish finished an hour ago.',{},{timeout:6000});
 assert.deepEqual(opened,[]);
});

test('a Codex call that fails says the host’s sentence and offers Send again, as a Claude one does (beta.17)',{timeout:15000},async()=>{
 const signedOut='This project’s Codex account is signed out. Sign in again in Settings, on Connected accounts, then send it again.';
 const calls=host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW,text:'where are we'},{id:'f',kind:'reply',created_at:NOW,state:'failed',detail:signedOut,parts:[]}]});
 onAccount(CODEX_HOME);
 mount();
 if(fixtureHasRequest)await openLatest();
 const said=await screen.findByRole('alert');
 assert.equal(said.textContent.replace(/\s*Send again$/,''),signedOut);
 fireEvent.click(screen.getByRole('button',{name:'Send again'}));
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='orchestrator_send'&&a.text==='where are we')));
});

test('the arrival line and the composer hint read the same on either account, with the account named (beta.17)',()=>{
 assert.equal(arrivalFailedLine(true),ARRIVAL_FAILED);
 assert.equal(arrivalFailedLine(false),'Couldn’t write a summary just now.');
 assert.equal(composerHint(true,'GPT-6-Luna',false,'codex'),'Finds the right thread or starts a new one · Answers with GPT-6-Luna on this project’s Codex account');
 assert.equal(composerHint(true,'Sonnet',false,'claude'),'Finds the right thread or starts a new one · Answers with Sonnet on this project’s Claude account');
 assert.equal(composerHint(true,'Sonnet'),'Finds the right thread or starts a new one · Answers with Sonnet on this project’s Claude account','an older host without provider reads as before');
 assert.equal(composerHint(true,'GPT-6-Luna',true,'codex'),'Finds the right thread or starts a new one','beside the agent chip the picker says it');
});

test('a Claude project keeps its composer: the hint says what Home runs on, and Send works (negative control for Codex)',async()=>{
 host({pickup:records});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command,result=await invoke(command,args);
  return op==='orchestrator_list'||op==='orchestrator_arrive'?{...result,home:{ready:true,provider:'claude',model:'Sonnet'}}:result;
 }};
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 await waitFor(()=>assert.equal(document.querySelector('.dv-composer .dv-hint').textContent,'Finds the right thread or starts a new one · Answers with Sonnet on this project’s Claude account'));
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 assert.equal(box.disabled,false);
 fireEvent.change(box,{target:{value:'where are we'}});
 assert.equal(screen.getByRole('button',{name:'Send'}).disabled,false);
 assert.equal(screen.queryByRole('button',{name:'Connect Claude'}),null);
});

test('a stopped run that left changes asks for a review, and only a clean one offers the other account (UX, item 4)',()=>{
 const left=item({...stopped,handoff:null,left_changes:true});
 assert.deepEqual(actionOf(left,false),{label:'Review the changes',focus:false});
 assert.deepEqual(actionOf(left,true),{label:'Review the changes',focus:false});
 assert.deepEqual(actionOf(item({...stopped,handoff:'Codex',left_changes:false}),false),{label:'Continue in Codex',focus:false});
 assert.deepEqual(actionOf(item({...stopped,handoff:null,left_changes:false}),false),{label:'Pick it up',focus:false});
 assert.deepEqual(actionOf(item({...stopped,handoff:null}),false),{label:'Pick it up',focus:false},'an older host without the field reads as before');
});

test('a job whose thread runs gets a Stop that stops it the palette’s way; a job that isn’t running gets none (UX H2)',{timeout:15000},async()=>{
 const calls=host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW-60,text:'two jobs'},{id:'r',kind:'reply',created_at:NOW-60,state:'ready',text:'Started both.',
  parts:[startPart({title:'Slow first load',state:'sent',channel_id:'n1'}),startPart({index:1,title:'Connectors docs',state:'sent',channel_id:'n2',thread_state:'finished'})]}]});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command,a=args.args??{};
  if(op==='tasks'){calls.push([op,a]);return [{id:'t-done',state:'ready_for_review',runs:[]},{id:'t-live',state:'working',runs:[{state:'running'}]}];}
  if(op==='task_stop'){calls.push([op,a]);return {};}
  return invoke(command,args);
 }};
 let changed=0;
 mount({onChanged:()=>{changed++;},conversations:[{id:'n1',name:'Slow first load',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',activity:{status:'running',running:true,step:'Reading',unread:0,since:Math.floor(Date.now()/1000)-60}},
  {id:'n2',name:'Connectors docs',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',activity:{status:null,running:false,step:null,unread:0}}]});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-job').length,2));
 const [live,done]=document.querySelectorAll('.dv-job');
 assert.equal(within(done).queryByRole('button',{name:/^Stop/}),null,'a finished job has nothing to stop');
 assert.equal(done.tagName,'BUTTON','and stays a plain link to its thread');
 const stop=within(live).getByRole('button',{name:'Stop Slow first load'});
 const before=changed;
 fireEvent.click(stop);
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='task_stop')));
 assert.deepEqual(calls.filter(([op])=>op==='tasks'||op==='task_stop'),[['tasks',{projectId:'proj',channelId:'n1'}],['task_stop',{projectId:'proj',channelId:'n1',taskId:'t-live'}]],
  'the thread’s working task only, as the palette’s Stop <thread> sends it');
 await waitFor(()=>assert.equal(within(live).getByRole('button',{name:'Stopping Slow first load'}).disabled,true));
 await waitFor(()=>assert.ok(changed>before,'the sidebar is asked again'));
 fireEvent.click(within(live).getByRole('button',{name:'Stopping Slow first load'}));
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.equal(calls.filter(([op])=>op==='task_stop').length,1,'never sent twice');
 const opened=[];
 cleanup();
 host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW-60,text:'one job'},{id:'r',kind:'reply',created_at:NOW-60,state:'ready',text:'Started it.',
  parts:[startPart({title:'Slow first load',state:'sent',channel_id:'n1'})]}]});
 mount({onOpen:id=>opened.push(id),conversations:[{id:'n1',name:'Slow first load',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',activity:{status:'running',running:true,step:'Reading',unread:0,since:Math.floor(Date.now()/1000)-60}}]});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.ok(document.querySelector('.dv-job .dv-job-open')));
 fireEvent.click(document.querySelector('.dv-job .dv-job-open'));
 assert.deepEqual(opened,['n1'],'the name still opens the thread');
});

test('a job Home filed carries Move, which opens the thread menu’s workstream list and moves it there (dd #10)',{timeout:15000},async()=>{
 host({pickup:records,entries:[{id:'y',kind:'you',created_at:NOW-60,text:'two jobs'},{id:'r',kind:'reply',created_at:NOW-60,state:'ready',text:'Filed in Website: Parallax.',
  parts:[startPart({title:'Parallax',state:'sent',channel_id:'n1'}),startPart({index:1,title:'Private notes',state:'sent',channel_id:'n2',thread_state:'finished'})]}]});
 const moved=[],opened=[];
 const threadMenu={workstreams:[{id:'w1',name:'Effects',count:1},{id:'w2',name:'Website',count:3}],filedUnder:()=> 'Effects',
  cannotMove:c=>c.visibility==='private'?'Private threads stay unfiled':null,rename(){},move:(c,target)=>moved.push([c.id,target]),archive(){},remove:async()=>{},stop:async()=>{}};
 mount({threadMenu,onOpen:id=>opened.push(id),conversations:[
  {id:'n1',name:'Parallax',visibility:'shared',project_id:'proj',profile_id:'p1',model:'m',activity:{status:null,running:false,step:null,unread:0}},
  {id:'n2',name:'Private notes',visibility:'private',project_id:'proj',profile_id:'p1',model:'m',activity:{status:null,running:false,step:null,unread:0}}]});
 if(fixtureHasRequest)await openLatest();
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-job').length,2));
 const [filed,hidden]=document.querySelectorAll('.dv-job');
 assert.equal(within(hidden).queryByRole('button',{name:/^Move/}),null,'a thread that can’t move gets no Move');
 const move=within(filed).getByRole('button',{name:'Move Parallax to another workstream'});
 assert.ok(move.classList.contains('dv-btn-quiet'),'quiet: a correction you reach for, so Stop and Send again stay the outlined ones (design review, 23 Sep)');
 fireEvent.keyDown(move,{key:'Enter'});
 const website=await screen.findByRole('menuitem',{name:/Website/});
 fireEvent.click(website);
 await waitFor(()=>assert.deepEqual(moved,[['n1',{ideaId:'w2',name:'Website'}]]));
 fireEvent.click(within(filed).getByRole('button',{name:/^Parallax/}));
 assert.deepEqual(opened,['n1'],'the name still opens the thread');
});

test('the head is a greeting in the 34px page title with its caption underneath, no pill, and Recent requests as a quiet icon',async()=>{
 host({pickup:{...records,since:Math.floor(Date.now()/1000)-2400}});
 mount({onCatchUp(){}});
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const head=pane.querySelector('.dv-now-head');
 const title=head.querySelector('h2');
 assert.equal(title.className,'dv-display-1');
 // A greeting by the hour (design, 8 Oct); the region keeps its name, Catch up.
 assert.match(title.textContent,/^(Still up|Good morning|Good afternoon|Good evening)\.$/);
 assert.equal(greeting(new Date(2026,9,8,9)),'Good morning.');assert.equal(greeting(new Date(2026,9,8,13)),'Good afternoon.');
 assert.equal(greeting(new Date(2026,9,8,21)),'Good evening.');assert.equal(greeting(new Date(2026,9,8,2)),'Still up.');
 assert.equal(title.nextElementSibling.tagName,'P','the caption is its own line under the title');
 assert.equal(title.nextElementSibling.className,'dv-caption');
 assert.equal(head.querySelectorAll('.dv-btn-ghost').length,0,'Go through these left the head (Bryce, 8 Oct); ⌘K still opens Catch up');
 const recent=within(head).getByRole('button',{name:'Recent requests'});
 assert.ok(recent.classList.contains('dv-btn-quiet'));
 assert.equal(recent.textContent,'','an icon, named for screen readers');
 const css=readFileSync(new URL('./Home.css',import.meta.url),'utf8');
 assert.doesNotMatch(css,/max-width:\s*800px/,'Home uses the 700px column every page uses');
 assert.doesNotMatch(css,/\.dv-measure\s*\{[^}]*max-width/,'no Home override of the column width');
});

test('the needs card enters only when it arrives while Home is on screen, never on opening Home',{timeout:15000},async()=>{
 host({pickup:records});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 let current=records;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command;
  return op==='project_pickup'?current:invoke(command,args);
 }};
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 await waitFor(()=>assert.ok(pane.querySelector('.dv-row.dv-open')));
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.equal(pane.querySelector('.dv-row.dv-open').hasAttribute('data-enter'),false,'the card already waiting doesn’t move on opening Home');
 const arrived=item({id:'c8',name:'Search index',moved:NOW,next:'Keep the old ranking?'});
 current={...records,items:[arrived,...records.items]};
 focusManager.setFocused(false);focusManager.setFocused(true);
 await waitFor(()=>assert.equal(pane.querySelector('.dv-row.dv-open .dv-row-name')?.textContent,'Search index'));
 assert.equal(pane.querySelector('.dv-row.dv-open').hasAttribute('data-enter'),true,'a card that turns up while you’re here enters once');
 focusManager.setFocused(undefined);
 const css=readFileSync(new URL('./Home.css',import.meta.url),'utf8');
 assert.match(css,/\.dv-row\.dv-open\[data-enter\]\s*\{\s*animation/);
 // Negative control: the rule before this played on every .dv-open, which is every mount.
 assert.doesNotMatch(css,/\.dv-now \.dv-row\.dv-open\s*\{\s*animation/);
 assert.doesNotMatch(css,/\[data-arrived\][^{]*\{[^}]*animation/,'the staggered rise after a history read is gone');
});

test('Recent requests read as plain words and say thread, not chat',()=>{
 assert.equal(plainSummary('**Slow first load** is still reading `rank.ts`.'),'Slow first load is still reading rank.ts.');
 assert.equal(plainSummary('A stray ** and a ` left over'),'A stray  and a  left over');
 const request=(last)=>({id:'q',title:'x',createdAt:NOW,entries:[{id:'y',kind:'you',created_at:NOW,text:'x'},last]});
 assert.equal(requestSummary(request({id:'r',kind:'reply',created_at:NOW,state:'ready',text:'**Connector picker** is ready.',parts:[]})),'Connector picker is ready.');
 assert.equal(requestSummary(request({id:'r',kind:'reply',created_at:NOW,state:'thinking',parts:[]})),'Finding the right threads…');
});

// ---------------------------------------------------------------- group 8: the agent and thinking for Home's jobs
const {hear,resolveLaunch,applyLaunch,launchLabel,accountFor,keptLine,CODEX_NO_CONNECTORS}=await import('./homeLaunch.ts');
const {accountsFrom}=await import('./models.ts');
const PROFILES=[{id:'me',provider:'claude',identity:'me@example.test',category:'personal',method:'subscription',plan:'max',name:'Personal'},
 {id:'cx',provider:'codex',identity:'me@example.test',category:'personal',method:'subscription',name:'ChatGPT'}];
const CODEX_MODELS=[{model:'gpt-6-sol',displayName:'GPT-6 Sol',supportedReasoningEfforts:['low','medium','high','xhigh'],defaultReasoningEffort:'medium'},
 {model:'gpt-6-luna',displayName:'GPT-6 Luna',supportedReasoningEfforts:['low','medium'],defaultReasoningEffort:'low'}];
const resolve=(text,extra={})=>resolveLaunch({accounts:accountsFrom(PROFILES),project:{profile_id:'me',model:'claude-opus-5-5'},picked:null,heard:hear(text),
 claudeVersion:'2.1.280',catalogFor:account=>account.provider==='codex'?CODEX_MODELS:undefined,...extra});

test('Home hears the agent, model and thinking you say, and only when the words hand it the work',()=>{
 assert.deepEqual({...hear('have Codex do search, think hard'),said:undefined},{agent:'codex',model:null,effort:'high',said:undefined,connectors:false});
 assert.equal(hear('Use Claude Code to tidy the sidebar').agent,'claude');
 assert.equal(hear('Codex, look at the failing test').agent,'codex');
 assert.equal(hear('fix the Codex login bug').agent,null,'naming Codex as the subject isn’t a choice');
 assert.equal(hear('have codex write it and claude review it').agent,null,'two agents in one message: Home doesn’t guess');
 assert.deepEqual([hear('ultrathink this').effort,hear('think really hard').effort,hear('think a little').effort,hear('go').effort],['max','xhigh','low',null]);
 assert.deepEqual([hear('use sonnet for this').model,hear('use sonnet for this').agent],['claude-sonnet-5-5','claude']);
 assert.deepEqual(['sonnet 5.5','sonnet 5','Sonnet 5 please'].map(words=>hear(`use ${words}`).model),['claude-sonnet-5-5','claude-sonnet-5','claude-sonnet-5']);
 assert.deepEqual([hear('gpt-6 luna please').model,hear('gpt-6 luna please').agent],['gpt-6-luna','codex']);
 // GPT-6.1 Sol (Codex, 29 Sep) isn't heard as GPT-6 Sol.
 assert.deepEqual(['gpt-6.1 sol','GPT-6.1-Sol','gpt 6.1 sol','gpt-6 sol'].map(words=>hear(`run it on ${words}`).model),['gpt-6.1-sol','gpt-6.1-sol','gpt-6.1-sol','gpt-6-sol']);
 assert.equal(hear('have codex do it with opus').model,null,'a Claude model with Codex named is dropped, not switched');
});

test('the picker says what Home understood, and holds Send only for a choice that can’t run',()=>{
 const said=resolve('have Codex do search, think hard');
 assert.deepEqual(said.choice,{profileId:'cx',model:'gpt-6-sol',effort:'high'});
 assert.equal(said.from,'words');
 assert.equal(said.heardLine,'From your message: Codex, high thinking');
 assert.equal(launchLabel(said,CODEX_MODELS),'Codex · GPT-6 Sol · High');
 // Behaviour review S1: the level is kept per project and model, so Home says so before you send.
 assert.equal(said.note,'High thinking becomes this project’s level for GPT-6 Sol.');
 assert.equal(resolve('have codex do it, think hard',{picked:{profileId:'cx',model:'gpt-6-sol',effort:'high'}}).note,null,'picked on the picker: no note');
 // Behaviour review S6: Codex has no connectors in Dervo.
 assert.equal(hear('catch me up from my email and calendar').connectors,true);
 assert.equal(resolve('have codex catch me up from my inbox').note,CODEX_NO_CONNECTORS);
 assert.equal(resolve('catch me up from my inbox').note,null,'Claude Code can use them');
 assert.equal(resolve('have codex tidy the sidebar').note,null);
 assert.equal(resolve('tidy the sidebar').choice,null,'nothing said, nothing chosen: the host sees no launch field');
 const luna=resolve('use gpt-6 luna, think really hard');
 assert.equal(luna.problem,null);
 assert.match(luna.note,/doesn’t offer Extra high/);
 assert.deepEqual(luna.choice,{profileId:'cx',model:'gpt-6-luna'},'a level the model lacks is said, not sent');
 const none=resolveLaunch({accounts:accountsFrom([PROFILES[0]]),project:{profile_id:'me',model:'claude-opus-5-5'},picked:null,heard:hear('have codex do it'),catalogFor:()=>undefined});
 assert.match(none.problem,/no Codex account is connected/);
 assert.equal(none.choice,null,'no fallback to the Claude account');
 const loading=resolve('have codex do it',{catalogFor:()=>undefined});
 assert.match(loading.problem,/Loading ChatGPT’s models/);
 const mine=resolve('have codex do it',{ignoreWords:true,picked:{profileId:'me',model:'claude-sonnet-5',effort:'low'}});
 assert.deepEqual(mine.choice,{profileId:'me',model:'claude-sonnet-5',effort:'low'},'Use my pick instead');
 const fable=resolve('use fable',{accounts:accountsFrom([{...PROFILES[0],plan:'pro'}])});
 assert.match(fable.problem,/may use paid credits/,'words don’t skip the Fable confirmation');
});

test('words pick the project’s account, then a subscription, and an API key only when it’s all there is, and say which',()=>{
 // Design review M3 (24 Sep): "use Claude Code" picked the first Claude account, an API key, and nothing said so.
 const key={id:'key',provider:'claude',identity:'me@example.test',category:'personal',method:'api_key',name:null};
 const team={id:'team',provider:'claude',identity:'me@work.test',category:'personal',method:'subscription',plan:'team',name:'Team'};
 const accounts=accountsFrom([key,PROFILES[0],PROFILES[1]]);
 assert.equal(accountFor(accounts,'claude','cx').id,'me','a subscription before an API key, even when the key is listed first');
 assert.equal(accountFor(accountsFrom([key,team,PROFILES[0]]),'claude','team').id,'team','the project’s own account first');
 assert.equal(accountFor(accountsFrom([key,PROFILES[1]]),'claude','cx').id,'key','an API key when it’s the only Claude account');
 assert.equal(accountFor(accountsFrom([PROFILES[1]]),'claude'),null);
 const onCodex={accounts,project:{profile_id:'cx',model:'gpt-6-sol'},picked:null,claudeVersion:'2.1.280',catalogFor:a=>a.provider==='codex'?CODEX_MODELS:undefined};
 const words=resolveLaunch({...onCodex,heard:hear('Use Claude Code on this, opus, think really hard')});
 assert.equal(words.account.id,'me');
 assert.equal(words.accountName,'Personal');
 assert.equal(launchLabel(words),'Claude Code (Personal) · Opus 5.5 · Extra high','the chip names the account when Claude Code has two');
 assert.equal(words.heardLine,'From your message: Claude Code (Personal), Opus 5.5, extra high thinking');
 const onlyKey=resolveLaunch({...onCodex,accounts:accountsFrom([key,PROFILES[1]]),heard:hear('Use Claude Code on this')});
 assert.equal(onlyKey.account.id,'key');
 assert.equal(onlyKey.accountName,null,'one Claude account: nothing to tell apart');
 assert.equal(launchLabel(onlyKey),'Claude Code · Opus 5.5');
 const picked=resolveLaunch({...onCodex,picked:{profileId:'key',model:'claude-opus-5-5'},heard:hear('tidy the sidebar')});
 assert.equal(launchLabel(picked),'Claude Code (API key) · Opus 5.5','a key you picked yourself is named too');
 assert.equal(launchLabel(resolve('have codex do it')),'Codex · GPT-6 Sol','one Codex account: no name');
});

test('a job added to a thread you already had says when that thread keeps its own agent or model',()=>{
 // Behaviour review S2: the choice sets up only threads Home makes.
 const launch={profile_id:'cx',provider:'codex',model:'gpt-6-sol'};
 const add={action:'add',state:'sent'};
 assert.equal(keptLine(add,launch,{profile_id:'me',model:'claude-opus-5'}),'Kept this thread’s own agent, Claude Code');
 assert.equal(keptLine(add,launch,{profile_id:'cx',model:'gpt-6-luna'}),'Kept this thread’s own model, GPT-6 Luna');
 assert.equal(keptLine(add,launch,{profile_id:'cx2',model:'gpt-6-sol'}),'Kept this thread’s own account');
 assert.equal(keptLine(add,launch,{profile_id:'cx',model:'gpt-6-sol'}),null,'the same: nothing to say');
 assert.equal(keptLine({action:'start',state:'sent'},launch,{profile_id:'me',model:'claude-opus-5'}),null,'a thread Home made starts on the choice');
 assert.equal(keptLine({action:'add',state:'failed'},launch,{profile_id:'me',model:'claude-opus-5'}),null);
 assert.equal(keptLine(add,null,{profile_id:'me',model:'claude-opus-5'}),null,'nothing chosen');
});

test('each job’s thread is set to the choice before the request, and a refusal fails the job with no fallback',async()=>{
 const calls=[];
 const invoke=async(op,args)=>{calls.push([op,args]);if(op==='binding')return {profile_id:'me',model:'claude-opus-5-5',provider:'claude'};
  if(op==='create_conversation')return {id:'new1'};if(op==='coding_request')return {id:'t1',state:'working'};return {};};
 const launch={profile_id:'cx',provider:'codex',model:'gpt-6-sol',effort:'high'};
 const outcome=await sendJob(startPart(),{projectId:'proj',profileId:'p1',clientId:'reply-l',launch},invoke);
 assert.equal(outcome.state,'sent');
 assert.deepEqual(calls.map(([op])=>op),['create_conversation','ideas_assign','binding','conversation_profile_set','coding_effort_set','coding_request']);
 assert.deepEqual(calls[3][1],{projectId:'proj',channelId:'new1',expectedProfileId:'me',profileId:'cx',expectedModel:'claude-opus-5-5',model:'gpt-6-sol'});
 calls.length=0;
 await applyLaunch({profile_id:'me',provider:'claude',model:'claude-sonnet-5'},{projectId:'proj',channelId:'c1'},invoke);
 assert.deepEqual(calls.map(([op])=>op),['binding','conversation_model_set'],'same account: the model alone, and no thinking when none was chosen');
 const refused=async(op,args)=>{if(op==='binding')return {profile_id:'me',model:'claude-opus-5-5'};if(op==='conversation_profile_set')throw new Error('Finish this conversation’s coding task before changing its account.');
  if(op==='coding_request')throw new Error('must not be sent');return {id:'new1'};};
 const failed=await sendJob(startPart(),{projectId:'proj',profileId:'p1',clientId:'reply-m',launch},refused);
 assert.equal(failed.state,'failed');
 assert.match(failed.detail,/^Couldn’t start it on Codex \(GPT-6 Sol\), so nothing was sent: Finish this/);
 // Behaviour review S2: a job for an existing thread leaves its account and model alone, so a busy one still queues.
 calls.length=0;
 const queued=await sendJob(addPart(),{projectId:'proj',profileId:'p1',clientId:'reply-n',launch},async(op,args)=>{calls.push([op,args]);return op==='coding_request'?{id:'q1',state:'queued'}:{};});
 assert.equal(queued.state,'sent');
 assert.deepEqual(calls.map(([op])=>op),['coding_request'],'no binding, profile, model or thinking change on an existing thread');
});

test('Home’s composer shows the picker, follows your words, and the send carries the choice',async()=>{
 const calls=host({pickup:records});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command;
  if(op==='profiles')return PROFILES;
  if(op==='projects')return [{id:'proj',profile_id:'me',model:'claude-opus-5-5'}];
  if(op==='usage_summary')return {accounts:[]};
  if(op==='claude_version')return {version:'2.1.280'};
  if(op==='model_status')return {state:'catalog',detail:'',models:CODEX_MODELS};
  return invoke(command,args);
 }};
 mount();
 const chip=await screen.findByTestId('home-launch');
 await waitFor(()=>assert.match(chip.textContent,/^Claude Code · Opus 5\.5/));
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:'have Codex do search, think hard'}});
 await waitFor(()=>assert.equal(chip.textContent,'Codex · GPT-6 Sol · High'));
 assert.equal(chip.dataset.from,'words');
 // Design review S2: beside the chip, the hint doesn't name Home's own model; the picker's foot does.
 assert.equal(document.querySelector('.dv-home-composer .dv-hint').textContent,'High thinking becomes this project’s level for GPT-6 Sol.');
 fireEvent.click(chip);
 const pop=await screen.findByRole('dialog',{name:/Agent, model and thinking/});
 assert.match(within(pop).getByText(/^New threads from this message open on this/).textContent,/Threads it adds to keep their own\. Thinking you pick or say becomes this project’s level for that model\./);
 // Design review S4: with no pick, ignoring the words is what the button says.
 assert.ok(within(pop).getByRole('button',{name:'Ignore my words'}));
 assert.equal(within(pop).queryByRole('button',{name:'Use my pick instead'}),null);
 fireEvent.keyDown(pop,{key:'Escape'});
 fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.deepEqual(calls.filter(([op])=>op==='orchestrator_send').map(([,a])=>a.launch),[{profileId:'cx',model:'gpt-6-sol',effort:'high'}]));
});

// ---------------------------------------------------------------- group 10: brought in from this Mac
test('Home says what came in from this Mac with one Undo, and only while nothing has been done in it',async()=>{
 const calls=host({pickup:records});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 let batch={batchId:'b1',at:Date.now()/1000-60,undone:false,projects:[{id:'proj',name:'tidepool',threads:9},{id:'p2',name:'recipe-box',threads:5}],threads:14,
  waiting:[{name:'billing-api',path:'/w/b'}],problems:[],undoable:true,blocked:null};
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command;
  if(op==='onboarding_batch')return batch;
  if(op==='onboarding_undo'){calls.push([op,args.args]);batch={...batch,undone:true};return batch;}
  return invoke(command,args);
 }};
 mount();
 const line=await screen.findByTestId('home-brought-in');
 assert.match(line.textContent,/^Brought in 2 projects and 14 threads from this Mac at .+\. No agent has run\./);
 // No Work or Personal step (Bryce, 25 Sep 2026): nothing is left behind, so an old batch's waiting list isn't said.
 assert.doesNotMatch(line.textContent,/stayed on this Mac|work account/);
 fireEvent.click(within(line).getByRole('button',{name:'Undo: remove every project brought in from this Mac'}));
 await waitFor(()=>assert.deepEqual(calls.filter(([op])=>op==='onboarding_undo').map(([,a])=>a),[{batchId:'b1'}]));
});

test('once something has been done in the batch, Home shows no Undo line',async()=>{
 host({pickup:records});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command;
  if(op==='onboarding_batch')return {batchId:'b1',at:0,undone:false,projects:[{id:'proj',name:'tidepool',threads:9}],threads:9,waiting:[],problems:[],undoable:false,blocked:'Something’s been done in tidepool.'};
  return invoke(command,args);
 }};
 mount();
 await screen.findByRole('region',{name:'Catch up'});
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(screen.queryByTestId('home-brought-in'),null);
});

// ---------------------------------------------------------------- beta.16.1, item 9: an offer has a Start button
const sessionsOffer=(extra={})=>({id:'offer1',action:'start',title:'Tester session identity',thread:null,workstream:'Beta release',state:'open',...extra});
const offering=(offers,extra={})=>[{id:'ask1',kind:'you',created_at:NOW-60,text:'Who do these sessions belong to?'},
 {id:'offer-reply',kind:'reply',created_at:NOW-50,state:'ready',text:'I can start a job to check whether usage sharing records anything per person. Want me to?',
  parts:[],looked:[],mentions:[],notes:[],changes:[],offers,...extra}];

test('only an open offer on a reply without its own question gets a Start',()=>{
 const reply=offering([sessionsOffer(),sessionsOffer({id:'o2',state:'held'}),sessionsOffer({id:'o3',state:'taken'}),sessionsOffer({id:'o4',state:'lapsed'})])[1];
 assert.deepEqual(liveOffers(reply).map(offer=>offer.id),['offer1']);
 assert.deepEqual(liveOffers({...reply,question:{ask:'Your rule says to ask first. Want me to start X?'}}),[],'the rule\'s tray answers it');
 assert.deepEqual(liveOffers({...reply,state:'thinking'}),[]);
 assert.equal(offerTitle(sessionsOffer()),'Tester session identity');
 assert.equal(offerTitle({id:'o5',action:'add',thread:{id:'c7',name:'Sidebar polish'},state:'open'}),'Sidebar polish');
 assert.equal(offerLine(sessionsOffer()),'Offered · in Beta release');
 assert.equal(offerLine({id:'o5',action:'add',thread:{id:'c7',name:'Sidebar polish'},state:'open'}),'Offered · adds to this thread');
});

test('Start on an offer sends only its id, and the host sends the job it wrote, never the page or a hand-off',{timeout:15000},async()=>{
 const handed=[];
 const calls=host({pickup:records,entries:offering([sessionsOffer()]),offer:(a,chat)=>({...chat,entries:[
  ...chat.entries.map(e=>e.id===a.entryId?{...e,offers:e.offers.map(o=>o.id===a.offerId?{...o,state:'taken',taken_by:'started-reply'}:o)}:e),
  {id:'click-you',kind:'you',created_at:Date.now()/1000,text:'Start Tester session identity',request_id:'fixture-request'},
  {id:'started-reply',kind:'reply',created_at:Date.now()/1000,request_id:'fixture-request',state:'ready',text:'Filed in Beta release: Tester session identity.',
   queued:true,clicked:true,looked:[],mentions:[],notes:[],changes:[],parts:[startPart({title:'Tester session identity',text:'Home routed this request to this chat.'})]}]})});
 mount({onHandOff:(...args)=>handed.push(args)});
 await openLatest();
 const start=await screen.findByRole('button',{name:'Start Tester session identity'});
 const row=start.closest('.dv-job');
 assert.equal(row.dataset.state,'offered');
 assert.match(row.textContent,/Tester session identityOffered · in Beta release/);
 fireEvent.click(start);
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='host_sent')),{timeout:6000});
 const [,sent]=calls.find(([op])=>op==='orchestrator_offer');
 assert.deepEqual(Object.keys(sent).sort(),['clientId','entryId','offerId','profileId','projectId'],'an id, never the offer\'s words');
 assert.equal(sent.offerId,'offer1');
 assert.equal(calls.filter(([op])=>op==='coding_request'||op==='create_conversation').length,0,'the host sends it as your click (backend: test_a_start_click_goes_out_as_a_click_and_a_typed_yes_does_not)');
 await waitFor(()=>assert.ok(document.querySelector('.dv-job[data-state="sent"]')),{timeout:6000});
 assert.deepEqual(handed,[],'nothing goes through onHandOff, which sends its text as your message');
 await waitFor(()=>assert.equal(screen.queryByRole('button',{name:'Start Tester session identity'}),null));
});

test('a refused Start says why in the composer\'s alert',{timeout:15000},async()=>{
 host({pickup:records,entries:offering([sessionsOffer()]),offer:()=>new Error('That offer lapsed when you sent another message. Ask Home again if you still want it.')});
 mount();
 await openLatest();
 fireEvent.click(await screen.findByRole('button',{name:'Start Tester session identity'}));
 await screen.findByText(/That offer lapsed when you sent another message/);
});

/** Polls ``check`` inside act until it passes, as the beta.16.1 test did. A check that compares a DOM node fails with
 * node:assert describing the node through React's own fields, and polling one of those ran the run out of memory
 * (tests review, 9 Oct), so these checks compare booleans, counts and text, never nodes. */
async function until(check,tries=120){
 const {act}=await import('@testing-library/react');
 for(let n=0;n<tries;n++){try{return check();}catch{/* not yet */}await act(()=>new Promise(resolve=>setTimeout(resolve,25)));}
 return check();
}
const gone=selector=>document.querySelector(selector)===null;

test('beta.16.1: Home lists an agent’s reminders on their day, quoted as the agent’s, and a row only opens its thread',async()=>{
 const reminder={id:'b'.repeat(32),projectId:'proj',channelId:'trial',taskId:'t1',thread:'Apple trial',project:null,asker:'Codex',mine:false,
  text:'Check whether the trial ended, then decide on the paid plan.',on:'2000-01-01',createdAt:1,notified:true};
 const calls=host({pickup:records,listFails:true,reminders:{due:[reminder]}});
 const opened=[];
 mount({onOpen:id=>opened.push(id)});
 // Since 9 Oct under Reminders, right after Waiting on you, and counted on its own.
 const list=await screen.findByRole('region',{name:'Reminders'});
 const waiting=screen.getByRole('region',{name:'Waiting on you'});
 const open=within(list).getByRole('button',{name:'Open Apple trial: Codex’s reminder'});
 const row=open.closest('article');
 assert.equal(open.textContent,'Apple trial','named as the threads above it are; the bell says it’s a reminder');
 assert.match(row.textContent,/“Check whether the trial ended, then decide on the paid plan\.”/,'the agent’s words, quoted');
 assert.match(row.querySelector('.dv-row-meta').textContent,/^Codex’s reminder·?Due /,'labelled as theirs, with the day it was due');
 assert.equal(!!(waiting.compareDocumentPosition(list)&4),true,'after what waits on you');
 assert.equal(list.querySelector('.dv-now-count').textContent,'1');
 assert.equal(waiting.querySelector('.dv-now-count').textContent,'3','Waiting on you counts the threads that wait on you, as the sidebar does');
 const before=calls.length;
 fireEvent.click(open);
 assert.deepEqual(opened,['trial']);
 assert.deepEqual(calls.slice(before),[],'opening it sends nothing and starts nothing, and an agent’s reminder waits for Done');
 fireEvent.click(within(row).getByRole('button',{name:'Done: Codex’s reminder on #Apple trial'}));
 await until(()=>assert.equal(gone(`[data-reminder-id="${'b'.repeat(32)}"]`),true));
 assert.deepEqual(calls.find(([op])=>op==='reminder_done')[1],{projectId:'proj',profileId:'p1',reminderId:'b'.repeat(32)},'Done takes it off Home');
 assert.equal(gone('[data-bucket="reminders"]'),true,'none left, no list');
 assert.ok(calls.every(([op])=>!['coding_request','orchestrator_send','conversation_send','task_question_answer'].includes(op)),'a reminder never sends or starts anything');
 // Never through the path that sends words as your message.
 assert.doesNotMatch(readFileSync(new URL('./Reminders.tsx',import.meta.url),'utf8'),/onHandOff|handToConversation\(/);
});

// ---------------------------------------------------------------- 9 Oct: Home is reminders, suggestions and what needs you
const {aheadLine,tomorrow,isoDay,reminderNote,reminderWho}=await import('./reminderState.ts');
const {suggestedBy}=await import('./HomeSuggestions.tsx');
const mineFor=(channelId,on,extra={})=>({id:'c'.repeat(32),projectId:'proj',channelId,taskId:null,thread:'Connector picker',project:null,asker:null,mine:true,
 text:'Come back to this thread',on,createdAt:NOW,notified:false,...extra});
const remindStatus=card=>card.querySelector('.dv-home-remind [role="status"]').textContent;

test('your own reminder reads as yours, on its row and in its notification, and says its day',()=>{
 const now=new Date(2026,9,9,21,30);
 assert.equal(tomorrow(now),'2026-10-10');
 assert.equal(tomorrow(new Date(2026,11,31,23,59)),'2027-01-01','the year turns over');
 assert.equal(isoDay(now),'2026-10-09');
 assert.equal(aheadLine('2026-10-10',now),'tomorrow');
 assert.match(aheadLine('2026-10-13',now),/Tue|13/,'later days say their weekday and date');
 const mine=mineFor('c2','2026-10-09');
 assert.equal(reminderWho(mine),'Your reminder');
 assert.equal(reminderWho({...mine,mine:false,asker:'Claude'}),'Claude’s reminder');
 assert.deepEqual(reminderNote(mine),{id:`reminder:${'c'.repeat(32)}`,title:'Your reminder: #Connector picker',body:'Come back to this thread',projectId:'proj',channelId:'c2'},
  'your own words, not quoted as an agent’s');
});

test('Remind me tomorrow on the card saves a line that’s still true tomorrow, says it’s set, keeps focus, and Undo takes it back',async()=>{
 const calls=host({pickup:records,listFails:true,reminders:{due:[]}});
 mount();
 const waiting=await screen.findByRole('region',{name:'Waiting on you'});
 const card=waiting.querySelector('.dv-row.dv-open');
 assert.equal(card.dataset.row,'c1','the card that asks you');
 const remind=await within(card).findByRole('button',{name:'Remind me tomorrow'});
 remind.focus();
 fireEvent.click(remind);
 await until(()=>assert.equal(remindStatus(card),'Reminder set for tomorrow'));
 assert.deepEqual(calls.find(([op])=>op==='reminder_add')[1],{projectId:'proj',profileId:'p1',channelId:'c1',on:tomorrow(),text:'Come back to this thread'},
  'not how the thread stands today, which may be over by then');
 assert.ok(calls.every(([op])=>!['coding_request','orchestrator_send','conversation_send','task_question_answer','task_thread_start'].includes(op)),'it saves a reminder and nothing else');
 assert.equal(screen.getByRole('region',{name:'Waiting on you'}).querySelector('.dv-row.dv-open')!==null,true,'the card still waits on you today');
 const undo=within(card).getByRole('button',{name:'Undo the reminder for tomorrow'});
 assert.equal(document.activeElement===undo,true,'focus stays on the button, which now says Undo');
 assert.equal(undo.textContent,'Undo','and doesn’t say Undoing while nothing is being undone');
 await until(()=>assert.equal(undo.getAttribute('aria-disabled'),null));
 fireEvent.click(undo);
 await until(()=>assert.equal(remindStatus(card),''));
 assert.equal(document.activeElement===within(card).getByRole('button',{name:'Remind me tomorrow'}),true,'and stays there after Undo');
 assert.equal(calls.find(([op])=>op==='reminder_done')[1].reminderId.length,32);
 assert.equal(calls.saved.upcoming.length,0);
});

test('Remind me tomorrow moves yours that’s due today, Undo brings it back, an agent’s stays, and a refusal is said on the card',async()=>{
 const today=isoDay(new Date());
 const yours=mineFor('c1',today,{id:'d'.repeat(32),thread:'Ideas storage'});
 const agents=mineFor('c1',today,{id:'a'.repeat(32),thread:'Ideas storage',mine:false,asker:'Claude',taskId:'t1',text:'Check the renewal first.'});
 const calls=host({pickup:records,listFails:true,reminders:{due:[yours,agents]}});
 mount();
 const list=await screen.findByRole('region',{name:'Reminders'});
 assert.deepEqual([...list.querySelectorAll('article')].map(row=>row.dataset.reminderId),['a'.repeat(32)],
  'yours on a thread that already waits on you isn’t a second row; the agent’s is');
 const card=screen.getByRole('region',{name:'Waiting on you'}).querySelector('.dv-row.dv-open');
 fireEvent.click(within(card).getByRole('button',{name:'Remind me tomorrow'}));
 await until(()=>assert.equal(remindStatus(card),'Reminder set for tomorrow'));
 await until(()=>assert.deepEqual(calls.filter(([op])=>op==='reminder_done').map(([,a])=>a.reminderId),['d'.repeat(32)]));
 assert.deepEqual(calls.saved.due.map(row=>row.id),['a'.repeat(32)],'yours moved to tomorrow; the agent’s isn’t yours to move');
 assert.deepEqual(calls.saved.upcoming.map(row=>row.on),[tomorrow()]);
 const undo=within(card).getByRole('button',{name:'Undo the reminder for tomorrow'});
 await until(()=>assert.equal(undo.getAttribute('aria-disabled'),null));
 fireEvent.click(undo);
 await until(()=>assert.equal(remindStatus(card),''));
 await until(()=>assert.deepEqual(calls.saved.due.map(row=>row.id).sort(),['a'.repeat(32),'d'.repeat(32)]));
 assert.deepEqual(calls.filter(([op])=>op==='reminder_add').map(([,a])=>[a.channelId,a.on,a.text]),
  [['c1',tomorrow(),'Come back to this thread'],['c1',today,'Come back to this thread']],'Undo puts back today’s, the same one by its day');
 assert.deepEqual(calls.saved.upcoming,[]);
 // The host refuses: its own sentence, where you pressed it. Tauri rejects with the text itself, not an Error.
 cleanup();
 host({pickup:records,listFails:true,reminders:{due:[]}});
 const base=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__.invoke=async(command,args={})=>args.operation==='reminder_add'?Promise.reject('This project already has 50 reminders waiting. Mark some done first.'):base(command,args);
 mount();
 const again=await screen.findByRole('region',{name:'Waiting on you'});
 fireEvent.click(within(again.querySelector('.dv-row.dv-open')).getByRole('button',{name:'Remind me tomorrow'}));
 const alert=await until(()=>{const node=again.querySelector('.dv-home-failure[role="alert"]');assert.equal(node!==null,true);return node;});
 assert.match(alert.textContent,/^This project already has 50 reminders waiting\. Mark some done first\./);
 fireEvent.click(within(alert).getByRole('button',{name:'Dismiss'}));
 await until(()=>assert.equal(gone('.dv-home-failure'),true));
});

test('yours on a thread that waits on you is said on its row, not listed twice, and opening the thread from Home settles it',async()=>{
 const yours=mineFor('c2',isoDay(new Date()),{id:'d'.repeat(32)});
 const calls=host({pickup:records,listFails:true,reminders:{due:[yours]}});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const row=await until(()=>{const node=pane.querySelector('[data-row="c2"]');assert.match(node.querySelector('.dv-row-meta').textContent,/Reminder set for today$/);return node;});
 assert.equal(gone('[data-bucket="reminders"]'),true,'no second row for the same thread');
 assert.equal(within(pane).getByRole('region',{name:'Waiting on you'}).querySelector('.dv-now-count').textContent,'3','and it isn’t counted twice');
 fireEvent.click(within(row).getByRole('button',{name:'Connector picker'}));
 await until(()=>assert.deepEqual(calls.filter(([op])=>op==='reminder_done').map(([,a])=>a.reminderId),['d'.repeat(32)]),'it asked you to come back to this thread, and you did');
 assert.deepEqual(calls.saved.due,[]);
});

test('a reminder still to come shows on its thread’s row, and the panel’s bell sets and takes back yours',async()=>{
 const calls=host({pickup:records,listFails:true,reminders:{due:[],upcoming:[mineFor('c2',tomorrow())]}});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const row=await until(()=>{const node=pane.querySelector('[data-row="c2"]');assert.match(node.querySelector('.dv-row-meta').textContent,/Reminder set for tomorrow$/);return node;});
 fireEvent.click(within(row).getByRole('button',{name:'Connector picker'}));
 const panel=await screen.findByRole('region',{name:'Thread: Connector picker'});
 const bell=within(panel).getByRole('button',{name:'Remind me tomorrow'});
 assert.equal(bell.getAttribute('aria-pressed'),'true','yours for tomorrow is set');
 assert.match(bell.title,/^Reminder set for tomorrow\. Press to take it back\.$/);
 fireEvent.click(bell);
 const toggle=()=>document.querySelector('[data-testid="home-panel"] .dv-home-remind-toggle');
 await until(()=>assert.equal(toggle().getAttribute('aria-pressed'),'false'));
 assert.equal(calls.find(([op])=>op==='reminder_done')[1].reminderId,'c'.repeat(32));
 await until(()=>assert.equal(toggle().getAttribute('aria-disabled'),null));
 fireEvent.click(toggle());
 await until(()=>assert.equal(toggle().getAttribute('aria-pressed'),'true'));
 assert.deepEqual(calls.filter(([op])=>op==='reminder_add').map(([,a])=>[a.channelId,a.on,a.text]),[['c2',tomorrow(),'Come back to this thread']]);
});

const suggestion=(extra={})=>({id:'e'.repeat(32),title:'Add tests for the parser',purpose:'The fix has no test for empty input.',channelId:'c3',thread:'Live board',taskId:'t-c3',
 provider:'claude',createdAt:Math.floor(Date.now()/1000)-600,...extra});

test('Suggested lists what agents proposed that nobody started, after Working; a row opens its task, Dismiss is the thread’s own, and nothing starts',async()=>{
 const pickup={...records,items:[item(),running,answered]};
 const calls=host({pickup,listFails:true,reminders:{due:[]},moreSuggested:2,
  suggestions:[suggestion(),suggestion({id:'f'.repeat(32),title:'Write the release note',purpose:'',provider:'codex',channelId:'c5',thread:'Sidebar polish',taskId:'t-c5'})]});
 const opened=[],tasks=[];
 mount({onOpen:id=>opened.push(id),onOpenTask:(channel,task)=>tasks.push([channel,task])});
 const list=await screen.findByRole('region',{name:'Suggested'});
 const pane=screen.getByRole('region',{name:'Catch up'});
 assert.equal(!!(within(pane).getByRole('region',{name:'Working'}).compareDocumentPosition(list)&4),true,'after Working');
 assert.equal(!!(list.compareDocumentPosition(within(pane).getByRole('region',{name:'Resolved'}))&4),true,'before what’s resolved');
 assert.equal(list.querySelector('.dv-now-count').textContent,'4','two shown and two more');
 assert.equal(list.querySelector('.dv-now-more').textContent,'2 more suggestions are in their threads.');
 const rows=[...list.querySelectorAll('article')];
 assert.match(rows[0].textContent,/^Add tests for the parserThe fix has no test for empty input\.Not startedSuggested by Claude in #Live board/);
 assert.match(rows[1].querySelector('.dv-row-meta').textContent,/^Not startedSuggested by Codex in #Sidebar polish/);
 assert.equal(rows[1].querySelector('.dv-row-end').textContent,'Dismiss','the time is in the line; Dismiss stands alone, as Done does');
 assert.equal(suggestedBy({provider:null,thread:'X'}),'Suggested in #X');
 fireEvent.click(within(rows[0]).getByRole('button',{name:'Open #Live board to read and start: Add tests for the parser'}));
 assert.deepEqual([tasks,opened],[[['c3','t-c3']],[]],'its task, where Start shows the whole request');
 fireEvent.click(within(rows[1]).getByRole('button',{name:'Dismiss the suggestion Write the release note'}));
 await until(()=>assert.equal(document.querySelectorAll('[data-bucket="suggested"] article').length,1));
 assert.deepEqual(calls.find(([op])=>op==='task_thread_dismiss')[1],{projectId:'proj',channelId:'c5',taskId:'t-c5',proposalId:'f'.repeat(32)});
 assert.ok(calls.every(([op])=>!['task_thread_start','coding_request','orchestrator_send','create_conversation'].includes(op)),'Home never starts one');
 await until(()=>assert.equal(document.querySelector('[data-bucket="suggested"] .dv-btn').disabled,false));
 fireEvent.click(document.querySelector('[data-bucket="suggested"] .dv-btn'));
 await until(()=>assert.equal(document.querySelectorAll('[data-bucket="suggested"] article').length,0));
});

test('what needs you leads Home: Waiting on you, Reminders, Working and Suggested come before the arrival summary and the push line, and the prompt is a one-line shortcut',async()=>{
 const arrivalAt=Date.now()/1000;
 const reminder=mineFor('c9',isoDay(new Date()),{thread:'Release notes',mine:false,asker:'Codex',taskId:'t9',text:'Send the notes today.'});
 host({pickup:{...records,since:arrivalAt-3600,items:[item(),running,answered]},entries:[{id:'arr',kind:'reply',created_at:arrivalAt,state:'ready',arrival:true,text:'Morning. One thing needs you.'}],
  push:{git:true,branch:'feature',remote:'origin',upstream:'origin/feature',ahead:2,behind:0,can_push:true,commits:[]},reminders:{due:[reminder]},suggestions:[suggestion()]});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const arrival=await until(()=>{const node=pane.querySelector('.dv-home-arrival');assert.equal(node!==null,true);return node;});
 const suggested=await within(pane).findByRole('region',{name:'Suggested'});
 const git=await within(pane).findByTestId('home-push');
 const order=[pane.querySelector('.dv-now-lead'),within(pane).getByRole('region',{name:'Waiting on you'}),within(pane).getByRole('region',{name:'Reminders'}),
  within(pane).getByRole('region',{name:'Working'}),suggested,arrival,git,within(pane).getByRole('region',{name:'Resolved'})];
 assert.deepEqual(order.slice(1).map((node,index)=>!!(order[index].compareDocumentPosition(node)&4)),order.slice(1).map(()=>true),
  'the lead, what waits on you, reminders, live work, suggestions, then the summary, the push line and finished work');
 assert.equal(document.querySelector('.dv-home-composer').dataset.view,'home','the prompt is still there, as Home’s shortcut');
 assert.match(readFileSync(new URL('./Home.css',import.meta.url),'utf8'),/\.dv-home-composer\[data-view="home"\] textarea\.dv-field \{ min-height: calc\(var\(--dv-fs-read\) \* var\(--dv-lh-read\)\); \}/);
});


// ---------------------------------------------------------------- 16.2 (#30): the useful row, its peek and the thread panel
const {rowSentence,rowMeta,peekFacts,paneOrder,stepThread,nextNeeding,PEEK_AFTER}=await import('./Home.tsx');
const {peekPlace,panelTask,lastReply}=await import('./HomePanel.tsx');

test('a row says what the thread did, then what it wants, its files and checks; rust only on the one thing that asks you',()=>{
 const review=item({id:'r',kind:'review',question:false,result:'Opening and revealing files works on Windows.',files:['a.py','b.py'],files_count:6,checks:'passed',checks_line:'Checks passed.',unchecked:'Not run on Windows.',next:'Your review, then commit.'});
 assert.equal(rowSentence(review),'Opening and revealing files works on Windows.','the task guide’s goal, no model call');
 assert.equal(rowSentence(item({question:false})),'Finished. 2 files changed. Checks passed.','a host older than 16.2: the status line it had');
 assert.equal(rowSentence(running),null,'a running row without a goal puts its step on the quiet line');
 assert.deepEqual(rowMeta(review),[{text:'Wants your review',tone:'ask'},{text:'6 files',tone:null},{text:'Checks passed',tone:'ok'}]);
 assert.deepEqual(rowMeta(item(),true),[{text:'Asks you one thing',tone:'accent'},{text:'No files changed',tone:null},{text:'Checks passed',tone:'ok'}]);
 assert.equal(rowMeta(item(),false)[0].tone,'ask','a second question keeps ink: rust is for the first');
 assert.deepEqual(rowMeta(item({kind:'permission',question:false,checks:null}),true),[{text:'Wants your permission',tone:'accent'}]);
 assert.deepEqual(rowMeta({...running,files_count:3},false,'Editing ChatMotion.css'),[{text:'Now: editing ChatMotion.css',tone:'live'},{text:'3 files',tone:null}]);
 assert.deepEqual(rowMeta({...stopped,handoff:null,files_count:1}),[{text:'Pick it up',tone:'ask'},{text:'1 file, unfinished',tone:null}]);
 assert.deepEqual(rowMeta(item({kind:'approved',needs:false,question:false,checks:null,line:'Approved and committed.',files_count:2})),[{text:'2 files',tone:null},{text:'Committed',tone:'ok'}]);
 assert.deepEqual(rowMeta(item({kind:'failed',needs:false,question:false,checks:'failed',account:'Codex'})),[{text:'On Codex',tone:null},{text:'A check failed',tone:'warn'}]);
 assert.deepEqual(peekFacts(review),[{label:'You asked',text:'Store ideas per project, with the idea as the group key.'},{label:'Changed',files:['a.py','b.py'],more:4},
  {label:'Checks',text:'Checks passed.'},{label:'Not checked',text:'Not run on Windows.'},{label:'Next',text:'Your review, then commit.'}]);
 assert.equal(peekFacts(item()).at(-1).text,'“Should an archived idea keep its threads?”','a question reads as the agent’s words');
 assert.equal(PEEK_AFTER,350,'the mock’s wait before a hover peek');
});

test('Up and Down walk the rows in the order Catch up draws them, and acting opens the next thing that needs you',()=>{
 const quiet=item({id:'q',kind:'answered',needs:false,question:false});
 const order=paneOrder([finished,running,item(),quiet]);
 assert.deepEqual(order.map(row=>row.id),['c1','c3','c4','q'],'Waiting on you (the question, then the review), Working, then Resolved');
 assert.equal(stepThread(order,'c1',1),'c3');
 assert.equal(stepThread(order,'c1',-1),'q','it wraps');
 assert.equal(stepThread(order,'gone',1),'c1','a thread no longer listed starts from the top');
 assert.equal(nextNeeding(order,'c1'),'c3','a review still needs you; running and answered don’t');
 assert.equal(nextNeeding([quiet,running],null),null);
});

test('the peek sits beside a row when Home has room, under it otherwise, and flips above near the bottom',()=>{
 const row={top:300,bottom:360,left:600,right:1300};
 assert.deepEqual(peekPlace(row,{left:0,right:1800},200,900),{top:300,left:1308,width:380},'room on the right');
 assert.deepEqual(peekPlace({...row,left:500,right:1300},{left:0,right:1400},200,900),{top:300,left:112,width:380},'room on the left');
 assert.deepEqual(peekPlace(row,{left:320,right:1512},200,900),{top:366,left:920,width:380},'no room either side: under the row');
 assert.equal(peekPlace({...row,top:700,bottom:760},{left:320,right:1512},200,900).top,494,'near the bottom it goes above');
});

test('the panel reads the thread’s own newest task and its last reply as plain words',()=>{
 const helper={id:'h',origin:'collaboration',messages:[]},mine={id:'t1',origin:'conversation',messages:[{role:'assistant',text:'## Changed\n- Added **retry** to `upload.py`.'}]};
 assert.equal(panelTask([mine,helper],null).id,'t1','a helper’s task is never the thread’s own');
 assert.equal(panelTask([mine,{...mine,id:'t2'}],'t1').id,'t1','the task the pickup named wins');
 assert.equal(lastReply(mine),'Changed Added retry to upload.py.');
 assert.equal(lastReply({messages:[{role:'user',text:'hi'}]}),null);
});

test('a row opens its thread in the panel: the decision bar’s own Approve, then the next thing that needs you; Up, Down and Esc',async()=>{
 const recent=Math.floor(Date.now()/1000);
 const review=item({id:'c3',name:'Live board',question:false,moved:recent-600,task_id:'t-c3',result:'Warns when two runs edit the same file.',files:['board.py','test_board.py'],files_count:2,
  checks:'passed',checks_line:'Checks passed.',unchecked:'Not tried with three runs.',next:'Your review, then commit.',next_by:'dervo'});
 const pickup={...records,since:recent-2400,items:[item({moved:recent-300,task_id:'t-c1',result:'Stores ideas per project.'}),review]};
 const calls=host({pickup,listFails:true});
 const task={id:'t-c3',origin:'conversation',state:'ready_for_review',session_id:'s',scope_version:1,files:[],argv:[],check_profile:'python-unittest',binding:{provider:'claude'},tokens:[],
  runs:[{id:'r1',state:'completed'}],messages:[{role:'user',text:'Warn me.',created_at:recent-900},{role:'assistant',text:'Added the warning and **two** tests.',created_at:recent-600}],
  completion:{at:recent-600,attempt:1,run_id:'r1',review_phase:null,changed_files:['board.py','test_board.py'],test_exit_status:0,check_profile:'python-unittest',human_approved:false}};
 const attempt={number:1,kind:'change',state:'verified',argv:[],diff:{'board.py':'+x'},before:{},after:{},test:{argv:[],stdout:'',stderr:'',exit_status:0,stopped:null}};
 const base=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__.invoke=async(command,args={})=>{
  const op=args.operation,a=args.args??{};
  if(op==='tasks'){calls.push([op,a]);return a.channelId==='c3'?[task]:[];}
  if(op==='task_detail'){calls.push([op,a]);return {task,git:true,rebound:false,retry:{available:false},evidence:{attempts:[attempt]}};}
  if(op==='task_review'){calls.push([op,a]);task.state='idle';pickup.items=[pickup.items[0],{...review,kind:'approved',needs:false,line:'Approved and committed.'}];return {};}
  return base(command,args);
 };
 const opened=[];
 mount({onOpen:id=>opened.push(id)});
 const pane=await screen.findByRole('region',{name:'Catch up'});
 fireEvent.click(await within(pane).findByRole('button',{name:'Live board'}));
 const panel=await screen.findByRole('region',{name:'Thread: Live board'});
 assert.equal(screen.getByRole('region',{name:'Catch up'}).closest('.dv-home-side')?.tagName,'ASIDE','Catch up stays, on the right');
 assert.equal(document.querySelector('#dervo-home-ask'),null,'Home’s own composer gives way to the thread’s');
 const side=document.querySelector('.dv-home-side [data-row="c3"]');
 assert.ok(side.hasAttribute('data-selected'),'the open thread’s row is marked');
 assert.equal(side.querySelector('button').getAttribute('aria-current'),'true');
 assert.match(panel.querySelector('.dv-home-panel-did').textContent,/^Warns when two runs edit the same file\.$/);
 assert.match(panel.textContent,/Changedboard\.pytest_board\.py/);
 assert.match(panel.textContent,/Not checkedNot tried with three runs\./);
 await within(panel).findByText('Added the warning and two tests.');
 const approve=await within(panel).findByRole('button',{name:'Approve and commit'});
 assert.ok(within(panel).getByRole('group',{name:'Your decision'}).contains(approve),'the thread’s own decision bar, not a second approve path');
 fireEvent.click(approve);
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='task_review'&&a.decision==='approved'&&a.attemptNumber===1&&a.channelId==='c3'&&a.taskId==='t-c3')));
 await screen.findByRole('region',{name:'Thread: Ideas storage'},{timeout:3000});
 assert.match(document.querySelector('.dv-home-panel-notice').textContent,/^Next: Ideas storage$/,'acting on one opens the next thing that needs you');
 fireEvent.keyDown(document.body,{key:'ArrowDown'});
 await screen.findByRole('region',{name:'Thread: Live board'});
 fireEvent.keyDown(document.body,{key:'k'});
 await screen.findByRole('region',{name:'Thread: Ideas storage'});
 const box=screen.getByRole('textbox',{name:'Reply in Ideas storage'});
 fireEvent.keyDown(box,{key:'ArrowDown'});
 assert.ok(screen.getByRole('region',{name:'Thread: Ideas storage'}),'arrows in the composer stay the composer’s');
 fireEvent.click(within(screen.getByRole('region',{name:'Thread: Ideas storage'})).getByRole('button',{name:'Open thread'}));
 assert.deepEqual(opened,['c1'],'Open thread still opens the full thread');
 fireEvent.keyDown(document.body,{key:'Escape'});
 await waitFor(()=>assert.equal(document.querySelector('[data-testid="home-panel"]'),null));
 assert.ok(document.querySelector('#dervo-home-ask'),'Esc goes back to Home');
});

test('Answer on the card opens the thread with its cursor in the composer, and what you write goes to that thread',async()=>{
 const recent=Math.floor(Date.now()/1000);
 const calls=host({pickup:{...records,since:recent-2400,items:[item({moved:recent-300})]},listFails:true});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 fireEvent.click(await within(pane).findByRole('button',{name:'Answer'}));
 const panel=await screen.findByRole('region',{name:'Thread: Ideas storage'});
 assert.match(within(panel).getByRole('group',{name:'Asks you'}).textContent,/Should an archived idea keep its threads\?/);
 const box=within(panel).getByRole('textbox',{name:'Reply in Ideas storage'});
 await waitFor(()=>assert.equal(document.activeElement,box));
 fireEvent.change(box,{target:{value:'Yes, keep them.'}});
 fireEvent.keyDown(box,{key:'Enter'});
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='coding_request'&&a.channelId==='c1'&&a.projectId==='proj'&&a.text==='Yes, keep them.')),'the thread composer’s own request, to this thread');
 assert.equal(calls.filter(([op])=>op==='orchestrator_send').length,0,'nothing went to Home');
 localStorage.clear();
});

// dd #21: after one Answer, every thread you stepped to took the cursor into its reply box, so Up and Down stopped there.
test('the cursor goes into the reply box only for the thread you pressed Answer on, so Up and Down keep going after it',async()=>{
 const recent=Math.floor(Date.now()/1000);
 host({pickup:{...records,since:recent-2400,items:[item({moved:recent-300}),{...finished,moved:recent-600}]},listFails:true});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 fireEvent.click(await within(pane).findByRole('button',{name:'Answer'}));
 const box=within(await screen.findByRole('region',{name:'Thread: Ideas storage'})).getByRole('textbox',{name:'Reply in Ideas storage'});
 await waitFor(()=>assert.equal(document.activeElement,box));
 fireEvent.keyDown(box,{key:'Escape'});
 assert.notEqual(document.activeElement,box,'Esc leaves the reply box');
 fireEvent.keyDown(document.activeElement,{key:'ArrowDown'});
 const next=await screen.findByRole('region',{name:'Thread: Live board'});
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.notEqual(document.activeElement,within(next).getByRole('textbox',{name:'Reply in Live board'}),'the next thread leaves the cursor where it was');
 fireEvent.keyDown(document.activeElement,{key:'ArrowUp'});
 await screen.findByRole('region',{name:'Thread: Ideas storage'});
 assert.notEqual(document.activeElement,screen.getByRole('textbox',{name:'Reply in Ideas storage'}),'coming back doesn’t take the cursor either');
 localStorage.clear();
});

test('hovering a row for a moment opens its peek; Esc closes it; the selected row has no bar on its edge',async()=>{
 const recent=Math.floor(Date.now()/1000);
 host({pickup:{...records,since:recent-2400,items:[item({moved:recent-300}),{...finished,moved:recent-600,files:['board.py'],files_count:1,checks_line:'Checks passed.',unchecked:'Not tried with three runs.'}]},listFails:true});
 mount();
 const pane=await screen.findByRole('region',{name:'Catch up'});
 const row=(await within(pane).findByRole('button',{name:'Live board'})).closest('[data-row]');
 fireEvent.mouseOver(row);
 assert.equal(screen.queryByTestId('home-peek'),null,'not at once');
 const peek=await screen.findByTestId('home-peek',{},{timeout:2000});
 assert.match(peek.textContent,/You askedStore ideas per project/);
 assert.match(peek.textContent,/Changedboard\.py/);
 assert.match(peek.textContent,/Not checkedNot tried with three runs\./);
 assert.equal(within(row).getByRole('button',{name:'Live board'}).getAttribute('aria-describedby'),peek.id);
 fireEvent.keyDown(document.body,{key:'Escape'});
 await waitFor(()=>assert.equal(screen.queryByTestId('home-peek'),null));
 const css=readFileSync(new URL('./Home.css',import.meta.url),'utf8');
 const selected=css.match(/\.dv-home-row\[data-selected\][^{]*\{([^}]*)\}/)[1];
 assert.match(selected,/background:/);
 assert.doesNotMatch(selected,/border|box-shadow|inset/,'a soft background only, nothing on the left edge (Bryce, 27 Sep)');
});

// ---------------------------------------------------------------- Enter sends at once (beta.18)
// Bryce, 6 Oct: "When I type in Home and press Enter, it doesn't instantly send." send() waited for the host's whole reply.
/** Hold orchestrator_send until ``release``; ``then`` runs instead of the host's answer when given. */
function holdSend(then){
 let release;const gate=new Promise(resolve=>{release=resolve;});
 const invoke=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__.invoke=async(command,args={})=>{
  if((args.operation??command)!=='orchestrator_send')return invoke(command,args);
  await gate;
  return then?then(()=>invoke(command,args)):invoke(command,args);
 };
 return ()=>release();
}
async function typeAndEnter(words){
 await screen.findByRole('region',{name:'Catch up'});
 const box=screen.getByRole('textbox',{name:/Ask Dervo/});
 fireEvent.change(box,{target:{value:words}});
 fireEvent.keyDown(box,{key:'Enter'});
 return box;
}
test('Enter shows your message and clears the box before the host answers, and the host’s copy takes over the same row',async()=>{
 const calls=host({pickup:records});
 const release=holdSend();
 mount();
 const box=await typeAndEnter('where are we');
 await waitFor(()=>assert.equal(document.querySelector('.dv-msg-you')?.textContent,'where are we'));
 assert.equal(box.value,'','the box is clear at once');
 const shown=document.querySelector('.dv-msg-you-wrap');
 assert.equal(shown.dataset.outgoing,'sending');
 assert.match(document.querySelector('.dv-msg-dervo').textContent,/Looking through your (threads|project)/,'the step line sits where the answer will land');
 assert.equal(screen.getByRole('button',{name:'Send'}).disabled,true,'one send at a time');
 release();
 await waitFor(()=>assert.equal(document.querySelector('.dv-msg-you-wrap').dataset.outgoing,undefined));
 assert.ok(calls.some(([op,a])=>op==='orchestrator_send'&&a.text==='where are we'),'sent');
 assert.equal(document.querySelectorAll('.dv-msg-you').length,1,'never two copies');
 assert.equal(document.querySelector('.dv-msg-you-wrap'),shown,'the same row, so nothing redraws when the host’s copy lands');
});
test('a refused send puts your words back in the box and goes back to Home, with the host’s reason',async()=>{
 host({pickup:records});
 const release=holdSend(()=>{throw new Error('This project’s Claude account is signed out.');});
 mount();
 const box=await typeAndEnter('where are we');
 await waitFor(()=>assert.ok(document.querySelector('.dv-msg-you')));
 release();
 await waitFor(()=>assert.equal(box.value,'where are we'));
 assert.equal(document.querySelector('.dv-msg-you'),null,'no message left that wasn’t sent');
 assert.ok(screen.getByRole('region',{name:'Catch up'}),'back where you wrote it');
 assert.ok(screen.getAllByRole('alert').some(node=>/signed out/.test(node.textContent)));
});
test('a refused send keeps your words on screen as not sent when you’ve typed since, and Put it back adds them to the box',async()=>{
 host({pickup:records});
 const release=holdSend(()=>{throw new Error('The model call failed.');});
 mount();
 const box=await typeAndEnter('where are we');
 await waitFor(()=>assert.ok(document.querySelector('.dv-msg-you')));
 fireEvent.change(box,{target:{value:'and the docs'}});
 release();
 await waitFor(()=>assert.equal(document.querySelector('.dv-msg-you-wrap').dataset.outgoing,'unsent'));
 assert.equal(box.value,'and the docs','what you typed since stays');
 assert.match(document.querySelector('.dv-msg-you-note').textContent,/^Not sent\./);
 fireEvent.click(screen.getByRole('button',{name:'Edit and send again'}));
 assert.equal(box.value,'and the docs\nwhere are we');
 assert.equal(document.querySelector('.dv-msg-you'),null);
});
test('a send that may still go through stays as sent, offers no resend, and lands when the host’s copy turns up',{timeout:15000},async()=>{
 const calls=host({pickup:records});
 // The host records it, but the bridge answers that it's still busy (b18/stopping-host's slow-request answer).
 // The host records it two seconds later, as the backend finishes after the bridge has given up waiting.
 const release=holdSend(async answer=>{setTimeout(()=>void answer(),2000);throw new Error('Dervo is still busy with orchestrator send. It may still finish, so check before you try it again.');});
 mount();
 const box=await typeAndEnter('where are we');
 release();
 await waitFor(()=>assert.equal(document.querySelector('.dv-msg-you-wrap')?.dataset.outgoing,'unsure'));
 assert.equal(box.value,'','not put back: it may have gone');
 assert.equal(screen.queryByRole('button',{name:'Send again'}),null);
 assert.equal(screen.queryByRole('button',{name:'Edit and send again'}),null);
 assert.match(document.querySelector('.dv-msg-you-note').textContent,/may still go through/);
 await waitFor(()=>assert.equal(document.querySelector('.dv-msg-you-wrap')?.dataset.outgoing,undefined),{timeout:8000});
 assert.equal(document.querySelectorAll('.dv-msg-you').length,1);
 assert.equal(calls.filter(([op])=>op==='orchestrator_send').length,1,'sent once');
});
// The bridge's two busy answers, word for word from b18/stopping-host (308d8fa5, src/bridge.rs busy_still and
// busy_skipped), and the pt-BR lines local() shows instead in Portuguese (its pt-BR.json).
test('sendFailure reads only the bridge’s still-busy answer as maybe sent, in English and Portuguese',async()=>{
 assert.equal(sendFailure('Dervo is still busy with orchestrator send. It may still finish, so check before you try it again.'),'unsure');
 assert.equal(sendFailure('Dervo is busy with orchestrator send, so this wasn’t done. Try again in a moment.'),'unsent');
 assert.equal(sendFailure('This project’s Claude account is signed out.'),'unsent');
 // Matched by code (isHostMessage), so the Portuguese lines count when the window is in Portuguese, which is when local() shows them.
 const {useLanguageForTests}=await import('../i18n/index.ts');
 useLanguageForTests('pt-BR');
 try{
  assert.equal(sendFailure('O Dervo ainda está ocupado com orchestrator send. Isso ainda pode terminar, então confira antes de tentar de novo.'),'unsure');
  assert.equal(sendFailure('O Dervo está ocupado com orchestrator send, então isto não foi feito. Tente de novo daqui a pouco.'),'unsent');
  assert.equal(sendFailure('Dervo is still busy with orchestrator send. It may still finish, so check before you try it again.'),'unsure','the English as sent still counts');
 }finally{useLanguageForTests('en');}
});
test('withOutgoing puts your message and the step line on the end of its own request only, until the host’s copy is there',()=>{
 const outgoing={key:'sending:a',text:'hi',images:[],requestId:null,at:5,state:'sending'};
 const shown=withOutgoing([],outgoing,null,new Map());
 assert.deepEqual(shown.map(entry=>[entry.kind,entry.id,entry.state??null]),[['you','sending:a',null],['reply','sending:a:reply','thinking']]);
 assert.deepEqual(withOutgoing([],outgoing,'other',new Map()),[],'another request doesn’t show it');
 const host=[{id:'y1',kind:'you',text:'hi',created_at:5}];
 assert.equal(withOutgoing(host,outgoing,null,new Map([['y1','sending:a']])),host,'the host’s copy is in: shown once');
 assert.deepEqual(withOutgoing([],{...outgoing,state:'unsent'},null,new Map()).map(entry=>entry.outgoing),['unsent'],'no step line once it isn’t sending');
});
