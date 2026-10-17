/* Pull requests from a finished thread (beta.16, b16/git), to the thread mock Bryce approved on 25 Sep
 * (evidence/design-tests/b16-git/thread-pr-*). Everything sits in the decision bar, beside Undo and under the commit
 * line: the Open pull request button, its confirm, the PR once it's open, or one line to sign in to GitHub.
 *
 * Opening a pull request is outward-facing, so it's its own button, never part of Approve. The confirm names both steps,
 * the repository and the account, and one "Push and open pull request" click does them (Bryce, 25 Sep). "This thread
 * only" is the first choice. GitHub is read again when the thread opens and when the window comes back into focus,
 * never on a timer in the background. What GitHub said is shown with when and as whom; reviewers' words stay quoted. */
import {useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {openUrl} from '@tauri-apps/plugin-opener';
import {ReplyActionsContext} from '@/shared/ui/markdown/replyActions';
import {attemptWords,checkedLine,checksWords,clickWords,decisionWords,githubStatus,othersLine,prCreate,prPreview,prStatus,prTitle,
 type GitHubStatus,type PrAttempt,type PrMode,type PrPreview,type PullRequest as PR} from './github';
import './PullRequest.css';
import {t,tr} from '@/i18n';

export const PrIcon=({merged=false}:{merged?:boolean})=>merged
 ?<svg className="dervo-pr-i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="4" cy="3.5" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5"/><circle cx="4" cy="12.5" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5"/><circle cx="12" cy="8" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5"/><path d="M4 5.2v5.6M4 5.2c0 2.2 1.8 2.8 6.3 2.8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
 :<svg className="dervo-pr-i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="4" cy="3.5" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5"/><circle cx="4" cy="12.5" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5"/><circle cx="12" cy="12.5" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5"/><path d="M4 5.2v5.6M12 10.8V6.5a2 2 0 0 0-2-2H7.5M9 3l-1.6 1.5L9 6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>;
const OutIcon=()=><svg className="dervo-pr-i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M5.5 10.5l5-5M6.5 5.5h4v4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>;

const message=(error:unknown)=>error instanceof Error?error.message:String(error);

/** A sentence with its branch names set as code, the way the mock shows them. Longest name first, so ``main`` inside
 * ``dervo/main-fix`` stays part of the longer one. */
export function withCode(text:string,names:(string|null|undefined)[]):ReactNode{
 const shown=[...new Set(names.filter((name):name is string=>!!name))].sort((a,b)=>b.length-a.length);
 if(!shown.length)return text;
 const pattern=new RegExp(`(${shown.map(name=>name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')})`);
 return text.split(pattern).map((part,index)=>index%2?<code key={index}>{part}</code>:part);
}

/** Opens a pull request's page in the browser. Only GitHub's own https links. */
export function openOnGitHub(url:string|null|undefined){
 if(url&&/^https:\/\/[^/\s]+\/[^\s]+$/.test(url))void openUrl(url).catch(()=>{/* the link stays in the PR line to copy */});
}

/** The pull request as GitHub last said it: state, checks, the reviewers' words quoted, and when Dervo checked and as whom.
 * The thread and History's open card show the same box. */
export function PullRequestBox({pr,className='',head}:{pr:PR;className?:string;head?:boolean}){
 const checks=checksWords(pr.checks);
 const decision=decisionWords(pr);
 return <section className={`dervo-pr-box ${className}`} aria-label={t('Pull request')} data-state={pr.state}>
  <div className="dervo-pr-head"><PrIcon merged={pr.state==='merged'}/><b>{prTitle(pr)}</b>
   <span className="dervo-pr-where">{pr.repo}{pr.head&&<> · {tr('{head} into {base}',{head:<code>{pr.head}</code>,base:<code>{pr.base}</code>})}</>}</span>
   {head!==false&&pr.url&&<button type="button" className="dv-btn dv-btn-quiet dervo-pr-gh" onClick={()=>openOnGitHub(pr.url)}>{t('Open on GitHub')}<OutIcon/></button>}</div>
  <dl className="dv-facts dervo-pr-facts">
   {checks&&<div className="dv-facts-row"><dt>{t('Checks')}</dt><dd>{checks}</dd></div>}
   {(decision||pr.comments.length>0)&&<div className="dv-facts-row"><dt>{t('Review')}</dt><dd className="dervo-pr-comments">
    {decision&&<p>{decision}</p>}
    {pr.comments.map((comment,index)=><p key={index}><q>{comment.text}</q>{' '}<span className="dervo-history-from">{comment.author}{comment.path&&<>, {tr('on {file}',{file:<code>{comment.path}{comment.line?`:${comment.line}`:''}</code>})}</>}</span></p>)}
   </dd></div>}
  </dl>
  <p className="dervo-pr-checked" role={pr.checkError?'status':undefined}>{checkedLine(pr)}</p>
 </section>;
}

/** Runs a fix command in the project terminal, where the click is the consent and you watch it happen. ``after`` (Check
 * again) shows once it's sent, or at once where there's no project terminal to send it to. */
function RunFix({command,label,after=null}:{command:string;label:string;after?:ReactNode}){
 const actions=useContext(ReplyActionsContext);
 const [sent,setSent]=useState(false),[failed,setFailed]=useState('');
 if(!actions)return <><span className="dervo-pr-cmd">{tr('In Terminal, run {command}.',{command:<code>{command}</code>})}</span>{after}</>;
 async function run(){setFailed('');try{await actions!.run(command,t('From the pull request'));setSent(true);}catch(error){setFailed(message(error));}}
 return <><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void run()}>{label}</button>
  <span className="dervo-pr-cmd">{sent?tr('Sent {command} to the project terminal.',{command:<code>{command}</code>}):tr('Runs {command} in the project terminal.',{command:<code>{command}</code>})}</span>
  {sent&&after}
  {failed&&<span className="dervo-pr-cmd" role="alert">{failed}</span>}</>;
}

/** gh missing, too old or signed out: one plain line and the one way to fix it (thread-pr-signin). Coming back to the
 * window checks again by itself; Check again is there once you've started the fix. */
function GitHubFix({status,onRecheck,busy}:{status:GitHubStatus;onRecheck:()=>void;busy:boolean}){
 const signedOut=status.state==='signed_out';
 const text=signedOut?t('To open a pull request, sign in to GitHub on this Mac. Dervo uses the GitHub CLI’s own sign-in and never sees the token.')
  :status.state==='missing'?t('To open a pull request, Dervo uses the GitHub CLI (gh), which isn’t on this Mac yet.')
  :status.state==='too_old'?t('To open a pull request, Dervo needs a newer GitHub CLI than {version}.',{version:status.version??''})
  :t('Dervo couldn’t check GitHub. {detail}',{detail:status.detail});
 const recheck=<button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={onRecheck}>{busy?t('Checking…'):t('Check again')}</button>;
 const command=signedOut?status.signInCommand:status.state==='missing'||status.state==='too_old'?status.installCommand:null;
 const label=signedOut?t('Sign in to GitHub'):status.state==='missing'?t('Install gh'):t('Update gh');
 return <div className="dv-decision-note dervo-pr-signin" data-testid="pr-signin"><p>{text}</p>
  {command?<RunFix command={command} label={label} after={recheck}/>
   :<>{status.state==='missing'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>openOnGitHub(status.installPage??'https://cli.github.com')}>{t('Get gh from cli.github.com')}<OutIcon/></button>}{recheck}</>}
 </div>;
}

function Choice({preview,checked,onPick,name,line}:{preview:PrPreview|undefined;checked:boolean;onPick:()=>void;name:string;line:string}){
 const off=!preview?.available;
 const count=preview?.commits.length??0;
 const names=[preview?.head,preview?.base,preview?.branch];
 return <button type="button" className="dv-choice" role="radio" aria-checked={checked} aria-disabled={off||undefined} onClick={()=>{if(!off)onPick();}}>
  <span className="dv-dot"/><span><span className="dv-c-name">{name}</span><span className="dv-c-line">{preview?withCode(off?preview.reason??'':line,names):t('Reading what would go…')}</span></span>
  {preview&&!off&&<span className="dv-c-end">{t('{count} commits',{count})}</span>}
 </button>;
}

/** The confirm (thread-pr-confirm): who and where, what goes in, the title, Draft, and what the one click does. */
function Confirm({projectId,channelId,startDraft,onCancel,onStarted}:{projectId:string;channelId:string;startDraft?:boolean;onCancel:()=>void;onStarted:(attempt:PrAttempt)=>void}){
 const thread=useQuery({queryKey:['pr-preview',projectId,channelId,'thread'],queryFn:()=>prPreview(projectId,channelId,'thread'),retry:false,gcTime:0,staleTime:0});
 const branch=useQuery({queryKey:['pr-preview',projectId,channelId,'branch'],queryFn:()=>prPreview(projectId,channelId,'branch'),retry:false,gcTime:0,staleTime:0});
 const [picked,setPicked]=useState<PrMode|null>(null);
 const mode:PrMode=picked??(thread.data?.available||!branch.data?.available?'thread':'branch');
 const shown=mode==='thread'?thread.data:branch.data;
 const [draft,setDraft]=useState<boolean|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const first=useRef<HTMLDivElement>(null);
 useEffect(()=>{first.current?.focus();},[]);
 const any=thread.data??branch.data;
 const isDraft=draft??(startDraft===false?false:any?.draft??true);
 const loadError=thread.error??branch.error;
 async function go(){
  if(!shown?.id||busy)return;
  setBusy(true);setError('');
  try{onStarted(await prCreate(projectId,channelId,shown.id,isDraft));}
  catch(failure){setError(message(failure));void thread.refetch();void branch.refetch();}
  finally{setBusy(false);}
 }
 const needSignIn=any&&!any.available&&any.ghState&&any.ghState!=='ready';
 return <div className="dv-decision-note dervo-prc" role="group" aria-label={t('Open a pull request')} ref={first} tabIndex={-1}
  onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();onCancel();}}}>
  {!any&&!loadError&&<p className="dervo-prc-what" role="status">{t('Reading what would go…')}</p>}
  {loadError&&<p role="alert" className="dervo-prc-what">{message(loadError)}</p>}
  {any?.repo&&any.account&&<p className="dervo-prc-q">{tr('Open a pull request on {repo} as {account}?',{repo:<b>{any.repo}</b>,account:<b>{any.account}</b>})}</p>}
  {any?.previousAccount&&<p className="dervo-prc-what" role="note">{tr('Last time this project used {account} on GitHub. Check this is the account you want.',{account:<b>{any.previousAccount}</b>})}</p>}
  {needSignIn?<p className="dervo-prc-what">{any.reason}</p>:any&&<>
   <div className="dv-choices dv-compact" role="radiogroup" aria-label={t('What goes in')}>
    <Choice preview={thread.data} checked={mode==='thread'} onPick={()=>setPicked('thread')} name={t('This thread only')}
     line={thread.data?`${(()=>{const parts={commits:`${thread.data.commits.map(c=>c.short).slice(0,3).join(', ')}${thread.data.commits.length>3?` ${t('and more')}`:''}`,head:thread.data.head??'',base:thread.data.base??''};return thread.data.commits.length===1?t('{commits} goes on a new branch, {head}, made from {base}.',parts):t('{commits} go on a new branch, {head}, made from {base}.',parts);})()} ${thread.data.branch?t('Your {branch} stays as it is.',{branch:thread.data.branch}):t('Your branch stays as it is.')}`:''}/>
    <Choice preview={branch.data} checked={mode==='branch'} onPick={()=>setPicked('branch')} name={t('Everything on {branch}',{branch:branch.data?.branch??any.branch??t('this branch')})}
     line={branch.data?othersLine(branch.data,channelId):''}/>
   </div>
   {shown?.available&&<>
    <div className="dervo-prc-fields"><span className="dervo-prc-field"><span>{t('Title')}</span><span className="dervo-prc-title">{shown.title}</span></span>
     <span className="dervo-prc-into">{tr('into {base}',{base:<code>{shown.base}</code>})}</span></div>
    <label className="dervo-prc-draft"><button type="button" className="dv-switch" role="switch" aria-checked={isDraft} aria-label={t('Open as a draft')} onClick={()=>setDraft(!isDraft)}/>
     <span><b>{t('Draft')}</b>{isDraft?t('Nobody is asked to review it until you mark it ready on GitHub.'):t('Reviewers GitHub names for this repository are asked straight away.')}</span></label>
    {startDraft===false&&<p className="dervo-prc-what" role="note">{t('GitHub didn’t allow a draft here last time, so Draft starts off.')}</p>}
    <p className="dervo-prc-what">{withCode(clickWords(shown),[shown.head,shown.base])}</p>
   </>}
  </>}
  {error&&<p role="alert" className="dervo-prc-what dervo-prc-error">{error}</p>}
  <div className="dervo-prc-actions">
   {shown?.available&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy} onClick={()=>void go()}>
    {busy?t('Starting…'):shown.pushNeeded?t('Push and open pull request'):t('Open pull request')}</button>}
   <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={onCancel}>{t('Cancel')}</button>
  </div>
 </div>;
}

/** Everything about pull requests for one committed task's thread, in its decision bar. ``pushed`` is whether Home's push
 * list has this commit (GuideActions' notPushed), for "Not on GitHub yet.". */
export function ThreadPullRequest({projectId,channelId,notOnGitHub}:{projectId:string;channelId:string;notOnGitHub:boolean}){
 const client=useQueryClient();
 const key=['pr-status',projectId,channelId];
 const status=useQuery({queryKey:key,queryFn:()=>prStatus(projectId,channelId,true),retry:false,staleTime:15000,refetchOnWindowFocus:true,
  refetchInterval:query=>query.state.data?.attempt?.state==='running'?1500:false});
 const gh=useQuery({queryKey:['github-status',projectId],queryFn:()=>githubStatus(projectId),retry:false,
  // Signed out or missing: coming back from Terminal checks again at once. Ready: the host keeps its answer five minutes anyway.
  staleTime:query=>query.state.data?.state==='ready'?60000:0,refetchOnWindowFocus:true});
 const [confirming,setConfirming]=useState(false),[startDraft,setStartDraft]=useState<boolean|undefined>(undefined);
 const [checking,setChecking]=useState(false),[error,setError]=useState('');
 const attempt=status.data?.attempt??null,pr=status.data?.pr??null;
 async function recheck(){setChecking(true);try{await client.fetchQuery({queryKey:['github-status',projectId],queryFn:()=>githubStatus(projectId,true),staleTime:0});}finally{setChecking(false);}}
 async function checkGitHub(){setChecking(true);setError('');try{client.setQueryData(key,await prStatus(projectId,channelId,true));}catch(failure){setError(message(failure));}finally{setChecking(false);}}
 function started(next:PrAttempt){setConfirming(false);client.setQueryData(key,{pr:null,attempt:next,pullOffer:null});void client.invalidateQueries({queryKey:['push-preview',projectId]});}
 const running=attempt?.state==='running';
 const open=!!pr&&attempt?.state==='opened';
 const canOffer=!open&&!running&&attempt?.state!=='unknown';
 const ghReady=gh.data?.state==='ready';
 const offer=canOffer&&!confirming&&(ghReady||!gh.data);
 return <>
  {offer&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dervo-open-pr" aria-expanded={false}
   onClick={()=>{setStartDraft(attempt?.state==='draft_refused'?false:undefined);setConfirming(true);}}><PrIcon/>{attempt?.state==='failed'||attempt?.state==='draft_refused'?t('Open pull request again'):t('Open pull request')}</button>}
  {confirming&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dervo-open-pr" aria-expanded={true} onClick={()=>setConfirming(false)}><PrIcon/>{t('Open pull request')}</button>}
  {canOffer&&!confirming&&gh.data&&!ghReady&&<GitHubFix status={gh.data} busy={checking} onRecheck={()=>void recheck()}/>}
  {canOffer&&!confirming&&!attempt&&notOnGitHub&&(ghReady||!gh.data)&&<p className="dv-decision-note" data-testid="pr-not-on-github">{t('Not on GitHub yet.')}</p>}
  {confirming&&<Confirm projectId={projectId} channelId={channelId} startDraft={startDraft} onCancel={()=>setConfirming(false)} onStarted={started}/>}
  {running&&<p className="dv-decision-note" role="status" data-testid="pr-running">{attemptWords(attempt!)}</p>}
  {!confirming&&(attempt?.state==='failed'||attempt?.state==='draft_refused')&&<p className="dv-decision-note" role="alert" data-testid="pr-failed">{attemptWords(attempt)}</p>}
  {attempt?.state==='unknown'&&<div className="dv-decision-note dervo-pr-unknown" role="alert" data-testid="pr-unknown"><p>{attemptWords(attempt)}</p>
   <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={checking} onClick={()=>void checkGitHub()}>{checking?t('Checking…'):t('Check GitHub')}</button></div>}
  {open&&<PullRequestBox pr={pr} className="dv-decision-note dervo-prline"/>}
  {open&&status.data?.pullOffer&&<div className="dv-decision-note dervo-pr-signin"><p>{status.data.pullOffer.count===1?t('Your local {base} still has this commit under its old id.',{base:status.data.pullOffer.base}):t('Your local {base} still has these {count} commits under their old ids.',{base:status.data.pullOffer.base,count:status.data.pullOffer.count})}</p>
   <RunFix command={status.data.pullOffer.command} label={t('Pull and rebase')}/></div>}
  {(error||status.error)&&<p className="dv-decision-note" role="alert">{error||message(status.error)}</p>}
 </>;
}
