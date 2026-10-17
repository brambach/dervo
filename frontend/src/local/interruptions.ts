import type {QueuedRequest} from './api';
import {providerAuthor} from './providerLabel';
import {t} from '@/i18n';

/** What the composer says about a message you sent while an agent was working (interruptions, #5 plus DC4, composer
 * side). The host already records which way it went: a message that joined the run sits in the task's messages with
 * ``steer: 'sent'`` (waiting for the agent's next step) or ``steer: 'read'`` (the agent has read it); one that couldn't
 * join waits in the conversation's queue. This reads those records only. The steering itself is the host's (#5, another
 * branch). Not recorded by the host today, so not said here: why a steer was refused and moved to the queue, and when a
 * steered message was read. */
export type SteerMessage={role:string;text:string;created_at:number;run_id?:string;steer?:string};
export type SteerTask={state:string;binding?:{provider?:string};runs:unknown[];messages:SteerMessage[]};
export type Interruption={kind:'steered'|'read'|'queued';text:string;messageIndex?:number};

/** The latest message you added to the running task, and where it stands. Null when nothing was added to the run now
 * working: a finished task, a steer from an earlier run, or an ordinary first request. */
export function steeredMessage(task:SteerTask|undefined):{index:number;message:SteerMessage}|null{
  if(!task||task.state!=='working')return null;
  const run=(task.runs.at(-1) as {id?:string}|undefined)?.id;
  for(let index=task.messages.length-1;index>=0;index--){
    const message=task.messages[index];
    if(message.role!=='user'||!message.steer)continue;
    if(run&&message.run_id&&message.run_id!==run)return null;
    return {index,message};
  }
  return null;
}
export function interruptionLabel(task:SteerTask|undefined,queue:Pick<QueuedRequest,'state'|'position'>[]):Interruption|null{
  const steered=steeredMessage(task);
  const who=providerAuthor(task?.binding?.provider);
  if(steered){
    if(steered.message.steer==='read')return {kind:'read',text:t('{who} read your message and is working with it',{who}),messageIndex:steered.index};
    return {kind:'steered',text:t('Added to the run · {who} reads it at its next step',{who}),messageIndex:steered.index};
  }
  const waiting=queue.filter(item=>item.state==='queued').length;
  if(waiting&&task?.state==='working')return {kind:'queued',text:waiting===1?t('Queued · runs when this task ends'):t('{waiting} queued · they run when this task ends',{waiting})};
  return null;
}
