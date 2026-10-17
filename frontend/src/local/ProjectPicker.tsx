import {useRef,useState,type KeyboardEvent} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Archive,ChevronsUpDown,Plus,Search,Settings} from 'lucide-react';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuLabel,DropdownMenuSeparator,DropdownMenuSub,DropdownMenuSubContent,DropdownMenuSubTrigger,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {StatusGlyph} from './StatusGlyph';
import {projectName,type DisplayProject} from './projectDisplay';
import {WorkTag} from './WorkMark';
import {local} from './api';
import {shortDate} from './SettingsKit';
import {TidySettingMenu} from './TidySetting';
import './Surfaces.css';
import {t} from '@/i18n';

/** Project picker A (design sweep, 19 Sep, Bryce's pick): a fast menu. A filter, focused as it opens; each project with
 * its folder and what's waiting there; Add a project, Archived projects and Project settings. Typing finds archived
 * projects too, and Return restores one and opens it. Manage projects is gone: rename, archive and delete live on
 * Settings > General. */

export type ArchivedProject=DisplayProject&{archived_at?:number|null};
export type ProjectCounts=Record<string,{needs:number;running:number}>;

/** What a project row says on the right: what needs you there first, otherwise what's running. Nothing when the host
 * couldn't say, or when nothing's waiting. One figure, so a long name keeps its room. */
export function projectFigure(counts:ProjectCounts|undefined,id:string):string{
  const count=counts?.[id];
  if(!count)return '';
  if(count.needs>0)return t('{needs} needs you',{needs:count.needs});
  if(count.running>0)return t('{running} running',{running:count.running});
  return '';
}

/** The projects whose name or folder contains the words, open ones first, then archived ones. */
export function filterProjects<T extends DisplayProject>(projects:T[],query:string):T[]{
  const words=query.trim().toLowerCase();
  if(!words)return projects;
  return projects.filter(project=>projectName(project).toLowerCase().includes(words)||(project.cwd??'').toLowerCase().includes(words));
}

export const monogram=(project:DisplayProject)=>projectName(project).trim().charAt(0)||'·';
const shortFolder=(cwd?:string|null)=>!cwd?'':cwd.replace(/^\/Users\/[^/]+(?=\/|$)/,'~').replace(/^\/home\/[^/]+(?=\/|$)/,'~');
const archivedOn=(at?:number|null)=>typeof at==='number'&&at>1e9?t('archived {date}',{date:shortDate(at)}):t('archived');
/** Radix menus take onOpenAutoFocus at runtime, but DropdownMenuContent's type leaves it out: focus the filter, not the menu. */
export const openFocus=(target:()=>HTMLElement|null)=>({onOpenAutoFocus:(event:Event)=>{event.preventDefault();target()?.focus();}}) as object;
const TICK=<svg className="dv-tick" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>;

type Props={projects:DisplayProject[];projectId:string;loading:boolean;failed:boolean;onRetry:()=>void;onSelect:(id:string)=>void;
  onAdd:(trigger:HTMLElement)=>void;onSettings:(trigger:HTMLElement)=>void};

export function ProjectPicker({projects,projectId,loading,failed,onRetry,onSelect,onAdd,onSettings}:Props){
  const client=useQueryClient();
  const [open,setOpen]=useState(false),[query,setQuery]=useState('');
  const [restoring,setRestoring]=useState(''),[error,setError]=useState(''),[inFilter,setInFilter]=useState(false);
  const trigger=useRef<HTMLButtonElement>(null),filter=useRef<HTMLInputElement>(null),content=useRef<HTMLDivElement>(null);
  // Focus goes elsewhere when a row opens Settings or the folder panel; the menu mustn't pull it back to the button.
  const leaving=useRef(false);
  const selected=projects.find(project=>project.id===projectId);
  const archived=useQuery({queryKey:['archived-projects'],enabled:open,queryFn:()=>local<ArchivedProject[]>('projects_archived')});
  // Updated each time the menu opens; the rows say nothing when the host can't.
  const counts=useQuery({queryKey:['projects-activity'],enabled:open,staleTime:0,queryFn:()=>local<ProjectCounts>('projects_activity')});
  const shown=filterProjects(projects,query);
  const archivedShown=query.trim()?filterProjects(archived.data??[],query):[];
  const words=query.trim();
  // What Return in the filter opens: the first match, or with nothing typed the project you're in.
  const first:DisplayProject|ArchivedProject|undefined=words?shown[0]??archivedShown[0]:selected??shown[0];

  function change(next:boolean){
    setOpen(next);
    if(!next){setQuery('');setError('');}
  }
  async function restore(project:ArchivedProject){
    if(restoring)return;
    setRestoring(project.id);setError('');
    try{
      await local('project_restore',{projectId:project.id});
      await Promise.all([client.invalidateQueries({queryKey:['local-projects']}),client.invalidateQueries({queryKey:['archived-projects']})]);
      change(false);onSelect(project.id);
    }catch(failure){setError(t('{name} couldn’t be restored. {why}',{name:projectName(project),why:failure instanceof Error?failure.message:String(failure)}));}
    finally{setRestoring('');}
  }
  function choose(project:DisplayProject|ArchivedProject,archivedRow:boolean){
    if(archivedRow){void restore(project);return;}
    change(false);
    if(project.id!==projectId)onSelect(project.id);
  }
  function items(){return [...(content.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([data-disabled])')??[])];}
  function filterKeys(event:KeyboardEvent<HTMLInputElement>){
    if(event.key==='ArrowDown'){event.preventDefault();event.stopPropagation();items()[0]?.focus();return;}
    if(event.key==='Enter'){event.preventDefault();event.stopPropagation();if(first)choose(first,!shown.includes(first as DisplayProject));return;}
    // Everything else is typing: keep it from the menu's own type-ahead, which would move focus to a row.
    if(event.key!=='Escape'&&event.key!=='Tab')event.stopPropagation();
  }
  function menuKeys(event:KeyboardEvent<HTMLDivElement>){
    // A letter typed on a row goes to the filter, as the kit says.
    if(event.target!==filter.current&&event.key.length===1&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&event.key!==' '){
      event.preventDefault();setQuery(value=>value+event.key);filter.current?.focus();return;
    }
    // Up from the first row goes back to the filter.
    if(event.key==='ArrowUp'&&event.target===items()[0]){event.preventDefault();filter.current?.focus();}
  }
  const active=(id:string)=>inFilter&&first?.id===id?'':undefined;
  const leave=(run:(trigger:HTMLElement)=>void)=>()=>{leaving.current=true;if(trigger.current)run(trigger.current);};
  return <DropdownMenu open={open} onOpenChange={change}>
    <DropdownMenuTrigger asChild>
      <button ref={trigger} type="button" className="dv-sb-project dervo-sb-picker" aria-label={selected?t('Project {name}',{name:projectName(selected)}):t('Choose a project')}>
        {/* The project's own mark, its first letter on pine, as the mockups draw it. The pebble is for brand moments
            (startup, welcome, project created), not for every screen (design review 23 Sep, item 9). */}
        <span className="dv-mark dv-ptile" data-empty={selected?undefined:''} aria-hidden="true">{selected?monogram(selected):null}</span>
        <span className="dv-sb-project-copy">
          <b className="dv-sb-project-name"><span>{selected?projectName(selected):loading?t('Loading projects…'):failed?t('Projects unavailable'):t('Choose a project')}</span>{selected?.context==='work'&&<WorkTag/>}</b>
          <small title={selected?.cwd}>{selected?.cwd?shortFolder(selected.cwd):t('Switch or add a project')}</small>
        </span>
        <ChevronsUpDown className="dv-chev" size={14} aria-hidden="true"/>
      </button>
    </DropdownMenuTrigger>
    <DropdownMenuContent ref={content} side="bottom" align="start" loop aria-label={t('Projects')} className="dv-menu-picker" data-filtering={words?'':undefined}
      {...openFocus(()=>filter.current)} onKeyDown={menuKeys}
      onEscapeKeyDown={event=>{if(query){event.preventDefault();setQuery('');filter.current?.focus();}}}
      onCloseAutoFocus={event=>{if(leaving.current){event.preventDefault();leaving.current=false;}}}>
      <div className="dv-mfilter">
        <Search size={14} strokeWidth={1.6} aria-hidden="true"/>
        <input ref={filter} value={query} placeholder={t('Find a project')} aria-label={t('Find a project')} autoComplete="off" spellCheck={false}
          onChange={event=>setQuery(event.target.value)} onKeyDown={filterKeys} onFocus={()=>setInFilter(true)} onBlur={()=>setInFilter(false)}/>
        {words&&<span className="dv-mfilter-end" aria-hidden="true">{t('esc to clear')}</span>}
      </div>
      {loading&&<DropdownMenuItem disabled><StatusGlyph state="running"/><span className="dv-mi-t">{t('Loading projects…')}</span></DropdownMenuItem>}
      {failed&&<><p className="dv-mempty" role="alert">{t('Projects couldn’t load.')}</p>
        <DropdownMenuItem onSelect={event=>{event.preventDefault();onRetry();}}><span className="dv-mi-t">{t('Try again')}</span></DropdownMenuItem></>}
      {!loading&&!failed&&!projects.length&&!words&&<p className="dv-mempty">{t('No projects yet.')}</p>}
      {words&&!shown.length&&!loading&&<p className="dv-mempty" role="status">{archivedShown.length?t('No open project matches “{words}”.',{words}):t('No project matches “{words}”.',{words})}</p>}
      {shown.length>0&&<>
        {!words&&<DropdownMenuLabel>{t('Projects')}</DropdownMenuLabel>}
        {shown.map(project=>{
          const here=project.id===projectId,figure=here?'':projectFigure(counts.data,project.id);
          return <DropdownMenuItem key={project.id} className="dv-mi-two" aria-current={here?'true':undefined} data-active={active(project.id)}
            aria-label={figure?t('Open project {name}, {figure}',{name:projectName(project),figure}):t('Open project {name}',{name:projectName(project)})} onSelect={()=>choose(project,false)}>
            <span className="dv-pmark" aria-hidden="true">{monogram(project)}</span>
            <span className="dv-mi-t"><span className="dv-mi-work">{projectName(project)}{project.context==='work'&&<WorkTag/>}</span><small>{shortFolder(project.cwd)}</small></span>
            {(here||figure)&&<span className="dv-mi-end" aria-hidden="true">{here?TICK:<span className="dv-val">{figure}</span>}</span>}
          </DropdownMenuItem>;
        })}
      </>}
      {archivedShown.length>0&&<>
        <DropdownMenuLabel>{t('Archived')}</DropdownMenuLabel>
        {archivedShown.map(project=>archivedRow(project,true))}
      </>}
      {error&&<p className="dv-merror" role="alert">{error}</p>}
      <DropdownMenuSeparator/>
      <DropdownMenuItem onSelect={leave(onAdd)}><Plus size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Add a project…')}</span><span className="dv-mi-end" aria-hidden="true">⌘O</span></DropdownMenuItem>
      {!words&&(archived.data?.length??0)>0&&<DropdownMenuSub>
        <DropdownMenuSubTrigger><Archive size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Archived projects')}</span><span className="dv-mi-end" aria-hidden="true"><span className="dv-val">{archived.data!.length}</span></span></DropdownMenuSubTrigger>
        <DropdownMenuSubContent className="dv-menu-picker" aria-label={t('Archived projects')}>
          {archived.data!.map(project=>archivedRow(project,false))}
        </DropdownMenuSubContent>
      </DropdownMenuSub>}
      {!words&&<><DropdownMenuSeparator/>
        {selected&&<TidySettingMenu projectId={selected.id}/>}
        <DropdownMenuItem className={selected?undefined:'dv-mi-two'} disabled={!selected} onSelect={leave(onSettings)}>
          <Settings size={16} strokeWidth={1.5}/>
          {selected?<span className="dv-mi-t">{t('Project settings')}</span>:<span className="dv-mi-t"><span>{t('Project settings')}</span><small>{t('Choose a project first')}</small></span>}
          <span className="dv-mi-end" aria-hidden="true">⌘,</span>
        </DropdownMenuItem></>}
    </DropdownMenuContent>
  </DropdownMenu>;

  function archivedRow(project:ArchivedProject,filtered:boolean){
    const busy=restoring===project.id;
    return <DropdownMenuItem key={`archived:${project.id}`} className="dv-mi-two" data-active={filtered?active(project.id):undefined} disabled={!!restoring&&!busy}
      aria-label={t('Restore and open {name}',{name:projectName(project)})} onSelect={event=>{event.preventDefault();void restore(project);}}>
      {busy?<StatusGlyph state="running"/>:<span className="dv-pmark" aria-hidden="true">{monogram(project)}</span>}
      <span className="dv-mi-t"><span>{projectName(project)}</span><small>{[shortFolder(project.cwd),archivedOn(project.archived_at)].filter(Boolean).join(' · ')}</small></span>
      <span className="dv-mi-end" aria-hidden="true"><span className="dv-val">{busy?t('Restoring…'):filtered&&active(project.id)!==undefined?t('↵ Restore'):t('Restore')}</span></span>
    </DropdownMenuItem>;
  }
}
