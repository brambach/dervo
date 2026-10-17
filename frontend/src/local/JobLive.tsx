import {useEffect,useState} from 'react';
import type {ConversationActivity} from './api';
import {t} from '@/i18n';

/** Live job lines (chat-first report, 23 Sep, #1). A job Home started used to say "started" for as long as it ran, so the
 * chat you launched from was the one place that never showed progress. Now, while its thread runs, the line under the
 * job's name is the run's current step from the activity index (the agent's own step when it reported one, otherwise the
 * tool it's in) and how long it's been going; once it ends, the record's one-line summary. Both come from the
 * conversation list Home already polls, so nothing new is fetched and nothing is guessed. */
export type LiveJob={kind:'running';step:string;since:number|null;next:string|null}|{kind:'ended';summary:string};

export function liveJob(part:{state?:string|null},activity:ConversationActivity|null|undefined,sentAt:number):LiveJob|null{
 if(part.state!=='sent'||!activity)return null;
 if(activity.running)return {kind:'running',step:activity.step?.trim()||t('Working'),since:activity.since??null,next:activity.next?.trim()||null};
 // Only a summary written after this job was sent: an older one belongs to an earlier run in the same thread.
 if(activity.summary&&activity.at&&activity.at>sentAt)return {kind:'ended',summary:activity.summary};
 return null;
}

/** How long a run has gone, to the minute: the line is glanced at, not watched. */
export function ranFor(seconds:number):string{
 const whole=Math.max(0,Math.floor(seconds));
 if(whole<60)return t('under a minute');
 const minutes=Math.floor(whole/60);
 if(minutes<60)return t('{minutes} min',{minutes});
 const hours=Math.floor(minutes/60),rest=minutes%60;
 return rest?t('{hours} h {rest} min',{hours,rest}):`${hours} h`;
}

/** The clock beside a running job's step. It ticks by itself, so it stays right when the step doesn't change. */
export function JobClock({since}:{since:number}){
 const [now,setNow]=useState(()=>Date.now()/1000);
 useEffect(()=>{setNow(Date.now()/1000);const timer=setInterval(()=>setNow(Date.now()/1000),10000);return ()=>clearInterval(timer);},[since]);
 return <span className="tnum">{ranFor(now-since)}</span>;
}

/** The small line itself: step and clock while it runs (the agent's next step, when it said one, on hover), the summary
 * once it's ended, then Home's own words for the job (its workstream) after a middle dot. */
export function LiveJobLine({live,rest}:{live:LiveJob;rest?:string}){
 const tail=rest?` · ${rest}`:'';
 if(live.kind==='ended')return <small data-live-job="ended">{live.summary}{tail}</small>;
 // The step is the long part (a file path, usually), so it's the one that gives way: the clock and the workstream after
 // it stay whole at any width (design review, 23 Sep: at 1052 a path cut the clock off, which is the point of the line).
 // The step's trailing space keeps the words readable to a screen reader; the gap draws it.
 const after=live.since!==null||!!rest;
 return <small data-live-job="running" title={live.next?t('Next: {next}',{next:live.next}):undefined}>
  <span className="dv-job-step">{after?`${live.step} `:live.step}</span>
  {after&&<span className="dv-job-when">{live.since!==null?<>· <JobClock since={live.since}/>{tail}</>:tail.trimStart()}</span>}</small>;
}
