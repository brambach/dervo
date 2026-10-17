import {useEffect} from 'react';
import {BrandWindow,MomentMark} from './BrandMoment';
import {MICROTASK_HEADLINE,MICROTASK_WELCOME} from './Microtasks';
import './Welcome.css';
import {t} from '@/i18n';
import {betaCorner} from '@/local/platformWords';

/** The first thing a person ever sees: one idea, a lot of air, one action. Bryce, 21 Sep: "a really clean dervo like
 * pristine welcome... really clean, really attractive, with a lot of spacing". Until 30 Sep the idea was the line from the
 * 19 Sep router post (12K views): you're not the router between your agents anymore. Now it's the pattern itself, because
 * the founder uses Dervo for tiny tasks all day beside the work while testers try one big task and stop: keep Dervo open
 * beside you and fire off tiny tasks (Microtasks.tsx). The router line lives on in posts and replies, and putting it back
 * is one string. Either way it hands off to the first-run form, which is where the work starts.
 *
 * It sits before sign-in and the folder, ahead of everything, so the import's own opening beat ("You've been here before")
 * stays the only one inside setup. Moving it is one line in ConnectedApp.tsx.
 *
 * Seen once, never again: the flag is in localStorage beside `dervo-project` and `dervo-overview-waiting`, the app's other
 * per-Mac memory. It's UI memory, not a record, so it stays out of the store; and it survives quitting halfway through
 * setup, which is the one case where the projects count alone would show the screen twice. Enter and Escape both continue,
 * so nobody is made to read it. */

const SEEN_KEY='dervo-welcomed';
export function welcomeSeen():boolean{try{return localStorage.getItem(SEEN_KEY)==='1';}catch{return false;}}
export function rememberWelcomeSeen(){try{localStorage.setItem(SEEN_KEY,'1');}catch{/* Shown again next time, at worst. */}}

export function Welcome({onContinue}:{onContinue:()=>void}){
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(event.defaultPrevented||event.metaKey||event.ctrlKey||event.altKey)return;
      if(event.key==='Enter'||event.key==='Escape'){event.preventDefault();onContinue();}
    };
    window.addEventListener('keydown',key);
    return ()=>window.removeEventListener('keydown',key);
  },[onContinue]);
  return <BrandWindow corner={betaCorner()} label={t('Welcome')}>
    <div className="dv-moment dv-wm"><div className="dv-moment-col dv-wm-col">
      <MomentMark enter/>
      <h1 className="dv-display-hero dv-wm-in">{t(MICROTASK_HEADLINE)}</h1>
      <p className="dv-lede dv-wm-in">{t(MICROTASK_WELCOME)}</p>
      <div className="dv-moment-actions dv-wm-in">
        <button type="button" className="dv-btn dv-btn-primary dv-wm-go" onClick={onContinue}>{t('Get started')}
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8h9M8.5 4l4 4-4 4"/></svg>
        </button>
      </div>
    </div></div>
  </BrandWindow>;
}
