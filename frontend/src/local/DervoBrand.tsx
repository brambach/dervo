import {useLayoutEffect} from 'react';
import './DervoTokens.css';
import './DervoComponents.css';
import './DervoBrand.css';
import './DervoKit.css';
import {PEBBLE,PEBBLE_BOX} from './pebble';
import {applyPalette} from './dervoPalette';

/** Apply Dervo's palette to this local workspace, including its portaled dialogs. */
export function useDervoBrand(){
  useLayoutEffect(()=>{
    const root=document.documentElement;
    const previous=root.getAttribute('data-dervo-local');
    root.setAttribute('data-dervo-local','');
    applyPalette();
    return ()=>{if(previous===null)root.removeAttribute('data-dervo-local');else root.setAttribute('data-dervo-local',previous);applyPalette(null);};
  },[]);
}

/** The forest pebble, Dervo's one mark (design sweep, brand/logo.html): the landing page's smile with its outline's
 * small bumps halved, pine with a cream face, pale pine in dark mode as the primary button is. At 20px and under the face
 * is the one drawn on the 16px pixel grid (brand/mark.js), so the eyes and smile stay whole pixels. This mark holds still
 * and never sits above an error. The one place the face moves is the working smile (WorkingMark.tsx, #31, owner's call
 * on 19 Sep, overturning the earlier "never animates in the app's chrome"): while an agent works the thread shows the
 * pebble thinking, and it smiles once when the work ends. Colours are the tokens, so it follows the theme. */
export function DervoMark({size=28,className='',label}:{size?:number;className?:string;label?:string}){
  const small=size<=20;
  return <svg className={`dv-pebble ${className}`} width={size} height={size} viewBox={PEBBLE_BOX} data-face={small?'small':'regular'}
    role={label?'img':undefined} aria-label={label} aria-hidden={label?undefined:true} focusable="false">
    <path className="dv-pebble-body" d={PEBBLE}/>
    {small
      ?<g className="dv-pebble-face"><ellipse cx="36.5" cy="42.4" rx="6" ry="6.5"/><ellipse cx="60.5" cy="36.4" rx="6" ry="6.5"/><path d="M32.5 60Q53.5 77 66 52.5" fill="none" strokeWidth="10" strokeLinecap="round"/></g>
      :<g className="dv-pebble-face" transform="rotate(-12 49 50)"><ellipse cx="35" cy="39" rx="4" ry="5"/><ellipse cx="61" cy="37" rx="4" ry="5"/><path d="M30 56Q48 79 68 54" fill="none" strokeWidth="6" strokeLinecap="round"/></g>}
  </svg>;
}
