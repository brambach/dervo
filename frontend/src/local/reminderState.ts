import {local} from './api';
import type {Note} from './notifications';
import {formatDate,t} from '@/i18n';

/** A reminder (backend/task_reminders.py): an agent's, or since 9 Oct one you set with Remind me tomorrow (``mine``, with
 * no asker and no task). An agent's ``text`` is its line, shown labelled as the asker's, never as your request. A
 * reminder only ever opens its thread: it never starts work or sends anything. */
export type Reminder={id:string;projectId:string;channelId:string;taskId:string|null;thread:string;project:string|null;asker:string|null;mine?:boolean;
 text:string;on:string;createdAt:number;notified:boolean};

/** ``upcoming``: what's still to come in this project, soonest first, so a thread's row can say a reminder is set. A host
 * older than 9 Oct leaves it out. */
export const readReminders=(projectId:string,profileId:string)=>local<{due:Reminder[];upcoming?:Reminder[]}>('reminders',{projectId,profileId});
export const readDueReminders=()=>local<{due:Reminder[]}>('reminders_due',{});
export const markReminderNotified=(reminderId:string)=>local<{id:string;notified:boolean}>('reminder_notified',{reminderId});
export const markReminderDone=(projectId:string,profileId:string,reminderId:string)=>local<{id:string;state:string}>('reminder_done',{projectId,profileId,reminderId});
/** Your own reminder on one of this project's threads. One per thread and day: asking again gives back the same one. */
export const addReminder=(projectId:string,profileId:string,channelId:string,on:string,text:string)=>
 local<Reminder&{added:boolean}>('reminder_add',{projectId,profileId,channelId,on,text});

/** A day as the host keeps it, YYYY-MM-DD, in this Mac's time zone (the host's too). */
export function isoDay(date:Date):string{
 return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
export function tomorrow(now=new Date()):string{return isoDay(new Date(now.getFullYear(),now.getMonth(),now.getDate()+1));}
const dayOf=(on:string)=>{const [year,month,day]=on.split('-').map(Number);return {date:new Date(year,month-1,day),year};};

/** "Today", or the day it was due when it's been waiting. ``now`` is for tests. */
export function dueLine(on:string,now=new Date()):string{
 if(on>=isoDay(now))return t('Today');
 const {date,year}=dayOf(on);
 return t('Due {date}',{date:formatDate(date,{day:'numeric',month:'short',...(year!==now.getFullYear()?{year:'numeric'}:{})})});
}
/** When a reminder still to come is due: "tomorrow", else its weekday and date. */
export function aheadLine(on:string,now=new Date()):string{
 if(on===tomorrow(now))return t('tomorrow');
 const {date,year}=dayOf(on);
 return formatDate(date,{weekday:'short',day:'numeric',month:'short',...(year!==now.getFullYear()?{year:'numeric'}:{})});
}

/** A reminder is for a day, so its notification waits until 8 am on it: one set for tomorrow doesn't go off at midnight
 * (design review, 9 Oct). Home lists it from the start of its day. */
export const NOTE_HOUR=8;
export function beforeNoteHour(now:Date):boolean{return now.getHours()<NOTE_HOUR;}

/** Whose reminder it is, as its row says it. */
export function reminderWho(reminder:Reminder):string{
 return reminder.mine||!reminder.asker?t('Your reminder'):t('{asker}’s reminder',{asker:reminder.asker});
}

/** The one macOS notification for a reminder: it opens the thread. An agent's words are quoted as its own; yours are
 * your own line. */
export function reminderNote(reminder:Reminder):Note{
 const mine=reminder.mine||!reminder.asker;
 return {id:`reminder:${reminder.id}`,title:mine?t('Your reminder: #{thread}',{thread:reminder.thread}):t('Reminder from #{thread}',{thread:reminder.thread}),
  body:mine?reminder.text:`${reminder.asker}: “${reminder.text}”`,projectId:reminder.projectId,channelId:reminder.channelId};
}
