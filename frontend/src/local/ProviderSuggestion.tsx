import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {local,setRoutePolicy,waitForReset,type RoutePolicy} from './api';
import {HandoffConfirm,RestoreSnapshot,type HandoffSnapshot} from './HandoffConfirm';
import type {CodingTask,TaskSuggest} from './CodingTasks';
import {StatusGlyph} from './StatusGlyph';
import {activityBasename} from './DervoActivity';
import {taskFlow} from './TaskFlow';
import {stoppedTask} from './CodingTasks';
import {clip} from './progressBrief';
import {openConnectedAccounts} from './openAccounts';
import {formatDate,t} from '@/i18n';

// i18n-ignore: sent to the agent as your message, and Dervo's words to agents stay English (GuideActions sends the same)
const RETRY_TEXT='Please try that again.';
const clock=(seconds:number)=>formatDate(seconds*1000,{hour:'numeric',minute:'2-digit'});
/** Up to `shown` names, then "and 2 more". */
const some=(items:string[],shown:number)=>items.length>shown?t('{items} and {count} more',{items:items.slice(0,shown).join(', '),count:items.length-shown}):items.join(', ');
const record=(value:unknown):Record<string,unknown>=>typeof value==='object'&&value!==null?value as Record<string,unknown>:{};
const WRITES=new Set(['dervo_write_file','dervo_edit_file','dervo_create_file','native:Edit','native:Write']);
/** The short provider names the handoff row uses: the byline already said "Claude Code". */
export function providerShort(provider?:string|null):string{return provider==='claude'?'Claude':provider==='codex'?'Codex':provider||t('The provider');}
/** What stopped the run, as the row's name. The kind is the host's; the words are Dervo's. */
export function failureName(who:string,suggest:Pick<TaskSuggest,'kind'|'reason'>):string{
 if(suggest.kind==='usage_limit')return t('{who} hit its usage limit',{who});
 if(suggest.kind==='rate_limit')return t('{who} hit its rate limit',{who});
 if(suggest.kind==='overloaded')return t('{who} was overloaded',{who});
 if(suggest.kind==='api_error')return t('{who} returned an error',{who});
 // After your own Stop (behaviour review N1): read after "after", as in "Codex took over, after you stopped Claude".
 if(suggest.kind==='stopped')return t('you stopped {who}',{who});
 return suggest.reason.replace(/[.]$/,'');
}
const lowerFirst=(text:string)=>text.charAt(0).toLowerCase()+text.slice(1);

/** Where the work stands when a provider stops, from the record: what you asked, which files changed, where it stopped
 * and what the agent said comes next. Nothing here is a claim about what the other provider is told. */
export function handoffFacts(record_:CodingTask):{label:string;text:string}[]{
 const task={...record_,tokens:record_.tokens??[],runs:record_.runs??[],messages:record_.messages??[]};
 const run=record(task.runs.at(-1)),runId=typeof run.id==='string'?run.id:undefined;
 const changed=new Set(task.tokens.filter(t=>t.run_id===runId&&WRITES.has(t.tool)&&t.state==='delivered'&&t.success!==false).map(t=>activityBasename(t.path)).filter(Boolean));
 const last=[...taskFlow(task)].reverse().find(row=>row.state==='done'&&!row.id.endsWith(':end'));
 const guide=record(run.guide);
 const next=guide.run_id===runId&&typeof guide.next==='string'?clip(guide.next,160):undefined;
 const facts=[{label:t('Asked'),text:t('Your request, as you wrote it.')}];
 if(changed.size)facts.push({label:t('Files'),text:t('The {count} changed files, uncommitted.',{count:changed.size})});
 if(last)facts.push({label:t('Stopped'),text:t('After it {step}.',{step:`${lowerFirst(last.text)}${last.files.length?` ${some(last.files,2)}`:''}`})});
 if(next)facts.push({label:t('Next'),text:next});
 return facts;
}

/** What the host records on the stopped task when the next agent picked its changed files up (retry_on). */
export type PickedUp={line:string;files:string[]};
/** The line under a stopped task whose files another agent carried on: the host's words, then who and which files. */
export function pickedUpLine(picked:PickedUp,to:string,why:string):string{
 const carried=picked.files.length?t('{who} carried on with {files}, after {why}.',{who:to,files:some(picked.files,3),why}):t('{who} carried on, after {why}.',{who:to,why});
 return `${picked.line.replace(/[.]$/,'')}. ${carried}`;
}

/** Why this request ran where it did. The handover itself is one line under the stopped task (below), so nothing is
 * said again under the task that took over. */
export function RouteWhy(_props:{task:Pick<CodingTask,'route'>}){return null;}

/** What to do after a failure on the provider's side, or when Auto's account is low: carry on with your account on
 * the other provider, wait for the reset, try again here, or let this project switch by itself. Every action goes
 * through the host; nothing is decided here. Presentation follows the design system's handoff row (thread-handoff.html).
 * ``resetsAt``: when the task's account says its usage window resets, from the usage summary, if it said.
 * ``onAccounts`` opens Connected accounts for "Add an account"; without it the click goes to the sidebar (openAccounts.ts). */
export function ProviderSuggestion({task,projectId,channelId,policy,latest,conversationProfileId,resetsAt,refresh,onAccounts}:{task:CodingTask;projectId:string;channelId:string;policy?:RoutePolicy;latest:boolean;conversationProfileId?:string;resetsAt?:number|null;refresh:()=>Promise<unknown>;onAccounts?:(trigger:HTMLElement)=>void}){
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[confirming,setConfirming]=useState(false);
  const suggest=task.suggest;
  const low=latest&&!suggest?task.route?.suggest:null;
  const failure=!!suggest;
  const detail=useQuery({queryKey:['task-detail',projectId,channelId,task.id],enabled:failure&&!suggest?.taken&&!suggest?.automatic,
    queryFn:()=>local<{retry?:{available:boolean};rebound?:boolean}>('task_detail',{projectId,channelId,taskId:task.id})});
  if(!failure&&!low)return null;
  if(low&&conversationProfileId!==task.route?.profile_id)return null;
  async function run(name:string,action:()=>Promise<unknown>){
    if(busy)return;
    setBusy(name);setError('');
    try{await action();await refresh();if(failure)await detail.refetch();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy('');}
  }
  const who=providerShort(task.binding?.provider??task.route?.provider);
  const other=suggest?.other??low;
  const otherName=other?providerShort(other.provider):'';
  const endedAt=(()=>{const run=record((task.runs??[]).at(-1));return typeof run.ended_at==='number'?run.ended_at:undefined;})();
  const limited=suggest?.kind==='usage_limit'||suggest?.kind==='rate_limit';
  const canWait=failure&&limited&&typeof resetsAt==='number'&&resetsAt*1000>Date.now()&&!task.waiting_for_reset;
  // Retrying on an account that ran out of usage runs straight into the same limit, whether or not the reset is known
  // (UX review, 23 Sep, M1). A rate limit passes sooner: there, Retry here stays unless waiting for the reset is on offer.
  const usageLimit=suggest?.kind==='usage_limit';
  const retryHere=failure&&!!detail.data?.retry?.available&&!detail.data?.rebound&&!usageLimit&&!(limited&&canWait);
  // Said when the provider gave no reset time, so the missing Wait button isn't a mystery.
  const resetUnknown=failure&&usageLimit&&!canWait&&!task.waiting_for_reset;
  // A handoff that carried changed files on left a snapshot here: Restore puts the folder back as it was (HandoffConfirm).
  const snapshot=(suggest as (TaskSuggest&{snapshot?:HandoffSnapshot|null})|undefined)?.snapshot;
  const pickedUp=(suggest as (TaskSuggest&{picked_up?:PickedUp|null})|undefined)?.picked_up;
  if(suggest?.taken)return <>
    <p className="dv-handover" data-testid="provider-suggestion" data-state="taken">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 5.5h9.5M10 3l2.5 2.5L10 8M13 10.5H3.5M6 8l-2.5 2.5L6 13"/></svg>
      {pickedUp?pickedUpLine(pickedUp,otherName,failureName(who,suggest))
       :suggest.other?t('{who} took over, after {why}.',{who:otherName,why:failureName(who,suggest)}):t('Sent again as a new task, after {why}.',{why:failureName(who,suggest)})}
    </p>
    {snapshot&&!snapshot.removed_at&&<RestoreSnapshot projectId={projectId} channelId={channelId} taskId={task.id} snapshot={snapshot} refresh={refresh}/>}
    {snapshot?.removed_at&&<div className="dv-carry-restore"><span className="dv-caption">{t('Dervo removed the snapshot a week after the work was settled.')}</span></div>}
  </>;
  if(task.waiting_for_reset)return <div className="dv-sheet dv-handoff" data-testid="provider-suggestion" data-state="waiting"><div className="dv-row dv-open">
    <StatusGlyph state="queued"/>
    <div><div className="dv-row-name">{suggest?failureName(who,suggest):t('{who} ran out',{who})}</div><div className="dv-row-line">{t('Waiting for the reset at {time}. Then the same request starts again. Cancel it from the queued message.',{time:clock(task.waiting_for_reset.until)})}</div></div>
    {endedAt&&<div className="dv-row-end"><span className="dv-when">{clock(endedAt)}</span></div>}
  </div></div>;
  if(suggest?.automatic)return <div className="dv-sheet dv-handoff" data-testid="provider-suggestion" data-state="automatic"><div className="dv-row dv-open">
    <StatusGlyph state="running"/>
    <div><div className="dv-row-name">{failureName(who,suggest)}</div><div className="dv-row-line">{t('Trying {other} by itself, because this project switches automatically.',{other:otherName})}</div></div>
    {endedAt&&<div className="dv-row-end"><span className="dv-when">{clock(endedAt)}</span></div>}
  </div></div>;
  if(low)return <div className="dv-sheet dv-handoff" data-testid="provider-suggestion" data-state="low"><div className="dv-row dv-open">
    <StatusGlyph state="needs"/>
    <div>
      <div className="dv-row-name">{t('{who} is running low',{who})}</div>
      <div className="dv-row-line">{low.reason} {t('Your next request here can use {other}.',{other:otherName})}</div>
      <div className="dv-row-actions">
        <button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void run('use',()=>local('conversation_profile_set',{projectId,channelId,expectedProfileId:task.route!.profile_id,profileId:low.profile_id,expectedModel:task.route!.model,model:low.model}))}>{busy==='use'?t('Switching…'):t('Use {otherName} next',{otherName})}</button>
      </div>
      {policy&&<label className="dv-suggest-switch dv-caption"><input type="checkbox" checked={policy.auto_switch} disabled={!!busy} onChange={event=>void run('policy',()=>setRoutePolicy(projectId,channelId,event.target.checked))}/> {t('Always switch automatically in this project')}</label>}
      {error&&<p role="alert" className="dv-row-line dv-suggest-error">{error}</p>}
    </div>
  </div></div>;
  const facts=handoffFacts(task);
  // After your own Stop nothing failed: the row only offers the other provider, on the thread's newest request while its
  // stopped run is still the last one. Retry stays in the Progress panel, and nothing switches by itself (behaviour
  // review N1; Bryce, 26 Sep). The stopped card above it already says what was done, so the row skips handoff facts: the
  // confirm shows what the next agent gets.
  if(suggest?.kind==='stopped'){
   if(!other||!latest||!stoppedTask(task))return null;
   return <div className="dv-sheet dv-handoff" data-testid="provider-suggestion" data-state="stopped"><div className="dv-row dv-open">
    <StatusGlyph state="stopped"/>
    <div>
      <div className="dv-row-name">{t('You paused {who}',{who})}</div>
      <div className="dv-row-line">{t('{otherName} can carry on from here.',{otherName})}</div>
      {confirming&&<HandoffConfirm projectId={projectId} channelId={channelId} taskId={task.id} to={otherName}
        onCancel={()=>setConfirming(false)} onSent={async()=>{setConfirming(false);await refresh();await detail.refetch();}}/>}
      {!confirming&&<div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>setConfirming(true)}>{t('Continue in {otherName}',{otherName})}</button></div>}
      {error&&<p role="alert" className="dv-row-line dv-suggest-error">{error}</p>}
    </div>
    {endedAt&&<div className="dv-row-end"><span className="dv-when">{clock(endedAt)}</span></div>}
   </div></div>;
  }
  return <div className="dv-sheet dv-handoff" data-testid="provider-suggestion" data-state="failure"><div className="dv-row dv-open">
    <StatusGlyph state="failed" accent/>
    <div>
      <div className="dv-row-name">{failureName(who,suggest!)}</div>
      <div className="dv-row-line">{[resetUnknown?t('{who} hasn’t said when this resets.',{who}):'',other?t('{otherName} can carry on from here.',{otherName}):t('Add an account on the other provider to carry on there.')].filter(Boolean).join(' ')}</div>
      {/* While the confirm is open it holds the facts, so they're said once. */}
      {!confirming&&<dl className="dv-facts">{facts.map(fact=><div key={fact.label} className="dv-facts-row"><dt>{fact.label}</dt><dd>{fact.text}</dd></div>)}</dl>}
      {suggest?.error&&<p className="dv-row-line" role="note">{t('Dervo didn’t retry by itself: {error}',{error:suggest.error})}</p>}
      {/* Continue opens the confirm first: what the other provider gets, and the snapshot when there are files to save. */}
      {confirming&&other&&<HandoffConfirm projectId={projectId} channelId={channelId} taskId={task.id} to={otherName}
        onCancel={()=>setConfirming(false)} onSent={async()=>{setConfirming(false);await refresh();await detail.refetch();}}/>}
      {!confirming&&<div className="dv-row-actions">
        {other&&<button type="button" className="dv-btn dv-btn-primary" disabled={!!busy} onClick={()=>setConfirming(true)}>{t('Continue in {otherName}',{otherName})}</button>}
        {canWait&&<button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void run('wait',()=>waitForReset(projectId,channelId,task.id))}>{busy==='wait'?t('Saving…'):t('Wait for {who} · {time}',{who,time:clock(resetsAt!)})}</button>}
        {retryHere&&<button type="button" className={other?'dv-btn dv-btn-quiet':'dv-btn dv-btn-primary'} disabled={!!busy} onClick={()=>void run('here',()=>local('task_continue',{projectId,channelId,taskId:task.id,text:RETRY_TEXT}))}>{busy==='here'?t('Starting…'):t('Retry here')}</button>}
        {/* One account and nowhere to go: the way through is a second account, on Connected accounts (UX M1). */}
        {!other&&<button type="button" className={retryHere||canWait?'dv-btn dv-btn-ghost':'dv-btn dv-btn-primary'} disabled={!!busy}
          onClick={event=>{if(onAccounts)onAccounts(event.currentTarget);else openConnectedAccounts();}}>{t('Add an account')}</button>}
      </div>}
      {policy&&other&&<label className="dv-suggest-switch dv-caption"><input type="checkbox" checked={policy.auto_switch} disabled={!!busy} onChange={event=>void run('policy',()=>setRoutePolicy(projectId,channelId,event.target.checked))}/> {t('Always switch automatically in this project')}</label>}
      {error&&<p role="alert" className="dv-row-line dv-suggest-error">{error}</p>}
    </div>
    {endedAt&&<div className="dv-row-end"><span className="dv-when">{clock(endedAt)}</span></div>}
  </div></div>;
}
