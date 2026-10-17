/* Preview (beta.11, panel direction C, notes/PLAN-panels.md). A thread's second column shows Progress or Preview, never
 * both. Preview shows what an agent made: an HTML file it wrote in the project, the images it wrote there, Markdown and
 * PDFs, or a local page. Beta.15 (group 1): files an agent made with shell commands come from git status after each run,
 * a path or local page in a reply opens here with one click, and a local page an agent started is offered with Open.
 * Nothing opens by itself. No terminal.
 *
 * Isolation, in short (the full reasoning is under "Preview security" in PLAN-panels.md): project files come from the
 * native dervo-preview:// scheme (src/preview.rs), never the app's origin, and every page sits in an iframe sandboxed
 * without allow-same-origin, so it can't reach this document, the Tauri bridge, cookies or storage. Nothing here listens
 * for messages from the frame. A local page is only ever one you typed, on localhost or 127.0.0.1 with a port. */
import {useEffect,useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {useQuery} from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {local as hostCall} from './api';
import {localPageIn,previewKind,type PreviewTarget} from '@/shared/ui/markdown/replyActions';
import {useSidebar} from '@/shared/ui/sidebar';
import type {CodingTask} from './CodingTasks';
import {isProjectTerminalKey} from './terminalKeys';
import './PreviewPane.css';
import {t} from '@/i18n';

export type ColumnView='progress'|'preview';
export type PreviewTab='mockup'|'screenshots'|'docs'|'page';
export type PreviewFile={path:string;kind:'page'|'image'|'markdown'|'pdf';writes:number;at:number};
/** A path or page a reply asked Preview to show, with when, so the same one clicked twice shows again. */
export type PreviewRequest={target:PreviewTarget;at:number};

const WRITES=new Set(['dervo_write_file','dervo_edit_file','dervo_create_file','native:Edit','native:Write']);
/** The width a mockup is drawn at before it's scaled to the column: Bryce reviews at 1052. */
export const PAGE_WIDTH=1052;
/** Under this window width, opening Preview folds the sidebar so the thread and Preview split what's left evenly. */
export const FOLD_UNDER=1240;

/** A project-relative path the preview scheme would serve: no absolute path, no dot names, no backslashes. The native
 * side checks again; this only keeps paths it would refuse out of the list. */
export function servablePath(path:unknown):path is string{
 if(typeof path!=='string'||!path||path.length>1024||path.startsWith('/')||path.includes('\\')||path.includes('\0'))return false;
 return path.split('/').every(part=>part.length>0&&!part.startsWith('.'));
}

/** What this thread's agents wrote that Preview can show, newest first. From each run's recorded writes, which count
 * toward reloading, each result's changed files, and (beta.15) what git reported changed since the thread's first run,
 * which catches files made with shell commands. A screenshot in the run's own temporary folder isn't here: it's gone
 * when the run ends (B2 in PLAN-native.md). */
export function previewFiles(tasks:CodingTask[],changed:{path:string;at:number}[]=[]):PreviewFile[]{
 const found=new Map<string,PreviewFile>();
 const note=(path:unknown,at:number,write:boolean)=>{
  if(!servablePath(path))return;
  const kind=previewKind(path);
  if(!kind)return;
  const entry=found.get(path)??{path,kind,writes:0,at:0};
  if(write)entry.writes+=1;
  entry.at=Math.max(entry.at,at);
  found.set(path,entry);
 };
 for(const task of tasks){
  for(const token of task.tokens??[]){
   if(!WRITES.has(token.tool)||token.state!=='delivered'||token.success===false)continue;
   // "screenshot <name>" is Dervo's label for a file in the run's temporary folder, not a project path.
   if(typeof token.path==='string'&&/^screenshot [^/]+$/.test(token.path))continue;
   note(token.path,token.ended_at??token.started_at??0,true);
  }
  for(const path of task.completion?.changed_files??[])note(path,task.completion?.at??0,false);
 }
 for(const file of changed)note(file.path,file.at,false);
 return [...found.values()].sort((a,b)=>b.at-a.at||a.path.localeCompare(b.path));
}

/** What you typed as a local page, as the URL to frame: a port, `localhost:<port>`, `127.0.0.1:<port>`, or either with
 * `http://` and a path. Null for anything else, including every remote address. Mirrors preview.rs `local_page`. */
export function localPageUrl(input:string):string|null{
 let text=input.trim();
 if(/^\d{1,5}$/.test(text))text=`localhost:${text}`;
 if(!text.includes('://'))text=`http://${text}`;
 const match=/^http:\/\/(localhost|127\.0\.0\.1):(\d{1,5})([/?#][^\s\\]*)?$/.exec(text);
 if(!match)return null;
 const port=Number(match[2]);
 if(port<1||port>65535)return null;
 return `http://${match[1]}:${port}${match[3]??'/'}`;
}

/** When this thread's first request was sent, in seconds: git changes from then on are this thread's to show. */
export function threadStart(tasks:CodingTask[]):number|null{
 const times=tasks.flatMap(task=>task.messages.slice(0,1).map(message=>message.created_at)).filter(at=>typeof at==='number'&&at>0);
 return times.length?Math.floor(Math.min(...times)):null;
}

/** Local pages this thread's agents started or printed, newest first: localhost links in their replies and a port in
 * the commands they ran (`--port 5173`, `http.server 8840`). Offered with Open; never opened by themselves. */
export function agentPages(tasks:CodingTask[]):string[]{
 const found:{url:string;at:number}[]=[];
 const add=(url:string|null,at:number)=>{if(url&&!found.some(item=>item.url===url))found.push({url,at});};
 for(const task of tasks){
  for(const message of task.messages)if(message.role!=='user')for(const match of message.text.matchAll(/https?:\/\/(?:localhost|127\.0\.0\.1):\d{1,5}[^\s)<>"'`]*/g))add(localPageIn(match[0]),message.created_at);
  for(const token of task.tokens??[]){
   const command=token.tool==='native:Bash'?token.command??'':'';
   // A bare -p is a port only for something that serves: `mkdir -p 2025/…`, `ssh -p 22` and `psql -p 5432` aren't pages
   // (design review, 23 Sep).
   const serves=/\b(serve|server|dev|preview|start|vite|next|astro|http-server|live-server|docker)\b/.test(command);
   const port=(serves?/(?:localhost:|127\.0\.0\.1:|--port[= ]|-p\s+|http\.server\s+|PORT=)(\d{2,5})\b(?!\/)/:/(?:localhost:|127\.0\.0\.1:|--port[= ]|http\.server\s+|PORT=)(\d{2,5})\b/).exec(command)?.[1];
   if(port)add(localPageIn(`http://localhost:${port}/`),token.started_at??0);
  }
 }
 return found.sort((a,b)=>b.at-a.at).map(item=>item.url).slice(0,4);
}

/** The frame address for a project file under a grant's base (`dervo-preview://<grant>/`). */
export function previewSrc(base:string,path:string,version=0):string{
 return base+path.split('/').map(encodeURIComponent).join('/')+(version?`?v=${version}`:'');
}

export function splitPath(path:string){const at=path.lastIndexOf('/');return {dir:at>=0?path.slice(0,at+1):'',name:at>=0?path.slice(at+1):path};}

/** Progress or Preview, the column's two-part switch. */
export function ColumnSwitch({value,onChange}:{value:ColumnView;onChange:(view:ColumnView)=>void}){
 const views:[ColumnView,string][]=[['progress',t('Progress')],['preview',t('Preview')]];
 return <div className="dv-seg dv-column-switch" role="radiogroup" aria-label={t('Show in this column')} title={t('⌥⌘P switches')}>
  {views.map(([view,label])=><button key={view} type="button" role="radio" aria-checked={value===view} onClick={()=>onChange(view)}>{label}</button>)}
 </div>;
}

/** ⌘\ shows or hides the column; ⌥⌘P switches Progress and Preview. Only while a thread is open. */
export function usePreviewShortcuts(active:boolean,{toggleColumn,switchView}:{toggleColumn:()=>void;switchView:()=>void}){
 const actions=useRef({toggleColumn,switchView});
 actions.current={toggleColumn,switchView};
 useEffect(()=>{
  if(!active)return;
  function onKey(event:KeyboardEvent){
   if(event.defaultPrevented||event.isComposing||event.repeat||isProjectTerminalKey(event)||!(event.metaKey||event.ctrlKey)||event.shiftKey)return;
   if(!event.altKey&&(event.code==='Backslash'||event.key==='\\')){event.preventDefault();actions.current.toggleColumn();}
   else if(event.altKey&&(event.code==='KeyP')){event.preventDefault();actions.current.switchView();}
  }
  window.addEventListener('keydown',onKey);
  return()=>window.removeEventListener('keydown',onKey);
 },[active]);
}

/** While Preview shows in a window under FOLD_UNDER wide, the sidebar folds; closing Preview brings it back, but only if
 * Preview was what folded it. Must render inside the SidebarProvider. */
export function FoldSidebarWhile({active,width=()=>window.innerWidth}:{active:boolean;width?:()=>number}){
 const {open,setOpen}=useSidebar();
 const folded=useRef(false);
 useEffect(()=>{
  if(active&&open&&!folded.current&&width()<FOLD_UNDER){folded.current=true;setOpen(false);}
  else if(!active&&folded.current){folded.current=false;setOpen(true);}
 },[active]);
 return null;
}

type Grant={grant:string;base:string};
const icon=(d:ReactNode,size=14)=><svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>;

/** A page drawn at PAGE_WIDTH and scaled down to fit the column, never up. */
function Scaled({children}:{children:(size:{scale:number;height:number})=>ReactNode}){
 const box=useRef<HTMLDivElement|null>(null);
 const [size,setSize]=useState({width:0,height:0});
 useLayoutEffect(()=>{
  const el=box.current;if(!el)return;
  const read=()=>setSize({width:el.clientWidth,height:el.clientHeight});
  read();
  if(typeof ResizeObserver==='undefined')return;
  const observer=new ResizeObserver(read);observer.observe(el);
  return()=>observer.disconnect();
 },[]);
 const scale=size.width?Math.min(1,size.width/PAGE_WIDTH):1;
 return <div ref={box} className="dv-preview-stage">{size.width>0&&children({scale,height:size.height/scale})}</div>;
}

function Pager({index,count,label,onChange}:{index:number;count:number;label:string;onChange:(next:number)=>void}){
 if(count<2)return null;
 return <span className="dv-preview-pager">
  <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Previous {label}',{label})} disabled={index<=0} onClick={()=>onChange(index-1)}>{icon(<path d="M10 3L5 8l5 5"/>)}</button>
  <span className="tnum">{t('{at} of {all}',{at:index+1,all:count})}</span>
  <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Next {label}',{label})} disabled={index>=count-1} onClick={()=>onChange(index+1)}>{icon(<path d="M6 3l5 5-5 5"/>)}</button>
 </span>;
}

/** A Markdown file, read by the host and rendered without HTML or images. Links stay text. */
/** The thread a file read names, when there is one: a thread that changes code works in its own folder, and its files
 * are looked for there first (project_files.folders). Home and Next up have none. */
export function thread(threadId:string|null|undefined):{threadId?:string}{return threadId?{threadId}:{};}

function MarkdownDoc({projectId,threadId,path,version}:{projectId:string;threadId?:string;path:string;version:number}){
 const doc=useQuery({queryKey:['preview-text',projectId,threadId,path,version],retry:false,queryFn:()=>hostCall<{text:string;truncated:boolean}>('project_file_text',{projectId,path,...thread(threadId)})});
 if(doc.isError)return <p role="alert" className="dv-preview-empty">{doc.error instanceof Error?doc.error.message:String(doc.error)}</p>;
 if(!doc.data)return <p role="status" className="dv-preview-empty">{t('Loading {path}…',{path})}</p>;
 return <div className="dv-preview-doc" data-testid="preview-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{img:({alt})=><span>{alt?`[${alt}]`:'[image]'}</span>,a:({children})=><span className="dv-preview-doc-link">{children}</span>}}>{doc.data.text}</ReactMarkdown>
  {doc.data.truncated&&<p className="dv-field-note">{t('Showing the first 512 KB.')}</p>}</div>;
}

/** ``onRequestHandled``: called once a reply's request is shown, so the shell can forget it and a later Preview (in this
 * thread or another) opens on its own tabs instead of replaying the click. */
type Props={projectId:string;threadId?:string;tasks:CodingTask[];onProgress:()=>void;onClose:()=>void;request?:PreviewRequest|null;onRequestHandled?:()=>void};

export function PreviewPane({projectId,threadId,tasks,onProgress,onClose,request,onRequestHandled}:Props){
 // Beta.15: previewable files git reports changed since this thread's first request, read again whenever a run ends.
 const since=threadStart(tasks);
 const settled=tasks.map(task=>`${task.id}:${task.state}:${(task.runs as {state?:string}[]).length}`).join(',');
 const changes=useQuery({queryKey:['preview-changes',projectId,threadId,since,settled],enabled:since!==null,retry:false,staleTime:Infinity,
  queryFn:()=>hostCall<{files:{path:string;at:number}[]}>('preview_changes',{projectId,since,...thread(threadId)})});
 const [opened,setOpened]=useState<{path:string;at:number}[]>([]);
 const files=previewFiles(tasks,[...(changes.data?.files??[]),...opened]);
 const pages=files.filter(file=>file.kind==='page'),images=files.filter(file=>file.kind==='image'),docs=files.filter(file=>file.kind==='markdown'||file.kind==='pdf');
 // The tab follows what there is to show until you (or a reply) pick one: git's list can arrive after Preview opens.
 const [picked,setTab]=useState<PreviewTab|null>(null);
 const tab:PreviewTab=picked??(pages.length?'mockup':images.length?'screenshots':docs.length?'docs':'page');
 const [pageAt,setPageAt]=useState(0),[imageAt,setImageAt]=useState(0),[docAt,setDocAt]=useState(0);
 const suggested=agentPages(tasks);
 const [reloads,setReloads]=useState(0);
 const [typed,setTyped]=useState(''),[local,setLocal]=useState<string|null>(null),[typedError,setTypedError]=useState('');
 const [error,setError]=useState('');
 // Read again whenever a run ends, like the list: a thread's own folder can appear (setup) or go (Merge) while it's open.
 const grant=useQuery({queryKey:['preview-grant',projectId,threadId,threadId?settled:''],staleTime:Infinity,retry:false,queryFn:()=>invoke<Grant>('preview_grant',{projectId,...thread(threadId)})});
 // The newest file is shown first; a new write moves you to it unless you've paged back yourself.
 const page=pages[Math.min(pageAt,Math.max(0,pages.length-1))],image=images[Math.min(imageAt,Math.max(0,images.length-1))],doc=docs[Math.min(docAt,Math.max(0,docs.length-1))];
 const shown=tab==='mockup'?page:tab==='screenshots'?image:tab==='docs'?doc:undefined;
 // A path or local page a reply asked for: its tab, and that file or page, once per click.
 const handled=useRef(0);
 useEffect(()=>{
  if(!request||request.at===handled.current)return;
  handled.current=request.at;
  onRequestHandled?.();
  const target=request.target;
  if(target.kind==='page'){setTab('page');setTyped(target.url.replace(/^http:\/\//,''));setTypedError('');setLocal(target.url);setReloads(value=>value+1);return;}
  const path=target.path.replace(/^\.\//,'');
  const kind=previewKind(path);
  if(!kind||!servablePath(path)){setError(t('Preview shows pages, images, Markdown and PDFs inside this project. Use Show in Finder for this one.'));return;}
  setError('');
  // The reply's path is newest now, so it's first in its list.
  setOpened(previous=>[...previous.filter(item=>item.path!==path),{path,at:Date.now()/1000+1e6}]);
  if(kind==='page'){setTab('mockup');setPageAt(0);}else if(kind==='image'){setTab('screenshots');setImageAt(0);}else{setTab('docs');setDocAt(0);}
 },[request]);

 async function openOutside(){
  setError('');
  try{
   if(tab==='page'&&local)await invoke('preview_open_outside',{url:local});
   else if(shown?.kind==='markdown')await hostCall('project_file_open',{projectId,path:shown.path,...thread(threadId)});
   else if(shown&&grant.data)await invoke('preview_open_outside',{grant:grant.data.grant,path:shown.path});
  }catch(failure){setError(String(failure));}
 }
 function openLocal(event:React.FormEvent){
  event.preventDefault();
  const url=localPageUrl(typed);
  if(!url){setTypedError(t('Only a page on this Mac: localhost or 127.0.0.1 with a port, like localhost:5173.'));return;}
  setTypedError('');setLocal(url);setReloads(value=>value+1);
 }
 const where=tab==='page'?local:shown?.path;
 const {dir,name}=splitPath(tab==='page'?(local??''):shown?.path??'');
 const caption=tab==='mockup'?t('Offline · reloads when the agent writes it'):tab==='screenshots'?t('What the agent saved in the project'):tab==='docs'?t('Markdown and PDFs from this thread'):t('Local page · this Mac only');

 return <aside className="dervo-preview-pane dv-panel" aria-label={t('Preview')} data-testid="preview-pane"
  onKeyDown={event=>{if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();onProgress();}}}>
  <header className="dv-preview-head">
   <ColumnSwitch value="preview" onChange={view=>{if(view==='progress')onProgress();}}/>
   <button type="button" className="dv-btn dv-btn-quiet dv-preview-close" aria-label={t('Close the column')} title={t('Close (⌘\\)')} onClick={onClose}>{icon(<path d="M4 4l8 8M12 4l-8 8"/>)}</button>
  </header>
  {where?<div className="dv-preview-path">
   <p className="dv-preview-where" title={where}><span>{dir}</span><b>{name||where}</b></p>
   {tab==='mockup'&&<Pager index={pages.indexOf(page!)} count={pages.length} label={t('page')} onChange={setPageAt}/>}
   {tab==='screenshots'&&<Pager index={images.indexOf(image!)} count={images.length} label={t('screenshot')} onChange={setImageAt}/>}
   {tab==='docs'&&<Pager index={docs.indexOf(doc!)} count={docs.length} label={t('document')} onChange={setDocAt}/>}
   <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Reload')} title={t('Reload')} onClick={()=>setReloads(value=>value+1)}>{icon(<><path d="M13 8a5 5 0 1 1-1.5-3.5"/><path d="M13 3v3h-3"/></>)}</button>
   <button type="button" className="dv-btn dv-btn-quiet" aria-label={tab==='page'||shown?.kind==='page'?t('Open in your browser'):t('Open with default app')} title={tab==='page'||shown?.kind==='page'?t('Open in your browser'):t('Open with default app')} disabled={tab!=='page'&&shown?.kind!=='markdown'&&!grant.data} onClick={()=>void openOutside()}>{icon(<path d="M5.5 10.5l5-5M6.5 5.5h4v4"/>)}</button>
  </div>:null}
  {error&&<p role="alert" className="dv-panel-error">{error}</p>}
  <div className="dv-preview-body">
   {tab==='page'?(local
    ?<Scaled>{({scale,height})=><iframe key={`${local}:${reloads}`} title={t('Local page')} data-testid="preview-frame" src={local} sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" allow=""
      style={{width:PAGE_WIDTH,height,transform:`scale(${scale})`}}/>}</Scaled>
    :<form className="dv-preview-local" onSubmit={openLocal}>
      {suggested.length>0&&<div className="dv-preview-started" role="group" aria-label={t('Pages this thread\'s agents started')}>
       <p className="dv-label">{t('Started in this thread')}</p>
       {suggested.map(url=><span key={url} className="dv-preview-started-row"><code>{url.replace(/^http:\/\//,'').replace(/\/$/,'')}</code>
        <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>{setTyped(url.replace(/^http:\/\//,''));setTypedError('');setLocal(url);setReloads(value=>value+1);}}>{t('Open')}</button></span>)}
      </div>}
      <label htmlFor="dv-preview-local-url">{t('Open a page running on this Mac')}</label>
      <p className="dv-field-note">{t('Nothing opens by itself. Type a port or address, like localhost:5173.')}</p>
      <span className="dv-preview-local-row"><input id="dv-preview-local-url" className="dv-input" value={typed} placeholder="localhost:5173" aria-invalid={!!typedError||undefined} onChange={event=>setTyped(event.target.value)}/>
       <button type="submit" className="dv-btn dv-btn-ghost dv-btn-sm">{t('Open')}</button></span>
      {typedError&&<p role="alert" className="dv-field-note">{typedError}</p>}
     </form>)
   :!shown?<div className="dv-preview-empty">
     <p>{t('Nothing to preview yet.')}</p>
     <p className="dv-field-note">{t('When an agent makes a page, an image, Markdown or a PDF in this project, it shows here.')}</p>
    </div>
   :grant.isError?<p role="alert" className="dv-preview-empty">{String(grant.error)}</p>
   :!grant.data?null
   :shown.kind==='markdown'?<MarkdownDoc projectId={projectId} threadId={threadId} path={shown.path} version={shown.writes+reloads+Math.floor(shown.at)}/>
   :shown.kind==='pdf'?<div className="dv-preview-pdf"><iframe key={`${shown.path}:${reloads}`} title={t('Preview of {path}',{path:shown.path})} data-testid="preview-frame" src={previewSrc(grant.data.base,shown.path,shown.writes+reloads)} sandbox="allow-scripts" referrerPolicy="no-referrer" allow=""/></div>
   :tab==='mockup'?<Scaled>{({scale,height})=><iframe key={`${shown.path}:${shown.writes}:${reloads}`} title={t('Preview of {path}',{path:shown.path})} data-testid="preview-frame"
      src={previewSrc(grant.data!.base,shown.path,shown.writes+reloads)} sandbox="allow-scripts" referrerPolicy="no-referrer" allow="" style={{width:PAGE_WIDTH,height,transform:`scale(${scale})`}}/>}</Scaled>
   :<div className="dv-preview-shot"><img key={`${shown.path}:${shown.writes}:${reloads}`} src={previewSrc(grant.data.base,shown.path,shown.writes+reloads)} alt={t('Screenshot {name}',{name})} referrerPolicy="no-referrer"/></div>}
  </div>
  <footer className="dv-preview-foot">
   <div className="dv-preview-tabs" role="tablist" aria-label={t('What to preview')}>
    {pages.length>0&&<button type="button" role="tab" aria-selected={tab==='mockup'} onClick={()=>setTab('mockup')}>{t('Mockup')}</button>}
    {images.length>0&&<button type="button" role="tab" aria-selected={tab==='screenshots'} onClick={()=>setTab('screenshots')}>{t('Screenshots')} <span className="tnum">{images.length}</span></button>}
    {docs.length>0&&<button type="button" role="tab" aria-selected={tab==='docs'} onClick={()=>setTab('docs')}>{t('Docs')} <span className="tnum">{docs.length}</span></button>}
    <button type="button" role="tab" aria-selected={tab==='page'} onClick={()=>setTab('page')}>{t('Local page')}</button>
   </div>
   <p className="dv-preview-caption">{caption}</p>
  </footer>
 </aside>;
}
