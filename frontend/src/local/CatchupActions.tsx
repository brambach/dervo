import {useState} from 'react';
import {Pause,RotateCcw} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {local,type WorkspaceRow} from './api';
import {approveLabel,type Detail} from './CodingTasks';

const RETRY_TEXT='Please try that again.';

/** Which one action a Catch up card offers beside Open and Record, from the saved row alone. The host checks again before
 * anything runs; a row can only nominate an action, never allow it. */
export function catchupAction(row:WorkspaceRow):'approve'|'retry'|'stop'|null{
  if(row.kind!=='task'||row.helper)return null;
  if(row.status==='working')return 'stop';
  if(row.status==='ready_for_review'&&row.humanApprovalRequired&&row.humanDecision!=='rejected'&&!row.stale&&!row.outcomeUncertain&&!row.reviewPending)return 'approve';
  if(row.status==='blocked'&&['failed','interrupted'].includes(row.runState??''))return 'retry';
  return null;
}

/** Why Catch up won't approve a result that carried on from a handoff: it commits files the stopped run changed too. */
export function carriedWords(here:string):string{return `This result includes files ${here} changed before the handoff. Open it to see them before approving.`;}

export function CatchupActions({row,projectId,disabled,onDone}:{row:WorkspaceRow;projectId:string;disabled:boolean;onDone:()=>Promise<unknown>}){
  const [busy,setBusy]=useState(false),[confirming,setConfirming]=useState(false),[error,setError]=useState(''),[git,setGit]=useState<boolean|undefined>(undefined);
  const [carried,setCarried]=useState<string|null>(null);
  const action=catchupAction(row);
  if(!action)return null;
  const scope={projectId,channelId:row.channelId,taskId:row.id};
  async function run(work:()=>Promise<unknown>){
    if(busy)return;
    setBusy(true);setError('');
    try{await work();setConfirming(false);await onDone();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy(false);}
  }
  // #27: the confirm promises a commit only when the host says the folder is a git repository. Reading the record is
  // read-only; until it answers (or if it fails) the question says Approve, which never promises a commit.
  function ask(){
    setConfirming(true);
    setCarried(null);
    local<Detail>('task_detail',scope).then(detail=>{setGit(detail?.git===true);setCarried(detail?.carried?carriedWords(detail.carried.here):null);}).catch(()=>setGit(undefined));
  }
  async function approve(){
    const detail=await local<Detail>('task_detail',scope);
    const latest=detail.evidence.attempts.at(-1);
    if(detail.rebound||!latest||latest.kind==='restoration'||detail.retry?.available||detail.task.state!=='ready_for_review')throw new Error('This result can’t be approved from here. See the record.');
    // A result that carried on from a handoff commits the stopped run's files too, and the list of them is in the task.
    if(detail.carried)throw new Error(carriedWords(detail.carried.here));
    await local('task_review',{...scope,attemptNumber:latest.number,decision:'approved'});
  }
  async function retry(){
    const detail=await local<Detail>('task_detail',scope);
    if(detail.rebound||!detail.retry?.available)throw new Error(detail.task.suggest?.other&&!detail.task.suggest.taken?`This task can be tried on ${detail.task.suggest.other.label} from its conversation.`:'This task can’t be retried from here. See the record.');
    await local('task_continue',{...scope,text:RETRY_TEXT});
  }
  return <>
    {action==='approve'&&!confirming&&<Button size="sm" variant="secondary" disabled={disabled||busy} onClick={ask}>Approve result</Button>}
    {action==='approve'&&confirming&&<span className="dervo-catchup-confirm" role="group" aria-label="Approve result">
      <span>{carried??(git?'Approve and commit this result without opening it?':'Approve this result without opening it?')}</span>
      {!carried&&<Button size="sm" disabled={busy} onClick={()=>void run(approve)}>{busy?'Approving…':approveLabel(git)}</Button>}
      <Button size="sm" variant="ghost" disabled={busy} onClick={()=>setConfirming(false)}>Cancel</Button>
    </span>}
    {action==='retry'&&<Button size="sm" variant="secondary" disabled={disabled||busy} onClick={()=>void run(retry)}><RotateCcw className="size-3.5"/>{busy?'Starting…':'Retry'}</Button>}
    {action==='stop'&&<Button size="sm" variant="ghost" disabled={disabled||busy} onClick={()=>void run(()=>local('task_stop',scope))}><Pause className="size-3 fill-current"/>{busy?'Pausing…':'Pause'}</Button>}
    {error&&<p role="alert" className="dervo-catchup-error">{error}</p>}
  </>;
}
