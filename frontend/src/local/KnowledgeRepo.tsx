import {useEffect,useRef,useState} from 'react';
import {ruleDate} from './KnowledgeNotebook';
import {showInFileManager} from '@/local/platformWords';

/** "From the repository" on the Knowledge page (knowledge-a.html, knowledge-a-repo.html): the repository's CLAUDE.md,
 * .claude/CLAUDE.md, .claude/rules and AGENTS.md, read only, in the order every task gets them (repo_guidance.py). A file
 * opens on the white card with its text as it is. "Stop sending it" confirms in place and changes nothing on disk. */
export type RepoFile={path:string;bytes?:number;lines?:number;sha256?:string;modified?:number;cut?:boolean;text?:string;sent_bytes?:number;
 empty?:boolean;error?:string;stopped?:boolean;changed?:boolean;last_sent_at?:number|null;last_sent_modified?:number|null};
export type RepoListing={files:RepoFile[];looked_for:string[];missing:string[];limit:number;available?:boolean};

/** A size as the page writes it: "4.3 KB", "41 KB", "812 bytes". */
export function fileSize(bytes:number):string{
 if(bytes<1024)return `${bytes} ${bytes===1?'byte':'bytes'}`;
 const kb=bytes/1024;
 return `${kb<10?(Math.round(kb*10)/10).toString():Math.round(kb)} KB`;
}
/** The day a file last changed: "Today", or "16 Sep". */
export function fileDay(seconds:number,now=new Date()):string{
 const day=new Date(seconds*1000);
 return day.toDateString()===now.toDateString()?'Today':ruleDate(day.toISOString());
}
/** The one line under a file's name. */
export function fileLine(file:RepoFile,limit:number):string{
 if(file.error)return file.error;
 if(file.stopped)return 'Not sent. New tasks don’t get it.';
 if(file.empty)return 'Empty, so there’s nothing to send.';
 const size=fileSize(file.bytes??0);
 if(file.cut)return `Cut to ${fileSize(limit)}. It’s ${size}, so agents get the first ${fileSize(limit)}. Move detail into .claude/rules to fit.`;
 if(file.changed)return `${size} · changed since the last task${file.last_sent_modified?`, which got the ${ruleDate(new Date(file.last_sent_modified*1000).toISOString())} version`:''}`;
 return `${size} · every task gets the whole file`;
}
const FILE_ICON=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M4 2.5h5l3 3v8H4z"/><path d="M9 2.5v3h3"/></svg>;
const SHOWN_LINES=16;

type Props={listing:RepoListing;busy:boolean;onSend:(file:RepoFile,send:boolean)=>Promise<unknown>;onOpen:(file:RepoFile,how:'editor'|'finder')=>Promise<unknown>};

export function KnowledgeRepo({listing,busy,onSend,onOpen}:Props){
 const [open,setOpen]=useState(''),[all,setAll]=useState(false),[confirm,setConfirm]=useState(false);
 const heading=useRef<HTMLDivElement>(null),rows=useRef(new Map<string,HTMLButtonElement>()),keep=useRef<HTMLButtonElement>(null);
 useEffect(()=>{if(confirm)keep.current?.focus();},[confirm]);
 const files=listing.files??[],missing=listing.missing??[];
 function show(path:string){setOpen(path);setAll(false);setConfirm(false);requestAnimationFrame(()=>heading.current?.focus());}
 function close(path:string){setOpen('');setConfirm(false);requestAnimationFrame(()=>rows.current.get(path)?.focus());}
 const count=files.length;
 return <div className="dv-kn-grp" data-testid="repo-files">
  <div className="dv-kn-grp-head"><span className="dv-kn-label">From the repository</span><span className="dv-kn-n">{count?`${count} ${count===1?'file':'files'}`:'none'}</span>
   {count>0&&<span className="dv-caption">Read only. Change them in your editor.</span>}</div>
  {!count&&<p className="dv-kn-line">No instruction files here. Dervo looks for <code>CLAUDE.md</code>, <code>.claude/CLAUDE.md</code>, <code>.claude/rules/*.md</code> and <code>AGENTS.md</code>, and picks them up when you add one.</p>}
  {count>0&&<div className="dv-sheet">{files.map(file=>{
   const line=fileLine(file,listing.limit);
   if(file.error)return <div key={file.path} className="dv-row">
    {FILE_ICON}
    <span className="dv-row-text"><span className="dv-row-name dv-kn-mono">{file.path}</span><span className="dv-row-line dv-kn-alert">{line}</span></span>
    <span className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void onOpen(file,'finder')}>{showInFileManager()}</button></span>
   </div>;
   if(file.stopped&&open!==file.path)return <div key={file.path} className="dv-row">
    {FILE_ICON}
    <span className="dv-row-text"><span className="dv-row-name dv-kn-mono">{file.path}</span><span className="dv-row-line">Not sent. New tasks don’t get it.</span></span>
    <span className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void onSend(file,true)}>Send it again</button></span>
   </div>;
   if(open!==file.path)return <button key={file.path} type="button" className="dv-row" ref={button=>{if(button)rows.current.set(file.path,button);else rows.current.delete(file.path);}} onClick={()=>show(file.path)} aria-expanded={false}>
     {FILE_ICON}
     <span className="dv-row-text"><span className="dv-row-name dv-kn-mono">{file.path}</span>
      <span className="dv-row-line">{file.cut?<><span className="dv-kn-cut">Cut to {fileSize(listing.limit)}.</span> It’s {fileSize(file.bytes??0)}, so agents get the first {fileSize(listing.limit)}. Move detail into <code>.claude/rules</code> to fit.</>:line}</span></span>
     <span className="dv-row-end"><span className="dv-when">{file.modified?fileDay(file.modified):''}</span></span>
    </button>;
   const lines=(file.text??'').split('\n');
   const long=lines.length>SHOWN_LINES;
   return <article key={file.path} className="dv-row dv-open" aria-label={file.path} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();if(confirm){setConfirm(false);}else close(file.path);}}}>
    {FILE_ICON}
    <div>
     <div className="dv-row-name dv-kn-mono" ref={heading} tabIndex={-1}>{file.path}</div>
     <p className="dv-kn-file-meta"><span>{fileSize(file.bytes??0)}, {file.lines} {file.lines===1?'line':'lines'}</span>{file.modified&&<><span>·</span><span>changed {fileDay(file.modified).replace('Today','today')}</span></>}</p>
     <p className="dv-kn-file-meta"><span>{file.stopped?'Not sent. New tasks don’t get it.':file.cut?`Agents get the first ${fileSize(listing.limit)} of ${fileSize(file.bytes??0)}. Claude Code and Codex get the same text.`
      :file.changed?'Changed since the last task. The next task gets this version, and Claude Code and Codex get the same text.':'Every task gets the whole file. Claude Code and Codex get the same text.'}</span></p>
     {file.text&&<div className="dv-kn-file-text" data-all={all||!long||undefined}><pre>{all||!long?file.text:lines.slice(0,SHOWN_LINES+4).join('\n')}</pre></div>}
     {all&&file.cut&&<p className="dv-kn-hint">That’s all agents get. Open in editor to read the rest.</p>}
     {confirm?<div className="dv-kn-confirm" role="alertdialog" aria-label={`Stop sending ${file.path}`}>
      <p className="dv-kn-what"><b>Stop sending {file.path}?</b> New tasks won’t get it. The file isn’t changed.</p>
      <div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void onSend(file,false).then(()=>{setConfirm(false);setOpen('');},()=>undefined)}>Stop sending</button>
       <button type="button" ref={keep} className="dv-btn dv-btn-quiet" onClick={()=>setConfirm(false)}>Keep sending</button></div>
     </div>:<div className="dv-row-actions">
      <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void onOpen(file,'editor')}>Open in editor</button>
      {long&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setAll(value=>!value)}>{all?'Show less':`Show all ${file.lines} lines`}</button>}
      <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void onOpen(file,'finder')}>{showInFileManager()}</button>
      <span className="dv-kn-spacer"/>
      {file.stopped?<button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void onSend(file,true)}>Send it again</button>
       :<button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>setConfirm(true)}>Stop sending it</button>}
     </div>}
    </div>
    <div className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>close(file.path)}>Close</button></div>
   </article>;
  })}</div>}
  {count>0&&missing.length>0&&<p className="dv-kn-faint">No {missing.map((name,index)=><span key={name}>{index?(index===missing.length-1?' or ':', '):''}<code>{name}</code></span>)} in this repository.</p>}
 </div>;
}
