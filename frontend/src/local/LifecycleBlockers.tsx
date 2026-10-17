import {useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {t} from '@/i18n';

export type BlockerAction='stop'|'settle'|'dismiss'|'cancel';
export type LifecycleBlocker={projectId:string;projectName:string;channelId:string|null;threadName:string;taskId:string|null;taskName:string;kind:string;instruction:string;
  taskState?:string|null;actions?:BlockerAction[];actionChannelId?:string|null;actionTaskId?:string|null;queueId?:string|null};
export type BlockerScope={scope:'release'|'project'|'conversation';projectId:string|null;channelId:string|null};
export type OpenBlocker=(blocker:LifecycleBlocker)=>void;

/** Each action is the task sheet's own host operation on the blocker's exact ids (app #9, 23 Sep): Stop, Leave it stopped (Mark as stopped until beta.18),
 * Don't run to decline a permission request that's waiting (the task sheet's word for task_deny: it refuses the request,
 * it doesn't just hide the card), Cancel for a queued request. The host checks each one again. */
export const ACTION_WORDS:Record<BlockerAction,{label:string;busy:string}>={
  stop:{label:t('Stop'),busy:t('Stopping…')},settle:{label:t('Leave it stopped'),busy:t('Leaving it stopped…')},dismiss:{label:t('Don’t run'),busy:t('Declining…')},cancel:{label:t('Cancel request'),busy:t('Cancelling…')},
};
export function blockerRequest(blocker:LifecycleBlocker,action:BlockerAction):[string,Record<string,unknown>]|null{
  const scope={projectId:blocker.projectId,channelId:blocker.actionChannelId};
  if(!blocker.actionChannelId)return null;
  if(action==='cancel')return blocker.queueId?['coding_queue_cancel',{...scope,queueId:blocker.queueId}]:null;
  if(!blocker.actionTaskId)return null;
  return [{stop:'task_stop',settle:'task_settle',dismiss:'task_deny'}[action],{...scope,taskId:blocker.actionTaskId}];
}
/** What the task is doing now, in the words the task sheet uses. */
const STATE_WORDS:Record<string,string>={working:t('Running'),disconnected:t('Dervo lost track of this run'),needs_permission:t('Waiting for your go-ahead'),
  ready_for_review:t('Ready for review'),blocked:t('Needs attention'),answered:t('Answered'),completed:t('Completed'),idle:t('Result approved')};

/** Links into the task or terminal that needs attention, and the button that clears it where it's named. The host
 * checks again before any lifecycle change. */
export function LifecycleBlockers({scope,onOpen,renderAction}:{scope:BlockerScope;onOpen:OpenBlocker;renderAction?:(blocker:LifecycleBlocker)=>ReactNode}){
 const client=useQueryClient();
 const query=useQuery({queryKey:['lifecycle-blockers',scope.scope,scope.projectId,scope.channelId],queryFn:()=>local<LifecycleBlocker[]>('lifecycle_blockers',scope),refetchInterval:3000});
 const [busy,setBusy]=useState(''),[failed,setFailed]=useState<{key:string;text:string}|null>(null);
 if(query.isPending)return <p role="status" className="text-xs text-muted-foreground">{t('Finding the work that needs attention…')}</p>;
 if(query.isError)return <p className="text-xs text-muted-foreground">{t('The task links couldn’t load.')} <button type="button" className="underline" onClick={()=>void query.refetch()}>{t('Try again')}</button></p>;
 if(!query.data.length)return null;
 async function act(blocker:LifecycleBlocker,action:BlockerAction,key:string){
  const request=blockerRequest(blocker,action);
  if(!request||busy)return;
  setBusy(key+action);setFailed(null);
  try{await local(request[0],request[1]);await Promise.all([query.refetch(),client.invalidateQueries({queryKey:['local-status']})]);}
  catch(failure){setFailed({key,text:failure instanceof Error?failure.message:String(failure)});}
  finally{setBusy('');}
 }
 return <section className="my-3 space-y-3 text-sm" aria-label={t('Work that needs attention')}>
  <p className="font-medium">{t('Work that needs attention')}</p>
  <ul className="space-y-3">{query.data.map((blocker,index)=>{
   const key=JSON.stringify([blocker.projectId,blocker.channelId,blocker.taskId,blocker.kind,blocker.queueId,index]);
   const state=blocker.taskState?STATE_WORDS[blocker.taskState]:undefined;
   return <li key={key} className="space-y-2 rounded-lg border p-3 break-words" data-testid="lifecycle-blocker">
   {(scope.scope==='release'||blocker.threadName!==blocker.taskName)&&<p className="text-xs text-muted-foreground">{scope.scope==='release'?`${blocker.projectName} · `:''}{blocker.threadName}</p>}
   {blocker.taskName&&<p className="font-medium">{blocker.taskName}</p>}
   {(blocker.taskId||blocker.actionTaskId)&&<p className="text-xs text-muted-foreground break-all">{state?`${state} · `:''}{t('Task {id}',{id:blocker.taskId??blocker.actionTaskId??''})}</p>}
   <p className="text-xs">{blocker.instruction}</p>
   <div className="flex flex-wrap gap-2">
    {(blocker.actions??[]).map(action=><button key={action} type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy}
      aria-label={`${ACTION_WORDS[action].label}: ${blocker.taskName||blocker.threadName}`} onClick={()=>void act(blocker,action,key)}>{busy===key+action?ACTION_WORDS[action].busy:ACTION_WORDS[action].label}</button>)}
    {blocker.kind==='terminal'?<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-label={t('Open terminal: {projectName}',{projectName:blocker.projectName})} onClick={()=>onOpen(blocker)}>{t('Open terminal')}</button>:blocker.channelId&&(renderAction?renderAction(blocker):<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" aria-label={blocker.taskId?t('Open task: {name}',{name:blocker.taskName||blocker.threadName||''}):t('Open thread: {name}',{name:blocker.taskName||blocker.threadName||''})} onClick={()=>onOpen(blocker)}>{blocker.taskId?t('Open task'):t('Open thread')}</button>)}
   </div>
   {failed?.key===key&&<p role="alert" className="text-xs text-destructive select-text">{failed.text}</p>}
  </li>;})}</ul>
 </section>;
}
