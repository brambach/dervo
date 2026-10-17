import {useEffect,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {DervoMainHead} from './DervoShell';
import {SettingRow,SettingsSection} from './DervoControls';
import {overlayOpen} from './escapeToStop';
import {isProjectTerminalKey} from './terminalKeys';
import {matchesSettingsSearch,settingsScope,settingsSearchHelp,type SettingsDestination} from './settingsSearch';
import {AppIconPicker} from './AppIconPicker';
import {LanguageSetting} from './LanguageSetting';
import {useUpdateNotice} from './updateNotice';
import {t,tr} from '@/i18n';
import './DervoSettings.css';

/** Settings, one surface (design sweep, 19 Sep; settings/shell.html). Opening it swaps the app sidebar for the settings
 * nav on the same meadow wash and fills the main panel with one page: a serif title, one plain line, then the page's
 * sections, in Home's 700px column. Esc or the back link returns to where you were, with focus on what opened it.
 * The groups are the lead's reconciled list: the project's pages, then You, then Dervo. */

export type SettingsPage='model'|'access'|'checks'|'helpers'|'agents'|'cloud'|'general'|'account'|'preferences'|'accounts'|'updates'|'testers';
type Nav={page:SettingsPage;label:string;icon:ReactNode};

const icon=(d:ReactNode)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{d}</svg>;
const ICONS={
  back:icon(<path d="M9.5 3.5L5 8l4.5 4.5"/>),
  search:icon(<><circle cx="6.8" cy="6.8" r="4.1"/><path d="M9.8 9.8l3.5 3.5"/></>),
  // A chip, not a burst: eight rays read as a loading spinner and sat two rows above Preferences' sun (review 23 Sep).
  model:icon(<><rect x="4.5" y="4.5" width="7" height="7" rx="1.5"/><path d="M6.5 2.5v2M9.5 2.5v2M6.5 11.5v2M9.5 11.5v2M2.5 6.5h2M2.5 9.5h2M11.5 6.5h2M11.5 9.5h2"/></>),
  access:icon(<path d="M8 2.2l4.6 1.7v3.6c0 2.9-2 4.9-4.6 6-2.6-1.1-4.6-3.1-4.6-6V3.9z"/>),
  checks:icon(<><circle cx="8" cy="8" r="5.6"/><path d="M5.7 8.2l1.6 1.6 3-3.3"/></>),
  helpers:icon(<><circle cx="6" cy="5.8" r="2.1"/><path d="M2.4 13c.4-2.2 1.9-3.4 3.6-3.4s3.2 1.2 3.6 3.4"/><circle cx="11.3" cy="6.4" r="1.7"/><path d="M11.2 9.5c1.4 0 2.4 1 2.6 2.7"/></>),
  agents:icon(<><circle cx="8" cy="8" r="5.6"/><path d="M10.2 8a2.2 2.2 0 1 1-.6-1.5V9a1.1 1.1 0 0 0 2.2 0V8"/></>),
  cloud:icon(<path d="M4.6 12.5h7a2.6 2.6 0 00.4-5.2 3.6 3.6 0 00-6.9-.9A2.9 2.9 0 004.6 12.5z"/>),
  general:icon(<path d="M2.5 4.5h3.8l1.4 1.5h5.8v6.5h-11z"/>),
  account:icon(<><circle cx="8" cy="5.6" r="2.6"/><path d="M3 13.5c.8-2.4 2.7-3.6 5-3.6s4.2 1.2 5 3.6"/></>),
  preferences:icon(<><circle cx="8" cy="8" r="2.2"/><path d="M8 2.5v1.6M8 11.9v1.6M2.5 8h1.6M11.9 8h1.6M4.1 4.1l1.1 1.1M10.8 10.8l1.1 1.1M4.1 11.9l1.1-1.1M10.8 5.2l1.1-1.1"/></>),
  accounts:icon(<path d="M6 2.5v3M10 2.5v3M4.5 5.5h7V8a3.5 3.5 0 01-7 0zM8 11.5v2"/>),
  updates:icon(<><circle cx="8" cy="8" r="5.5"/><path d="M8 5v5.2M5.9 8.2L8 10.3l2.1-2.1"/></>),
  testers:icon(<><circle cx="6.5" cy="5.8" r="2.2"/><path d="M2.6 13c.4-2.2 1.9-3.4 3.9-3.4 1 0 1.9.3 2.6.9"/><path d="M10.2 11.4l1.3 1.3 2.3-2.6"/></>),
  feedback:icon(<path d="M3 3.5h10v7H7.2L4.5 13v-2.5H3z"/>),
};

export const PROJECT_PAGES:Nav[]=[
  {page:'model',label:t('Account and model'),icon:ICONS.model},{page:'access',label:t('Access'),icon:ICONS.access},
  {page:'checks',label:t('Checks and review'),icon:ICONS.checks},{page:'helpers',label:t('Helper agents'),icon:ICONS.helpers},
  {page:'agents',label:t('Agents and routines'),icon:ICONS.agents},
  {page:'cloud',label:t('Send to cloud'),icon:ICONS.cloud},
  {page:'general',label:t('General'),icon:ICONS.general},
];
export const YOU_PAGES:Nav[]=[{page:'account',label:t('Account'),icon:ICONS.account},{page:'preferences',label:t('Preferences'),icon:ICONS.preferences}];
export const DERVO_PAGES:Nav[]=[{page:'accounts',label:t('Connected accounts'),icon:ICONS.accounts},{page:'updates',label:t('Updates'),icon:ICONS.updates},
  {page:'testers',label:t('Testers'),icon:ICONS.testers}];
const PROJECT=new Set<SettingsPage>(PROJECT_PAGES.map(nav=>nav.page));
export const isProjectPage=(page:SettingsPage)=>PROJECT.has(page);

/** Each page's title and its one line, from the area mockups. */
export function pageHead(page:SettingsPage,project:string,folder?:string):{title:string;lead:ReactNode}{
  switch(page){
    case 'model':return {title:t('Account and model'),lead:t('What new threads in {project} start with. Any thread can switch from its model picker.',{project})};
    case 'access':return {title:t('Access'),lead:folder?tr('What agents can read, change and run in {folder}.',{folder:<code>{folder}</code>}):t('What agents can read, change and run in {project}.',{project})};
    case 'checks':return {title:t('Checks and review'),lead:t('What happens when an agent finishes a change in {project}, in order. A thread can use its own checks and review.',{project})};
    case 'helpers':return {title:t('Helper agents'),lead:t('Agents available to help in {project}. Each works in its own thread, on its own account.',{project})};
    case 'agents':return {title:t('Agents and routines'),lead:t('Agents you @mention in {project}, routines that run them on a schedule, and when Home tells you a thread needs you.',{project})};
    case 'cloud':return {title:t('Send to cloud'),lead:t('Run a task in Claude’s or Codex’s own cloud on {project}’s account, and bring the result back as a branch.',{project})};
    case 'general':return {title:t('General'),lead:t('This project’s name and folder, and what happens when you’re done with it.')};
    case 'account':return {title:t('Account'),lead:t('Your Dervo sign-in and beta access. Your projects and threads stay on this Mac.')};
    case 'preferences':return {title:t('Preferences'),lead:t('How Dervo looks, and when it tells you something, on this Mac.')};
    case 'accounts':return {title:t('Connected accounts'),lead:t('Provider accounts connected on this Mac. A work project only runs on a work account and a personal project only on a personal one, so your employer’s code never lands on your personal subscription.')};
    case 'updates':return {title:t('Updates'),lead:t('Which version of Dervo you’re on, and newer betas when they’re out.')};
    case 'testers':return {title:t('Testers'),lead:t('Who can use the private beta. Only admins see this page.')};
  }
}

/** Every way into Settings and the page it opens on. The project picker opens the project's first page, the account menu
 * opens Account (settings/interactions.md, project-settings/interactions.md). The picker's "Set up a reviewer" opens
 * Checks and review, where the second review lives. */
export type SettingsEntry='project-picker'|'account-menu'|'palette-project'|'palette-settings'|'usage-manage'|'account-notice'|'first-run-account'|'change-model'|'review-setup'|'connection';
export const SETTINGS_ENTRY:Record<SettingsEntry,SettingsPage>={
  'project-picker':'model','account-menu':'account','palette-project':'model','palette-settings':'account','usage-manage':'accounts',
  'account-notice':'account','first-run-account':'account','change-model':'model','review-setup':'checks','connection':'accounts',
};

/** Whether Settings is open, on which page, and what to give focus back to. A page is remembered while Settings is
 * open; each entry point says where it lands. */
export function useSettingsSurface(){
  const [page,setPage]=useState<SettingsPage|null>(null);
  const trigger=useRef<HTMLElement|null>(null);
  function open(entry:SettingsEntry,from?:HTMLElement|null){
    trigger.current=from??(document.activeElement instanceof HTMLElement&&document.activeElement!==document.body?document.activeElement:null);
    setPage(SETTINGS_ENTRY[entry]);
  }
  function close(){
    const target=trigger.current;trigger.current=null;
    setPage(null);
    requestAnimationFrame(()=>{if(target?.isConnected&&!target.closest('[hidden],[data-away]'))target.focus();});
  }
  return {page,open,close,go:setPage};
}

/** Esc goes back, unless something else wants it: an open menu, dialog or popover (checked before Radix closes it, in
 * the capture phase), a field that put its value back (it prevents the default), or a field that has focus, which Esc
 * leaves first. So Esc in a field leaves the field, and the next Esc leaves Settings. */
export function useSettingsEscape(active:boolean,back:()=>void){
  const latest=useRef(back);latest.current=back;
  useEffect(()=>{
    if(!active)return;
    let overlay=false;
    const capture=(event:globalThis.KeyboardEvent)=>{if(event.key==='Escape')overlay=overlayOpen();};
    const bubble=(event:globalThis.KeyboardEvent)=>{
      if(event.key!=='Escape'||event.isComposing||event.repeat||event.defaultPrevented||overlay||isProjectTerminalKey(event))return;
      const target=event.target instanceof HTMLElement?event.target:null;
      if(target&&(target.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))){
        if(!target.classList.contains('dv-input')){target.blur();return;}
      }
      event.preventDefault();
      latest.current();
    };
    window.addEventListener('keydown',capture,true);
    window.addEventListener('keydown',bubble);
    return ()=>{window.removeEventListener('keydown',capture,true);window.removeEventListener('keydown',bubble);};
  },[active]);
}

type NavProps={page:SettingsPage;onPage:(page:SettingsPage)=>void;onBack:()=>void;onFeedback:()=>void;projectName?:string;admin:boolean;testersCount?:number};

/** The settings sidebar: the way back, then the project's pages, You and Dervo. The rows are one tab stop; ↑ ↓ move,
 * Home and End jump, Return opens. Testers shows only for admins, so nobody meets a row they can't use. */
export function SettingsNav({page,onPage,onBack,onFeedback,projectName,admin,testersCount}:NavProps){
  const rows=useRef<Partial<Record<SettingsDestination,HTMLButtonElement|null>>>({});
  const search=useRef<HTMLInputElement>(null);
  const [query,setQuery]=useState('');
  const searching=query.trim().length>0;
  const available:{id:string;label:string;items:Nav[]}[]=[
    ...(projectName?[{id:'project',label:projectName,items:PROJECT_PAGES}]:[]),
    {id:'you',label:t('You'),items:YOU_PAGES},
    {id:'dervo',label:'Dervo',items:DERVO_PAGES.filter(nav=>nav.page!=='testers'||admin)},
  ];
  const groups=available.map(group=>({...group,items:group.items.filter(nav=>matchesSettingsSearch(nav.page,query))}));
  const feedback=matchesSettingsSearch('feedback',query);
  const flat:SettingsDestination[]=[...groups.flatMap(group=>group.items.map(nav=>nav.page)),...(feedback?['feedback' as const]:[])];
  const [focused,setFocused]=useState<SettingsDestination|null>(page);
  // A newer build (beta.15, group 9): a dot on Updates until you're on it.
  const update=useUpdateNotice().dot;
  const focusAt=focused&&flat.includes(focused)?focused:!searching&&flat.includes(page)?page:flat[0];
  useEffect(()=>{setFocused(page);},[page]);
  function keys(event:KeyboardEvent,index:number){
    if(event.nativeEvent.isComposing)return;
    if(event.key==='Escape'&&searching){event.preventDefault();setQuery('');search.current?.focus();return;}
    const last=flat.length-1;
    const next=event.key==='ArrowDown'?Math.min(last,index+1):event.key==='ArrowUp'?Math.max(0,index-1):event.key==='Home'?0:event.key==='End'?last:-1;
    if(next<0)return;
    event.preventDefault();setFocused(flat[next]);rows.current[flat[next]]?.focus();
  }
  function searchKeys(event:KeyboardEvent<HTMLInputElement>){
    if(event.nativeEvent.isComposing)return;
    if(event.key==='Escape'){
      event.preventDefault();
      if(searching)setQuery('');else event.currentTarget.blur();
      return;
    }
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault();
      const next=event.key==='ArrowDown'?flat[0]:flat.at(-1);
      if(next){setFocused(next);rows.current[next]?.focus();}
    }else if(event.key==='Enter'&&searching&&flat[0]){
      event.preventDefault();rows.current[flat[0]]?.click();
    }
  }
  const row=(key:SettingsDestination,label:string,glyph:ReactNode,meta?:ReactNode)=>{
    return <button key={key} ref={element=>{rows.current[key]=element;}} type="button" className="dv-sb-item" tabIndex={key===focusAt?0:-1}
      aria-label={label} aria-describedby={searching?`dv-settings-scope-${key}`:undefined}
      aria-current={key===page?'page':undefined} onKeyDown={event=>keys(event,flat.indexOf(key))} onFocus={()=>setFocused(key)}
      onClick={()=>key==='feedback'?onFeedback():onPage(key)}>{glyph}<span className="dv-name">{label}{searching&&<span className="dv-settings-result-scope" id={`dv-settings-scope-${key}`}>{settingsScope(key,projectName??t('this project'))}</span>}</span>{meta}</button>;
  };
  return <nav className="dv-sb dv-sb-settings" aria-label={t('Settings')} data-testid="dervo-settings-nav">
    <button type="button" className="dv-sb-item dv-sb-back" onClick={onBack}>{ICONS.back}<span className="dv-name">{projectName?t('Back to {projectName}',{projectName}):t('Back')}</span><kbd className="dv-kbd" aria-hidden="true">esc</kbd></button>
    <label className="dv-settings-search">{ICONS.search}<input ref={search} className="dv-input" type="search" aria-label={t('Search settings')} placeholder={t('Search settings')} value={query}
      autoComplete="off" spellCheck={false} onChange={event=>{setQuery(event.target.value);setFocused(null);}} onKeyDown={searchKeys}/></label>
    {searching&&<p className="dv-settings-search-status" role="status">{flat.length?t('{count} results',{count:flat.length}):t('No matching settings.')}</p>}
    {searching&&!flat.length&&<p className="dv-settings-search-help">{settingsSearchHelp(query,!!projectName)}</p>}
    {groups.filter(group=>group.items.length||(group.id==='dervo'&&feedback)).map(group=><div key={group.id} role="group" aria-label={group.label} className="dv-sb-group">
      <div className="dv-sb-group-head" aria-hidden="true"><span className="dv-label">{group.label}</span></div>
      {group.items.map(nav=>row(nav.page,nav.label,nav.icon,nav.page==='testers'&&testersCount?<span className="dv-meta">{testersCount}</span>
        :nav.page==='updates'&&update?<span className="dv-update-dot" role="img" aria-label={t('A newer version is ready')}/>:undefined))}
      {group.id==='dervo'&&feedback&&row('feedback',t('Send feedback'),ICONS.feedback)}
    </div>)}
  </nav>;
}

/** The main panel while Settings is open: the 52px band that's the window's drag area, then the page in Home's column. */
export function SettingsFrame({page,projectName,folder,children}:{page:SettingsPage;projectName:string;folder?:string;children:ReactNode}){
  const title=useRef<HTMLHeadingElement>(null),column=useRef<HTMLDivElement>(null),section=useRef<HTMLElement>(null);
  const head=pageHead(page,projectName,folder);
  // A new page starts at its top, and its title takes focus so a screen reader reads where you are. When a menu opened
  // Settings, the title waits for the menu to close, or the menu's focus trap takes focus back. Something on the page
  // that already has focus (a field an entry point asked for) keeps it.
  useEffect(()=>{
    if(column.current)column.current.scrollTop=0;
    let frame=0,tries=0;
    const place=()=>{
      if(overlayOpen()&&tries++<40){frame=requestAnimationFrame(place);return;}
      const active=document.activeElement;
      if(active&&active!==title.current&&section.current?.contains(active))return;
      title.current?.focus({preventScroll:true});
    };
    place();
    return ()=>cancelAnimationFrame(frame);
  },[page]);
  return <section ref={section} className="dv-settings" aria-labelledby="dv-settings-title" data-testid="dervo-settings" data-page={page}>
    <DervoMainHead/>
    <div className="dv-column" ref={column}><div className="dv-measure">
      <header className="dv-page-head"><p className="dv-label dv-settings-page-scope">{settingsScope(page,projectName)}</p><h1 id="dv-settings-title" ref={title} tabIndex={-1} className="dv-page-title">{head.title}</h1><p className="dv-page-lead">{head.lead}</p></header>
      {/* Language (beta.17) leads Preferences, so someone who can't read the rest finds it first. */}
      {page==='preferences'&&<LanguageSetting/>}
      {children}
      {/* The Mac icon picker (beta.15, group 7), after Theme. */}
      {page==='preferences'&&<AppIconPicker/>}
      {page==='preferences'&&<SettingsSection title={t('Motion')} id="dv-pref-motion"><SettingRow name={t('Reduce motion')} line={t('Dervo follows your Mac’s Reduce motion setting. To change it, open System Settings, then Accessibility, Display, and Reduce motion.')}/></SettingsSection>}
    </div></div>
  </section>;
}
