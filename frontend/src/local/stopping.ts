import {useSyncExternalStore} from 'react';

/** Stop pressed, run still ending (run-lifecycle map, section 5: "Stopping. It'll finish the step it's on.").
 *
 * The host doesn't record a stopping state on the task (coding_integration ignores the adapter's 'stopping' event), and a
 * second Stop while the first is honoured used to turn a clean stop into "outcome not confirmed". So the app remembers the
 * Stops it sent, by task id and the run it was for, for as long as that run still reads as working. Every Stop control reads the same memory:
 * the Progress panel's button, the composer's Stop and Esc. It's per launch, which is right: a relaunch ends the run.
 */
const asked=new Map<string,number|null>();   // task id → how many runs it had when the Stop was sent; null: not known
const listeners=new Set<()=>void>();
let version=0;
const changed=()=>{version++;for(const listener of listeners)listener();};

type Stoppable={id:string;state?:string;runs?:unknown[]};
export function markStopping(tasks:(Stoppable|null|undefined)[]){
 let added=false;
 // Keyed to the run: a Pause after a Resume is a new Pause (reviewer, 8 Oct), not one already recorded.
 for(const task of tasks){
  if(!task)continue;
  const runs=task.runs?task.runs.length:null;
  if(asked.get(task.id)!==runs){asked.set(task.id,runs);added=true;}
 }
 if(added)changed();
}
export function forgetStopping(id:string){if(asked.delete(id))changed();}

/** Whether this task is ending because you asked: a Stop was sent and it still works, or the host says so itself. */
export function isStopping(task:Stoppable|null|undefined):boolean{
 if(!task)return false;
 const hostSays=(task.runs?.at(-1) as {state?:string}|undefined)?.state==='stopping';
 // Once that run has ended, the Stop is spent. Resume works on the same task id with a new run, and a remembered Stop
 // then read the resumed run as "Pausing…" for good, with every Stop control disabled (polisher, 8 Oct). Counting runs
 // catches it even when nothing rendered the task between the Pause and the Resume (paused from Home, resumed from
 // catch-up).
 let at=asked.get(task.id);
 // A Pause marked without its task row (null) learns its run on the first read: the run that's working now. Otherwise
 // a resumed run would read as Pausing… throughout (review of #19). Setting it while rendering is safe: it's the same
 // answer on every render and nothing re-renders from it.
 if(at===null&&task.state==='working'&&task.runs){at=task.runs.length;asked.set(task.id,at);}
 if(task.state!=='working'||at===undefined||(at!==null&&(task.runs?.length??0)>at))return task.state==='working'&&hostSays;
 return true;
}

/** Re-render when a Stop is sent anywhere in the app. */
export function useStoppingVersion():number{
 return useSyncExternalStore(listener=>{listeners.add(listener);return ()=>{listeners.delete(listener);};},()=>version,()=>version);
}
