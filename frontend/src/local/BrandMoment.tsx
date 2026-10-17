import {useEffect,useState,type ReactNode} from 'react';
import {DervoMark} from './DervoBrand';
import './BrandMoment.css';
import {t} from '@/i18n';

/** The brand moments (design sweep, brand/): startup, first run, welcome and project created. The window is paper with
 * the meadow photo across its top, and the forest pebble is the one mark. It settles in once (`enter`), rocks slowly only
 * while real work is happening (`attending`), and never sits above an error: the failure pages use the small lockup. */

/** The opening screen appears only when loading takes longer than this, counted from when the page started, so the
 * startup gate and the projects load share one clock and one screen. Before it, the window is plain paper. */
export const OPENING_DELAY=600;
/** The clock the delay is measured on. Tests replace `now`. */
export const openingClock={now:()=>performance.now()};

/** True once `delay` ms have passed since the page started. */
export function useAfterStart(delay=OPENING_DELAY){
  const [shown,setShown]=useState(()=>openingClock.now()>=delay);
  useEffect(()=>{
    if(shown)return;
    const timer=setTimeout(()=>setShown(true),Math.max(0,delay-openingClock.now()));
    return ()=>clearTimeout(timer);
  },[shown,delay]);
  return shown;
}

/** A full window for a brand moment: the drag band along the top, the meadow unless an error leads, and the corner
 * caption. `lockup` puts the small mark and name top left, where the failure pages keep it. */
export function BrandWindow({children,meadow=true,corner,lockup=false,label}:{children?:ReactNode;meadow?:boolean;corner?:ReactNode;lockup?:boolean;label?:string}){
  return <main className="dv-brand" data-meadow={meadow?'photo':undefined} aria-label={label}>
    <div className="dv-brand-drag" data-tauri-drag-region aria-hidden="true"/>
    {lockup&&<div className="dv-brand-lockup"><DervoMark size={20}/>Dervo</div>}
    {corner&&<span className="dv-brand-corner">{corner}</span>}
    {children}
  </main>;
}

/** The mark at a brand moment. `enter` settles it in once; `attending` rocks it while work is really happening. */
export function MomentMark({size=56,enter=false,attending=false}:{size?:number;enter?:boolean;attending?:boolean}){
  return <span className={`dv-moment-mark${enter?' dv-enter':''}${attending?' dv-attending':''}`} style={{width:size,height:size}}><DervoMark size={size}/></span>;
}

/** One "Opening" screen for the whole launch: the startup gate while the local workspace starts, then the app while its
 * projects load. Blank paper until OPENING_DELAY, then the rocking mark and one caption. */
export function OpeningScreen({caption=t('Opening your workspace')}:{caption?:string}){
  const shown=useAfterStart();
  return <BrandWindow label="Dervo">
    {shown&&<div className="dv-opening" data-testid="dv-opening"><MomentMark attending/><p className="dv-caption" role="status">{caption}</p></div>}
  </BrandWindow>;
}

/** The raw detail behind a quiet disclosure, with the same +/− as the rest of the app. */
export function DetailBlock({detail,open=false}:{detail:string;open?:boolean}){
  return <details className="dv-brand-details" open={open}><summary>{t('Details')}</summary><pre>{detail}</pre></details>;
}
