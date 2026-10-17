import {useRef,type KeyboardEvent} from 'react';
import {MomentMark} from './BrandMoment';
import {projectName} from './projectDisplay';
import {shortPath} from './DervoSidebar';
import type {DisplayProject as Project} from './projectDisplay';
import './WorkspaceWelcome.css';
import {t} from '@/i18n';

/** The main area when no project is open (design sweep, brand/welcome-*). Two states: your saved projects, listed right
 * here to open, or the list that didn't load, with the error leading and no mark above it. While a project's threads
 * load, the area stays quiet: the one opening screen is the startup one. With no projects at all, first run shows
 * instead, and with a project open, Home does, so neither is drawn here. */
type Props={loading:boolean;failed:boolean;detail?:string;projects:Project[];onOpen:(id:string)=>void;onAdd:(trigger:HTMLElement)=>void;onRetry:()=>void};
export function WorkspaceWelcome({loading,failed,detail,projects,onOpen,onAdd,onRetry}:Props){
  const rows=useRef<(HTMLButtonElement|null)[]>([]);
  if(loading)return <section className="dv-welcome" aria-label={t('Opening project')} aria-busy="true"><p className="sr-only" role="status">{t('Opening your project')}</p></section>;
  if(failed)return <section className="dv-welcome dv-welcome-failed" aria-label={t('Projects didn’t load')}><div className="dv-welcome-col">
    <h1 className="dv-display-2">{t('Your projects didn’t load')}</h1>
    <p className="dv-lede">{t('They’re still saved on this Mac. Dervo’s local workspace didn’t answer when it was asked for the list.')}</p>
    <div className="dv-moment-actions"><button type="button" className="dv-btn dv-btn-primary" onClick={onRetry}>{t('Try again')}</button></div>
    {detail&&<details className="dv-brand-details"><summary>{t('Details')}</summary><pre>{detail}</pre></details>}
  </div></section>;
  function keys(event:KeyboardEvent<HTMLButtonElement>,index:number){
    const next=event.key==='ArrowDown'?index+1:event.key==='ArrowUp'?index-1:event.key==='Home'?0:event.key==='End'?projects.length-1:-1;
    if(next<0||next>=projects.length)return;
    event.preventDefault();rows.current[next]?.focus();
  }
  return <section className="dv-welcome" aria-label={t('Choose a project')}><div className="dv-welcome-col">
    <MomentMark size={48} enter/>
    <h1 className="dv-display-1">{t('Pick up where you left off')}</h1>
    <p className="dv-lede">{t('Choose a project to open its threads and knowledge.')}</p>
    <div className="dv-sheet dv-welcome-list" role="list">
      {projects.map((project,index)=><div role="listitem" key={project.id}><button type="button" ref={element=>{rows.current[index]=element;}} className="dv-row"
        onClick={()=>onOpen(project.id)} onKeyDown={event=>keys(event,index)}>
        <span className="dv-welcome-row"><span className="dv-row-name">{projectName(project)}</span><span className="dv-row-line">{shortPath(project.cwd)}</span></span>
        <span className="dv-row-end" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></svg></span>
      </button></div>)}
    </div>
    <div className="dv-moment-actions"><button type="button" className="dv-btn dv-btn-ghost" onClick={event=>onAdd(event.currentTarget)}>{t('Add a project')}</button></div>
  </div></section>;
}
