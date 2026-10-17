import type {LocalEvent,HarnessRequest,QueuedRequest} from './api';
import type {CodingTask} from './CodingTasks';
import type {ConversationStatus} from './TurnControls';
import {t} from '@/i18n';

/** 16.4: the feed carries the thread's newest page. Rows before ``before`` come from conversation_older. */
export type FeedPage={before:number|null;has_older:boolean};
export type ConversationSnapshot={activity:ConversationStatus;history:LocalEvent[];tasks:CodingTask[];requests:HarnessRequest[];queue?:QueuedRequest[];page?:FeedPage};
export type FeedState={cursor:string;snapshot:ConversationSnapshot;active:boolean};
type Patch={op:'set';path:(string|number)[];value:unknown}|{op:'remove';path:(string|number)[]}|{op:'append';path:(string|number)[];length:number;text:string};
export type FeedResponse={cursor:string;base:string|null;active:boolean}&({reset:true;snapshot:ConversationSnapshot}|{reset:false;patches:Patch[]});
const forbidden=new Set(['__proto__','prototype','constructor']);
const validCursor=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
const reject=()=>{throw new Error(t('Conversation updates lost their place. Reload this conversation.'));};
export const validPage=(value:unknown):value is FeedPage=>!!value&&typeof value==='object'&&!Array.isArray(value)&&typeof (value as FeedPage).has_older==='boolean'&&((value as FeedPage).before===null||typeof (value as FeedPage).before==='number'&&Number.isFinite((value as FeedPage).before));
function snapshot(value:unknown):ConversationSnapshot{
  if(!value||typeof value!=='object'||Array.isArray(value))return reject();
  const item=value as ConversationSnapshot;
  if(!item.activity||typeof item.activity!=='object'||Array.isArray(item.activity)||typeof item.activity.state!=='string'||typeof item.activity.active!=='boolean'||!Array.isArray(item.history)||!Array.isArray(item.tasks)||!Array.isArray(item.requests))return reject();
  // Nested evidence uses the existing trusted host schema. Reject broken collection records here.
  if(item.queue!==undefined&&!Array.isArray(item.queue))return reject();
  if(item.page!==undefined&&!validPage(item.page))return reject();
  for(const collection of [item.history,item.tasks,item.requests,item.queue??[]])if(collection.some(row=>!row||typeof row!=='object'||Array.isArray(row)||typeof row.id!=='string'))return reject();
  return item;
}
function change(value:unknown,patch:Patch,depth=0):unknown{
  if(depth===patch.path.length){
    if(patch.op==='set')return patch.value;
    if(patch.op==='append'&&typeof value==='string'&&Number.isSafeInteger(patch.length)&&value.length===patch.length&&typeof patch.text==='string')return value+patch.text;
    return reject();
  }
  const key=patch.path[depth];
  if(!value||typeof value!=='object')return reject();
  if(Array.isArray(value)){
    if(typeof key!=='number'||!Number.isSafeInteger(key)||key<0||key>=value.length||patch.op==='remove'&&depth===patch.path.length-1)return reject();
    const next=value.slice();next[key]=change(value[key],patch,depth+1);return next;
  }
  if(typeof key!=='string'||forbidden.has(key))return reject();
  const record=value as Record<string,unknown>;
  const exists=Object.hasOwn(record,key);
  if(!exists&&!(patch.op==='set'&&depth===patch.path.length-1))return reject();
  const next={...record};
  if(patch.op==='remove'&&depth===patch.path.length-1)delete next[key];
  else next[key]=change(record[key],patch,depth+1);
  return next;
}
/** One immutable snapshot owner. A stale response can't overwrite a newer cursor. */
export function applyConversationFeed(previous:FeedState|undefined,response:FeedResponse):FeedState{
  if(!validCursor(response.cursor)||typeof response.active!=='boolean'||typeof response.reset!=='boolean'||response.base!==(previous?.cursor??null))return reject();
  if(response.reset)return {cursor:response.cursor,snapshot:snapshot(response.snapshot),active:response.active};
  if(!previous||!Array.isArray(response.patches)||response.patches.length>2048)return reject();
  if(response.patches.length===0&&response.cursor===previous.cursor&&response.active===previous.active)return previous;
  let next:unknown=previous.snapshot;
  for(const patch of response.patches){
    if(!patch||!Array.isArray(patch.path)||patch.path.length>32||!['set','remove','append'].includes(patch.op))return reject();
    next=change(next,patch);
  }
  return {cursor:response.cursor,snapshot:snapshot(next),active:response.active};
}

/** Older pages the reader scrolled to (16.4 item 1). They cover every row from ``before`` up to ``top``, the newest
 * page's start when they were last joined to it, with no gap: rows that slide out of the newest page move in here. */
export type OlderPages={history:LocalEvent[];tasks:CodingTask[];before:number|null;hasOlder:boolean;top:number|null};
export type OlderPage={history:LocalEvent[];tasks:CodingTask[];before:number|null;has_older:boolean};
/** Where a row sorts (timelineRows.orderRows, the host's conversation_feed.stamp): a message by its time, a task by
 * its first message's. */
export function rowTime(row:{created_at?:number;messages?:{created_at?:number}[]}):number{
  const at=row.messages?row.messages[0]?.created_at:row.created_at;
  return typeof at==='number'&&Number.isFinite(at)&&at>=0?at:0;
}
function freshPages(snapshot:ConversationSnapshot):OlderPages{
  const cut=snapshot.page?.before??null;
  return {history:[],tasks:[],before:cut,hasOlder:cut!==null&&!!snapshot.page?.has_older,top:cut};
}
/** The older pages after a new snapshot. ``previous`` is the snapshot it replaced in the same unbroken read; without
 * one (a first read, or a read after a failure) loaded pages are dropped and load again on scroll, since tasks in them
 * may have changed unseen. */
export function carryOlder(older:OlderPages|undefined,previous:ConversationSnapshot|undefined,next:ConversationSnapshot):OlderPages{
  const made=freshPages(next),cut=made.top;
  // Nothing loaded and the same position keeps the same object, so an unchanged read re-renders nothing.
  const fresh=older&&!older.history.length&&!older.tasks.length&&older.before===made.before&&older.hasOlder===made.hasOlder&&older.top===made.top?older:made;
  if(!previous||cut===null)return fresh;
  const base=older??freshPages(previous);
  if(base.top===null)return fresh;
  // A task the snapshot carries is its newest copy.
  const listed=new Set(next.tasks.map(task=>task.id));
  const tasks=base.tasks.filter(task=>!listed.has(task.id));
  if(cut===base.top)return tasks.length===base.tasks.length?base:{...base,tasks};
  if(cut<base.top){
    // The newest page reaches further back now: keep only what it doesn't carry.
    if(base.before===null||base.before>=cut)return fresh;
    return {...base,history:base.history.filter(row=>rowTime(row)<cut),tasks:tasks.filter(task=>rowTime(task)<cut),top:cut};
  }
  // The newest page starts later: the rows between move here, but only if the previous snapshot held all of them.
  if(previous.page?.before!==base.top)return fresh;
  const top=base.top,between=(row:{created_at?:number;messages?:{created_at?:number}[]})=>{const at=rowTime(row);return at>=top&&at<cut;};
  return {history:[...base.history,...previous.history.filter(row=>row.state!=='streaming'&&between(row))],
    tasks:[...tasks,...previous.tasks.filter(task=>between(task)&&!listed.has(task.id))],before:base.before,hasOlder:base.hasOlder,top:cut};
}
/** One more page, if it's the one just before what's loaded. A late page for an older position is ignored. */
export function addOlderPage(older:OlderPages,before:number,page:OlderPage):OlderPages{
  if(older.before!==before)return older;
  if(!Array.isArray(page.history)||!Array.isArray(page.tasks)||typeof page.has_older!=='boolean'||page.before!==null&&(typeof page.before!=='number'||page.before>=before))return reject();
  const seenRows=new Set(older.history.map(row=>row.id)),seenTasks=new Set(older.tasks.map(task=>task.id));
  return {...older,history:[...page.history.filter(row=>!seenRows.has(row.id)),...older.history],
    tasks:[...page.tasks.filter(task=>!seenTasks.has(task.id)),...older.tasks],before:page.before??before,hasOlder:page.has_older&&page.before!==null};
}
/** What the thread shows: older pages first, then the snapshot. The snapshot's copy of a row wins. */
export function withOlder(snapshot:ConversationSnapshot,older:OlderPages|undefined):{history:LocalEvent[];tasks:CodingTask[]}{
  if(!older||!older.history.length&&!older.tasks.length)return {history:snapshot.history,tasks:snapshot.tasks};
  const rows=new Set(snapshot.history.map(row=>row.id)),tasks=new Set(snapshot.tasks.map(task=>task.id));
  const merged=[...older.tasks.filter(task=>!tasks.has(task.id)),...snapshot.tasks];
  // Creation order, which the thread's readers (newest task, guide) expect; open tasks from older pages sit in the snapshot.
  const ordered=merged.map((task,index)=>({task,index})).sort((a,b)=>rowTime(a.task)-rowTime(b.task)||a.index-b.index).map(({task})=>task);
  return {history:[...older.history.filter(row=>!rows.has(row.id)),...snapshot.history],tasks:ordered};
}
