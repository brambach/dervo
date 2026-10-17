import {useEffect,useState,type ReactNode} from 'react';
import {spoken} from './homeTime';
import './ChatMotion.css';
import {t} from '@/i18n';

/** Dervo's presence on Home while it answers (20 Sep, the standard pattern: the composer never moves, the step shows
 * where the reply is about to appear). The composer stays exactly as it is while Home works: same height, same
 * placeholder, same hint, Send disabled in its place. In the message stream, where the answer will land, one line with a
 * soft shimmer says one honest step; the answer replaces it in place. No name row above a reply (reply header D): what
 * it read and when is said once, at the reply's foot, on the newest reply, and on hover for the older ones.
 *
 * The step is only what Home can honestly know. The host makes one model call per message (project_orchestrator.py
 * `_begin`), with no stream and no step events, and gives up after two minutes; the jobs are sent from this screen after
 * the answer lands. So the step comes from three sources and nothing finer: the clock (the snapshot reads the shared
 * threads before the call, then the model thinks), whether you sent images, and which job this screen is sending. */

/** How long the step says it's reading, before it says it's thinking. The snapshot reads the shared threads first. */
export const READ_FOR=4;
/** When "Thinking it through" becomes "Still thinking", with the time. */
export const STILL_AFTER=20;

/** Time spent, in the words the step uses: "20 sec", "40 sec", "1 min", "2 min". Tens of seconds, so it doesn't tick. */
export function waited(seconds:number):string{
 if(seconds<60)return t('{seconds} sec',{seconds:Math.max(10,Math.floor(seconds/10)*10)});
 return t('{minutes} min',{minutes:Math.floor(seconds/60)});
}

/** The step where the reply will appear. ``since`` is when the host recorded the thinking reply (its clock and this
 * screen's are the same Mac's). ``fresh`` is whether this screen sent the message in this session: only then do the
 * first seconds say it's looking through the threads, because a thinking reply found after a reload was snapshotted
 * before the app came back, and that reading is long done. No count: the snapshot reads the scope's own threads plus
 * the personal ones (project_ideas.py ON_THIS_MAC) and describes at most 30 in full, and this screen's list has neither
 * filter, so any number here could be wrong. ``hasThreads`` says whether there's anything shared to look through.
 * ``starting`` is the job this screen is sending right now, already worded ("Starting Connectors docs"); it wins,
 * because it's the one thing Home knows for certain. */
export function workingStep({since,now,fresh=true,hasThreads=true,images=0,starting=null}
 :{since:number;now:number;fresh?:boolean;hasThreads?:boolean;images?:number;starting?:string|null}):{text:string;waited:string|null}{
 if(starting)return {text:starting,waited:null};
 const elapsed=Math.max(0,now-since);
 if(fresh&&elapsed<READ_FOR){
  if(images>0)return {text:images===1?t('Looking at your image'):t('Looking at your images'),waited:null};
  return {text:hasThreads?t('Looking through your threads'):t('Looking through your project'),waited:null};
 }
 if(elapsed<STILL_AFTER)return {text:t('Thinking it through'),waited:null};
 return {text:t('Still thinking'),waited:waited(elapsed)};
}

/** The words for a job as this screen sends it: a new thread starts, an existing one is added to. */
export function startingLine(part:{action:'start'|'add';name?:string;title?:string|null;conversation?:{name:string}|null}):string{
 const name=part.name??part.conversation?.name??part.title??t('a new thread');
 return part.action==='add'?t('Adding to {name}',{name}):t('Starting {name}',{name});
}

/** What the model read before it answered: "Checked Sidebar polish", "Read 3 threads", or nothing. */
export function readLine(looked:string[]|undefined):string{
 if(!looked?.length)return '';
 return looked.length===1?t('Checked {thread}',{thread:looked[0]}):t('Read {length} threads',{length:looked.length});
}

/** The receipt at a reply's foot: what it read, then when. Said once, here, and nowhere else. */
export function receiptLine(looked:string[]|undefined,at:number,now=Date.now()/1000):string{
 return [readLine(looked),spoken(at,now)].filter(Boolean).join(' · ');
}

/** A failed answer, in the reply's place: the host's own sentence. When Send again sits beside it, the sentence's own
 * "Send it again." would say the button twice, so that goes. */
export function failureLine(detail:string|null|undefined,withButton:boolean):string{
 const said=(detail??'').trim()||t('Couldn’t answer just now.');
 return withButton?said.replace(/\s*Send it again\.$/,''):said;
}

/** Whether the person asked for less motion. Read once on mount and again when it changes, so a test can set it. */
export function useReducedMotion():boolean{
 const query=()=>typeof window!=='undefined'&&!!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
 const [reduced,setReduced]=useState(query);
 useEffect(()=>{
  const media=typeof window!=='undefined'?window.matchMedia?.('(prefers-reduced-motion: reduce)'):null;
  if(!media)return;
  setReduced(!!media.matches);
  const change=()=>setReduced(!!media.matches);
  media.addEventListener?.('change',change);
  return ()=>media.removeEventListener?.('change',change);
 },[]);
 return reduced;
}

/** The same quiet working text on Home, in replies and beside the composer. Keep this span mounted as the observed
 * step changes so the shimmer doesn't restart. Callers own the actual work state; settled text never shimmers. */
export function PresenceText({children,active=false,className=''}:{children:ReactNode;active?:boolean;className?:string}){
 const reduced=useReducedMotion();
 return <span className={`dv-presence-text${className?` ${className}`:''}`} data-shimmer={(active&&!reduced)||undefined}>{children}</span>;
}

/** The step line: one honest step, shimmering softly where the answer will land. The words change in place; the wait
 * ("· 2 min") sits after them in the caption size. With reduced motion there's no shimmer: the text just changes.
 * ``foot`` puts it at a reply's foot in the receipt's size, for the job this screen is sending after an answer; that
 * line never shimmers (a moving caption under an answer is noise). */
export function StepLine({text,waited,foot=false}:{text:string;waited:string|null;foot?:boolean}){
 const reduced=useReducedMotion();
 return <p className={`dv-home-step${foot?' dv-home-step-foot':' dv-read'}`} role="status" aria-live="polite" data-shimmer={(!reduced&&!foot)||undefined}>
  <PresenceText className="dv-home-step-words" active={!foot}>{text}</PresenceText>
  {waited&&<span className="dv-home-step-waited">· {waited}</span>}
 </p>;
}
