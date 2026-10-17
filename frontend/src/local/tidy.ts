import {local,type Conversation} from './api';
import {formatDate,t} from '@/i18n';

/** Quiet threads tidy themselves (beta.16, b16/lifecycle; Bryce, 25 Sep: archived after 14 days, never deleted). The
 * host decides what's quiet (project_lifecycle.quiet_threads): nobody opened it or wrote in it for the project's days,
 * nothing runs or waits on you, no unreviewed or uncommitted changes, not pinned. The sidebar asks once per project when
 * the app opens, never while you're looking, and says what went in one line with Undo and See them.
 *
 * These operations reach the host through project_lifecycle's own list. The shell's bridge (bridge.rs) still has to
 * name them (asked of b16/bridge); until it does, each call is refused and the sidebar simply doesn't tidy. */

export type TidiedThread={id:string;name:string;quiet_since:number};
/** The project's setting: 7, 14 or 30 days, Smart, or null for Never. */
export type TidyDays=number|null|'smart';
/** ``days`` is the one the tidy used: 0 for a Smart tidy. */
export type TidyResult={batch:string|null;at:number|null;count:number;days:number|null;threads:TidiedThread[]};
export type TidySetting={days:TidyDays;default:number;choices:number[]};
/** An archived thread, with the tidy that put it away when Dervo did. */
export type ArchivedThread=Conversation&{archived_at:number;tidy_batch?:string|null;tidied_at?:number|null;quiet_since?:number|null};

export const TIDY_DEFAULT=14;
export const TIDY_CHOICES=[7,14,30] as const;
/** Smart (beta.16.1 item 7, Bryce 26 Sep): a thread whose work is settled goes once nobody has opened it for a day, and
 * any other quiet thread after 7 days. The host decides which is which (project_lifecycle.settled). */
export const TIDY_SMART='smart';

const asTidy=(value:unknown):TidyResult|null=>value&&typeof value==='object'&&'count' in value&&Array.isArray((value as TidyResult).threads)?value as TidyResult:null;

/** Projects tidied in this launch of the app, so switching back to one never tidies it again while you're here. */
const tidiedThisLaunch=new Map<string,TidyResult|null>();
/** ``keep``: the thread on screen as the app opens (the last one you had open), which is never put away under you. */
export function tidyOnce(projectId:string,keep:string[]=[]):Promise<TidyResult|null>{
 if(tidiedThisLaunch.has(projectId))return Promise.resolve(tidiedThisLaunch.get(projectId)??null);
 tidiedThisLaunch.set(projectId,null);
 return local<unknown>('conversations_tidy',{projectId,keep}).then(value=>{const found=asTidy(value);tidiedThisLaunch.set(projectId,found);return found;},()=>null);
}
/** Undo's answer, and the launch record forgets the batch so the line doesn't come back. */
export async function undoTidy(projectId:string,batch:string):Promise<{ids:string[]}>{
 const result=await local<{ids:string[]}>('conversations_tidy_undo',{projectId,batch});
 const seen=tidiedThisLaunch.get(projectId);
 if(seen?.batch===batch)tidiedThisLaunch.set(projectId,{...seen,batch:null,count:0,threads:[]});
 return result;
}
export const tidiedToday=(projectId:string)=>local<TidyResult&{batch:string}|null>('conversations_tidied',{projectId});
export const pinnedThreads=(projectId:string)=>local<string[]>('conversation_pins',{projectId});
export const pinThread=(projectId:string,channelId:string,pinned:boolean)=>local<{id:string;pinned:boolean}>('conversation_pin',{projectId,channelId,pinned});
export const tidySetting=(projectId:string)=>local<TidySetting>('project_tidy_setting',{projectId});
export const setTidySetting=(projectId:string,days:TidyDays)=>local<TidySetting>('project_tidy_set',{projectId,days});
/** Only for tests: forget which projects were tidied. */
export function forgetTidies(){tidiedThisLaunch.clear();}

/** "Tidied 4 quiet threads", or nothing. */
export function tidiedLine(count:number):string{
 return count>0?t('Tidied {count} quiet threads',{count}):'';
}
/** Home's one line on the day it happens (Bryce's pick, 25 Sep): "Put away 4 threads you hadn't touched in two weeks." */
export function homeTidyLine(count:number,days:TidyDays=TIDY_DEFAULT):string{
 if(count<=0)return '';
 // A Smart tidy (recorded as 0 days) took finished threads after a day and other quiet ones after a week.
 if(days===0||days===TIDY_SMART)return t('Put away {count} finished or quiet threads.',{count});
 const span=days===7?t('a week'):days===30?t('a month'):days===14||days===null?t('two weeks'):t('{days} days',{days});
 return t('Put away {count} threads you hadn’t touched in {span}.',{count,span});
}
/** How long a tidied thread had been quiet, short: "15 d". */
export function quietFor(since:number|null|undefined,now=Date.now()):string{
 if(typeof since!=='number'||since<1e9)return '';
 return `${Math.max(1,Math.floor((now-since*1000)/86400000))} d`;
}
/** "Tidied today", "Tidied yesterday" or "Tidied 20 Sep". */
export function tidiedWhen(at:number|null|undefined,now=Date.now()):string{
 if(typeof at!=='number'||at<1e9)return t('Tidied');
 const when=new Date(at*1000),today=new Date(now);
 if(when.toDateString()===today.toDateString())return t('Tidied today');
 if(when.toDateString()===new Date(now-86400000).toDateString())return t('Tidied yesterday');
 return t('Tidied {date}',{date:formatDate(when,{day:'numeric',month:'short'})});
}
/** The setting as the project menu shows it: "14 days", "Smart" or "Never". */
export function tidyLabel(days:TidyDays):string{return days===null?t('Never'):days===TIDY_SMART?t('Smart'):t('{days} days',{days});}

/** The fold groups by why (design b16-lifecycle, archived-open): each tidy Dervo did, newest first, then what you
 * archived yourself. */
export function archivedGroups(rows:ArchivedThread[],days:TidyDays,now=Date.now()):{key:string;title:string;rows:ArchivedThread[];tidied:boolean}[]{
 const groups:{key:string;title:string;rows:ArchivedThread[];tidied:boolean}[]=[];
 const quiet=days===null?'':days===TIDY_SMART?t(', finished or quiet'):t(', quiet {days} days or more',{days});
 for(const row of rows.filter(row=>row.tidy_batch)){
  const key=`tidy:${row.tidy_batch}`;
  let group=groups.find(g=>g.key===key);
  if(!group){group={key,title:`${tidiedWhen(row.tidied_at,now)}${quiet}`,rows:[],tidied:true};groups.push(group);}
  group.rows.push(row);
 }
 groups.sort((a,b)=>(b.rows[0].tidied_at??0)-(a.rows[0].tidied_at??0));
 // Least quiet first, as the mock draws it: 15 d, 15 d, 16 d, 19 d.
 for(const group of groups)group.rows.sort((a,b)=>(b.quiet_since??0)-(a.quiet_since??0)||a.name.localeCompare(b.name));
 const mine=rows.filter(row=>!row.tidy_batch);
 if(mine.length)groups.push({key:'mine',title:t('Archived by you'),rows:mine,tidied:false});
 return groups;
}

/** A size, as the fold says what a delete freed: "480 KB", "3.4 MB", "1.2 GB". Decimal units, as Finder counts. */
export function sizeLabel(bytes:number):string{
 if(!(bytes>0))return '0 KB';
 if(bytes<1e6)return `${Math.max(1,Math.round(bytes/1e3))} KB`;
 if(bytes<1e9)return `${(bytes/1e6).toFixed(bytes<1e7?1:0)} MB`;
 return `${(bytes/1e9).toFixed(1)} GB`;
}
