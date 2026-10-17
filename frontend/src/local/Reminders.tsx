import {useRef,useState,type MouseEvent} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {addReminder,aheadLine,dueLine,isoDay,markReminderDone,readReminders,reminderWho,tomorrow,type Reminder} from './reminderState';
import './Reminders.css';
import {t} from '@/i18n';

/** Beta.16.1, item 1 (Bryce, 26 Sep: reminders show on Home and as a macOS notification), and 9 Oct (Bryce and tester
 * Harrison: Home is reminders, suggestions and what needs you, with reminders kept minimal). A reminder whose day has
 * come is a row under Reminders that names its thread and opens it. An agent's line is quoted and labelled with who
 * wrote it; yours is your own. Opening the thread is all a reminder does: it never starts work or sends your words, so
 * it doesn't go through the send-a-message path (ConnectedApp.handToConversation). Done takes it off Home.
 *
 * Remind me tomorrow, on the card that asks you and in the thread panel, saves your own reminder on that thread for
 * tomorrow (task_reminders reminder_add). The host only shows it on its day and posts one notification that opens the
 * thread. Yours asks you to come back to the thread, so opening that thread from Home is what settles it. */

type Saved={due:Reminder[];upcoming?:Reminder[]};
/** Where a change was made, so its failure is said there: the card, a reminder's row, or one thread's panel. */
export type ReminderPlace='card'|'row'|'panel';
type Failure={text:string;place:ReminderPlace;channelId:string};
/** The host's own sentence for a refusal (local() hands it over as text, already in the window's language). */
const said=(error:unknown)=>error instanceof Error?error.message:typeof error==='string'?error:'';
export type ReminderControl={due:Reminder[];upcoming:Reminder[];
 /** What's in progress: `done:<id>` or `later:<channelId>`. */
 busy:string|null;failure:Failure|null;clear:()=>void;
 /** The soonest reminder still to come on a thread, anyone's. */
 ahead:(channelId:string)=>Reminder|null;
 /** Your reminder for tomorrow on a thread, the one Remind me tomorrow sets. */
 tomorrowFor:(channelId:string)=>Reminder|null;
 /** Your own reminder on a thread whose day has come. */
 dueMine:(channelId:string)=>Reminder|null;
 /** Resolves true once the host has it done. */
 done:(reminder:Reminder,place:ReminderPlace)=>Promise<boolean>;
 later:(channelId:string,place:ReminderPlace)=>Promise<void>;
 /** You opened the thread from Home: yours that asked you to come back to it are done. */
 cameBack:(channelId:string)=>void};

/** This project's reminders, due and still to come, read once a minute and after each change. The host's answer to a
 * change goes straight into the list, so the row says it at once; the read after it is the record. */
export function useReminders(projectId:string,profileId:string):ReminderControl{
 const client=useQueryClient();
 const key=['reminders',projectId,profileId];
 const found=useQuery({queryKey:key,enabled:!!projectId&&!!profileId,queryFn:()=>readReminders(projectId,profileId),refetchInterval:60000,retry:false});
 const [busy,setBusy]=useState<string|null>(null),[failure,setFailure]=useState<Failure|null>(null);
 // What each of tomorrow's took the place of, by its id, so Undo can bring those back (state review, 9 Oct).
 const moved=useRef(new Map<string,Reminder[]>());
 const settling=useRef(new Set<string>());
 const due=found.data?.due??[],upcoming=found.data?.upcoming??[];
 const edit=(change:(saved:Saved)=>Saved)=>client.setQueryData<Saved>(key,saved=>saved?change(saved):saved);
 const fail=(place:ReminderPlace,channelId:string,text:string)=>setFailure({text,place,channelId});
 async function done(reminder:Reminder,place:ReminderPlace){
  if(busy)return false;
  setBusy(`done:${reminder.id}`);setFailure(null);
  try{
   await markReminderDone(projectId,profileId,reminder.id);
   edit(saved=>({...saved,due:saved.due.filter(row=>row.id!==reminder.id),upcoming:saved.upcoming?.filter(row=>row.id!==reminder.id)}));
   // Taking back tomorrow's that moved yours from today brings today's back: Undo puts things as they were.
   const back=moved.current.get(reminder.id)??[];
   moved.current.delete(reminder.id);
   try{
    for(const old of back){
     const today=isoDay(new Date());
     const row=await addReminder(projectId,profileId,old.channelId,old.on>today?old.on:today,old.text);
     edit(saved=>({...saved,due:[...saved.due.filter(other=>other.id!==row.id),row]}));
    }
   }catch(error){fail(place,reminder.channelId,said(error)||t('That reminder couldn’t be saved. Try again.'));}
   await client.invalidateQueries({queryKey:key});
   return true;
  }catch(error){
   fail(place,reminder.channelId,said(error)||(reminder.on>isoDay(new Date())?t('That reminder couldn’t be taken back. It’s still set.'):t('That reminder couldn’t be marked done. It’s still here.')));
   return false;
  }finally{setBusy(null);}
 }
 /** Saves yours for tomorrow. One of yours already due on that thread moves to tomorrow with it, as asking again
  * means; if marking it done fails, it stays on Home, which is what the host's record says. */
 async function later(channelId:string,place:ReminderPlace){
  if(busy)return;
  setBusy(`later:${channelId}`);setFailure(null);
  try{
   // The line is read tomorrow, so it says nothing about how the thread stands today (product review, 9 Oct).
   const row=await addReminder(projectId,profileId,channelId,tomorrow(),t('Come back to this thread'));
   edit(saved=>({...saved,upcoming:[...(saved.upcoming??[]).filter(other=>other.id!==row.id),row].sort((a,b)=>a.on.localeCompare(b.on)||a.createdAt-b.createdAt)}));
   const took:Reminder[]=[];
   for(const old of due.filter(other=>other.channelId===channelId&&other.mine)){
    if(await markReminderDone(projectId,profileId,old.id).then(()=>true,()=>false))took.push(old);
   }
   if(took.length){
    moved.current.set(row.id,took);
    edit(saved=>({...saved,due:saved.due.filter(other=>!took.some(old=>old.id===other.id))}));
   }
   await client.invalidateQueries({queryKey:key});
  }catch(error){fail(place,channelId,said(error)||t('That reminder couldn’t be saved. Try again.'));}
  finally{setBusy(null);}
 }
 function cameBack(channelId:string){
  for(const old of due.filter(row=>row.channelId===channelId&&row.mine&&!settling.current.has(row.id))){
   settling.current.add(old.id);
   // Quietly: if the host can't mark it, it simply stays on Home with its own Done.
   void markReminderDone(projectId,profileId,old.id).then(()=>{
    edit(saved=>({...saved,due:saved.due.filter(row=>row.id!==old.id)}));
    return client.invalidateQueries({queryKey:key});
   },()=>undefined).finally(()=>settling.current.delete(old.id));
  }
 }
 return {due,upcoming,busy,failure,clear:()=>setFailure(null),done,later,cameBack,
  ahead:channelId=>upcoming.find(row=>row.channelId===channelId)??null,
  tomorrowFor:channelId=>{const day=tomorrow();return upcoming.find(row=>row.channelId===channelId&&row.mine&&row.on===day)??null;},
  dueMine:channelId=>due.find(row=>row.channelId===channelId&&row.mine)??null};
}

/** A bell, in the status glyph's place: a reminder isn't a run, so it doesn't take a run's shape. */
const Bell=({filled=false}:{filled?:boolean})=><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
 <path d="M4.3 10.9V7.3a3.7 3.7 0 0 1 7.4 0v3.6l1.2 1.4H3.1z" fill={filled?'currentColor':'none'} stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
 <path d="M6.6 13.9a1.5 1.5 0 0 0 2.8 0" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>;

/** Before a row leaves the list, focus goes to the row after it, or the one before, so it doesn't fall to the page. */
export function keepFocus(event:MouseEvent<HTMLElement>):()=>void{
 const row=event.currentTarget.closest('article');
 const next=[row?.nextElementSibling,row?.previousElementSibling].map(node=>node?.querySelector<HTMLElement>('.dv-home-row-open')).find(Boolean)??null;
 return ()=>{if(!document.activeElement||document.activeElement===document.body||!document.activeElement.isConnected)next?.focus();};
}

/** A reminder whose day has come, as a row under Reminders. The name opens the thread (the whole row, as every Home row
 * does); Done takes it off Home. */
export function ReminderRow({reminder,saving,disabled,onOpen,onDone}:{reminder:Reminder;saving:boolean;disabled:boolean;onOpen:(channelId:string)=>void;
 onDone:(reminder:Reminder)=>Promise<boolean>}){
 const mine=reminder.mine||!reminder.asker,who=reminderWho(reminder);
 return <article className="dv-row dv-home-row dv-home-reminder" data-reminder-id={reminder.id}>
  <span className="dv-st dv-home-bell" aria-hidden="true"><Bell/></span>
  <div>
   <button type="button" className="dv-row-name dv-home-row-open" onClick={()=>onOpen(reminder.channelId)}
    aria-label={t('Open {thread}: {who}',{thread:reminder.thread,who})}>{reminder.thread}</button>
   <p className="dv-row-did">{mine?reminder.text:`“${reminder.text}”`}</p>
   <p className="dv-row-meta"><span data-tone="ask">{who}</span><span>{dueLine(reminder.on)}</span></p>
  </div>
  <div className="dv-row-end">
   <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={disabled} aria-label={t('Done: {who} on #{thread}',{who,thread:reminder.thread})}
    onClick={event=>{const refocus=keepFocus(event);void onDone(reminder).then(ok=>{if(ok)refocus();});}}>{saving?t('Saving…'):t('Done')}</button>
  </div>
 </article>;
}

/** Remind me tomorrow on the card that asks you: one tap, then the same place says it's set, with Undo. One button
 * that changes its words, so focus stays on it, and a status line that's always there, so the change is announced. */
export function RemindLater({control,channelId}:{control:ReminderControl;channelId:string}){
 const set=control.tomorrowFor(channelId);
 const busy=set?control.busy===`done:${set.id}`:control.busy===`later:${channelId}`;
 const day=set?aheadLine(set.on):'';
 return <span className="dv-home-remind" data-set={set?'':undefined}>
  <span className="dv-caption" role="status">{set?t('Reminder set for {day}',{day}):''}</span>
  <button type="button" className={set?'dv-btn dv-btn-quiet dv-btn-sm':'dv-btn dv-btn-quiet'} aria-disabled={!!control.busy||undefined}
   aria-label={set?t('Undo the reminder for {day}',{day}):undefined}
   onClick={()=>{if(control.busy)return;void(set?control.done(set,'card'):control.later(channelId,'card'));}}>
   {set?(busy?t('Undoing…'):t('Undo')):(busy?t('Saving…'):t('Remind me tomorrow'))}</button>
 </span>;
}

/** The same in the thread panel's head, where there's room for an icon only: a bell that's filled while it's set.
 * Pressing it again takes the reminder back. */
export function RemindToggle({control,channelId,name}:{control:ReminderControl;channelId:string;name:string}){
 const set=control.tomorrowFor(channelId);
 const tip=set?t('Reminder set for {day}. Press to take it back.',{day:aheadLine(set.on)}):t('Remind me about {name} tomorrow',{name});
 return <button type="button" className="dv-btn-icon dv-home-remind-toggle" aria-pressed={!!set} aria-label={t('Remind me tomorrow')} title={tip}
  aria-disabled={!!control.busy||undefined} onClick={()=>{if(control.busy)return;void(set?control.done(set,'panel'):control.later(channelId,'panel'));}}>
  <Bell filled={!!set}/></button>;
}

/** A reminder change that didn't go through, said where you made it (one thread's panel says only its own), with
 * Dismiss. */
export function ReminderFailure({control,place,channelId}:{control:ReminderControl;place:ReminderPlace;channelId?:string}){
 const failure=control.failure;
 if(!failure||failure.place!==place||(channelId&&failure.channelId!==channelId))return null;
 return <p className="dv-caption dv-home-failure" role="alert">{failure.text}{' '}
  <button type="button" className="dv-btn-quiet" onClick={control.clear}>{t('Dismiss')}</button></p>;
}
