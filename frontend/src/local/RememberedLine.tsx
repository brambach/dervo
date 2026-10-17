import {useEffect,useId,useState,type ReactNode} from 'react';
import {local} from './api';
import type {CodingTask} from './CodingTasks';
import {flagLabel,type MemoryEntry,type NoteFlag} from './ProjectMemory';
import './ActivityLine.css';
import './RememberedLine.css';
import {t} from '@/i18n';

/** The "Remembered" line on a reply (beta.16, b16/memory; one line since beta.17.1, #41 direction A, mock
 * evidence/design-tests/memory-explore-20261005/line-*.png in the mvp1 folder).
 *
 * What an agent kept while it wrote this reply, said where you read the reply, so nothing is kept out of sight: one line
 * of counts ("Kept 2 notes, retired 1 · 1 waits for you") with Show. Up to beta.17 every note was quoted in full under
 * the reply, so three saves took 242 px at 1052 (Bryce, 5 Oct: "chunky"). Show opens one clipped row per thing, what
 * waits for you first, with the same actions as before: Edit and Undo on a note, Keep and Remove on a marked one, Undo
 * on a retired one. The full words are in the row's tooltip and in Knowledge. Only what the run's record says was saved
 * is shown. b16/thread owns the reply and mounts this in the slot under its words. */
export type RememberedItem={key:string;kind:'note'|'lesson'|'flagged'|'skill'|'retired'|'forgot'|'refused'|'withheld'|'noted';
 text:string;entryId?:string;flag?:NoteFlag;previous?:{id:string;text:string};reason?:string;skill?:{id:string;name:string;version:number;replaced?:number}};

/** Why a note was retired, for the reply's row and the Progress panel. */
export function retiredWhy(reason:string|undefined):string{
 if(reason==='merged')return t('another note says the same with newer details');
 if(reason==='out_of_date')return t('it looked out of date');
 if(reason==='expired')return t('it said where something was, over two weeks ago');
 return t('it was the note written longest ago');
}
function withheld(what:unknown):string{
 if(what==='lesson')return t('Didn’t save a lesson: it looked like a key or password.');
 if(what==='skill')return t('Didn’t save a skill: it looked like a key or password.');
 return t('Didn’t save a note: it looked like a key or password.');
}

function record(value:unknown):Record<string,unknown>{return value&&typeof value==='object'?value as Record<string,unknown>:{};}
const str=(value:unknown)=>typeof value==='string'&&value?value:undefined;

/** Every line for one run, in the order they happened. ``runId`` picks the run the reply came from; the newest by default. */
export function rememberedItems(task:Pick<CodingTask,'runs'>,runId?:string):RememberedItem[]{
 const runs=(task.runs??[]).map(record);
 const run=runId?runs.find(item=>item.id===runId)??{}:runs.at(-1)??{};
 const items:RememberedItem[]=[];
 (Array.isArray(run.remembered)?run.remembered:[]).map(record).forEach((item,index)=>{
  const text=str(item.text)??'',id=str(item.entry_id),key=`m${index}`;
  const flag=item.flag&&typeof item.flag==='object'?item.flag as NoteFlag:undefined;
  if(item.action==='withheld'){items.push({key,kind:'withheld',text:withheld(item.what)});return;}
  if(!text)return;
  if(item.action==='dropped'){items.push({key,kind:'retired',text,entryId:id,reason:str(item.reason)??'oldest'});return;}
  if(item.action==='remove'){items.push({key,kind:'forgot',text,entryId:id});return;}
  if(item.action==='refused'){items.push({key,kind:'refused',text,reason:str(item.reason)});return;}
  if(item.action==='noted'){items.push({key,kind:'noted',text});return;}
  if(!['add','replace','supersede'].includes(String(item.action))||!id)return;
  const previous=item.action==='supersede'&&str(item.superseded_id)&&str(item.previous)?{id:String(item.superseded_id),text:String(item.previous)}:undefined;
  items.push({key,kind:flag?'flagged':item.kind==='lesson'?'lesson':'note',text,entryId:id,flag,previous});
 });
 (Array.isArray(run.skills_proposed)?run.skills_proposed:[]).map(record).forEach((item,index)=>{
  // Before beta.16 a skill waited for review; only a skill that applied is a New skill here.
  if(item.applied!==true||!str(item.skill_id)||!str(item.name))return;
  items.push({key:`s${index}`,kind:'skill',text:str(item.description)??String(item.name),
   skill:{id:String(item.skill_id),name:String(item.name),version:Number(item.version)||1,...(typeof item.replaced==='number'?{replaced:item.replaced}:{})}});
 });
 return items;
}

/** The one line: what the run did to memory, in counts, the first naming its noun ("Kept 2 notes, retired 1"), and how
 * many marked notes wait for your Keep. Null for a run that kept nothing. ``waiting`` overrides the record's count once
 * memory has been read (a mark you kept since doesn't wait). */
export function rememberedSummary(items:RememberedItem[],waiting?:number):{text:string;waits:number}|null{
 if(!items.length)return null;
 const count=(test:(item:RememberedItem)=>boolean)=>items.filter(test).length;
 const parts:string[]=[];
 type Say=(count:number)=>string;
 const add=(n:number,first:Say,later:Say)=>{if(n)parts.push(parts.length?later(n):first(n));};
 add(count(item=>['note','lesson','flagged','noted'].includes(item.kind)&&!item.previous),
  n=>t('Kept {count} notes',{count:n}),n=>t('kept {count}',{count:n}));
 add(count(item=>item.kind==='note'&&!!item.previous),n=>t('Updated {count} notes',{count:n}),n=>t('updated {count}',{count:n}));
 add(count(item=>item.kind==='forgot'),n=>t('Forgot {count} notes',{count:n}),n=>t('forgot {count}',{count:n}));
 add(count(item=>item.kind==='retired'),n=>t('Retired {count} notes',{count:n}),n=>t('retired {count}',{count:n}));
 add(count(item=>item.kind==='skill'),n=>t('Saved {count} skills',{count:n}),n=>t('{count} new skills',{count:n}));
 add(count(item=>item.kind==='refused'||item.kind==='withheld'),n=>t('Didn’t save {count} notes',{count:n}),n=>t('didn’t save {count}',{count:n}));
 return {text:parts.join(', '),waits:waiting??count(item=>item.kind==='flagged')};
}

/** Memory as it is now, for one thread's scope: every reply's lines share one read, kept 15 s and dropped after a change
 * made from a line. Null when it couldn't be read, so the lines stay as the record has them. Since beta.17.1 it's the
 * brief read (ids, words and marks), not Knowledge's whole view, and only a reply that's opened or has a note waiting
 * for you asks for it. */
const memoryReads=new Map<string,{at:number;read:Promise<MemoryEntry[]|null>}>();
function activeNotes(scope:Record<string,unknown>):Promise<MemoryEntry[]|null>{
 const key=JSON.stringify(scope),now=Date.now(),cached=memoryReads.get(key);
 if(cached&&now-cached.at<15000)return cached.read;
 const read=local<{entries?:MemoryEntry[]}>('knowledge_notes_list',{...scope,brief:true}).then(view=>Array.isArray(view?.entries)?view.entries:null,()=>null);
 memoryReads.set(key,{at:now,read});
 return read;
}
/** Only for tests: forget the shared reads. */
export function forgetMemoryReads(){memoryReads.clear();}

/** What became of a line's note since its run kept it, from memory as it is now, or null while the line still stands
 * (behaviour review S7, 25 Sep: after Edit or Undo, reopening the thread offered them again, and they then failed).
 * A note that another took the place of says so with the words in use now. */
export function nowState(item:Pick<RememberedItem,'kind'|'entryId'>,entries:MemoryEntry[]|null):{label:string;text?:string}|null{
 if(!entries||!item.entryId)return null;
 const active=entries.find(entry=>entry.id===item.entryId);
 if(item.kind==='note'||item.kind==='lesson'||item.kind==='flagged'){
  if(active)return item.kind==='flagged'&&!active.flag?{label:t('Kept')}:null;
  const replaced=entries.find(entry=>entry.supersedes===item.entryId);
  return replaced?{label:t('Changed since'),text:replaced.text}:{label:t('Not in memory now')};
 }
 if(item.kind==='retired'||item.kind==='forgot')return active?{label:t('Brought back')}:null;
 return null;
}

// Rows open with what waits for you first, then the order the mock gives (Bryce's 5 Oct pick).
const ROW_ORDER=['flagged','note','lesson','skill','forgot','retired','refused','withheld','noted'];
function rowLabel(item:RememberedItem):string{
 switch(item.kind){
  case 'flagged': return t('Waits for you');
  case 'note': return item.previous?t('Updated'):t('Kept');
  case 'lesson': return t('Lesson');
  case 'skill': return t('New skill');
  case 'forgot': return t('Forgot');
  case 'retired': return t('Retired');
  case 'noted': return t('Dervo noted');
  default: return t('Didn’t save');
 }
}

type Props={task:Pick<CodingTask,'id'|'runs'|'binding'>;projectId:string;channelId:string;runId?:string;
 /** The project's own account, which skills belong to; the thread's account if it's the same. */
 projectProfileId?:string;
 /** The thread's account now. Memory is read and changed through it: the host refuses any other account for this thread
  * ("This conversation uses another account", 16.4 item 13), and a run's own record keeps the account it ran on, which
  * is out of date once the thread moves to another account. */
 threadProfileId?:string;
 /** Opens Knowledge, for Edit on a skill. Without it, a skill's line has Undo only. */
 onOpenKnowledge?:()=>void;className?:string};
type Done=Record<string,string>;

export function RememberedLines({task,projectId,channelId,runId,projectProfileId,threadProfileId,onOpenKnowledge,className}:Props){
 const [done,setDone]=useState<Done>({});
 const [texts,setTexts]=useState<Record<string,string>>({});
 const [editing,setEditing]=useState<{key:string;text:string}|null>(null);
 const [busy,setBusy]=useState('');
 const [error,setError]=useState<{key:string;text:string}|null>(null);
 const [open,setOpen]=useState(false);
 const [entries,setEntries]=useState<MemoryEntry[]|null>(null);
 const rowsId=useId();
 const items=rememberedItems(task,runId);
 const profileId=threadProfileId??(task.binding as {profile_id?:string}|undefined)?.profile_id;
 const scope={projectId,channelId,profileId};
 const marked=items.some(item=>item.kind==='flagged');
 const needed=items.some(item=>!!item.entryId)&&(open||marked);
 useEffect(()=>{
  if(!needed)return;
  let live=true;
  void activeNotes({projectId,channelId,profileId}).then(found=>{if(live)setEntries(found);});
  return()=>{live=false;};
 },[projectId,channelId,profileId,needed]);
 if(!items.length)return null;
 async function act(key:string,label:string,fn:()=>Promise<unknown>){
  setBusy(key);setError(null);
  try{
   await fn();memoryReads.delete(JSON.stringify(scope));setDone(current=>({...current,[key]:label}));
   // Read again, so a row this change affects (the older note of a correction chain) says what it is now.
   void activeNotes(scope).then(found=>{if(found)setEntries(found);});
   return true;
  }
  catch(failure){setError({key,text:failure instanceof Error?failure.message:String(failure)});return false;}
  finally{setBusy('');}
 }
 const note=(op:string,args:Record<string,unknown>)=>local(op,{...scope,...args});
 const restore=(id:string)=>note('knowledge_note_restore',{entryId:id});
 async function withdraw(skill:NonNullable<RememberedItem['skill']>){
  const account=projectProfileId??profileId;
  const listing=await local<{skills:{id:string;revision:number}[];archived:{id:string;revision:number}[]}>('skills_list',{projectId,profileId:account});
  const found=[...listing.skills,...listing.archived].find(item=>item.id===skill.id);
  if(!found)throw new Error(t('That skill isn’t in this project any more.'));
  await local('skills_review',{projectId,profileId:account,skillId:skill.id,expectedRevision:found.revision,version:skill.version,decision:'withdraw'});
 }
 function button(label:string,onClick:()=>void,describe?:string){
  return <button type="button" className="dv-activity-action" disabled={!!busy} aria-label={describe} onClick={onClick}>{label}</button>;
 }
 const spent=(item:RememberedItem)=>done[item.key]??nowState(item,entries)?.label;
 function actions(item:RememberedItem):ReactNode{
  const state=spent(item);
  if(state)return <span className="dv-activity-done">{state}</span>;
  const words=texts[item.key]??item.text;
  const edit=item.entryId&&button(t('Edit'),()=>setEditing({key:item.key,text:words}),t('Edit what was remembered: {text}',{text:words}));
  // A chain of corrections undoes newest first: the host refuses the other order, since it would leave two notes that
  // disagree (reviewer, 20 Sep; the Progress panel held this rule up to beta.17).
  const later=item.entryId&&items.find(other=>other.previous?.id===item.entryId&&!done[other.key]);
  if(item.previous&&later)return <>{edit}<span className="dv-activity-done">{t('(undo the later correction first)')}</span></>;
  switch(item.kind){
   case 'note': case 'lesson':
    return <>{edit}{button(t('Undo'),()=>void act(item.key,t('Undone'),()=>item.previous?restore(item.previous.id):note('knowledge_note_remove',{entryId:item.entryId})),t('Undo remembering: {text}',{text:words}))}</>;
   case 'flagged':
    return <>{button(t('Keep'),()=>void act(item.key,t('Kept'),()=>note('knowledge_note_keep',{entryId:item.entryId})),t('Keep: {text}',{text:words}))}
     {button(t('Remove'),()=>void act(item.key,t('Removed'),()=>note('knowledge_note_remove',{entryId:item.entryId})),t('Remove: {text}',{text:words}))}</>;
   case 'skill':
    return <>{onOpenKnowledge&&button(t('Edit'),onOpenKnowledge,t('Edit skill {name} in Knowledge',{name:item.skill!.name}))}
     {button(t('Undo'),()=>void act(item.key,t('Undone'),()=>withdraw(item.skill!)),t('Undo skill {name}',{name:item.skill!.name}))}</>;
   case 'retired': case 'forgot':
    return item.entryId?button(t('Undo'),()=>void act(item.key,t('Undone'),()=>restore(item.entryId!)),t('Bring back: {text}',{text:words})):null;
   default: return null;
  }
 }
 /** The row's words, as the note reads now, and its tooltip: the whole of it, with what the row's label can't say. */
 function words(item:RememberedItem):{shown:string;title:string}{
  const shown=texts[item.key]??(done[item.key]?undefined:nowState(item,entries)?.text)??item.text;
  switch(item.kind){
   case 'note': return {shown,title:item.previous?t('{text} In place of: {previous}',{text:shown,previous:item.previous.text}):shown};
   case 'flagged': return {shown,title:t('{mark}. Agents don’t get it until you keep it. {text}',{mark:flagLabel(item.flag),text:shown})};
   case 'retired': return {shown,title:t('{text} Retired because {why}. It’s in Knowledge.',{text:shown,why:retiredWhy(item.reason)})};
   case 'skill': return {shown,title:t('{text} Later chats about this job get it.',{text:shown})};
   case 'refused': return {shown,title:item.reason?`${shown} ${item.reason}`:shown};
   default: return {shown,title:shown};
  }
 }
 const waiting=entries?items.filter(item=>item.kind==='flagged'&&!spent(item)).length:undefined;
 const summary=rememberedSummary(items,waiting)!;
 const rows=[...items].sort((a,b)=>ROW_ORDER.indexOf(a.kind)-ROW_ORDER.indexOf(b.kind));
 return <div className={className?`dv-rem ${className}`:'dv-rem'} data-testid="remembered-lines">
  <p className="dv-activity dv-rem-line">
   <span className="dv-activity-text"><span className="dv-rem-tag">{summary.text}</span>
    {summary.waits>0&&<><span className="dv-rem-sep"> · </span><span className="dv-rem-waits">{t('{count} wait for you',{count:summary.waits})}</span></>}
    <span className="dv-rem-acts"><button type="button" className="dv-activity-action" aria-expanded={open} aria-controls={rowsId}
     onClick={()=>setOpen(value=>!value)}>{open?t('Hide'):t('Show')}</button></span></span>
  </p>
  {open&&<ul className="dv-rem-rows" id={rowsId} aria-label={t('What this reply kept')}>
   {rows.map(item=>{
    if(editing?.key===item.key)return <li key={item.key}><form className="dv-rem-edit" aria-label={t('Edit what was remembered')} onSubmit={event=>{
     event.preventDefault();const text=editing.text.trim();if(!text||busy)return;
     void act(item.key,t('Edited'),()=>note('knowledge_note_edit',{entryId:item.entryId,text})).then(ok=>{if(ok){setTexts(current=>({...current,[item.key]:text}));setEditing(null);}});
    }} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();setEditing(null);}}}>
     <textarea aria-label={t('Note text')} value={editing.text} maxLength={400} rows={2} autoFocus onChange={event=>setEditing({key:item.key,text:event.target.value})}/>
     <span className="dv-rem-edit-acts">
      <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy||!editing.text.trim()}>{busy===item.key?t('Saving…'):t('Save')}</button>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setEditing(null)}>{t('Cancel')}</button>
      <span className="dv-caption">{t('The old wording stays in its history')}</span>
     </span>
     {error?.key===item.key&&<span className="dv-rem-error" role="alert">{error.text}</span>}
    </form></li>;
    const said=words(item);
    return <li key={item.key} className="dv-rem-row" data-kind={item.kind}>
     <span className="dv-rem-kind">{rowLabel(item)}</span>
     <span className="dv-rem-said" title={said.title}>{said.shown}</span>
     <span className="dv-rem-acts">{actions(item)}</span>
     {error?.key===item.key&&<span className="dv-rem-error" role="alert">{t('Couldn’t change it. {error}',{error:error.text})}</span>}
    </li>;
   })}
  </ul>}
 </div>;
}
