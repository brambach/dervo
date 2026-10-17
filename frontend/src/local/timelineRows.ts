import type {LocalEvent} from './api';
import {agentLabel} from './collaboration';
import {isOverviewRequest,PROJECT_OVERVIEW_SUMMARY} from './projectOverview';
import {answeredMessage,QUESTION_TAG,threadQuestions} from './workerQuestionState';
import {noteRow,type NoteOrigin} from './threadNoteState';
import {t} from '@/i18n';

export type TaskLike={id:string;origin?:string;read_only?:boolean;packet_id?:string;agent?:string;role?:string;messages:{role:string;text:string;created_at:number;source?:string}[];questions?:readonly unknown[];note_origin?:NoteOrigin|null};
type Keyed=LocalEvent&{sortKey:number};
export function hasHelperRequest(task:TaskLike){return task.origin==='collaboration'&&!!task.packet_id&&task.messages[0]?.role==='user';}

// A job Home routed here (project_orchestrator.route_handoff) starts with Dervo's own brief to the agent. Only its
// "Your request:" part is yours, so only that shows as your message (chat_links.your_words, the same rule).
// i18n-ignore: the host's marker on a routed brief, matched, never shown
const ROUTED='Home routed this request to this chat.';
// i18n-ignore: the host's own labels in a routed brief, matched, never shown
const ROUTED_LABELS=['Focus for this chat','Your request','Previous request for context','Offer you accepted','Selected project items'];
const escaped=(text:string)=>text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const YOUR_REQUEST=new RegExp(`(?:^|\\n\\n)Your request:\\n([\\s\\S]*?)(?=\\n\\n(?:${ROUTED_LABELS.map(escaped).join('|')}):\\n|\\n\\nWhat you said on Home|$)`);
export function yourWords(text:string):string{
  if(!text.startsWith(ROUTED))return text;
  return YOUR_REQUEST.exec(text)?.[1]?.trim()||text;
}

/** Task rows keep their task's request and reply together, ordered by when the task was created. */
export function taskRows(tasks:TaskLike[],channelId:string,overview=false):Keyed[] {
  const questions=threadQuestions(tasks);
  return tasks.flatMap(task=>task.messages.map((m,index)=>{
    const request=index===0&&hasHelperRequest(task);
    const setup=index===0&&m.role==='user'&&task.origin==='conversation'&&task.read_only===true&&isOverviewRequest(m.text,overview);
    // A run a note or review started (beta.17): the sender's words quoted as theirs, and Dervo's one line. Never yours.
    const noted=index===0&&m.role==='user'&&!setup&&!request&&task.note_origin?noteRow(task.note_origin,m.text):null;
    if(noted)return {id:taskRowId(task,index),content:noted.content,pubkey:'dervo-coordinator',kind:9,sig:'',created_at:m.created_at,tags:[['h',channelId],noted.tag],state:'task',sortKey:(task.messages[0]?.created_at??m.created_at)*1000+index};
    // Your answer to an agent's question: your words as your message, the question above it as the asker's (tagged).
    const answered=m.role==='user'&&!setup&&!request?answeredMessage(m.text,questions):null;
    return {id:taskRowId(task,index),content:setup?PROJECT_OVERVIEW_SUMMARY:request?(task.role==='reviewer'?(task.agent?t('Review requested from {agent}.',{agent:agentLabel(task.agent)}):t('Review requested.')):(task.agent?t('Help requested from {agent}.',{agent:agentLabel(task.agent)}):t('Help requested.'))):answered?answered.answer:m.role==='user'?yourWords(m.text):m.text,pubkey:setup||request||m.source==='independent_review'?'dervo-coordinator':m.role==='assistant'?'provider':'local-user',kind:9,sig:'',created_at:m.created_at,tags:answered?[['h',channelId],[QUESTION_TAG,answered.asker,answered.ask]]:[['h',channelId]],state:'task',sortKey:(task.messages[0]?.created_at??m.created_at)*1000+index};
  }));
}
export function taskRowId(task:TaskLike,index:number){return `${task.id}:${index}`;}
/** The footer for a task hangs on its last row, so the newest task's status sits under its own request. */
export function taskFooterKey(task:TaskLike){return taskRowId(task,task.messages.length-1);}
export function orderRows(history:LocalEvent[],tasks:TaskLike[],channelId:string,overview=false):LocalEvent[] {
  const rows:Keyed[]=[...history.map(row=>({...row,sortKey:row.created_at*1000})),...taskRows(tasks,channelId,overview)];
  return rows.sort((a,b)=>a.sortKey-b.sortKey).map(({sortKey:_,...row})=>row);
}
