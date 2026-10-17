import * as React from 'react';
import {toast} from 'sonner';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {copyTextToClipboard} from '@/shared/lib/clipboard';
import {ReplyActionsContext,commandRisk,previewKind} from './replyActions';
import './ReplyChips.css';
import {showInFileManager} from '@/local/platformWords';

const failed=(error:unknown)=>toast.error(error instanceof Error?error.message:String(error));
const lowerFirst=(text:string)=>text.charAt(0).toLowerCase()+text.slice(1);
const glyph=(d:React.ReactNode)=><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>;
const PLAY=glyph(<path d="M5 3.5v9l7-4.5z" fill="currentColor" stroke="none"/>);
const OPEN=glyph(<path d="M5.5 10.5l5-5M6.5 5.5h4v4"/>);

/** Run on a command block: it goes to the project terminal, in the project folder. A command that installs, deletes or
 * changes files asks once, in place, unless agent commands are on in Access. Nothing runs before the click. */
export function RunCommand({command,from='From a reply'}:{command:string;
 /** Where it was sent from, as the terminal's run card says it: Settings' command rows pass their own. */
 from?:string}){
 const actions=React.useContext(ReplyActionsContext);
 const [asking,setAsking]=React.useState<string|null>(null);
 const [sent,setSent]=React.useState(false);
 const confirm=React.useRef<HTMLButtonElement>(null),runButton=React.useRef<HTMLButtonElement>(null);
 React.useEffect(()=>{if(asking)confirm.current?.focus();},[asking]);
 // Cancel (or Esc) takes the question away with the focused button in it: focus goes back to Run, not to the page.
 function cancel(){setAsking(null);runButton.current?.focus();}
 React.useEffect(()=>{if(!sent)return;const timer=setTimeout(()=>setSent(false),1600);return()=>clearTimeout(timer);},[sent]);
 if(!actions)return null;
 const act=actions;
 async function go(){
  setAsking(null);
  try{await act.run(command,from);setSent(true);}catch(error){failed(error);}
 }
 function click(){
  const risk=commandRisk(command);
  if(risk&&!act.commandsAllowed){setAsking(risk);return;}
  void go();
 }
 return <>
  <div className="dv-reply-actions"><button ref={runButton} type="button" className="dv-reply-run" onClick={click} aria-label={`Run in the project terminal: ${command}`} title="Run in the project terminal">
   {PLAY}<span>{sent?'Sent':'Run'}</span></button></div>
  {asking&&<div className="dv-reply-ask" role="group" aria-label="Run this command?" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();cancel();}}}>
   <p><b>Run this in the project terminal?</b> {asking}</p>
   <span className="dv-reply-ask-actions"><button ref={confirm} type="button" className="dv-reply-run" onClick={()=>void go()}>{PLAY}<span>Run it</span></button>
    <button type="button" className="dv-reply-quiet" onClick={cancel}>Cancel</button></span>
  </div>}
 </>;
}

/** A path in a reply, as a chip with its three ways in: Preview, Finder and the default app. A folder gets Finder only. */
export function PathChip({path,children}:{path:string;children:React.ReactNode}){
 const actions=React.useContext(ReplyActionsContext);
 if(!actions)return <>{children}</>;
 const kind=previewKind(path),folder=path.endsWith('/');
 const openable=!folder&&/\.(html?|md|markdown|txt|pdf|png|jpe?g|gif|webp|avif|svg|heic|csv|json|log|mov|mp4|m4v|mp3|wav|rtf|docx|xlsx|pptx)$/i.test(path);
 const act=actions;
 return <DropdownMenu>
  <DropdownMenuTrigger asChild><button type="button" className="dv-reply-path" title={path} aria-label={`${path}: open, ${lowerFirst(showInFileManager())} or copy`}>{children}</button></DropdownMenuTrigger>
  <DropdownMenuContent align="start" className="dv-reply-menu">
   {/* With no Preview column here, preview() opens the default app, which is the item below: one way in, said once. */}
   {kind&&act.previewHere!==false&&<DropdownMenuItem onSelect={()=>act.preview({kind:'file',path})}>Open in Preview</DropdownMenuItem>}
   <DropdownMenuItem onSelect={()=>void act.reveal(path.replace(/\/$/,'')).catch(failed)}>{showInFileManager()}</DropdownMenuItem>
   {openable&&<DropdownMenuItem onSelect={()=>void act.openDefault(path).catch(failed)}>Open with default app</DropdownMenuItem>}
   <DropdownMenuSeparator/>
   <DropdownMenuItem onSelect={()=>copyTextToClipboard(path,'Path copied')}>Copy path</DropdownMenuItem>
  </DropdownMenuContent>
 </DropdownMenu>;
}

/** Open beside a local page an agent printed: one click shows it in Preview. It never opens by itself. */
export function OpenLocalPage({url}:{url:string}){
 const actions=React.useContext(ReplyActionsContext);
 if(!actions)return null;
 const act=actions;
 const where=url.replace(/^http:\/\//,'').replace(/\/$/,'');
 const into=act.previewHere===false?'in your browser':'in Preview';
 return <button type="button" className="dv-reply-open" onClick={()=>act.preview({kind:'page',url})} aria-label={`Open ${where} ${into}`} title={`Open ${into}`}>{OPEN}<span>Open</span></button>;
}
