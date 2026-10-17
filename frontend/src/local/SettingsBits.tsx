import type {ReactNode} from 'react';
import {StatusGlyph} from './StatusGlyph';
import './ProjectSettings.css';
import {t} from '@/i18n';

/** Small pieces the project settings pages share (project-settings/parts.css): an error under the control it's about,
 * with the action that fixes it; a plain list of consequences; a disclosure for the longer help. */

/** An error, in ink with the failed glyph, under the control it's about (interactions.md, "Errors"). */
export function FieldError({message,onRetry}:{message?:string;onRetry?:()=>void}){
  if(!message)return null;
  return <p className="dv-field-error" role="alert"><StatusGlyph state="failed"/><span>{message}</span>
    {onRetry&&<button type="button" className="dv-btn dv-btn-quiet" onClick={onRetry}>{t('Try again')}</button>}</p>;
}

/** A consequence stated before you say yes, one point per line. */
export function Points({items}:{items:ReactNode[]}){
  return <ul className="dv-points">{items.map((item,index)=><li key={index}>{item}</li>)}</ul>;
}

const CHEV_R=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;
/** Longer help behind a disclosure, never four paragraphs before the first control. */
export function Disclose({summary,children,className=''}:{summary:ReactNode;children:ReactNode;className?:string}){
  return <details className={`dv-disclose ${className}`}><summary>{CHEV_R}{summary}</summary><div className="dv-disclose-body">{children}</div></details>;
}
