import {useEffect,useMemo,useRef,useState,type FormEvent} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {MomentMark} from './BrandMoment';
import {StatusGlyph} from './StatusGlyph';
import {local,type Conversation} from './api';
import type {ArchivedConversation} from './ConversationLifecycle';
import {answerHistory,askedWhen,blankState,candidateLine,collapseLine,foundLede,historyDeclined,lookHistory,madeNew,namesFor,nameFor,previewHistory,readHistory,rememberHistoryDeclined,setAsideLine,sortingLead,sourcesLabel,threadWhen,tildePath,
 type BlankState,type HistoryCandidate,type HistoryImport,type HistoryLook,type HistoryWorkstream} from './historyImport';
import './ImportHistory.css';
import {formatDate,t,tr} from '@/i18n';

/** Whether to offer reading this project's history, from the host's two lists (historyImport.blankState). The look at
 * what's on disk happens only once the project is known to be blank and you haven't said no; a host without the reader
 * refuses it, and that reads as nothing to offer, so the project is a normal empty one. */
export function useHistoryOffer(projectId:string,live:Conversation[]|undefined):{state:BlankState;archived:number;look:HistoryLook|null;decline:()=>void;finish:()=>void}{
 const client=useQueryClient();
 const [,tick]=useState(0);
 // The same key the sidebar's Archived list keeps, so the two never disagree.
 const archived=useQuery({queryKey:['archived-conversations',projectId],enabled:!!projectId&&!!live&&live.length===0,
  queryFn:()=>local<ArchivedConversation[]>('conversations_archived',{projectId})});
 const state=blankState(live,live&&live.length?[]:archived.data);
 const declined=historyDeclined(projectId);
 const look=useQuery({queryKey:['history-look',projectId],enabled:state==='blank'&&!declined,retry:false,staleTime:60000,queryFn:()=>lookHistory(projectId)});
 const fresh=state==='blank'&&!declined&&look.isSuccess&&!!look.data&&look.data.conversations>0?look.data:null;
 // Once offered, the offer holds until the flow says it's done or you decline: the reader makes the project's
 // conversations while it reads, and the live list filling up must not swap the screens out from under the read.
 // Leaving the project lets go of it; coming back finds threads, so nothing is offered twice.
 const held=useRef<{projectId:string;look:HistoryLook}|null>(null);
 if(held.current&&held.current.projectId!==projectId)held.current=null;
 if(fresh&&!held.current)held.current={projectId,look:fresh};
 const offer=held.current?.look??null;
 return {state,archived:archived.data?.length??0,look:offer,
  decline(){held.current=null;rememberHistoryDeclined(projectId);tick(n=>n+1);},
  finish(){held.current=null;tick(n=>n+1);void client.invalidateQueries({queryKey:['local-conversations',projectId]});void client.invalidateQueries({queryKey:['project-ideas']});void client.invalidateQueries({queryKey:['project-pickup']});}};
}

/** Reading what's already here, in the main panel of a blank project (the mockup, 20 Sep, built per project on 21 Sep).
 * Four screens and then Home, no congratulations: Found is the offer and asks first; Reading plays back what the reader
 * looked at and what it kept; Sorting lands each workstream with the rule that placed it and folds it to a line, which is
 * what lets it survive a real result; Answer closes the one loop that was left open, with one keystroke. Every timing
 * here is playback of a result the host already produced, so nothing on screen is a guess about what it's doing. */

export type Stage='found'|'choose'|'reading'|'sorting'|'ask';
type Props={projectId:string;projectName:string;look:HistoryLook;onDecline:()=>void;onDone:()=>void};

// Playback paces in ms, from the mockup. The stream takes about three seconds however long it is. Tests shorten these.
export const pace={stream:3000,step:66,count:3000,settle:1200,workstream:190,thread:130,gap:90,answered:1400};
const ease=(t:number)=>1-(1-t)**3;
export const KEPT_SHOWN=8;

/** The Sorting screen's steps in order: each workstream's head, then its threads, then the fold. */
export function sortingSteps(found:HistoryImport):{at:number;kind:'head'|'thread'|'fold';ws:number;thread?:number}[]{
 const steps:{at:number;kind:'head'|'thread'|'fold';ws:number;thread?:number}[]=[];
 let t=0;
 found.workstreams.forEach((ws,index)=>{
  if(index>0)steps.push({at:t,kind:'fold',ws:index-1});
  steps.push({at:t,kind:'head',ws:index});
  t+=pace.workstream;
  ws.threads.forEach((_,thread)=>{steps.push({at:t,kind:'thread',ws:index,thread});t+=pace.thread;});
  t+=pace.gap;
 });
 if(found.workstreams.length)steps.push({at:t+120,kind:'fold',ws:found.workstreams.length-1});
 return steps;
}

function useTimers(){
 const timers=useRef<ReturnType<typeof setTimeout>[]>([]);
 const clear=()=>{timers.current.forEach(clearTimeout);timers.current=[];};
 useEffect(()=>clear,[]);
 return {at:(ms:number,fn:()=>void)=>{timers.current.push(setTimeout(fn,ms));},clear};
}

export function ImportHistory({projectId,projectName,look,onDecline,onDone}:Props){
 const [stage,setStage]=useState<Stage>('found');
 const [found,setFound]=useState<HistoryImport|null>(null);
 const [readError,setReadError]=useState('');
 const [reading,setReading]=useState(false);
 const [candidates,setCandidates]=useState<HistoryCandidate[]>([]);
 const [selected,setSelected]=useState<Set<string>>(new Set());
 const [limit,setLimit]=useState(50);
 const [showOlder,setShowOlder]=useState(false);
 // Sessions a tool ran: off unless you tick it (NEXT-UP #38), and only offered when the folder has some.
 const [toolRuns,setToolRuns]=useState(0);
 const [withTools,setWithTools]=useState(false);
 // Names you typed for the conversations the rules couldn't name (NEXT-UP #37), by session.
 const [names,setNames]=useState<Record<string,string>>({});
 const timers=useTimers();
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return ()=>{alive.current=false;};},[]);

 // ---------------------------------------------------------------- 1. Found: the offer
 async function choose(includeToolRuns=withTools){
  if(reading)return;
  setReading(true);setReadError('');
  try{
   const result=await previewHistory(projectId,includeToolRuns);
   if(!alive.current)return;
   setCandidates(result.candidates);setLimit(result.limit);setToolRuns(result.toolRuns??0);setWithTools(includeToolRuns);
   // The first time, the open ones are ticked. Turning tool runs on or off keeps what you'd ticked that's still listed,
   // and ticks the open tool runs you just asked for, the same rule as the rest.
   setSelected(before=>new Set(result.candidates.filter(row=>stage==='choose'?before.has(row.sessionId)||(row.toolRun&&row.recommended):row.recommended).map(row=>row.sessionId)));
   setStage('choose');
  }catch(failure){if(alive.current)setReadError(failure instanceof Error?failure.message:String(failure));}
  finally{if(alive.current)setReading(false);}
 }
 async function read(){
  if(reading)return;
  setReading(true);setReadError('');setStage('reading');
  try{
   const result=await readHistory(projectId,[...selected],{names:namesFor(candidates,selected,names),includeToolRuns:withTools});
   if(!alive.current)return;
   setFound(result);
  }catch(failure){
   if(!alive.current)return;
   setReadError(failure instanceof Error?failure.message:String(failure));
  }finally{if(alive.current)setReading(false);}
 }

 // ---------------------------------------------------------------- 2. Reading: playback of what the reader saw
 const [counts,setCounts]=useState({conversations:0,sessions:0,working:0});
 const [shown,setShown]=useState(0);
 const [settled,setSettled]=useState(false);
 const stream=useRef<HTMLDivElement>(null);
 useEffect(()=>{
  if(stage!=='reading'||!found)return;
  timers.clear();
  setShown(0);setSettled(false);
  const start=performance.now();
  let frame=0;
  const tick=(now:number)=>{
   const t=Math.min(1,(now-start)/pace.count),e=ease(t);
   setCounts({conversations:Math.round(found.counts.conversations*e),sessions:Math.round(found.counts.sessions*e),working:Math.round(found.counts.working*e)});
   if(t<1)frame=requestAnimationFrame(tick);
  };
  frame=requestAnimationFrame(tick);
  const step=Math.min(pace.step,pace.stream/Math.max(1,found.read.length));
  found.read.forEach((_,index)=>timers.at(index*step,()=>setShown(index+1)));
  const end=found.read.length*step+120;
  timers.at(end,()=>setSettled(true));
  timers.at(end+pace.settle,()=>setStage('sorting'));
  return ()=>cancelAnimationFrame(frame);
 },[stage,found]);
 useEffect(()=>{const node=stream.current;if(node)node.scrollTop=node.scrollHeight;},[shown]);

 // ---------------------------------------------------------------- 3. Sorting: workstreams land, then fold
 const [landed,setLanded]=useState(0);
 const [folded,setFolded]=useState<Set<number>>(new Set());
 const [sorted,setSorted]=useState(false);
 const steps=useMemo(()=>found?sortingSteps(found):[],[found]);
 useEffect(()=>{
  if(stage!=='sorting'||!found)return;
  timers.clear();
  setLanded(0);setFolded(new Set());setSorted(false);
  steps.forEach((step,index)=>timers.at(step.at,()=>{
   if(step.kind==='fold')setFolded(previous=>new Set(previous).add(step.ws));
   else setLanded(index+1);
  }));
  const last=steps.at(-1)?.at??0;
  timers.at(last+200,()=>setSorted(true));
 },[stage,found]);
 const visible=(ws:number,thread?:number)=>{
  const index=steps.findIndex(step=>step.kind!=='fold'&&step.ws===ws&&(thread===undefined?step.kind==='head':step.thread===thread));
  return index>=0&&index<landed;
 };

 // ---------------------------------------------------------------- 4. Answer: one keystroke
 const question=found?.question??null;
 const [picked,setPicked]=useState<number|null>(null);
 const [answered,setAnswered]=useState(false);
 const [answerError,setAnswerError]=useState('');
 const [typed,setTyped]=useState('');
 async function answer(text:string,index:number|null){
  if(answered||!question||!text.trim())return;
  setAnswered(true);setPicked(index);setAnswerError('');
  try{
   const sent=await answerHistory(projectId,question.thread_id,text.trim());
   if(!alive.current)return;
   if(sent.id===null)throw new Error(sent.detail||t('The thread couldn’t start.'));
   timers.at(pace.answered,onDone);
  }catch(failure){
   if(!alive.current)return;
   setAnswered(false);setPicked(null);
   setAnswerError(failure instanceof Error?failure.message:String(failure));
  }
 }
 useEffect(()=>{
  if(stage!=='ask'||!question?.choices.length)return;
  const key=(event:KeyboardEvent)=>{
   const n=Number(event.key);
   if(!Number.isInteger(n)||n<1||n>question.choices.length||event.metaKey||event.ctrlKey||event.altKey)return;
   if((event.target as HTMLElement|null)?.tagName==='INPUT')return;
   event.preventDefault();
   void answer(question.choices[n-1],n-1);
  };
  window.addEventListener('keydown',key);
  return ()=>window.removeEventListener('keydown',key);
 },[stage,question,answered]);
 function submitTyped(event:FormEvent){event.preventDefault();void answer(typed,null);}

 const lede=foundLede(look);
 const folder=tildePath(look.folder);
 const total=found?found.workstreams.reduce((n,ws)=>n+ws.threads.length,0):0;
 const lead=found?sortingLead(found):null;
 // The rule column shows why each workstream landed where it did. On a first import every row says "new workstream",
 // because nothing existed to match, so the column explains nothing and the lead says it once instead (21 Sep).
 const rulesDiffer=!!found&&new Set(found.workstreams.map(ws=>ws.rule)).size>1;

 return <section className="dv-import" data-stage={stage} aria-label={t('Reading what’s already here')}>
  {stage==='found'&&<div className="dv-import-stage dv-import-found">
   <div className="dv-import-hero">
    <MomentMark size={40} enter/>
    <h1 className="dv-display-hero">{t('You’ve been here before')}</h1>
    <p className="dv-lede">{(()=>{const parts={count:<b className="dv-import-n">{lede.count}</b>,sources:sourcesLabel(look.sources),where:<b className="dv-import-n">{lede.where}</b>,span:lede.span?`, ${lede.span}`:''};
     return look.conversations===1?tr('There is {count} from {sources} in {where}{span}.',parts):tr('There are {count} from {sources} in {where}{span}.',parts);})()}</p>
    <div className="dv-import-acts">
     <button type="button" className="dv-btn dv-btn-primary" disabled={reading} onClick={()=>void choose()}>{reading?t('Reading history…'):t('Choose conversations')}</button>
     <button type="button" className="dv-btn dv-btn-ghost" onClick={onDecline}>{t('Start empty instead')}</button>
    </div>
    <p className="dv-import-fine">{t('Dervo reads the files {sources} already wrote to this Mac. Nothing is uploaded and no model is called. You choose which conversations to bring in before Dervo creates anything.',{sources:sourcesLabel(look.sources)})}</p>
   </div>
  </div>}

  {stage==='found'&&readError&&<p role="alert">{readError}</p>}
  {stage==='choose'&&<div className="dv-import-stage dv-import-choose">
   <h1 className="dv-display-hero">{t('Choose what to bring with you')}</h1>
   <p className="dv-lede">{t('Start with a few recent conversations. Each comes with a history brief so you can see where it left off.')}</p>
   {(toolRuns>0||withTools)&&<label className="dv-import-tools">
    <input type="checkbox" checked={withTools} disabled={reading} onChange={event=>void choose(event.target.checked)}/>
    <span><b>{t('Include sessions run by a tool ({count})',{count:toolRuns})}</b>
     <small>{t('Runs a script or another app started, like codex exec. They come in marked as the tool’s, not your words.')}</small></span>
   </label>}
   <div className="dv-import-selection" role="group" aria-label={t('Conversations to import')}>
    {candidates.filter(row=>showOlder||row.recommended).map(row=><label key={row.sessionId} className="dv-import-choice" data-unnamed={row.unnamed||undefined} data-tool={row.toolRun||undefined}>
     <input type="checkbox" checked={selected.has(row.sessionId)} disabled={!selected.has(row.sessionId)&&selected.size>=limit}
      onChange={event=>setSelected(before=>{const next=new Set(before);if(event.target.checked)next.add(row.sessionId);else next.delete(row.sessionId);return next;})}/>
     <span>{row.unnamed?<b className="dv-import-unnamed">{t('Couldn’t safely generate a name')}</b>:<b>{row.name}</b>}
      <small>{candidateLine(row,row.reason)}</small><p>{row.excerpt}</p>
      {row.unnamed&&<input className="dv-input dv-import-name" value={names[row.sessionId]??''} maxLength={100} placeholder={nameFor(row,'')}
       aria-label={t('Name for this conversation')} onChange={event=>{const value=event.target.value;setNames(before=>({...before,[row.sessionId]:value}));}}/>}
     </span>
    </label>)}
    {!candidates.length&&<p>{t('There aren’t any new conversations to bring in.')}</p>}
    {!!candidates.length&&!candidates.some(row=>row.recommended)&&!showOlder&&<p>{t('No recent open conversations. You can browse older ones or start empty.')}</p>}
   </div>
   {candidates.some(row=>!row.recommended)&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setShowOlder(value=>!value)}>{showOlder?t('Show recommended only'):t('Browse all conversations')}</button>}
   <div className="dv-import-acts">
    <button type="button" className="dv-btn dv-btn-primary" disabled={!selected.size||reading} onClick={()=>void read()}>{t('Import {count} conversations',{count:selected.size})}</button>
    <button type="button" className="dv-btn dv-btn-ghost" onClick={onDecline}>{t('Start empty instead')}</button>
   </div>
   {readError&&<p className="dv-err" role="alert">{readError}</p>}
   <p className="dv-import-fine">{t('Up to {limit} at a time. Nothing runs until you send a message.',{limit})}</p>
  </div>}
  {stage==='reading'&&<div className="dv-import-stage">
   <div className="dv-import-read">
    <div className="dv-import-counts" aria-hidden={found?undefined:true}>
     <span className="dv-import-c"><b>{counts.conversations.toLocaleString()}</b><small>{t('conversations')}</small></span>
     <span className="dv-import-c"><b>{counts.sessions.toLocaleString()}</b><small>{t('sessions')}</small></span>
     <span className="dv-import-c" data-keep><b>{counts.working.toLocaleString()}</b><small>{t('being worked on')}</small></span>
    </div>
    {readError?<div className="dv-import-failed" role="alert">
     <p className="dv-read">{t('Couldn’t read the history in {folder}. {why}',{folder,why:readError})}</p>
     <div className="dv-import-acts">
      <button type="button" className="dv-btn dv-btn-primary" onClick={()=>void read()}>{t('Try again')}</button>
      <button type="button" className="dv-btn dv-btn-ghost" onClick={onDecline}>{t('Start empty instead')}</button>
     </div>
    </div>
    :!found?<p className="dv-import-wait" role="status"><StatusGlyph state="running"/>{t('Reading {folder}',{folder})}</p>
    :<div className="dv-import-split">
     <div><div className="dv-label dv-import-h" role="status">{settled?setAsideLine(found):t('Reading')}</div>
      <div className="dv-import-stream" ref={stream}>{found.read.slice(0,shown).map((line,index)=><div key={index} className="dv-import-l" data-drop={line.kept?undefined:''}>{line.label}</div>)}</div></div>
     <div><div className="dv-label dv-import-h">{settled?t('Kept'):t('Keeping')}</div>
      {/* The newest eight kept: a real folder keeps more than the column holds, so the oldest give way at the top. */}
      {/* Keyed by position: two real sessions can carry the same saved title. */}
      <div className="dv-import-kept">{found.read.slice(0,shown).filter(line=>line.kept).slice(-KEPT_SHOWN).map((line,index)=><div key={index} className="dv-import-k"><b>{line.label}</b></div>)}</div></div>
    </div>}
   </div>
  </div>}

  {stage==='sorting'&&found&&<div className="dv-import-stage">
   <div className="dv-import-sort">
    <div className="dv-import-sort-head">
     <h2 className="dv-display-2">{t('Sorting')}</h2>
     <p className="dv-import-sort-lead" aria-live="polite">{sorted&&lead?<><b>{lead.bold}</b> {lead.rest}</>:t('Naming each thread, then filing it under a workstream. New ones get made where nothing fits.')}</p>
    </div>
    <div className="dv-import-tree">
     <div className="dv-import-proj-head"><b>{projectName}</b><code>{folder}</code><span className="dv-import-pc">{collapseLine(found.counts)}</span></div>
     {found.workstreams.map((ws,index)=>visible(index)&&<Workstream key={ws.id} ws={ws} folded={folded.has(index)} rule={rulesDiffer} shown={ws.threads.filter((_,thread)=>visible(index,thread)).length}/>)}
    </div>
    <div className="dv-import-foot" data-on={sorted||undefined}>
     <span>{t('Every group here was made by a rule, not a model. Move anything and it stays moved.')}</span>
     {question?<button type="button" className="dv-btn dv-btn-primary" disabled={!sorted} onClick={()=>setStage('ask')}>{t('One thing needs you')}</button>
      :<button type="button" className="dv-btn dv-btn-primary" disabled={!sorted} onClick={onDone}>{t('Open Home')}</button>}
    </div>
    <p className="sr-only">{t('{count} threads filed.',{count:total})}</p>
   </div>
  </div>}

  {stage==='ask'&&question&&<div className="dv-import-stage">
   <div className="dv-import-ask">
    <div className="dv-import-ask-head">
     <h2 className="dv-display-2">{t('One thing was left unanswered')}</h2>
     <p>{tr('From {workstream}, {when}. It stopped here and waited.',{workstream:<b>{question.workstream}</b>,when:askedWhen(question.asked_at)})}</p>
    </div>
    <div className="dv-sheet">
     <article className="dv-row dv-open">
      <StatusGlyph state={answered?'running':'needs'} accent={!answered}/>
      <div>
       <div className="dv-row-name">{question.thread_name}</div>
       <div className="dv-row-line">{t('Asked you one thing before it carries on.')}</div>
       <dl className="dv-facts">
        <dt>{t('Asked')}</dt><dd>{question.asked}</dd>
        {question.did&&<><dt>{t('Did')}</dt><dd>{question.did}</dd></>}
        <dt>{t('Next')}</dt><dd>“{question.question}” {t('Answer to continue.')}</dd>
       </dl>
       {question.choices.length>0?<div className="dv-import-choices" role="group" aria-label={t('Your answer')}>
        {question.choices.map((choice,index)=><button key={index} type="button" className="dv-import-choice" data-picked={picked===index||undefined} disabled={answered} onClick={()=>void answer(choice,index)}><kbd>{index+1}</kbd>{choice}</button>)}
       </div>
       :<form className="dv-import-typed" onSubmit={submitTyped}>
        <input className="dv-input" aria-label={t('Your answer')} value={typed} disabled={answered} onChange={event=>setTyped(event.target.value)} placeholder={t('Your answer')}/>
        <button type="submit" className="dv-btn dv-btn-primary" disabled={answered||!typed.trim()}>{t('Answer')}</button>
       </form>}
       {answerError&&<p className="dv-err dv-import-hint" role="alert">{t('Couldn’t send your answer. {why}',{why:answerError})}</p>}
       {answered?<p className="dv-import-done" role="status">{t('Answered. Picking the thread back up where it stopped.')}</p>
        :<p className="dv-import-hint">{question.choices.length?t('Press a key, or click. Nothing runs until you answer.'):t('Nothing runs until you answer.')}</p>}
      </div>
      <div className="dv-row-end"><span className="dv-state" data-accent={!answered||undefined} data-state={answered?'running':undefined}>{answered?t('Running'):t('Needs you')}</span><span className="dv-when">{formatDate(new Date(question.asked_at*1000),{hour:'numeric',minute:'2-digit'})}</span></div>
     </article>
    </div>
    {!answered&&<div className="dv-import-later"><button type="button" className="dv-btn dv-btn-quiet" onClick={onDone}>{t('Not now')}</button></div>}
   </div>
  </div>}
 </section>;
}

function Workstream({ws,folded,rule,shown}:{ws:HistoryWorkstream;folded:boolean;rule:boolean;shown:number}){
 const open=ws.threads.filter(thread=>thread.open).length;
 return <div className="dv-import-ws" data-folded={folded||undefined} data-rule={ws.rule}>
  <div className="dv-import-ws-head">
   <b>{ws.name}</b>
   {rule&&<span className="dv-import-rule" data-new={madeNew(ws.rule)||undefined}>{ws.rule}</span>}
   {folded&&<span className="dv-import-fold">{ws.threads.length} {ws.threads.length===1?'thread':'threads'}{open?t(', {open} open',{open}):''}</span>}
   <span className="dv-import-ct">{shown} {shown===1?'thread':'threads'}</span>
  </div>
  {ws.threads.slice(0,shown).map(thread=><div key={thread.id} className="dv-import-thread">
   <StatusGlyph state={thread.open?'idle':'done'}/>
   <span className="dv-import-tn">{thread.name}{thread.toolRun&&<small className="dv-import-tool-tag">{t('tool run')}</small>}</span>
   <span className="dv-import-tw">{threadWhen(thread.last_active)}</span>
  </div>)}
 </div>;
}
