import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import {local} from './api';
import {t} from '@/i18n';

/** A13 and A14. What the host's session summary says: results you haven't opened and conversations waiting on you. */
export type SummaryItem={thread_id:string;project_id:string;name:string;project:string|null;count:number;changed:number};
export type SessionSummary={since:number|null;finished:SummaryItem[];needs:SummaryItem[]};
export type NotificationPrefs={finished:boolean;needs:boolean;backgroundOnly:boolean};
/** ``covers`` names what else a note speaks for (the good-morning note's items), so none of it is sent again. */
export type Note={id:string;title:string;body:string;projectId:string;channelId:string;covers?:string[]};
export type OnScreen={focused:boolean;projectId:string;channelId:string};
/** A thread's activity as the conversations list reads it: how its newest task ended and the sentence about it. */
export type ThreadActivity={ended?:string|null;summary?:string|null};
/** What the watcher knows beyond the summary: each thread's activity, and the threads Home sent a job to (thread id to
 * when, in seconds). */
export type NoteContext={activity?:Record<string,ThreadActivity>;fromHome?:Record<string,number>};
/** A run the host before this one lost when it closed (status.lostRuns, fix-lifecycle). */
export type LostRun={taskId:string;channelId:string;projectId:string;name:string|null;/** Beta.18: settled as paused from its own record. */paused?:boolean};
export type PushRecord={id?:string;state:string;detail?:string|null;branch?:string|null;remote?:string|null};

/** Ends that aren't a result to read: your Pause, Leave it stopped, and a pause Dervo settled after a restart (beta.18). */
const STOPPED_ENDS=new Set(['stopped','left','restart_paused']);
export const DEFAULT_PREFS:NotificationPrefs={finished:true,needs:true,backgroundOnly:true};
const PREFS_KEY='dervo-notifications';
export const OPEN_EVENT='dervo-notification-open';
/** The channel a note carries when a click should land on the project's Home rather than a thread. No thread id starts
 * with a colon. */
export const HOME_CHANNEL=':home';

export function loadPrefs():NotificationPrefs{
  try{const saved=JSON.parse(localStorage.getItem(PREFS_KEY)??'null');return {...DEFAULT_PREFS,...(saved&&typeof saved==='object'?saved:{})};}
  catch{return DEFAULT_PREFS;}
}
export function savePrefs(prefs:NotificationPrefs){try{localStorage.setItem(PREFS_KEY,JSON.stringify(prefs));}catch{/* The toggles still apply this session. */}}

/** Small per-Mac memories, kept a few days and capped, that never break the watcher when storage is missing. */
function readMap(key:string,days:number):Record<string,number>{
  try{
    const saved=JSON.parse(localStorage.getItem(key)??'{}');
    if(!saved||typeof saved!=='object')return {};
    const cutoff=Date.now()/1000-days*86400;
    return Object.fromEntries(Object.entries(saved).filter(([,at])=>typeof at==='number'&&at>=cutoff)) as Record<string,number>;
  }catch{return {};}
}
function writeMap(key:string,map:Record<string,number>,cap:number){
  try{localStorage.setItem(key,JSON.stringify(Object.fromEntries(Object.entries(map).sort((a,b)=>b[1]-a[1]).slice(0,cap))));}catch{/* Only this launch remembers. */}
}
const POSTED_KEY='dervo-notified',HOME_KEY='dervo-home-jobs';
/** Every note id this Mac posted in the last week, so a relaunch or a reload never sends the same note twice. */
export const loadPosted=()=>readMap(POSTED_KEY,7);
export function rememberPosted(note:Note,now=Date.now()/1000){
  const posted=loadPosted();
  for(const id of [note.id,...note.covers??[]])posted[id]=now;
  writeMap(POSTED_KEY,posted,400);
}
/** Whether a note would say anything new: its id, or something it covers, hasn't been posted. */
export function isNews(note:Note,posted:Record<string,number>):boolean{
  return !(note.id in posted)||(note.covers??[]).some(id=>!(id in posted));
}
/** Home sent a job to this thread (#2: Home jobs start on their own). Its next note says so. */
export function rememberHomeJob(channelId:string,now=Date.now()/1000){const jobs=readMap(HOME_KEY,3);jobs[channelId]=now;writeMap(HOME_KEY,jobs,100);}
export const loadHomeJobs=()=>readMap(HOME_KEY,3);
export function forgetHomeJob(channelId:string){const jobs=readMap(HOME_KEY,3);if(channelId in jobs){delete jobs[channelId];writeMap(HOME_KEY,jobs,100);}}

/** A short, stable id for a list of keys: macOS ids stay under the native side's 200 characters however many items a
 * note covers. */
export function digest(keys:string[]):string{
  let a=5381,b=52711;
  for(const ch of keys.slice().sort().join('|')){const c=ch.charCodeAt(0);a=(a*33)^c;b=(b*31)^c;a|=0;b|=0;}
  return `${(a>>>0).toString(36)}${(b>>>0).toString(36)}`;
}
/** "Home started this in dervo app.": a job Home sent, said with its project when the summary names one. */
const homeStarted=(item:SummaryItem)=>item.project?t('Home started this in {project}.',{project:item.project}):t('Home started this.');
const key=(kind:'needs'|'finished',item:SummaryItem)=>`${kind}:${item.thread_id}:${item.changed}`;
/** The connector a failed run names (claude_coding.CONNECTOR_DROPPED, #9), from the thread's summary. */
export function droppedConnector(activity?:ThreadActivity):string|null{
  const found=/The (.{1,60}?) connector dropped/.exec(activity?.summary??'');
  return found?found[1]:null;
}
/** New since the previous summary: a conversation that wasn't listed, or whose latest change is newer. Nothing for the
 * conversation you're looking at, nothing a preference turned off, and nothing at all while Dervo is in front when
 * notifications are for the background only. Nothing for a run you stopped yourself: it ended because you asked. A job
 * Home sent says it came from Home, and a run a connector dropped names the connector. The first summary of a session
 * sends nothing here; see goodMorning. */
export function notificationsToSend(previous:SessionSummary|null,next:SessionSummary,prefs:NotificationPrefs,screen:OnScreen,context:NoteContext={}):Note[]{
  if(!previous||(prefs.backgroundOnly&&screen.focused))return [];
  const seen=(list:SummaryItem[],item:SummaryItem)=>list.some(old=>old.thread_id===item.thread_id&&old.changed>=item.changed);
  const onScreen=(item:SummaryItem)=>screen.focused&&item.project_id===screen.projectId&&item.thread_id===screen.channelId;
  const activity=(item:SummaryItem)=>context.activity?.[item.thread_id];
  // A few seconds' slack: Home records the send just before the host stamps the task.
  const home=(item:SummaryItem)=>{const at=context.fromHome?.[item.thread_id];return at!==undefined&&item.changed>=at-5;};
  const notes:Note[]=[];
  if(prefs.needs)for(const item of next.needs)if(!seen(previous.needs,item)&&!onScreen(item)){
    const connector=droppedConnector(activity(item));
    const why=connector?t('The {connector} connector dropped, so it couldn’t finish.',{connector}):null;
    const body=home(item)?`${homeStarted(item)} ${why??t('It’s waiting for your decision.')}`:why??(item.project?t('A task in {project} is waiting for your decision.',{project:item.project}):t('A task is waiting for your decision.'));
    notes.push({id:key('needs',item),title:t('#{name} needs you',{name:item.name}),body,projectId:item.project_id,channelId:item.thread_id});
  }
  if(prefs.finished)for(const item of next.finished)if(!seen(previous.finished,item)&&!onScreen(item)&&!STOPPED_ENDS.has(activity(item)?.ended??'')){
    const ready=t('{count} results are ready to read.',{count:item.count});
    const body=home(item)?`${homeStarted(item)} ${ready}`:item.project?t('{count} results in {project} are ready to read.',{count:item.count,project:item.project}):ready;
    notes.push({id:key('finished',item),title:t('#{name} finished',{name:item.name}),body,projectId:item.project_id,channelId:item.thread_id});
  }
  return notes;
}

/** A14: one note when Dervo opens with results you haven't read or conversations waiting on you. It opens what needs you
 * first, otherwise the newest result. When the last Dervo closed with runs going, that's the note: one for all of them,
 * opening Home, where the Now pane says which. */
export function goodMorning(summary:SessionSummary,prefs:NotificationPrefs,lost:LostRun[]=[]):Note|null{
  const lostRuns=prefs.needs?lost:[],lostThreads=new Set(lostRuns.map(run=>run.channelId));
  const needs=prefs.needs?summary.needs.filter(item=>!lostThreads.has(item.thread_id)):[],finished=prefs.finished?summary.finished:[];
  const covers=[...needs.map(item=>key('needs',item)),...finished.map(item=>key('finished',item)),
    ...summary.needs.filter(item=>lostThreads.has(item.thread_id)).map(item=>key('needs',item))];
  const parts=(more:boolean)=>[...(finished.length?[t('{count} conversations have results you haven’t read',{count:finished.length})]:[]),
    ...(needs.length?[more?t('{count} more need you',{count:needs.length}):t('{count} need you',{count:needs.length})]:[])];
  const joined=(items:string[])=>items.join(`, ${t('and')} `);
  // Beta.18: runs Dervo settled as paused from the provider's own record aren't lost. Said as paused when nothing else was.
  if(lostRuns.length&&lostRuns.every(run=>run.paused)){
    const one=lostRuns.length===1,also=parts(true);
    return {id:`lost:${digest(lostRuns.map(run=>run.taskId))}`,covers,
      title:one?(lostRuns[0].name?t('#{name} paused when Dervo restarted',{name:lostRuns[0].name}):t('A run paused when Dervo restarted')):t('{length} runs paused when Dervo restarted',{length:lostRuns.length}),
      body:`${one?t('It was between steps. Continue picks it up.'):t('They were between steps. Continue picks each one up.')}${also.length?` ${t('Also, {items}.',{items:joined(also)})}`:''}`,
      projectId:lostRuns[0].projectId,channelId:HOME_CHANNEL};
  }
  const unsure=lostRuns.filter(run=>!run.paused);
  if(unsure.length){
    const lostRuns=unsure,one=lostRuns.length===1,also=parts(true);
    return {id:`lost:${digest(lostRuns.map(run=>run.taskId))}`,covers,
      title:one?(lostRuns[0].name?t('#{name} stopped when Dervo closed',{name:lostRuns[0].name}):t('A run stopped when Dervo closed')):t('{length} runs stopped when Dervo closed',{length:lostRuns.length}),
      body:`${one?t('Dervo can’t tell how far it got. Check it on Home.'):t('Dervo can’t tell how far they got. Check them on Home.')}${also.length?` ${t('Also, {items}.',{items:joined(also)})}`:''}`,
      projectId:lostRuns[0].projectId,channelId:HOME_CHANNEL};
  }
  if(!needs.length&&!finished.length)return null;
  const first=needs[0]??finished[0];
  return {id:`good-morning:${digest(covers)}`,covers,title:summary.since?t('While you were away'):t('Waiting in Dervo'),body:`${joined(parts(false))}.`,projectId:first.project_id,channelId:first.thread_id};
}

/** A push you started that ended failed or unknown while Dervo was behind other windows: one note, opening Home where
 * the push line and Check GitHub are. ``before`` is the last record this launch saw for the project (undefined when it
 * hasn't looked yet, so a result from before this launch never notifies). Nothing while Dervo is in front: you're
 * watching the push line. */
export function pushNote(projectId:string,before:PushRecord|null|undefined,after:PushRecord|null,prefs:NotificationPrefs,focused:boolean):Note|null{
  if(before===undefined||!after||focused||!prefs.needs||(after.state!=='failed'&&after.state!=='unknown'))return null;
  if(before&&before.id===after.id&&before.state!=='running')return null;
  const target=after.remote&&after.branch?`${after.remote}/${after.branch}`:'';
  const failed=after.state==='failed';
  return {id:`push:${after.id??''}:${after.state}`,projectId,channelId:HOME_CHANNEL,
    title:failed?(target?t('Push to {target} failed',{target}):t('Push failed')):(target?t('Push to {target} may not have landed',{target}):t('Push may not have landed')),
    body:failed?(after.detail||t('Git didn’t push.')):`${after.detail||t('Dervo lost track of this push.')} ${t('Choose Check GitHub on Home.')}`};
}

export const readSummary=()=>local<SessionSummary>('session_summary');
export const notificationPermission=(request:boolean)=>invoke<string>('notification_permission',{request});
/** System Settings at Notifications, when macOS has them turned off for Dervo. A fixed destination in the native app. */
export const openNotificationSettings=()=>invoke<void>('notification_settings');
export const postNotification=(note:Note)=>invoke<boolean>('notification_post',{id:note.id,title:note.title,body:note.body,projectId:note.projectId,channelId:note.channelId});
export const setDockBadge=(count:number)=>invoke<void>('dock_badge',{count});
export const onNotificationOpen=(handler:(target:{projectId:string;channelId:string})=>void)=>listen<{projectId:string;channelId:string}>(OPEN_EVENT,event=>handler(event.payload));
