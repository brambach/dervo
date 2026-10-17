import {useEffect,useRef} from 'react';
import {local} from './api';
import {beforeNoteHour,markReminderNotified,readDueReminders,reminderNote} from './reminderState';
import {HOME_CHANNEL,forgetHomeJob,goodMorning,isNews,loadHomeJobs,loadPosted,loadPrefs,notificationsToSend,onNotificationOpen,postNotification,pushNote,readSummary,rememberPosted,setDockBadge,type LostRun,type Note,type OnScreen,type PushRecord,type SessionSummary,type ThreadActivity} from './notifications';

const INTERVAL=10000;
type Target={projectId:string;channelId:string};

/** Watches the host's session summary: posts a note for new results and waits, keeps the Dock badge at the number of
 * conversations waiting on you, sends the good-morning note once per launch (or, after a close that lost runs, one note
 * for all of them), and opens a clicked note. It also watches the push on the project you're in, and any push it saw
 * running, for a result you'd miss behind other windows. Every note id is remembered on this Mac, so a reload or a
 * relaunch never sends one twice. A reminder gets one note on its day (beta.16.1), from 8 am, checked about once a minute,
 * and the host records that it went out, so another launch doesn't send it again. ``clock`` is for tests. Failures to post or badge are
 * ignored: notifications are a courtesy, and the sidebar, Home and Catch up still show everything. */
export function useNotifications({screen,onOpen,onOpenHome,interval=INTERVAL,clock=()=>new Date()}:{screen:()=>OnScreen;onOpen:(target:Target)=>void;onOpenHome?:(projectId:string)=>void;interval?:number;clock?:()=>Date}){
  const latest=useRef({screen,onOpen,onOpenHome,clock});latest.current={screen,onOpen,onOpenHome,clock};
  useEffect(()=>{
    let alive=true,previous:SessionSummary|null=null,greeted=false,timer:ReturnType<typeof setTimeout>|undefined;
    let unlisten:(()=>void)|undefined,polls=0;
    // The last push record seen per project this launch. A project not in here hasn't been looked at yet.
    const pushes=new Map<string,PushRecord|null>();
    void onNotificationOpen(target=>{
      const {onOpen:open,onOpenHome:home}=latest.current;
      // A note about Home (lost runs, a push): Home when the shell can open it, else the project's Catch up.
      if(target.channelId===HOME_CHANNEL){if(home)home(target.projectId);else open({projectId:target.projectId,channelId:''});}
      else open(target);
    }).then(stop=>{if(alive)unlisten=stop;else stop();}).catch(()=>{});
    async function activityOf(projects:string[]){
      const found:Record<string,ThreadActivity>={};
      for(const projectId of projects){
        try{for(const row of await local<{id:string;activity?:ThreadActivity|null}[]>('conversations',{projectId}))if(row.activity)found[row.id]=row.activity;}
        catch{/* Without it the note is the plain one. */}
      }
      return found;
    }
    async function pushNotes(view:OnScreen,prefs:ReturnType<typeof loadPrefs>):Promise<Note[]>{
      const watch=new Set([...(view.projectId?[view.projectId]:[]),...[...pushes].filter(([,record])=>record?.state==='running').map(([id])=>id)]);
      const notes:Note[]=[];
      for(const projectId of watch){
        try{
          const record=await local<PushRecord|null>('push_status',{projectId});
          const note=pushNote(projectId,pushes.get(projectId),record,prefs,view.focused);
          if(note)notes.push(note);
          pushes.set(projectId,record??null);
        }catch{pushes.set(projectId,null);/* An older host, or an archived project: no push to watch. */}
      }
      return notes;
    }
    /** Reminders whose day has come and that haven't had their note, from 8 am. Like every other note, none while Dervo
     * is in front when notifications are for the background only; Home shows them meanwhile. */
    async function reminderNotes(view:OnScreen,prefs:ReturnType<typeof loadPrefs>):Promise<{id:string;note:Note}[]>{
      if(!prefs.needs||(prefs.backgroundOnly&&view.focused)||beforeNoteHour(latest.current.clock()))return [];
      try{return (await readDueReminders()).due.filter(reminder=>!reminder.notified).map(reminder=>({id:reminder.id,note:reminderNote(reminder)}));}
      catch{return [];/* An older host: no reminders. */}
    }
    async function poll(){
      try{
        const next=await readSummary();
        if(!alive)return;
        const prefs=loadPrefs(),view=latest.current.screen();
        // Once you've looked at a thread Home sent a job to, it's yours again: its next note doesn't say Home.
        if(view.focused&&view.channelId)forgetHomeJob(view.channelId);
        const notes:Note[]=[];
        if(!greeted){
          greeted=true;
          const lost=await local<{lostRuns?:LostRun[]}>('status').then(status=>Array.isArray(status?.lostRuns)?status.lostRuns:[]).catch(()=>[] as LostRun[]);
          const note=goodMorning(next,prefs,lost);
          if(note)notes.push(note);
        }
        const fromHome=loadHomeJobs();
        let fresh=notificationsToSend(previous,next,prefs,view,{fromHome});
        // Only when something is about to be sent: how each of those threads ended (a Stop you pressed says nothing)
        // and what it said (a dropped connector is named).
        if(fresh.length){
          const activity=await activityOf([...new Set(fresh.map(note=>note.projectId))]);
          fresh=notificationsToSend(previous,next,prefs,view,{fromHome,activity});
        }
        notes.push(...fresh,...await pushNotes(view,prefs));
        const reminders=polls++%6===0?await reminderNotes(view,prefs):[];
        if(!alive)return;
        const posted=loadPosted();
        for(const note of notes){
          if(!isNews(note,posted))continue;
          rememberPosted(note);
          if(note.id.startsWith('finished:'))forgetHomeJob(note.channelId);
          void postNotification(note).catch(()=>{});
        }
        for(const {id,note} of reminders){
          if(isNews(note,posted)){rememberPosted(note);void postNotification(note).catch(()=>{});}
          void markReminderNotified(id).catch(()=>{/* This Mac remembers it was posted. */});
        }
        void setDockBadge(next.needs.length).catch(()=>{});
        previous=next;
      }catch{/* The next poll tries again. */}
      finally{if(alive)timer=setTimeout(()=>void poll(),interval);}
    }
    void poll();
    return()=>{alive=false;if(timer)clearTimeout(timer);unlisten?.();};
  },[]);
}
