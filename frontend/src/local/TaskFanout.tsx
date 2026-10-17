import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useChannelNavigation} from '@/shared/context/ChannelNavigationContext';
import {local} from './api';
import './TaskFanout.css';
import {t} from '@/i18n';

type Proposal={id:string;title:string;purpose:string;request:string;source_request:string;state:string;channelId:string|null;taskId:string|null;canStart:boolean;canDismiss:boolean;detail:string|null;read_only:boolean;identity:string;model:string;fullAccess:boolean};
export type ThreadSource={channelId:string|null;taskId:string|null;name:string;purpose:string};
const LABELS:Record<string,string>={proposed:t('Ready to start'),starting:t('Checking whether it started'),started:t('Started'),queued:t('Waiting for a free task slot'),cancelled:t('Cancelled'),dismissed:t('Dismissed'),failed:t('Needs attention'),unknown:t('Needs checking')};

export function ThreadSourceLink({source}:{source:ThreadSource}){
 const navigation=useChannelNavigation();
 return <p className="dv-caption">From {source.channelId?<button type="button" className="underline" onClick={()=>navigation.onOpenTask&&source.taskId?navigation.onOpenTask(source.channelId!,source.taskId):navigation.onOpenChannel?.(source.channelId!)}>{source.name}</button>:source.name} · {source.purpose}</p>;
}

export function TaskFanout({projectId,channelId,taskId}:{projectId:string;channelId:string;taskId:string}){
 const client=useQueryClient(),navigation=useChannelNavigation();
 const scope={projectId,channelId,taskId},key=['thread-proposals',projectId,channelId,taskId];
 const query=useQuery({queryKey:key,queryFn:()=>local<Proposal[]>('task_thread_proposals',scope),refetchInterval:q=>q.state.data?.some(row=>['proposed','starting','queued'].includes(row.state))?3000:false});
 const [busy,setBusy]=useState(''),[error,setError]=useState('');
 const alive=useRef(true),sending=useRef(false),cancelBatch=useRef(false);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;cancelBatch.current=true;};},[projectId,channelId,taskId]);
 async function refresh(){await Promise.all([client.invalidateQueries({queryKey:key}),client.invalidateQueries({queryKey:['local-conversations',projectId]})]);}
 async function act(operation:string,rows:Proposal[]){
  if(sending.current)return;
  sending.current=true;cancelBatch.current=false;setError('');setBusy(rows.length>1?'batch':rows[0].id);
  try{
   for(const row of rows){
    if(cancelBatch.current)break;
    await local(operation,{...scope,proposalId:row.id});
    await refresh();
   }
  }catch(failure){if(alive.current)setError(failure instanceof Error?failure.message:String(failure));}
  finally{sending.current=false;if(alive.current)setBusy('');}
 }
 async function open(row:Proposal){
  await client.invalidateQueries({queryKey:['local-conversations',projectId]});
  if(!alive.current||!row.channelId)return;
  if(row.taskId&&navigation.onOpenTask)navigation.onOpenTask(row.channelId,row.taskId);
  else navigation.onOpenChannel?.(row.channelId);
 }
 if(query.isPending)return <p role="status" className="dv-caption">{t('Loading proposed threads…')}</p>;
 if(query.isError)return <p role="alert" className="dv-line-alert">{t('Thread proposals couldn’t load.')} <button type="button" className="underline" onClick={()=>void query.refetch()}>{t('Try again')}</button></p>;
 const rows=query.data,ready=rows.filter(row=>row.canStart),full=ready.some(row=>row.fullAccess);
 if(!rows.length)return null;
 return <section className="dv-thread-proposals" aria-label={t('Separate threads')}>
  <div className="dv-actions"><h3 className="dv-label">{t('Separate threads')}</h3>{ready.length>1&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy} onClick={()=>void act('task_thread_start',ready)}>Start {ready.length} threads{full?' with full access':''}</button>}
   {busy==='batch'&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{cancelBatch.current=true;}}>{t('Stop starting more')}</button>}</div>
  {/* Bryce, 26 Sep: should several ideas share one session, or keep a thread each? (PLAN-beta17, section 1). The same
      rule is in the agent's instructions (thread_fanout.PROMPT); Dervo has no help page, so it's said here, where it applies. */}
  <p className="dv-caption">{t('Each thread gets its own request, result and Catch up row, for work you’ll want to find, review or approve on its own. They sit under this one in the sidebar, and this thread gets a review of them when they’ve all finished. Splitting one piece of work inside a thread is the agent’s own sub-agents’ job.')}</p>
  {rows.map(row=><article key={row.id} className="dv-thread-proposal">
   <div className="dv-actions"><h4>{row.title}</h4><span className="dv-caption" role="status">{LABELS[row.state]??row.state}</span></div>
   <p>{row.purpose}</p><p className="dv-caption">{row.identity} · {row.model}{row.read_only?' · Read only':''}{row.fullAccess?' · Full access':''}</p>
   {/* Open, never folded away: these are the agent's words, and Start sends every one of them. */}
   <div><p className="dv-label">{t('Request to send')}</p><p className="dv-proposed-request">{row.request}</p></div>
   {row.detail&&<p className="dv-line-alert">{row.detail}</p>}
   <div className="dv-actions">{row.canStart&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy} onClick={()=>void act('task_thread_start',[row])}>{busy===row.id?t('Starting…'):row.state==='failed'||row.state==='starting'?t('Check and retry'):row.fullAccess?t('Start with full access'):t('Start thread')}</button>}
    {row.canDismiss&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void act('task_thread_dismiss',[row])}>{t('Dismiss proposal')}</button>}
    {row.channelId&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void open(row)}>{t('Open thread')}</button>}</div>
  </article>)}
  {error&&<p role="alert" className="dv-line-alert">{error}</p>}
 </section>;
}
