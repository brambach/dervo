import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {Button} from '@/shared/ui/button';
import {Input} from '@/shared/ui/input';
import {ArrowLeft,ArrowUpRight,RefreshCw,Search,X} from 'lucide-react';
import {agentLabel} from './collaboration';
import './WorkspaceSearch.css';
import {CatchupActions} from './CatchupActions';
import {workspaceRequest,type WorkspaceScope,type WorkspaceRow,type WorkspaceExcerpt,type WorkspacePage} from './api';
import {formatDate,t} from '@/i18n';
import {blockedEnding} from './runEnding';

export type SearchMode='conversation'|'project'|'catchup';
/** ``request`` runs one project search for the given words, as ⌘K's search row asks; ``onRequestHandled`` lets the shell clear it. */
type Props={projectId:string;channelId:string;profileId:string;projectName?:string;mode:SearchMode;open:boolean;onClose:()=>void;returnLabel?:string;onOpenConversation?:(channelId:string)=>void;onOpenTask?:(channelId:string,taskId:string)=>void;onOpenKnowledge?:()=>void;request?:{query:string;id:number}|null;onRequestHandled?:()=>void;onScope?:(mode:'project'|'conversation')=>void;conversationName?:string};
/** A saved time, as the clock time today or the short date otherwise. Never relative words, which go stale on screen. */
export function whenSaved(seconds?:number|null):string {
  if(!seconds)return '';
  const date=new Date(seconds*1000),now=new Date();
  const sameDay=date.toDateString()===now.toDateString();
  return sameDay?formatDate(date,{hour:'2-digit',minute:'2-digit'}):formatDate(date,{day:'numeric',month:'short'})+' '+formatDate(date,{hour:'2-digit',minute:'2-digit'});
}
/** The row's most telling time: completion, then answer, then request. */
export function rowTime(row:WorkspaceRow):{label:string;at:number}|undefined {
  if(row.completedAt)return {label:t('Completed'),at:row.completedAt};
  if(row.answeredAt)return {label:t('Answered'),at:row.answeredAt};
  if(row.askedAt)return {label:t('Asked'),at:row.askedAt};
  return undefined;
}
const label=(mode:SearchMode)=>mode==='conversation'?t('Search this conversation'):mode==='project'?t('Search project'):t('Project catch-up');
function queryKey(p:Props){return JSON.stringify(['dervo-search',p.projectId,p.profileId,p.mode==='conversation'?p.channelId:'project']);}
function savedQuery(key:string){try{return sessionStorage.getItem(key)??'';}catch{return '';}}

/** Saved text is display-only: HTML, remote media and navigation stay inert. */
function SavedText({text}:{text:string}){
  return <div className="dervo-saved-text space-y-2 break-words leading-relaxed [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1 [&_pre]:whitespace-pre-wrap [&_pre]:rounded-md [&_pre]:bg-muted/60 [&_pre]:p-3">
    <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{a:({children})=><span>{children}</span>,img:({alt})=><span className="text-muted-foreground">{alt?t('[Image: {alt}]',{alt}):'[Image]'}</span>,table:({children})=><div className="dervo-saved-table" role="region" aria-label={t('Saved answer table')} tabIndex={0}><table>{children}</table></div>}}>{text}</ReactMarkdown>
  </div>;
}
/** List markers and clipped Markdown blocks shouldn't dominate a short preview. */
type PreviewNode={type:string;value?:string;children?:PreviewNode[];tagName?:string;properties?:Record<string,unknown>};
function matchingText(query:string){
  const escaped=query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  return ()=>(tree:PreviewNode)=>{
    if(!escaped)return;
    const pattern=new RegExp(`(${escaped})`,'gi');
    // Keep empty split pieces until their match positions have been assigned.
    function highlight(node:PreviewNode){
      if(!node.children)return;
      node.children=node.children.flatMap(child=>{
        if(child.type!=='text'||!child.value){highlight(child);return [child];}
        return child.value.split(pattern).map((part,index):PreviewNode=>index%2===1?{type:'element',tagName:'mark',properties:{},children:[{type:'text',value:part}]}:{type:'text',value:part}).filter(child=>child.type!=='text'||child.value);
      });
    }
    highlight(tree);
  };
}
function SearchPreview({text,query=''}:{text:string;query?:string}){
  const leading=text.startsWith('…'),trailing=text.length>1&&text.endsWith('…');
  const body=text.slice(leading?1:0,trailing?-1:undefined);
  const block=({children}:{children?:React.ReactNode})=><span>{children}{' '}</span>;
  return <div className="dervo-saved-text break-words leading-relaxed">{leading&&'…'}<ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} rehypePlugins={[matchingText(query)]} components={{
    p:block,h1:block,h2:block,h3:block,h4:block,h5:block,h6:block,
    ul:block,ol:block,li:block,pre:block,blockquote:block,table:block,thead:block,tbody:block,tr:block,th:block,td:block,
    a:({children})=><span>{children}</span>,
    img:({alt})=><span>{alt?t('[Image: {alt}]',{alt}):'[Image]'}</span>,
    br:()=> <span>{' '}</span>,
  }}>{body}</ReactMarkdown>{trailing&&'…'}</div>;
}
function SourceRecord({excerpt}:{excerpt:WorkspaceExcerpt}){
  const [expanded,setExpanded]=useState(false);
  return <details className="rounded-lg border p-3 text-xs" onToggle={event=>setExpanded(event.currentTarget.open)}><summary className="cursor-pointer">{t('Source record')}</summary><p className="my-3 break-all text-muted-foreground">{t('Source ID: {id}',{id:excerpt.id})}</p>{excerpt.kind==='task'&&<dl><dt>{t('Recorded status')}</dt><dd>{excerpt.status??t('unknown')}</dd><dt>{t('Recorded decision')}</dt><dd>{excerpt.humanDecision??t('Not recorded')}</dd></dl>}{expanded&&<pre className="whitespace-pre-wrap break-words">{excerpt.text||t('No saved text.')}</pre>}</details>;
}
function known(value?:string){return value&&!value.startsWith('Unknown:')?value:undefined;}
function readLabel(row:WorkspaceRow){return row.matchRole==='user'?t('Read request'):row.matchRole==='assistant'||row.kind==='task'&&row.hasAnswer!==false?t('Read answer'):t('Read conversation');}
function helperTitle(row:WorkspaceRow){
  const match=row.helper&&row.title?.match(/^(Review|Help) by @(review-[a-f0-9]{24})$/);
  return match?(match[1]==='Review'?t('Independent review'):t('Help from {agent}',{agent:agentLabel(match[2])})):row.title;
}
function helperOwner(row:WorkspaceRow){
  const match=row.helper&&row.owner?.match(/^(claude|codex) · (review-[a-f0-9]{24})$/);
  return match?`${match[1]==='claude'?'Claude Code':'Codex'} · ${agentLabel(match[2])}`:row.owner;
}
export function savedStatus(row:WorkspaceRow){
  if(row.kind!=='task')return ({queued:row.kind==='message'?t('Queued locally'):t('Was queued'),sending:t('Was sending'),running:t('Was in progress'),stopping:t('Was stopping'),failed:t('Couldn’t finish'),interrupted:t('Stopped'),cancelled:t('Stopped'),uncertain:t('Outcome unconfirmed'),unknown:t('Outcome unconfirmed'),lost:t('Outcome unconfirmed')}[row.status??'']??(row.matchRole==='assistant'?t('Answer saved'):t('Request saved')));
  if(row.outcomeUncertain)return t('Outcome unconfirmed');
  if(row.stale)return t('Needs a fresh check');
  if(row.status==='ready_for_review')return row.reviewPending?t('Review was in progress'):row.humanDecision==='rejected'?t('Waiting for your feedback'):row.humanApprovalRequired&&!known(row.blocker)?t('Waiting for your review'):t('Needs another look');
  if(row.status==='idle'&&row.humanDecision==='approved')return t('Result approved');
  if(row.status==='blocked'&&blockedEnding(row.ending))return blockedEnding(row.ending)!.what;  // a limit, a run that never started (17.1)
  return ({completed:t('Completed'),answered:t('Answer saved'),blocked:t('Couldn’t finish'),disconnected:t('Outcome unconfirmed'),working:t('Was in progress'),unknown:t('Outcome unconfirmed')}[row.status??'unknown']??t('Saved activity'));
}
export function savedNextAction(row:WorkspaceRow):string {
  if(row.outcomeUncertain||['disconnected','unknown','uncertain','lost'].includes(row.status??''))return t('Open the conversation and check the saved activity before retrying.');
  if(row.stale)return t('Open the conversation to request fresh checks against the current files.');
  if(row.reviewPending)return t('Open the conversation to check whether the review has finished.');
  if(row.status==='ready_for_review'&&row.humanDecision==='rejected')return t('Open the task result and send your correction feedback.');
  if(row.status==='ready_for_review'&&row.humanApprovalRequired&&!known(row.blocker))return t('Inspect and try the changes, then approve the result or request a correction.');
  if(row.status==='idle'&&row.humanDecision==='approved')return t('Continue this conversation if you need a follow-up.');
  return t('Open the conversation to read this result in context.');
}
export function latestConversations(rows:WorkspaceRow[]){
  const seen=new Set<string>();
  return rows.filter(row=>{if(row.helper||seen.has(row.channelId))return false;seen.add(row.channelId);return true;});
}
const needsAttention=(row:WorkspaceRow)=>row.outcomeUncertain||row.stale||!!known(row.blocker)||['ready_for_review','blocked','disconnected','unknown','uncertain','failed','interrupted','cancelled','lost','queued'].includes(row.status??'unknown');

/** Stays beside the mounted composer. Source inspection never navigates or invokes task filesystem tools. */
export function WorkspaceSearch(props:Props){
  const {projectId,channelId,profileId,mode,open,onClose}=props;
  const key=queryKey(props);
  const [queries,setQueries]=useState<Record<string,string>>({});
  const query=queries[key]??savedQuery(key);
  const [results,setResults]=useState<WorkspaceRow[]|null>(null);
  const [cursor,setCursor]=useState<string|null>(null);
  const [incomplete,setIncomplete]=useState(false);
  const [indexing,setIndexing]=useState(false);
  const [since,setSince]=useState<number|null>(null),[lessonsPending,setLessonsPending]=useState<number|null>(null);
  const [indexingPass,setIndexingPass]=useState(0);
  const [searchedQuery,setSearchedQuery]=useState('');
  const [excerpt,setExcerpt]=useState<WorkspaceExcerpt|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [allActivity,setAllActivity]=useState(false),[selectedKey,setSelectedKey]=useState('');
  const [resultFilter,setResultFilter]=useState(t('All'));
  const [updatedAt,setUpdatedAt]=useState<number|null>(null);
  // Render-time identity guards also reject responses between navigation and effect cleanup.
  const context=JSON.stringify([projectId,channelId,profileId,mode,open]);
  const current=useRef(context);current.current=context;
  const [dataContext,setDataContext]=useState(context);
  const visible=dataContext===context;
  const visibleResults=visible?results:null;
  const generation=useRef(0);
  const returnButton=useRef<HTMLButtonElement|null>(null);
  const sourceButtons=useRef(new Map<string,HTMLButtonElement>());
  const sourceFocus=useRef<{key:string;element:HTMLElement;requested:boolean}|null>(null);
  const returning=useRef(false);
  useLayoutEffect(()=>{
    const target=sourceFocus.current;
    if(visible&&excerpt&&target?.requested){
      const active=document.activeElement;
      if(active===document.body||active===document.documentElement||active===target.element)returnButton.current?.focus();
      target.requested=false;
    }else if(!excerpt&&returning.current){
      returning.current=false;
      if(target)sourceButtons.current.get(target.key)?.focus();
    }
  },[visible,excerpt,selectedKey]);
  const scope:WorkspaceScope={projectId,channelId,profileId,scope:mode==='conversation'?'conversation':'project'};
  useEffect(()=>{generation.current++;setResults(null);setCursor(null);setIncomplete(false);setIndexing(false);setExcerpt(null);setSelectedKey('');setAllActivity(false);setResultFilter(t('All'));setError('');setBusy(false);},[context]);
  useEffect(()=>{if(open&&mode==='catchup')void run();},[context]);
  // A request runs once when search is open for the project; the shell clears it, so reopening search doesn't repeat it.
  useEffect(()=>{const request=props.request;if(!open||mode!=='project'||!request?.query.trim())return;change(request.query);void run(false,request.query);props.onRequestHandled?.();},[context,props.request?.id]);
  useEffect(()=>{if(!open||mode!=='catchup'||!indexing)return;const timer=setTimeout(()=>void run(),750);return()=>clearTimeout(timer);},[context,indexing,indexingPass]);
  useEffect(()=>()=>{generation.current++;},[]);
  function change(value:string){
    setQueries(old=>({...old,[key]:value}));
    try{sessionStorage.setItem(key,value);}catch{/* In-memory retention still works when storage is unavailable. */}
  }
  async function run(continuePage=false,requested?:string){
    // A15: refreshing Catch up keeps the cards on screen until the new briefing arrives, instead of blanking the list.
    const refreshing=!continuePage&&mode==='catchup'&&dataContext===context&&results!==null;
    const token=++generation.current;setDataContext(context);setBusy(true);setIndexing(false);setError('');if(!refreshing){setExcerpt(null);setSelectedKey('');}
    const activeQuery=continuePage?searchedQuery:requested??query;
    if(!continuePage){if(!refreshing)setResults(null);setCursor(null);setIncomplete(false);setSearchedQuery(activeQuery);setResultFilter(t('All'));}
    const continuation=continuePage&&cursor?{cursor}:{};
    try{
      const response=await workspaceRequest<WorkspacePage>(mode==='catchup'?'project_catchup':'workspace_search',mode==='catchup'?{...scope,limit:20,...continuation}:{...scope,query:activeQuery,limit:20,...continuation});
      if(current.current===context&&generation.current===token){
        setResults(old=>continuePage?[...(old??[]),...response.results]:response.results);
        setCursor(response.cursor??null);setIncomplete(Boolean(response.incomplete));setIndexing(Boolean(response.indexing));
        if(!continuePage){setSince(response.since??null);setLessonsPending(response.lessonsPending??null);setUpdatedAt(Math.floor(Date.now()/1000));}
        if(response.indexing)setIndexingPass(pass=>pass+1);
      }
    }catch(e){if(current.current===context&&generation.current===token)setError(String(e));}
    finally{if(current.current===context&&generation.current===token)setBusy(false);}
  }
  async function inspect(row:WorkspaceRow,element:HTMLButtonElement){
    const active=document.activeElement;
    sourceFocus.current={key:`${row.kind}:${row.id}`,element,requested:active===element||active===document.body||active===document.documentElement};
    const token=++generation.current;setDataContext(context);setBusy(true);setError('');setExcerpt(null);setSelectedKey(`${row.kind}:${row.id}`);
    try{
      const response=await workspaceRequest<WorkspaceExcerpt>('workspace_source',{...scope,source:row.source});
      if(current.current===context&&generation.current===token)setExcerpt(response);
    }catch(e){if(current.current===context&&generation.current===token)setError(String(e));}
    finally{if(current.current===context&&generation.current===token)setBusy(false);}
  }
  if(!open)return null;
  const catchup=mode==='catchup';
  const waiting=catchup&&!allActivity?(visibleResults??[]).filter(row=>!row.helper&&row.status==='ready_for_review'&&(row.humanDecision==='rejected'||row.humanApprovalRequired&&!known(row.blocker))&&!row.outcomeUncertain&&!row.reviewPending&&!row.stale):[];
  const waitingIds=new Set(waiting.map(row=>`${row.kind}:${row.id}`));
  const rows=catchup&&!allActivity?latestConversations(visibleResults??[]).filter(row=>!waitingIds.has(`${row.kind}:${row.id}`)):visibleResults??[];
  const running=catchup&&!allActivity?rows.filter(row=>row.status==='working'):[];
  const groups=catchup&&!allActivity?[
    {title:t('Waiting for you'),rows:waiting},
    {title:t('Needs a look'),rows:rows.filter(needsAttention)},
    {title:t('Running'),rows:running},
    {title:t('Latest from your conversations'),rows:rows.filter(row=>!needsAttention(row)&&row.status!=='working')},
  ]:catchup?[{title:t('All saved activity'),rows}]:[
    {title:t('Answers'),rows:rows.filter(row=>!row.helper&&row.matchRole==='assistant')},
    {title:t('Matching requests'),rows:rows.filter(row=>!row.helper&&row.matchRole==='user')},
    {title:t('Conversation excerpts'),rows:rows.filter(row=>!row.helper&&!row.matchRole)},
    {title:t('Helper work'),rows:rows.filter(row=>row.helper)},
  ];
  const selected=visible&&!!selectedKey;
  const filters=[{title:t('All'),count:rows.length},...groups.map(group=>({title:group.title,count:group.rows.length}))];
  function chooseFilter(value:string){setResultFilter(value);setExcerpt(null);setSelectedKey('');sourceFocus.current=null;returning.current=false;}
  function back(){generation.current++;returning.current=true;setBusy(false);setError('');setExcerpt(null);setSelectedKey('');}
  function chooseActivity(value:boolean){setAllActivity(value);setExcerpt(null);setSelectedKey('');sourceFocus.current=null;returning.current=false;}
  return <section aria-label={label(mode)} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();onClose();}}} className="dervo-find-workspace">
    <header className="dervo-find-header">
      <div className="dervo-find-heading"><div><p className="dervo-find-project">{props.projectName??(mode==='conversation'?t('This conversation'):t('Your project'))}</p><h2 data-panel-focus={catchup?'search':undefined} tabIndex={-1}>{catchup?t('Catch up'):mode==='conversation'?t('Search this conversation'):t('Search')}</h2></div></div>
      <Button variant="ghost" className="dervo-find-close" onClick={onClose}><X/>{props.returnLabel??t('Back to conversation')}</Button>
    </header>
    <div className="dervo-find-toolbar">
      {catchup?<div className="dervo-brief-controls"><div aria-label={t('Catch up view')} className="dervo-segmented"><button disabled={busy} aria-pressed={!allActivity} onClick={()=>chooseActivity(false)}>{t('Latest by conversation')}</button><button disabled={busy} aria-pressed={allActivity} onClick={()=>chooseActivity(true)}>{t('All activity')}</button></div><Button variant="ghost" disabled={busy} onClick={()=>void run()} aria-label={t('Refresh catch-up')}><RefreshCw className={busy?'animate-spin motion-reduce:animate-none':''}/>{t('Refresh')}</Button></div>:<form className="dervo-find-form" onSubmit={event=>{event.preventDefault();void run();}}>
        {props.onScope&&<div aria-label={t('Search scope')} role="group" className="dervo-segmented dervo-find-scope"><button type="button" aria-pressed={mode==='project'} disabled={busy} onClick={()=>props.onScope?.('project')}>{t('This project')}</button><button type="button" aria-pressed={mode==='conversation'} disabled={busy} onClick={()=>props.onScope?.('conversation')}>{props.conversationName?`#${props.conversationName}`:t('This conversation')}</button></div>}<label htmlFor="dervo-saved-search">{t('Find words in your saved conversations')}</label><div><Search aria-hidden="true"/><Input id="dervo-saved-search" data-panel-focus="search" aria-label={t('Search saved text')} placeholder={t('A decision, a question, something you worked on…')} maxLength={256} value={query} onChange={event=>change(event.target.value)}/><Button type="submit" disabled={busy||!query.trim()}>{t('Search')}</Button></div>
      </form>}
      <p className="dervo-find-boundary">{catchup?t('Saved conversations and project tasks. Older activity may not be in exact time order.'):mode==='conversation'?t('Only this conversation.'):t('Shared conversations in this project and account.')} {mode!=='conversation'&&t('Private conversations stay separate.')}</p>
      {visibleResults&&<div className="dervo-find-coverage">
        <p role="status">{indexing?t('Preparing your saved activity… This only happens when older records need organising.'):incomplete?`${catchup?t('This briefing is incomplete.'):t('Search is incomplete.')} ${cursor?t('Load more to include earlier saved work.'):t('Some saved records couldn’t be safely included. You can still open their original conversations.')}`:catchup?t('Showing the saved activity available in this project.'):t('{count} matches loaded.',{count:rows.length})}</p>
        {visible&&cursor&&<Button variant="outline" disabled={busy} onClick={()=>void run(true)}>{catchup?t('Load more'):t('Continue search')}</Button>}
      </div>}
      {!catchup&&visibleResults&&visibleResults.length>0&&<div className="dervo-result-filters" aria-label={t('Filter loaded search results')}>{filters.map(filter=><button key={filter.title} aria-pressed={resultFilter===filter.title} disabled={busy||filter.count===0} onClick={()=>chooseFilter(filter.title)}>{filter.title}{' '}<span>{filter.count}</span></button>)}</div>}
    </div>
    <div className={`dervo-find-body ${selected?'has-preview':''}`}>
      <div className="dervo-find-results" aria-label={catchup?t('Project briefing'):t('Search results')}>
        {catchup&&visibleResults&&visibleResults.length>0&&!allActivity&&(()=>{
          const look=rows.filter(needsAttention).length,fresh=visibleResults.filter(r=>r.isNew).length;
          return <div className="dervo-brief-summary" data-testid="brief-summary" role="status">
            <div className="dervo-brief-stats">
              <p className="dervo-brief-stat"><b>{waiting.length}</b><span> {t('waiting for you')}</span></p>
              <p className={`dervo-brief-stat${look?' is-attention':''}`}><b>{look}</b><span> {t('need a look',{count:look})}</span></p>
              <p className="dervo-brief-stat"><b>{running.length}</b><span> {t('running')}</span></p>
            </div>
            <p className="dervo-brief-since">{since?(fresh?t('{count} new since you looked at {when}.',{count:fresh,when:whenSaved(since)}):t('Nothing new since you looked at {when}.',{when:whenSaved(since)})):t('This is your first look at this project.')}{updatedAt?` ${t('Updated {when}.',{when:whenSaved(updatedAt)})}`:''}</p>
            {!!lessonsPending&&<p className="dervo-brief-counts">{t('{count} lesson suggestions in Knowledge.',{count:lessonsPending})}{props.onOpenKnowledge&&<> <button type="button" className="dervo-brief-link" onClick={props.onOpenKnowledge}>{t('Open Knowledge')}</button></>}</p>}
          </div>;
        })()}
        {visibleResults&& !catchup&&query!==searchedQuery&&<p className="dervo-find-notice">{t('Results for: {query}',{query:searchedQuery})}</p>}
        {visible&&busy&&!selected&&!visibleResults&&<p role="status" className="dervo-find-notice">{catchup?t('Gathering your recent work…'):t('Searching saved records…')}</p>}
        {!busy&&!incomplete&&!error&&visibleResults?.length===0&&<div className="dervo-find-empty"><h3>{catchup?t('Your recent work will appear here'):t('No matches yet')}</h3><p role="status">{catchup?t('Start a conversation in this project. You can return here to read its saved messages and task results.'):t('Try a shorter phrase or a word you remember from the conversation.')}</p></div>}
        {!busy&&visibleResults===null&&!error&&!catchup&&<div className="dervo-find-empty"><h3>{t('Find the answer you came back for.')}</h3><p>{t('Search the words you remember, then read the matching conversation here.')}</p></div>}
        {groups.filter(group=>group.rows.length>0&&(catchup||resultFilter==='All'||resultFilter===group.title)).map(group=><section key={group.title} className="dervo-brief-section"><h3 className="dervo-brief-label">{group.title}{' '}<span>{group.rows.length}</span></h3>{group.rows.map(row=><article key={`${row.kind}:${row.id}`} className={`dervo-result ${selectedKey===`${row.kind}:${row.id}`?'is-selected':''}`}>
          <div className="dervo-result-meta"><span>{row.isNew&&<span className="dervo-result-new">{t('New')}</span>}{row.helper?(row.matchRole==='user'||row.hasAnswer===false?t('Helper request'):t('Helper answer')):row.matchRole==='user'?t('Your request'):row.matchRole==='assistant'?(catchup&&row.kind!=='task'?t('Conversation'):t('Saved answer')):row.kind==='message'?t('Conversation excerpt'):t('Project task')}{(()=>{const time=rowTime(row);return time?<span className="dervo-result-time"> · {time.label} {whenSaved(time.at)}</span>:null;})()}</span>{(catchup||row.kind==='task')&&<span className={`dervo-result-status ${needsAttention(row)?'needs-attention':''}`}>{savedStatus(row)}</span>}</div>
          <h4>{row.helper?helperTitle(row):row.channelName}</h4>
          {row.title&&row.title!==row.channelName&&!(row.channelName.length>=32&&row.title.startsWith(row.channelName))&&!row.helper&&row.matchRole!=='user'&&<p className="dervo-result-request">{row.title}</p>}
          <div className="dervo-result-excerpt"><SearchPreview text={row.snippet} query={catchup?'':searchedQuery}/></div>
          {catchup&&row.kind==='task'&&row.summary&&!(known(row.blocker)&&row.summary.includes(row.blocker!))&&<p className="dervo-result-summary" data-testid="result-summary">{row.summary}</p>}
          {known(row.blocker)&&<p className="dervo-result-blocker">{row.stale?t('Files changed since this result. Read it before asking for a fresh check.'):row.blocker}</p>}
          <div className="dervo-result-actions"><Button ref={element=>{const key=`${row.kind}:${row.id}`;if(element)sourceButtons.current.set(key,element);else sourceButtons.current.delete(key);}} variant="ghost" className="dervo-result-open" disabled={busy} onClick={event=>void inspect(row,event.currentTarget)}>{readLabel(row)}<ArrowUpRight/></Button>{catchup&&props.onOpenConversation&&<Button variant="ghost" className="dervo-result-open" disabled={busy} onClick={()=>props.onOpenConversation?.(row.channelId)}>{t('Open conversation')}</Button>}{catchup&&row.kind==='task'&&!row.helper&&props.onOpenTask&&<Button variant="ghost" className="dervo-result-open" disabled={busy} onClick={()=>props.onOpenTask?.(row.channelId,row.id)}>{t('See the record')}</Button>}{catchup&&<CatchupActions row={row} projectId={projectId} disabled={busy} onDone={()=>run()}/>}</div>
        </article>)}</section>)}
        {catchup&&!allActivity&&(visibleResults?.length??0)>0&&rows.length===0&&waiting.length===0&&!busy&&!error&&<div className="dervo-find-empty"><h3>{t('Only helper activity in these results')}</h3><p>{t('Open All activity to read it, or load more work.')}</p></div>}
      </div>
      {selected&&<aside className="dervo-result-reader" aria-label={t('Saved answer preview')}>
        <div className="dervo-reader-top"><Button ref={returnButton} data-panel-focus="search" variant="ghost" onClick={back}><ArrowLeft/>{catchup?t('Back to briefing'):t('Return to search')}</Button>{excerpt&&props.onOpenConversation&&<Button onClick={()=>props.onOpenConversation?.(excerpt.channelId)}>{t('Continue conversation')}<ArrowUpRight/></Button>}</div>
        {busy&&<p role="status" className="dervo-find-notice">{t('Opening the saved answer…')}</p>}
        {excerpt&&<><div className="dervo-reader-heading"><p>{excerpt.kind==='task'?t('Saved project result'):t('Saved conversation')}</p><h3>{excerpt.channelName}</h3>{(catchup||excerpt.kind==='task')&&<span>{savedStatus(excerpt)}</span>}</div><div className="dervo-reader-text"><SavedText text={excerpt.text||t('No saved text.')}/></div>
          {excerpt.truncated&&<p className="dervo-find-notice">{t('This is an excerpt. Open the conversation to read it in context.')}</p>}
          <div className="dervo-reader-record">{excerpt.kind==='task'&&<dl>{excerpt.owner&&<><dt>{t('From')}</dt><dd>{helperOwner(excerpt)}</dd></>}{known(excerpt.blocker)&&<><dt>{t('Needs attention')}</dt><dd>{excerpt.blocker}</dd></>}<dt>{t('What’s next')}</dt><dd>{savedNextAction(excerpt)}</dd></dl>}<SourceRecord key={`${excerpt.kind}:${excerpt.id}:${excerpt.excerptStart}`} excerpt={excerpt}/></div>
        </>}
      </aside>}
    </div>
    {visible&&error&&<div role="alert" className="dervo-find-error"><p>{error}</p><Button variant="outline" disabled={busy} onClick={()=>void run()}>{t('Try again')}</Button></div>}
  </section>;
}
