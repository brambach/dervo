import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {ActivityLine} from './ActivityLine';
import {ThreadPullRequest} from './PullRequest';
import {CarriedNote,REVIEW_OPEN,approveLabel,canApproveAttempt,carriedArgs,correctionRequested,handoffHoldsRetry,needsAttention,quitPaused,staleCompleted,stoppedTask,type CodingTask,type Detail} from './CodingTasks';
import {t} from '@/i18n';
import {CONTINUE_TEXT,RECHECK_TEXT,RESTART_TEXT,continuable,endingOf,lostRecord,recheckable} from './runEnding';

// i18n-ignore: sent to the agent as your message, and Dervo's words to agents stay English (CatchupActions and ConnectedApp send the same)
const RETRY_TEXT='Please try that again.';
/** What Resume says for you after a Pause (16.4). */
// i18n-ignore: sent to the agent as your message, like RETRY_TEXT
export const RESUME_TEXT='Carry on from where you paused.';
const short=(sha:string|null|undefined)=>typeof sha==='string'?sha.slice(0,7):'';

/** The one commit line: what Dervo committed for this task, what undid it, or why nothing was committed. */
export function commitLine(commit:CodingTask['commit']):string|null{
  if(!commit)return null;
  if(commit.sha&&commit.reverted&&typeof commit.reverted==='object'&&typeof (commit.reverted as {sha?:unknown}).sha==='string')
    return t('Undone in {sha}. This task’s files are back to how they were before it.',{sha:short((commit.reverted as {sha:string}).sha)});
  if(commit.sha)return (commit.branch?t('Committed {sha} on {branch}.',{sha:short(commit.sha),branch:commit.branch}):t('Committed {sha}.',{sha:short(commit.sha)}))+(typeof commit.index_warning==='string'&&commit.index_warning?` ${commit.index_warning}`:'');
  if(commit.skipped)return commit.skipped;
  if(commit.error)return t('Nothing was committed: {error}',{error:commit.error});
  return null;
}

/** Whether this task's commit is still only on this Mac: it's one of the commits Home's Push would send (push_preview's
 * list, the branch's commits that aren't on its upstream). False once it's pushed, once it's undone, and whenever the
 * host can't say (no git, no remote, an older host without push_preview), so the line never guesses. */
export function notPushed(commit:CodingTask['commit'],preview:{commits?:{sha:string}[]|null}|null|undefined):boolean{
  if(!commit?.sha||commit.reverted||!preview)return false;
  return (preview.commits??[]).some(row=>row?.sha===commit.sha);
}

/** "Not pushed yet." under the commit line, with a link to Home, where the Push button is (PLAN-github-push.md). It reads
 * the same push_preview query as Home's Now pane, so a push there clears it here.
 *
 * Beta.16 (b16/git): given the thread's ``channelId``, the line becomes the thread's pull request pieces
 * (PullRequest.tsx): Open pull request beside Undo, its confirm, and the PR once it's open. Home keeps its Push button. */
export function NotPushedLine({task,projectId,channelId,onOpenHome}:{task:CodingTask;projectId:string;channelId?:string;onOpenHome?:()=>void}){
  const committed=!!task.commit?.sha&&!task.commit.reverted;
  const push=useQuery({queryKey:['push-preview',projectId],enabled:committed&&!!projectId,retry:false,staleTime:15000,refetchOnWindowFocus:true,
    queryFn:()=>local<{commits?:{sha:string}[]|null}>('push_preview',{projectId})});
  if(committed&&channelId)return <ThreadPullRequest projectId={projectId} channelId={channelId} notOnGitHub={notPushed(task.commit,push.data)}/>;
  if(!committed||!notPushed(task.commit,push.data))return null;
  return <ActivityLine className="dv-decision-note" note="not-pushed" text={t('Not pushed yet.')}
    action={onOpenHome?{label:t('Push from Home'),onClick:onOpenHome,describe:t('Open Home to push this commit')}:null}/>;
}

/** Beta.18 (record-b): a commit made outside this run moved the folder's HEAD while it worked, and Dervo recorded the run
 * on top of it (native_evidence.changes, the git guard saw the agent run no git); and files the run wrote outside the
 * project, which Dervo never records. Both from the task record (``moved_head``, ``outside``), quiet lines under the card. */
export function OutsideNotes({task}:{task:CodingTask}){
  const moved=(task as {moved_head?:{commits?:{sha:string;subject?:string}[];branch?:string|null}}).moved_head;
  const outside=(task as {outside?:{count:number;files:string[]}}).outside;
  const first=moved?.commits?.[0];
  const commit=first?(first.subject?`${short(first.sha)} “${first.subject}”`:short(first.sha)):'';
  const more=(moved?.commits?.length??0)-1;
  return <>
    {moved&&<p className="dv-decision-note">{commit?(more>0?t('{count} commits landed on {branch} during this run from outside it, the first {commit}, so Dervo recorded the run on top of them.',{count:more+1,branch:moved.branch??'HEAD',commit})
      :t('A commit landed on {branch} during this run from outside it ({commit}), so Dervo recorded the run on top of it.',{branch:moved.branch??'HEAD',commit}))
      :t('The folder’s HEAD moved during this run from outside it, so Dervo recorded the run on top of where it is now.')}</p>}
    {outside?.files?.map(path=><p key={path} className="dv-decision-note"><code>{path}</code> {t('outside this project, not recorded')}</p>)}
    {!!outside&&outside.count>(outside.files?.length??0)&&<p className="dv-decision-note">{t('and {count} more outside this project',{count:outside.count-(outside.files?.length??0)})}</p>}
  </>;
}

/** What you can do with a task's outcome, as the buttons of the decision bar under its report (CodingTasks.tsx, TaskFooter).
 * Each button exists only when the host's record says the action can run: the same conditions the record uses, never a
 * guess. A note that needs a whole line (a lost run, an undo confirmation, an error) wraps under the buttons. */
/** ``onResume`` sends "Carry on from where you paused." as your next message in this thread; only the thread's newest
 * request gets it, and it returns why it couldn't (a draft in the composer). */
export function GuideActions({task,projectId,channelId,detail,onChanged,onCorrection,onRunIt,onResume}:{task:CodingTask;projectId:string;channelId:string;detail?:Detail;onChanged:()=>Promise<unknown>;onCorrection:(kind:'feedback'|'correction')=>void;onRunIt?:()=>void;onResume?:(text?:string)=>string|undefined}){
  const client=useQueryClient();
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[confirmUndo,setConfirmUndo]=useState(false);
  const scope={projectId,channelId,taskId:task.id};
  const reviewActive=REVIEW_OPEN.has(task.collaboration?.review?.phase??'');
  if(task.state==='working'||reviewActive||task.state==='needs_permission'||task.origin==='collaboration')return null;
  async function act(name:string,operation:string,args:Record<string,unknown>={},after?:()=>void){
    if(busy)return;
    setBusy(name);setError('');
    try{await local(operation,{...scope,...args});if(operation==='task_settle')await client.invalidateQueries({queryKey:['local-status']});await onChanged();after?.();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy('');}
  }
  const rebound=!!detail?.rebound;
  const attempts=detail?.evidence.attempts??[],latest=attempts.at(-1);
  const retry=!rebound&&!!detail?.retry?.available&&!handoffHoldsRetry(task);
  const stale=staleCompleted(task);
  const reviewable=!rebound&&!!latest&&latest.kind!=='restoration'&&!detail?.retry?.available&&task.state==='ready_for_review'&&!stale&&!correctionRequested(task);
  const approvable=reviewable&&!needsAttention(task);
  const commit=task.commit;
  const committed=!!commit?.sha&&!commit.reverted;
  const commitable=!rebound&&((task.state==='completed'&&!stale&&!commit?.sha)||(task.state==='idle'&&!!commit?.error));
  const undoable=!rebound&&committed&&(task.state==='idle'||task.state==='completed');
  const changed=(task.completion?.changed_files?.length??0)>0||(commit?.files?.length??0)>0;
  const runnable=!!onRunIt&&changed&&(task.state==='completed'||task.state==='idle'||task.state==='ready_for_review');
  // Dervo lost track of this run. Only you can end it, and settling never claims what the run did.
  const lost=!rebound&&(task.state==='disconnected'||task.runs.some(run=>(run as {state?:string}).state==='uncertain'))&&task.state!=='working';
  // Beta.18: the provider's record names one step that may have reached outside the folder. Continue, checking first
  // settles it and carries on in the same session, telling the agent that step's outcome is unknown (task_settle checking).
  const unsure=lost&&task.state==='disconnected'&&lostRecord(task)?.state==='unsure';
  // Beta.18: Dervo settled it as paused after a restart. Continue resumes the same session, like Resume after your Pause.
  const restarted=endingOf(task)?.by==='restart';
  // Beta.18, row 20: finished work whose checks didn't finish, or whose files changed after them.
  const recheck=!rebound&&!!onResume&&recheckable(task);
  // Pause (16.4): after your own Pause, Retry becomes Resume. It picks up in the same session on this task when the host
  // allows a retry (no checked edits since), which Codex can only do this way; otherwise it sends a message for you.
  const paused=!rebound&&(stoppedTask(task)||quitPaused(task));
  // Beta.17.1: a run Dervo ended at one of its limits gets Continue, the same way: in this task's session when a retry
  // can run, else as your next message.
  const limited=!rebound&&!paused&&continuable(endingOf(task));
  // At the attempt limit a retry in the same task is refused again, so Continue goes as a new message: a new task.
  const fresh=limited&&endingOf(task)?.limit==='attempts';
  const resumeHere=(paused||limited)&&(!retry||fresh)&&!!onResume;
  const outside=!!(task as {moved_head?:unknown}).moved_head||!!(task as {outside?:unknown}).outside;
  const nothing=!retry&&!resumeHere&&!reviewable&&!commitable&&!undoable&&!runnable&&!lost&&!recheck&&!outside&&!correctionRequested(task)&&!(task.state==='completed'||stale)&&!rebound;
  if(nothing&&!error)return null;
  return <>
    {retry&&!(fresh&&resumeHere)&&<button type="button" className="dv-btn dv-btn-primary" disabled={!!busy} onClick={()=>void act('retry','task_continue',{text:restarted?RESTART_TEXT:paused?RESUME_TEXT:limited?CONTINUE_TEXT:RETRY_TEXT})}>{busy==='retry'?t('Starting…'):restarted?t('Continue'):paused?t('Resume'):limited?t('Continue'):t('Retry')}</button>}
    {resumeHere&&<button type="button" className="dv-btn dv-btn-primary" disabled={!!busy} onClick={()=>{setError('');const why=limited?onResume!(CONTINUE_TEXT):restarted?onResume!(RESTART_TEXT):onResume!();if(why)setError(why);}}>{limited||restarted?t('Continue'):t('Resume')}</button>}
    {unsure&&<button type="button" className="dv-btn dv-btn-primary" disabled={!!busy} onClick={()=>void act('checking','task_settle',{checking:true})}>{busy==='checking'?t('Starting…'):t('Continue, checking first')}</button>}
    {lost&&<button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void act('settle','task_settle')}>{busy==='settle'?t('Leaving it stopped…'):t('Leave it stopped')}</button>}
    {recheck&&<button type="button" className="dv-btn dv-btn-primary" disabled={!!busy} onClick={()=>{setError('');const why=onResume!(RECHECK_TEXT);if(why)setError(why);}}>{t('Run the checks again')}</button>}
    {runnable&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={onRunIt}>{t('Run it')}</button>}
    {reviewable&&<button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void act('reject','task_review',{attemptNumber:latest!.number,decision:'rejected'},()=>onCorrection('feedback'))}>{t('Ask for changes')}</button>}
    {/* Beta.15: with files Dervo couldn't record, the card says "Can't approve yet", so no Approve beside it. */}
    {approvable&&!task.unrecorded?.length&&<button type="button" className="dv-btn dv-btn-primary" title={detail?.carried?t('Commits this task’s files and the ones listed from before the handoff. The agent never runs git.'):t('Commits exactly this task’s files. The agent never runs git, and outside a git repository nothing is committed.')} disabled={!!busy||!canApproveAttempt(task,latest!)} onClick={()=>void act('approve','task_review',{attemptNumber:latest!.number,decision:'approved',...carriedArgs(detail?.carried)})}>{busy==='approve'?t('Approving…'):approveLabel(detail?.git)}</button>}
    {!rebound&&correctionRequested(task)&&!reviewable&&<button type="button" className="dv-btn dv-btn-ghost" onClick={()=>onCorrection('feedback')}>{t('Write the correction')}</button>}
    {!rebound&&(task.state==='completed'||stale)&&<button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>onCorrection('correction')}>{t('Ask for changes')}</button>}
    {commitable&&<button type="button" className={task.state==='idle'?'dv-btn dv-btn-ghost':'dv-btn dv-btn-primary'} disabled={!!busy} onClick={()=>void act('commit','task_commit',carriedArgs(detail?.carried))}>{busy==='commit'?t('Committing…'):task.state==='idle'?t('Try committing again'):t('Commit these changes')}</button>}
    {undoable&&!confirmUndo&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>setConfirmUndo(true)}>{t('Undo')}</button>}
    {(approvable||commitable)&&<CarriedNote carried={detail?.carried} className="dv-decision-note"/>}
    {rebound&&<p className="dv-decision-note">{t('This conversation uses another account now, so this task can’t be continued or committed from here.')}</p>}
    {lost&&<p className="dv-decision-note">{unsure?t('Continue tells the agent that step’s outcome is unknown, so it checks before running it again. Everything else in this run is confirmed.'):quitPaused(task)?t('Its last step may not have finished. Resume to carry on from a summary, or leave it stopped. Your files stay exactly as they are; check them in git.'):t('Dervo lost track of this run, so it can’t say what it did. Leave it stopped to clear it. Your files stay exactly as they are; check them in git.')}</p>}
    <OutsideNotes task={task}/>
    {undoable&&confirmUndo&&<div className="dv-decision-note dv-decision-confirm" role="group" aria-label={t('Undo this task')}>
      <p>{t('Add a commit that puts this task’s files back to how they were before {sha}? Dervo refuses if later changes touch the same files.',{sha:short(commit!.sha)})}</p>
      <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void act('undo','task_undo',{},()=>setConfirmUndo(false))}>{busy==='undo'?t('Undoing…'):t('Undo')}</button>
      <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>setConfirmUndo(false)}>{t('Cancel')}</button>
    </div>}
    {error&&<p role="alert" className="dv-decision-note dv-decision-error">{error}</p>}
  </>;
}
