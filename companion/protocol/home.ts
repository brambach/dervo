import {HOME_CHANNEL,type Ask,type Bucket,type HomeRow,type HomeSnapshot,type ThreadDetail,type ThreadMessage} from './messages.ts';

/** The host's records as its operations return them today (``projects``, ``conversations``, ``status``, ``tasks``,
 * ``conversation_feed``). Only the fields the phone reads are named; the rest is ignored. When the "Make Dervo feel
 * premium" lane's per-thread state record lands, this file is the one place that changes. */
export type HostProject={id:string;name?:string|null;cwd?:string|null;profile_id?:string|null};
export type HostActivity={status?:'needs'|'running'|null;running?:boolean;step?:string|null;unread?:number;since?:number|null;goal?:string|null;
  why?:string|null;summary?:string|null;at?:number|null;ended?:string|null;attention?:boolean;lost?:boolean;pending_question?:string|null};
export type HostConversation={id:string;name:string;project_id:string;activity?:HostActivity|null};
export type HostLostRun={channelId:string;projectId:string};
export type HostTask={id:string;state:string;messages?:{role:string;text:string;created_at:number}[];
  scope_version?:number;project_access?:boolean|null;folders?:string[]|null;full_access?:boolean|null;completions?:unknown[]|null;runs?:unknown[]|null;
  title?:string|null;text?:string|null;files?:string[]|null;review?:string|null;check_profile?:string|null;
  completion?:{stale?:boolean}|null;acceptance?:{required?:boolean}|null;collaboration?:{review?:{phase?:string|null}|null}|null;
  questions?:{id:string;taskId?:string;ask?:string;options?:{id:string;label?:string;detail?:string}[];state?:string;canAnswer?:boolean;canSkip?:boolean}[]};
/** ``task_detail`` for a result waiting on review: only what decides whether the phone can accept it. */
export type HostAttempt={number:number;kind?:string;state?:string;unrecorded?:unknown[]|null;argv?:unknown[];
  test?:{exit_status?:number|null;stopped?:boolean}|null;diff?:Record<string,unknown>|null};
export type HostTaskDetail={rebound?:boolean;carried?:unknown;retry?:{available?:boolean}|null;evidence?:{attempts?:HostAttempt[]}|null};
export type HostFeedRow={id:string;content?:string;pubkey?:string;created_at?:number;state?:string};

export function projectName(project:HostProject):string{
  if(project.name)return project.name;
  const parts=(project.cwd??'').split(/[\\/]/).filter(Boolean);
  return parts.at(-1)??'Project';
}

/** Which bucket a thread is in. Order matters: losing sight of a run beats everything, because showing "working" for a
 * run Dervo can't see would be a guess. A question beats running: the agent may keep going, but it's waiting on you. */
export function bucketOf(activity:HostActivity|null|undefined,lost:boolean):Bucket{
  if(lost||activity?.lost)return 'unknown';
  if(!activity)return 'idle';
  if(activity.status==='needs'||activity.pending_question||activity.attention)return 'needs';
  if(activity.running||activity.status==='running')return 'working';
  if((activity.unread??0)>0)return 'finished';
  return 'idle';
}

const LINES:Record<Bucket,string>={needs:'Waiting on you',working:'Working',finished:'Finished',idle:'',unknown:'Dervo lost sight of this run'};

export function lineOf(bucket:Bucket,activity:HostActivity|null|undefined):string{
  const a=activity??{};
  const pick=(...values:(string|null|undefined)[])=>values.find(value=>typeof value==='string'&&value.trim())?.trim();
  switch(bucket){
    case 'unknown':return LINES.unknown;
    case 'needs':return pick(a.pending_question,a.why,a.summary)??LINES.needs;
    case 'working':return pick(a.step,a.goal)??LINES.working;
    case 'finished':return pick(a.summary)??LINES.finished;
    default:return pick(a.summary,a.goal)??'';
  }
}

export function rowOf(conversation:HostConversation,project:string,lost:Set<string>):HomeRow{
  const activity=conversation.activity??null;
  const bucket=bucketOf(activity,lost.has(conversation.id));
  return {id:conversation.id,projectId:conversation.project_id,project,name:conversation.name||'Untitled thread',bucket,
    line:lineOf(bucket,activity),step:null,unread:Math.max(0,activity?.unread??0),at:activity?.at??activity?.since??null,
    question:activity?.pending_question??null,stoppable:bucket==='working',quiet:false};
}

const ORDER:Record<Bucket,number>={needs:0,unknown:1,working:2,finished:3,idle:4};

export function buildSnapshot(input:{mac:string;now:number;projects:HostProject[];conversations:Record<string,HostConversation[]>;lostRuns:HostLostRun[]}):HomeSnapshot{
  const lost=new Set(input.lostRuns.map(run=>run.channelId));
  const projects=input.projects.map(project=>({id:project.id,name:projectName(project)}));
  const names=new Map(projects.map(project=>[project.id,project.name]));
  const rows=Object.entries(input.conversations).flatMap(([projectId,list])=>list.map(conversation=>rowOf(conversation,names.get(projectId)??'Project',lost)));
  rows.sort((a,b)=>ORDER[a.bucket]-ORDER[b.bucket]||(b.at??0)-(a.at??0)||a.name.localeCompare(b.name));
  return {at:input.now,mac:input.mac,projects,rows};
}

/** The tasks a Stop on this thread ends: the ones still working. Turns and harness requests (the desktop Pause's other
 * targets) aren't reachable from the phone in v1, so a thread whose only live work is one of those shows no Stop. */
/** The runs a Stop tapped at ``tapped`` (ms, this Mac's clock) should stop: working ones whose current run had started
 * by then, give or take two seconds. A run starts with your latest message to it; one with none can't be dated, so it
 * counts as started. */
export function stopTargets(tasks:HostTask[],tapped=Infinity):string[]{
  return tasks.filter(task=>{
    if(task.state!=='working')return false;
    const started=Math.max(...(task.messages??[]).filter(m=>m.role==='user').map(m=>m.created_at).filter(Number.isFinite));
    return !Number.isFinite(started)||started*1000<=tapped+2000;
  }).map(task=>task.id);
}

const MAX_MESSAGES=60;

/** A thread's recent conversation for the phone: your messages and the agents' replies, from both the chat history and
 * each coding task's messages, oldest first, newest 60. */
export function threadOf(input:{projectId:string;channelId:string;name:string;now:number;history:HostFeedRow[];tasks:HostTask[];you:string}):ThreadDetail{
  const messages:ThreadMessage[]=[];
  for(const row of input.history){
    if(typeof row.content!=='string'||!row.content.trim())continue;
    messages.push({id:row.id,from:row.pubkey===input.you?'you':'agent',text:row.content,at:row.created_at??null});
  }
  for(const task of input.tasks){
    (task.messages??[]).forEach((message,index)=>{
      if(typeof message.text!=='string'||!message.text.trim())return;
      // Tool and system rows are the run's plumbing, not conversation.
      const from=message.role==='user'?'you':message.role==='assistant'?'agent':message.role==='reviewer'?'dervo':null;
      if(!from)return;
      messages.push({id:`${task.id}:${index}`,from,text:message.text,at:message.created_at??null});
    });
  }
  messages.sort((a,b)=>(a.at??0)-(b.at??0));
  return {projectId:input.projectId,channelId:input.channelId,name:input.name,messages:messages.slice(-MAX_MESSAGES),at:input.now};
}

/** One entry of Home's chat, as ``orchestrator_list`` returns it (b19/home-stream). Only what the phone shows is read. */
export type HostHomeEntry={id:string;kind?:string;text?:string|null;state?:string;created_at?:number;
  parts?:{name?:string|null;channel_id?:string|null;state?:string|null}[]};

/** A project's Home chat as a thread the phone can show: your messages, and Dervo's replies with where each job went. */
export function homeChatOf(input:{projectId:string;name:string;now:number;entries:HostHomeEntry[]}):ThreadDetail{
  const messages:ThreadMessage[]=[];
  for(const entry of input.entries){
    const at=typeof entry.created_at==='number'?entry.created_at:null;
    if(entry.kind==='you'){
      if(typeof entry.text==='string'&&entry.text.trim())messages.push({id:entry.id,from:'you',text:entry.text,at});
      continue;
    }
    if(entry.kind!=='reply')continue;
    if(entry.state==='thinking'){messages.push({id:entry.id,from:'dervo',text:'Thinking…',at,pending:true});continue;}
    const lines=typeof entry.text==='string'&&entry.text.trim()?[entry.text.trim()]:[];
    for(const part of entry.parts??[]){
      if(part?.state==='sent'&&part.name)lines.push(`Sent to ${part.name}.`);
    }
    if(lines.length)messages.push({id:entry.id,from:'dervo',text:lines.join('\n\n'),at});
  }
  return {projectId:input.projectId,channelId:HOME_CHANNEL,name:input.name,messages:messages.slice(-MAX_MESSAGES),at:input.now};
}

/** One record from the state spine's ``thread_states`` (branch b19/state-spine, state-spine-interface.md). When the
 * host has it, Home is built from these; the ``conversations`` path above is the fallback for a host without it. */
export type HostThreadState={id:string;projectId:string;name?:string|null;archived?:boolean;gone?:boolean;
  state:'needs_you'|'working'|'quiet'|'unknown'|'stopped'|'finished'|'idle';step?:string|null;since?:number|null;aliveAt?:number|null;
  summary?:string|null;goal?:string|null;why?:string|null;unread?:number;at?:number|null;pending_question?:string|null;
  /** True while a run is live, including on a needs_you row (b19/state-spine d39137f). */
  running?:boolean};

export function rowOfState(record:HostThreadState,project:string):HomeRow{
  const unread=Math.max(0,record.unread??0);
  const bucket:Bucket=record.state==='needs_you'?'needs':record.state==='working'||record.state==='quiet'?'working':record.state==='unknown'?'unknown'
    :record.state==='finished'&&unread>0?'finished':'idle';
  let line=lineOf(bucket,{pending_question:record.pending_question,why:record.why,summary:record.summary,step:record.step,goal:record.goal});
  // Alive but silent: say so rather than look busy.
  if(record.state==='quiet')line='No word from the agent for over a minute';
  if(record.state==='stopped')line=record.summary?.trim()||'Stopped. Edits kept.';
  return {id:record.id,projectId:record.projectId,project,name:record.name||'Untitled thread',bucket,line,step:null,unread,
    at:record.at??record.since??null,question:record.pending_question??null,
    // A thread can ask you something while its run keeps going. The spine says so with ``running``; an older record
    // without it keeps Stop on a needs row, and the Mac answers "Nothing is running" if that's the case.
    stoppable:record.running??(bucket==='working'||bucket==='needs'),quiet:record.state==='quiet'};
}

export function buildSnapshotFromStates(input:{mac:string;now:number;projects:HostProject[];threads:HostThreadState[]}):HomeSnapshot{
  const projects=input.projects.map(project=>({id:project.id,name:projectName(project)}));
  const names=new Map(projects.map(project=>[project.id,project.name]));
  const rows=input.threads.filter(record=>!record.gone&&!record.archived&&names.has(record.projectId)).map(record=>rowOfState(record,names.get(record.projectId)!));
  rows.sort((a,b)=>ORDER[a.bucket]-ORDER[b.bucket]||(b.at??0)-(a.at??0)||a.name.localeCompare(b.name));
  return {at:input.now,mac:input.mac,projects,rows};
}

/** What fits in one sealed message with room to spare (crypto.ts MAX_PLAINTEXT is 256 KB). */
export const FIT_BYTES=200*1024;
const bytes=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).length;

/** Drops the oldest messages until the thread fits, and says so. A long thread shows its recent end instead of nothing. */
export function fitThread(thread:ThreadDetail,budget=FIT_BYTES):ThreadDetail{
  if(bytes(thread)<=budget)return thread;
  const messages=[...thread.messages];
  const fitted={...thread,messages,earlier:true};
  while(messages.length>1&&bytes(fitted)>budget)messages.shift();
  // The newest message alone can still be too long: keep its end.
  if(messages.length===1&&bytes(fitted)>budget){
    const last=messages[0];
    messages[0]={...last,text:'…'+last.text.slice(-Math.floor(budget/4))};
  }
  return fitted;
}

/** Drops rows from the end (idle first, by the sort order) until Home fits, and counts them. */
export function fitSnapshot(home:HomeSnapshot,budget=FIT_BYTES):HomeSnapshot{
  if(bytes(home)<=budget)return home;
  const rows=[...home.rows];
  const fitted={...home,rows,hidden:0};
  while(rows.length&&bytes(fitted)>budget){rows.pop();fitted.hidden++;}
  return fitted;
}

/** Phases in which the host's own reviewers still drive a result (collaboration.REVIEW_OPEN). */
const REVIEW_OPEN=new Set(['pending','initial','correction','final']);

/** What a thread waits on you for, from its tasks: open questions, a scope to approve, a result to accept. The same
 * rules as the Mac's own buttons (CodingTasks.tsx, GuideActions.tsx), and the host checks again when you tap. */
export function asksOf(tasks:HostTask[],details:Record<string,HostTaskDetail>):Ask[]{
  const asks:Ask[]=[];
  for(const task of tasks){
    for(const question of task.questions??[]){
      if(question.state!=='pending'||!question.canAnswer)continue;
      asks.push({kind:'question',taskId:task.id,questionId:question.id,ask:question.ask??'',canSkip:question.canSkip===true,
        options:(question.options??[]).map(option=>({id:option.id,label:option.label??'',detail:option.detail??''}))});
    }
    const what=taskWhat(task);
    if(task.state==='needs_permission'&&Number.isSafeInteger(task.scope_version))
      asks.push({kind:'scope',taskId:task.id,scopeVersion:task.scope_version!,what,files:task.files??[],phone:scopeOnPhone(task)});
    if(task.state==='ready_for_review'){
      const detail=details[task.id];
      const latest=detail?.evidence?.attempts?.at(-1);
      const phase=task.collaboration?.review?.phase??'';
      // Not a decision for anyone yet: the host's reviewers still run, a correction was asked for, or it went stale.
      if(!detail||!latest||latest.kind==='restoration'||detail.rebound||detail.retry?.available||task.completion?.stale
        ||task.review==='rejected'||REVIEW_OPEN.has(phase))continue;
      const checked=task.check_profile==='manual-review'?false:latest.state==='verified'&&latest.test?.exit_status===0&&!latest.test.stopped;
      const approvable=task.check_profile==='manual-review'
        ?latest.kind==='manual-review'&&latest.state==='unverified'&&latest.test==null&&(latest.argv?.length??0)===0
        :checked;
      asks.push({kind:'review',taskId:task.id,attemptNumber:latest.number,what,files:Object.keys(latest.diff??{}).length,checked,
        // With approval off for this request, an unfinished result needs a correction, not an approval.
        phone:approvable&&task.acceptance?.required!==false&&!latest.unrecorded?.length&&!detail.carried});
    }
  }
  return asks;
}
/** Whether a scope is only its own files, the one thing the phone's card shows. Anything broader is approved on the Mac. */
export function scopeOnPhone(task:HostTask):boolean{
  return task.project_access!==true&&task.full_access!==true&&!(task.folders?.length);
}
/** What changes when a result waiting on review gets a new attempt or moves on, so its evidence is read again. */
export function detailKey(task:HostTask):string{
  return JSON.stringify([task.state,task.review??null,task.scope_version??null,task.collaboration?.review?.phase??null,task.completion?.stale??null,
    task.completions?.length??null,task.runs?.length??null]);
}
function taskWhat(task:HostTask):string{
  const first=task.messages?.find(message=>message.role==='user')?.text;
  return (task.title||task.text||first||'').trim().split('\n')[0].slice(0,400);
}
