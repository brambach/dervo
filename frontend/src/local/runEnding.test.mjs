// Beta.17.1 (Bryce, 5 Oct 2026): "too many times a 'X' appears when it shouldn't". Only an ending the host classes as
// failed (backend/run_ending.py) draws the failure X; every other way a run ends says what ended it.
import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {endingDecision,endingWord,lastSeenLine,continuable,CONTINUE_TEXT}=await import('./runEnding.ts');
const {decisionLine}=await import('./CodingTasks.tsx');
const {panelState}=await import('./TaskGuide.tsx');
const {GuideActions}=await import('./GuideActions.tsx');
const {threadSummary}=await import('./ThreadPickup.tsx');
const {glyphOf}=await import('./Home.tsx');
const {threadGlyph}=await import('./DervoSidebar.tsx');
const {limitLines,hoursLabel}=await import('./SettingsLimits.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const base={id:'t1',origin:'conversation',state:'blocked',files:[],messages:[{role:'user',text:'x',created_at:1}],tokens:[],runs:[{id:'r',state:'failed'}],
 binding:{provider:'claude',identity:'me',model:'m',cwd:'/p',visibility:'shared'},check_profile:'manual-review',scope_version:1,argv:[],session_id:'s'};
const at=Date.UTC(2026,9,5,15,41)/1000;

test('a failure the work reported keeps its X; nothing else does', ()=>{
 const failed={...base,error:'Claude Code reported an error.',ending:{kind:'failed'}};
 assert.equal(decisionLine(failed).glyph,'failed');
 assert.equal(endingDecision(failed),null,'a real failure is left to the failure line');
 const endings=[
  [{kind:'limit',limit:'idle'},'stopped','Stopped after a long silence.'],
  [{kind:'limit',limit:'hard'},'stopped','Stopped at the run length limit.'],
  [{kind:'limit',limit:'attempts'},'stopped','Stopped at the attempt limit.'],
  [{kind:'limit',limit:'output'},'stopped','Stopped at the output limit.'],
  [{kind:'not_started'},'stopped','Didn’t start.'],
  [{kind:'finished',snag:'checks'},'needs','Finished.'],
  [{kind:'stopped',by:'you',confirmed:true},'stopped','Paused.'],
 ];
 for(const [ending,glyph,lead] of endings){
  const line=decisionLine({...base,error:'Something the host said.',ending});
  assert.deepEqual([line.glyph,line.lead],[glyph,lead],JSON.stringify(ending));
 }
});

test('lost track is unknown, with what the run was last seen doing', ()=>{
 const lost={...base,state:'disconnected',runs:[{id:'r',state:'uncertain'}],error:'Dervo closed while this was running, so it can’t say how it ended.',
  ending:{kind:'unknown',last_seen:at,step:'Running the backend tests'}};
 const line=decisionLine(lost);
 assert.equal(line.glyph,'unknown');
 assert.equal(line.lead,'Outcome not confirmed.');
 assert.match(line.clause,/Last seen .*: Running the backend tests\.$/);
 assert.equal(panelState(lost).glyph,'unknown');
 assert.equal(lastSeenLine({kind:'unknown'}),'','nothing recorded, nothing said');
 assert.equal(lastSeenLine({kind:'unknown',step:'Reading'}),'It was on: Reading.');
});

test('a limit line says which limit and that Continue picks it up', ()=>{
 const idle={...base,error:'Claude Code sent nothing for 2 hours, so Dervo stopped the run.',ending:{kind:'limit',limit:'idle',last_seen:at}};
 const line=endingDecision(idle);
 assert.match(line.clause,/^Claude Code sent nothing for 2 hours, so Dervo stopped the run\. Last seen .*\. Continue picks up where it stopped\.$/);
 assert.equal(continuable(idle.ending),true);
 assert.equal(continuable({kind:'limit',limit:'provider'}),false,'the usage limit has its own handoff row');
 assert.equal(endingDecision({...idle,suggest:{kind:'usage_limit'}}),null,'the handoff row stays the home for a provider limit');
 assert.equal(endingDecision({...base,error:'Dervo closed before this started. Nothing was sent. Send it again.',ending:{kind:'not_started'}}).clause,
  'Dervo closed before this started. Nothing was sent. Send it again.','not "Nothing was sent" twice');
});

test('finished work with files Dervo couldn’t record keeps its own line', ()=>{
 const held={...base,runs:[{id:'r',state:'completed'}],unrecorded:[{path:'a.py',why:'x'}],ending:{kind:'finished',snag:'record'}};
 assert.equal(endingDecision(held),null);
 assert.deepEqual([decisionLine(held).glyph,decisionLine(held).lead],['needs','Nothing to approve.']);
 assert.equal(endingWord(held).word,'Nothing to approve');
});

test('the sheet and Progress panel words', ()=>{
 assert.deepEqual(endingWord({state:'blocked',ending:{kind:'limit',limit:'hard'}}),{glyph:'stopped',word:'Stopped at a limit'});
 assert.deepEqual(endingWord({state:'blocked',ending:{kind:'finished',snag:'checks'}}),{glyph:'needs',word:'Finished'});
 assert.equal(endingDecision({...base,error:'This task has used all 64 of its attempts, this project’s limit. Raise it in Settings, Access, or ask for the rest in a new message.',ending:{kind:'limit',limit:'attempts',last_seen:at}}).clause,
  'This task has used all 64 of its attempts, this project’s limit. Raise it in Settings, Access, or ask for the rest in a new message.','the attempt line says what to do, once');
 assert.equal(endingWord({state:'blocked',ending:{kind:'failed'}}),null);
 assert.deepEqual(panelState({...base,ending:{kind:'not_started'}}),{glyph:'stopped',word:'Didn’t start',running:false,caption:''});
 assert.equal(panelState({...base,error:'boom',ending:{kind:'failed'}}).glyph,'failed');
});

test('Home, the pickup strip and the sidebar draw no X for a limit, a snag or a lost run', ()=>{
 const item={kind:'stopped',needs:true,failed:false,question:false,checks:null};
 assert.equal(glyphOf({...item,ending:'limit'}),'stopped');
 assert.equal(glyphOf({...item,ending:'finished'}),'needs');
 assert.equal(glyphOf({...item,kind:'unconfirmed',ending:'unknown'}),'unknown');
 assert.equal(glyphOf({...item,failed:true,ending:'failed'}),'failed');
 assert.equal(threadSummary({...item,ending:'limit',did:'Stopped. Claude Code sent nothing for 2 hours.'}).word,'Stopped at a limit');
 assert.equal(threadSummary({...item,ending:'finished',did:'Finished. Tests were not run.'}).mark,'needs');
 assert.equal(threadSummary({...item,kind:'unconfirmed',did:'Dervo restarted.'}).mark,'unknown');
 assert.equal(threadSummary({...item,failed:true,did:'Stopped. It crashed.'}).mark,'failed');
 assert.equal(threadGlyph({status:'needs',ended:'limit'}).state,'stopped');
 assert.equal(threadGlyph({status:'needs',ended:'snag'}).state,'needs');
 assert.equal(threadGlyph({status:'needs',ended:'disconnected'}).state,'unknown');
 assert.equal(threadGlyph({status:'needs',ended:'blocked'}).state,'failed');
});

test('Continue after a limit: in the same task when a retry can run, as a new message at the attempt limit', async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});return {};}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const sent=[];
 const mount=(ending,retry)=>render(React.createElement(QueryClientProvider,{client},React.createElement(GuideActions,{task:{...base,ending},projectId:'p',channelId:'c',
  detail:{task:base,retry:{available:retry},evidence:{attempts:[]}},onChanged:async()=>{},onCorrection:()=>{},onResume:text=>{sent.push(text);return undefined;}})));
 mount({kind:'limit',limit:'idle'},true);
 fireEvent.click(screen.getByRole('button',{name:'Continue'}));
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.deepEqual(calls.map(call=>[call.operation,call.args.text]),[['task_continue',CONTINUE_TEXT]]);
 cleanup();
 mount({kind:'limit',limit:'attempts'},true);
 assert.deepEqual(screen.queryAllByRole('button').map(button=>button.textContent),['Continue'],'no Retry: the same task would hit the limit again');
 fireEvent.click(screen.getByRole('button',{name:'Continue'}));
 assert.deepEqual(sent,[CONTINUE_TEXT]);
 cleanup();
 mount({kind:'failed'},true);
 assert.deepEqual(screen.queryAllByRole('button').map(button=>button.textContent),['Retry'],'a failure keeps Retry');
});

test('Settings words for each limit', ()=>{
 assert.deepEqual([hoursLabel(1800),hoursLabel(7200),hoursLabel(86400),hoursLabel(null)],['30 min','2 h','24 h','No cap']);
 const lines=limitLines({idle_seconds:7200,run_seconds:null,attempts:null,warning:'With no attempt limit, a task can keep trying.'});
 assert.equal(lines.idle,'A run that sends nothing for 2 h is stopped. Continue picks it up from there.');
 assert.equal(lines.run,'No cap on how long a run lasts. Only silence ends it.');
 assert.equal(lines.attempts,'With no attempt limit, a task can keep trying.');
 assert.match(limitLines({idle_seconds:1800,run_seconds:86400,attempts:64}).attempts,/after 64 attempts/);
});

test('Catch up draws an X only for a blocked row that failed', async()=>{
 const {catchupPlan,blockedEnding}=await import('./ProjectCatchup.tsx');
 const NOW=Date.UTC(2026,9,5,9,0)/1000;
 const row=(id,ending,extra)=>({id,kind:'task',channelId:id,channelName:id,snippet:'',source:{},title:`Request in ${id}`,status:'blocked',runState:'failed',isNew:true,askedAt:NOW-600,ending,...extra});
 const rows=[row('failed','failed',{blocker:'Claude Code reported an error.'}),row('limit','limit',{blocker:'Claude Code sent nothing for 2 hours, so Dervo stopped the run.'}),
  row('unsent','not_started'),row('snag','finished',{runState:'completed'}),row('old',undefined,{blocker:'The server is overloaded.'})];
 const one=r=>{const plan=catchupPlan([r],[{id:r.id,name:r.id,visibility:'shared',project_id:'p',profile_id:'a',model:'m',activity:null}],{since:NOW-3600,last:null,now:NOW});return [plan.needs[0]?.glyph,plan.needs[0]?.what];};
 const [failed,limit,unsent,snag,old]=rows.map(one);
 assert.deepEqual(failed,['failed','Couldn’t finish: Claude Code reported an error.']);
 assert.deepEqual(limit,['stopped','Stopped at a limit']);
 assert.deepEqual(unsent,['stopped','Didn’t start']);
 assert.deepEqual(snag,['needs','Finished, a step didn’t complete']);
 assert.deepEqual(old,['failed','Couldn’t finish: The server is overloaded.'],'a row from a host without endings keeps today’s mark');
 assert.equal(blockedEnding('failed'),null);
 assert.equal(blockedEnding(undefined),null);
 assert.equal(blockedEnding('unknown').glyph,'unknown');
});

test('search says what ended a blocked run, in the same words as Catch up', async()=>{
 const {savedStatus}=await import('./WorkspaceSearch.tsx');
 const row=ending=>({id:'t',kind:'task',channelId:'c',channelName:'c',snippet:'',source:{},status:'blocked',ending});
 assert.equal(savedStatus(row('limit')),'Stopped at a limit');
 assert.equal(savedStatus(row('not_started')),'Didn’t start');
 assert.equal(savedStatus(row('failed')),'Couldn’t finish');
 assert.equal(savedStatus(row(undefined)),'Couldn’t finish');
});

test('beta.18: Leave it stopped, a restart pause, Don’t run and one unsure step say what they are, never the failure X',()=>{
 const run={id:'r',state:'failed',prior_attempts:0};
 const make=(extra)=>({id:'t',state:'blocked',origin:'conversation',files:[],messages:[],tokens:[],runs:[run],binding:{provider:'claude'},scope_version:1,argv:[],session_id:'s',...extra});
 const left=make({error:'Provider exited. You left this stopped. Dervo never confirmed what the run did.',ending:{kind:'stopped',by:'you',left:true,confirmed:false}});
 assert.equal(decisionLine(left).glyph,'stopped');
 assert.equal(decisionLine(left).lead,'Left stopped.');
 assert.deepEqual(endingWord(left),{glyph:'stopped',word:'Left stopped'});
 const restart=make({error:'Paused when Dervo restarted. It had changed DervoSidebar.tsx. Those edits are in your project folder, not committed.',
  ending:{kind:'stopped',by:'restart',confirmed:true},runs:[{id:'r',state:'interrupted',end_cause:'restart_paused'}]});
 const said=decisionLine(restart);
 assert.equal(said.lead,'Paused when Dervo restarted.');
 assert.equal(said.clause,'Claude Code’s own record shows every step it started finished, so nothing is uncertain. It had changed DervoSidebar.tsx. Those edits are in your project folder, not committed. Continue picks up in the same session.');
 const denied=make({error:'Execution scope denied. No task request was sent.',ending:{kind:'not_started',declined:true},runs:[]});
 assert.deepEqual([decisionLine(denied).glyph,decisionLine(denied).clause],['stopped','You chose not to run it. Nothing was sent.']);
 const unsure=make({state:'disconnected',error:'Dervo closed while this was running.',ending:{kind:'unknown',cause:'closed'},
  runs:[{id:'r',state:'uncertain',after_close:{record:{state:'unsure',step:{tool:'Bash',text:'gh workflow run preview.yml',at:null}}}}]});
 assert.deepEqual([decisionLine(unsure).glyph,decisionLine(unsure).lead],['unknown','One step may not have finished.']);
 assert.equal(decisionLine(unsure).clause,'gh workflow run preview.yml started and Dervo restarted before it ended. Running it again could do it twice.');
 assert.deepEqual(endingWord(unsure),{glyph:'unknown',word:'One step not confirmed'});
 // The strip: a quit pause reads Paused (it read Not confirmed up to 17.1), and a restart pause reads Paused.
 assert.equal(threadSummary({kind:'unconfirmed',ending:'stopped',failed:false,needs:false,did:'Paused when Dervo quit.',line:'',now:null,next:null,next_by:null,question:null,moved:null,running_since:null}).word,'Paused');
 assert.equal(threadSummary({kind:'stopped',ending:'stopped',paused_by:'restart',failed:false,needs:false,did:'Paused when Dervo restarted.',line:'',now:null,next:null,next_by:null,question:null,moved:null,running_since:null}).word,'Paused');
 assert.equal(threadSummary({kind:'stopped',ending:'stopped',failed:false,needs:false,did:'You paused this.',line:'',now:null,next:null,next_by:null,question:null,moved:null,running_since:null}).word,'Paused','your own Pause says Paused, as the card under it does');
 assert.equal(threadGlyph({status:null,ended:'restart_paused'}).label,'Paused when Dervo restarted');
 assert.equal(threadGlyph({status:null,ended:'stopped',unread:1}).label,'You paused this');
 assert.equal(threadGlyph({status:null,ended:'left'}).label,'Left stopped');
});
