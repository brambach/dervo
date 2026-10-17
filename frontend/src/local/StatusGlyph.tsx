import {useRef,type AnimationEvent,type ReactElement} from 'react';
import './StatusGlyph.css';

/* The status glyph from the design system (evidence/design-system/app.css, .st). The shape carries the state, so it
   never depends on colour alone, and it always sits beside a word somewhere on the same row or screen. Rust (accent)
   marks the one thing per view that needs you first; every other glyph stays ink, green or sage. */
/* 'unknown' (beta.17.1): Dervo lost track of the run, so nobody can say how it ended. A question, never the failure X. */
export type GlyphState='running'|'queued'|'needs'|'failed'|'stopped'|'unknown'|'done'|'idle';

const shapes:Record<GlyphState,ReactElement>={
 running:<><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="2"/><path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></>,
 queued:<circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="2.4 2.2"/>,
 needs:<><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6"/><circle cx="8" cy="8" r="3" fill="currentColor"/></>,
 failed:<><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6"/><path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></>,
 stopped:<><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6"/><path d="M6.5 5.5v5M9.5 5.5v5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></>,
 unknown:<><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6"/><path d="M6.3 6.4a1.8 1.8 0 0 1 3.5.5c0 1.2-1.8 1.4-1.8 2.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/><circle cx="8" cy="11.4" r=".9" fill="currentColor"/></>,
 done:<><circle cx="8" cy="8" r="7" fill="currentColor" opacity=".16"/><path d="M5 8.2l2 2 4-4.4" pathLength={1} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></>,
 idle:<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.5"/>,
};

/** How a glyph arrived at its state, when it changed while on screen: 'done' for a run that just finished (the check
 * draws, then its disc fades in), 'swap' for any other change (a 140ms crossfade). Null on first show, so a list of
 * glyphs never animates just for appearing (design report, 23 Sep, #3: only real state changes move). */
export function arrivalOf(before:GlyphState,after:GlyphState):'done'|'swap'|null{
 if(before===after)return null;
 return before==='running'&&after==='done'?'done':'swap';
}

/** ``from``: the state this glyph replaces when it mounts in another element's place, so the change still reads as one.
 * The Progress panel's head swaps its Running symbol for this glyph when a run ends, and passes 'running'. */
export function StatusGlyph({state,accent=false,label,from}:{state:GlyphState;accent?:boolean;label?:string;from?:GlyphState}){
 // Worked out during render, so the new shape's first frame is already the start of its entrance. Keyed by state, the
 // svg remounts on every change, which restarts its CSS animation even when two changes in a row are the same kind.
 const last=useRef(from??state),arrival=useRef<'done'|'swap'|null>(null);
 if(last.current!==state){arrival.current=arrivalOf(last.current,state);last.current=state;}
 // One shot: once the entrance's last animation (the crossfade, or the disc after the check) ends, the attribute goes,
 // so a list that re-sorts and re-inserts this row can't make it play again (design review, 23 Sep, 2).
 const settled=(event:AnimationEvent<HTMLSpanElement>)=>{
  if(event.animationName!=='dv-st-in')return;
  arrival.current=null;event.currentTarget.removeAttribute('data-arrive');
 };
 return <span className="dv-st" data-state={state} data-accent={accent||undefined} data-arrive={arrival.current??undefined} onAnimationEnd={settled}
  role={label?'img':undefined} aria-label={label} aria-hidden={label?undefined:true}>
  <svg key={state} viewBox="0 0 16 16" focusable="false">{shapes[state]}</svg>
 </span>;
}
