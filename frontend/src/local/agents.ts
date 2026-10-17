import {local,type LocalEvent} from './api';
import {t} from '@/i18n';

/** Agents you define and @mention, and Home's routines and heartbeat (backend/dervo_agents.py, dervo_assistant.py).
 * An agent works in its own thread, so every answer has a run record there; the asking thread shows your ask and its
 * answer as rows. Nothing here decides access: the host does, and a read-only agent stays read-only. */

export type AgentScope='read'|'change';
export type DervoAgent={id:string;project_id:string;handle:string;name:string;brief:string;skills:string[];provider:'claude'|'codex';profile_id:string;
  model:string;scope:AgentScope;channel_id:string;created_at:number;updated_at:number;state:'ready'|'unavailable';detail:string|null};
export type AgentTemplate={handle:string;name:string;scope:AgentScope;brief:string};
export type AgentMention={id:string;from_thread:string|null;handle:string;agent_name:string;channel_id:string;client_id:string;asked:string;
  state:'saved'|'queued'|'working'|'answered'|'stopped'|'failed'|'unknown';task_id:string|null;task_state?:string|null;detail:string|null;answer:string|null;
  created_at:number;answered_at:number|null};
export type Schedule={kind:'once';at:number}|{kind:'every';minutes:number}|{kind:'daily';time:string;days:number[]};
export type Gate='always'|'changed'|'needs_you';
export type RoutineRun={id:string;state:'due'|'queued'|'working'|'done'|'skipped'|'failed'|'stopped'|'unknown';detail:string|null;answer:string|null;
  created_at:number;ended_at:number|null;channel_id:string|null;task_id:string|null;trigger:'schedule'|'now'};
export type Routine={id:string;name:string;prompt:string;agent_id:string;schedule:Schedule;gate:Gate;state:'active'|'paused'|'done';next_due:number|null;
  when:string;agent:{handle:string;name:string}|null;detail:string|null;last_run:RoutineRun|null};
export type RoutineProposal={schedule:Schedule|null;when:string|null;agentId:string|null;handle:string|null;prompt:string|null;name:string|null;gate:Gate;
  missing:('when'|'agent'|'prompt')[]};
export type Heartbeat={enabled:boolean;minutes:number;last_at:number|null;said_at:number|null};

export const agentsList=(projectId:string)=>local<DervoAgent[]>('agents_list',{projectId});
export const agentTemplates=()=>local<AgentTemplate[]>('agent_templates',{});
export type AgentDraft={handle:string;name:string;brief:string;skills:string[];profileId:string;model:string;scope:AgentScope};
export const agentSave=(projectId:string,draft:AgentDraft,agentId?:string)=>local<DervoAgent>('agent_save',{projectId,...draft,...(agentId?{agentId}:{})});
export const agentDelete=(projectId:string,agentId:string)=>local<DervoAgent[]>('agent_delete',{projectId,agentId});
export const agentMentions=(projectId:string,channelId:string|null)=>local<AgentMention[]>('agent_mentions',{projectId,...(channelId?{channelId}:{})});
export const routines=(projectId:string)=>local<Routine[]>('assistant_jobs',{projectId});
export const routineSave=(projectId:string,routine:{name:string;prompt:string;agentId:string;schedule:Schedule;gate:Gate},jobId?:string)=>
  local<Routine>('assistant_job_save',{projectId,...routine,...(jobId?{jobId}:{})});
export const routinePause=(projectId:string,jobId:string,paused:boolean)=>local<Routine>('assistant_job_pause',{projectId,jobId,paused});
export const routineDelete=(projectId:string,jobId:string)=>local<Routine[]>('assistant_job_delete',{projectId,jobId});
export const routineRunNow=(projectId:string,jobId:string)=>local<RoutineRun>('assistant_job_run_now',{projectId,jobId});
export const routineParse=(projectId:string,text:string)=>local<RoutineProposal>('assistant_parse',{projectId,text});
export const heartbeat=(projectId:string)=>local<Heartbeat>('assistant_heartbeat',{projectId});
export const heartbeatSet=(projectId:string,enabled:boolean,minutes:number)=>local<Heartbeat>('assistant_heartbeat_set',{projectId,enabled,minutes});

/** The same reading of "@handle" the host uses (dervo_agents.MENTION): not an email, not inside `code`. */
const MENTION=/(?<![\w@.])@([A-Za-z][A-Za-z0-9-]{0,31})(?![\w@-])/g;
const CODE=/```[\s\S]*?```|`[^`\n]*`/g;
export function agentsNamed(text:string,agents:Pick<DervoAgent,'handle'>[]):string[]{
  const known=new Set(agents.map(agent=>agent.handle));
  const found:string[]=[];
  for(const match of text.replace(CODE,' ').matchAll(MENTION)){const handle=match[1].toLowerCase();if(known.has(handle)&&!found.includes(handle))found.push(handle);}
  return found;
}

/** Send a message that names agents. One request id per draft, kept until the host answers, so a lost answer is
 * retried under the same id and never sends twice (the host keys each mention by it). When the call fails, the host's
 * own record decides: no row under that id means nothing was saved (a refusal, or the call never arrived), so the id is
 * dropped and the draft can be edited and sent again; a row means the host has it and its tick finishes the send. Only
 * when that check fails too is the outcome unknown, and the id stays. */
const readPending=(key:string)=>{try{const saved=localStorage.getItem(key);return saved?JSON.parse(saved):null;}catch{return null;}};
const writePending=(key:string,value:unknown)=>{try{if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,JSON.stringify(value));}catch{/* storage off: no retry memory */}};
export async function agentMention(projectId:string,channelId:string|null,text:string):Promise<AgentMention[]>{
  const key=`dervo-agent-pending:${projectId}:${channelId??'home'}`;
  const old=readPending(key);
  if(old&&old.text!==text)throw new Error(t('An earlier message to an agent has an unknown outcome. Send that one again first.'));
  const request=old??{projectId,clientId:crypto.randomUUID().replace(/-/g,''),text,...(channelId?{channelId}:{})};
  writePending(key,request);
  try{
    const result=await local<AgentMention[]>('agent_mention',request);
    writePending(key,null);
    return result;
  }catch(failure){
    const known=await agentMentions(projectId,channelId).catch(()=>null);
    if(known)writePending(key,null);
    if(known?.some(mention=>mention.client_id===request.clientId))return known.filter(mention=>mention.client_id===request.clientId);
    throw failure;
  }
}

const STATE_LINES:Record<AgentMention['state'],string>={saved:t('Sending'),queued:t('Waiting for its thread to finish a run'),working:t('Working'),
  answered:t('Answered'),stopped:t('Stopped'),failed:t('Didn’t start'),unknown:t('Outcome unknown')};
export const SETTLED_MENTION:AgentMention['state'][]=['answered','stopped','failed','unknown'];
export const mentionStateLine=(mention:AgentMention)=>mention.detail?`${STATE_LINES[mention.state]}: ${mention.detail}`:STATE_LINES[mention.state];

type Keyed=LocalEvent&{sortKey:number;sortOrder:number};
/** Your ask and the agent's answer as timeline rows of the asking thread. One ask naming two agents is one row of yours
 * (rows share the client id) and one answer row per agent. The answer row's pubkey says which agent, so its name shows. */
export function agentMentionRows(mentions:AgentMention[],channelId:string):Keyed[]{
  const rows:Keyed[]=[],asked=new Set<string>();
  for(const mention of mentions){
    if(!asked.has(mention.client_id)){
      asked.add(mention.client_id);
      rows.push({id:`agent-ask:${mention.client_id}`,content:mention.asked,pubkey:'local-user',kind:9,sig:'',created_at:mention.created_at,tags:[['h',channelId]],state:'agent',sortKey:mention.created_at*1000,sortOrder:0});
    }
    const at=mention.answered_at??mention.created_at;
    const body=mention.state==='answered'&&mention.answer?(mention.detail?`${mention.answer}\n\n${mention.detail}`:mention.answer):mentionStateLine(mention);
    rows.push({id:`agent-answer:${mention.id}`,content:body,pubkey:`agent:${mention.handle}`,kind:9,sig:'',created_at:at,tags:[['h',channelId]],state:'agent',sortKey:at*1000+1,sortOrder:1});
  }
  return rows;
}
export const isAgentPubkey=(pubkey:string)=>pubkey.startsWith('agent:');
export const agentAuthor=(pubkey:string)=>`@${pubkey.slice('agent:'.length)}`;
