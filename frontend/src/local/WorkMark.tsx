import type {ReactNode} from 'react';
import './WorkMark.css';
import {t} from '@/i18n';

/** Work's mark: a briefcase and the word, never colour alone (b16-work design, rule 6). Personal stays unmarked, the way
 * Dervo already looks, so someone with only personal projects sees nothing new. */
export const BRIEFCASE=<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
 <path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/></svg>;

/** The tag beside a project's name: "Work". ``title`` says what it means on hover. */
export function WorkTag({className}:{className?:string}){
 return <span className={`dv-work-tag${className?` ${className}`:''}`} title={t('A Work project: it only runs on a work account')}>{BRIEFCASE}{t('Work')}</span>;
}

/** A name with the briefcase before it, for the picker chip and account names. */
export function WorkName({children}:{children:ReactNode}){
 return <span className="dv-work-name">{BRIEFCASE}{children}</span>;
}
