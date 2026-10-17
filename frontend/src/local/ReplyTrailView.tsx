import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {PresenceText} from './Presence';
import {runSummary,type ReplyRun,type TrailStep} from './replyTrail';
import {PEBBLE,PEBBLE_BOX} from './pebble';
import {t} from '@/i18n';

/** The live reply's trail and end chip (16.2, mock F). Words and data come from replyTrail.ts. */

const Tick=()=><svg className="dv-trail-tick" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.5l2.3 2.3 4.7-5"/></svg>;
const Words=({step}:{step:TrailStep})=><>{step.text}{step.subject&&<> <span className="dv-trail-subject">{step.subject}</span></>}</>;

/** The last finished steps, faint and ticked, then the live step shimmering. When the line is short the oldest crumbs
 * drop out whole, never cut mid-word, and the live step never shrinks (fitTrail in the mock). Rechecked on resize. */
export function ReplyTrailLine({run}:{run:ReplyRun}){
 const line=useRef<HTMLDivElement|null>(null);
 const [width,setWidth]=useState(0);
 useEffect(()=>{
  const el=line.current;
  if(!el||typeof ResizeObserver==='undefined')return;
  const observer=new ResizeObserver(entries=>setWidth(Math.round(entries[0]?.contentRect.width??0)));
  observer.observe(el);
  return()=>observer.disconnect();
 },[]);
 // Show every crumb, then hide the oldest while the line overflows. React never sets ``hidden`` here, so it keeps it.
 useLayoutEffect(()=>{
  const el=line.current;
  if(!el)return;
  const parts=[...el.querySelectorAll<HTMLElement>('[data-trail-part]')];
  for(const part of parts)part.hidden=false;
  for(const part of parts){if(el.scrollWidth<=el.clientWidth+1)break;part.hidden=true;}
 },[run.key,width]);
 const now=run.now;
 return <div ref={line} className="dv-trail" data-testid="reply-trail">
  {run.live&&<Beat/>}
  {run.done.map((step,i)=><span key={step.id} data-trail-part className="dv-trail-part">
   <span className={`dv-trail-crumb${run.done.length>1&&i===0?' dv-trail-old':''}`}><Tick/><Words step={step}/></span>
   <span className="dv-trail-sep" aria-hidden="true">›</span>
  </span>)}
  {now&&<span className="dv-trail-now" key={now.id}>
   <PresenceText className="dv-live-step" active>{now.text}</PresenceText>
   {now.subject&&<span className="dv-trail-subject">{now.subject}</span>}
   {now.helpers&&<span className="dv-trail-dots" role="img" aria-label={t('{length} of {length2} finished',{length:now.helpers.filter(Boolean).length,length2:now.helpers.length})}>{now.helpers.map((finished,i)=><i key={i} data-done={finished||undefined}/>)}</span>}
  </span>}
 </div>;
}

/** The thinking beat (design, 8 Oct): the pebble's shape with no face, small and ink, breathing on a 2.4 s cycle from the moment you send until the run ends, when the end chip's tick takes its place. One element,
 * transform and opacity only, still under reduced motion. Mock F took the face out of the row and Bryce took the
 * green orb out of Progress (23 Sep), so it stays quiet: a pulse you feel at the edge of your eye, not a character. */
function Beat(){
 return <svg className="dv-trail-beat" viewBox={PEBBLE_BOX} aria-hidden="true" focusable="false" data-testid="reply-beat"><path d={PEBBLE}/></svg>;
}

/** A finished run folds into one chip under its reply: "6 steps · 2 helpers · 13s ›", which opens Progress. */
export function ReplyRunChip({run}:{run:ReplyRun}){
 const words=runSummary(run);
 if(!words||!run.steps&&!run.helpers)return null;
 return <button type="button" className="dv-run-chip" data-testid="reply-run-chip" onClick={()=>window.dispatchEvent(new window.CustomEvent('dervo-open-progress'))}>
  <Tick/>{words}<span aria-hidden="true">›</span>
 </button>;
}

/** The header's model, beside the agent's name and quieter than it: "Claude Code  Opus 5.5". Beta.18 (Bryce, 6 Oct: the
 * "Claude Code · Opus 5.5 · 15s" header "just doesn't feel good"; he wants it clean and premium, option B in
 * evidence/b18-polish/run-header): no clock ticking every second. How long it took is in the end chip and in Progress. */
export function ReplyRunMeta({run}:{run:ReplyRun}){
 if(!run.model)return null;
 return <span className="dv-reply-meta" data-testid="reply-run-meta">{run.model}</span>;
}
