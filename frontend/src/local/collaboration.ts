import {mentionsIn,type CollabPacket,type CollabPolicy} from './api';
import {formatDate,t} from '@/i18n';

/** Where one composer message goes. Exactly one path, decided before anything is sent. */
export type SubmitRoute={path:'coding'|'coordination'|'chat';agents:string[]};
const COLLABORATING=new Set(['mentions','automatic']);
/**
 * Coding permission granted: the message is a coding request. When the project collaboration policy is on for this
 * conversation, an @name must be one of the agents the policy makes available here; the coding runtime then decides the
 * handoff (mentions mode: only the named agents; automatic: any allowed agent). An unknown @name is refused before
 * anything is sent, so the draft stays. When collaboration is off here, an @handle keeps the older text-only
 * coordination path exactly as before. Fresh projects use scoped reading for ordinary questions. Explicit mentions retain coordination.
 */
export function routeSubmit(text:string,coding?:{state:string;reading?:{state:string}}|null,collaboration?:Pick<CollabPolicy,'state'|'available'>|null,coordination?:{state:string;helper:string|null}|null):SubmitRoute {
  const mentions=mentionsIn(text).map(m=>m.slice(1));
  const coordinating=coordination?.state==='available'&&!!coordination.helper;
  if(coding?.state==='granted'){
    if(collaboration&&COLLABORATING.has(collaboration.state)){
      const known=(collaboration.available??[]).map(a=>a.name);
      const unknown=mentions.filter(m=>!known.includes(m));
      if(unknown.length)throw new Error(t('@{name} is not a named agent this conversation may ask. Named agents: {known}. Nothing was sent.',{name:unknown[0],known:known.map(n=>'@'+n).join(', ')||t('none')}));
      return {path:'coding',agents:mentions.filter(m=>known.includes(m))};
    }
    if(mentions.length&&coordinating)return {path:'coordination',agents:[]};
    return {path:'coding',agents:[]};
  }
  if(coding?.state==='missing'&&coding.reading?.state==='available'&&!mentions.length)return {path:'coding',agents:[]};
  if(coordinating)return {path:'coordination',agents:[]};
  return {path:'chat',agents:[]};
}

/** Status words for a handoff row. Never "executed", never "approved": a returned result is the helper's claim plus host evidence. */
export const PACKET_LABELS:Record<string,string>={queued:t('Pending'),running:t('Running'),returned:t('Returned'),failed:t('Failed'),refused:t('Refused'),withdrawn:t('Withdrawn'),disconnected:t('Disconnected')};
export const PACKET_ACTIVE=new Set(['queued','running']);
/** Display only. Requests, saved records and permission checks keep the original agent name. */
export function agentLabel(name:string):string {return /^review-[a-f0-9]{24}$/.test(name)?t('Reviewer'):`@${name}`;}
export function roleLabel(role:string|null|undefined):string {return role==='reviewer'?'reviewer, read-only':role==='worker'?t('worker, may change files'):t('unknown role');}
export function packetLabel(packet:Pick<CollabPacket,'status'|'stale'|'changed_since'|'refusal'|'error'|'result'>):string {
  const base=PACKET_LABELS[packet.status]??packet.status;
  if(packet.status==='refused')return `${base}: ${packet.refusal??t('no reason recorded')}`;
  if(packet.status==='returned'){
    const verified=packet.result?.verified?t(', verified by host tests'):packet.result?.attempt?t(', not verified'):'';
    return packet.stale?`${base}${verified} · ${t('stale: {files} changed since',{files:packet.changed_since.join(', ')})}`:`${base}${verified}`;
  }
  return packet.error?`${base}: ${packet.error}`:base;
}
/** One line for the Independent review record of an owner task. Phases are the host's words; a passed review is never
 * called accepted or approved, because human acceptance is a separate step. */
const REVIEW_PHASES:Record<string,(r:{unresolved?:string|null;error?:string|null})=>string>={
  pending:()=>t('waiting for the request to finish'),initial:()=>t('initial review running'),correction:()=>t('owner correcting the findings'),final:()=>t('final check running'),
  passed:()=>t('passed, no open findings'),unresolved:r=>t('unresolved after one correction: {findings}',{findings:r.unresolved??''}),
  stale:r=>t('stale, the files changed while it ran; findings returned: {findings}',{findings:r.unresolved??''}),failed:r=>t('failed: {why}',{why:r.error??t('no reason recorded')}),
  interrupted:r=>`interrupted: ${r.error??t('no reason recorded')}`,skipped:r=>`skipped: ${r.error??t('no reason recorded')}`};
/** The per-request Independent review override of the composer. ``default`` sends no key (the project setting applies and the
 * request keeps its older shape); ``off`` sends the explicit off record; ``on`` sends the project's configured reviewer binding
 * exactly, and is an explicit error when no reviewer is configured. Nothing here invents a reviewer or an effort. */
export type ReviewOverrideValue='default'|'off'|'on';
type ReviewSetting={enabled:boolean;reviewer:string|null;effort:string|null};
export const NO_REVIEWER=t('No reviewer is configured for Independent review. Turn it on with a reviewer in Collaboration settings first.');
export function reviewOverridePayload(value:ReviewOverrideValue,review:ReviewSetting|null|undefined):ReviewSetting|undefined {
  if(value==='default')return undefined;
  if(value==='off')return {enabled:false,reviewer:null,effort:null};
  if(!review?.enabled||!review.reviewer||!review.effort)throw new Error(NO_REVIEWER);
  return {enabled:true,reviewer:review.reviewer,effort:review.effort};
}
/** ``taskError``: records saved before 16 September 2026 embedded the owner task's error in the review error. The task's
 * error has one home on screen, so that fragment is dropped here; a record without it is returned as written. */
/** The first finding only, cut at a sentence length, with how many more there are. The full text stays in the task record. */
export function shortFindings(text:string|null|undefined):string {
  const items=(text??'').split(/\n+/).map(line=>line.trim()).filter(Boolean);
  if(!items.length)return '';
  const first=items[0].length>160?`${items[0].slice(0,157).trimEnd()}…`:items[0];
  return items.length>1?t('{first} ({count} more in the task record)',{first,count:items.length-1}):first;
}
export function reviewLabel(review:{phase:string;reviewer:string;effort:string;unresolved?:string|null;error?:string|null}|null|undefined,taskError?:string|null,{short=false}:{short?:boolean}={}):string {
  // ``taskError`` is kept for callers; the legacy shape is recognised on its own so a repaired task error still cleans the line.
  if(!review)return '';
  // Legacy shape: "The owner ended <state>: <task error>. No review was sent." The middle part is the task's, not the review's.
  const legacy=review.error?.match(/^(The owner(?:'s correction)? ended [a-z_]+): .*\. (No review was sent\.)$/s);
  const cleaned=legacy?{...review,error:`${legacy[1]}. ${legacy[2]}`}:taskError&&review.error?.includes(`: ${taskError}`)?{...review,error:review.error.replace(`: ${taskError}`,'')}:review;
  const state=(REVIEW_PHASES[cleaned.phase]??(()=>cleaned.phase))(short?{...cleaned,unresolved:shortFindings(cleaned.unresolved),error:cleaned.error&&shortFindings(cleaned.error)}:cleaned);
  const who=agentLabel(review.reviewer);
  return who==='Reviewer'?t('Independent review (effort {effort}): {state}',{effort:review.effort,state}):t('Independent review by {who} (effort {effort}): {state}',{who,effort:review.effort,state});
}
/** Snapshot bytes as text when they are UTF-8; otherwise null so the inspector says so instead of guessing. */
export function decodeSnapshot(bytesB64:string):string|null {
  try{
    const bin=atob(bytesB64);const bytes=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
    return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  }catch{return null;}
}
export function when(seconds:number|null|undefined):string {return seconds?formatDate(new Date(seconds*1000),{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'';}
