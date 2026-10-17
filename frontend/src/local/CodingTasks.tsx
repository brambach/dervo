import {TaskFanout,ThreadSourceLink,type ThreadSource} from './TaskFanout';
import {useChannelNavigation} from '@/shared/context/ChannelNavigationContext';
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {Input} from '@/shared/ui/input';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/shared/ui/dialog';
import {Sheet,SheetContent,SheetTitle} from '@/shared/ui/sheet';
import {local,reconcileTask,type FileRestoration,type CollabLimits,type TaskReview,type ReconcileReport,type ReconcileStatus} from './api';
import {roleLabel,reviewLabel,agentLabel} from './collaboration';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {WorkingMark} from './WorkingMark';
import {GuideActions,NotPushedLine,commitLine} from './GuideActions';
import {modelName,providerAuthor} from './providerLabel';
import {AlongsideNote,InheritedNote,UnrecordedFiles} from './ResultNotes';
import {QuestionHistory} from './WorkerQuestions';
import {inTray,readWorkerQuestion} from './workerQuestionState';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './TaskReading.css';
import './Surfaces.css';
import {formatDate,formatList,t,tr} from '@/i18n';
import {hostMessage} from '@/i18n/hostMessages';
import {endingDecision,endingOf,endingWord,lastSeenLine,lostRecord,stepName} from './runEnding';

type Binding={provider:string;identity:string;model:string;cwd:string;visibility:string};
export const CODING_PROVIDERS=new Set(['codex','claude']);
export type TaskToken={native?:boolean;prose?:boolean;command?:string|null;connector?:string|null;started_at?:number;ended_at?:number;tool:string;state:string;attempt?:number|null;path?:string|null;packet_id?:string;run_id?:string;success?:boolean};
/** The owner's request-time collaboration decision (depth 0) or a helper's chain position. Never widened after creation. */
export type TaskCollaboration={mode:string;agents:string[];unavailable?:Record<string,string>;depth:number;root_task_id:string;chain:string[];limits:CollabLimits;review?:TaskReview|null};
/** Recorded when a result became Completed without human approval: the checked attempt, the review outcome (with the exact reviewed snapshot), the task's cumulative changed files (first before versus final after; a reverted file is not listed) and the last attempt's own non-empty diffs. Never a human decision. Earlier completion records of the same task stay in ``completions``. */
/** check_profile is absent on tasks and completion records made before check profiles existed; those ran the Python unittest argv shown on the task. */
export type TaskCompletion={at:number;attempt:number;run_id:string;review_phase:string|null;review_snapshot?:Record<string,string>|null;changed_files:string[];attempt_changed_files?:string[];test_exit_status:number;check_profile?:string|null;human_approved:false;stale?:boolean};
/** Your account on the other provider, as the host names it. Auto never offers another account of the same provider. */
export type RoutePair={profile_id:string;model:string;provider:string;identity:string;method:string;label:string};
/** Who ran a request and why, recorded by the host when it started. */
export type TaskRoute={mode:'auto'|'fixed';provider:string;profile_id:string;identity:string;method:string;model:string;label:string;why:string;suggest:(RoutePair&{reason:string})|null;switched_from:{profile_id:string;provider:string;identity:string;method:string;model:string}|null;retry_of:string|null;automatic:boolean};
/** After a failure on the provider's side: the other provider's account to try, whether the host is retrying there by
 * itself, the task it started (``taken``) and why an automatic retry couldn't run (``error``). */
export type TaskSuggest={reason:string;kind:string;other:RoutePair|null;automatic:boolean;taken:string|null;error:string|null};
/** What Dervo committed when the result was approved, or why nothing was committed. */
export type TaskCommit={sha:string|null;branch?:string|null;files?:string[];at?:number;how?:string;reverted?:unknown;skipped?:string;error?:string;left?:Record<string,string>;index_warning?:string};
/** ``steer`` is on a message you sent while the task was working: ``sent`` until the agent reads it at its next step, then ``read``. */
export type CodingTask={inherited?:import('./ResultNotes').Inherited[];unrecorded?:import('./ResultNotes').Unrecorded[];alongside?:import('./ResultNotes').Alongside;questions?:import('./workerQuestionState').WorkerQuestion[];has_thread_proposals?:boolean;thread_source?:ThreadSource;full_access?:boolean;images?:string[];read_only?:boolean;id:string;state:string;error?:string;origin?:string;session_id:string|null;scope_version:number;files:string[];folders?:string[];argv:string[];check_profile?:string;check_timeout_seconds?:number;binding:Binding;review?:string;messages:{role:string;text:string;created_at:number;run_id?:string;source?:string;steer?:string}[];tokens:TaskToken[];runs:unknown[];role?:string;agent?:string;packet_id?:string;collaboration?:TaskCollaboration;acceptance?:{required:boolean;source:string};completion?:TaskCompletion;completions?:TaskCompletion[];corrections?:{from_state:string;attempt:number;created_at:number}[];route?:TaskRoute;suggest?:TaskSuggest;commit?:TaskCommit|null;waiting_for_reset?:{queue_id:string;until:number}|null};
/** Phases the host still drives: the reviewer may be active after the owner turn ended, so Stop must stay available. */
export const REVIEW_OPEN=new Set(['pending','initial','correction','final']);
/** Display the recorded interruption without changing retry state or concealing uncertain outcomes. */
export function stoppedTask(task:{state:string;runs?:unknown[];tokens?:{state:string}[]}):boolean{
 const state=(run:unknown)=>typeof run==='object'&&run!==null&&'state' in run?run.state:undefined;
 return task.state==='blocked'&&state(task.runs?.at(-1))==='interrupted'&&!task.runs?.some(run=>state(run)==='uncertain')&&!task.tokens?.some(token=>token.state==='uncertain');
}
/** A run Dervo paused when it quit, before the run could finish its step (backend CLOSE_LOST). */
export function quitPaused(task:{state:string;error?:string}):boolean{return task.state==='disconnected'&&!!task.error?.startsWith('Dervo quit mid-run');}
/** Whether the handoff row stands in for Retry: after a failure on the provider's side, or once another agent took the
 * task over. After your own Stop (kind ``stopped``, behaviour review N1, 26 Sep) Retry stays where it was, and the row
 * only adds Continue in the other provider. */
export function handoffHoldsRetry(task:Pick<CodingTask,'suggest'>):boolean{return !!task.suggest&&(task.suggest.kind!=='stopped'||!!task.suggest.taken);}
/** Only settled host outcomes collapse. Uncertain work and unfinished review keep their controls. */
export function settledTask(task:CodingTask):boolean {
 const phase=task.collaboration?.review?.phase;
 const runs=task.runs as {id?:string;state?:string}[];
 if(task.error||task.completion?.stale||(phase&&phase!=='passed'&&!(phase==='skipped'&&task.state==='answered'))||runs.some(run=>run.state==='uncertain')||task.tokens.some(token=>['uncertain','executing','running','delivering'].includes(token.state)))return false;
 if(['running','starting','stopping'].includes(runs.at(-1)?.state??''))return false;
 if(task.state==='answered')return true;
 if(task.state==='idle')return task.review==='approved';
 return task.state==='completed'&&!!task.completion&&task.completion.test_exit_status===0&&(!runs.at(-1)?.id||runs.at(-1)?.id===task.completion.run_id);
}
/** A result that was Completed and whose files changed afterwards: approval is unavailable for it; a correction continues the task. */
export function staleCompleted(task:Pick<CodingTask,'state'|'completion'>):boolean {return task.state==='ready_for_review'&&!!task.completion?.stale;}
/** A finished owner result that the human never asked to approve: approval is off for this request, so whatever kept it from
 * Completed (a failed check, a reviewer that ended without a pass) needs attention and a correction, not a human approval. */
export function needsAttention(task:Pick<CodingTask,'state'|'completion'|'collaboration'|'acceptance'>):boolean{
 return task.state==='ready_for_review'&&task.acceptance?.required===false&&!REVIEW_OPEN.has(task.collaboration?.review?.phase??'')&&!staleCompleted(task);
}
export function correctionRequested(task:Pick<CodingTask,'state'|'review'|'completion'|'collaboration'>):boolean {
 return task.state==='ready_for_review'&&task.review==='rejected'&&!staleCompleted(task)&&!REVIEW_OPEN.has(task.collaboration?.review?.phase??'');
}
/** The thread's newest result waiting for the changes you asked for, which a plain message carries on (task_continue).
 * Not with images: a correction takes text only, so that message starts its own task as before. */
export function correctionTarget(tasks:readonly CodingTask[],images=false):CodingTask|undefined{
 if(images)return undefined;
 const latest=[...tasks].reverse().find(task=>task.origin==='conversation');
 return latest&&correctionRequested(latest)?latest:undefined;
}
/** What the result footer says about a finished result. Completed is a host fact, never a human approval. While agents still review, the human is not asked; with approval off, an unfinished result needs attention, not an approval. */
export function resultLabel(task:Pick<CodingTask,'state'|'completion'|'collaboration'|'acceptance'|'error'>&Partial<Pick<CodingTask,'runs'|'tokens'|'review'>>):string{
 if(stoppedTask(task))return REVIEW_OPEN.has(task.collaboration?.review?.phase??'')?t('Owner paused, review running'):t('Paused');
 if(task.state==='completed')return task.completion?.changed_files.length?t('Completed'):t('Completed, checks passed, no files changed');
 if(task.state==='ready_for_review'){
  if(REVIEW_OPEN.has(task.collaboration?.review?.phase??''))return t('Independent review running');
  if(staleCompleted(task))return t('Completed result no longer current');
  if(correctionRequested(task))return t('Waiting for your feedback');
  if(needsAttention(task))return t('Needs attention');
  return t('Needs your review');
 }
 return '';
}
/** Why an approval-off result stopped short of Completed, from recorded facts only: the host error or the reviewer's end phase. */
export function attentionDetail(task:Pick<CodingTask,'error'|'collaboration'>):string{
 const phase=task.collaboration?.review?.phase;
 const cause=task.error?t('The host checks did not pass.'):phase&&phase!=='passed'?t('Independent review ended {phase}, so the result did not complete.',{phase}):t('The result did not complete.');
 return t('{cause} Result approval is off for this request, so no approval is asked of you. Inspect it and request a correction.',{cause});
}
/** The review activity line: the reviewer's own phase, or how the completion relates to it. */
export function completionDetail(task:Pick<CodingTask,'state'|'completion'>):string{
 if(task.state!=='completed'||!task.completion)return '';
 const files=task.completion.changed_files.length?t('Changed {files}',{files:task.completion.changed_files.join(', ')}):t('No file changed');
 const review=task.completion.review_phase==='passed'?t('Independent review passed'):t('No independent review was configured');
 const status=task.completion.test_exit_status;
 return `${files}. ${['rust-check','typescript-check'].includes(task.completion.check_profile??'')?t('Host type check exit {status}.',{status}):t('Host tests exit {status}.',{status})} ${review}. ${t('Not marked as approved by you.')}`;
}
/** A helper task's label: the named agent and whether it may change files or only read. */
export function helperLabel(task:Pick<CodingTask,'origin'|'agent'|'role'>):string {return task.origin==='collaboration'?t('Helper {name} ({role})',{name:agentLabel(task.agent??'?'),role:roleLabel(task.role)}):'';}
/** What a working task is doing right now, from the host's own tool records. Never a claim of success. */
const PROGRESS:Record<string,[string,string]>={dervo_read_file:[t('Reading'),t('Read')],dervo_write_file:[t('Writing'),t('Wrote')],dervo_edit_file:[t('Editing'),t('Edited')],dervo_create_file:[t('Creating'),t('Created')],
 dervo_list_files:[t('Listing'),t('Listed')],dervo_search_files:[t('Searching'),t('Searched')],dervo_update_task_guide:[t('Updating the task guide'),t('Updated the task guide')],
 dervo_run_tests:[t('Running the check'),t('Ran the check')]};
export function taskProgress(task:{state:string;tokens:TaskToken[];binding?:{provider?:string}}):string{
 if(task.state!=='working')return '';
 const who=task.binding?.provider?providerAuthor(task.binding.provider):t('the provider');
 const last=task.tokens.at(-1);
 if(!last)return t('Waiting for {who}. No file is changed yet.',{who});
 const words=PROGRESS[last.tool];
 if(!words)return t('Waiting for {who}.',{who});
 const target=last.tool==='dervo_update_task_guide'||last.tool==='dervo_run_tests'?'':last.path?` ${last.path}`:'';
 const attempt=(last.tool==='dervo_write_file'||last.tool==='dervo_edit_file'||last.tool==='dervo_create_file')&&last.attempt?` ${t('(attempt {number})',{number:last.attempt})}`:'';
 return ['executing','running','delivering','queued'].includes(last.state)?`${words[0]}${target}${attempt}`:`${words[1]}${target}${attempt}. ${t('Waiting for {who}.',{who})}`;
}
export type Attempt={unrecorded?:string[];native?:boolean;number:number;
 /** Committed files this run removed, keyed by path (native_evidence.finish): their diff is every line going, so they read as Deleted. */
 deleted?:Record<string,unknown>;check_timeout_seconds?:number;state:string;kind?:string;created?:string;diff:Record<string,string>;before:unknown;after:unknown;argv:string[];blocker?:string;test?:null|{argv:string[];stdout:string;stderr:string;exit_status:number;stopped:string|null;check_profile?:string}};
export function canApproveAttempt(task:Pick<CodingTask,'check_profile'>,attempt:Attempt):boolean {
 // A full-access attempt Dervo couldn't record completely can't be approved; the host refuses it too.
 if(attempt.unrecorded?.length)return false;
 if(task.check_profile==='manual-review')return attempt.kind==='manual-review'&&attempt.state==='unverified'&&attempt.test==null&&attempt.argv.length===0;
 return attempt.state==='verified'&&attempt.test?.exit_status===0&&!attempt.test.stopped;
}
const CHECK_LABELS:Record<string,string>={'manual-review':t('No automated checks'),'python-unittest':t('Python unittest'),'node-test':t('Node test'),'typescript-check':t('TypeScript check'),'rust-check':t('Rust type check'),'rust-test':t('Rust tests')};
/** The recorded check profile in the user's words. A missing value is an older record, not a Python guess: the argv shows what ran. */
export function checkProfileLabel(profile:string|null|undefined):string{
 if(!profile)return t('Not recorded (older record; the argv shows what ran)');
 return CHECK_LABELS[profile]?`${CHECK_LABELS[profile]} (${profile})`:profile;
}
/** What a check exit means for the reader: process success, not correctness. stopped names why a run doesn't count even at exit 0. */
export function checkExitDetail(exit:number,stopped:string|null|undefined):string{
 if(stopped==='no_tests_reported')return t('ran no tests, so it doesn’t count as passing');
 if(stopped)return t('stopped: {why}',{why:stopped.replace(/_/g,' ')});
 return exit===0?t('passed'):t('failed (exit {exit})',{exit});
}
// i18n-ignore: the host's own reason, compared below so the same words aren't shown twice
const RESTORATION_UNAVAILABLE_HOST='File restoration is unavailable because Dervo can\'t guarantee preservation of concurrent external edits. Inspect the recorded diff and snapshots.';
const RESTORATION_UNAVAILABLE=t('Dervo won’t put this file back on its own, in case something else changed it since. The changes above show what to undo by hand.');
/** What task_detail returns: the task, its evidence, and whether retry and restoration can run now. ``rebound`` means the
 * conversation has moved to another account since, so nothing here can act on it. */
/** The approve button's words. Inside a git repository approving also commits the task's files, so the button says so. */
export function approveLabel(git:boolean|undefined,plain=t('Approve')):string{return git===true?t('Approve and commit'):plain;}
/** The line beside Approve in the task sheet. Inside a git repository approving commits exactly the task's files, so it
 * says how many, and the branch when a record names it (task_detail doesn't). Anywhere else it records your decision,
 * and nothing says "attempt 1", which is Dervo's word, not yours (UX review 23 Sep, M4). */
export function approvingLine(git:boolean|undefined,files:string[],branch?:string|null):string{
 if(git!==true||!files.length)return t('Approving records your decision.');
 if(files.length===1)return branch?t('Approving commits this file to {branch}.',{branch}):t('Approving commits this file.');
 return branch?t('Approving commits these {count} files to {branch}.',{count:files.length,branch}):t('Approving commits these {count} files.',{count:files.length});
}
/** A task that carried on from a handoff snapshot commits the stopped run's files with its own (task_detail's
 * ``carried``): the ones it includes, and the ones it flagged and leaves out, with why. ``id`` goes back with Approve or
 * Commit, so the host commits only what this list showed. */
export type CarriedPlan={id:string;from_task_id:string;here:string;to:string;files:string[];flagged:{path:string;why:string}[]};
const listed=(items:string[],shown=3)=>items.length>shown?t('{items} and {count} more',{items:items.slice(0,shown).join(', '),count:items.length-shown}):items.join(', ');
/** "Includes 2 files Claude changed before the handoff: a.py, b.py." Null when nothing from before the handoff is in. */
export function carriedLine(carried:CarriedPlan|null|undefined):string|null{
 if(!carried?.files.length)return null;
 const count=carried.files.length;
 return t('Includes {count} files {who} changed before the handoff: {files}.',{count,who:carried.here,files:listed(carried.files)});
}
/** One line for each file the host left out, with its reason. Those are never committed. */
export function leftOutLines(carried:CarriedPlan|null|undefined):string[]{
 return (carried?.flagged??[]).map(item=>t('Left out: {path}, {why}.',{path:item.path,why:item.why.replace(/[.]$/,'')}));
}
/** The files approving commits: this task's own and the carried ones, once each. */
export function committedFiles(changed:string[],carried:CarriedPlan|null|undefined):string[]{return [...new Set([...changed,...(carried?.files??[])])];}
/** What Approve or Commit sends for a task that carried on: the plan's id. Nothing for any other task. */
export function carriedArgs(carried:CarriedPlan|null|undefined):{carriedId?:string}{return carried?{carriedId:carried.id}:{};}
/** The note beside Approve for a task that carried on: what it includes from before the handoff, and what it leaves out. */
export function CarriedNote({carried,className='dv-sheet-note'}:{carried:CarriedPlan|null|undefined;className?:string}){
 const line=carriedLine(carried),left=leftOutLines(carried);
 if(!line&&!left.length)return null;
 return <div className={className} data-testid="carried-note">{line&&<p>{line}</p>}{left.map(text=><p key={text}>{text}</p>)}</div>;
}
export type Detail={task:CodingTask;git?:boolean;rebound?:boolean;carried?:CarriedPlan|null;restoration?:FileRestoration[];retry?:{available:boolean};reconcile?:ReconcileStatus;evidence:{attempts:Attempt[];review?:{decision:string}}};
/** What the Reconcile action says before it runs: the outcome is unknown, what an inspection can and cannot settle, and that nothing is replayed. */
export function reconcileDetail(status:ReconcileStatus):string{
 if(!status.available)return t('Reconcile is unavailable: {reason}',{reason:status.reason});
 if(status.kind==='restoration')return t('File restoration has an uncertain outcome. Reconcile reports the manual inspection boundary. Nothing is replayed or overwritten.');
 return t('Creation of {path} was interrupted before confirmation, so its outcome is unknown, not a success. Reconcile inspects the current files: if the target is absent, the recorded intent is dropped and the task can continue; if the target is present, it is left exactly as found and stays unknown. Nothing is retried, replayed or overwritten.',{path:status.path??t('the target')});
}
/** The host's report after one inspection, in the user's terms. Never a claim that the interrupted creation succeeded. */
export function reconcileOutcome(report:ReconcileReport):string{
 if(report.action==='dropped_absent_creation')return t('Settled: {path} is absent, so the interrupted creation never reached disk. The recorded intent was dropped and nothing was written. The task can continue from its recorded scope.',{path:report.path??t('the target')});
 if(report.action==='inspect')return t('Still unknown: {detail}',{detail:report.detail});
 return report.detail;
}
/** Who acts, where, and under what confinement. The identity, model, project and visibility stay in view; the confinement
 * boundaries sit behind one disclosure so the primary text stays short. ``children`` extends that disclosure with the
 * caller's own technical rules, so a dialog shows one details block instead of several stacked ones. */
export function TaskDisclosure({binding,detailsTestId,summary=t('Confinement and boundaries'),children}:{binding:Binding;detailsTestId?:string;summary?:string;children?:ReactNode}){
 return <div className="space-y-2 text-sm"><p>{binding.provider} · {binding.identity} {t('(expected)')} · {binding.model}</p><p className="break-all">{t('Project: {cwd}. Conversation: {visibility}. Edits change the listed project files on this Mac.',{cwd:binding.cwd,visibility:binding.visibility})}</p>
  <details data-testid={detailsTestId} className="space-y-2"><summary className="cursor-pointer">{summary}</summary>
   <p>{t('Private conversation visibility doesn\'t make filesystem edits private.')}</p>
   <p>{t('Only explicitly listed existing files can be read or edited through host tools. Tests run on isolated snapshots. Shell, browser, apps, hooks, external MCP servers and tool network access remain disabled.')}</p>
   <p>{binding.provider==='claude'?t('Unmodified Claude Code sees only the Dervo host tools; a stopped or vanished run is recorded as interrupted or unknown, never as success.'):t('Account and policy checks run before the request is sent.')}</p>
   {children}
  </details></div>;
}
export function TaskStart({open,onOpenChange,projectId,channelId,binding,refresh}:{open:boolean;onOpenChange:(v:boolean)=>void;projectId:string;channelId:string;binding:Binding;refresh:()=>Promise<unknown>}){
 const [text,setText]=useState(''),[files,setFiles]=useState(''),[testFile,setTestFile]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function create(){setBusy(true);setError('');try{await local('task_create',{projectId,channelId,text,files:files.split('\n').filter(Boolean),testFile});await refresh();onOpenChange(false);setText('');}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('New coding task')}</DialogTitle><DialogDescription>{t('Save the scope first. Nothing is sent or edited until you approve execution.')}</DialogDescription></DialogHeader><div className="max-h-[70vh] space-y-4 overflow-auto"><TaskDisclosure binding={binding}/><label className="block text-sm">{t('Development request')}<textarea aria-label={t('Development request')} className="mt-1 w-full rounded border bg-background p-2" value={text} onChange={e=>setText(e.target.value)}/></label><label className="block text-sm">{t('Existing relative files, one per line')}<textarea aria-label={t('Allowed files')} className="mt-1 w-full rounded border bg-background p-2" placeholder={'sample.py\ntest_sample.py'} value={files} onChange={e=>setFiles(e.target.value)}/></label><Input aria-label={t('Test file')} placeholder="test_sample.py" value={testFile} onChange={e=>setTestFile(e.target.value)}/>{error&&<p role="alert" className="text-sm text-destructive">{error}</p>}<Button disabled={busy||!text||!files||!testFile||!CODING_PROVIDERS.has(binding.provider)} onClick={()=>void create()}>{t('Save task scope')}</Button>{!CODING_PROVIDERS.has(binding.provider)&&<p>{t('Coding tasks need a Codex or Claude Code account binding.')}</p>}</div></DialogContent></Dialog>;
}
/** The decision bar under a task's report (design system, .dv-decision): a glyph, one short sentence about the outcome,
 * one clause about checks or the commit, then what you can do. It's the one home for the task's outcome and for its
 * error in the thread; the Progress panel says how the run went, never this. Nothing shows while the owner works: the
 * live step sits under the reply and the panel spins. Nothing shows under a text answer either. */
export type Decision={glyph:GlyphState;accent?:boolean;lead:string;clause?:string;inspect:string;stop?:boolean};
const CHECK_WORDS:Record<string,string>={'python-unittest':'Python','node-test':'Node','typescript-check':'TypeScript','rust-check':'Rust','rust-test':'Rust'};
/** 17.1 (#42): a review that was skipped (its reviewer deleted, or on an account this one isn't linked to) says why on the
 * decision bar, not only in Inspect: the host's first sentence, with an automatic reviewer's name shown as "Reviewer". */
function skippedReview(review?:Pick<TaskReview,'phase'|'error'>|null):string{
 if(review?.phase!=='skipped'||!review.error)return '';
 const named=review.error.replace(/@(review-[a-f0-9]{24})\b/g,(_,name:string)=>agentLabel(name));
 return ` ${named.match(/^.*?[.?](?=\s|$)/s)?.[0]??named}`;
}
/** The project's own checks after a full-access run (project_checks, on the run's record): what they found, in the
 * host's words, or null when none ran. */
export type RunChecks={state:'running'|'passed'|'new_failures'|'unclear'|'not_run'|'skipped';summary?:string|null};
export function runChecks(task:Partial<Pick<CodingTask,'runs'>>):RunChecks|null{
 const run=(task.runs??[]).at(-1) as {checks?:RunChecks}|undefined;
 return run&&typeof run==='object'&&run.checks&&typeof run.checks.state==='string'?run.checks:null;
}
const sentence=(text:string)=>/[.?!]$/.test(text)?text:`${text}.`;
/** One clause about checks, from the record: what Dervo ran, or that it ran nothing. */
export function checksClause(task:Pick<CodingTask,'check_profile'|'full_access'|'completion'|'collaboration'>&Partial<Pick<CodingTask,'runs'>>,latest?:Attempt):string{
 const review=task.collaboration?.review?.phase==='passed'||task.completion?.review_phase==='passed'?` ${t('Review passed.')}`:skippedReview(task.collaboration?.review);
 if(task.full_access){  // start_checks runs them whatever the grant's own check profile is
  // Full access runs the project's own checks once you've turned them on (Settings, Checks and review). Before this the
  // bar said "No checks ran in Dervo." even when they had (critic, 9 Oct).
  const ran=runChecks(task);
  if(ran?.state==='running')return t('Dervo is running your checks.');
  if(ran?.summary)return `${sentence(hostMessage(ran.summary))}${ran.state==='passed'?review:''}`;
 }
 if(task.check_profile==='manual-review'||task.full_access)return `${t('No checks ran in Dervo.')}${review}`;
 const name=CHECK_WORDS[task.check_profile??'']?t('{name} check',{name:CHECK_WORDS[task.check_profile??'']}):t('The check');
 const exit=typeof task.completion?.test_exit_status==='number'?task.completion.test_exit_status:latest?.test?.exit_status;
 if(exit===0)return `${t('{name} passed.',{name})}${review}`;
 if(typeof exit==='number')return t('{name} failed.',{name});
 return `${t('No checks ran in Dervo.')}${review}`;
}
/** 16.2 (Bryce, 27 Sep, #25): a file this run couldn't record because another thread's result is still waiting with it
 * (native_evidence.unrecorded puts that thread on the row). The block names the thread and the bar offers to open it. */
export type HeldRow=import('./ResultNotes').Unrecorded&{task_id?:string|null;thread_id?:string|null;thread?:string|null};
export function heldBy(task:Pick<CodingTask,'unrecorded'>):HeldRow|null{
 return (task.unrecorded as HeldRow[]|undefined)?.find(row=>!!row.thread_id)??null;
}
/** The holding thread's name, or null when the host only knows it's another thread (it sends "another thread"). */
const heldName=(held:HeldRow)=>held.thread&&held.thread!=='another thread'?held.thread:null;
function heldClause(held:HeldRow,count:number,blocked:boolean):string{
 const name=heldName(held);
 const who=name?count===1?t('{thread} has a result waiting with this file.',{thread:name}):t('{thread} has a result waiting with one of these files.',{thread:name})
  :count===1?t('Another thread has a result waiting with this file.'):t('Another thread has a result waiting with one of these files.');
 return `${who} ${blocked?t('Approve that one first, then send this again.'):t('Approve that one first, then ask for a correction here.')}`;
}
export function decisionLine(task:CodingTask,latest?:Attempt):Decision|null{
 const phase=task.collaboration?.review?.phase??'';
 const reviewOpen=REVIEW_OPEN.has(phase)&&!(phase==='pending'&&task.state==='working');
 const who=providerAuthor(task.binding.provider);
 const uncertain=task.state==='disconnected'||task.runs.some(run=>(run as {state?:string})?.state==='uncertain')||task.tokens.some(token=>token.state==='uncertain');
 if(task.origin==='collaboration')return null;
 if(task.state==='working'&&!reviewOpen)return null;
 if(reviewOpen)return {glyph:'running',lead:stoppedTask(task)?t('You paused it. The review is still running.'):t('Independent review running.'),clause:phase==='pending'?t('It starts when the request finishes.'):undefined,inspect:t('Inspect'),stop:true};
 // A quit that paused a run before it could finish its step (coding_integration.CLOSE_LOST): still unconfirmed, but you
 // paused it, so it says so rather than "Outcome not confirmed".
 if(uncertain&&quitPaused(task))return {glyph:'stopped',lead:t('Paused when Dervo quit.'),clause:hostMessage(String(task.error)),inspect:t('Inspect')};
 // Beta.18: the provider's own record shows one step that could reach outside the folder (a shell command, a connector)
 // started with no end, so that step is the one thing to ask about (backend/lost_runs.py, direction B).
 const record=lostRecord(task);
 if(uncertain&&task.state==='disconnected'&&record?.state==='unsure'){
  const at=typeof record.step?.at==='number'?formatDate(record.step.at*1000,{hour:'numeric',minute:'2-digit'}):'';
  return {glyph:'unknown',accent:true,lead:t('One step may not have finished.'),clause:[at?t('{step} started at {at} and Dervo restarted before it ended.',{step:stepName(record.step),at}):t('{step} started and Dervo restarted before it ended.',{step:stepName(record.step)}),
   t('Running it again could do it twice.')].join(' '),inspect:t('Inspect')};
 }
 // Beta.17.1: lost track is unknown, never the failure X, with what the run was last seen doing (run_ending).
 if(uncertain)return {glyph:'unknown',lead:t('Outcome not confirmed.'),clause:[task.error?hostMessage(String(task.error)):t('Dervo lost track of this run.'),lastSeenLine(endingOf(task))].filter(Boolean).join(' '),inspect:t('Inspect')};
 // Your own Pause (Escape, Pause, or quitting Dervo) is one quiet line, not a failure (Bryce, 29 Sep: the old "Stopped
 // before it finished." read as something going wrong). The line above the composer says how to carry on, and Resume
 // (GuideActions) does it for you.
 // Beta.18: Dervo settled it as paused after a restart, from the provider's own record. Said as that, not as your Pause.
 if(stoppedTask(task)&&endingOf(task)?.by==='restart'){const restarted=endingDecision(task);if(restarted)return restarted;}
 if(stoppedTask(task)){
  // A thread on its own branch works in its own folder, not the project folder (beta.18, thread folders).
  const where=task.binding as {cwd?:string;project_cwd?:string}|undefined;
  const own=!!where?.cwd&&!!where.project_cwd&&where.cwd!==where.project_cwd;
  return {glyph:'stopped',lead:t('Paused.'),clause:task.read_only?undefined:own?t('Its edits so far are on this thread’s branch.'):t('Its edits so far are in your project folder.'),inspect:t('Inspect')};
 }
 // Beta.17.1: a limit, a run that never started and finished work with a Dervo step left undone aren't failures.
 const ended=endingDecision(task);
 if(ended)return ended;
 // Beta.15: a run that finished but left only files Dervo can't record didn't stop early. Say so, and list them below.
 const unrecorded=task.unrecorded??[];
 const unrecordedCount=unrecorded.reduce((total,row)=>total+(row.count??1),0);
 const held=heldBy(task);
 if(task.state==='blocked'&&unrecorded.length&&!task.suggest)return {glyph:'needs',accent:true,lead:t('Nothing to approve.'),clause:held?heldClause(held,unrecordedCount,true):t('Dervo couldn’t record the {count} files this run left.',{count:unrecordedCount}),inspect:t('Inspect')};
 if(task.state==='blocked'){
  if(task.suggest)return null;  // The handoff row is the home for a failure on the provider's side.
  return {glyph:'failed',lead:t('{who} stopped before finishing.',{who}),clause:task.error?hostMessage(String(task.error)):undefined,inspect:t('Inspect')};
 }
 if(task.state==='needs_permission')return {glyph:'needs',accent:true,lead:t('Waiting for your permission.'),clause:t('Check where Dervo can work before it starts.'),inspect:t('Review the scope')};
 if(task.state==='ready_for_review'){
  if(staleCompleted(task))return {glyph:'needs',lead:t('The files changed after this result.'),clause:t('Ask for changes to continue from the current files.'),inspect:t('Inspect')};
  if(correctionRequested(task))return {glyph:'needs',lead:t('Waiting for your feedback.'),clause:t('Say what to change and the task carries on.'),inspect:t('Inspect')};
  if(unrecorded.length)return {glyph:'needs',accent:true,lead:t('Can’t approve yet.'),clause:held?heldClause(held,unrecordedCount,false):t('Dervo couldn’t record {count} files from this run.',{count:unrecordedCount}),inspect:t('Inspect')};
  if(needsAttention(task))return {glyph:'needs',accent:true,lead:t('Needs attention.'),clause:task.error?hostMessage(String(task.error)):skippedReview(task.collaboration?.review).trim()||(phase&&phase!=='passed'?t('Independent review ended {phase}.',{phase}):t('The result didn’t complete.')),inspect:t('Inspect')};
  return {glyph:'needs',accent:true,lead:t('Ready for review.'),clause:checksClause(task,latest),inspect:t('Inspect')};
 }
 if(task.state==='completed')return task.error?{glyph:'needs',accent:true,lead:t('Needs attention.'),clause:hostMessage(String(task.error)),inspect:t('Inspect')}:{glyph:'done',lead:t('Finished.'),clause:checksClause(task,latest),inspect:t('Inspect')};
 if(task.state==='idle'){
  if(task.error)return {glyph:'needs',accent:true,lead:t('Needs attention.'),clause:hostMessage(String(task.error)),inspect:t('Inspect')};
  const line=commitLine(task.commit);
  const automatic=task.commit?.how==='automatic'&&!!task.commit.sha&&!task.commit.reverted;  // 16.2: Commit results automatically
  return {glyph:'done',lead:automatic?t('Committed automatically.'):task.review==='approved'?t('Approved.'):t('Finished.'),clause:line??undefined,inspect:t('Inspect')};
 }
 if(task.state==='answered'){
  if(task.error)return {glyph:'needs',accent:true,lead:t('Needs attention.'),clause:hostMessage(String(task.error)),inspect:t('Inspect')};
  // Beta.18 (record-b): an answer whose run met a commit from outside it, or wrote files outside the project, keeps a
  // quiet card so GuideActions can say so (native_evidence.finish sets moved_head and outside on answered tasks too).
  const noted=!!(task as {moved_head?:unknown}).moved_head||!!(task as {outside?:unknown}).outside;
  return noted?{glyph:'done',lead:t('Finished.'),inspect:t('Inspect')}:null;
 }
 return null;
}
const DETAIL_STATES=new Set(['ready_for_review','completed','blocked','disconnected']);
/** What the thread's feed says about a task that its record (task_detail) could have changed with. The footer asks for the
 * record again when this changes, instead of every 5 s: b161/window found a 40-task thread re-reading about 8 full records
 * a second (diffs and check output included), which kept the window busy and its memory churning while nothing moved
 * (evidence/b161-window/NOTES.md). A run whose outcome Dervo couldn't observe still polls every 5 s, so reconcile news
 * shows up without a feed change; the rest re-read once a minute in case a record changed some other way. */
export function detailKey(task:CodingTask):string{
 const runs=task.runs.map(run=>(run as {id?:string;state?:string})??{}).map(run=>`${run.id??''}:${run.state??''}`).join(',');
 const done=task.completion;
 return [task.state,task.error??'',task.review??'',runs,done?`${done.at}:${done.attempt}:${done.run_id??''}:${done.review_phase??''}:${done.human_approved??''}`:'',
  (task.completions??[]).length,(task.corrections??[]).length,task.collaboration?.review?.phase??'',task.commit?JSON.stringify(task.commit):'',(task.unrecorded??[]).length].join('|');
}
export const SETTLED_DETAIL_MS=60000,UNCERTAIN_DETAIL_MS=5000;
export function TaskFooter({task,projectId,channelId,onInspect,extra,guideVisible=false,onRunIt,onOpenHome,onResume}:{task:CodingTask;projectId:string;channelId:string;guideVisible?:boolean;onInspect:(trigger:HTMLElement)=>void;extra?:ReactNode;onRunIt?:()=>void;onOpenHome?:()=>void;onResume?:()=>string|undefined}){
 const [error,setError]=useState(''),[busy,setBusy]=useState(false);
 const inspect=useRef<HTMLButtonElement>(null);
 const client=useQueryClient();
 const uncertain=task.runs.some(run=>(run as {state?:string})?.state==='uncertain');
 const wantsDetail=DETAIL_STATES.has(task.state)||uncertain;
 const detail=useQuery({queryKey:['task-detail',projectId,channelId,task.id],enabled:wantsDetail,refetchInterval:uncertain?UNCERTAIN_DETAIL_MS:SETTLED_DETAIL_MS,queryFn:()=>local<Detail>('task_detail',{projectId,channelId,taskId:task.id})});
 const key=detailKey(task),seen=useRef(key);
 useEffect(()=>{if(seen.current===key)return;seen.current=key;if(wantsDetail)void detail.refetch();},[key,wantsDetail]);
 const latest=detail.data?.task.id===task.id?detail.data.evidence.attempts.at(-1):undefined;
 const decision=decisionLine(task,latest);
 const helper=helperLabel(task);
 async function stop(){setBusy(true);setError('');try{await local('task_stop',{projectId,channelId,taskId:task.id});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function refresh(){await client.invalidateQueries({queryKey:['conversation-feed',projectId,channelId]});if(wantsDetail)await detail.refetch();}
 const handoff=guideVisible&&!!(task.suggest||task.route?.suggest);
 const asked=(task.questions??[]).some(question=>{const read=readWorkerQuestion(question);return !!read&&!inTray(read,projectId,channelId);});
 if(!decision&&!error&&!extra&&!handoff&&!helper&&!asked&&!task.has_thread_proposals&&!task.thread_source)return null;
 return <div className="dervo-task-footer" data-testid="task-footer" data-task-id={task.id} data-task-origin={task.origin??''} data-settled={decision?undefined:'true'}>
  {helper&&<p className="dv-caption" data-testid="helper-label">{helper}</p>}
  {asked&&<QuestionHistory projectId={projectId} channelId={channelId} questions={task.questions}/>}
  {decision&&<div className="dv-decision" data-decision={decision.glyph} data-long={decision.lead.length+(decision.clause?.length??0)>110||undefined} role="group" aria-label={t('Your decision')}>
   <div className="dv-decision-line">{decision.glyph==='running'?<WorkingMark working/>:<StatusGlyph state={decision.glyph} accent={decision.accent}/>}
   <p className="dv-what" role="status"><b>{decision.lead}</b>{decision.clause?` ${decision.clause}`:''}</p></div>
   <button ref={inspect} type="button" className="dv-btn dv-btn-quiet" onClick={event=>onInspect(event.currentTarget)}>{decision.inspect}</button>
   {decision.stop&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void stop()}>{busy?t('Stopping…'):t('Stop')}</button>}
   <GuideActions task={task} projectId={projectId} channelId={channelId} detail={detail.data?.task.id===task.id?detail.data:undefined} onChanged={refresh} onCorrection={()=>onInspect(inspect.current??document.body)} onRunIt={onRunIt} onResume={onResume}/>
   {task.state==='idle'&&<NotPushedLine task={task} projectId={projectId} channelId={channelId} onOpenHome={onOpenHome}/>}
   {(task.state==='ready_for_review'||task.state==='blocked')&&<HeldThreadButton held={heldBy(task)}/>}
   {!!task.unrecorded?.length&&(task.state==='ready_for_review'||task.state==='blocked')&&<UnrecordedFiles rows={task.unrecorded} blocked={task.state==='blocked'}/>}
   {!!task.alongside?.count&&<AlongsideNote alongside={task.alongside}/>}
   {!!task.inherited?.length&&task.state==='ready_for_review'&&<InheritedNote rows={task.inherited}/>}
   {error&&<p role="alert" className="dv-decision-note dv-decision-error">{error}</p>}
  </div>}
  {/* With the Progress panel open the shell hands the handoff offer to the panel; the panel puts it back here (TaskGuide). */}
  {handoff&&<div className="dervo-handoff-slot" data-dervo-handoff-slot={task.id}/>}
  {extra}
  {task.thread_source&&<ThreadSourceLink source={task.thread_source}/>}
  {task.has_thread_proposals&&<TaskFanout key={task.id} projectId={projectId} channelId={channelId} taskId={task.id}/>}
 </div>;
}
/** Opens the thread whose waiting result holds a file this run couldn't record, on that result when Dervo knows it. */
function HeldThreadButton({held}:{held:HeldRow|null}){
 const navigation=useChannelNavigation();
 if(!held?.thread_id||(!navigation.onOpenTask&&!navigation.onOpenChannel))return null;
 const open=()=>navigation.onOpenTask&&held.task_id?navigation.onOpenTask(held.thread_id!,held.task_id):navigation.onOpenChannel?.(held.thread_id!);
 return <button type="button" className="dv-btn dv-btn-quiet" data-testid="held-thread" onClick={open}>{heldName(held)?t('Open {thread}',{thread:heldName(held)!}):t('Open that thread')}</button>;
}
/** Everything recorded about one task, and the actions that act on that record: the result, changes and checks, the activity
 * and scope, approval, correction, retry, restoration and reconcile. Shown in the Task guide's Record tab, and in the Task result
 * dialog wherever the guide isn't open. ``intent`` jumps to a correction field when the guide asks for one. */
export function TaskRecord({task,projectId,channelId,refresh,open,extra,records,intent}:{task:CodingTask;projectId:string;channelId:string;refresh:()=>Promise<unknown>;open:boolean;extra?:ReactNode;records?:ReactNode;intent?:{kind:'feedback'|'correction';id:number}}){
 const [feedback,setFeedback]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[report,setReport]=useState<ReconcileReport|null>(null);
 const feedbackRevision=useRef(0);
 function editFeedback(value:string){feedbackRevision.current+=1;setFeedback(value);}
 const body=useRef<HTMLDivElement>(null);
 const [page,setPage]=useState<'result'|'changes'|'record'>(task.state==='needs_permission'?'record':'result');
 const [correctionJump,jumpCorrection]=useState(0);
 const feedbackField=useRef<HTMLTextAreaElement>(null);
 const [focusFeedback,setFocusFeedback]=useState(false);
 useEffect(()=>{if(focusFeedback&&feedbackField.current){feedbackField.current.focus();setFocusFeedback(false);}},[focusFeedback,task]);
 useEffect(()=>{if(correctionJump)body.current?.querySelector<HTMLTextAreaElement>('[data-correction]')?.focus();},[correctionJump]);
 const scope={projectId,channelId,taskId:task.id};
 useEffect(()=>{setError('');},[task.id,projectId,channelId,open]);
 async function reconcile(){setBusy(true);setError('');try{setReport(await reconcileTask(projectId,channelId,task.id));await refresh();await detail.refetch();}catch(e){setError(String(e));}finally{setBusy(false);}}
 const detail=useQuery({queryKey:['task-detail',projectId,channelId,task.id],enabled:open,refetchInterval:query=>open?(query.state.data?.task.state==='working'||REVIEW_OPEN.has(query.state.data?.task.collaboration?.review?.phase??'')?700:5000):false,queryFn:()=>local<Detail>('task_detail',scope)});
 // The guide's Ask for changes on a completed result: open the result page, then focus the field that sends it as soon as it renders, once.
 const pendingFocus=useRef<'feedback'|'correction'|null>(null);
 useEffect(()=>{if(!intent)return;setPage('result');pendingFocus.current=intent.kind;},[intent?.id]);
 useEffect(()=>{
  const kind=pendingFocus.current;if(!kind)return;
  const field=kind==='feedback'?feedbackField.current:body.current?.querySelector<HTMLTextAreaElement>('[data-correction]');
  if(field){field.focus();pendingFocus.current=null;}
 });
 async function action(operation:string,args:Record<string,unknown>={}){
  const submittedRevision=feedbackRevision.current;
  setBusy(true);setError('');
  try{
   await local(operation,{...scope,...args});
   // Clear only the acknowledged submission. Keep any draft typed while it was sending.
   if(operation==='task_continue'&&feedbackRevision.current===submittedRevision)setFeedback('');
   await refresh();await detail.refetch();
   if(operation==='task_review'&&args.decision==='rejected')setFocusFeedback(true);
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 const attempts=detail.data?.evidence.attempts??[],latest=attempts.at(-1);
 const labels:Record<string,string>={idle:t('Result approved'),needs_permission:t('Needs permission'),working:t('Working'),answered:t('Answered in text, no files changed'),blocked:t('Needs attention'),disconnected:t('Disconnected')};
 const reviewActive=REVIEW_OPEN.has(task.collaboration?.review?.phase??'');
 const attention=needsAttention(task);
 // One outcome line from recorded facts, then the evidence. Completed is a host fact; attention is an unfinished approval-off result.
 const outcome=correctionRequested(task)?t('Describe what you’d like changed below. Sending your feedback starts the correction.'):stoppedTask(task)?t('The latest run ended before completion. Read any saved output before continuing.'):task.state==='completed'?completionDetail(task):task.state==='ready_for_review'&&reviewActive?t('Agents are still reviewing this result. Nothing is asked of you yet; Stop ends the review.'):staleCompleted(task)?t('The files changed after this result was completed, so approval is unavailable for the old result and the completed record is kept as recorded.'):attention?attentionDetail(task):task.state==='answered'?(task.read_only?t('Read-only answer. Inspect Activity and scope for the files Dervo read. No files were changed or checks run.'):t('Text answer only. No project file was changed and no test was run in this run.')):task.state==='ready_for_review'?(task.check_profile==='manual-review'?t('No automated checks ran. Inspect and try the changes, then approve or request a correction.'):t('Inspect the diff, host checks and review activity, then approve or request a correction.')):'';
 const response=[...task.messages].reverse().find(message=>message.role==='assistant')?.text;
 const check=latest?.test;
 const checkText=task.check_profile==='manual-review'?t('No automated checks ran. Approval records your decision only.'):check?checkExitDetail(check.exit_status,check.stopped):latest?t('No check is recorded for the latest attempt.'):task.completion?t('Recorded completion: {result}',{result:checkExitDetail(task.completion.test_exit_status,null)}):t('No check is recorded for this task.');
 // Whether the check line says it passed: the latest attempt's check, or the completion record when no attempt is loaded.
 const passed=task.check_profile!=='manual-review'&&(check?check.exit_status===0&&!check.stopped:!latest&&task.completion?.test_exit_status===0);
 const canCorrectCompleted=!!detail.data&&(latest?.kind!=='restoration'||latest.state==='restored')&&(task.state==='completed'||staleCompleted(task));
 const warnings=[...new Set([task.error?hostMessage(String(task.error)):undefined,latest?.blocker,check?.stopped].filter(Boolean))];
 return <>
  <nav className="dervo-task-nav" aria-label={t('Task views')}>{(['result','changes','record'] as const).map(value=><button key={value} aria-pressed={page===value} onClick={event=>{setPage(value);event.currentTarget.focus();if(body.current)body.current.scrollTop=0;}}>{value==='result'?t('Result'):value==='changes'?t('Changes and checks'):task.state==='needs_permission'?t('Execution scope'):t('Activity and scope')}</button>)}</nav>
  <div className="dervo-task-body" ref={body}>
   <div className={`dervo-task-outcome ${staleCompleted(task)||attention?'dervo-task-attention':''}`} data-testid="task-outcome"><div className="dervo-task-outcome-top"><p role="status">{resultLabel(task)||labels[task.state]||task.state}</p>{(task.state==='working'||reviewActive)&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void action('task_stop')}>{t('Stop task')}</Button>}{canCorrectCompleted&&<Button size="sm" variant="outline" onClick={()=>{setPage('result');jumpCorrection(value=>value+1);}}>{t('Write a correction')}</Button>}</div>{outcome&&<p>{outcome}</p>}</div>
   {warnings.map(warning=><p key={warning} role="alert" className="dervo-task-warning">{warning}</p>)}
   {(error||detail.error)&&<p role="alert" className="dervo-task-warning">{error||String(detail.error)}</p>}
   {!detail.data&&!detail.error&&<p role="status" className="dervo-task-note">{t('Reading the saved changes and checks…')}</p>}
   {page==='result'&&<>
    {detail.data&&<div className="dervo-task-checks" data-testid="task-check-summary"><section><h3>{t('Check')}</h3><p>{checkProfileLabel(latest?(check?.check_profile??task.check_profile):task.completion?task.completion.check_profile:task.check_profile)}</p><p>{checkText}</p>{passed&&<p className="dervo-task-note">{t('Passing tests don’t prove it works. Try it yourself.')}</p>}{latest&&!check&&task.completion&&<p>{t('Saved completion record: {check}, {result}.',{check:checkProfileLabel(task.completion.check_profile),result:checkExitDetail(task.completion.test_exit_status,null)})}</p>}</section><section><h3>{t('Independent review')}</h3><p>{task.collaboration?.review?reviewLabel(task.collaboration.review,task.error?hostMessage(String(task.error)):undefined):t('No independent review is recorded.')}</p></section></div>}
    {response&&<section className="dervo-task-response"><h3>{t('Latest saved response from {who}',{who:providerAuthor(task.binding.provider)})}</h3><div data-testid="task-response"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{img:()=>null}}>{response}</ReactMarkdown></div></section>}
    {detail.data&&<section className="dervo-task-next" aria-label={t('Next action')}>
 {((detail.data?.reconcile&&detail.data.reconcile.attempt!==null)||report)&&<div className="space-y-2" data-testid="reconcile">{detail.data?.reconcile&&detail.data.reconcile.attempt!==null&&<><p>{reconcileDetail(detail.data.reconcile)}</p><Button variant="outline" disabled={busy||!detail.data.reconcile.available} onClick={()=>void reconcile()}>{detail.data.reconcile.kind==='restoration'?t('Inspect interrupted restoration'):t('Reconcile interrupted creation')}</Button></>}{report&&<p role="status" data-testid="reconcile-report">{reconcileOutcome(report)}</p>}</div>}
 {detail.data?.retry?.available&&task.state!=='working'&&<div className="space-y-2"><p>{t('No file attempt was recorded for the last run. There\'s no new result to approve. Retry sends your feedback to this same task and account. If scope approval is no longer current, you\'ll need to approve it again before anything starts.')}</p><label>{t('Retry task feedback')}<textarea aria-label={t('Retry task feedback')} className="w-full rounded border bg-background p-2" value={feedback} onChange={e=>editFeedback(e.target.value)}/></label><Button disabled={busy||!feedback.trim()} onClick={()=>void action('task_continue',{text:feedback})}>{t('Retry task')}</Button></div>}
 {latest&&latest.kind!=='restoration'&&!detail.data?.retry?.available&&task.state==='ready_for_review'&&!staleCompleted(task)&&!correctionRequested(task)&&<div className="space-y-2">{attention&&<p>{t('Approval is off for this request, so there\'s nothing to approve. The host continues this task only after a recorded correction request: Ask for changes first, then send your feedback with Continue task.')}</p>}{!attention&&<CarriedNote carried={detail.data?.carried} className="dervo-task-note"/>}<div className="flex gap-2">{!attention&&<Button disabled={busy||reviewActive||!canApproveAttempt(task,latest)} onClick={()=>void action('task_review',{attemptNumber:latest.number,decision:'approved',...carriedArgs(detail.data?.carried)})}>{approveLabel(detail.data?.git,t('Approve result'))}</Button>}<Button variant={attention?'default':'outline'} disabled={busy||reviewActive} onClick={()=>void action('task_review',{attemptNumber:latest.number,decision:'rejected'})}>{t('Ask for changes')}</Button></div></div>}
 {!detail.data?.retry?.available&&(detail.data?.evidence.review?.decision==='rejected'||task.review==='rejected')&&task.state==='ready_for_review'&&!staleCompleted(task)&&<div className="space-y-2"><label>{t('What would you like changed?')}<textarea ref={feedbackField} aria-label={t('Continue task feedback')} className="w-full rounded border bg-background p-2" value={feedback} onChange={e=>editFeedback(e.target.value)}/></label><Button disabled={busy||!feedback.trim()} onClick={()=>void action('task_continue',{text:feedback})}>{t('Continue task')}</Button></div>}
 {latest?.kind==='restoration'&&latest.state==='restored'&&!staleCompleted(task)&&<div className="space-y-2"><p>{t('Restoration didn\'t run checks or start an agent. Send feedback to request fresh work and checks.')}</p><label>{t('Restoration follow-up')}<textarea aria-label={t('Restoration follow-up')} className="w-full rounded border bg-background p-2" value={feedback} onChange={e=>editFeedback(e.target.value)}/></label><Button disabled={busy||!feedback.trim()} onClick={()=>void action('task_continue',{text:feedback})}>{t('Continue after restoration')}</Button></div>}
 {canCorrectCompleted&&<div className="space-y-2"><p>{t('Result approval was off for this request. A correction continues this same task from the current files (your later edits are kept); nothing about the completed result is recorded as rejected or approved.')}{task.completion?.review_phase==='passed'?` ${t('The earlier independent review stays recorded; a correction gets a fresh review of the corrected files within the same request budget.')}`:''}</p><label>{t('Correction request')}<textarea aria-label={t('Correction request')} data-correction className="w-full rounded border bg-background p-2" value={feedback} onChange={e=>editFeedback(e.target.value)}/></label><Button disabled={busy||!feedback.trim()} onClick={()=>void action('task_continue',{text:feedback})}>{t('Request a correction')}</Button></div>}

    </section>}
    {extra&&<section>{extra}</section>}
   </>}
   {page==='changes'&&<div className="dervo-task-evidence">
    {detail.data&&!attempts.length&&<p>{t('No file attempt is recorded for this task.')}</p>}
 {attempts.length>0&&<p>{t('Changes, attempt by attempt.')} {task.check_profile==='manual-review'?t('No automated checks ran, so try the changes yourself.'):t('Passing tests don’t prove it works, because the agent can edit tests too. Try it yourself.')}</p>}
 {attempts.map(a=><section key={a.number} className="space-y-2 border-t pt-3"><h3>{attemptHeading(a)}</h3>{Object.entries(a.diff).map(([path,diff])=><div key={path}><p>{path}{a.deleted&&Object.hasOwn(a.deleted,path)?` · ${t('Deleted')}`:''}</p><pre className="overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-2">{diff||t('No change')}</pre>{detail.data?.restoration?.filter(r=>r.attempt===a.number&&r.path===path).map(r=><div key={r.path} className="space-y-1"><p>{RESTORATION_UNAVAILABLE}</p>{!r.available&&r.reason&&r.reason!==RESTORATION_UNAVAILABLE_HOST&&r.reason!==RESTORATION_UNAVAILABLE&&<p>{r.reason}</p>}</div>)}</div>)}<details><summary>{t('Check output')}</summary><p>{t('Maximum check time: {seconds} seconds.',{seconds:a.check_timeout_seconds??5})}</p><pre className="whitespace-pre-wrap break-all">{checkOutput(a)}</pre></details><details><summary>{t('File snapshots')}</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({before:a.before,after:a.after},null,2)}</pre></details></section>)}

   </div>}
   {page==='record'&&<div className="dervo-task-record">
 <TaskDisclosure binding={task.binding} summary={t('Scope, session and delivery records')}><p>{t('Task session: {id}. Separate from ordinary chat.',{id:task.session_id??t('Not created')})}</p><p>{task.read_only?t('Read-only access to the selected project. Hidden files, credential paths, links and generated dependencies are excluded.'):t('Granted files')}</p><pre className="whitespace-pre-wrap break-all">{task.files.join('\n')}</pre>{task.folders?.length?<><p>{t('Granted folders (list, search, read and edit regular files found inside, create new files inside)')}</p><pre className="whitespace-pre-wrap break-all" data-testid="task-folders">{task.folders.join('\n')}</pre></>:null}{!task.read_only&&<><p>{t('Host-selected check')}</p><pre className="whitespace-pre-wrap break-all" data-testid="task-check-profile">{checkProfileLabel(task.check_profile)}</pre><p data-testid="task-check-timeout">{t('Maximum check time: {seconds} seconds per check.',{seconds:task.check_timeout_seconds??5})}</p><p>{t('Host-selected test argv')}</p><pre className="whitespace-pre-wrap break-all">{JSON.stringify(task.argv)}</pre></>}{task.completion&&<p data-testid="completion-check">{t('Completed check: {check}, {result}.',{check:checkProfileLabel(task.completion.check_profile),result:checkExitDetail(task.completion.test_exit_status,null)})}</p>}<p>{t('Routine tools and delivery records')}</p><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({tokens:task.tokens,runs:task.runs},null,2)}</pre></TaskDisclosure>
    {detail.data?.evidence.review&&<p>{t('Human result review: {decision}',{decision:detail.data.evidence.review.decision==='approved'?t('approved'):detail.data.evidence.review.decision==='rejected'?t('rejected'):detail.data.evidence.review.decision})}</p>}
    {records}
 {task.state==='needs_permission'&&<div className="flex gap-2"><Button disabled={busy} onClick={()=>void action('task_approve',{scopeVersion:task.scope_version})}>{t('Approve scope and start')}</Button><Button variant="outline" disabled={busy} onClick={()=>void action('task_deny')}>{t('Deny execution')}</Button></div>}

   </div>}
  </div></>;
}

/** A task's diff as plain mono lines, the first few shown with "Show all" for the rest (task-sheet-changes.html). */
export function diffLines(diff:string):{kind:'add'|'del'|'ctx'|'hunk';text:string}[]{
 return diff.split('\n').filter((line,index,all)=>!(index===all.length-1&&line===''))
  .filter(line=>!line.startsWith('+++')&&!line.startsWith('---'))
  .map(line=>line.startsWith('@@')?{kind:'hunk' as const,text:line}:line.startsWith('+')?{kind:'add' as const,text:line.slice(1)}:line.startsWith('-')?{kind:'del' as const,text:line.slice(1)}:{kind:'ctx' as const,text:line.startsWith(' ')?line.slice(1):line});
}
const SHOWN_LINES=12;
function FileDiff({path,diff,extra,deleted}:{path:string;diff:string;extra?:ReactNode;deleted?:boolean}){
 const [all,setAll]=useState(false);
 const lines=diffLines(diff),added=lines.filter(l=>l.kind==='add').length,removed=lines.filter(l=>l.kind==='del').length;
 const shown=all?lines:lines.slice(0,SHOWN_LINES);
 return <section>
  <div className="dv-file-head"><span>{path}</span><span className="dv-caption">{deleted?t('Deleted, {count} lines',{count:removed}):diff?[added?`+${added}`:'',removed?`−${removed}`:''].filter(Boolean).join(' ')||t('No change'):t('No change')}</span></div>
  {diff&&<pre className="dv-diff">{shown.map((line,index)=><span key={index} className={`dv-ln dv-ln-${line.kind}`}><i>{line.kind==='add'?'+':line.kind==='del'?'-':''}</i><span>{line.text}</span></span>)}</pre>}
  {lines.length>SHOWN_LINES&&<button type="button" className="dv-more-lines" onClick={()=>setAll(value=>!value)}>{all?t('Show fewer'):t('Show all {count} lines',{count:lines.length})}</button>}
  {extra}
 </section>;
}
/** A copy button whose confirmation is a tick on itself for 1.5 seconds, never a toast (surfaces, inventory 13). */
export function CopyTick({text,label,name}:{text:string;label:string;name?:string}){
 const [state,setState]=useState<''|'done'|'failed'>('');
 useEffect(()=>{if(!state)return;const timer=setTimeout(()=>setState(''),1500);return()=>clearTimeout(timer);},[state]);
 return <button type="button" className="dv-btn dv-btn-quiet" title={state==='failed'?t('Couldn’t copy'):undefined} aria-label={name&&!state?name:undefined}
  onClick={()=>{void Promise.resolve().then(()=>navigator.clipboard.writeText(text)).then(()=>setState('done')).catch(()=>setState('failed'));}}>
  {state==='done'?<svg className="dv-copied" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>
   :<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="7.5" height="7.5" rx="1.5"/><path d="M3 10.5V4a1 1 0 011-1h6.5"/></svg>}
  {state==='done'?t('Copied'):state==='failed'?t('Couldn’t copy'):label}
 </button>;
}
const WHEN=(at?:number)=>typeof at==='number'&&at>1e9?formatDate(at*1000,{hour:'numeric',minute:'2-digit'}):'';
/** One attempt's heading: its number, how its check went, and what blocked it. */
const attemptHeading=(a:Attempt)=>`${t('Attempt {number}',{number:a.number})}${a.test?` · ${t('check {result}',{result:checkExitDetail(a.test.exit_status,a.test.stopped)})}`:` · ${t('not checked')}`}${a.blocker?` · ${a.blocker}`:''}`;
// i18n-ignore: a process's own fields, named as a terminal shows them
const checkOutput=(a:Attempt)=>`argv: ${JSON.stringify(a.test?.argv??a.argv)}\nexit: ${a.test?.exit_status??t('Not run')}\nstdout:\n${a.test?.stdout??''}\nstderr:\n${a.test?.stderr??''}\n${a.blocker??a.test?.stopped??''}`;

/** The task record as a sheet beside the thread (surfaces/task-sheet*.html): one status line, the Asked / Did / Checked /
 * Review facts, the agent's report, and the decision pinned at the foot. Every control the Task result dialog had is
 * here: stop, approve, ask for changes, retry, the correction and restoration follow-ups, reconcile, approve or decline
 * the scope, each attempt's diff and check output. IDs and JSON sit behind "Full record, for support". The Progress
 * panel keeps TaskRecord for its own task. */
export function TaskDialog({task,projectId,channelId,refresh,open,onOpenChange,onCloseAutoFocus,activation,extra,records,threadName}:{task:CodingTask;projectId:string;channelId:string;refresh:()=>Promise<unknown>;open:boolean;onOpenChange:(v:boolean)=>void;onCloseAutoFocus?:(event:Event)=>void;activation?:number;extra?:ReactNode;records?:ReactNode;threadName?:string}){
 const heading=useRef<HTMLHeadingElement>(null);
 // Returning from a nested request can activate the same mounted task. Keep its draft and refocus the heading.
 useEffect(()=>{if(open)heading.current?.focus();},[activation,open]);
 const asked=task.messages.find(message=>message.role==='user');
 const title=task.state==='needs_permission'&&!asked?t('Execution scope'):task.origin==='collaboration'?t('Help from @{agent}',{agent:task.agent??t('helper')}):asked?.text.split('\n')[0].trim()||t('Task result');
 const kicker=[task.origin==='collaboration'?t('Helper task'):task.state==='needs_permission'?t('Task'):t('Earlier task'),threadName?t('in {threadName}',{threadName}):'',asked?.created_at?`· ${task.state==='needs_permission'?t('asked {time}',{time:WHEN(asked.created_at)}):WHEN(asked.created_at)}`:''].filter(Boolean).join(' ');
 return <Sheet open={open} onOpenChange={onOpenChange} modal={false}>
  <SheetContent side="right" className="dv-sheet-r" aria-describedby={undefined} onOpenAutoFocus={event=>{event.preventDefault();heading.current?.focus();}} onCloseAutoFocus={onCloseAutoFocus}
   onInteractOutside={event=>event.preventDefault()}>
   <div className="dv-sh-head"><div><span className="dv-caption">{kicker}</span><SheetTitle ref={heading} tabIndex={-1} className="dv-title">{title}</SheetTitle></div></div>
   <TaskSheetBody task={task} projectId={projectId} channelId={channelId} refresh={refresh} open={open} extra={extra} records={records}/>
  </SheetContent>
 </Sheet>;
}

const SHEET_TABS=[['result',t('Result')],['changes',t('Changes and checks')],['record',t('Scope and activity')]] as const;
/** The sheet's three views as tabs, drawn as the kit's segmented control: arrows move and choose, one tab stop. */
function SheetTabs({value,onChange}:{value:'result'|'changes'|'record';onChange:(next:'result'|'changes'|'record')=>void}){
 const refs=useRef<(HTMLButtonElement|null)[]>([]);
 return <div className="dv-seg" role="tablist" aria-label={t('Task views')}>
  {SHEET_TABS.map(([key,label],index)=><button key={key} ref={element=>{refs.current[index]=element;}} type="button" role="tab" aria-selected={value===key} aria-checked={value===key}
   tabIndex={value===key?0:-1} onClick={event=>{event.currentTarget.focus();onChange(key);}} onKeyDown={event=>{
    const next=event.key==='ArrowRight'?(index+1)%3:event.key==='ArrowLeft'?(index+2)%3:event.key==='Home'?0:event.key==='End'?2:-1;
    if(next<0)return;event.preventDefault();refs.current[next]?.focus();onChange(SHEET_TABS[next][0]);}}>{label}</button>)}
 </div>;
}
function TaskSheetBody({task,projectId,channelId,refresh,open,extra,records}:{task:CodingTask;projectId:string;channelId:string;refresh:()=>Promise<unknown>;open:boolean;extra?:ReactNode;records?:ReactNode}){
 const client=useQueryClient();
 const lost=(task.state==='disconnected'||task.runs.some(run=>typeof run==='object'&&run!==null&&'state' in run&&run.state==='uncertain'))&&task.state!=='working'&&!REVIEW_OPEN.has(task.collaboration?.review?.phase??'');
 const [feedback,setFeedback]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(''),[report,setReport]=useState<ReconcileReport|null>(null);
 const feedbackRevision=useRef(0);
 function editFeedback(value:string){feedbackRevision.current+=1;setFeedback(value);}
 const body=useRef<HTMLDivElement>(null),field=useRef<HTMLTextAreaElement>(null);
 const [page,setPage]=useState<'result'|'changes'|'record'>(task.state==='needs_permission'?'record':'result');
 const [focusField,setFocusField]=useState(0);
 useEffect(()=>{if(focusField)field.current?.focus();},[focusField,task]);
 const scope={projectId,channelId,taskId:task.id};
 useEffect(()=>{setError('');},[task.id,projectId,channelId,open]);
 const detail=useQuery({queryKey:['task-detail',projectId,channelId,task.id],enabled:open,refetchInterval:query=>open?(query.state.data?.task.state==='working'||REVIEW_OPEN.has(query.state.data?.task.collaboration?.review?.phase??'')?700:5000):false,queryFn:()=>local<Detail>('task_detail',scope)});
 async function reconcile(){setBusy('reconcile');setError('');try{setReport(await reconcileTask(projectId,channelId,task.id));await refresh();await detail.refetch();}catch(e){setError(String(e));}finally{setBusy('');}}
 async function action(operation:string,args:Record<string,unknown>={},key=operation){
  const submittedRevision=feedbackRevision.current;
  setBusy(key);setError('');
  try{
   await local(operation,{...scope,...args});
   if(operation==='task_settle')await client.invalidateQueries({queryKey:['local-status']});
   // Clear only the acknowledged submission. Keep any draft typed while it was sending.
   if(operation==='task_continue'&&feedbackRevision.current===submittedRevision)setFeedback('');
   await refresh();await detail.refetch();
   if(operation==='task_review'&&args.decision==='rejected'){setPage('result');setFocusField(value=>value+1);}
  }catch(e){setError(String(e));}finally{setBusy('');}
 }
 const attempts=detail.data?.evidence.attempts??[],latest=attempts.at(-1);
 const labels:Record<string,string>={idle:t('Result approved'),needs_permission:t('Waiting for your go-ahead'),working:t('Working'),answered:t('Answered in text, no files changed'),blocked:t('Needs attention'),disconnected:t('Disconnected')};
 const reviewActive=REVIEW_OPEN.has(task.collaboration?.review?.phase??'');
 const attention=needsAttention(task);
 const retry=!!detail.data?.retry?.available&&task.state!=='working';
 const check=latest?.test;
 const checkText=task.check_profile==='manual-review'?t('No automated checks ran. Approval records your decision only.'):check?checkExitDetail(check.exit_status,check.stopped):latest?t('No check is recorded for the latest attempt.'):task.completion?t('Recorded completion: {result}',{result:checkExitDetail(task.completion.test_exit_status,null)}):t('No check is recorded for this task.');
 // Whether the check line says it passed: the latest attempt's check, or the completion record when no attempt is loaded.
 const passed=task.check_profile!=='manual-review'&&(check?check.exit_status===0&&!check.stopped:!latest&&task.completion?.test_exit_status===0);
 const canCorrectCompleted=!!detail.data&&(latest?.kind!=='restoration'||latest.state==='restored')&&(task.state==='completed'||staleCompleted(task));
 const canDecide=!!latest&&latest.kind!=='restoration'&&!retry&&task.state==='ready_for_review'&&!staleCompleted(task)&&!correctionRequested(task);
 const rejected=!retry&&(detail.data?.evidence.review?.decision==='rejected'||task.review==='rejected')&&task.state==='ready_for_review'&&!staleCompleted(task);
 const restored=latest?.kind==='restoration'&&latest.state==='restored'&&!staleCompleted(task);
 const warnings=[...new Set([task.error?hostMessage(String(task.error)):undefined,latest?.blocker,check?.stopped].filter(Boolean))];
 // One line about the outcome when the facts alone don't say what to do next.
 const outcome=correctionRequested(task)?t('Say what you’d like changed below. Sending it starts the correction.'):stoppedTask(task)?t('The latest run ended before it finished. Read any saved output before continuing.'):task.state==='completed'?completionDetail(task)
  :task.state==='ready_for_review'&&reviewActive?t('Agents are still reviewing this result. Nothing is asked of you yet; Stop ends the review.'):staleCompleted(task)?t('The files changed after this result was completed, so approval is unavailable for the old result and the completed record is kept as recorded.')
  :attention?attentionDetail(task):task.state==='answered'?(task.read_only?t('Read-only answer. Scope and activity lists the files Dervo read. No files were changed or checks run.'):t('Text answer only. No project file was changed and no test was run in this run.')):'';
 const response=[...task.messages].reverse().find(message=>message.role==='assistant')?.text;
 const asked=task.messages.find(message=>message.role==='user');
 const changed=task.completion?.changed_files??(latest?Object.keys(latest.diff):[]);
 const ending=['blocked','disconnected'].includes(task.state)&&!reviewActive?endingWord(task):null;  // beta.17.1, run_ending
 const glyph:GlyphState=task.state==='working'||reviewActive?'running':task.state==='needs_permission'?'needs':stoppedTask(task)?'stopped':ending?ending.glyph:['blocked','disconnected'].includes(task.state)||attention?'failed':'done';
 const label=resultLabel(task)||ending?.word||labels[task.state]||task.state;
 const word=label===t('Needs your review')?t('Ready for your review'):label;
 const checkShort=task.check_profile==='manual-review'||!check?(latest||task.completion?t('no checks ran'):''):check.exit_status===0&&!check.stopped?t('check passed'):t('check failed');
 const caption=task.state==='needs_permission'?t('Nothing runs until you approve'):[changed.length?t('{count} files',{count:changed.length}):'',checkShort].filter(Boolean).join(' · ');
 const did=changed.length?changed.length>2?t('Changed {files} and {count} more.',{files:formatList(changed.slice(0,2)),count:changed.length-2}):t('Changed {files}.',{files:formatList(changed)}):task.state==='answered'?t('Answered in text. No files changed.'):task.state==='working'?t('Still working.'):task.state==='needs_permission'?t('Nothing yet. It starts when you approve the scope.'):t('No file change is recorded.');
 const approved=detail.data?.evidence.review?.decision==='approved'||task.review==='approved';
 // The check in words, never its raw id: "Python unittest passed.", or why nothing counts.
 const profile=latest?(check?.check_profile??task.check_profile):task.completion?task.completion.check_profile:task.check_profile;
 // A run that only read the project has no check, which isn't an older record (RC review, 9 Oct).
 const checkedLine=task.read_only&&!profile&&!check?t('Nothing to check: it only read the project.'):task.check_profile==='manual-review'?checkText:check?`${CHECK_LABELS[profile??'']??checkProfileLabel(profile)} ${checkText}.`:`${CHECK_LABELS[profile??'']??checkProfileLabel(profile)}. ${checkText}`;
 const fieldFor=retry?{label:t('Retry task feedback'),title:t('Anything to add before it tries again?'),send:t('Retry'),note:t('No file attempt was recorded for the last run, so there’s nothing new to approve. Retry sends this to the same task and account; if the scope approval is no longer current, you’ll approve it again first.')}
  :rejected?{label:t('Continue task feedback'),title:t('What would you like changed?'),send:t('Send')}
  :restored?{label:t('Restoration follow-up'),title:t('Anything to add?'),send:t('Send'),note:t('Restoration didn’t run checks or start an agent. Send feedback to ask for fresh work and checks.')}
  :canCorrectCompleted?{label:t('Correction request'),title:t('What would you like changed?'),send:t('Send'),note:`${t('Approval was off for this request. A correction continues this same task from the current files, and your later edits are kept; nothing about the completed result is recorded as rejected or approved.')}${task.completion?.review_phase==='passed'?` ${t('The earlier review stays recorded; the corrected files get a fresh one.')}`:''}`}:null;
 const what=task.state==='needs_permission'?t('You can stop it at any time with esc.'):task.state==='working'||reviewActive?t('It’s working. Stop ends this run.')
  :canDecide&&!attention?approvingLine(detail.data?.git,committedFiles(changed,detail.data?.carried),task.commit?.branch):canDecide&&attention?t('Approval is off for this request. Ask for changes to carry on.'):approved&&latest?t('You approved this result.'):'';
 const full=JSON.stringify({task:task.id,session:task.session_id,scope:{files:task.files,folders:task.folders??[],check:task.check_profile,argv:task.argv,timeout:task.check_timeout_seconds??5},
  binding:task.binding,completion:task.completion??null,tokens:task.tokens,runs:task.runs,attempts:attempts.map(a=>({number:a.number,state:a.state,kind:a.kind,test:a.test??null,before:a.before,after:a.after}))},null,2);
 return <>
  <div className="dv-sh-tabs"><SheetTabs value={page} onChange={value=>{setPage(value);if(body.current)body.current.scrollTop=0;}}/></div>
  <div className="dv-sh-body" ref={body}>
   <div className="dv-status-line" data-testid="task-outcome"><StatusGlyph state={glyph}/><span className="dv-state" data-state={glyph} role="status">{word}</span>{caption&&<span className="dv-caption">{caption}</span>}</div>
   {outcome&&<p className="dv-sheet-note">{outcome}</p>}
   {lost&&<p className="dv-sheet-note">{t('Dervo lost track of this run. Inspect its saved output before marking it as stopped. Your project files stay as they are.')}</p>}
   {warnings.map(warning=><p key={warning} role="alert" className="dv-line-alert">{warning}</p>)}
   {(error||detail.error)&&<p role="alert" className="dv-line-alert">{error||String(detail.error)}</p>}
   {!detail.data&&!detail.error&&<p role="status" className="dv-sheet-note">{t('Reading the saved changes and checks…')}</p>}
   {page==='result'&&<>
    <dl className="dv-facts dv-wide" data-testid={detail.data?'task-check-summary':undefined}>
     {asked&&<><dt>{t('Asked')}</dt><dd>{asked.text}</dd></>}
     <dt>{t('Did')}</dt><dd>{did}</dd>
     {detail.data&&<><dt>{t('Checked')}</dt><dd>{checkedLine}{passed?` ${t('Passing tests don’t prove it works, so try it too.')}`:''}{latest&&!check&&task.completion?` ${t('Saved completion record: {check}, {result}.',{check:checkProfileLabel(task.completion.check_profile),result:checkExitDetail(task.completion.test_exit_status,null)})}`:''}</dd>
      <dt>{t('Review')}</dt><dd>{task.collaboration?.review?reviewLabel(task.collaboration.review,task.error?hostMessage(String(task.error)):undefined):t('No second agent reviewed this one.')}</dd></>}
    </dl>
    {(canDecide||task.state==='completed')&&<CarriedNote carried={detail.data?.carried}/>}
    {response&&<section><p className="dv-label">{t('{who}’s report',{who:providerAuthor(task.binding.provider)})}</p><div className="dv-report" data-testid="task-response"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{img:()=>null}}>{response}</ReactMarkdown></div></section>}
    {((detail.data?.reconcile&&detail.data.reconcile.attempt!==null)||report)&&<section data-testid="reconcile">{detail.data?.reconcile&&detail.data.reconcile.attempt!==null&&<><p className="dv-sheet-note">{reconcileDetail(detail.data.reconcile)}</p>
     <div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy||!detail.data.reconcile.available} onClick={()=>void reconcile()}>{detail.data.reconcile.kind==='restoration'?t('Inspect interrupted restoration'):t('Reconcile interrupted creation')}</button></div></>}
     {report&&<p role="status" data-testid="reconcile-report" className="dv-sheet-note">{reconcileOutcome(report)}</p>}</section>}
    {extra&&<section className="dervo-task-linked">{extra}</section>}
   </>}
   {page==='changes'&&<>
    {detail.data&&!attempts.length&&<p className="dv-sheet-note">{t('No file attempt is recorded for this task.')}</p>}
    {attempts.length>0&&<p className="dv-sheet-note">{task.check_profile==='manual-review'?t('No automated checks ran, so try the changes yourself.'):t('Passing tests don’t prove it works, because the agent can edit tests too.')}</p>}
    {attempts.map(a=><section key={a.number}>
     <div className="dv-status-line"><StatusGlyph state={a.test?(a.test.exit_status===0&&!a.test.stopped?'done':'failed'):'idle'}/><h3 className="dv-state">{attemptHeading(a)}</h3></div>
     {Object.entries(a.diff).map(([path,diff])=><FileDiff key={path} path={path} diff={diff} deleted={!!a.deleted&&Object.hasOwn(a.deleted,path)} extra={detail.data?.restoration?.filter(r=>r.attempt===a.number&&r.path===path).map(r=><div key={r.path} className="dv-sheet-note"><p>{RESTORATION_UNAVAILABLE}</p>{!r.available&&r.reason&&r.reason!==RESTORATION_UNAVAILABLE_HOST&&r.reason!==RESTORATION_UNAVAILABLE&&<p>{r.reason}</p>}</div>)}/>)}
     <details className="dv-disclose"><summary><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>{t('Check output')}</summary>
      <div className="dv-disclose-body"><p>{t('Maximum check time: {seconds} seconds.',{seconds:a.check_timeout_seconds??5})}</p><pre>{checkOutput(a)}</pre></div></details>
    </section>)}
   </>}
   {page==='record'&&<>
    <dl className="dv-kv">
     <dt>{task.read_only?t('Can read'):t('Can edit')}</dt><dd className="dv-mono-list">{task.read_only?t('The selected project, read only.'):[...task.files,...(task.folders??[]).map(folder=>`${folder}/`)].join('\n')||t('Nothing yet')}</dd>
     <dt>{t('Can’t touch')}</dt><dd>{task.read_only?t('Hidden files, credential paths, links and generated dependencies.'):t('Everything else in the folder, hidden files, credentials and links. Shell, browser, apps and network stay off.')}</dd>
     {!task.read_only&&<><dt>{t('Check')}</dt><dd><span data-testid="task-check-profile">{CHECK_LABELS[task.check_profile??'']??checkProfileLabel(task.check_profile)}</span>{task.argv.length?<> {tr('on {file}',{file:<code>{task.argv.at(-1)}</code>})}</>:null}, <span data-testid="task-check-timeout">{t('at most {seconds} seconds',{seconds:task.check_timeout_seconds??5})}</span>.</dd></>}
     <dt>{t('Folder')}</dt><dd><code>{task.binding.cwd}</code>{task.binding.visibility==='private'?t('. The thread is private, but its edits still change these files on this Mac.'):''}</dd>
     <dt>{t('Runs on')}</dt><dd>{t('{who} · {identity} · {model}, in its own session, apart from this chat.',{who:providerAuthor(task.binding.provider),identity:task.binding.identity,model:modelName(task.binding.model)})}</dd>
     {task.completion&&<><dt>{t('Completed check')}</dt><dd data-testid="completion-check">{CHECK_LABELS[task.completion.check_profile??'']??checkProfileLabel(task.completion.check_profile)}, {checkExitDetail(task.completion.test_exit_status,null)}.</dd></>}
    </dl>
    {records}
    <details className="dv-disclose"><summary><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>{t('Full record, for support')}</summary>
     <div className="dv-disclose-body">
      <dl className="dv-kv"><dt>{t('Task')}</dt><dd><code>{task.id}</code></dd><dt>{t('Session')}</dt><dd><code>{task.session_id??t('Not created')}</code></dd>
       <dt>{t('Runs')}</dt><dd><code>{(task.runs as {id?:string}[]).map(run=>run?.id).filter(Boolean).join(', ')||t('None yet')}</code></dd>
       <dt>{t('Project')}</dt><dd><code>{task.binding.cwd}</code></dd></dl>
      <CopyTick text={full} label={t('Copy the full record')}/>
     </div></details>
   </>}
  </div>
  {fieldFor&&<div className="dv-sh-field">
   {fieldFor.note&&<p className="dv-sheet-note">{fieldFor.note}</p>}
   <label>{fieldFor.title}<textarea ref={field} aria-label={fieldFor.label} className="dv-input" value={feedback} onChange={event=>editFeedback(event.target.value)}/></label>
  </div>}
  <div className="dv-sh-foot">
   <span className="dv-what">{what}</span>
   {(task.state==='working'||reviewActive)&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void action('task_stop')}>{busy==='task_stop'?t('Pausing…'):t('Pause')}</button>}
   {lost&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void action('task_settle')}>{busy==='task_settle'?t('Leaving it stopped…'):t('Leave it stopped')}</button>}
   {task.state==='needs_permission'&&<><button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void action('task_deny')}>{busy==='task_deny'?t('Declining…'):t('Don’t run')}</button>
    <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy} onClick={()=>void action('task_approve',{scopeVersion:task.scope_version})}>{busy==='task_approve'?t('Starting…'):t('Approve and start')}</button></>}
   {canDecide&&<><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy||reviewActive} onClick={()=>void action('task_review',{attemptNumber:latest!.number,decision:'rejected'},'reject')}>{busy==='reject'?t('Sending…'):t('Ask for changes')}</button>
    {!attention&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy||reviewActive||!canApproveAttempt(task,latest!)} onClick={()=>void action('task_review',{attemptNumber:latest!.number,decision:'approved',...carriedArgs(detail.data?.carried)},'approve')}>{busy==='approve'?t('Approving…'):approveLabel(detail.data?.git)}</button>}</>}
   {fieldFor&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy||!feedback.trim()} onClick={()=>void action('task_continue',{text:feedback},'continue')}>{busy==='continue'?t('Sending…'):fieldFor.send}</button>}
  </div>
 </>;
}
