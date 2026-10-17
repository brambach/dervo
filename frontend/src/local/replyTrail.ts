import type {CodingTask} from './CodingTasks';
import {modelName,providerAuthor} from './providerLabel';
import {helpersWaiting,runHelpers,tokenRow,type FlowRow} from './TaskFlow';
import {t} from '@/i18n';

/** The live reply's trail (16.2, mock F in evidence/design-tests/b162-thinking, picked 27 Sep): the model and a clock in
 * the header, the last two finished steps under the reply, ticked and fading, then the live step. Claude Code's
 * sub-agents are one crumb with a dot each. When the run ends the trail folds into one chip that opens Progress.
 * Every step is one Dervo recorded (the same rows as Progress, TaskFlow.tokenRow). Nothing is predicted. */
export type TrailStep={id:string;text:string;subject?:string};
export type ReplyRun={
 /** Changes whenever anything drawn changes, except the clock, which ticks on its own. */
 key:string;
 model?:string;startedAt?:number;endedAt?:number;
 live:boolean;
 /** Finished steps, oldest first, the last few only. */
 done:TrailStep[];
 /** The step running now; ``helpers`` has one entry per sub-agent, true once it's finished. */
 now?:TrailStep&{helpers?:boolean[]};
 steps:number;helpers:number;
 /** The task's latest run, whose reply carries the end chip. Progress only shows that run. */
 latest:boolean;
 /** Set on the run's last reply row only, which is where the chip goes (responsePresentation.ts). */
 tail?:boolean;
};

const record=(value:unknown):Record<string,unknown>=>typeof value==='object'&&value!==null?value as Record<string,unknown>:{};
const time=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)&&value>0?value:undefined;
/** Finished steps the trail keeps. fitTrail drops them oldest first when the line is short. */
export const TRAIL_KEPT=2;


/** When the run began: its own record, else the message you sent that started it. */
function runStart(task:CodingTask,run:Record<string,unknown>,runId:string):number|undefined{
 const own=time(run.started_at);
 if(own)return own;
 const first=task.messages.findIndex(message=>message.role==='assistant'&&message.run_id===runId);
 const before=(first<0?task.messages:task.messages.slice(0,first)).filter(message=>message.role==='user').at(-1);
 return time(before?.created_at);
}

export function replyRun(task:CodingTask,runId:string):ReplyRun|undefined{
 const run=record(task.runs.find(item=>record(item).id===runId));
 if(!run.id)return undefined;
 const latest=record(task.runs.at(-1)).id===runId;
 const live=latest&&task.state==='working'&&['checking','sending','running'].includes(String(run.state));
 const ended=!live&&['completed','failed','interrupted','cancelled','uncertain'].includes(String(run.state));
 // The run's own steps only: a sub-agent's steps sit under its Agent call and are counted in its crumb.
 const rows:(FlowRow&{at:number})[]=[];
 (task.tokens??[]).forEach((token,index)=>{
  if(token.run_id!==runId||(token as {parent?:string}).parent)return;
  const row=tokenRow(task,token,index,runId,live);
  if(row)rows.push({...row,at:time(record(token).started_at)??0});
 });
 const helpers=runHelpers(run),waiting=live&&helpers.some(helper=>!helper.finished);
 const finished:(TrailStep&{at:number})[]=[];
 for(const row of rows){
  if(row.state==='live'||row.state==='unknown')continue;
  const step={id:row.id,text:row.text,subject:row.files[0],at:row.at};
  const last=finished.at(-1);
  if(last&&last.text===step.text&&last.subject===step.subject)continue;  // the same step twice in a row is one
  finished.push(step);
 }
 if(helpers.length&&!waiting)finished.push({id:`${runId}:helpers`,text:t('{count} helpers finished',{count:helpers.length}),at:Math.min(...helpers.map(helper=>helper.at))});
 finished.sort((a,b)=>a.at-b.at);
 let now:ReplyRun['now'];
 if(live){
  const running=[...rows].reverse().find(row=>row.state==='live');
  if(waiting)now={id:`${runId}:helpers`,text:helpersWaiting(helpers.length),helpers:helpers.map(helper=>helper.finished)};
  else if(running)now={id:running.id,text:running.text,subject:running.files[0]};
  else now={id:`${runId}:idle`,text:run.state==='running'?t('Thinking'):t('Starting {who}',{who:providerAuthor(task.binding?.provider)})};
 }
 const finalMessage=time([...task.messages].reverse().find(message=>message.role==='assistant'&&message.run_id===runId&&message.source!=='independent_review')?.created_at);
 const view:Omit<ReplyRun,'key'>={
  model:modelName(task.binding?.model)||undefined,startedAt:runStart(task,run,runId),
  endedAt:ended?time(run.ended_at)??time(task.completion?.run_id===runId?task.completion?.at:undefined)??finalMessage:undefined,
  live,done:finished.slice(-TRAIL_KEPT).map(({at:_,...step})=>step),now,
  steps:finished.filter(step=>step.id!==`${runId}:helpers`).length,helpers:helpers.length,latest,
 };
 return {...view,key:JSON.stringify(view)};
}

/** The chip a finished run folds into: "6 steps · 2 helpers · 13s". */
export function runSummary(run:Pick<ReplyRun,'steps'|'helpers'|'startedAt'|'endedAt'>):string{
 return [run.steps?t('{count} steps',{count:run.steps}):'',run.helpers?t('{count} helpers',{count:run.helpers}):'',run.startedAt&&run.endedAt&&run.endedAt>=run.startedAt?elapsed(run.endedAt-run.startedAt):''].filter(Boolean).join(' · ');
}
/** The header's clock: "12s", "4:05", "1:02:03". */
export function elapsed(seconds:number):string{
 const whole=Math.max(0,Math.floor(seconds));
 if(whole<60)return `${whole}s`;
 const h=Math.floor(whole/3600),m=Math.floor(whole/60)%60,s=String(whole%60).padStart(2,'0');
 return h?`${h}:${String(m).padStart(2,'0')}:${s}`:`${m}:${s}`;
}
