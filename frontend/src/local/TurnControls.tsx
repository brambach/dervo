import {DervoActivity,latestObservableActivity,type ActivityTone} from './DervoActivity';
import {REVIEW_OPEN,decisionLine,settledTask,type CodingTask} from './CodingTasks';
import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {MoreVertical} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/shared/ui/dialog';
import {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent,DropdownMenuItem} from '@/shared/ui/dropdown-menu';
import {local,type CodingPermission,type CollabPolicy,type HarnessAvailability,type LocalEvent} from './api';
import {ScopeProposalCard,type Proposal} from './ScopeProposal';
import {providerAuthor} from './providerLabel';
import {t} from '@/i18n';

/** Settled states whose decision bar under the report owns the outcome line. */
const DECIDED=new Set(['ready_for_review','completed','idle','needs_permission']);

type Binding={identity:string;provider:string;model:string;cwd:string};
type TurnStatus={active:boolean;phase?:string;state:string;error?:string|null};

export type ConversationStatus={active_turn_ids?:string[];human_decision?:'approved'|'rejected'|null;outcome_uncertain?:boolean;state:'unchecked'|'checking'|'working'|'idle'|'blocked'|'unknown'|'needs_permission'|'ready_for_review'|'completed'|'answered'|'disconnected';mode?:string;active:boolean;detail:string;review_active?:boolean;review_phase?:string|null;acceptance_required?:boolean|null;stale_completion?:boolean;interrupted?:boolean;harness?:HarnessAvailability;coding?:CodingPermission;collaboration?:CollabPolicy};
/** The indicator's label for a finished coding result: agents still reviewing, a result the host completed, a completion that is no longer current, or work that needs the human. Never "unknown" for a recorded state. */
export function statusLabel(status:Pick<ConversationStatus,'state'|'review_active'|'acceptance_required'|'stale_completion'|'interrupted'|'human_decision'|'outcome_uncertain'>):string {
  const labels:Record<string,string>={unchecked:t('Checks before sending'),checking:t('Checking'),working:t('Working'),idle:t('Idle'),blocked:t('Needs attention'),unknown:t('Delivery unknown'),needs_permission:t('Needs permission'),ready_for_review:t('Ready for review'),completed:t('Completed'),answered:t('Answered in text'),disconnected:t('Disconnected')};
  if(status.outcome_uncertain)return t('Outcome unknown');
  if(status.state==='idle'&&status.human_decision==='approved')return t('Result approved');
  if((status.state==='blocked'||status.state==='idle')&&status.interrupted)return status.review_active?t('Owner paused, review running'):t('Paused');
  if(status.state==='ready_for_review'){
    if(status.review_active)return t('Independent review running');
    if(status.stale_completion)return t('Completed result no longer current');
    if(status.human_decision==='rejected')return t('Waiting for your feedback');
    if(status.acceptance_required===false)return t('Needs attention');
  }
  return labels[status.state]??t('Status unknown');
}
/** The mode the composer is in: coding when the project permission is granted (naming the agents the policy lets this conversation ask), coordination when the host resolved this conversation's own helper, otherwise the existing chat modes. */
export function composerMode(binding?:Pick<Binding,'provider'>,status?:ConversationStatus):string {
  if(status?.coding?.state==='granted'){
    const collab=status.collaboration;
    const names=(collab?.available??[]).map(a=>'@'+a.name).join(', ');
    const helpers=collab?.state==='automatic'?` · ${t('may ask {names}',{names})}`:collab?.state==='mentions'?` · ${t('ask by name: {names}',{names})}`:'';
    return `${t('Coding in this project')} · ${providerAuthor(binding?.provider)}${helpers}`;
  }
  if(status?.coding?.state==='missing'&&status.coding.reading?.state==='available')return `${t('Reading this project')} · ${providerAuthor(binding?.provider)}`;
  const coordination=status?.harness;
  if(coordination?.state==='available'&&coordination.helper)return `${t('Coordination')} · ${providerAuthor(coordination.provider)} · ${t('text only')}${coordination.can_ask&&coordination.catalogue?.length?` · ${t('may ask linked helpers')}`:''}`;
  // Before the first status read there is nothing to say: "Sending blocked" flashed on every thread open.
  if(!status)return '';
  return status.mode??(binding?.provider==='codex'?t('Chat only'):t('Sending blocked'));
}
/** ``preparing``: the project's first overview is set up but hasn't started (onboarding's last step, or waiting for the
 * plan or beta approval). The bar says Dervo is getting the project ready, never "Idle · Sending blocked", which read as
 * broken on the first screen after onboarding (8 Oct). */
export function ConversationIndicator({binding,status,error,task,guideVisible=false,taskFooterShown=false,preparing=false,onSettings}:{binding?:Pick<Binding,'provider'|'identity'|'model'>;status?:ConversationStatus;error?:boolean;task?:CodingTask;guideVisible?:boolean;taskFooterShown?:boolean;preparing?:boolean;onSettings:(trigger?:HTMLButtonElement)=>void}) {
  if(preparing&&!error&&!status?.active&&!status?.outcome_uncertain)return <div className="px-5 pb-2 text-xs text-muted-foreground" data-testid="conversation-status">
    <div className="py-1"><DervoActivity label={t('Getting your projects ready')} active tone="neutral"/></div></div>;
  const finished=status&&(status.state==='completed'||status.state==='answered'||status.state==='idle'&&status.human_decision==='approved');
  if(finished&&!error&&!status.active&&!status.review_active&&!status.outcome_uncertain&&!status.stale_completion&&!status.interrupted&&(!status.review_phase||status.review_phase==='passed'||status.review_phase==='skipped'&&status.state==='answered')&&(!task||settledTask(task)))return null;
  // With the Task guide open for this task, the guide owns the outcome, its reason and the next step. Two homes for the
  // same sentence was the clutter Bryce saw on 16 September 2026. A status error still shows: the guide is unavailable then.
  if(guideVisible&&task&&!error)return null;
  // A running task says Working beside its own Stop, and its review line sits under it. The bar would say it a second time.
  if(taskFooterShown&&task&&!error&&!status?.outcome_uncertain&&(task.state==='working'||REVIEW_OPEN.has(task.collaboration?.review?.phase??'')))return null;
  // A settled task whose decision bar sits under the report ("Ready for review. No checks ran in Dervo.") already says
  // the outcome. With Preview in the second column the guide isn't visible, and the bar said it a second time with the
  // raw model id (fresh-eyes, 20 Sep). Failures and unconfirmed runs keep this line: their detail can have no other home.
  if(taskFooterShown&&task&&!error&&!status?.outcome_uncertain&&DECIDED.has(task.state)&&decisionLine(task))return null;
  // Before anything is sent there's nothing to report; the account and model are already under the composer.
  if(!error&&status?.state==='unchecked'&&!status.active)return null;
  const repeated=!!task?.error&&status?.detail===String(task.error);
  const coordination=status?.harness;
  const coding=status?.coding?.state==='granted';
  const reading=status?.coding?.state==='missing'&&status.coding.reading?.state==='available';
  const configuredCoordination=!coding&&!reading&&coordination?.state==='available'&&!!coordination.helper&&status?.state==='blocked'&&!status.active&&!status.outcome_uncertain;
  const observed=!!status?.active&&!status.outcome_uncertain&&!error&&!['unknown','disconnected','blocked'].includes(status.state);
  const reviewing=!error&&!status?.outcome_uncertain&&!['unknown','disconnected'].includes(status?.state??'')&&!!status?.review_active;
  const working=observed&&status?.state==='working'&&!reviewing;
  const active=working||(observed&&reviewing&&status?.review_phase!=='pending')||(observed&&status?.state==='checking');
  const label=error?t('Status unavailable'):configuredCoordination?t('Coordination configured'):reviewing?status?.interrupted?t('Owner stopped, review running'):status?.review_phase==='pending'?t('Waiting for independent review'):status?.review_phase==='correction'?t('Correcting review findings'):t('Independent review running'):working?t('{who} is working',{who:providerAuthor(binding?.provider)}):status?statusLabel(status):t('Checking status');
  const tone:ActivityTone=error||status?.state==='blocked'&&!status?.interrupted?'error':status?.outcome_uncertain||['unknown','disconnected','needs_permission','ready_for_review'].includes(status?.state??'')?'attention':status?.interrupted?'cancelled':['completed','answered'].includes(status?.state??'')||status?.human_decision==='approved'&&status.state==='idle'?'completed':'neutral';
  const secondary=working&&task?.binding.provider===binding?.provider&&task?.binding.identity===binding?.identity&&task?.binding.model===binding?.model?latestObservableActivity(task):undefined;
  const detail=error?t('The local host could not report this conversation status.'):reading?status?.coding?.reading?.detail:coding?t('Coding can use this project’s granted files and checks. Each request verifies its account, model and permissions.'):coordination?.state==='available'?coordination.detail:status?.detail;
  return <div className="px-5 pb-2 text-xs text-muted-foreground" data-testid="conversation-status">
    <button onClick={event=>onSettings(event.currentTarget)} className="dervo-conversation-status-button rounded-md py-1 text-left transition-colors motion-reduce:transition-none hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring" title={`${detail??''}${binding?` ${t('Expected account: {identity}.',{identity:binding.identity})}`:''}`}>
      <DervoActivity label={label} active={active} tone={active||reviewing||configuredCoordination?'neutral':tone} secondary={secondary}/>
      <span className="dervo-conversation-status-meta"><span>{binding?`${providerAuthor(binding.provider)} · ${binding.model}`:t('Loading…')}</span><span>{coding?t('Project coding'):composerMode(binding,status)}</span></span>
    </button>
    {(error||(status?.state==='blocked'&&!configuredCoordination)||status?.state==='unknown'||status?.state==='disconnected'||status?.outcome_uncertain)&&!repeated&&<p>{error?t('Status could not be checked. No automatic retry will send messages.'):status?.detail}</p>}
  </div>;
}

/** Saved queues need an explicit selection. Status polling never sends them. */
export function TurnControls({row,projectId,channelId,binding,refresh,proposal,onAdjustScope}:{row:LocalEvent;projectId:string;channelId:string;binding?:Binding;refresh:()=>Promise<unknown>;proposal?:Proposal;onAdjustScope?:()=>void}) {
  const [confirm,setConfirm]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const scope={projectId,channelId,messageId:row.id};
  async function propose() {
    setBusy(true);setError('');
    try {await local('task_propose',{projectId,channelId,text:row.content,messageId:row.id});await refresh();}
    catch(e){setError(String(e));}
    finally{setBusy(false);}
  }
  const status=useQuery({queryKey:['local-turn',projectId,channelId,row.id,row.state],
    queryFn:()=>local<TurnStatus>('turn_status',scope),
    refetchInterval:q=>q.state.data?.active||['queued','sending','running','stopping'].includes(row.state)?300:false});
  async function action(operation:string) {
    setBusy(true);setError('');
    try {await local(operation,scope);setConfirm(false);await status.refetch();await refresh();}
    catch(e){setError(String(e));}
    finally{setBusy(false);}
  }
  const active=status.data?.active??false;
  const words:Record<string,string>={queued:t('Queued locally'),sending:t('Sending'),running:t('Sending'),stopping:t('Stopping'),cancelled:t('Cancelled'),uncertain:t('Delivery not confirmed')};
  const state=active&&row.state==='queued'?t('Checking connection'):words[row.state];
  // A delivered message needs no line under it. Never print a raw state.
  if(!state&&!active&&!proposal&&!error&&!status.data?.error)return null;
  return <div className="ml-[58px] text-2xs text-muted-foreground/70">
    <div className="flex items-center gap-1">{state&&<span data-testid="queue-state">{state}</span>}
      {row.state==='queued'&&!active&&<DropdownMenu><DropdownMenuTrigger asChild><button aria-label={t('Queued message options')} className="rounded p-1 hover:bg-muted"><MoreVertical className="size-3"/></button></DropdownMenuTrigger><DropdownMenuContent>
        {binding&&!proposal&&<DropdownMenuItem onSelect={()=>void propose()}>{t('Propose coding scope')}</DropdownMenuItem>}
        {binding?.provider==='codex'&&<DropdownMenuItem onSelect={()=>setConfirm(true)}>{t('Send to Codex')}</DropdownMenuItem>}
        <DropdownMenuItem onSelect={()=>void action('cancel')}>{t('Cancel queued message')}</DropdownMenuItem>
      </DropdownMenuContent></DropdownMenu>}
      {active&&<button disabled={busy} onClick={()=>void action('stop_turn')} className="rounded px-2 hover:bg-muted">{t('Stop')}</button>}
      {row.state==='uncertain'&&!active&&<button disabled={busy} onClick={()=>void action('reconcile_turn')} className="rounded px-2 hover:bg-muted">{t('Check delivery')}</button>}
    </div>
    {(error||status.data?.error)&&<p role="status" className="max-w-xl text-destructive">{error||status.data?.error}</p>}
    {proposal&&<ScopeProposalCard proposal={proposal} projectId={projectId} channelId={channelId} refresh={refresh} onAdjust={()=>onAdjustScope?.()}/>}
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>{t('Send this message to Codex?')}</DialogTitle><DialogDescription>{t('Only this queued message will be sent. Sign-in and model checks don\'t send anything else.')}</DialogDescription></DialogHeader>
      <dl className="space-y-2 text-sm"><div><dt>{t('Personal account')}</dt><dd>{binding?.identity}</dd></div><div><dt>{t('Model')}</dt><dd>{binding?.model}</dd></div><div><dt>{t('Project')}</dt><dd className="break-all">{binding?.cwd}</dd></div></dl>
      <p className="text-sm">{t('Read-only does not confine reads to this folder. Shell, browser, apps, MCP and hooks are disabled. Permission requests are denied. This mode can\'t edit your project.')}</p>
      <p className="max-h-32 overflow-auto whitespace-pre-wrap text-sm text-muted-foreground">{row.content}</p>
      <Button disabled={busy} onClick={()=>void action('start_turn')}>{t('Send this message')}</Button>
    </DialogContent></Dialog>
  </div>;
}
