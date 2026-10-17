import {useEffect,useRef,useState} from 'react';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import {projectTerminalController,terminalBytes,terminalIdle,type TerminalRun,type TerminalView} from './projectTerminalSession';
import '@xterm/xterm/css/xterm.css';
import './ProjectTerminal.css';
import {formatDate,t} from '@/i18n';

export type ProjectTerminalProps={projectId:string;projectName:string;visible:boolean;onHide:()=>void;
 /** The project's folder, shown before a shell starts. The shell's own folder wins once it runs. */
 cwd?:string};
type Tone='live'|'ready'|'quiet'|'needs';
/** The header's one status: what the shell is doing now, in a word or two, and the colour that means it. */
export function terminalStatus(view:TerminalView):{tone:Tone;label:string}{
 if(view.issue)return {tone:'needs',label:t('Needs you')};
 if(view.busy==='load')return {tone:'quiet',label:t('Connecting…')};
 if(view.busy==='start')return {tone:'quiet',label:t('Starting…')};
 if(view.busy==='close'||view.session?.state==='closing')return {tone:'quiet',label:t('Closing…')};
 const session=view.session;
 if(!session)return {tone:'quiet',label:t('Not started')};
 if(session.state==='exited')return {tone:'quiet',label:`Exited${session.exitCode===null?'':` (${session.exitCode})`}`};
 if(session.state==='closed')return {tone:'quiet',label:t('Closed')};
 return session.foreground?{tone:'live',label:t('Running {foreground}',{foreground:session.foreground})}:{tone:'ready',label:t('Ready')};
}
const retryLabel=(action:string)=>action==='input'?t('Retry input'):action==='input-limit'?t('Continue typing'):action==='close'?t('Retry close'):action==='start'?t('Retry start'):action==='resize'?t('Retry resize'):t('Reconnect');
/** The folder as a person reads it: the home folder as ~. */
export const shownFolder=(cwd:string|undefined)=>cwd?cwd.replace(/^\/Users\/[^/]+(?=\/|$)/,'~'):'';
const clock=(at:number)=>formatDate(new Date(at),{hour:'numeric',minute:'2-digit'},'en-US');

const PROMPT=<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m3 4 4 4-4 4M9 12h4"/></svg>;
const HIDE=<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M4 8h8"/></svg>;

/** A command a reply or Settings sent, as a labelled block: where it came from, when, and the command. The newest one
 * says Running while the shell runs something after it was sent. */
function RunBlock({run,running}:{run:TerminalRun;running:boolean}){
 return <li className="dv-terminal-run" data-running={running||undefined}>
  <span className="dv-terminal-run-meta">{run.label} · {clock(run.at)}{running&&<b> {t('· Running')}</b>}</span>
  <code title={run.command}>{run.command.split('\n').join(' ⏎ ')}</code>
 </li>;
}

/** Project-level dock. Hiding it never closes the shell; only Close does. Beta.15 design pass: Dervo's type, colours and
 * spacing, the folder and a running-command state in the header, both themes, and commands from replies as blocks. */
export function ProjectTerminal({projectId,projectName,visible,onHide,cwd}:ProjectTerminalProps){
 const controller=projectTerminalController(projectId);
 const [view,setView]=useState(controller.snapshot);
 const target=useRef<HTMLDivElement>(null),terminal=useRef<Terminal|null>(null),fit=useRef<FitAddon|null>(null);
 const visibleRef=useRef(visible);visibleRef.current=visible;
 useEffect(()=>controller.subscribe(setView),[controller]);
 useEffect(()=>{
  const element=target.current;if(!element)return;
  let alive=true;
  const style=getComputedStyle(document.documentElement);
  const instance=new Terminal({fontFamily:style.getPropertyValue('--dv-font-mono').trim()||'ui-monospace, monospace',fontSize:12.5,lineHeight:1.35,letterSpacing:0,scrollback:2000,cursorBlink:false,cursorStyle:'bar',cursorWidth:2,allowProposedApi:false,convertEol:false,disableStdin:true,screenReaderMode:true,linkHandler:{activate:()=>{}},windowOptions:{}});
  const sizing=new FitAddon();instance.loadAddon(sizing);instance.open(element);terminal.current=instance;fit.current=sizing;
  // Terminal output never receives permission to write the clipboard or open a URL.
  const clipboard=instance.parser.registerOscHandler(52,()=>true);
  const unicodeInput=instance.onData(data=>controller.enqueue(terminalBytes(data)));
  const binaryInput=instance.onBinary(data=>controller.enqueue(terminalBytes(data,true)));
  const writes=new Set<()=>void>();
  const detach=controller.attach({reset:()=>instance.reset(),write:bytes=>new Promise(resolve=>{writes.add(resolve);instance.write(bytes,()=>{writes.delete(resolve);resolve();});})});
  const updateTheme=()=>{
   const tokens=getComputedStyle(document.documentElement),color=(name:string,fallback:string)=>tokens.getPropertyValue(name).trim()||fallback;
   const dark=document.documentElement.classList.contains('dark')||tokens.colorScheme==='dark';
   // ANSI colours tuned to Dervo's palette: rust for red, live green, sage and pine, with ink and muted for the greys.
   const ansi=dark?{black:color('--dv-sunk','#2e2c29'),red:'#e0845b',green:'#80c993',yellow:'#d9bd78',blue:'#9fb8d6',magenta:'#c9a3c7',cyan:'#8fc4bd',white:'#cdc9c1',brightBlack:'#a5a198',brightRed:'#eba283',brightGreen:'#a6dbb2',brightYellow:'#e8d29e',brightBlue:'#bccfe6',brightMagenta:'#dcbfda',brightCyan:'#b0d8d2',brightWhite:'#eeece7'}
    :{black:'#1b1b19',red:'#b0461f',green:'#2f7844',yellow:'#8a6a1f',blue:'#3f6690',magenta:'#7f5680',cyan:'#2f6f6c',white:'#dcdbd4',brightBlack:'#696762',brightRed:'#9a3c19',brightGreen:'#22382b',brightYellow:'#735816',brightBlue:'#33567c',brightMagenta:'#6b476c',brightCyan:'#265c59',brightWhite:'#f7f6f3'};
   const surface=color('--dv-raised',dark?'#242220':'#ffffff');
   instance.options.theme={background:surface,foreground:color('--dv-ink',dark?'#eeece7':'#1b1b19'),cursor:color('--dv-live',dark?'#80c993':'#2f7844'),cursorAccent:surface,
    selectionBackground:color('--dv-line-strong',dark?'#433f3a':'#dcdbd4'),selectionInactiveBackground:color('--dv-line',dark?'#2d2b28':'#ebeae5'),...ansi};
  };
  updateTheme();const theme=new MutationObserver(updateTheme);theme.observe(document.documentElement,{attributes:true,attributeFilter:['class','style','data-dervo-local','data-dervo-palette']});
  const resize=()=>{if(!alive||!visibleRef.current||!element.clientWidth||!element.clientHeight)return;sizing.fit();controller.resize(instance.cols,instance.rows);};
  const observer=new ResizeObserver(resize);observer.observe(element);
  void document.fonts?.ready.then(resize);
  void controller.load();
  const timer=setInterval(()=>{void controller.read();},250);
  resize();
  return()=>{alive=false;clearInterval(timer);observer.disconnect();theme.disconnect();detach();for(const finish of writes)finish();writes.clear();unicodeInput.dispose();binaryInput.dispose();clipboard.dispose();instance.dispose();terminal.current=null;fit.current=null;};
 },[controller]);
 useEffect(()=>{
  if(terminal.current)terminal.current.options.disableStdin=!!view.busy||!!view.issue||view.session?.state!=='running';
 },[view]);
 useEffect(()=>{
  if(!visible)return;
  const frame=requestAnimationFrame(()=>{const instance=terminal.current;if(!instance||!target.current?.clientWidth)return;fit.current?.fit();controller.resize(instance.cols,instance.rows);if(view.session?.state==='running')instance.focus();});
  return()=>cancelAnimationFrame(frame);
 },[visible,controller,view.session?.sessionId,view.runs.length]);
 const running=view.session?.state==='running'||view.session?.state==='closing';
 const status=terminalStatus(view);
 const folder=shownFolder(view.session?.cwd??cwd);
 const runs=view.runs.filter(run=>run.sessionId===view.session?.sessionId);
 const idle=terminalIdle(view);
 return <section id="project-terminal" data-project-terminal className="dv-terminal" hidden={!visible} aria-label={t('Terminal for {projectName}',{projectName})} onKeyDown={event=>event.stopPropagation()}>
  <header className="dv-terminal-head">
   <div className="dv-terminal-title">
    <span className="dv-terminal-mark">{PROMPT}</span>
    <strong>Terminal</strong>
    <span className="dv-terminal-folder" title={view.session?.cwd??cwd??projectName}>{folder||projectName}</span>
   </div>
   <span className="dv-terminal-state" data-tone={status.tone} role="status"><i aria-hidden="true"/>{status.label}</span>
   <div className="dv-terminal-actions">
    {running?<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={!!view.busy} onClick={()=>void controller.close()} title={t('Stop this shell and everything it started')}>{t('Close')}</button>
     :<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!view.busy||!!view.issue} onClick={()=>void controller.start()}>{view.session?t('New terminal'):t('Start')}</button>}
    <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm dv-terminal-hide" aria-label={t('Hide terminal')} title={t('Hide. The shell keeps running.')} onClick={onHide}>{HIDE}</button>
   </div>
  </header>
  {view.issue&&<div className="dv-terminal-notice" role="alert"><span>{view.issue.message}</span><button type="button" className="dv-link" disabled={!!view.busy} onClick={()=>void controller.retry()}>{retryLabel(view.issue.action)}</button></div>}
  {view.lostOutput&&<p className="dv-terminal-notice" data-tone="quiet" role="status">{t('Earlier output is no longer available. The terminal shows what it kept.')}</p>}
  {runs.length>0&&<div className="dv-terminal-runs">
   <ol aria-label={t('Commands sent from replies and Settings')}>{runs.map((run,index)=><RunBlock key={run.id} run={run} running={index===runs.length-1&&!!view.session?.foreground}/>)}</ol>
   <button type="button" className="dv-link dv-terminal-clear" onClick={()=>controller.dismissRuns()}>{t('Clear')}</button>
  </div>}
  <div className="dv-terminal-body">
   <div ref={target} className="dv-terminal-screen" aria-label={t('Terminal input and output')}/>
   {idle&&<div className="dv-terminal-idle">
    <p>{t('A shell in this project’s folder.')}</p>
    <p className="dv-terminal-idle-note">{t('Press Start, or Run on a command in a reply. Nothing runs by itself.')}</p>
   </div>}
  </div>
 </section>;
}
