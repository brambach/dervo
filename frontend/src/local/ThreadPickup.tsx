import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import type {PickupItem} from './Home';
import {stoppedTask,type CodingTask} from './CodingTasks';
import type {WorkerQuestion} from './workerQuestionState';
import {ActivityLine} from './ActivityLine';
import {RunningOrb} from './RunningOrb';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {isOverviewRequest,PROJECT_OVERVIEW_SUMMARY} from './projectOverview';
import './ThreadPickup.css';
import {formatDate,language,t} from '@/i18n';

/** The top of a thread (app #15, beta.16; approved 25 Sep, evidence/design-tests/b16-thread/summary-*): one strip that
 * says where the thread is. A small first line has the state (the running orb, or a glyph), its word, a time and Hide.
 * Under it, one sentence at reading size says what's going on.
 *
 * The sentence comes from records, never a model call (Bryce's pick in the design, 25 Sep): while it runs, the step
 * the agent keeps in its Task guide; while it asks, its question; once it's done, the first sentence of its reply, which
 * Dervo asks agents to make the outcome; and for a stop, Dervo's own line from the host (pickup.py, conversation_pickup).
 * With none of those, the old Asked, Did and Next rows stand in. Nothing shows for a thread that has never run
 * anything, and Hide puts it away until you next open the thread. */
/** A bug or idea line from the list's records (project_ideas.thread_lines): what this thread is working on, or what
 * your approval's commit marked done, or reopened when you undid the task and its commit. Undo on a done line puts each
 * item back to the state it was in (``was``), through the same ideas_item_set the Bugs and ideas page uses, with the
 * revision the host gave. */
export type WorkItem={id:string;kind:string;number:number;name:string;revision:number;target:string|null;was:string};
export type WorkLine={kind:'done'|'working'|'reopened';line:string;can_undo:boolean;undone:boolean;project_id:string;profile_id:string|null;items:WorkItem[]};
export type ThreadPickupData=PickupItem&{lines:string[];continuity?:string|null;note?:string|null;work?:WorkLine[]|null};

/** What Dervo did for this thread's newest message, under the recap (handoff-phase2 item 10): what the session got
 * (``continuity``) and a note the agent stopped before reading (``note``). The host writes both; blank ones are dropped. */
export function recapActivity(data:Pick<ThreadPickupData,'continuity'|'note'>):string[]{
 return [data.continuity,data.note].filter((line):line is string=>typeof line==='string'&&!!line.trim()).map(line=>line.trim());
}

/** The bug and idea lines the host sent, whole ones only; an older host sends none. */
export function workLines(data:Pick<ThreadPickupData,'work'>):WorkLine[]{
 return (Array.isArray(data.work)?data.work:[]).filter((line):line is WorkLine=>!!line&&typeof line==='object'&&typeof line.line==='string'&&!!line.line.trim()&&Array.isArray(line.items));
}

/** One line of plain text from a request that may carry Markdown: no backticks, stars, link brackets or heading marks. */
export function plainLine(text:string):string{
 return text.replace(/```[\s\S]*?```/g,' ').replace(/`([^`]*)`/g,'$1').replace(/\*\*([^*]+)\*\*/g,'$1').replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|$|[.,;:])/g,'$1$2')
  .replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/^#{1,6}\s+/gm,'').replace(/\s+/g,' ').trim();
}

/** The rows the recap shows, in order. A running thread's Did is its current step, so that row is called Now. */
export function recapRows(data:Pick<PickupItem,'asked'|'did'|'next'|'kind'>,overview=false):{label:string;text:string}[]{
 return [data.asked?{label:t('Asked'),text:isOverviewRequest(data.asked,overview,true)?PROJECT_OVERVIEW_SUMMARY:plainLine(data.asked)}:null,
  data.did?{label:data.kind==='running'?t('Now'):t('Did'),text:plainLine(data.did)}:null,
  data.next?{label:t('Next'),text:plainLine(data.next)}:null].filter((row):row is {label:string;text:string}=>!!row&&!!row.text);
}

/** What the strip says: its mark (the orb while it runs), the state's word, a time, and the one sentence. */
export type ThreadSummary={mark:GlyphState|'orb';accent:boolean;state:string;word:string;caption:string;text:string;
 /** The thread already says this lower down: the live trail while it runs, the reply's first sentence once it's done,
  * the thread's strip then stays out, so one outcome isn't said three times. */
 below?:boolean};
/** ``question.saved``: your answer is saved on this device and the agent hasn't confirmed it yet. */
export type SummaryFrom={task?:(Pick<CodingTask,'messages'|'runs'>&{state?:string})|null;who?:string;question?:Pick<WorkerQuestion,'ask'|'createdAt'>&{saved?:boolean}|null;now?:number};

const record=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'?value as Record<string,unknown>:{};
/** A pickup read this recent is current: the tasks landing after it aren't a change in the thread. */
export const PICKUP_FRESH_MS=600;
const stop=(text:string)=>/[.?!…]$/.test(text)?text:`${text}.`;
/** The Task guide's step for the task's newest run, only when it was written in that run. */
function guideStep(task:SummaryFrom['task']):string{
 const run=record((task?.runs??[]).at(-1)),guide=record(run.guide);
 return guide.run_id===run.id&&typeof guide.step==='string'?plainLine(guide.step):'';
}
/** The first sentence of the agent's last reply that reads as a sentence: headings, labels and bullets are skipped. */
export function firstSentence(text:string|null|undefined,limit=220):string{
 for(const raw of (text??'').split(/\n+/)){
  const line=plainLine(raw.replace(/^\s*(?:[-*•]|\d+[.)])\s+/,''));
  if(line.split(' ').length<4||/^#/.test(raw.trim()))continue;
  const found=line.match(/^.+?[.?!](?=\s|$)/)?.[0]??line;
  const chars=Array.from(found);
  return chars.length>limit?`${chars.slice(0,limit-1).join('').replace(/\s+\S*$/,'')}…`:found;
 }
 return '';
}
/** What the reply says is missing: the first item under its "Not checked" heading, or on a "Not checked:" line, as
 * "Not checked: The frontend." Empty when the reply doesn't say. */
export function missingSentence(text:string|null|undefined):string{
 const lines=(text??'').split(/\n+/);
 for(const [index,raw] of lines.entries()){
  const label=plainLine(raw.replace(/^\s*#+\s*/,'')).match(/^(?:Not checked|Didn[’']t check|Not done|Still missing)\s*:?\s*(.*)$/i);
  if(!label)continue;
  const item=label[1]?label[1]:lines.slice(index+1).find(line=>line.trim())??'';
  const found=firstSentence(item,160)||plainLine(item.replace(/^\s*(?:[-*•]|\d+[.)])\s+/,''));
  return found?stop(t('Not checked: {found}',{found})):'';
 }
 return '';
}
/** "Codex is running the Node check." from the guide's step "Running the Node check"; any other step as it's written. */
export function stepSentence(step:string,who?:string):string{
 const [first='',...rest]=step.split(' ');
 // i18n-ignore: the step is the agent’s own words, usually English; only an English window turns "Running…" into a sentence.
 if(who&&language()==='en'&&/^[A-Z][a-z]+ing$/.test(first))return stop(`${who} is ${first.toLowerCase()}${rest.length?` ${rest.join(' ')}`:''}`);
 return stop(step);
}
const minutes=(seconds:number)=>Math.max(1,Math.round(seconds/60));
/** How long it's been running: "41 min", "1 h 5 min". */
export function runningFor(since:number|null|undefined,now=Date.now()/1000):string{
 if(typeof since!=='number'||since<=0||since>now+60)return '';
 // Under a minute says nothing: "1 min" three seconds into a run read as wrong.
 if(now-since<60)return '';
 const total=minutes(now-since);
 return total<60?t('{total} min',{total}):total%60?t('{hours} h {minutes} min',{hours:Math.floor(total/60),minutes:total%60}):t('{hours} h',{hours:Math.floor(total/60)});
}
/** When something happened: "just now", "2 min ago", or the clock ("1:52 AM"), with the day once it's not today. */
function agoOrClock(at:number|null|undefined,now:number,clockOnly=false):string{
 if(typeof at!=='number'||at<=0)return '';
 if(!clockOnly&&now-at<60)return t('just now');
 if(!clockOnly&&now-at<3600)return t('{minutes} min ago',{minutes:minutes(now-at)});
 const when=new Date(at*1000),clock=formatDate(when,{hour:'numeric',minute:'2-digit'});
 return when.toDateString()===new Date(now*1000).toDateString()?clock:`${formatDate(when,{weekday:'short'})} ${clock}`;
}

/** The strip for one thread, from the host's pickup and the thread's newest task. Null for a thread with no work yet. */
export function threadSummary(data:Pick<PickupItem,'kind'|'needs'|'did'|'line'|'now'|'next'|'next_by'|'question'|'moved'|'running_since'|'failed'>&Partial<Pick<PickupItem,'ending'|'paused_by'>>,from:SummaryFrom={}):ThreadSummary|null{
 const now=from.now??Date.now()/1000,who=from.who;
 const reply=[...(from.task?.messages??[])].reverse().find(message=>message.role==='assistant')?.text;
 // Done, then what's missing when the reply says (the design's "what was done and what's missing").
 const done=firstSentence(reply),missing=done?missingSentence(reply):'';
 const outcome=done?[done,missing].filter(Boolean).join(' '):plainLine(data.did??data.line??'');
 const at=agoOrClock(data.moved,now,true);
 // Said lower down only when the outcome is the reply's own first sentence and nothing's missing from it: a host line or
 // a "Not checked" isn't on screen anywhere else (review, 8 Oct).
 const said=!!done&&!missing;
 // Waiting on your answer: the question in the tray, or the one its reply ended on.
 const asking=from.question?plainLine(from.question.ask):data.question&&data.next_by==='agent'&&data.next?plainLine(data.next):'';
 // Whether it's running comes from the live task when there is one, not the host's pickup, which is read every 15 s:
 // the orb stops with the run and starts with the next (behaviour review S4, 25 Sep). The component re-reads the pickup
 // whenever the task changes, so these stand-ins last a moment.
 const live=typeof from.task?.state==='string'?from.task.state:null;
 const kind=live==='working'?'running':live&&data.kind==='running'
  ?({ready_for_review:'review',needs_permission:'permission',blocked:'stopped',answered:'answered'} as Record<string,string>)[live]??'finished'
  :data.kind;
 // The question itself stays in the reply and the tray; the strip says what was done and that it waits on you, so the
 // same words aren't on screen twice (design review S1, 25 Sep).
 if(asking&&kind!=='running'||from.question){
  const agent=who??t('The agent');
  const lead=done&&!asking.includes(done.replace(/[.?!…]$/,''))&&!done.includes(asking.replace(/[.?!…]$/,''))?`${done} `:'';
  const text=from.question?.saved?t('{lead}{agent} hasn’t picked up your answer yet.',{lead,agent})
   :from.question?t('{lead}{agent} is waiting on your answer below.',{lead,agent}):t('{lead}{agent} ended its reply with a question for you.',{lead,agent});
  return {mark:'needs',accent:true,state:'needs',word:t('Needs you'),
   caption:from.question?.saved?t('Answer saved'):t('Asked {when}',{when:agoOrClock(from.question?.createdAt??data.moved,now)}).trim(),text};
 }
 switch(kind){
  case 'running':return {below:true,mark:'orb',accent:false,state:'running',word:t('Running'),caption:runningFor(data.running_since,now),
   text:stepSentence(guideStep(from.task)||plainLine(data.now??'')||t('Working'),who)};
  case 'review':return {mark:'needs',accent:true,state:'needs',word:t('Ready for your review'),caption:at&&t('Finished {at}',{at}),text:outcome};
  case 'finished':return {below:said,mark:'done',accent:false,state:'done',word:t('Finished'),caption:at,text:outcome};
  case 'answered':return {below:said,mark:'done',accent:false,state:'done',word:t('Answered'),caption:at,text:outcome};
  case 'approved':return {below:said,mark:'done',accent:false,state:'done',word:t('Approved'),caption:at,text:outcome};
  case 'permission':return {mark:'needs',accent:true,state:'needs',word:t('Waiting for your permission'),caption:'',text:plainLine(data.did??t('Waiting for your permission to start.'))};
  case 'stopped':
   // Beta.17.1 (run_ending): finished work with a Dervo step left undone, a limit and a run that never started aren't failures.
   if(!data.failed&&data.ending==='finished')return {mark:'needs',accent:false,state:'needs',word:t('Finished'),caption:at,text:plainLine(data.did??data.line??'')};
   return {mark:data.failed?'failed':'stopped',accent:false,state:data.failed?'failed':'stopped',
    word:data.failed?t('Stopped'):data.ending==='limit'?t('Stopped at a limit'):data.ending==='not_started'?t('Didn’t start'):data.paused_by==='restart'?t('Paused'):data.paused_by==='left'?t('Left stopped'):data.ending==='stopped'?t('Paused'):t('Stopped'),caption:at,text:plainLine(data.did??data.line??'')};
  // Beta.18: Dervo paused it when it quit (the card says Paused, so the strip does too; it said Not confirmed up to 17.1).
  case 'unconfirmed':if(data.ending==='stopped')return {mark:'stopped',accent:false,state:'stopped',word:t('Paused'),caption:at,text:plainLine(data.did??data.line??'')};
   // Beta.17.1: Dervo lost track, so the outcome is unknown, not failed (backend/run_ending.py).
   return {mark:'unknown',accent:false,state:'unknown',word:t('Not confirmed'),caption:at,text:plainLine(data.did??data.line??'')};
  default:return null;
 }
}

export type ThreadPickupProps={projectId:string;channelId:string;overview?:boolean;
 /** The thread's newest task, for the guide's step and the reply's first sentence. */
 task?:SummaryFrom['task'];
 /** Who works in this thread: "Codex", "Claude Code". */
 who?:string;
 /** The question in the thread's tray, if one waits on you. */
 question?:SummaryFrom['question'];
 /** The thread's head says the state (ThreadLive), so the strip keeps only its sentence and Hide: status once. */
 headless?:boolean};

export function ThreadPickup({projectId,channelId,overview=false,task,who,question,headless=false}:ThreadPickupProps){
 const [hidden,setHidden]=useState(false);
 const [undoing,setUndoing]=useState(false);
 const [undoFailed,setUndoFailed]=useState<Record<number,string>>({});
 const client=useQueryClient();
 const key=['conversation-pickup',projectId,channelId];
 const pickup=useQuery({queryKey:key,enabled:!!projectId&&!!channelId,refetchInterval:15000,
  queryFn:()=>local<ThreadPickupData>('conversation_pickup',{projectId,channelId})});
 // A run starting, ending or being stopped changes the task at once; the pickup follows it straight away.
 const moved=`${task?.state??''}:${task?.runs?.length??0}`;
 // Not while the first read is on its way or has just landed: opening a thread read the pickup two or three times (the mount's
 // own read, this effect, then the tasks arriving), and the host answers one request at a time.
 useEffect(()=>{
  const state=client.getQueryState(key);
  if(state?.fetchStatus==='fetching'||(state?.dataUpdatedAt&&Date.now()-state.dataUpdatedAt<PICKUP_FRESH_MS))return;
  void client.invalidateQueries({queryKey:key});
 },[moved]);
 const data=pickup.data;
 const summary=data?threadSummary(data,{task,who,question}):null;
 const shown=summary?.text?summary:null;
 // Under the thread's head the strip speaks only when the thread doesn't already say it (polish, 8 Oct).
 // A paused run has its own card in the thread that says so, with Resume, so that counts as said too: on exactly the
 // test CodingTasks draws that card by, or a stop that left no card would leave nothing at all (review, 8 Oct).
 const echo=headless&&!!shown&&(!!shown.below||(!!task?.state&&stoppedTask(task as Parameters<typeof stoppedTask>[0])));
 const rows=data&&!shown?recapRows(data,overview):[];
 const activity=data?recapActivity(data):[];
 const work=data?workLines(data):[];
 if(hidden||(!shown&&!rows.length&&!activity.length&&!work.length)||(echo&&!activity.length&&!work.length))return null;
 // Undo puts every item of the line back to where it was; the first refusal is shown in place of the line's text.
 async function undo(line:WorkLine,index:number){
  if(undoing||!line.profile_id)return;
  setUndoing(true);
  try{
   for(const item of line.items)await local('ideas_item_set',{projectId:line.project_id,profileId:line.profile_id,itemId:item.id,expectedRevision:item.revision,name:item.name,state:item.was||'building',target:item.target});
   await client.invalidateQueries({queryKey:key});
   await client.invalidateQueries({queryKey:['bugs-items']});
  }catch(failure){setUndoFailed(previous=>({...previous,[index]:failure instanceof Error?failure.message:String(failure)}));}
  finally{setUndoing(false);}
 }
 return <section className="dervo-thread-recap" aria-label={t('Where this thread is')}>
  {shown&&headless&&!echo&&<div className="dv-summary" data-state={shown.state} data-headless="">
   <p className="dv-summary-text">{shown.text}</p>
   <button type="button" className="dv-summary-hide" onClick={()=>{
    // The button leaves with the strip, so hand focus to the head pill rather than drop it on the page.
    document.querySelector<HTMLElement>('button[data-testid="thread-live"]')?.focus();setHidden(true);}}>{t('Hide')}</button>
  </div>}
  {shown&&!headless&&<div className="dv-summary" data-state={shown.state}>
   <div className="dv-summary-head">{shown.mark==='orb'?<RunningOrb/>:<StatusGlyph state={shown.mark} accent={shown.accent}/>}
    <span className="dv-state" data-state={shown.state}>{shown.word}</span>{shown.caption&&<span className="dv-summary-time">{shown.caption}</span>}
    <button type="button" onClick={()=>setHidden(true)}>{t('Hide')}</button></div>
   <p className="dv-summary-text">{shown.text}</p>
  </div>}
  {rows.length>0&&<dl className="dv-recap">
   {rows.map((row,index)=><div key={row.label} className="dv-recap-row">
    <dt>{row.label}</dt>
    <dd><span title={row.text}>{row.text}</span>{index===0&&<button type="button" onClick={()=>setHidden(true)}>{t('Hide')}</button>}</dd>
   </div>)}
  </dl>}
  {(activity.length>0||work.length>0)&&<div className="dv-activity-list">
   {activity.map(line=><ActivityLine key={line} text={line}/>)}
   {work.map((line,index)=><ActivityLine key={`work:${index}`} text={undoFailed[index]??line.line} done={line.undone?t('Undone'):null}
    action={line.can_undo&&!line.undone&&!undoFailed[index]&&line.profile_id?{label:t('Undo'),describe:t('Undo: {line}',{line:line.line}),disabled:undoing,onClick:()=>void undo(line,index)}:null}/>)}
  </div>}
 </section>;
}
