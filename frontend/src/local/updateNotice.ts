import {useSyncExternalStore} from 'react';
import {local} from './api';

/** The update prompt's state from the host (beta_update_host.py, notice): one poll shared by the prompt, the dot on the
 * account menu's Settings and the dot on Settings > Updates. The host checks the catalog on open and every few hours by
 * itself; this only reads what it found. A failed read shows nothing, and Settings > Updates keeps its own error. */
export type UpdateStatus={context:string;state:string;detail:string;busy:boolean;received:number;
  canFetch?:boolean;canDownload:boolean;canUpdate?:boolean;release:{release_id:string;bytes:number;format:string}|null;
  update?:{source:'local'|'download';releaseId:string}|null;
  /** Why an update couldn't replace this app where it runs (beta_update_host.py install_problem), or null. */
  installProblem?:{kind:'move'|'permission';line:string}|null};
export type UpdateNotice={
  prompt:{releaseId:string;title:string;notes:string[];bytes:number|null}|null;
  dot:boolean;
  updated:{releaseId:string;notes:string[]}|null;
  status:UpdateStatus|null;
};

export const EMPTY_NOTICE:UpdateNotice={prompt:null,dot:false,updated:null,status:null};
/** Every minute while nothing's happening; every second while an update downloads or unpacks, for its progress. */
export const QUIET_MS=60_000, BUSY_MS=1000;

let current:UpdateNotice=EMPTY_NOTICE;
let timer:ReturnType<typeof setTimeout>|undefined;
let sequence=0;
const listeners=new Set<()=>void>();

function publish(next:UpdateNotice){current=next;for(const listener of listeners)listener();}
export const pollDelay=(notice:UpdateNotice)=>notice.status?.busy?BUSY_MS:QUIET_MS;

async function poll(){
  clearTimeout(timer);
  const request=++sequence;
  let next=current;
  try{next=await local<UpdateNotice>('beta_update_notice');}catch{next=EMPTY_NOTICE;}
  if(request!==sequence)return;
  publish(next);
  // The first check takes a moment: read again soon so the prompt appears once it's done.
  if(listeners.size)timer=setTimeout(()=>void poll(),next.status?.state==='checking'?BUSY_MS:pollDelay(next));
}

function subscribe(listener:()=>void){
  listeners.add(listener);
  if(listeners.size===1)void poll();
  return ()=>{listeners.delete(listener);if(!listeners.size){clearTimeout(timer);sequence++;}};
}

export function useUpdateNotice():UpdateNotice{
  return useSyncExternalStore(subscribe,()=>current,()=>EMPTY_NOTICE);
}

/** Read again now, after an action changed the host's state. */
export function refreshUpdateNotice(){return poll();}

/** Later: this build's prompt stays hidden until tomorrow. The dot stays. */
export async function updateLater(releaseId:string){publish(await local<UpdateNotice>('beta_update_later',{releaseId}));}
/** The one-time "You're on" message has been read. */
export async function updateSeen(releaseId:string){publish(await local<UpdateNotice>('beta_update_seen',{releaseId}));}

/** For tests: forget the shared state between cases. */
export function resetUpdateNotice(){clearTimeout(timer);sequence++;listeners.clear();current=EMPTY_NOTICE;}
