import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import {activityBasename} from './DervoActivity';
import {providerAuthor} from './providerLabel';
import {REVIEW_OPEN,type CodingTask,type TaskToken} from './CodingTasks';
import {nativeStep} from './nativeSteps';
import {formatList,t} from '@/i18n';

/** The steps of a run as Dervo recorded them, for the Progress panel (TaskGuide.tsx). Every row is a recorded tool
 * call, the review phase, or the report that closed the run. Nothing is predicted. A tool call that returned an error
 * is a done step with a caption, because the agent carried on from it; how the run ended (stopped, failed, not
 * confirmed) is the panel's status line, said once, never a red row here. */
export type FlowState='live'|'done'|'problem'|'attention'|'cancelled'|'unknown';
export type FlowRow={id:string;state:FlowState;text:string;files:string[];meta?:string;startedAt?:number;endedAt?:number;timed?:boolean};

const record=(value:unknown):Record<string,unknown>=>typeof value==='object'&&value!==null?value as Record<string,unknown>:{};
const time=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)&&value>0?value:undefined;
const LIVE=new Set(['executing','running','queued','delivering']);
const CHECK_NAMES:Record<string,string>={'python-unittest':'Python','node-test':'Node','typescript-check':'TypeScript','rust-check':'Rust','rust-test':'Rust'};
/** Present and past tense per recorded tool. Tools outside this table are never shown, so arbitrary tool data can't reach the panel. */
const VERBS:Record<string,[string,string]>={
 dervo_read_file:[t('Reading'),t('Read')],dervo_write_file:[t('Editing'),t('Edited')],dervo_edit_file:[t('Editing'),t('Edited')],dervo_create_file:[t('Creating'),t('Created')],
 dervo_list_files:[t('Listing files'),t('Listed files')],dervo_search_files:[t('Searching project files'),t('Searched project files')],
 dervo_request_help:[t('Asking a helper'),t('Asked a helper')],dervo_suggest_lesson:[t('Suggesting a lesson'),t('Suggested a lesson')],
};
const FILE_TOOLS=new Set(['dervo_read_file','dervo_write_file','dervo_edit_file','dervo_create_file']);

/** A duration as a caption: "3 s", "2 min", "1 h 20 min". */
export function duration(seconds:number){
 const whole=Math.max(0,Math.round(seconds));
 if(whole<60)return `${whole} s`;
 const minutes=Math.floor(whole/60);
 if(minutes<60)return t('{minutes} min',{minutes});
 const hours=Math.floor(minutes/60),rest=minutes%60;
 return rest?t('{hours} h {rest} min',{hours,rest}):`${hours} h`;
}
const join=(...parts:(string|undefined)[])=>parts.filter(Boolean).join(' · ')||undefined;

export function tokenRow(task:CodingTask,token:TaskToken,index:number,runId:string,observed:boolean):FlowRow|undefined{
 const native=nativeStep(token);
 const raw=record(token),startedAt=time(raw.started_at),endedAt=time(raw.ended_at);
 const took=startedAt&&endedAt&&endedAt-startedAt>=1?duration(endedAt-startedAt):undefined;
 if(native){
  const id=`${runId}:${index}`,subject=native.subject?(native.file?activityBasename(native.subject)??native.subject:native.subject):undefined;
  const files=subject?[subject]:[];
  if(token.state==='running')return observed?{id,state:'live',text:native.present,files,startedAt,timed:token.tool==='native:Bash'}:{id,state:'unknown',text:native.present,files,meta:t('Last saved step')};
  if(token.state==='cancelled')return {id,state:'cancelled',text:native.present,files,meta:t('Didn’t finish')};
  if(token.state!=='delivered')return undefined;
  const command=token.tool==='native:Bash';
  return {id,state:'done',text:native.past,files,meta:token.success===false?t('Returned an error'):command?took:undefined,startedAt,endedAt,timed:command};
 }
 const check=token.tool==='dervo_run_tests';
 const verbs=VERBS[token.tool];
 if(!verbs&&!check)return undefined;
 const name=CHECK_NAMES[task.check_profile??'']??'configured';
 const label=check?(name==='configured'?t('Configured check'):t('{name} check',{name})):'';
 const file=FILE_TOOLS.has(token.tool)?activityBasename(token.path):undefined;
 const folder=!FILE_TOOLS.has(token.tool)&&!check?activityBasename(token.path):undefined;
 const present=check?(name==='configured'?t('Running the configured check'):t('Running the {check}',{check:label})):folder?t('{verb} in',{verb:verbs[0]}):verbs[0];
 const past=check?label:folder?t('{verb} in',{verb:verbs[1]}):verbs[1];
 const files=file?[file]:folder?[folder]:[];
 const id=`${runId}:${index}`;
 if(token.state==='uncertain')return {id,state:'attention',text:present,files,meta:t('Not confirmed')};
 if(token.state==='cancelled')return {id,state:'cancelled',text:present,files,meta:t('Cancelled')};
 if(LIVE.has(token.state)){
  if(!observed)return {id,state:'unknown',text:present,files,meta:t('Last saved step')};
  if(token.state==='queued')return {id,state:'live',text:check?(name==='configured'?t('Waiting to run the configured check'):t('Waiting to run the {check}',{check:label})):t('Waiting to start: {step}',{step:present.charAt(0).toLowerCase()+present.slice(1)}),files,startedAt,timed:check};
  return {id,state:'live',text:present,files,startedAt,timed:check};
 }
 if(token.state!=='delivered')return undefined;
 // A command that returned an error is an ordinary step; a check that didn't finish isn't a pass, so it gets no tick (RC review, 9 Oct).
 if(token.success===false)return {id,state:check?'attention':'done',text:check?t('{label} didn’t finish',{label}):past,files,meta:t('Returned an error'),startedAt,endedAt,timed:check};
 const completion=task.completion;
 const exit=check&&completion?.run_id===runId&&typeof completion.test_exit_status==='number'?t('exit {test_exit_status}',{test_exit_status:completion.test_exit_status}):undefined;
 return {id,state:'done',text:check?t('{label} passed',{label}):past,files,meta:check?join(exit,took):undefined,startedAt,endedAt,timed:check};
}

/** Claude Code's sub-agents that the run itself called (16.2, ``run.subagents``), not ones a sub-agent called or ones
 * refused for asking for a worktree. A background one that has returned has only been launched, so it's still working.
 * Shared by Progress and the reply's trail (replyTrail.ts), so both say the same thing. */
export function runHelpers(run:Record<string,unknown>):{finished:boolean;at:number}[]{
 const calls=Array.isArray(run.subagents)?run.subagents.map(record):[];
 return calls.filter(call=>!call.parent&&call.state!=='refused').map(call=>({
  finished:!(call.state==='called'||call.state==='running'||(call.state==='returned'&&call.background===true)),
  at:time(call.called_at)??0,
 }));
}
export const helpersWaiting=(count:number)=>t('Waiting on {count} helpers',{count});

const REVIEW_ROWS:Record<string,[FlowState,string]>={
 pending:['live',t('Waiting for independent review')],initial:['live',t('Independent review in progress')],final:['live',t('Independent review in progress')],
 correction:['live',t('Correcting review findings')],passed:['done',t('Independent review passed')],failed:['problem',t('Independent review failed')],
 unresolved:['problem',t('Review found unresolved issues')],stale:['problem',t('Review is out of date')],interrupted:['attention',t('Independent review was interrupted')],
};

/** Recorded steps for the current run only: tool tokens, the review phase and how the run ended. Nothing is predicted. */
export function taskFlow(task:CodingTask,unavailable=false):FlowRow[]{
 const run=record(task.runs.at(-1)),runId=typeof run.id==='string'?run.id:undefined;
 if(!runId)return [];
 const uncertain=task.state==='disconnected'||task.runs.some(r=>record(r).state==='uncertain')||task.tokens.some(t=>t.state==='uncertain');
 const observed=!unavailable&&!uncertain;
 const rows:FlowRow[]=[];
 // Your MCP servers that Claude Code couldn't start (failed or waiting for sign-in) when the first message went. The run
 // goes on without them, so the thread says which, rather than the agent quietly not having their tools (9 Oct, parity).
 const failed=Array.isArray(record(run.connectors).failed)?(record(run.connectors).failed as unknown[]).filter((name):name is string=>typeof name==='string'&&!!name).map(name=>name.replace(/^claude\.ai\s+/,'')):[];
 if(failed.length)rows.push({id:`${runId}:connectors`,state:'problem',text:t('Couldn’t start {servers}',{servers:failed.length>3?t('{items} and {count} more',{items:failed.slice(0,3).join(', '),count:failed.length-3}):formatList(failed)}),files:[],
  meta:failed.length>1?t('Not connected or need sign-in in Claude Code. This run went on without them.'):t('Not connected or needs sign-in in Claude Code. This run went on without it.')});
 // A step its record left open can't be live once its run has ended: it shows as the last saved step, not with a
 // spinner beside a finished run (design review S2, 25 Sep).
 const ended=['completed','failed','interrupted','cancelled'].includes(String(run.state))||typeof run.ended_at==='number';
 task.tokens.forEach((token,index)=>{if(token.run_id!==runId)return;const row=tokenRow(task,token,index,runId,!unavailable&&!ended);if(row)rows.push(row);});
 // Consecutive plain reads fold into one row. Anything with a problem, time or note stays on its own.
 const folded=rows.reduce<FlowRow[]>((out,row)=>{
  const last=out.at(-1);
  if(last&&last.state==='done'&&row.state==='done'&&last.text==='Read'&&row.text==='Read'&&!last.meta&&!row.meta)last.files=[...last.files,...row.files];
  // The same finished step twice in a row (searching one folder again) is one row.
  else if(last&&last.state==='done'&&row.state==='done'&&last.text===row.text&&last.files.join()===row.files.join()&&!last.meta&&!row.meta){}
  else out.push({...row,files:[...row.files]});
  return out;
 },[]);
 const phase=task.collaboration?.review?.phase??'';
 const reviewRow=REVIEW_ROWS[phase];
 const ownerWorking=task.state==='working'&&!(REVIEW_OPEN.has(phase)&&phase!=='pending');
 if(observed&&ownerWorking&&!folded.some(row=>row.state==='live')){
  // While its sub-agents work the run itself is waiting on them, not on the provider (16.2).
  const helpers=runHelpers(run);
  folded.push({id:`${runId}:waiting`,state:'live',text:helpers.some(helper=>!helper.finished)?helpersWaiting(helpers.length):t('Waiting for {who}',{who:providerAuthor(task.binding.provider)}),files:[]});
 }
 if(reviewRow&&!(phase==='pending'&&task.state==='working')){
  const [state,text]=reviewRow;
  folded.push({id:`${runId}:review`,state:state==='live'&&!observed?'unknown':state,text,files:[],meta:state==='live'&&!observed?t('Last saved step'):undefined,startedAt:time(record(task.collaboration?.review).started_at),timed:state==='live'});
 }
 const runState=run.state;
 const finalMessage=time([...task.messages].reverse().find(m=>m.role==='assistant'&&m.run_id===runId&&m.source!=='independent_review')?.created_at);
 // How the run ended is the panel's status line and the decision bar's sentence; only a finished report is a step.
 if(task.state==='answered'&&runState==='completed')folded.push({id:`${runId}:end`,state:'done',text:t('Answered'),files:[],endedAt:finalMessage});
 else if(task.state!=='working'&&runState==='completed'&&finalMessage)folded.push({id:`${runId}:end`,state:'done',text:t('Wrote the report'),files:[],endedAt:finalMessage});
 return folded;
}

export type FileDiff={path:string;added:number;removed:number;lines:{kind:'add'|'del';text:string}[];deleted?:boolean};
const LINE_LIMIT=6;
/** Only added and removed lines, capped. The full diff stays in Inspect task. */
export function summariseDiff(path:string,diff:string):FileDiff{
 const lines:FileDiff['lines']=[];let added=0,removed=0;
 for(const line of diff.split('\n')){
  if(line.startsWith('+++')||line.startsWith('---'))continue;
  if(line.startsWith('+')){added++;if(lines.length<LINE_LIMIT)lines.push({kind:'add',text:line.slice(1)});}
  else if(line.startsWith('-')){removed++;if(lines.length<LINE_LIMIT)lines.push({kind:'del',text:line.slice(1)});}
 }
 return {path,added,removed,lines};
}

type AttemptDetail={task:CodingTask;evidence:{attempts:{number:number;diff:Record<string,string>;deleted?:Record<string,unknown>}[]}};
/** Loads the checked attempt's diffs for a completed guide task. Same read as Inspect task, only once the result is settled. */
export function useCompletionDiffs(projectId:string,channelId:string,task?:CodingTask):FileDiff[]|undefined{
 const completion=task?.state==='completed'&&!task.completion?.stale?task.completion:undefined;
 const run=record(task?.runs.at(-1));
 const enabled=!!task&&!!completion&&completion.run_id===run.id&&completion.changed_files.length>0;
 const detail=useQuery({queryKey:['task-guide-diff',projectId,channelId,task?.id,completion?.run_id,completion?.attempt],enabled,retry:false,staleTime:Infinity,
  queryFn:async()=>{
   const result=await local<AttemptDetail>('task_detail',{projectId,channelId,taskId:task!.id});
   const attempt=result.evidence.attempts.find(item=>item.number===completion!.attempt);
   if(result.task.id!==task!.id||!attempt)return [];
   return Object.entries(attempt.diff).filter(([,diff])=>!!diff&&!diff.startsWith('Binary diff')).map(([path,diff])=>({...summariseDiff(path,diff),deleted:!!attempt.deleted&&Object.hasOwn(attempt.deleted,path)}));
  }});
 return enabled?detail.data:undefined;
}
