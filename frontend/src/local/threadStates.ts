import {useEffect,useRef,useSyncExternalStore} from 'react';
import {listen} from '@tauri-apps/api/event';
import {local} from './api';

/** The state spine (backend/thread_states.py): one live record per conversation, across projects. The host writes one
 * short event when any record moves, and the window then reads only the records past the version it already has. */
export type ThreadState='needs_you'|'working'|'quiet'|'unknown'|'stopped'|'finished'|'idle';
export type ThreadRecord={id:string;projectId:string;name:string|null;visibility:string;archived:boolean;version:number;
  state:ThreadState;step:string|null;since:number|null;aliveAt:number|null;summary:string|null;goal:string|null;next:string|null;
  request:string|null;attention:boolean;unread:number;lost:boolean;at:number|null;
  /** A run is live (a needs row can still be running: asking doesn't stop the worker). */
  running:boolean;
  /** What a worker asked you and is waiting on, or null. */
  pending_question:string|null;pending_question_count:number;
  /** The thread's newest Send to cloud run, as Dervo last saw it, or null (PR #12). */
  cloud:CloudRun|null};
export type CloudRun={id:string;provider:'claude'|'codex';state:'starting'|'working'|'ready'|'failed'|'unknown';url:string|null;
  branch:string|null;detail:string|null;startedAt:number;at:number};
export type ThreadChanges={version:number;threads:(ThreadRecord|{id:string;gone:true})[]};
export type ThreadStates={version:number;records:ReadonlyMap<string,ThreadRecord>};
export const HOST_EVENT='dervo-host-event';
/** Without a push (an older host, or a browser preview), the window still asks what changed, this often. */
export const FALLBACK_MS=3000;
/** Lists that re-read on a push still re-read this often, in case a change came from somewhere the host doesn't push. */
export const SAFETY_MS=30000;

/** The records after applying ``changes``: moved ones replaced, deleted ones dropped, a stale answer ignored. */
export function mergeChanges(current:ThreadStates,changes:ThreadChanges):ThreadStates{
  if(changes.version<current.version)return current;
  const records=new Map(current.records);
  for(const row of changes.threads){
    if('gone' in row)records.delete(row.id);
    else if((records.get(row.id)?.version??-1)<=row.version)records.set(row.id,row);
  }
  return {version:changes.version,records};
}

/** Whether a pushed event says there's something newer than ``version`` to read. */
export function eventIsNewer(payload:unknown,version:number):boolean{
  if(!payload||typeof payload!=='object')return false;
  const event=payload as {kind?:unknown;version?:unknown};
  return event.kind==='threads'&&typeof event.version==='number'&&event.version>version;
}

/** How a store listens and keeps itself fresh, replaceable in tests. ``listen`` resolves when pushes can arrive. */
type Wiring={listen:(onEvent:(payload:unknown)=>void)=>Promise<()=>void>;setTimer:(run:()=>void,ms:number)=>unknown;clearTimer:(timer:unknown)=>void};
const tauriWiring:Wiring={
  listen:onEvent=>listen<unknown>(HOST_EVENT,event=>onEvent(event.payload)),
  setTimer:(run,ms)=>setTimeout(run,ms),
  clearTimer:timer=>clearTimeout(timer as ReturnType<typeof setTimeout>),
};
/** A failed read (the host answering "busy", say) is tried again this soon, doubling up to RETRY_MAX_MS. */
export const RETRY_MS=1000,RETRY_MAX_MS=15000;

/** One store per window: every screen that shows a thread's state reads the same records, through one listener and
 * one timer however many screens use it (code review 8 Oct: one each made every push cost two reads). */
function createStore(read:(since:number)=>Promise<ThreadChanges>,wiring:Wiring=tauriWiring){
  let state:ThreadStates={version:0,records:new Map()};
  let reading:Promise<void>|null=null,again=false;
  let pushes=false,failures=0,timer:unknown=null,stopListening:(()=>void)|null=null,generation=0;
  const listeners=new Set<()=>void>();
  function publish(next:ThreadStates){if(next!==state){state=next;for(const listener of listeners)listener();}}
  function schedule(){
    if(timer!==null)wiring.clearTimer(timer);
    if(!listeners.size){timer=null;return;}
    // Pushing: a slow safety read. Not (yet): ask every FALLBACK_MS. A failed read: try again soon.
    const wait=failures?Math.min(RETRY_MS*2**(failures-1),RETRY_MAX_MS):pushes?SAFETY_MS:FALLBACK_MS;
    timer=wiring.setTimer(()=>{timer=null;void refresh();},wait);
  }
  async function refresh(){
    if(reading){again=true;return reading;}
    reading=(async()=>{
      do{
        again=false;
        try{
          const changes=await read(state.version);
          // A lower newest number than the window has means the host is on a fresh store: read it all, start over.
          publish(changes.version<state.version?mergeChanges({version:0,records:new Map()},await read(0)):mergeChanges(state,changes));
          failures=0;
        }catch{failures+=1;}
      }while(again);
    })().finally(()=>{reading=null;schedule();});
    return reading;
  }
  function start(){
    void refresh();
    const mine=++generation;  // a stop (StrictMode's mount, unmount, mount) before listen resolves retires this one
    try{
      void wiring.listen(payload=>{if(eventIsNewer(payload,state.version))void refresh();})
        .then(unlisten=>{if(mine===generation&&listeners.size){stopListening=unlisten;pushes=true;void refresh();}else unlisten();})  // a push sent before this was missed
        .catch(()=>{/* no Tauri here: the fallback reads */});
    }catch{/* no Tauri here */}
  }
  function stop(){generation+=1;stopListening?.();stopListening=null;pushes=false;if(timer!==null)wiring.clearTimer(timer);timer=null;}
  return {
    get:()=>state,
    refresh,
    subscribe(listener:()=>void){
      listeners.add(listener);
      if(listeners.size===1)start();
      return()=>{listeners.delete(listener);if(!listeners.size)stop();};
    },
  };
}

let shared:ReturnType<typeof createStore>|null=null;
function store(){return shared??=createStore(since=>local<ThreadChanges>('thread_states',{since}));}
/** Tests only: a fresh store reading through ``read``. */
export function resetThreadStatesForTest(read:(since:number)=>Promise<ThreadChanges>,wiring?:Wiring){shared=createStore(read,wiring);return shared;}

/** Every conversation's live record: read when the window first needs it, then on each host push (every FALLBACK_MS
 * without pushes, every SAFETY_MS with them, in case one was missed). */
export function useThreadStates():ThreadStates{
  const current=store();
  return useSyncExternalStore(current.subscribe,current.get,current.get);
}

/** Lists re-read at most this often however fast rows move (code review 8 Oct: five busy runs re-read Home's
 * pickup several times a second). */
export const CHANGES_MS=1000;

/** Calls ``changed`` with the ids of ``projectId``'s conversations whose rows moved (any project's for null; with ``only``, just the moves it accepts, given the row now and before), at most
 * every CHANGES_MS, so a screen re-reads its own list when one of its rows moved instead of on a timer. It listens to
 * the store directly, so the screen using it never re-renders for a record it doesn't draw (second review, 8 Oct: the
 * whole app re-rendered on every push). */
export function useThreadChanges(projectId:string|null,changed:(ids:string[])=>void,only?:(next:ThreadRecord|undefined,before:ThreadRecord|undefined)=>boolean){
  const latest=useRef(changed);latest.current=changed;
  const filter=useRef(only);filter.current=only;
  useEffect(()=>{
    const current=store();
    let last=current.get(),timer:ReturnType<typeof setTimeout>|null=null,at=0;
    const pending=new Set<string>();
    const mine=(id:string,records:ReadonlyMap<string,ThreadRecord>,before:ReadonlyMap<string,ThreadRecord>)=>{
      const project=(records.get(id)??before.get(id))?.projectId;
      return (projectId===null||project===projectId)&&(!filter.current||filter.current(records.get(id),before.get(id)));
    };
    const fire=()=>{timer=null;at=Date.now();const ids=[...pending];pending.clear();if(ids.length)latest.current(ids);};
    const off=current.subscribe(()=>{
      const next=current.get(),before=last;last=next;
      // The store's first read isn't a change: each list just loaded its own rows (third review, Low 4).
      if(before.version===0)return;
      for(const [id,record] of next.records)if(before.records.get(id)?.version!==record.version&&mine(id,next.records,before.records))pending.add(id);
      for(const id of before.records.keys())if(!next.records.has(id)&&mine(id,next.records,before.records))pending.add(id);
      if(!pending.size||timer)return;
      const wait=CHANGES_MS-(Date.now()-at);
      if(wait<=0)fire();else timer=setTimeout(fire,wait);
    });
    return()=>{off();if(timer)clearTimeout(timer);};
  },[projectId]);
}
