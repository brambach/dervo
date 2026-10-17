import {useSyncExternalStore} from 'react';
import {toast} from 'sonner';
import {CopyTick} from './CodingTasks';
import './ErrorNotes.css';
import {formatDate,t} from '@/i18n';

/** Errors you can keep (Bryce, 23 Sep, beta.15 group 4): an error toast's words couldn't be selected or copied, and it
 * vanished after six seconds. Dervo's error toasts now have selectable text, Copy and Close, and an error the window
 * showed is kept in the thread that was open, for this session, with its own Copy. Nothing here is sent anywhere.
 * One place at a time (Bryce, 8 Oct: every error showed twice, in the toast and at the top of the thread). A toast's
 * error moves into the thread only when the toast times out unread; Close means you've seen it, so it isn't kept. */

export type ErrorNote={id:number;text:string;at:number;channelId:string|null};
export const NOTES_KEPT=20;
/** How long an error toast stays unless you hover it or close it. Longer than an ordinary toast's 6 seconds. */
export const ERROR_TOAST_MS=12_000;

let notes:ErrorNote[]=[];
let nextId=1;
let openChannel:string|null=null;
const listeners=new Set<()=>void>();
const changed=()=>{for(const listener of listeners)listener();};

/** The words of an error, the way the window shows it. */
export function errorWords(error:unknown):string{
  const text=error instanceof Error?error.message:typeof error==='string'?error:String(error);
  return text.trim()||t('Something went wrong.');
}
/** Which thread an error belongs to: the one open when it showed. ConnectedApp keeps this current. */
export function setOpenThread(channelId:string|null){openChannel=channelId;}
/** Keep an error for the thread that's open. The same words again within a minute move the first one's time on. */
export function noteError(error:unknown,{channelId=openChannel,now=Date.now()}:{channelId?:string|null;now?:number}={}){
  const text=errorWords(error);
  const same=notes.find(note=>note.channelId===channelId&&note.text===text&&now-note.at<60_000);
  if(same)notes=notes.map(note=>note===same?{...note,at:now}:note);
  else notes=[...notes,{id:nextId++,text,at:now,channelId}].slice(-NOTES_KEPT);
  changed();
}
export function dismissNote(id:number){notes=notes.filter(note=>note.id!==id);changed();}
export function forgetNotes(){notes=[];changed();}
export function threadNotes(channelId:string|null):ErrorNote[]{return notes.filter(note=>note.channelId===channelId);}
function subscribe(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener);};}
export function useThreadNotes(channelId:string|null){
  const all=useSyncExternalStore(subscribe,()=>notes,()=>notes);
  return all.filter(note=>note.channelId===channelId);
}

const dervoActive=()=>typeof document!=='undefined'&&document.documentElement.hasAttribute('data-dervo-local');

/** The toast itself: the kit's material, words you can select, Copy and Close. Swiping is off (sonner's dismissible),
 * because sonner starts a swipe on the first move of a press, before any text is selected. */
export function ErrorToast({id,title,detail}:{id:string|number;title:string;detail?:string}){
  const text=detail?`${title}\n${detail}`:title;
  // No role here: sonner's own region announces the toast, and a second alert inside it is read twice.
  return <div className="dv-err-toast" data-testid="error-toast">
    <p className="dv-err-text">{title}</p>
    {detail&&<p className="dv-err-detail">{detail}</p>}
    <div className="dv-err-acts"><CopyTick text={text} label={t('Copy')} name={t('Copy error')}/><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>toast.dismiss(id)}>{t('Close')}</button></div>
  </div>;
}
export function showErrorToast(title:unknown,detail?:unknown){
  const words=errorWords(title),more=detail===undefined||detail===null||detail===''?undefined:errorWords(detail);
  // The thread it showed in, not whichever is open when the toast leaves.
  const channelId=openChannel,text=more?`${words}\n${more}`:words;
  return toast.custom(id=><ErrorToast id={id} title={words} detail={more}/>,{duration:ERROR_TOAST_MS,dismissible:false,unstyled:true,
    onAutoClose:()=>noteError(text,{channelId})});
}

/** Every toast.error in the app becomes Dervo's error toast while Dervo's local app is showing. Elsewhere, and in tests
 * that never mark the page, it's sonner's own. Installed once, from main.tsx. */
let installed=false;
export function installErrorToasts(){
  if(installed)return;
  installed=true;
  const original=toast.error.bind(toast);
  toast.error=((message:unknown,data?:{description?:unknown})=>{
    if(!dervoActive())return original(message as Parameters<typeof original>[0],data as Parameters<typeof original>[1]);
    const description=typeof data?.description==='function'?undefined:data?.description;
    return showErrorToast(typeof message==='function'?t('Something went wrong.'):message,typeof description==='string'?description:undefined);
  }) as typeof toast.error;
}

/** The thread's own copy of the errors it showed, under the thread's alert line. Each has Copy and a dismiss. */
export function ThreadErrors({channelId,except}:{channelId:string|null;except?:string}){
  const shown=useThreadNotes(channelId).filter(note=>note.text!==except);
  if(!shown.length)return null;
  return <section className="dv-thread-errors" aria-label={t('Errors in this thread')}>
    {shown.map(note=><div key={note.id} className="dv-thread-error">
      <time className="dv-err-time" dateTime={new Date(note.at).toISOString()}>{formatDate(new Date(note.at),{hour:'numeric',minute:'2-digit'})}</time>
      <p className="dv-err-text">{note.text}</p>
      <span className="dv-err-acts"><CopyTick text={note.text} label={t('Copy')} name={t('Copy: {text}',{text:note.text.split('\n')[0]})}/>
        <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Dismiss: {text}',{text:note.text.split('\n')[0]})} onClick={()=>dismissNote(note.id)}>{t('Dismiss')}</button></span>
    </div>)}
  </section>;
}
