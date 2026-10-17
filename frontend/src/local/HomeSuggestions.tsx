import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {StatusGlyph} from './StatusGlyph';
import {SAFETY_MS,useThreadChanges} from './threadStates';
import {spoken} from './homeTime';
import {keepFocus} from './Reminders';
import {t} from '@/i18n';

/** Home's Suggested list (9 Oct, Bryce and tester Harrison: Home is reminders, suggestions and what needs you).
 * Harrison asked whether Dervo sees the recommended tasks Claude shows after a long task. Those chips come from a tool
 * the Claude desktop app gives Claude. Codex sends nothing like them; Claude Code can print one guessed next prompt per
 * turn (--prompt-suggestions), which Dervo doesn't ask for, since a guess at what you'd type isn't a task an agent
 * proposes. These rows are Dervo's own instead: thread proposals (backend/thread_fanout.py) an agent made in one of
 * your threads, that nobody has started or dismissed this week. A row opens its task, where the proposal shows its
 * whole request beside Start. Nothing on Home starts one: Start sends the agent's words, so you read them first.
 * Dismiss here is the thread's own Dismiss. */
export type Suggestion={id:string;title:string;purpose:string;channelId:string;thread:string;taskId:string;provider:string|null;createdAt:number};
export type Suggestions={suggestions:Suggestion[];more:number};

/** Who suggested it and where: "Suggested by Claude in #Parser fix". */
export function suggestedBy(row:Pick<Suggestion,'provider'|'thread'>):string{
 const who=row.provider==='codex'?'Codex':row.provider==='claude'?'Claude':null;
 return who?t('Suggested by {who} in #{thread}',{who,thread:row.thread}):t('Suggested in #{thread}',{thread:row.thread});
}

/** ``shown``: Home draws the list once its threads are read, so rows don't land above it and push it down. */
export function HomeSuggestions({projectId,profileId,shown,onOpen,onOpenTask}:{projectId:string;profileId:string;shown:boolean;onOpen:(channelId:string)=>void;
 onOpenTask?:(channelId:string,taskId:string,trigger:HTMLElement)=>void}){
 const client=useQueryClient();
 const key=['home-suggestions',projectId,profileId];
 // A proposal is made as a run ends and started from its thread, and both change a thread's state word, so the state
 // spine says when to look again (threadStates.ts); a step moving on doesn't. The slow timer is only a safety net. A
 // host without the op refuses once, and there's no list.
 const found=useQuery({queryKey:key,enabled:!!projectId&&!!profileId,retry:false,refetchInterval:SAFETY_MS,
  queryFn:()=>local<Suggestions>('home_suggestions',{projectId,profileId})});
 useThreadChanges(projectId,()=>void client.invalidateQueries({queryKey:key}),(next,before)=>next?.state!==before?.state);
 const [busy,setBusy]=useState(''),[failure,setFailure]=useState('');
 const rows=found.data?.suggestions??[],more=found.data?.more??0;
 if(!shown||!rows.length)return null;
 async function dismiss(row:Suggestion){
  if(busy)return false;
  setBusy(row.id);setFailure('');
  try{
   await local('task_thread_dismiss',{projectId,channelId:row.channelId,taskId:row.taskId,proposalId:row.id});
   client.setQueryData<Suggestions>(key,list=>list&&{...list,suggestions:list.suggestions.filter(other=>other.id!==row.id)});
   // The thread's own card shows it dismissed too, and the list fills up from the rest.
   await Promise.all([client.invalidateQueries({queryKey:key}),client.invalidateQueries({queryKey:['thread-proposals',projectId,row.channelId,row.taskId]})]);
   return true;
  }catch(error){setFailure((error instanceof Error?error.message:typeof error==='string'?error:'')||t('That suggestion couldn’t be dismissed. It’s still here.'));return false;}
  finally{setBusy('');}
 }
 const now=Date.now()/1000;
 return <section className="dv-now-section dv-bucket" aria-label={t('Suggested')} data-bucket="suggested">
  <div className="dv-bucket-bar"><div className="dv-label dv-now-label dv-bucket-head">{t('Suggested')}{' '}<span className="dv-now-count">{rows.length+more}</span></div></div>
  <div className="dv-sheet">{rows.map(row=><article key={row.id} className="dv-row dv-home-row dv-home-suggestion" data-suggestion={row.id}>
   <StatusGlyph state="idle"/>
   <div>
    <button type="button" className="dv-row-name dv-home-row-open" aria-label={t('Open #{thread} to read and start: {title}',{thread:row.thread,title:row.title})}
     onClick={event=>onOpenTask?onOpenTask(row.channelId,row.taskId,event.currentTarget):onOpen(row.channelId)}>{row.title}</button>
    {row.purpose&&<p className="dv-row-did">{row.purpose}</p>}
    <p className="dv-row-meta"><span>{t('Not started')}</span><span>{suggestedBy(row)}</span><span>{spoken(row.createdAt,now)}</span></p>
   </div>
   <div className="dv-row-end">
    <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={!!busy} aria-label={t('Dismiss the suggestion {title}',{title:row.title})}
     onClick={event=>{const refocus=keepFocus(event);void dismiss(row).then(ok=>{if(ok)refocus();});}}>{busy===row.id?t('Dismissing…'):t('Dismiss')}</button>
   </div>
  </article>)}</div>
  {more>0&&<p className="dv-caption dv-now-more">{t('{count} more suggestions are in their threads.',{count:more})}</p>}
  {failure&&<p className="dv-caption dv-home-failure" role="alert">{failure}</p>}
 </section>;
}
