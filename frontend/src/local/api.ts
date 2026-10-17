import { invoke } from '@tauri-apps/api/core';
import type { RelayEvent } from '@/shared/api/types';
import { hostFailure } from '@/i18n/hostMessages';
/** A host request. A refusal comes back as the host's English; hostFailure shows a known one in the window's language. */
export const local = <T,>(operation: string, args: Record<string,unknown> = {}) => invoke<T>('local_request',{operation,args}).catch((failure:unknown)=>{throw hostFailure(failure);});
/** What the host's activity index says about one conversation: the most urgent status across its coding tasks, whether one
 * is running now with its current step, and how many settled results you haven't opened. Absent when the store has no index yet. */
export type ConversationActivity={status:'needs'|'running'|null;running:boolean;step:string|null;unread:number;since?:number|null;goal?:string|null;why?:string|null;next?:string|null;request?:string|null;summary?:string|null;at?:number|null;ended?:string|null;helpers?:{who:string;state:string}[];attention?:boolean;
 /** A question from the thread's newest task that waits on you, even while its agent keeps working. */
 pending_question?:string|null;pending_question_count?:number};
/** `origin` is set on the two threads the host makes with a project ('starter' from project setup, 'overview' for
 * "Project overview") and null on every thread you started. It never changes, so a rename can't lose it. Absent from
 * a host older than 20 Sep. Only the blank-project check reads it; the sidebar shows these threads like any other. */
export type Conversation = {id:string;name:string;visibility:'shared'|'private';project_id:string;profile_id:string;model:string;origin?:'starter'|'overview'|null;organisationGroup?:string|null;activity?:ConversationActivity|null;
  /** Beta.17: the thread that proposed this one when it's a subthread and that thread is listed too (else null), and how many notes or reviews wait for you here (thread_notes). */
  parentId?:string|null;notesWaiting?:number};
/** Record that you have looked at this conversation now. Unread counts are derived from saved task changes after that time. */
/** Clear "Needs you" and unread results for a conversation until something new happens there. Its tasks stay as they are. */
export function conversationDismiss(projectId:string,channelId:string):Promise<{id:string}> {return local<{id:string}>('conversation_dismiss',{projectId,channelId});}
export function conversationSeen(projectId:string,channelId:string):Promise<{id:string}> {return local<{id:string}>('conversation_seen',{projectId,channelId});}
/** One account's usage, entirely from what its provider reported. ``percent`` is null when nothing honest fills it: a Claude
 * subscription has no allowance figure, so its bar shows runs instead. ``basis`` says which of the three a row is. */
/** ``name`` is the name you gave the account on Connected accounts, when the host sends one. */
export type UsageWindow={percent:number;resets_at:number|null;window_mins:number|null};
export type UsageAccount={windows?:UsageWindow[];profile_id:string;provider:string;identity:string;method:string;name?:string|null;monthly_limit_usd:number|null;runs_today:number;tokens_today:number;cost_today_usd:number|null;cost_month_usd:number|null;runs_month:number;resets_at:number|null;window_mins?:number|null;percent:number|null;low:boolean;basis:'rate_limits'|'spend_limit'|'runs';rate_limits?:{planType?:string}|null};
export type UsageSummary={at:number;accounts:UsageAccount[]};
export function usageSummary():Promise<UsageSummary> {return local<UsageSummary>('usage_summary');}
/** What happens after a provider failure in this project: Dervo suggests your other account (the default) or switches by itself. */
export type RoutePolicy={auto_switch:boolean;source:'explicit'|'default';detail:string};
/** A conversation's own choice: Auto, or always its own account. ``alternative`` is your account on the other provider Auto would use. */
export type ConversationRoute={mode:'auto'|'fixed';alternative:{profile_id:string;model:string;provider:string;identity:string;method:string;label:string}|null};
export function setConversationRoute(projectId:string,channelId:string,mode:'auto'|'fixed'):Promise<CodingPermission> {return local<CodingPermission>('conversation_route_set',{projectId,channelId,mode});}
/** Send a provider-failed request again on your account on the other provider. The host moves the conversation and starts a new task. */
/** D5: send a request that stopped because its account ran out again once that account's reported usage window resets. */
export function waitForReset(projectId:string,channelId:string,taskId:string):Promise<QueuedRequest> {return local<QueuedRequest>('coding_wait_for_reset',{projectId,channelId,taskId});}
export function retryOnOther(projectId:string,channelId:string,taskId:string):Promise<CodingRequestResult> {return local<CodingRequestResult>('coding_retry_on',{projectId,channelId,taskId});}
export function setRoutePolicy(projectId:string,channelId:string,autoSwitch:boolean):Promise<RoutePolicy> {return local<RoutePolicy>('coding_route_set',{projectId,channelId,autoSwitch});}

export type LocalEvent = RelayEvent & {state:string};
const bindings = new Map<string,string>();
export function registerConversations(rows:Conversation[]) { for(const row of rows) bindings.set(row.id,row.project_id); }
export function scoped(channelId:string) {
  const projectId=bindings.get(channelId);
  if(!projectId) throw new Error('Load this project conversation before sending.');
  return {channelId,projectId};
}
export async function queueLocal(channelId:string,text:string,parentId:string|null=null):Promise<RelayEvent> {
  const scope=scoped(channelId);
  const key=`dervo-pending:${channelId}:${parentId??'root'}`;
  const saved=localStorage.getItem(key);
  const old=saved ? JSON.parse(saved) : null;
  if(old && old.text!==text) throw new Error('An earlier save has an unknown outcome. Restore that draft before sending different text.');
  const request=old??{clientId:crypto.randomUUID(),text,parentId,...scope};
  localStorage.setItem(key,JSON.stringify(request));
  const result=await local<{id:string;state?:string}>('queue',request);
  const turnScope={...scope,messageId:result.id};
  type Turn={active:boolean;state:string;error?:string|null};
  let turn=await local<Turn>('turn_status',turnScope);
  if(turn.state==='queued'&&!turn.active) turn=await local<Turn>('start_turn',turnScope);
  // Preflight and a transport write aren't provider acknowledgement.
  while(turn.active&&['queued','sending'].includes(turn.state)) {
    await new Promise(resolve=>setTimeout(resolve,100));
    turn=await local<Turn>('turn_status',turnScope);
  }
  if(!['running','completed'].includes(turn.state)) throw new Error(turn.error??`Delivery is ${turn.state}. Your draft and request ID are retained. Check the saved message before sending again.`);
  const rows=await local<LocalEvent[]>('history',scope);
  const event=rows.find(row=>row.id===result.id);
  if(!event) throw new Error('Saved message is not in the scoped history. Keep the draft and reload.');
  if(!['running','completed'].includes(event.state)) throw new Error(`Delivery is ${event.state}. Your draft and request ID are retained.`);
  localStorage.removeItem(key);
  return event;
}
/** Coordination through the packaged harness: one named helper, text only, in this conversation's own account. */
export type HarnessCatalogueEntry={handle:string;provider:string;model:string;can_ask:boolean};
export type HarnessAvailability={state:'available'|'unavailable';helper:string|null;provider?:string|null;model?:string|null;account?:string;detail:string;can_ask?:boolean;catalogue?:HarnessCatalogueEntry[]};
export type HarnessBoundary={id:string;profiles:string[];created_at:number};
export type HarnessStatus={state:string;detail:string;helpers:string[];library:string;details:Record<string,{provider:string;model:string;account:string;fake:boolean;can_ask?:boolean}>;excluded:{profile:string;model:string;reason:string}[];boundaries:HarnessBoundary[];actions?:string[]};
export type HarnessDelegation={id:string;handle:string;status:string;purpose:string;result:{summary:string}|null;error:string|null;returned_at?:string|null};
export type HarnessActivity={seq:number;kind:string;text:string};
export type HarnessRequest={knowledge?:{count:number;omitted:number}|null;id:string;state:string;mode:string;helper:string;provider?:string|null;text:string;mentions?:string[];linked_return:{summary:string;refs:string[];worker:string;worker_is_fake:boolean;result_message_id:string;returned_at:string}|null;delegations:HarnessDelegation[];activity?:HarnessActivity[];error:string|null;created_at:number;detail:string};
export type MentionResolution={resolved:boolean;mention?:string;handle?:string;reason?:string;asker_can_ask?:boolean;note?:string;provider?:string};
/** The @handles in a message, in order, once each, lower-cased. Same token rule as the harness: not inside an email, path or another mention. */
export function mentionsIn(text:string):string[] {
  const out:string[]=[];
  for(const match of text.matchAll(/(?<![\w.@/])@([A-Za-z0-9][A-Za-z0-9-]{0,31})(?![\w@-])/g)){const handle='@'+match[1].toLowerCase();if(!out.includes(handle))out.push(handle);}
  return out;
}
export async function coordinate(channelId:string,text:string,helper:string):Promise<HarnessRequest> {
  const scope=scoped(channelId);
  // Every @handle is resolved by the host before anything is sent: an unresolvable one keeps the draft and says why.
  const mentions:string[]=[];
  for(const mention of mentionsIn(text)){
    const answer=await local<MentionResolution>('harness_resolve',{...scope,mention});
    if(!answer.resolved) throw new Error(`${answer.mention??mention} cannot be asked: ${answer.reason??'not resolvable'}`);
    if(!answer.asker_can_ask) throw new Error(answer.note??'This conversation\'s helper cannot ask linked helpers.');
    mentions.push(answer.handle??mention);
  }
  const key=`dervo-coordination-pending:${channelId}`;
  const saved=localStorage.getItem(key);
  const old=saved?JSON.parse(saved):null;
  if(old&&(old.text!==text||old.helper!==helper)) throw new Error('An earlier coordination request has an unknown outcome. Restore that draft before sending different text.');
  const request=old??{clientId:crypto.randomUUID(),text,helper,mode:'coordinate',mentions,...scope};
  localStorage.setItem(key,JSON.stringify(request));
  const result=await local<HarnessRequest&{id:string|null}>('harness_request',request);
  // unavailable and unsupported answers carry no id: nothing was recorded, the draft and request ID stay for a retry.
  if(result.id===null) throw new Error(result.detail);
  localStorage.removeItem(key);
  return result;
}
/** Coding from the composer. One explicit project permission (existing files plus the root unittest file), then a message is a coding task. */
/** ``owner_effort`` is the effort configured for exactly this provider and model (null: legacy, the provider default, no key sent). */
/** The optional human result approval policy of the project. Separate from the coding grant and from Independent review: it never authorises file access, destructive actions, deployment or spending. */
export type ResultApproval={required:boolean;source:'default'|'legacy'|'explicit';detail:string;actor?:string|null;created_at?:number|null};
/** ``folders`` are the explicit granted folders (relative, existing, never the root) coding may list, search, read and edit regular files in, and create new files inside; ``creation_detail`` states that authority and its limits in words. Absent or empty means file-only, unchanged. */
/** A host check profile: what one confined verification executes. ``available:false`` entries are shown but cannot be chosen (the host refuses them with ``detail``). */
export type CheckProfile={id:string;label:string;test_pattern:string;available:boolean;detail:string};
export const PYTHON_CHECK='python-unittest';
/** Root test file names each profile accepts; mirrors the host rule, which decides. */
export const CHECK_TEST_NAMES:Record<string,RegExp>={'python-unittest':/^test_[A-Za-z0-9_]+\.py$/,'node-test':/^(test_[A-Za-z0-9_]+|[A-Za-z0-9_-]+\.test)\.(mjs|cjs|js)$/,'typescript-check':/^tsconfig\.json$/,'rust-check':/^[A-Za-z_][A-Za-z0-9_]*\.rs$/,'rust-test':/^[A-Za-z_][A-Za-z0-9_]*\.rs$/};
/** ``check_profile`` is the one check selected at grant time (Python unittest for a grant recorded before profiles existed); ``check_profiles`` is the host catalogue; ``check_detail`` states exactly what the selected check may execute. */
/** One automated check a grant allows: the profile, its root check file and, for Rust, the toolchain. Primary first. */
export type CheckEntry={profile:string;test_file:string|null;rust_toolchain?:string|null;label:string};
/** A project's run limits (backend/run_limits.py, beta.17.1): seconds of silence that end a run, the longest a run may
 * last (null: no cap) and attempts per task (null: no limit). ``choices`` are the only values the host takes. */
export type RunLimits={idle_seconds:number;run_seconds:number|null;attempts:number|null;warning?:string|null;
 defaults?:{idle_seconds:number;run_seconds:number|null;attempts:number|null};choices?:{idle_seconds:number[];run_seconds:(number|null)[];attempts:(number|null)[]}};
/** A check the project's own files suggest, or one you saved, by name (project_checks). The window sends names only. */
export type RunCheck={name:string;argv:string[];cwd:string;timeout:number};
export type CodingPermission={run_checks?:{commands:RunCheck[];suggested:RunCheck[];offer?:boolean};run_limits?:RunLimits;unusable_files?:string[];unusable_folders?:string[];checks?:CheckEntry[];project_access?:boolean;project_access_available?:boolean;reading?:{state:'available'|'unavailable';detail:string};state:'granted'|'missing'|'stale'|'unsupported';detail:string;files:string[];folders?:string[];creation_detail?:string;folder_risk?:string;test_file:string|null;check_timeout_seconds?:number;check_profile?:string|null;rust_toolchain?:string|null;check_profiles?:CheckProfile[];check_detail?:string;provider?:string;candidates?:string[];test_candidates?:string[];created_at?:number|null;actor?:string|null;owner_effort?:string|null;owner_effort_detail?:string;acceptance?:ResultApproval;route?:RoutePolicy;conversation_route?:ConversationRoute;full_access?:import('./FullAccess').FullAccessView;continuity?:import('./SessionContinuity').ContinuityView;memory?:import('./ProjectMemory').ProjectMemoryView};
/** The grant payload: files and the test file always; ``folders`` only when at least one explicit folder is named and ``checkProfile`` only when it is not the Python default, so an older grant keeps its older shape. */
export type ExtraCheck={checkProfile:string;testFile:string;rustToolchain?:string};
export function grantPayload(files:string[],testFile:string,folders:string[],checkProfile:string=PYTHON_CHECK,rustToolchain:string='',checkTimeoutSeconds?:number,projectAccess:boolean=false,extraChecks:ExtraCheck[]=[]):{projectAccess?:boolean;files:string[];testFile:string|null;folders?:string[];checkProfile?:string;rustToolchain?:string;checkTimeoutSeconds?:number;checks?:ExtraCheck[]} {
  const named=[...new Set(folders.map(f=>f.trim().replace(/\/+$/,'')).filter(Boolean))];
  const manual=checkProfile==='manual-review';
  const checks=manual?[]:extraChecks.filter(c=>c.checkProfile&&c.testFile).map(c=>({checkProfile:c.checkProfile,testFile:c.testFile,...(['rust-check','rust-test'].includes(c.checkProfile)&&c.rustToolchain?.trim()?{rustToolchain:c.rustToolchain.trim()}:{})}));
  const checkFiles=[...new Set([testFile,...checks.map(c=>c.testFile)])];
  return {...(checks.length?{checks}:{}),files:projectAccess?(manual?[]:checkFiles):files,testFile:manual?null:testFile,...(projectAccess?{projectAccess:true}:{}),...(checkTimeoutSeconds!==undefined?{checkTimeoutSeconds}:{}),...(!projectAccess&&named.length?{folders:named}:{}),...(checkProfile&&checkProfile!==PYTHON_CHECK?{checkProfile}:{}),...(['rust-check','rust-test'].includes(checkProfile??'')&&rustToolchain.trim()?{rustToolchain:rustToolchain.trim()}:{})};
}
/** Save the project's result approval policy. Pending tasks and existing approval records are never changed by this. */
export function setResultApproval(projectId:string,channelId:string,required:boolean):Promise<CodingPermission> {return local<CodingPermission>('coding_acceptance_set',{projectId,channelId,required});}
export type CodingRequestResult={id:string|null;taskId:string|null;task_id?:string|null;state:string;detail:string;error?:string|null;client_id?:string;text?:string;created_at?:number;active?:ActiveRun;queued?:boolean;position?:number|null};
/** A message sent while this conversation's own coding task was running. It waits in the host's queue and starts when that
 * run ends; ``failed`` means it couldn't start and ``detail`` says why. Nothing was created or sent for it. */
/** ``until``: a request waiting for its account's usage window to reset (D5) starts no earlier than this, in seconds. */
export type QueuedRequest={id:string;client_id:string;state:'queued'|'failed'|string;text:string;images:string[];checkProfile?:string|null;review?:CollabReviewSetting|null;position:number|null;error:string|null;detail:string;created_at:number;until?:number|null;why?:string|null;
 /** Kept across a restart and waiting until the runs Dervo lost in this project are settled (coding_queue.view). */
 held?:boolean};
/** The one coding run open across the app, named by the host with the ids Open and Stop need. ``name`` is null when the run
 * is in someone else's private conversation. */
export type ActiveRun={task_id:string;thread_id:string;project_id:string;origin:string|null;agent?:string|null;name:string|null};
/** A coding request refused because another run holds the one coding slot. The draft is kept; nothing was recorded. */
export class CodingBusyError extends Error{active:ActiveRun;constructor(message:string,active:ActiveRun){super(message);this.name='CodingBusyError';this.active=active;}}
/** The optional Independent review setting: off unless enabled with an explicit reviewer agent and a requested effort. */
export type CollabReviewSetting={enabled:boolean;reviewer:string|null;effort:string|null};
/** ``skipped_here``: why the project's default review won't run for this conversation's account, or null. */
export type CollabReview=CollabReviewSetting&{provider:string|null;model:string|null;notice:string;skipped_here?:string|null};
/** Documented Claude Code CLI --effort levels (code.claude.com/docs/en/cli-reference). A Codex effort is whatever its model catalog advertises; the host checks that before sending. */
export const CLAUDE_EFFORTS=['low','medium','high','xhigh','max'] as const;
/** ``review`` is sent only as an explicit per-request override; without it the project setting applies and the request keeps its older shape. */
export async function codingRequest(channelId:string,text:string,review?:CollabReviewSetting,images:string[]=[],checkProfile?:string):Promise<CodingRequestResult> {
  const scope=scoped(channelId);
  const key=`dervo-coding-pending:${channelId}`;
  const saved=localStorage.getItem(key);
  const old=saved?JSON.parse(saved):null;
  if(old&&(old.text!==text||JSON.stringify(old.images??[])!==JSON.stringify(images))) throw new Error('An earlier coding request has an unknown outcome. Restore that draft before sending different text.');
  const request=old??{clientId:crypto.randomUUID(),text,...scope,...(review?{review}:{}),...(images.length?{images}:{}),...(checkProfile?{checkProfile}:{})};
  localStorage.setItem(key,JSON.stringify(request));
  const result=await local<CodingRequestResult>('coding_request',request);
  // No id means nothing was recorded (permission missing or stale): the draft stays and the request id is not needed again.
  if(result.id===null){localStorage.removeItem(key);if(result.state==='busy_elsewhere'&&result.active)throw new CodingBusyError(result.detail,result.active);throw new Error(result.detail);}
  localStorage.removeItem(key);
  return result;
}
/** What the host found when it inspected an interrupted creation. ``dropped_absent_creation`` settles a phantom intent (the
 * target never reached disk); ``inspect`` leaves a present target untouched and uncertain; ``none`` and ``wait`` change nothing. */
export type ReconcileReport={action:'none'|'wait'|'inspect'|'dropped_absent_creation';detail:string;attempt:number|null;path?:string};
/** Host-computed availability of the one Reconcile action: only an interrupted creation with an uncertain outcome, and only
 * once the provider turn, tool deliveries, host checks and any independent review have ended. ``reason`` explains a disabled action. */
export type ReconcileStatus={kind?:string;available:boolean;attempt:number|null;path:string|null;reason:string};
/** Ask the host to inspect the current files for an interrupted creation. It never retries, replays or overwrites anything. */
export function reconcileTask(projectId:string,channelId:string,taskId:string):Promise<ReconcileReport> {return local<ReconcileReport>('task_reconcile',{projectId,channelId,taskId});}
/** Coding collaboration: persistent named agents, one project policy, and the packet records of help the owner's coding model asked for. */
export type CollabAgent={name:string;profile_id:string;provider:string;model:string;project_id:string;channel_id:string;role:'reviewer'|'worker';tools:string[];instructions:string;created_at:number;actor:string;state:'ready'|'unusable';detail:string};
export type CollabLimits={max_parallel:number;max_depth:number;max_calls:number};
export type CollabPolicy={state:'off'|'private'|'channel_not_allowed'|'unavailable'|'mentions'|'automatic';detail:string;mode:'off'|'mentions'|'automatic';agents:string[];channels:string[];limits:CollabLimits;available:CollabAgent[];unavailable:{name:string;reason:string}[];review?:CollabReview};
/** The Independent review record on an owner task: bound at request time, driven by the host through its phases, never by the renderer. */
export type TaskReview={enabled:true;reviewer:string;effort:string;provider:string;model:string;profile_id:string;channel_id:string;source:'policy'|'request';phase:'pending'|'initial'|'correction'|'final'|'passed'|'unresolved'|'stale'|'failed'|'interrupted'|'skipped';packets:string[];findings:string|null;unresolved:string|null;snapshot:Record<string,string>|null;error:string|null};
export type PacketEnd={project_id:string;thread_id:string;profile_id:string;model:string;provider:string};
export type CollabResult={child_state:string;summary:string|null;attempt:number|null;verified:boolean;test:{exit_status:number|null;stopped:string|null;stderr:string}|null;diff:string|null;after:Record<string,string>|null;stale:boolean;changed_since:string[];kind?:string;state?:string};
export type CollabPacket={id:string;owner_task_id:string;root_task_id:string;parent_packet_id:string|null;depth:number;agent:string;phase?:'initial'|'final'|null;role:string|null;kind:string|null;source:PacketEnd&{task_id:string;run_id:string;token:string};destination:(PacketEnd&{agent:string})|null;reason:string;instructions:string;files:{path:string;sha256:string|null}[];payload_text:string|null;payload_sha256:string|null;child_task_id:string|null;status:'queued'|'running'|'returned'|'failed'|'refused'|'withdrawn'|'disconnected';refusal:string|null;result:CollabResult|null;late_result:CollabResult|null;error:string|null;created_at:number;queued_at:number|null;started_at:number|null;returned_at:number|null;stale:boolean;changed_since:string[]};
export type CollabPacketDetail=CollabPacket&{snapshots:Record<string,{sha256:string;bytes_b64:string}>};
export type AgentConversation={id:string;name:string;visibility:'shared';project_id:string;profile_id:string;provider:string;model:string};
/** One atomic step: a shared conversation of the project bound to an explicit owned personal account and model. The project binding, existing conversations and credentials are untouched. */
export function createAgentConversation(projectId:string,profileId:string,model:string,name:string):Promise<AgentConversation> {return local<AgentConversation>('create_agent_conversation',{projectId,profileId,model,name});}
export async function localBuzzInvoke<T>(command:string,args:Record<string,unknown>={}):Promise<T> {
  if(command==='set_window_vibrancy') return invoke<T>(command,args);
  if(command==='get_identity') {
    const status=await local<{actor:string}>('status');
    return {pubkey:status.actor,display_name:'You (local)'} as T;
  }
  if(command==='get_channel_window') return local<T>('history',scoped(String(args.channelId)));
  // Unsupported Buzz services are errors, never successful empty relay responses.
  throw new Error(`${command} isn't available in this local workspace. No relay or provider is connected.`);
}

/** Saved-record operations only. The host enforces the current account and visibility again on open. */
export const WORKSPACE_OPERATIONS = ['workspace_search','workspace_source','project_catchup'] as const;
export type WorkspaceScope={projectId:string;channelId:string;profileId:string;scope:'conversation'|'project'};
export type WorkspaceSource={kind:'message'|'task'|'coordination';id:string;channelId:string;revision:string;offset:number};
/** Task rows carry facts from the saved record only: when it was asked, answered and completed, what changed, the check
 * result, the review phase, the run state and the tool under way. ``summary`` is one sentence the host composed from them.
 * ``isNew`` compares the record's activity order with your last Catch up visit. */
export type WorkspaceRow={/** How a blocked or lost run ended (backend/run_ending.py, beta.17.1); only 'failed' is a failure. */ending?:'failed'|'finished'|'unknown'|'stopped'|'limit'|'not_started'|null;id:string;kind:'message'|'task'|'coordination';channelId:string;channelName:string;snippet:string;source:WorkspaceSource;title?:string;status?:string;owner?:string;blocker?:string;summary?:string;hasAnswer?:boolean;helper?:boolean;humanApprovalRequired?:boolean;humanDecision?:'approved'|'rejected'|null;outcomeUncertain?:boolean;reviewPending?:boolean;stale?:boolean;matchRole?:'user'|'assistant';isNew?:boolean;askedAt?:number|null;answeredAt?:number|null;completedAt?:number|null;changedFiles?:string[];changedFileCount?:number;testExitStatus?:number|null;checkProfile?:string|null;reviewPhase?:string|null;runState?:string|null;lastTool?:string|null;lastPath?:string|null};
export type WorkspacePage={results:WorkspaceRow[];cursor:string|null;incomplete:boolean;indexing?:boolean;since?:number|null;lessonsPending?:number|null;skillsPending?:number|null};
export type WorkspaceExcerpt=WorkspaceRow&{text:string;truncated:boolean;excerptStart:number};
export function workspaceRequest<T>(operation:typeof WORKSPACE_OPERATIONS[number],args:Record<string,unknown>):Promise<T>{
  if(!WORKSPACE_OPERATIONS.includes(operation))return Promise.reject(new Error('Unsupported saved-record operation'));
  return local<T>(operation,args);
}

export const ORGANISATION_OPERATIONS=['organisation_get','organisation_set','organisation_undo'] as const;
export type OrganisationState={name:string;group:string|null;nameLocked:boolean;groupLocked:boolean;revision:number;canUndo:boolean;groupAllowed?:boolean;reason:string;source:{kind:string;id?:string}|null;suggestion:{name:string|null;group:string|null;nameApplied?:boolean;groupApplied?:boolean;applied?:boolean}|null;peers:{id:string;name:string}[]};
export function organisationRequest<T>(operation:typeof ORGANISATION_OPERATIONS[number],args:Record<string,unknown>):Promise<T>{
  if(!ORGANISATION_OPERATIONS.includes(operation))return Promise.reject(new Error('Unsupported organisation operation'));
  return local<T>(operation,args);
}

/** One human-selected recorded edit. Content and actor come from Host evidence, never the renderer. */
export type FileRestoration={attempt:number;path:string;available:boolean;reason:string|null;scope_version:number;expected_hash:string;expected_attempts:number};
export const FILE_RECOVERY_OPERATIONS=['task_restore_file'] as const;
export function fileRecoveryRequest<T>(operation:string,args:Record<string,unknown>):Promise<T> {
 if(!FILE_RECOVERY_OPERATIONS.some(name=>name===operation))return Promise.reject(new Error('Unsupported file recovery operation'));
 return local<T>(operation,args);
}
export function restoreRecordedFile(projectId:string,channelId:string,taskId:string,selection:FileRestoration):Promise<{state:string;number:number}> {
 return fileRecoveryRequest('task_restore_file',{projectId,channelId,taskId,attemptNumber:selection.attempt,scopeVersion:selection.scope_version,path:selection.path,expectedHash:selection.expected_hash,expectedAttempts:selection.expected_attempts});
}

/** Dervo identity is separate from provider profiles. No credentials enter renderer state. */
export type DervoAccountStatus={photo?:{state:'not_loaded'|'ready'|'missing'|'error'|'cached';data_url:string|null;checked?:boolean;error?:string}|null;state:'unavailable'|'signed_out'|'awaiting_user'|'verifying'|'signed_in'|'expired'|'cancelled'|'unknown'|'interrupted'|'restore_required'|'account_mismatch'|'failed';detail:string;identity:{issuer:string;subject:string;display_name:string}|null;operation_id:string|null;expires_at:number|null;can_start:boolean;can_cancel:boolean;can_poll:boolean;can_restore:boolean;can_sign_out:boolean;ownership:'local_unclaimed';sync_available:boolean;method?:'email_code';can_verify?:boolean;can_save_profile?:boolean;membership?:{status:'pending'|'approved'|'revoked';allowed:boolean;expires_at:string|null;release_channel:'private-beta';is_admin:boolean}|null;access_valid_until?:number|null;plan?:DervoPlan|null;billing?:{state:'idle'|'starting'|'opening'|'opened'|'error';action:'trial'|'checkout'|'manage'|null;detail:string|null}|null;can_start_trial?:boolean;can_buy?:boolean;can_manage_billing?:boolean};
/** dervo_plan() as the host checked it (backend/supabase_client.py _plan_document). Times are epoch seconds. */
export type DervoPlan={plan:'beta'|'trial'|'paid'|'none'|'removed';environment:'sandbox'|'live';trial:{days:number;mode:'card'|'app';ends_at:number|null;available:boolean};subscription:{status:'active'|'trialing'|'past_due'|'paused'|'canceled';interval:'month'|'year';period_ends_at:number|null;cancel_at:number|null;access_until:number|null}|null;can_buy:boolean;can_manage:boolean};

export type BetaFeedbackCategory = 'feedback' | 'bug' | 'confusing' | 'idea';
export type BetaFeedbackContext = {
  route: 'conversation' | 'catch-up' | 'search' | 'knowledge' | 'settings' | 'welcome' | 'workspace';
  projectId?: string; taskId?: string; conversationId?: string; sessionId?: string;
  provider?: 'codex' | 'claude' | 'openrouter'; appVersion?: string;
  viewport?: {width: number; height: number};
};
export type BetaFeedbackRequest = {contextId: string; requestId: string; category: BetaFeedbackCategory; message: string; context: BetaFeedbackContext};
export type BetaFeedbackResult = {requestId: string; state: 'sending' | 'sent' | 'error'; receipt: {id: string; created_at: string} | null; detail: string | null};
export const betaFeedbackContext = () => local<{contextId: string; ownerKey: string; appVersion?: string | null}>('beta_feedback_context');
export async function sendBetaFeedback(request: BetaFeedbackRequest): Promise<BetaFeedbackResult> {
  let result = await local<BetaFeedbackResult>('beta_feedback_send', request);
  while (result.state === 'sending') {
    await new Promise(resolve => setTimeout(resolve, 350));
    result = await local<BetaFeedbackResult>('beta_feedback_status', {requestId: request.requestId, contextId: request.contextId});
  }
  if (result.state !== 'sent' || result.receipt?.id !== request.requestId) {
    // A refusal the host explains (the hourly feedback limit) carries its line as ``detail``, for BetaFeedback to show.
    throw Object.assign(new Error('Feedback wasn’t confirmed. Your draft is kept. Check your account and connection, then retry.'), {detail: result.state === 'error' ? result.detail : null});
  }
  return result;
}

/* Beta.16's shared GitHub operations (backend/shared_operations.py fixes their fields; src/bridge.rs lists the same
 * names) have their types and calls in github.ts. b16/lifecycle's idle-thread, Next up and tester-report operations
 * keep their own, in tidy.ts and nextUpPlan.ts. */
