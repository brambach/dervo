import {useState} from 'react';
import {codingRequest,local,type LocalEvent,type QueuedRequest} from './api';
import {formatDate,language,t} from '@/i18n';

/** Messages sent while this conversation's own coding task was running. Each waits as your own row with a footer that says
 * where it stands; nothing is created or sent for it until the run ends. One that couldn't start keeps its reason until
 * it's sent again or dismissed. Send again re-sends the item's exact text, images, check and review as a fresh request
 * through codingRequest(), which owns the request id, and dismisses the failed item only once the host accepted the resend. */
type Keyed=LocalEvent&{sortKey:number;sortOrder:number};
export function queueRowId(item:Pick<QueuedRequest,'id'>){return `queue:${item.id}`;}
export function queueRows(queue:QueuedRequest[],channelId:string):Keyed[] {
  return queue.map(item=>({id:queueRowId(item),content:item.text,pubkey:'local-user',kind:9,sig:'',created_at:item.created_at,tags:[['h',channelId]],state:'queued',sortKey:item.created_at*1000,sortOrder:3}));
}
/** A message you just sent, shown at once while the host records it. It carries no footer and is replaced by the saved row. */
export type SendingRow=Keyed&{images?:string[]};
export function sendingRow(channelId:string,text:string,now=Date.now(),images:string[]=[]):SendingRow {
  return {id:`sending:${crypto.randomUUID()}`,content:text,pubkey:'local-user',kind:9,sig:'',created_at:Math.floor(now/1000),tags:[['h',channelId]],state:'sending',sortKey:now+1e12,sortOrder:4,images};
}
const clock=(seconds:number)=>formatDate(new Date(seconds*1000),{hour:'numeric',minute:'2-digit'});
export function queueLabel(item:Pick<QueuedRequest,'state'|'position'>&{until?:number|null;why?:string|null;held?:boolean}):string {
  if(item.state!=='queued')return t('Couldn’t start');
  // Kept across a restart and held until the runs Dervo lost here are settled (backend review, 23 Sep): not a slot wait,
  // so it never reads as one. The line under it says what to do.
  if(item.held)return t('Kept after a restart');
  if(item.until)return t('Waiting for the reset at {time}',{time:clock(item.until)});
  // Every task slot was busy in other threads when it was sent (23 Sep): it starts when one frees, not after a run here.
  if(item.why==='slot')return t('Waiting for a slot');
  if(item.position===1||item.position==null)return t('Queued · runs next');
  // "3rd" in English; another language writes its own ordinal mark in the catalog around the number.
  const n=item.position,ordinal=language()!=='en'?String(n):`${n}${n%10===2&&n%100!==12?'nd':n%10===3&&n%100!==13?'rd':'th'}`;
  return t('Queued · {ordinal} in line',{ordinal});
}
export function QueueFooter({item,projectId,channelId,refresh}:{item:QueuedRequest;projectId:string;channelId:string;refresh:()=>Promise<unknown>}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function run(action:()=>Promise<unknown>){
    if(busy)return;
    setBusy(true);setError('');
    try{await action();await refresh();}
    catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }
  const cancel=()=>run(()=>local('coding_queue_cancel',{projectId,channelId,queueId:item.id}));
  const sendAgain=()=>run(async()=>{
    await codingRequest(channelId,item.text,item.review??undefined,item.images,item.checkProfile??undefined);
    await local('coding_queue_cancel',{projectId,channelId,queueId:item.id});
  });
  return <div className="dervo-task-footer dervo-queue-item" data-testid="queue-item" data-state={item.state}>
    <div className="dervo-queue-line">
      <span role="status" className="dv-caption">{queueLabel(item)}</span>
      {item.state==='queued'&&<button type="button" disabled={busy} onClick={()=>void cancel()} className="dv-btn dv-btn-quiet">{t('Cancel')}</button>}
      {item.state==='failed'&&<>
        <button type="button" disabled={busy} onClick={()=>void sendAgain()} className="dv-btn dv-btn-quiet">{t('Send again')}</button>
        <button type="button" disabled={busy} onClick={()=>void cancel()} className="dv-btn dv-btn-quiet">{t('Dismiss')}</button>
      </>}
    </div>
    {item.state==='failed'&&<p role="alert" className="dv-row-line dervo-queue-detail">{item.detail}</p>}
    {item.state==='queued'&&item.held&&<p className="dv-row-line dervo-queue-detail">{item.detail} {t('Mark each one stopped once you’ve checked its folder.')}</p>}
    {error&&<p role="alert" className="dv-row-line dervo-queue-detail">{error}</p>}
  </div>;
}
