/* The Progress panel: what it holds and what it drops. Decided 19 Sep 2026 from Bryce's review of the thread screen
 * ("keep it, it's a strong idea; decide exactly what belongs there, cut the rest, and make the type excellent").
 *
 * Holds: the kicker and the thread's short name (only when no Progress / Preview switch sits above it: then the tab and the
 * thread's head say them, fresh-eyes 20 Sep), one status line for the run (the state word, when it ended and how long
 * it took, or how long it's been going, with Stop while it runs), the steps Dervo recorded (folded to the last five,
 * one line each with an optional caption), the files it changed with their line deltas, and one way into the record.
 *
 * Drops: the review decision and its checks clause (the decision bar under the report owns that), the task's error
 * (same bar), the agent's goal, "why it matters" and plan cards, "Your turn", the test steps (the report says how to
 * test), token counts and cost, diff previews, notes saved to memory (Knowledge shows them), the account and model
 * eyebrow (the byline in the thread names the agent), the Progress and Record tabs (the record opens from the foot) and
 * the panel's own Hide (the thread header has it).
 *
 * A tool call that returned an error is a done step with a plain caption, never a red step: the agent carried on from
 * it. Only the run's own end can be a failure, and it's said once, on the last row. Only the step running now moves. */
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local,type Conversation} from './api';
import {REVIEW_OPEN,quitPaused,stoppedTask,TaskRecord,type CodingTask,type Detail} from './CodingTasks';
import {isStopping,markStopping,useStoppingVersion} from './stopping';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {WorkingMark} from './WorkingMark';
import {activityBasename} from './DervoActivity';
import {duration,summariseDiff,taskFlow,type FileDiff} from './TaskFlow';
import {progressPlan} from './progressBrief';
import {rememberedItems,rememberedSummary} from './RememberedLine';
import {lasted} from './homeTime';
import {RunToldForTask} from './RunContext';
import {endingWord} from './runEnding';
import './TaskGuide.css';
import {formatDate,t} from '@/i18n';

const record=(value:unknown):Record<string,unknown>=>typeof value==='object'&&value!==null?value as Record<string,unknown>:{};
const time=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)&&value>0?value:undefined;
const active=(task:CodingTask)=>task.state==='working'||REVIEW_OPEN.has(task.collaboration?.review?.phase??'');
const requestTime=(task:CodingTask)=>Math.max(0,...task.messages.filter(m=>m.role==='user').map(m=>m.created_at));
const clock=(seconds:number)=>formatDate(new Date(seconds*1000),{hour:'numeric',minute:'2-digit'});
const WRITES=new Set(['dervo_write_file','dervo_edit_file','dervo_create_file','native:Edit','native:Write']);
const SHOWN=5;

/** Prefer work still running, including a continued older task. Helpers don't replace the owner's guide. */
export function selectGuideTask(tasks:CodingTask[]):CodingTask|undefined{
 return tasks.reduce<CodingTask|undefined>((selected,task)=>{
  if(task.origin==='collaboration')return selected;
  if(!selected||Number(active(task))>Number(active(selected)))return task;
  if(active(task)===active(selected)&&requestTime(task)>=requestTime(selected))return task;
  return selected;
 },undefined);
}

/** ``working``: the agent is still working though the panel says Needs you (it asked and carried on), so Stop stays. */
export type PanelState={glyph:GlyphState;word:string;running:boolean;caption:string;working?:boolean};
/** The one status line: the run's state as a word with its glyph, and when it ended and how long it took, or how long
 * it's been going. The decision (review, approval, retry) lives under the report, so it's never said here. */
/** ``askedAt``: when this task's question in the thread's tray was asked, while it waits on you (beta.16, b16-thread
 * question frames: the strip, this panel and the sidebar row all say Needs you). */
export function panelState(task:CodingTask,now=Date.now()/1000,unavailable=false,askedAt?:number|null):PanelState{
 const run=record(task.runs.at(-1));
 const phase=task.collaboration?.review?.phase??'';
 const reviewOpen=REVIEW_OPEN.has(phase)&&!(phase==='pending'&&task.state==='working');
 const uncertain=task.state==='disconnected'||task.runs.some(r=>record(r).state==='uncertain')||task.tokens.some(t=>t.state==='uncertain');
 const start=time(run.started_at)??time(task.messages.find(m=>m.role==='user')?.created_at);
 const finalMessage=time([...task.messages].reverse().find(m=>m.role==='assistant')?.created_at);
 const end=time(run.ended_at)??time(task.completion?.run_id===run.id?task.completion?.at:undefined)??finalMessage;
 const who=task.route?.switched_from?(task.binding.provider==='codex'?'Codex':task.binding.provider==='claude'?'Claude Code':undefined):undefined;
 const finished=[who,end?clock(end):undefined,start&&end&&end-start>=60?lasted(start,end):undefined].filter(Boolean).join(' · ');
 // Under a minute says nothing, like the pickup strip beside it: "1 min" for 3 s read as wrong (reviewer, 8 Oct).
 const going=[who,start&&now-start>=60?lasted(start,now):undefined].filter(Boolean).join(' · ');
 if(unavailable)return {glyph:'idle',word:t('Last saved record'),running:false,caption:finished};
 if(uncertain&&quitPaused(task))return {glyph:'stopped',word:t('Paused'),running:false,caption:finished};
 if(uncertain)return {glyph:'unknown',word:t('Not confirmed'),running:false,caption:finished};  // lost track isn't failed (17.1)
 // A waiting question wins over Running, even while the agent carries on (Bryce, 26 Sep: Needs you in the strip and here).
 if(typeof askedAt==='number')return {glyph:'needs',word:t('Needs you'),running:false,caption:t('Asked {time} ago',{time:lasted(askedAt,now)}),...(task.state==='working'?{working:true}:{})};
 if(task.state==='working'&&!reviewOpen)return {glyph:'running',word:t('Running'),running:true,caption:going};
 if(reviewOpen)return {glyph:'running',word:t('Reviewing'),running:true,caption:going};
 if(task.state==='needs_permission')return {glyph:'needs',word:t('Waiting for permission'),running:false,caption:''};
 if(stoppedTask(task))return {glyph:'stopped',word:t('Paused'),running:false,caption:finished};
 // Beta.15: it finished, but Dervo couldn't record what it left. That's not a failed run.
 if(task.state==='blocked'&&task.unrecorded?.length)return {glyph:'needs',word:t('Nothing to approve'),running:false,caption:finished};
 // Beta.17.1 (run_ending): a limit, a run that never started or finished work with a step left undone isn't a failure.
 const ending=task.state==='blocked'?endingWord(task):null;
 if(ending)return {glyph:ending.glyph,word:ending.word,running:false,caption:finished};
 if(task.state==='blocked')return {glyph:'failed',word:run.provider_failure||run.end_cause==='host_limit'?t('Stopped'):t('Failed'),running:false,caption:finished};
 return {glyph:'done',word:t('Finished'),running:false,caption:finished};
}

export type PanelStep={id:string;glyph:GlyphState;text:string;files:string[];caption?:string;now?:boolean;later?:boolean};
/** The steps as Dervo recorded them, one line each, then what the agent said comes next while it still works. */
export function panelSteps(task:CodingTask,now=Date.now()/1000,unavailable=false):PanelStep[]{
 const steps:PanelStep[]=taskFlow(task,unavailable).map(row=>{
  const at=row.endedAt&&(row.meta||row.id.endsWith(':end'))?clock(row.endedAt):undefined;
  if(row.state==='live')return {id:row.id,glyph:'running',text:row.text,files:row.files,caption:[t('Now'),row.startedAt&&now-row.startedAt>=60?duration(now-row.startedAt):undefined].filter(Boolean).join(' · '),now:true};
  if(row.state==='cancelled')return {id:row.id,glyph:'stopped',text:row.text,files:row.files,caption:[at,row.meta].filter(Boolean).join(' · ')||undefined};
  if(row.state==='problem'||row.state==='attention')return {id:row.id,glyph:'failed',text:row.text,files:row.files,caption:[at,row.meta].filter(Boolean).join(' · ')||undefined};
  if(row.state==='unknown')return {id:row.id,glyph:'idle',text:row.text,files:row.files,caption:row.meta};
  return {id:row.id,glyph:'done',text:row.text,files:row.files,caption:[at,row.meta].filter(Boolean).join(' · ')||undefined};
 });
 if(task.state==='working'&&!unavailable){
  const next=progressPlan(task).find(step=>step.state==='next');
  if(next)steps.push({id:'next',glyph:'idle',text:next.text,files:[],caption:t('Next'),later:true});
 }
 return steps;
}

export type PanelFile={name:string;path:string;delta?:string};
// A file the run removed reads as Deleted, not as "−4" (design review S7, 24 Sep): approving commits the removal.
const delta=(diff:Pick<FileDiff,'added'|'removed'|'deleted'>)=>diff.deleted?t('Deleted'):[diff.added?`+${diff.added}`:'',diff.removed?`−${diff.removed}`:''].filter(Boolean).join(' ')||undefined;
/** The files this run changed, with line deltas where a diff is recorded: the completion's diffs, else the latest
 * attempt's, else the files its write calls named while it still works. */
export function panelFiles(task:CodingTask,diffs?:FileDiff[],detail?:Detail):PanelFile[]{
 const run=record(task.runs.at(-1)),runId=typeof run.id==='string'?run.id:undefined;
 const completion=task.completion&&task.completion.run_id===runId&&!task.completion.stale?task.completion:undefined;
 if(completion?.changed_files.length){
  return completion.changed_files.map(path=>{const diff=diffs?.find(d=>d.path===path||activityBasename(d.path)===activityBasename(path));return {name:activityBasename(path)??path,path,delta:diff?delta(diff):undefined};});
 }
 const attempt=detail?.task.id===task.id?detail.evidence.attempts.at(-1):undefined;
 // A granted file the attempt left alone has an empty diff: it isn't a changed file (polish review, 9 Oct).
 const changed=attempt?Object.entries(attempt.diff).filter(([path,diff])=>diff||(attempt.deleted&&Object.hasOwn(attempt.deleted,path))):[];
 if(attempt&&attempt.kind!=='restoration'&&changed.length){
  return changed.map(([path,diff])=>({name:activityBasename(path)??path,path,delta:attempt.deleted&&Object.hasOwn(attempt.deleted,path)?t('Deleted'):diff&&!diff.startsWith('Binary diff')?delta(summariseDiff(path,diff)):undefined}));
 }
 const seen=new Map<string,string>();
 for(const token of task.tokens){
  if(token.run_id!==runId||!WRITES.has(token.tool)||token.state!=='delivered'||token.success===false)continue;
  const name=activityBasename(token.path);
  if(name&&!seen.has(name))seen.set(name,token.path??name);
 }
 return [...seen].map(([name,path])=>({name,path}));
}

/** What this run did to project memory, as one line: the same counts as the reply's Remembered line (beta.17.1, #41
 * direction A). Up to beta.17 this repeated every note in full, 334 to 461 px at 1052 for one to three saves. Each note,
 * with Edit, Undo, Keep and Remove, is one Show away under the reply, and every note is in Knowledge. */
function Remembered({task}:{task:CodingTask;projectId:string;channelId:string;onError:(message:string)=>void}){
 const items=rememberedItems(task);
 const summary=rememberedSummary(items);
 if(!summary)return null;
 const failed=items.every(item=>item.kind==='refused'||item.kind==='withheld');
 return <section aria-label={t('Remembered')} className="dv-panel-section">
  <ul className="dv-steps">
   <li className="dv-step" data-glyph={failed?'failed':'done'}>
    <StatusGlyph state={failed?'failed':'done'}/>
    <span>{summary.waits?t('{summary}, {count} wait for you',{summary:summary.text,count:summary.waits}):summary.text}<small>{t('In Knowledge, newest first')}</small></span>
   </li>
  </ul>
 </section>;
}

function useTicking(on:boolean){
 const [now,setNow]=useState(()=>Date.now()/1000);
 useEffect(()=>{if(!on)return;setNow(Date.now()/1000);const timer=setInterval(()=>setNow(Date.now()/1000),1000);return ()=>clearInterval(timer);},[on]);
 return now;
}

function FileNames({files}:{files:string[]}){
 if(!files.length)return null;
 const shown=files.length>2?[files[0]]:files;
 return <>{shown.map((file,index)=><span key={`${file}:${index}`}>{index>0?', ':' '}<code>{file}</code></span>)}{files.length>2&&<> {t('and {count} more',{count:files.length-1})}</>}</>;
}

/** The handoff offer belongs under the report. The shell hands it to the panel while the panel is open, so the panel
 * puts it back into the slot the decision bar leaves for it (CodingTasks.tsx, TaskFooter). */
function HandoffSlot({taskId,children}:{taskId:string;children:ReactNode}){
 const [slot,setSlot]=useState<Element|null>(null);
 useEffect(()=>{
  const find=()=>setSlot(document.querySelector(`[data-dervo-handoff-slot="${taskId}"]`));
  find();
  const observer=typeof MutationObserver==='undefined'?undefined:new MutationObserver(find);
  observer?.observe(document.body,{childList:true,subtree:true});
  return ()=>observer?.disconnect();
 },[taskId]);
 return slot?createPortal(children,slot):<div className="dv-panel-handoff">{children}</div>;
}

type Props={task:CodingTask;projectId:string;channelId:string;unavailable?:boolean;diffs?:FileDiff[];onClose:()=>void;refresh:()=>Promise<unknown>;detail?:Detail;
 onDetailChanged?:()=>Promise<unknown>;recordRequest?:number;records?:ReactNode;extra?:ReactNode;suggestion?:ReactNode;
 /** When this task's question in the tray was asked, while it waits on you. */
 askedAt?:number|null;
 onInspect?:(trigger:HTMLElement)=>void;onOpenSource?:(channelId:string,taskId:string,trigger:HTMLElement)=>void;onRunIt?:()=>void;onFollowUp?:()=>void;
 /** Knowledge's "What it was told" view links back to Knowledge (design sweep, run-context.html). */
 onOpenKnowledge?:()=>void;
 /** The column's Progress and Preview switch (panel direction C), shown above every view. */
 switcher?:ReactNode;
 /** History (agent history, 23 Sep): the run's record and a changed file's trace open in the main column when given. */
 onOpenRecord?:()=>void;onTraceFile?:(path:string)=>void};

/** The column while a thread's tasks load (beta.18): the same box Progress opens into, so the thread beside it keeps its
 * width and nothing pops in, with the column's own switch and still shapes where the state and steps will be. */
export function TaskGuideLoading({switcher}:{switcher?:ReactNode}){
 return <aside className="dervo-task-guide dv-panel" aria-label={t('Progress')} aria-busy="true" data-testid="task-guide-loading">
  {switcher}
  <div className="dv-panel-skeleton" aria-hidden="true"><i data-w="state"/><i data-w="label"/><i/><i data-w="short"/><i data-w="label"/><i data-w="short"/></div>
 </aside>;
}

/** The Progress panel beside a thread. ``detail`` is the shell's task_detail read for this task, shared with the record;
 * ``recordRequest`` opens the record when it changes. */
export function TaskGuide(props:Props){
 const {task,projectId,channelId,unavailable=false,diffs,refresh,detail,onDetailChanged,recordRequest=0,records,extra,suggestion,askedAt}=props;
 const [view,setView]=useState<'progress'|'record'|'told'>('progress');
 const [expanded,setExpanded]=useState(false);
 const [pending,setPending]=useState(false),[error,setError]=useState('');
 useEffect(()=>{if(recordRequest)setView('record');},[recordRequest]);
 useEffect(()=>{setView('progress');setExpanded(false);setError('');},[task.id]);
 const client=useQueryClient();
 // The shell already loaded this project's conversations; read the cache and follow it, never fetch here.
 const conversations=useQuery<Conversation[]>({queryKey:['local-conversations',projectId],enabled:false,queryFn:()=>client.getQueryData<Conversation[]>(['local-conversations',projectId])??[]});
 const name=(conversations.data??client.getQueryData<Conversation[]>(['local-conversations',projectId]))?.find(c=>c.id===channelId)?.name;
 const now=useTicking(active(task)&&!unavailable);
 const state=panelState(task,now,unavailable,askedAt);
 // Whether this same task's run was going on the last render and has just ended, so the finish reads as one change.
 const was=useRef({id:task.id,running:state.running});
 const endedHere=was.current.id===task.id&&was.current.running&&!state.running;
 useEffect(()=>{was.current={id:task.id,running:state.running};});
 const steps=panelSteps(task,now,unavailable);
 const files=panelFiles(task,diffs,detail);
 const hidden=expanded?0:Math.max(0,steps.length-SHOWN);
 // Once a Stop is sent the button stays "Stopping…" until the run ends, so a second press can't turn a clean stop into
 // an unconfirmed one (run-lifecycle map, finding 3).
 useStoppingVersion();
 const stopping=pending||isStopping(task);
 async function stop(){
  setPending(true);setError('');
  try{await local('task_stop',{projectId,channelId,taskId:task.id});markStopping([task]);await refresh();}
  catch(e){setError(String(e));}finally{setPending(false);}
 }
 return <aside className="dervo-task-guide dv-panel" aria-label={t('Progress')} data-testid="task-guide" data-view={view} data-running={state.running||undefined}>
  {props.switcher}
  {view==='told'?<>
   <header className="dv-panel-head">
    <button type="button" className="dv-btn dv-btn-quiet dv-panel-back" onClick={()=>setView('progress')}><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 3L5 8l5 5"/></svg>{t('Back to progress')}</button>
    <p className="dv-panel-kicker">{t('What it was told')}</p>
    <h2 className="dv-panel-title" title={name}>{name??t('This thread')}</h2>
   </header>
   <RunToldForTask projectId={projectId} channelId={channelId} taskId={task.id} provider={(task as {binding?:{provider?:string}}).binding?.provider} onOpenKnowledge={props.onOpenKnowledge}/>
  </>:view==='record'?<>
   <header className="dv-panel-head">
    <button type="button" className="dv-btn dv-btn-quiet dv-panel-back" onClick={()=>setView('progress')}><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 3L5 8l5 5"/></svg>{t('Back to progress')}</button>
    <p className="dv-panel-kicker">{t('Record')}</p>
    <h2 className="dv-panel-title" title={name}>{name??t('This thread')}</h2>
   </header>
   <div className="dervo-guide-record" data-testid="task-record">
    <TaskRecord task={task} projectId={projectId} channelId={channelId} refresh={async()=>{await refresh();await onDetailChanged?.();}} open extra={extra} records={records}/>
   </div>
  </>:<>
   <header className="dv-panel-head">
    {/* Beside the Progress / Preview switch the tab already says Progress and the thread's head names the thread. */}
    {!props.switcher&&<><p className="dv-panel-kicker">{t('Progress')}</p>
    <h2 className="dv-panel-title" title={name}>{name??t('This thread')}</h2></>}
    <div className="dv-panel-state">
     {/* Running is Dervo itself thinking (Bryce, 23 Sep: the green orb had to go, he looks at it all day): the pebble's
         working smile, the same mark the composer and a reply's live step use, on the same 2.4 s beat. When this run
         ends here, the glyph that takes its place draws its check (StatusGlyph ``from``). */}
     {state.running?<WorkingMark working size={16} step={steps.find(step=>step.now)?.text}/>:<StatusGlyph state={state.glyph} from={endedHere?'running':undefined}/>}
     <span className="dv-state" data-state={state.glyph==='running'||state.glyph==='done'?state.glyph:undefined} role="status">{state.word}</span>
     {state.caption&&<span className="dv-caption tnum">{state.caption}</span>}
     {(state.running||state.working)&&!unavailable&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-panel-stop" disabled={stopping} onClick={()=>void stop()}>{stopping?t('Pausing…'):t('Pause')}</button>}
    </div>
    {error&&<p role="alert" className="dv-panel-error">{error}</p>}
   </header>
   {suggestion&&<HandoffSlot taskId={task.id}>{suggestion}</HandoffSlot>}
   {steps.length>0&&<section aria-label={t('Steps')} className="dv-panel-section">
    <h3 className="dv-label">{t('Steps')}</h3>
    {hidden>0&&<button type="button" className="dv-fold" onClick={()=>setExpanded(true)}><span className="dv-st" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M5 6.5L8 9.5l3-3"/></svg></span>{t('{count} earlier steps',{count:hidden})}</button>}
    <ol className="dv-steps" aria-label={t('Recorded steps')}>
     {steps.slice(hidden).map(step=><li key={step.id} className={`dv-step${step.later?' dv-later':''}`} data-now={step.now||undefined} data-glyph={step.glyph} aria-current={step.now?'step':undefined}>
      <StatusGlyph state={step.glyph}/>
      <span>{step.text}<FileNames files={step.files}/>{step.caption&&<small className="tnum">{step.caption}</small>}</span>
     </li>)}
    </ol>
   </section>}
   {files.length>0&&<section aria-label={state.running||state.working?t('Files so far'):t('Changed files')} className="dv-panel-section">
    <h3 className="dv-label">{state.running||state.working?t('Files so far'):t('Changed files')}</h3>
    <ul className="dv-files">{files.map(file=><li key={file.path} className="dv-file" title={file.path}>{props.onTraceFile?<button type="button" className="dv-file-trace" aria-label={t('Trace {path}',{path:file.path})} onClick={()=>props.onTraceFile!(file.path)}>{file.name}</button>:<span>{file.name}</span>}{file.delta&&<span className="dv-delta">{file.delta}</span>}</li>)}</ul>
   </section>}
   <Remembered key={task.id} task={task} projectId={projectId} channelId={channelId} onError={setError}/>
   <button type="button" className="dv-btn dv-btn-quiet dv-panel-told" onClick={()=>setView('told')}>{t('What it was told')}<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg></button>
   <button type="button" className="dv-btn dv-btn-quiet dv-panel-foot" onClick={()=>props.onOpenRecord?props.onOpenRecord():setView('record')}>{t('Open the record')}<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5.5 10.5l5-5M6.5 5.5h4v4"/></svg></button>
  </>}
 </aside>;
}
