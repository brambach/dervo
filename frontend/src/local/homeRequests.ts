import type {HomeEntry} from './Home';
import {pendingQuestion} from './questionShape';
import {t} from '@/i18n';

export type HomeRequest={id:string;title:string;createdAt:number;entries:HomeEntry[]};

/** Explicit request IDs group new exchanges. Older user/reply pairs remain readable without guessing intent. */
export function homeRequests(entries:HomeEntry[]):HomeRequest[]{
 const groups=new Map<string,HomeRequest>(),userGroups=new Map<string,string>();
 let previous:string|undefined;
 for(const entry of entries){
  if(entry.arrival)continue;
  if(entry.kind==='you'){
   const id=entry.request_id||entry.id;
   userGroups.set(entry.id,id);previous=id;
   if(!groups.has(id))groups.set(id,{id,title:entry.text?.trim()||'Image request',createdAt:entry.created_at,entries:[]});
   groups.get(id)!.entries.push(entry);
  }else{
   const id=entry.request_id||(entry.answers?userGroups.get(entry.answers):previous);
   // Something Home says on its own (an agent's answer, a routine's result, the heartbeat) is its own request, titled
   // by what it is, never filed under the last thing you asked (dervo_assistant.home_post).
   if(id&&entry.assistant&&!groups.has(id))groups.set(id,{id,title:entry.assistant.title,createdAt:entry.created_at,entries:[]});
   if(id&&groups.has(id))groups.get(id)!.entries.push(entry);
  }
 }
 return [...groups.values()].reverse();
}

export function requestQuestion(request:HomeRequest){return pendingQuestion(request.entries);}

/** A reply as one plain line for the list: Home's **bold** and `code` are markup for the reply itself, and in a
 * two-line summary they read as stray symbols (UX review, 23 Sep, Lows). */
export function plainSummary(text:string):string{
 return text.replace(/\*\*([^*]+)\*\*/g,'$1').replace(/`([^`\n]*)`/g,'$1').replace(/\*\*|`/g,'');
}

export function requestSummary(request:HomeRequest):string{
 const last=request.entries.at(-1);
 const question=requestQuestion(request);
 if(question)return question.ask;
 if(last?.state==='thinking')return last.held?t('Waits for Home’s earlier answers.'):t('Finding the right threads…');
 if(last?.state==='failed')return t('The routing reply failed. Open to retry.');
 const parts=request.entries.flatMap(entry=>entry.parts??[]);
 if(parts.some(part=>part.state==='failed'))return t('A request couldn’t be sent. Open to retry.');
 const names=[...new Set(parts.map(part=>part.conversation?.name||part.title||part.name).filter(Boolean))];
 if(names.length)return names.join(' · ');
 return last?.kind==='reply'?(last.text?plainSummary(last.text):t('Open request')):t('Waiting for a reply');
}
