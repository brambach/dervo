import {useEffect,useRef,useState,type ReactNode} from 'react';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {RunCommand} from '@/shared/ui/markdown/ReplyChips';
import './SettingsPages.css';
import {formatDate,t} from '@/i18n';

/** The parts the Settings pages share (design sweep, 19 Sep; settings/settings.css): the one open card, an in-place
 * confirm, an add row, a usage meter, a command with Copy, and a disclosure. The look is SettingsPages.css. */

/** The one thing being filled in or confirmed on a page, lifted onto a card. Esc inside it closes it. */
export function OpenCard({title,caption,children,onEscape,label}:{title?:ReactNode;caption?:ReactNode;children:ReactNode;onEscape?:()=>void;label?:string}){
  const id=useRef(`dv-card-${Math.random().toString(36).slice(2,8)}`).current;
  return <div className="dv-open-card" role="group" aria-labelledby={title?id:undefined} aria-label={title?undefined:label}
    onKeyDown={event=>{if(event.key==='Escape'&&onEscape&&!event.defaultPrevented){event.preventDefault();event.stopPropagation();onEscape();}}}>
    {title&&<div className="dv-card-head"><h3 className="dv-title" id={id}>{title}</h3>{caption&&<span className="dv-caption">{caption}</span>}</div>}
    {children}
  </div>;
}

/** A destructive action asked in place: what happens, the way out first, then the verb. Focus starts on the way out. */
export function Confirm({title,line,keep,act,busy,busyLabel,onKeep,onAct}:{title:ReactNode;line:ReactNode;keep:string;act:string;busy?:boolean;busyLabel?:string;onKeep:()=>void;onAct:()=>void}){
  const stay=useRef<HTMLButtonElement>(null);
  useEffect(()=>{stay.current?.focus();},[]);
  return <div className="dv-confirm" role="group" aria-label={typeof title==='string'?title:undefined}
    onKeyDown={event=>{if(event.key==='Escape'&&!busy){event.preventDefault();event.stopPropagation();onKeep();}}}>
    <div><p className="dv-set-name">{title}</p><p className="dv-set-line">{line}</p></div>
    <div className="dv-set-end"><button ref={stay} type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={onKeep}>{keep}</button>
      <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy} onClick={onAct}>{busy?busyLabel??act:act}</button></div>
  </div>;
}

const PLUS=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;
/** "Add a Claude account", "Link two accounts": a quiet row that opens its card in place. */
export function AddRow({children,onClick,disabled,title}:{children:ReactNode;onClick:()=>void;disabled?:boolean;title?:string}){
  return <button type="button" className="dv-add-row" disabled={disabled} title={title} onClick={onClick}>{PLUS}<span>{children}</span></button>;
}

/** What's left, as a bar and a number, with one caption line. No bar when the provider hasn't reported one. */
export function Meter({percent,value,caption,title}:{percent:number|null;value?:string;caption:ReactNode;title?:string}){
  if(percent===null)return <div className="dv-meter dv-none" title={title}><small>{caption}</small></div>;
  return <div className="dv-meter" title={title}>
    <span className="dv-bar" aria-hidden="true"><i style={{width:`${Math.min(100,Math.max(0,percent))}%`}}/></span>
    <span className="dv-pct">{value}</span>{caption!==''&&<small>{caption}</small>}
  </div>;
}

/** A command to run in Terminal, with Copy, and (beta.15) Run, which types it into the open project's terminal. Run
 * shows only with a project open; one that installs or changes files asks first. "Copied" shows for 1.6 seconds. */
export function CopyCommand({command,label=t('Copy')}:{command:string;label?:string}){
  const [copied,setCopied]=useState(false);
  useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),1600);return ()=>clearTimeout(timer);},[copied]);
  async function copy(){
    try{await navigator.clipboard.writeText(command);setCopied(true);}
    catch{/* The command stays selectable when the clipboard isn't available. */}
  }
  return <div className="dv-cmd" data-runnable=""><code>{command}</code>
    <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={()=>void copy()} aria-label={copied?t('Copied'):`${label} ${command}`}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M3.5 10.5h-.5a1 1 0 01-1-1V3a1 1 0 011-1h6.5a1 1 0 011 1v.5"/></svg>
      {copied?t('Copied'):label}</button>
    <RunCommand command={command} from={t('From Settings')}/></div>;
}

const CHEVRON=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;
/** Longer help behind a disclosure, never paragraphs before the first control. */
export function Disclosure({summary,children,className='',open,onToggle}:{summary:ReactNode;children:ReactNode;className?:string;open?:boolean;onToggle?:(open:boolean)=>void}){
  return <details className={`dv-disclose ${className}`} open={open} onToggle={event=>onToggle?.((event.currentTarget as HTMLDetailsElement).open)}>
    <summary>{summary}{CHEVRON}</summary><div className="dv-disclose-body">{children}</div>
  </details>;
}

/** A status word with its glyph, shown once on the row it's about. */
export function StatusWord({state,children,accent}:{state:GlyphState;children:ReactNode;accent?:boolean}){
  return <span className="dv-status" data-state={state}><StatusGlyph state={state} accent={accent}/>{children}</span>;
}

/** "Saved" beside a field for two seconds after it saves. */
export function useSaved():[boolean,()=>void]{
  const [saved,setSaved]=useState(false);
  useEffect(()=>{if(!saved)return;const timer=setTimeout(()=>setSaved(false),2000);return ()=>clearTimeout(timer);},[saved]);
  return [saved,()=>setSaved(true)];
}

/** A date for a person, in a sentence: "today, 7:40 AM", "18 Sep, 11:02 PM", or without the time "3 Oct". */
export function dayLabel(seconds:number,{time=true,now=Date.now()}:{time?:boolean;now?:number}={}):string{
  const when=new Date(seconds*1000),today=new Date(now);
  const clock=formatDate(when,{hour:'numeric',minute:'2-digit'},'en-US');
  const day=when.toDateString()===today.toDateString()?t('today'):shortDate(seconds,now);
  return time?`${day}, ${clock}`:day;
}
const MONTHS=[t('Jan'),t('Feb'),t('Mar'),t('Apr'),t('May'),t('Jun'),t('Jul'),t('Aug'),t('Sep'),t('Oct'),t('Nov'),t('Dec')];
/** "18 Sep", or "18 Sep 2025" in another year. Spelled out here: en-GB now writes "Sept". */
export function shortDate(seconds:number,now=Date.now()):string{
  const when=new Date(seconds*1000);
  return `${when.getDate()} ${MONTHS[when.getMonth()]}${when.getFullYear()!==new Date(now).getFullYear()?` ${when.getFullYear()}`:''}`;
}
/** The same, starting a line: "Today, 9:12 AM". */
export const dayStart=(seconds:number,now=Date.now())=>{const text=dayLabel(seconds,{now});return text.charAt(0).toUpperCase()+text.slice(1);};
