/* A file's trace (agent history, first slice): its lines with the run that wrote each block beside them, and for the
 * line you pick, why it's there: the change it belongs to, who wrote it and how, the step it had reported, what it read
 * and was told first, your words, and the check that ran after. A line nobody wrote in Dervo says so and shows git's
 * author. Built toward reports/review-20260923/agent-history/c-trace-file.html.
 *
 * The selected line and the run that wrote it share the pale green highlight. Nothing else is coloured. */
import {useEffect,useRef,useState,type FormEvent,type ReactNode} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Quoted,WhoMark,answerFrom,type HistoryActions,type HistoryScope} from './HistoryParts';
import {checkLine,clock,dayInLine,traceFile,traceLine,writtenLine,type TraceBlock,type TraceFileView,type TraceLine} from './runHistory';
import './TraceFile.css';
import {t,tr} from '@/i18n';

/** The line a trace opens on when you didn't name one: the newest change a run made here (the host opens the window
 * on it), else the first block a run wrote that isn't committed, else the first a run wrote, else none. */
export function defaultLine(view:TraceFileView):number{
 if(view.focus&&view.focus>=view.first&&view.focus<=view.last)return view.focus;
 const pending=view.blocks.find(block=>block.taskId&&!block.committed);
 const written=view.blocks.find(block=>block.taskId);
 return (pending??written)?.first??0;
}

function WhyFacts({why}:{why:TraceLine}){
 const rows:[string,ReactNode][]=[];
 if(why.change)rows.push([t('Change'),why.change]);
 if(why.run&&why.written)rows.push([t('Written'),writtenLine(why)]);
 if(why.nobody)rows.push([t('Written'),<>{why.nobody}{why.author?.name?<> {tr('Git says {name} wrote it{when}.',{name:<b>{why.author.name}</b>,when:why.author.at?`, ${t('{day} at {time}',{day:dayInLine(why.author.at),time:clock(why.author.at)})}`:''})}</>:''}</>]);
 if(why.author?.subject&&why.nobody)rows.push([t('Commit'),<q>{why.author.subject}</q>]);
 if(why.step)rows.push([t('Its step'),<><q>{why.step.text}</q>{why.step.at?<> <span className="dervo-history-from">{t('Reported at {time}',{time:clock(why.step.at)})}</span></>:null}</>]);
 if(why.before)rows.push([t('Before that'),why.before]);
 // Only your own words go under "You asked" or "You answered". An agent's (a proposal, the question you answered,
 // Home's instruction with no message of yours on record) go under "It said", quoted as theirs.
 if(why.origin)rows.push([t('Started'),why.origin.line]);
 if(why.asked?.text&&why.asked.where!=='answer')rows.push([t('You asked'),<><q>{why.asked.text}</q>{' '}<span className="dervo-history-from">{why.asked.at?clock(why.asked.at):''}{why.asked.where==='home'?` ${t('on Home')}`:''}</span></>]);
 if(why.asked?.text&&why.asked.where==='answer')rows.push([t('You answered'),<><q>{why.asked.text}</q>{' '}<span className="dervo-history-from">{answerFrom(why.asked.picked)}{why.asked.at?`, ${clock(why.asked.at)}`:''}</span></>]);
 if(why.origin?.quote)rows.push([why.origin.kind==='home'?t('Home wrote'):t('It said'),<Quoted said={{text:why.origin.quote,from:why.origin.kind==='home'?undefined:why.origin.quoteFrom??undefined}}/>]);
 if(why.checked)rows.push([t('Checked'),why.checked.at?checkLine(why.checked):why.checked.text]);
 return <dl className="dervo-trace-why">{rows.map(([label,value],index)=><div key={index}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function PathForm({onPath,path=''}:{onPath:(path:string,line?:number)=>void;path?:string}){
 const [value,setValue]=useState(path);
 function submit(event:FormEvent){
  event.preventDefault();
  const match=value.trim().match(/^(.*?)(?::(\d+))?$/);
  if(match?.[1])onPath(match[1],match[2]?Number(match[2]):undefined);
 }
 return <form className="dervo-trace-pick" onSubmit={submit}>
  <label className="dervo-history-find"><span className="sr-only">{t('File to trace')}</span>
   <input value={value} onChange={event=>setValue(event.target.value)} placeholder={t('A file in this project, like backend/host.py or backend/host.py:120')} aria-label={t('File to trace')} spellCheck={false} autoFocus/></label>
  <button type="submit" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!value.trim()}>{t('Trace')}</button>
 </form>;
}

/** A long file shows 80 lines at a time; this moves the window. */
function GoToLine({first,last,total,onLine}:{first:number;last:number;total:number;onLine:(line:number)=>void}){
 const [value,setValue]=useState('');
 const line=Number(value);
 const valid=Number.isInteger(line)&&line>=1&&line<=total;
 return <form className="dervo-trace-goto" onSubmit={event=>{event.preventDefault();if(valid)onLine(line);}}>
  <span className="dv-caption">{t('Showing lines {first} to {last} of {total}.',{first,last,total})}</span>
  <label><span className="dv-caption">{t('Go to line')}</span><input className="dv-input" inputMode="numeric" value={value} onChange={event=>setValue(event.target.value.replace(/[^0-9]/g,''))} aria-label={t('Go to line')}/></label>
  <button type="submit" className="dv-btn dv-btn-quiet" disabled={!valid}>Go</button>
 </form>;
}

export function TraceFilePage({scope,path,line,actions,onPath}:{scope:HistoryScope;path:string;line?:number;actions:HistoryActions;onPath:(path:string,line?:number)=>void}){
 const file=useQuery({queryKey:['trace-file',scope.projectId,scope.profileId,path,line??0],queryFn:()=>traceFile(scope,path,line??0),enabled:!!path,retry:false});
 const [picked,setPicked]=useState<number|null>(line??null);
 const [view,setView]=useState<'lines'|'runs'>('lines');
 const selected=picked??(file.data?defaultLine(file.data):0);
 const why=useQuery({queryKey:['trace-line',scope.projectId,scope.profileId,path,selected],queryFn:()=>traceLine(scope,path,selected),enabled:!!path&&selected>0,retry:false});
 const code=useRef<HTMLDivElement>(null);
 // The picked line stays in view inside the code box, centred when it opens and moved only as far as needed after.
 const placed=useRef(false);
 useEffect(()=>{
  const box=code.current,row=box?.querySelector<HTMLElement>('[data-selected]');
  if(!box||!row)return;
  const top=row.offsetTop,bottom=top+row.offsetHeight;
  if(!placed.current)box.scrollTop=Math.max(0,top-(box.clientHeight-row.offsetHeight)/2);
  else if(top<box.scrollTop||bottom>box.scrollTop+box.clientHeight)box.scrollTop=Math.max(0,top-box.clientHeight/3);
  placed.current=true;
 },[file.data,selected]);
 if(!path)return <div className="dv-column dervo-trace-body"><div className="dv-measure">
  <header className="dervo-trace-head"><div><h1 className="dv-display-2">{t('Trace a file')}</h1><span className="dv-caption">{t('Pick a file to see which run wrote each line, and why.')}</span></div></header>
  <PathForm onPath={onPath}/>
 </div></div>;
 if(file.isError)return <div className="dv-column dervo-trace-body"><div className="dv-measure">
  <header className="dervo-trace-head"><div><h1 className="dervo-trace-name">{path.split('/').pop()}</h1></div></header>
  <p role="alert" className="dervo-history-error">{file.error instanceof Error?file.error.message:String(file.error)}</p>
  <PathForm onPath={onPath} path={path}/>
 </div></div>;
 if(!file.data)return <div className="dv-column dervo-trace-body"><div className="dv-measure"><p role="status" className="dv-caption">{t('Reading {path}…',{path})}</p></div></div>;
 const data=file.data;
 const blockAt=new Map<number,TraceBlock>();
 for(const block of data.blocks)blockAt.set(block.first,block);
 const writer=why.data?.run?.taskId;
 const caption=[data.folder,t('{count} changes in Dervo’s records',{count:data.changes}),data.uncommitted?t('{uncommitted} not committed',{uncommitted:data.uncommitted}):''].filter(Boolean).join(' · ');
 return <div className="dv-column dervo-trace-body" data-testid="trace-file"><div className="dv-measure">
  <header className="dervo-trace-head">
   <div><h1 className="dervo-trace-name" tabIndex={-1} title={data.path}>{data.name}</h1><span className="dv-caption">{caption}</span></div>
   <div className="dv-seg" role="radiogroup" aria-label={t('View')}>
    <button type="button" role="radio" aria-checked={view==='lines'} onClick={()=>setView('lines')}>{t('Lines')}</button>
    <button type="button" role="radio" aria-checked={view==='runs'} onClick={()=>setView('runs')}>{t('Runs')}</button>
   </div>
  </header>
  {data.note&&<p className="dv-caption dervo-trace-note">{data.note}</p>}
  {view==='lines'&&<>
   <div ref={code} className="dervo-trace-code" role="region" aria-label={data.total?t('Lines {first} to {last} of {total}',{first:data.first,last:data.last,total:data.total}):t('An empty file')} tabIndex={0}>
    {data.total?<table><tbody>{data.lines.map(row=>{
     const block=blockAt.get(row.n);
     const on=row.n===selected;
     return <tr key={row.n} data-selected={on||undefined} onClick={()=>setPicked(row.n)}>
      {/* One cell per block, its name held at the top while the block scrolls by. */}
      {block&&<td className="blame" rowSpan={block.last-block.first+1}><span title={block.committed?(block.sha?t('Commit {sha}',{sha:block.sha}):t('Committed')):t('Not committed')}>{block.label}</span></td>}
      <td className="ln"><button type="button" aria-label={t('Why line {n} is here',{n:row.n})} aria-pressed={on} onClick={event=>{event.stopPropagation();setPicked(row.n);}}>{row.n}</button></td>
      <td className="src">{row.text||' '}</td>
     </tr>;})}</tbody></table>:<p className="dervo-record-none">{t('This file is empty.')}</p>}
   </div>
   {(data.first>1||data.last<data.total)&&<GoToLine first={data.first} last={data.last} total={data.total} onLine={n=>onPath(data.path,n)}/>}
  </>}
  <div className="dervo-trace-below" data-view={view}>
   {view==='lines'&&<section aria-labelledby="trace-why">
    <h2 id="trace-why" className="dv-label">{selected?t('Why line {selected} is here',{selected}):t('Why a line is here')}</h2>
    {!selected&&<p className="dervo-record-none">{t('Pick a line number to see who wrote it and why.')}</p>}
    {selected>0&&why.isError&&<p role="alert" className="dervo-history-error">{why.error instanceof Error?why.error.message:String(why.error)}</p>}
    {selected>0&&!why.data&&!why.isError&&<p role="status" className="dv-caption">{t('Tracing line {line}…',{line:selected})}</p>}
    {why.data&&why.data.line===selected&&<>
     {why.data.note&&why.data.note!==data.note&&<p className="dv-caption dervo-trace-note" role="status">{why.data.note}</p>}
     <WhyFacts why={why.data}/>
     {why.data.run&&<div className="dervo-trace-actions">
      <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>actions.onRecord(why.data!.run!.taskId,why.data!.run!.runId)}>{t('Open the record')}</button>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>actions.onOpenThread(why.data!.run!.channelId)}>{t('Open the thread')}</button>
     </div>}
    </>}
   </section>}
   <section aria-labelledby="trace-runs">
    <h2 id="trace-runs" className="dv-label">{t('Runs on this file')}</h2>
    {data.runs.length?<ul className="dervo-trace-runs">{data.runs.map(run=><li key={`${run.taskId}:${run.runIndex}`} data-linked={run.taskId===writer||undefined}>
     <button type="button" onClick={()=>actions.onRecord(run.taskId,run.runId)}>
      <WhoMark provider={run.provider}/>
      <span><b>{run.name}</b><small>{run.at?`${clock(run.at)} · `:''}{run.what}</small></span>
     </button></li>)}</ul>:<p className="dervo-record-none">{t('No run in Dervo’s records has read or changed this file.')}</p>}
    <span className="dv-caption dervo-trace-foot">{t('Anything older comes from git. A line nobody wrote in Dervo says so.')}</span>
   </section>
  </div>
 </div></div>;
}
