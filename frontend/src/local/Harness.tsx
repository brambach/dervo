import {useState} from 'react';
import {local,type HarnessDelegation,type HarnessRequest,type LocalEvent} from './api';
import {providerAuthor} from './providerLabel';
import {ConversationMemory} from './ConversationMemory';
import {t} from '@/i18n';

/** Coordination rows and controls. Text only: a returned answer is a message, never a file change or an executed task. */
export const COORDINATION_ACTIVE=new Set(['queued','running','stopping']);
export const COORDINATION_LABELS:Record<string,string>={queued:t('Queued'),running:t('Writing an answer'),stopping:t('Stopping'),completed:t('Answer ready'),cancelled:t('Stopped'),failed:t('Couldn’t answer'),lost:t('Helper disconnected'),unknown:t('Outcome unconfirmed')};
type Keyed=LocalEvent&{sortKey:number;sortOrder:number};
export function requestRowId(request:Pick<HarnessRequest,'id'>){return `harness:${request.id}:request`;}
export function resultRowId(request:Pick<HarnessRequest,'id'>){return `harness:${request.id}:result`;}
function epoch(iso:string|null|undefined,fallback:number){
  if(typeof iso!=='string'||!/(Z|[+-]\d{2}:\d{2})$/.test(iso))return fallback;
  const parsed=Date.parse(iso),fraction=iso.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1]??'';
  return Number.isFinite(parsed)&&parsed>=0?parsed/1000+(fraction.length>3?Number('0.'+fraction.slice(3))/1000:0):fallback;
}
function returnedAt(request:HarnessRequest){return epoch(request.linked_return?.returned_at,request.created_at);}
export function helpRowId(request:Pick<HarnessRequest,'id'>,delegation:Pick<HarnessDelegation,'id'>){return `harness:${request.id}:help:${delegation.id}`;}
/** A helper's provider from its stable handle (`@claude-…`, `@codex-…`); anything else is labelled unknown. */
export function helperProvider(handle:string):string {const prefix=handle.replace(/^@/,'').split('-')[0];return prefix==='claude'||prefix==='codex'?prefix:'';}
/** The request as your own row, the helper's answer as a provider row, and each linked helper's returned answer as its own provider row. */
export function harnessRows(requests:HarnessRequest[],channelId:string):Keyed[] {
  return requests.flatMap(request=>{
    const rows:Keyed[]=[{id:requestRowId(request),content:request.text,pubkey:'local-user',kind:9,sig:'',created_at:request.created_at,tags:[['h',channelId]],state:'coordination',sortKey:request.created_at*1000,sortOrder:0}];
    if(request.linked_return)rows.push({id:resultRowId(request),content:request.linked_return.summary,pubkey:`helper:${request.provider??''}`,kind:9,sig:'',created_at:returnedAt(request),tags:[['h',channelId]],state:'coordination',sortKey:Math.max(returnedAt(request),request.created_at)*1000,sortOrder:2});
    for(const delegation of request.delegations)if(delegation.status==='returned'&&delegation.result){
      const at=Math.max(epoch(delegation.returned_at,request.created_at),request.created_at);
      rows.push({id:helpRowId(request,delegation),content:t('{handle} answered: {summary}',{handle:delegation.handle,summary:delegation.result.summary}),pubkey:`helper:${helperProvider(delegation.handle)}`,kind:9,sig:'',created_at:at,tags:[['h',channelId]],state:'coordination',sortKey:at*1000,sortOrder:1});
    }
    return rows;
  });
}
/** Interleave coordination rows into an already ordered timeline by time; ties keep the existing rows first. */
export function mergeHarnessRows(rows:LocalEvent[],extra:Keyed[]):LocalEvent[] {
  if(!extra.length)return rows;
  const keyed:Keyed[]=[...rows.map(row=>({...row,sortKey:row.created_at*1000,sortOrder:-1})),...extra];
  return keyed.sort((a,b)=>a.sortKey-b.sortKey||a.sortOrder-b.sortOrder).map(({sortKey:_,sortOrder:__,...row})=>row);
}
export function HarnessFooter({request,projectId,channelId,refresh}:{request:HarnessRequest;projectId:string;channelId:string;refresh:()=>Promise<unknown>}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function stop(){setBusy(true);setError('');try{await local('harness_cancel',{projectId,channelId,requestId:request.id});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  const provider=providerAuthor(request.provider);
  const label=COORDINATION_LABELS[request.state]??request.state;
  return <div className="ml-[58px] text-2xs text-muted-foreground/70" data-testid="coordination-state">
    <div className="flex items-center gap-2"><span role="status">{label}{request.state==='running'||request.state==='completed'?` · ${provider}`:''}</span>
      {COORDINATION_ACTIVE.has(request.state)&&<button disabled={busy||request.state==='stopping'} onClick={()=>void stop()} className="rounded px-2 hover:bg-muted">{t('Stop')}</button>}
    </div>
    {(request.activity??[]).map(a=><p key={a.seq} data-testid="coordination-activity">{a.text}</p>)}
    {!(request.activity??[]).length&&request.delegations.length>0&&<p>{t('Helpers asked: {helpers}',{helpers:request.delegations.map(d=>`${d.handle} (${d.status})`).join(', ')})}</p>}
    {request.delegations.some(d=>d.status==='dispatched'||d.status==='cancelling')&&<p>{t('A linked helper is still working; its answer will appear here when it returns.')}</p>}
    {request.state==='completed'&&<p>{t('This reply doesn\'t change project files. To edit files, send your request without an @mention in a conversation with coding allowed.')}</p>}
    <ConversationMemory request={request} projectId={projectId} channelId={channelId}/>
    {(request.error||error)&&<p role="alert" className="max-w-xl text-destructive">{request.error||error}</p>}
  </div>;
}
