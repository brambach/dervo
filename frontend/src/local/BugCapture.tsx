import {useEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';
import {useQueryClient} from '@tanstack/react-query';
import {Dialog,DialogClose,DialogContent,DialogDescription,DialogTitle} from '@/shared/ui/dialog';
import {local} from './api';
import {Segmented} from './DervoControls';
import {Disclosure} from './SettingsKit';
import {providerAuthor} from './providerLabel';
import type {ItemKind,ItemListing} from './BugsList';
import './BugCapture.css';
import {t} from '@/i18n';

/** ⌘⇧B for the Dervo admin (ux.md, "Bugs and ideas as Bryce's admin tool", slice 1): a sheet over whatever's on screen
 * that logs a bug or idea in one move. One box, whose first line is the title. Saved through `ideas_item_add` in a
 * project's Bugs and ideas, marked as about Dervo unless you pick This project, with what was on screen appended to the
 * note. Dervo reports lists it. Testers get Send feedback on Bug instead (ConnectedApp.tsx).
 *
 * Screenshots aren't attached. imageInput.ts uploads into a thread's or Home's own image store, and those images show
 * up in that composer's drafts until a message uses them; a bug or idea has no image of its own to hold one. */

/** Where a report is saved: the open project when it can keep reports, otherwise a personal project that can. */
export type CaptureTarget={projectId:string;profileId:string;name:string;here:boolean};
/** What was on screen, from the shell. Names and ids only: never a message, a file or an error's text. */
export type CaptureContext={project:{id:string;name:string}|null;screen:string;thread:{id:string;name:string}|null;
  task:{id:string;state:string}|null;provider:string|null;model:string|null};
export type ShownError={type:string;screen:string;at:number;ref?:string};
type About='dervo'|'project';
type Logged={id:string;revision:number;number:number;projectId:string;profileId:string;name:string;here:boolean;draft:{text:string;kind:ItemKind;about:About}};

export const NAME_LIMIT=60;
/** The box's limit, below the host's 8,000-character note so the lines sent with it always fit. */
export const TEXT_LIMIT=7000;
/** How long "Logged #37. Undo." stays before the sheet closes by itself. */
export const LOGGED_MS=5000;

// ---------------------------------------------------------------- errors shown this session
/** The last few errors the window showed, as the host's error reports keep them: the kind and the screen, never the
 * words. Kept in memory for this session only. */
const shown:ShownError[]=[];
export const ERRORS_KEPT=3;
/** An error's kind: a JavaScript error's built-in name, or "Host error" for the plain text the host refuses with. */
export function errorKind(error:unknown):string{
  if(error instanceof Error)return /^[A-Za-z_][A-Za-z0-9_]{0,59}$/.test(error.name)?error.name:'Error';
  return /^((?:[A-Z][A-Za-z0-9_]{0,54})?Error):/.exec(String(error))?.[1]??t('Host error');
}
/** A read that keeps failing every few seconds is one error, not three: the same kind on the same screen within a minute
 * moves the last one's time on instead. */
/** The host's six-character reference for an unexpected error ("… (ref 3be21c) …", host.error_answer), or undefined.
 * It names one row of the host's error log and nothing else, so a report keeps it (23 Sep: three "Host error" reports
 * had only a time, and couldn't be traced). */
export function errorRef(error:unknown):string|undefined{
  return /\(ref ([0-9a-f]{6})\)/.exec(error instanceof Error?error.message:String(error))?.[1];
}
export function noteShownError(error:unknown,screen:string,now=Date.now()){
  const type=errorKind(error),ref=errorRef(error),last=shown.at(-1);
  if(last&&last.type===type&&last.screen===screen&&last.ref===ref&&now-last.at<60_000){last.at=now;return;}
  shown.push({type,screen,at:now,...(ref?{ref}:{})});
  shown.splice(0,Math.max(0,shown.length-ERRORS_KEPT));
}
export function recentErrors():ShownError[]{return shown.slice();}
export function forgetShownErrors(){shown.length=0;}

// ---------------------------------------------------------------- the report
/** The title is the first line with words in it, on one line, clipped to the host's 60 characters. */
export function reportName(text:string):string{
  const line=text.split('\n').map(part=>part.replace(/\s+/g,' ').trim()).find(Boolean)??'';
  return line.length>NAME_LIMIT?line.slice(0,NAME_LIMIT-1).trimEnd()+'…':line;
}
const two=(value:number)=>String(value).padStart(2,'0');
const clock=(at:number)=>{const when=new Date(at);return `${two(when.getHours())}:${two(when.getMinutes())}`;};
/** What's attached automatically, one line each, in the words the note keeps. */
// i18n-ignore: the note goes to Bryce's list, so what Dervo attaches stays in English
export function contextLines({version,context,errors,size}:{version:string|null;context:CaptureContext;errors:ShownError[];size:{width:number;height:number}|null}):string[]{
  const lines=[version?`Dervo ${version}`:'Dervo, version unknown'];
  if(context.project)lines.push(`Project: ${context.project.name} (${context.project.id})`);
  lines.push(`Screen: ${context.screen}`);
  if(context.thread)lines.push(`Thread: ${context.thread.name} (${context.thread.id})`);
  if(context.task)lines.push(`Task: ${context.task.id}, ${context.task.state.replace(/_/g,' ')}`);
  if(context.provider||context.model)lines.push(`Model: ${[context.provider?providerAuthor(context.provider):'',context.model??''].filter(Boolean).join(', ')}`);
  lines.push(errors.length?`Errors shown: ${errors.map(error=>`${error.type}${error.ref?` (ref ${error.ref})`:''} on ${error.screen} at ${clock(error.at)}`).join('; ')}`:'Errors shown: none this session');
  if(size)lines.push(`Window: ${size.width} × ${size.height}`);
  return lines;
}
/** The first note: everything you typed, then what was sent with it. */
// i18n-ignore: part of the note, in English like contextLines
export function reportNote(text:string,lines:string[]):string{
  return `${text.trim()}\n\nSent with it:\n${lines.map(line=>`- ${line}`).join('\n')}`;
}
/** A name no open item of the same kind has yet. The host refuses a second "Sidebar flickers", so the next is "(2)". */
export function uniqueName(name:string,taken:string[]):string{
  const used=new Set(taken.map(value=>value.toLowerCase()));
  if(!used.has(name.toLowerCase()))return name;
  for(let n=2;;n++){
    const tail=` (${n})`,base=name.length+tail.length>NAME_LIMIT?name.slice(0,NAME_LIMIT-tail.length).trimEnd():name;
    if(!used.has((base+tail).toLowerCase()))return base+tail;
  }
}
const reason=(failure:unknown)=>failure instanceof Error?failure.message:String(failure);
export function captureTitle(kind:ItemKind,about:About):string{
  if(about==='dervo')return kind==='idea'?t('Log a Dervo idea'):t('Log a Dervo bug');
  return kind==='idea'?t('Log an idea'):t('Log a bug');
}

async function appVersion():Promise<string|null>{
  try{const {getVersion}=await import('@tauri-apps/api/app');return await getVersion();}catch{return null;}
}

export function BugCapture({open,onOpenChange,target,context,getVersion=appVersion,errors=recentErrors}:{open:boolean;onOpenChange:(open:boolean)=>void;
  target:CaptureTarget|null;context:CaptureContext;getVersion?:()=>Promise<string|null>;errors?:()=>ShownError[]}){
  const client=useQueryClient();
  const [text,setText]=useState(''),[kind,setKind]=useState<ItemKind>('bug'),[about,setAbout]=useState<About>('dervo');
  const [version,setVersion]=useState<string|null>(null),[seen,setSeen]=useState<ShownError[]>([]),[size,setSize]=useState<{width:number;height:number}|null>(null);
  const [busy,setBusy]=useState(false),[failure,setFailure]=useState(''),[shotNote,setShotNote]=useState(false);
  const [logged,setLogged]=useState<Logged|null>(null),[undone,setUndone]=useState('');
  const box=useRef<HTMLTextAreaElement>(null),done=useRef<HTMLDivElement>(null),returnFocus=useRef<HTMLElement|null>(null);
  const closeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const stopTimer=()=>{if(closeTimer.current){clearTimeout(closeTimer.current);closeTimer.current=null;}};
  useEffect(()=>()=>stopTimer(),[]);
  // Each opening reads the screen as it is now: the window, the errors so far and the app's version.
  useEffect(()=>{
    if(!open)return;
    returnFocus.current=document.activeElement instanceof HTMLElement&&document.activeElement!==document.body?document.activeElement:null;
    setSize({width:Math.round(window.innerWidth),height:Math.round(window.innerHeight)});setSeen(errors());setFailure('');setUndone('');setShotNote(false);
    let current=true;
    void getVersion().then(value=>{if(current)setVersion(value);}).catch(()=>{});
    return()=>{current=false;};
  },[open]);
  // "This project" needs the open project to keep reports; without one, Dervo is the only choice.
  const aboutNow:About=target?.here?about:'dervo';
  const name=reportName(text);
  const lines=contextLines({version,context,errors:seen,size});
  const ready=!!target&&!!name&&!busy;

  function close(next:boolean){
    if(!next){stopTimer();setLogged(null);setUndone('');}
    onOpenChange(next);
  }
  async function save(){
    if(!ready||!target)return;
    setBusy(true);setFailure('');setUndone('');
    const scope={projectId:target.projectId,profileId:target.profileId};
    const args={...scope,kind,name,text:reportNote(text,lines),product:aboutNow};
    const add=()=>local<ItemListing>('ideas_item_add',args);
    try{
      let listing:ItemListing;
      try{listing=await add();}
      catch(refused){
        if(!/already has that name/.test(reason(refused)))throw refused;
        const current=await local<ItemListing>('ideas_item_list',scope);
        args.name=uniqueName(name,current.items.filter(item=>item.kind===kind).map(item=>item.name));
        listing=await add();
      }
      const record=listing.items.find(item=>item.kind===kind&&item.name===args.name);
      if(!record)throw new Error(t('The report was saved, but Dervo couldn’t find its number. Open Bugs and ideas to check it.'));
      setLogged({id:record.id,revision:record.revision,number:record.number,...scope,name:target.name,here:target.here,draft:{text,kind,about:aboutNow}});
      setText('');
      void client.invalidateQueries({queryKey:['bugs-items',scope.projectId]});void client.invalidateQueries({queryKey:['dervo-feedback']});
      requestAnimationFrame(()=>done.current?.focus());
      stopTimer();closeTimer.current=setTimeout(()=>close(false),LOGGED_MS);
    }catch(refused){setFailure(reason(refused));}
    finally{setBusy(false);}
  }
  async function undo(){
    if(!logged||busy)return;
    stopTimer();setBusy(true);setFailure('');
    try{
      await local<ItemListing>('ideas_archive',{projectId:logged.projectId,profileId:logged.profileId,ideaId:logged.id,expectedRevision:logged.revision});
      void client.invalidateQueries({queryKey:['bugs-items',logged.projectId]});void client.invalidateQueries({queryKey:['dervo-feedback']});
      // Undo takes the report back and puts your words where they were, to fix and log again.
      setText(logged.draft.text);setKind(logged.draft.kind);setAbout(logged.draft.about);
      setUndone(t('Removed #{number}. Your words are back.',{number:logged.number}));setLogged(null);
      requestAnimationFrame(()=>box.current?.focus());
    }catch(refused){setFailure(t('#{number} couldn’t be removed. {why}',{number:logged.number,why:reason(refused)}));}
    finally{setBusy(false);}
  }
  function refuseShot(files:FileList|null|undefined,event:{preventDefault:()=>void}){
    if(!files?.length)return;
    event.preventDefault();
    if([...files].some(file=>file.type.startsWith('image/')))setShotNote(true);
  }

  const title=captureTitle(kind,aboutNow);
  // Where it goes before you save, and where it is after.
  const listed=(dervo:boolean)=>dervo?` ${t('Dervo reports lists it.')}`:'';
  const where=logged?`${t('It’s in Bugs and ideas in {name}.',{name:logged.name})}${listed(logged.draft.about==='dervo')}`:target?`${t('It goes in Bugs and ideas in {name}.',{name:target.name})}${listed(aboutNow==='dervo')}`
    :t('Add a project on a personal account first. For now, reports are kept in a project’s Bugs and ideas.');
  const summary=t('Sent with it: {screen}, {version} and {count} more',{screen:context.project?t('{screen} in {project}',{screen:context.screen,project:context.project.name}):context.screen,version:version?`Dervo ${version}`:t('the app version'),count:lines.length-(context.project?3:2)});
  return <Dialog open={open} onOpenChange={close}>
    <DialogContent className="dv-feedback dv-capture" data-testid="bug-capture" overlayVariant="transparent" showCloseButton={false}
      onOpenAutoFocus={event=>{event.preventDefault();(logged?done.current:box.current)?.focus();}}
      onCloseAutoFocus={event=>{if(returnFocus.current?.isConnected){event.preventDefault();returnFocus.current.focus();}}}>
      <div className="dv-feedback-head"><div><DialogTitle>{title}</DialogTitle><DialogDescription>{where}</DialogDescription></div>
        <DialogClose asChild><button type="button" className="dv-btn dv-btn-icon" aria-label={t('Close')}><X size={16} aria-hidden="true"/></button></DialogClose></div>
      {logged?<div ref={done} tabIndex={-1} className="dv-capture-done" role="status">
        <p>{logged.here?t('Logged #{number}.',{number:logged.number}):t('Logged #{number} in {name}.',{number:logged.number,name:logged.name})}</p>
        <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={busy} onClick={()=>void undo()}>{t('Undo')}</button>
        {failure&&<p role="alert" className="dv-field-note">{failure}</p>}
      </div>
      :<form onSubmit={event=>{event.preventDefault();void save();}} onKeyDown={event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)&&!event.nativeEvent.isComposing){event.preventDefault();void save();}}}
        onDragOver={event=>{if(event.dataTransfer?.types?.includes('Files'))event.preventDefault();}} onDrop={event=>refuseShot(event.dataTransfer?.files,event)}>
        <label className="sr-only" htmlFor="dervo-bug-capture-text">{t('What happened')}</label>
        <textarea id="dervo-bug-capture-text" ref={box} className="dv-input" rows={5} maxLength={TEXT_LIMIT} value={text} disabled={busy}
          placeholder={kind==='bug'?t('What happened? The first line is the title.'):t('What would help? The first line is the title.')}
          onChange={event=>{setText(event.target.value);if(failure)setFailure('');}} onPaste={event=>refuseShot(event.clipboardData?.files,event)}/>
        {undone&&<p role="status" className="dv-field-note">{undone}</p>}
        {shotNote&&<p role="status" className="dv-field-note">{t('Screenshots can’t be saved with a report yet. Say what you saw instead.')}</p>}
        <div className="dv-capture-choices">
          <Segmented label={t('Kind')} value={kind} onChange={setKind} disabled={busy} options={[{value:'bug',label:t('Bug')},{value:'idea',label:t('Idea')}]}/>
          <span className="dv-capture-about"><span className="dv-caption" aria-hidden="true">{t('About')}</span>
            <Segmented label={t('About')} value={aboutNow} onChange={setAbout} disabled={busy}
              options={[{value:'dervo',label:'Dervo'},{value:'project',label:t('This project'),disabled:!target?.here,title:target?.here?undefined:t('Open a project on a personal account to log it there')}]}/></span>
        </div>
        <Disclosure className="dv-capture-context" summary={summary}>
          <ul>{lines.map(line=><li key={line}>{line}</li>)}</ul>
        </Disclosure>
        {failure&&<p role="alert" className="dv-field-note">{failure}</p>}
        <div className="dv-feedback-foot">
          <span className="dv-caption">{t('⌘ Return to save')}</span>
          <DialogClose asChild><button type="button" className="dv-btn dv-btn-quiet">{t('Cancel')}</button></DialogClose>
          <button type="submit" className="dv-btn dv-btn-primary" disabled={!ready}>{busy?t('Saving…'):t('Log {kind}',{kind})}</button>
        </div>
      </form>}
    </DialogContent>
  </Dialog>;
}
