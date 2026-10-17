import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {animate} from 'motion';
import {PEBBLE,PEBBLE_BOX} from './pebble';
import './ChatMotion.css';

/** The working smile, direction C ("think, then smile", owner, 19 Sep; evidence/design-tests/chat-feel, card C). While an
 * agent works the pebble's mouth is a small flat line that drifts and its eyes look up. When the work ends the mouth
 * morphs into the full smile and the pebble does one small hop, once. It sits where the grey spinner used to: the live
 * step under a reply, the status row above the composer, and a decision line while an independent review runs. It is
 * shown only while real work runs; the parent unmounts it (or stops it) when the run ends. With reduced motion nothing
 * moves: the mouth switches between the line and the smile at once and there is no drift or hop. */
export const SMILE='M30 56Q48 79 68 54';
export const THINK='M40 61Q49 63 58 60';
const NUMBERS=/-?\d+(?:\.\d+)?/g;
const numbers=(path:string)=>path.match(NUMBERS)!.map(Number);
const SMILE_N=numbers(SMILE),THINK_N=numbers(THINK);
/** The mouth part way between the flat line and the smile, 0 = line, 1 = smile. */
export function mouthPath(t:number):string{
  const p=Math.max(0,Math.min(1,t));
  const n=THINK_N.map((v,i)=>+(v+(SMILE_N[i]-v)*p).toFixed(2));
  return `M${n[0]} ${n[1]}Q${n[2]} ${n[3]} ${n[4]} ${n[5]}`;
}
export const MORPH_MS=420;
export const HOP_MS=520;
const EASE:[number,number,number,number]=[.2,.7,.2,1];
const reduced=()=>typeof window!=='undefined'&&!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** What the pebble does while it works, from the words of the step Dervo recorded (backend conversation_activity
 * STEP_WORDS), so the motion never claims more than the line beside it says (Bryce, 24 Sep; the study is
 * evidence/design-tests/thinking-marks). Reading travels a line with its eyes, searching darts, editing writes its mouth,
 * a command blinks a caret, waiting closes its eyes and breathes. Anything else is plain thinking. */
export type WorkMotion='think'|'read'|'search'|'write'|'run'|'wait';
export function motionOf(step?:string|null):WorkMotion{
  const s=(step??'').trim().toLowerCase();
  if(!s)return 'think';
  if(/^(waiting|queued|connecting)\b/.test(s))return 'wait';
  if(/^(searching|finding|looking through|looking for)\b/.test(s))return 'search';
  if(/^(editing|writing|creating|updating the task guide|saving)\b/.test(s))return 'write';
  if(/^running\b/.test(s))return 'run';
  if(/^(reading|listing|fetching|checking)\b/.test(s))return 'read';
  return 'think';
}

export function WorkingMark({working,size=16,className='',label,step}:{working:boolean;size?:number;className?:string;label?:string;step?:string|null}){
  const mouth=useRef<SVGPathElement>(null);
  // Where the mouth is now, 0 = the flat line, 1 = the smile. Kept here, not in the markup, so a re-render can't jump it.
  const at=useRef(working?0:1);
  const was=useRef(working);
  const morph=useRef<{stop:()=>void}|null>(null);
  // The hop plays once, on the change from working to done inside one mount. A mark that mounts already done sits still.
  const [done,setDone]=useState(false);
  useLayoutEffect(()=>{
    const changed=was.current!==working;
    was.current=working;
    if(!changed)return;
    if(working){setDone(false);return;}
    setDone(!reduced());
  },[working]);
  useEffect(()=>{
    const path=mouth.current;
    if(!path)return;
    morph.current?.stop();morph.current=null;
    const target=working?0:1;
    if(reduced()){at.current=target;path.setAttribute('d',mouthPath(target));return;}
    if(at.current===target){path.setAttribute('d',mouthPath(target));return;}
    morph.current=animate(at.current,target,{duration:MORPH_MS/1000,ease:EASE,onUpdate:value=>{at.current=value;path.setAttribute('d',mouthPath(value));}});
    return()=>{morph.current?.stop();morph.current=null;};
  },[working]);
  return <svg className={`dv-work ${className}`} width={size} height={size} viewBox={PEBBLE_BOX} data-working={working||undefined} data-done={done||undefined} data-motion={working?motionOf(step):undefined}
    role={label?'img':undefined} aria-label={label} aria-hidden={label?undefined:true} focusable="false" data-testid="working-mark">
    <g className="dv-work-whole">
      <path className="dv-work-body" d={PEBBLE}/>
      <g transform="rotate(-12 49 50)">
        <g className="dv-work-eyes"><ellipse cx="35" cy="39" rx="4" ry="5"/><ellipse cx="61" cy="37" rx="4" ry="5"/></g>
        <path ref={mouth} className="dv-work-mouth" d={mouthPath(working?0:1)} pathLength={1} fill="none" strokeWidth="6" strokeLinecap="round"/>
      </g>
    </g>
  </svg>;
}
