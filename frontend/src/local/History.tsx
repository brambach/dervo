/* History (agent history, 23 Sep 2026; grouped by thread in beta.16, b16/git): what each piece of work in the project
 * added up to, by day, newest first. A card is one thread: how it stands, who worked on it, what it changed and
 * checked, and its git line (commits, whether they're on GitHub, the pull request). Its runs sit inside; open one and it
 * says what you asked, what it was told, what it did, what it said (quoted, and only there) and what it's linked to.
 * From here: a run's record (RunRecord.tsx) and a file's trace (TraceFile.tsx), in the same main column.
 *
 * Built to evidence/design-tests/b16-git/grouped-* (approved 25 Sep). One accent per view: rust marks only the newest
 * card waiting on you; merged pull requests are sage. No lanes, no connector lines, no coloured side bars. */
import {useEffect,useMemo,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {keepPreviousData,useQuery} from '@tanstack/react-query';
import {Search} from 'lucide-react';
import {StatusGlyph} from './StatusGlyph';
import {Coded,LinkText,Quoted,WhoMark,answerFrom,type HistoryActions as Actions,type HistoryScope as Scope} from './HistoryParts';
import {PrIcon,PullRequestBox,openOnGitHub} from './PullRequest';
import {prChip,prStatus,type PullRequest} from './github';
import {RunRecordPage} from './RunRecord';
import {TraceFilePage} from './TraceFile';
import {byDay,clock,didLines,historyList,runLine,span,workSinceLine,type HistoryFilter,type HistoryLink,type HistoryPlace,type HistoryRow,type WorkItem} from './runHistory';
import './History.css';
import {t} from '@/i18n';

function Facts({row,actions}:{row:HistoryRow;actions:Actions}){
 // The words that started it come first when an agent wrote them (a proposal, a question, Home's instruction).
 const said=row.said.filter(item=>item.source!=='reply'&&item.source!=='limits')
  .concat(row.said.filter(item=>item.source==='reply').slice(0,2),row.said.filter(item=>item.source==='limits'));
 const openLink=(link:HistoryLink)=>{if(link.taskId)actions.onRecord(link.taskId);};
 return <dl className="dv-facts dervo-history-facts">
  {row.origin&&<div className="dv-facts-row"><dt>{t('Started')}</dt><dd>{row.origin.line}</dd></div>}
  {row.asked?.text&&row.asked.where!=='answer'&&<div className="dv-facts-row"><dt>{t('Asked')}</dt><dd><q>{row.asked.text}</q>{' '}<span className="dervo-history-from">{row.asked.where==='home'?t('You, on Home'):t('You')}{row.asked.at?`, ${clock(row.asked.at)}`:''}</span></dd></div>}
  {row.asked?.text&&row.asked.where==='answer'&&<div className="dv-facts-row"><dt>{t('Answered')}</dt><dd><q>{row.asked.text}</q>{' '}<span className="dervo-history-from">{answerFrom(row.asked.picked)}{row.asked.at?`, ${clock(row.asked.at)}`:''}</span></dd></div>}
  {row.kind==='run'&&<div className="dv-facts-row"><dt>{t('Told')}</dt><dd>{row.told??t('Not recorded for this run. It ran before Dervo kept what each run was told.')}</dd></div>}
  {row.kind==='run'&&<div className="dv-facts-row"><dt>{t('Did')}</dt><dd>{didLines(row).map((line,index)=><span key={index} className="dervo-history-did"><Coded text={line}/>{' '}</span>)}</dd></div>}
  {said.length>0&&<div className="dv-facts-row"><dt>{t('It said')}</dt><dd className="dervo-history-said">{said.map((item,index)=><span key={index}><Quoted said={item}/>{' '}</span>)}</dd></div>}
  {row.links.length>0&&<div className="dv-facts-row"><dt>{t('Linked')}</dt><dd>{row.links.map((link,index)=><span key={index}><LinkText link={link} onOpen={openLink}/>{' '}</span>)}</dd></div>}
 </dl>;
}

/* ---------------------------------------------------------------- pieces of work (beta.16, b16/git)
 * One card per thread (evidence/design-tests/b16-git/grouped-*, approved 25 Sep): its name and how it stands, who worked
 * on it and what it added up to, then the git line: each commit, whether it's on GitHub, and the pull request. Runs sit
 * inside as quiet lines; a line opens today's facts, and Record opens the run's record. An archived thread keeps its
 * place, outlined, and opening it brings it back. */
const CommitIcon=()=><svg className="dervo-pr-i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.6"/><path d="M1.5 8h3.9M10.6 8h3.9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>;
const CloudIcon=()=><svg className="dervo-pr-i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13V6.5M5.5 9 8 6.5 10.5 9" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/><path d="M3 3.5h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>;
const LocalIcon=()=><svg className="dervo-pr-i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="2" y="3" width="12" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5"/><path d="M5.5 13.5h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>;

/** The git line: each commit, where it is, and the pull request. Shape and words carry the state, not colour. */
function GitLine({item}:{item:WorkItem}){
 const live=item.commits.filter(commit=>!commit.reverted);
 const pr=item.pr&&item.pr.number?item.pr:null;
 if(!item.commits.length&&!pr)return <div className="dervo-work-git"><span className="dervo-work-none">{item.glyph==='needs'||item.glyph==='running'?t('Nothing committed yet'):t('Nothing committed')}</span></div>;
 return <div className="dervo-work-git">
  {item.commits.map(commit=><span key={commit.sha} className="dervo-git-chip" data-kind="commit" data-reverted={commit.reverted||undefined}
   title={commit.reverted?t('Committed, then undone'):undefined}><CommitIcon/><code>{commit.short}</code>{commit.reverted&&<span className="sr-only"> (undone)</span>}</span>)}
  {live.length>0&&item.where==='github'&&<span className="dervo-git-chip" data-kind="where"><CloudIcon/>{t('On GitHub')}</span>}
  {live.length>0&&item.where==='local'&&<span className="dervo-git-chip" data-kind="where"><LocalIcon/>{t('Not on GitHub')}</span>}
  {pr&&<span className="dervo-git-chip" data-kind={pr.state==='merged'?'merged':'pr'}><PrIcon merged={pr.state==='merged'}/>{prChip({number:pr.number!,draft:pr.draft,state:pr.state??'open',review:pr.review})}</span>}
 </div>;
}

function Agents({item,rows}:{item:WorkItem;rows:HistoryRow[]}){
 return <span className="dervo-work-agents">{item.agents.map((label,index)=>{
  const provider=rows.find(row=>row.providerLabel===label)?.provider;
  return <span key={label} className="dervo-work-agent">{index>0&&<span className="dervo-history-sep" aria-hidden="true">{t('then')}</span>}<WhoMark provider={provider}/>{label}</span>;})}</span>;
}

/** The pull request on the open card, read again from GitHub as you when the card opens (pr_status refresh). */
function WorkPrBox({item,scope}:{item:WorkItem;scope:Scope}){
 const read=useQuery({queryKey:['pr-status',scope.projectId,item.channelId],queryFn:()=>prStatus(scope.projectId,item.channelId,true),retry:false,staleTime:15000});
 const pr:PullRequest|null=read.data?.pr??(item.pr&&item.pr.number?{...item.pr,number:item.pr.number,state:item.pr.state??'open'} as PullRequest:null);
 return pr?<PullRequestBox pr={pr} head={false} className="dervo-work-pr"/>:null;
}

function WorkCard({item,rows,open,accent,onToggle,actions,scope,linked}:{item:WorkItem;rows:HistoryRow[];open:boolean;accent:boolean;onToggle:()=>void;actions:Actions;scope:Scope;linked:boolean}){
 const [facts,setFacts]=useState<string|null>(null);
 const mine=item.runs.map(key=>rows.find(row=>row.key===key)).filter((row):row is HistoryRow=>!!row);
 const runs=mine.filter(row=>row.kind==='run');
 const showRuns=open||mine.length>1;
 const pr=item.pr&&item.pr.number?item.pr:null;
 const tidiedDays=item.tidied?.at&&item.tidied.quietSince?Math.round((item.tidied.at-item.tidied.quietSince)/86400):null;
 const firstFile=item.files[0];
 return <li className={`dervo-work${open?' dv-open':''}`} data-archived={item.archived||undefined} data-linked={linked||undefined} data-testid="history-work">
  <StatusGlyph state={item.glyph} accent={accent} label={item.state}/>
  <div className="dervo-work-main">
   <button type="button" className="dervo-work-head" aria-expanded={open} onClick={onToggle}>
    <span className="dv-row-name">{item.name}</span>{item.archived&&<span className="dv-tag dervo-work-archived">{t('Archived')}</span>}
    <span className="dv-state" data-accent={accent||undefined}>{item.state}</span></button>
   <p className="dervo-work-sum"><Agents item={item} rows={runs}/>{item.summary&&<><span className="dervo-history-sep" aria-hidden="true">·</span> {item.summary}.</>}</p>
   <GitLine item={item}/>
  </div>
  <div className="dv-row-end"><span className="dv-when">{span(item.startedAt,item.endedAt)}</span></div>
  <div className="dervo-work-more">
   {showRuns&&<ol className="dervo-work-runs">{(open?mine:mine.slice(0,3)).map(row=>{
    const number=runs.indexOf(row)+1;
    return <li key={row.key} data-open={facts===row.key||undefined}>
     <StatusGlyph state={row.glyph} label={row.ended}/>
     <button type="button" className="dervo-work-run" aria-expanded={open?facts===row.key:undefined} disabled={!open}
      onClick={()=>setFacts(key=>key===row.key?null:row.key)}><WhoMark provider={row.provider} home={row.kind==='home'}/><span>{runLine(row,number)}</span></button>
     {open&&row.kind==='run'&&row.taskId&&<button type="button" className="dv-btn dv-btn-quiet dervo-work-record" onClick={()=>actions.onRecord(row.taskId!,row.runId??undefined)}>{t('Record')}</button>}
     <span className="dv-when">{span(row.startedAt,row.endedAt)}</span>
     {open&&facts===row.key&&<div className="dervo-work-facts"><Facts row={row} actions={actions}/></div>}
    </li>;})}</ol>}
   {open&&pr&&<WorkPrBox item={item} scope={scope}/>}
   {open&&item.pr&&!pr&&item.pr.detail&&<p className="dervo-work-note">{item.pr.detail}</p>}
   {open?<div className="dv-row-actions">
    {item.reviewTaskId?<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>actions.onOpenTask(item.channelId,item.reviewTaskId!)}>{t('Review')}</button>
     :pr?.url&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>openOnGitHub(pr.url)}>{t('Open on GitHub')}</button>}
    <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>actions.onOpenThread(item.channelId)}>{t('Open the thread')}</button>
    {firstFile&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>actions.onTrace(firstFile)}>{t('Trace a file')}</button>}
   </div>:item.reviewTaskId&&<div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>actions.onOpenTask(item.channelId,item.reviewTaskId!)}>{t('Review')}</button></div>}
   {item.archived&&<p className="dervo-work-note">{tidiedDays!==null?t('Archived after {tidiedDays} quiet days.',{tidiedDays}):t('Archived.')} {t('Opening it brings it back.')}</p>}
  </div>
 </li>;
}

const FILTER_LABELS:[HistoryFilter,string,'work'|'waiting'|'local'|'prs'][]=[['all',t('All'),'work'],['waiting',t('Waiting on you'),'waiting'],['local',t('Not on GitHub'),'local'],['prs',t('Pull requests'),'prs']];

export function HistoryLog({scope,actions,now}:{scope:Scope;actions:Actions;now?:number}){
 const [filter,setFilter]=useState<HistoryFilter>('all');
 const [typed,setTyped]=useState(''),[file,setFile]=useState('');
 useEffect(()=>{const timer=setTimeout(()=>setFile(typed.trim()),250);return ()=>clearTimeout(timer);},[typed]);
 const listing=useQuery({queryKey:['history-list',scope.projectId,scope.profileId,filter,file],queryFn:()=>historyList(scope,filter,'',file),placeholderData:keepPreviousData});
 const rows=useMemo(()=>listing.data?.runs??[],[listing.data]);
 const work=useMemo(()=>listing.data?.work??[],[listing.data]);
 const [openKey,setOpenKey]=useState<string|null>(null);
 const heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{requestAnimationFrame(()=>heading.current?.focus());},[]);
 // One accent per view: the newest piece of work waiting on you.
 const accent=work.find(item=>item.glyph==='needs')?.key;
 const open=work.find(item=>item.key===openKey);
 const linkedRows=new Set(open?open.runs.flatMap(key=>rows.find(row=>row.key===key)?.linked??[]):[]);
 const linked=(item:WorkItem)=>!!open&&item.key!==open.key&&item.runs.some(key=>linkedRows.has(key));
 const counts=listing.data?.counts;
 const narrowed=!!file||filter!=='all';
 return <div className="dv-column dervo-history-body"><div className="dv-measure">
  <header className="dervo-history-head-row">
   <h1 ref={heading} tabIndex={-1} className="dv-display-1">{t('History')}</h1>
   <span className="dv-caption" data-testid="history-since">{listing.data?workSinceLine(work,rows,now):listing.isError?'':t('Reading the records…')}</span>
  </header>
  <div className="dervo-history-filters">
   <div className="dv-seg" role="radiogroup" aria-label={t('Show')}>
    {FILTER_LABELS.map(([value,label,count])=><button key={value} type="button" role="radio" aria-checked={filter===value} onClick={()=>setFilter(value)}>
     {label}{counts&&counts[count]!==undefined&&<span className="dervo-history-count">{counts[count]}</span>}</button>)}
   </div>
   <label className="dervo-history-find"><Search size={15} aria-hidden="true"/><span className="sr-only">{t('Filter by file')}</span>
    <input type="search" value={typed} onChange={event=>setTyped(event.target.value)} placeholder={t('Filter by file')} aria-label={t('Filter by file')} spellCheck={false}/></label>
  </div>
  {listing.isError&&<p role="alert" className="dervo-history-error">{t('History couldn’t load. {why}',{why:listing.error instanceof Error?listing.error.message:String(listing.error)})}{' '}
   <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void listing.refetch()}>{t('Try again')}</button></p>}
  {listing.data&&!work.length&&<div className="dv-quiet"><h2 className="dv-display-2">{narrowed?t('Nothing matches'):t('Nothing has run yet')}</h2>
   <p className="dv-small">{narrowed?(filter==='prs'?t('Open a pull request from a finished thread and it shows here.'):filter==='local'?t('Everything committed here is on GitHub.'):t('Try All, or another file name.'))
    :t('Every thread in this project shows here once it runs: what it added up to, its commits and its pull request.')}</p></div>}
  {byDay(work,now).map(day=><section key={day.day} className="dervo-history-day" aria-label={day.day}>
   <h2 className="dv-label">{day.day}</h2>
   <ul className="dervo-work-list">
    {day.rows.map(item=><WorkCard key={item.key} item={item} rows={rows} open={item.key===openKey} accent={item.key===accent} linked={linked(item)} scope={scope}
     onToggle={()=>setOpenKey(key=>key===item.key?null:item.key)} actions={actions}/>)}
   </ul>
  </section>)}
  {listing.data&&(listing.data.more>0||listing.data.incomplete)&&<p className="dv-caption dervo-history-more">
   {listing.data.more>0?`${t('{count} older pieces of work aren’t shown.',{count:listing.data.more})} `:''}{listing.data.note??(listing.data.incomplete?t('Some older records were too large or too old to read here.'):'')}</p>}
 </div></div>;
}

type Props={projectId:string;profileId:string;projectName?:string;open:boolean;place?:HistoryPlace;placeRequest?:number;returnLabel?:string;onClose:()=>void;
 onOpenThread:(channelId:string)=>void;onOpenTask:(channelId:string,taskId:string)=>void;now?:number};

/** History in the main column: the log, one run's record or a file's trace, with one way back each. */
export function HistoryScreen(props:Props){
 const {projectId,profileId,open}=props;
 const [place,setPlace]=useState<HistoryPlace>(props.place??{view:'log'});
 useEffect(()=>{if(props.place)setPlace(props.place);},[props.placeRequest]);
 if(!open)return null;
 const scope={projectId,profileId};
 const actions:Actions={onRecord:(taskId,runId)=>setPlace({view:'record',taskId,runId}),onTrace:path=>setPlace({view:'trace',path}),
  onOpenThread:props.onOpenThread,onOpenTask:props.onOpenTask};
 const here:ReactNode=place.view==='log'?null:place.view==='record'?t('Record'):place.path||t('Trace a file');
 function keys(event:KeyboardEvent){
  if(event.key!=='Escape'||event.defaultPrevented)return;
  const target=event.target as HTMLElement;
  if(target.closest('input,textarea,select'))return;
  event.stopPropagation();
  if(place.view!=='log')setPlace({view:'log'});else props.onClose();
 }
 return <section aria-label={t('History')} className="dervo-history-workspace" data-view={place.view} onKeyDown={keys} data-testid="history">
  <header className="dv-main-head dervo-history-bar"><span className="dv-title">{props.projectName??t('Your project')}</span>
   <span className="dv-crumb">{here?<><button type="button" className="dervo-history-crumb" onClick={()=>setPlace({view:'log'})}>{t('History')}</button><span className="dv-crumb-sep" aria-hidden="true">/</span><span className="dv-here" aria-current="page">{here}</span></>:t('History')}</span>
   <div className="dv-tools"><button type="button" className="dv-btn dv-btn-quiet dervo-history-return" onClick={props.onClose}>{props.returnLabel??t('Back to project')}</button></div></header>
  {place.view==='log'&&<HistoryLog scope={scope} actions={actions} now={props.now}/>}
  {place.view==='record'&&<RunRecordPage key={`${place.taskId}:${place.runId??''}`} scope={scope} taskId={place.taskId} runId={place.runId} actions={actions}/>}
  {place.view==='trace'&&<TraceFilePage key={`${place.path}:${place.line??0}`} scope={scope} path={place.path} line={place.line} actions={actions}
   onPath={(path,line)=>setPlace({view:'trace',path,line})}/>}
 </section>;
}
