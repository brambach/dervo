import {isFeedbackCommand} from './feedbackCommand';
import {Fragment,useEffect,useLayoutEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {rememberHomeJob} from './notifications';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {SAFETY_MS,useThreadChanges} from './threadStates';
import {local,type Conversation,type ConversationActivity,type DervoAccountStatus} from './api';
import {lasted,spoken} from './homeTime';
export {lasted,spoken} from './homeTime';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {LiveJobLine,liveJob} from './JobLive';
import {StepLine,failureLine,receiptLine,startingLine,workingStep} from './Presence';
import {ActivityLine,ActivityLines} from './ActivityLine';
import {SentImages} from './LocalImageComposer';
import {imageRequest,prepareImage,uploadImage,thumbnailOf,tilePreview,type LocalImage} from './imageInput';
import {QuestionTray} from './Questions';
import {pendingQuestion,questionComposerCopy} from './questionShape';
import {archivedLine} from './historyImport';
import {MICROTASK_EXAMPLES} from './Microtasks';
import {revealArchived} from './ArchivedFold';
import './Home.css';
import {homeRequests,requestQuestion,requestSummary} from './homeRequests';
import {ReminderFailure,ReminderRow,RemindLater,useReminders} from './Reminders';
import {aheadLine} from './reminderState';
import {HomeSuggestions} from './HomeSuggestions';
import {OutsideSessions} from './OutsideSessions';
import {conversationStopTargets} from './ConversationStop';
import {markStopping} from './stopping';
import type {CodingTask} from './CodingTasks';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {MoveRows,type ThreadMenuModel} from './ThreadMenu';
import {HomeLaunchPicker,useCatalog} from './ModelPicker';
import {useClaudeVersion} from './AddProject';
import {accountsFrom,type Profile} from './models';
import {usageSummary} from './api';
import {accountFor,applyLaunch,hear,keptLine,launchFailed,resolveLaunch,type Launch,type Picked} from './homeLaunch';
import {batchFor,bringWaiting,broughtLine,undoBatch,waitingLine,waitingWork} from './onboarding';
import {useWorkSide} from './WorkAccount';
import {homeTidyLine,tidiedToday,tidySetting} from './tidy';
import {HomePanel,HomePeek} from './HomePanel';
import {VoiceButton,VoiceStrip,useHomeVoice,useTalkKey} from './HomeVoice';
import type {SendOutcome} from './voiceState';
import {paneSheet} from './homeFamilies';
import './MacOnboarding.css';
import {formatDate,t} from '@/i18n';
import {isHostMessage} from '@/i18n/hostMessages';

/** Catch up is Home's default view. Saved request groups keep routing exchanges out of the work list.
 * Opening a chat leaves Home; returning starts at catch-up. Questions stay visible until their exchange is answered.
 * Jobs, permissions, retries and receipts continue to use the host's existing records. */

export type Named={id:string;name:string};
export type PickupItem={id:string;name:string;private:boolean;seen:number|null;moved:number|null;kind:string;status:string;needs:boolean;
 asked:string|null;line:string|null;did:string|null;now:string|null;next:string|null;next_by:'agent'|'dervo'|null;running_since:number|null;
 changed:boolean;question:boolean;failed:boolean;checks:'passed'|'failed'|null;handoff:string|null;
 /** How a stopped run ended (backend/run_ending.py, beta.17.1): only 'failed' is a failure. */
 ending?:'failed'|'finished'|'unknown'|'stopped'|'limit'|'not_started'|null;
 /** Beta.18: a stopped row Dervo settled as paused after a restart, or one you left stopped (backend/pickup.py). */
 paused_by?:'restart'|'left'|null;
 /** A stopped run left uncommitted file changes. In a git repository the host still offers ``handoff``: Continue opens
  * a confirm and Dervo saves a snapshot first. Anywhere else there's no handoff, and the row asks you to review them. */
 left_changes?:boolean;
 /** 16.2 (#30), from backend/pickup.result_facts. ``result`` is the task guide's goal from the current run, which the
  * agent writes as the outcome, so the row's sentence costs no model call. ``files`` names up to eight of the
  * ``files_count`` changed files (a running or stopped run is counted, not named). ``checks_line`` is Dervo's own
  * check, ``unchecked`` the guide's "not checked". All absent from a host older than 16.2, and the row falls back. */
 task_id?:string|null;result?:string|null;account?:string|null;files?:string[];files_count?:number;checks_line?:string|null;unchecked?:string|null};
export type PickupCounts={threads:number;running:number;moved:number;finished:number;stopped:number;asking:number};
/** ``awake``: while the host holds the Mac awake for open runs, the line Home shows under Running (keep_awake.LINE), else
 * null. The host owns the wording. Absent from a host older than 23 Sep. */
export type Pickup={since:number|null;lead:string;middle:(Named&{next:string|null})|null;items:PickupItem[];more:number;at:number;counts:PickupCounts;awake?:string|null};
/** ``state``: 'starting' while the host sends it, 'unknown' when Dervo closed mid-send and the thread's records don't
 * show it arrived (16.4, item 7). ``sent_at`` is when the host recorded it sent. */
export type HomePart={index:number;action:'start'|'add';text:string;title?:string|null;conversation:Named|null;idea:Named|null;new_idea?:boolean;
 channel_id?:string|null;state?:'pending'|'starting'|'sent'|'failed'|'needs_you'|'unknown';detail?:string|null;name?:string;thread_state?:string|null;thread_state_at?:number|null;
 sent_at?:number|null};
/** What an answer changed on its own (fix-organisation's CONTRACT, 19 Sep): the host writes ``line``; Undo is orchestrator_undo. */
// 'logged' and 'noted' are bugs and ideas Home logged or added your words to: idea is null, item names the bug or idea.
// 'acted' is a click you typed ("approve it", "archive that thread"), done by the host as that click (home_commands.py).
export type HomeChange={kind:'filed'|'moved'|'renamed'|'made'|'logged'|'noted'|'acted';thread:Named|null;idea:Named|null;before:Named|null;revision:number|null;can_undo:boolean;undone:boolean;line:string;
 item?:{id:string;kind:'bug'|'idea';number:number;name:string};note?:string};
// `question` is the choices tray's payload (#16). It stays `unknown` on purpose: readQuestion in questionShape.ts is the
// only thing that turns it into something the UI shows, and it rebuilds it from the fields it knows. Typing it here would
// let a reply's shape be trusted because TypeScript said so, and the shape is the host's word, not a guarantee.
/** A job Home offered and didn't start (beta.16.1, item 9): the host's record, checked when it was made. Start sends its
 * id and nothing else; the host starts it as offered. 'lapsed': you said something else since, so only words start it. */
export type HomeOffer={id:string;action:'start'|'add';title?:string|null;thread?:Named|null;workstream?:string|null;
 state:'open'|'held'|'taken'|'lapsed';taken_by?:string|null};
export type HomeEntry={id:string;created_at:number;kind:'you'|'reply';request_id?:string;answers?:string;text?:string|null;state?:'thinking'|'ready'|'failed';arrival?:boolean;
 /** Waiting for the answer before it (b19/home-stream): nothing is running for it yet. ``started_at``: when it went. */
 held?:boolean;started_at?:number;
 parts?:HomePart[];looked?:string[];mentions?:Named[];detail?:string|null;images?:string[];changes?:HomeChange[];notes?:string[];question?:unknown;
 offers?:HomeOffer[];
 /** The host is still sending this reply's jobs (16.4, item 7). */
 queued?:boolean;
 /** Dervo's router answered this with no model call (thread_router, 8 Oct). ``move``: it wasn't quite sure, so the job
  * waits until ``not_before`` (seconds) and Move can still send it somewhere else. */
 routed?:{decision?:string|null;kind:'existing'|'new';confidence:number;why?:string|null;move:boolean;moved?:boolean};not_before?:number;
 /** The agent, model and thinking level you chose for this message's jobs (group 8), checked by the host. */
 launch?:Launch;
 /** Something Home said on its own: an agent's answer, a routine's result or the heartbeat (dervo_assistant.home_post).
  * ``ref`` names the thread and task it's about. */
 assistant?:{source:'agent'|'job'|'heartbeat';title:string;ref:{kind:string;channel_id?:string|null;task_id?:string|null;threads?:string[]}};
 /** What happened to a job this request sent, said as it happened (home_news.py): done, needs you or failed. */
 news?:{kind:'done'|'needs_you'|'failed';thread:Named;at:number}};
/** The host's status read after a relaunch (fix-lifecycle's CONTRACT, 19 Sep): runs a dead host left open. */
export type LostRun={taskId:string;channelId:string;projectId:string;name:string;/** Beta.18: settled as paused from its own record. */paused?:boolean};
export type LostStatus={lostRuns?:LostRun[]|null;lostRunsDetail?:string|null};
/** What Home runs on in this project, from the host before anyone types (23 Sep, UX H1). ``model`` is what Home's own
 * replies run on and ``provider`` whose account that is: since beta.17 Home answers on Claude and Codex accounts alike.
 * ``model_id``, ``chosen`` and ``effort`` are the project's Home pick, when it made one (HomeModelSetting.tsx). */
export type HomeAvailability={ready:boolean;model?:string|null;provider?:'claude'|'codex'|null;profile_id?:string|null;model_id?:string|null;chosen?:boolean;effort?:string|null};
export type HomeListing={entries:HomeEntry[];thinking:boolean;statuses:Record<string,{status:string;at:number}>;waiting?:boolean;autostart?:boolean;home?:HomeAvailability};
/** What the host says about the branch (PLAN-github-push.md, section 2; the op names are the push brief's). Read with
 * care: the backend lands on its own branch, so a missing field reads as "not known", never as a crash. */
export type PushCommit={sha:string;short?:string;subject:string;thread_id?:string|null;thread_name?:string|null;made_outside_dervo?:boolean};
export type PushRecord={id?:string;state:'running'|'pushed'|'failed'|'unknown'|'not_pushed';detail?:string|null;count?:number;branch?:string|null;remote?:string|null;url?:string|null};
export type PushPreview={git?:boolean;branch:string|null;upstream?:string|null;remote:string|null;url?:string|null;ahead?:number;behind?:number;head?:string|null;
 can_push?:boolean;commits?:PushCommit[];confirm_token?:string|null;confirmToken?:string|null;detail?:string|null;latest?:PushRecord|null};
// Just after Dervo opens, the beta access check may still be running and Home says it's waiting: ask again for a minute.
export const ARRIVE_RETRY=3000,ARRIVE_TRIES=20;
/** The pane folds into a strip above the chat when Home is narrower than this (the narrow mockup's "below about 1000 px
 * it would fold into C's strip", measured on Home itself rather than the window). */
export const FOLD_AT=640;
/** Images attached on Home live under this thread key, per project, with the thread composer's image ops. A host without
 * that scope refuses the first read, and the paperclip stays hidden. */
export const homeImageChannel=(projectId:string)=>`home:${projectId}`;
type Outcome={state:'sent'|'failed'|'needs_you';channelId:string|null;detail:string|null};
type Invoke=<T>(operation:string,args:Record<string,unknown>)=>Promise<T>;
const DAY=86400;
/** How long a card that just arrived keeps its entrance mark: longer than the entrance, so a re-render can't cut it. */
const ENTER_FOR=1000;

/** The chat's text as paragraphs and bullet lists. "- " lines are a list; blank lines separate paragraphs. */
export function blocks(text:string):({kind:'p';text:string}|{kind:'ul';items:string[]})[]{
 const out:({kind:'p';text:string}|{kind:'ul';items:string[]})[]=[];
 for(const chunk of text.split(/\n\s*\n/)){
  const lines=chunk.split('\n').map(line=>line.trim()).filter(Boolean);
  let paragraph:string[]=[];
  const flush=()=>{if(paragraph.length)out.push({kind:'p',text:paragraph.join(' ')});paragraph=[];};
  for(const line of lines){
   const item=/^[-*•]\s+(.*)$/.exec(line);
   if(item){flush();const last=out.at(-1);if(last?.kind==='ul')last.items.push(item[1]);else out.push({kind:'ul',items:[item[1]]});}
   else paragraph.push(line);
  }
  flush();
 }
 return out;
}

/** **bold** and `code` spans, the only markup Home renders. Everything else is text. */
function Inline({text}:{text:string}){
 const parts=text.split(/(\*\*[^*]+\*\*|`[^`\n]+`)/g);
 return <>{parts.map((part,index)=>part.startsWith('**')&&part.endsWith('**')&&part.length>4?<b key={index}>{part.slice(2,-2)}</b>
  :part.startsWith('`')&&part.endsWith('`')&&part.length>2?<code key={index}>{part.slice(1,-1)}</code>:part)}</>;
}

/** What the model read before it answered, in one quiet line: the receipt's first half (Presence.tsx). */
export {readLine as didLine} from './Presence';

// ---------------------------------------------------------------- the pickup, from records
/** Blocked on you: the agent asked a question, or it can't start without your permission. Home's one accent. */
export function asking(item:PickupItem):boolean{return item.needs&&(item.kind==='permission'||item.question);}
/** What comes first: blocked on you, or stopped short. Finished work waiting for review is done. */
export function attention(item:PickupItem):boolean{return asking(item)||(item.needs&&(item.kind==='stopped'||item.kind==='unconfirmed'));}

/** The glyph whose shape carries the state. Rust is added by the caller, for the first thing that asks you. */
export function glyphOf(item:PickupItem):GlyphState{
 if(item.kind==='running')return 'running';
 if(asking(item))return 'needs';
 if(item.kind==='unconfirmed')return item.failed?'failed':item.ending==='stopped'?'stopped':'unknown';  // lost track: unknown, not failed (17.1); paused at quit (18)
 if(item.kind==='stopped')return item.failed?'failed':item.ending==='finished'?'needs':'stopped';
 if(item.kind==='review'||item.kind==='finished')return item.checks==='failed'?'failed':'done';
 if(item.kind==='approved'||item.kind==='answered')return 'done';
 return 'idle';
}

/** The word beside the glyph on the right of a row. */
export function stateWord(item:PickupItem):{word:string;tone:'running'|'done'|null;accent:boolean}{
 if(item.kind==='running')return {word:t('Running'),tone:'running',accent:false};
 if(asking(item))return {word:t('Needs you'),tone:null,accent:true};
 if(item.kind==='stopped'||item.kind==='unconfirmed')return {word:t('Stopped'),tone:null,accent:false};
 if(item.kind==='answered')return {word:t('Answered'),tone:'done',accent:false};
 if(item.kind==='approved')return {word:t('Approved'),tone:'done',accent:false};
 return {word:item.checks==='failed'?t('Check failed'):t('Finished'),tone:item.checks==='failed'?null:'done',accent:false};
}

/** The pane's three sections. Needs you is what asks you (questions and permission); Running is live work; everything
 * else, stopped work included, is what happened since you left, newest first as the host ordered it. */
export function sections(items:PickupItem[]):{needs:PickupItem[];running:PickupItem[];since:PickupItem[]}{
 return {needs:items.filter(asking),running:items.filter(item=>item.kind==='running'&&!asking(item)),
  since:items.filter(item=>!asking(item)&&item.kind!=='running')};
}

/** Which row is open on the card: the first thing blocked on you or stopped; with nothing blocked and nothing running,
 * the thread you were in the middle of; while runs work and nothing needs you, none. */
export function openIndex(items:PickupItem[],middleId:string|null):number{
 const first=items.findIndex(attention);
 if(first>=0)return first;
 if(!items.length||items.some(item=>item.kind==='running'))return -1;
 const middle=items.findIndex(item=>item.id===middleId);
 return middle>=0?middle:0;
}

/** The one action a row gets. Open on the card it's the primary button; in a compact row a small ghost one. Running
 * rows have none. Every action opens the thread; "Answer" also puts the cursor in its composer. A stopped run offers the
 * other account only when the host says the handoff will go through, changed files included where it can snapshot them
 * (the thread's Continue asks first); one that left changes it can't carry on asks for a review. */
export function actionOf(item:PickupItem,open:boolean):{label:string;focus:boolean}|null{
 if(item.kind==='running')return null;
 if(asking(item))return {label:t('Answer'),focus:true};
 if(item.kind==='stopped'||item.kind==='unconfirmed')return {label:item.handoff?t('Continue in {handoff}',{handoff:item.handoff}):item.left_changes?t('Review the changes'):t('Pick it up'),focus:false};
 if(open)return {label:t('Pick it up'),focus:false};
 if(item.kind==='review')return {label:t('Review'),focus:false};
 return null;
}

/** The open card's line under the name: why it's blocked on you, or when you last looked. */
export function openLine(item:PickupItem,now=Date.now()/1000):string{
 if(item.kind==='permission')return t('Waiting for your permission before it starts.');
 if(item.question)return t('Asked you one thing before it carries on.');
 if(!item.seen)return t('You haven’t opened it yet.');
 const said=spoken(item.seen,now);
 return /^\d/.test(said)?t('You last opened it at {time}.',{time:said}):t('You last opened it {when}.',{when:said});
}

/** A compact row's one line: the question it asked you, or its status line from records. An answered row's word on
 * the right already says Answered, so the line drops it (status once) and a bare "Answered in text." shows no line. */
export function rowLine(item:PickupItem):string|null{
 if(item.question&&item.next)return `“${item.next}”`;
 if(item.kind==='answered'&&item.line)return item.line.replace(/^Answered(?::\s*|\s+in text\.?\s*$)/,'')||null;
 return item.line;
}

// ---------------------------------------------------------------- the useful row, its peek and the panel (16.2, #30)
/** A row's one sentence: what the thread did or is doing, from the task guide's goal. A running row without a goal says
 * nothing here (its step is on the quiet line); anything else falls back to the status line it had before. */
export function rowSentence(item:PickupItem):string|null{
 if(item.result)return item.result;
 return item.kind==='running'?null:rowLine(item);
}
export type MetaPart={text:string;tone:'ask'|'accent'|'ok'|'warn'|'live'|null};
const lowerFirst=(text:string)=>/^[A-Z][a-z]/.test(text)?text[0].toLowerCase()+text.slice(1):text;
/** The quiet line under the sentence: what it wants from you, how many files changed, whether checks passed. Rust
 * (``accent``) only on the one thing that asks you; the Now step while it runs. ``step`` is the freshest step known. */
export function rowMeta(item:PickupItem,accent=false,step:string|null=item.now):MetaPart[]{
 const out:MetaPart[]=[];
 const files=item.files_count??0;
 if(item.account)out.push({text:t('On {account}',{account:item.account}),tone:null});
 if(item.kind==='running'){
  if(step)out.push({text:t('Now: {step}',{step:lowerFirst(step)}),tone:'live'});
  if(files)out.push({text:t('{count} files',{count:files}),tone:null});
  return out;
 }
 if(asking(item))out.push({text:item.kind==='permission'?t('Wants your permission'):t('Asks you one thing'),tone:accent?'accent':'ask'});
 else if(item.kind==='stopped'||item.kind==='unconfirmed'){const action=actionOf(item,false);if(action)out.push({text:action.label,tone:'ask'});}
 else if(item.kind==='review'&&item.needs)out.push({text:t('Wants your review'),tone:'ask'});
 if(files)out.push({text:item.kind==='stopped'?t('{count} files, unfinished',{count:files}):item.kind==='unconfirmed'?t('{count} files, not committed',{count:files}):t('{count} files',{count:files}),tone:null});
 else if(asking(item)&&item.kind!=='permission')out.push({text:t('No files changed'),tone:null});
 if(item.checks==='passed')out.push({text:t('Checks passed'),tone:'ok'});
 else if(item.checks==='failed')out.push({text:t('A check failed'),tone:'warn'});
 if(item.kind==='approved')out.push({text:item.line?.endsWith('committed.')?t('Committed'):t('Approved'),tone:'ok'});
 return out;
}
export type PeekFact={label:string;text?:string;files?:string[];more?:number};
/** The peek's facts, in the mock's order: what you asked, the changed files, checks, what wasn't checked, what's next.
 * Each only when a record holds it. */
export function peekFacts(item:PickupItem):PeekFact[]{
 const out:PeekFact[]=[];
 if(item.asked)out.push({label:t('You asked'),text:item.asked});
 const files=item.files??[];
 if(files.length)out.push({label:t('Changed'),files,more:Math.max(0,(item.files_count??files.length)-files.length)});
 if(item.checks_line)out.push({label:t('Checks'),text:item.checks_line});
 if(item.unchecked)out.push({label:t('Not checked'),text:item.unchecked});
 if(item.next)out.push({label:t('Next'),text:item.question?`“${item.next}”`:item.next});
 return out;
}
/** Home's four buckets, as the Claude Projects panel has them (design, 8 Oct): Waiting on you holds anything that needs
 * you, questions and reviews and stopped work alike; Working is live; Idle is work that stopped without asking; Resolved
 * is the rest. Every item lands in exactly one. */
export type Buckets={waiting:PickupItem[];working:PickupItem[];idle:PickupItem[];resolved:PickupItem[]};
export function buckets(items:PickupItem[]):Buckets{
 const waits=(item:PickupItem)=>asking(item)||(item.needs&&item.kind!=='running');
 const waiting=[...items.filter(asking),...items.filter(item=>waits(item)&&!asking(item))];
 return {waiting,working:items.filter(item=>item.kind==='running'&&!asking(item)),
  idle:items.filter(item=>!waits(item)&&(item.kind==='stopped'||item.kind==='unconfirmed')),
  resolved:items.filter(item=>!waits(item)&&item.kind!=='running'&&item.kind!=='stopped'&&item.kind!=='unconfirmed')};
}
/** The rows in the order Home draws them: Waiting on you, Working, Idle, Resolved. Up and Down walk this. */
export function paneOrder(items:PickupItem[]):PickupItem[]{
 const parts=buckets(items);
 return [...parts.waiting,...parts.working,...parts.idle,...parts.resolved];
}
/** The greeting at the head of Home, by the hour (serif, like the landing page). */
export function greeting(date=new Date()):string{
 const hour=date.getHours();
 return hour<5?t('Still up.'):hour<12?t('Good morning.'):hour<18?t('Good afternoon.'):t('Good evening.');
}
/** The thread Up (-1) or Down (+1) opens from ``id``, wrapping at the ends. */
export function stepThread(order:PickupItem[],id:string|null,delta:1|-1):string|null{
 if(!order.length)return null;
 const at=order.findIndex(item=>item.id===id);
 if(at<0)return order[delta>0?0:order.length-1].id;
 return order[(at+delta+order.length)%order.length].id;
}
/** What opens once you've acted on ``id``: the first other row that still needs you, in the order Catch up draws them. */
export function nextNeeding(order:PickupItem[],id:string|null):string|null{
 return order.find(item=>item.id!==id&&(asking(item)||item.needs))?.id??null;
}
/** A Catch up row by its thread id, read from the attribute rather than put into a selector. */
function rowNode(root:HTMLElement|null,id:string|null,within:string):HTMLElement|null{
 return [...root?.querySelectorAll<HTMLElement>(`${within}[data-row]`)??[]].find(node=>node.dataset.row===id)??null;
}
/** Home is too narrow for the panel and Catch up side by side below this width, measured on Home itself: a window
 * under about 900 px with the sidebar open, where Catch up folds into a strip above the panel. At 1052 (Bryce's review
 * size) Home is about 736 px and both fit. */
export const PANEL_SIDE_AT=700;
/** How long the pointer rests on a row before its peek opens (the mock's 350 ms). */
export const PEEK_AFTER=350;

/** Whether the pickup makes room for the conversation: once you've written in the chat this visit, or when Dervo
 * answered you in the last ten minutes. With the pane beside the chat nothing shrinks any more; the rule still says
 * whether the chat is the live thing, which is what the composer scrolls for. */
export const COMPACT_FOR=10*60;
export function compactFor(talk:HomeEntry[],wrote:boolean,now=Date.now()/1000):boolean{
 const last=talk.at(-1);
 return wrote||(!!last&&!last.arrival&&now-last.created_at<COMPACT_FOR);
}

/** Your message between Enter and the host's copy landing (beta.18). ``unsent``: the host refused it and nothing went.
 * ``unsure``: the host may still be working on it, so it isn't offered again. ``landed``: the host's copy, once it's in. */
export type Outgoing={key:string;text:string;images:string[];requestId:string|null;at:number;state:'sending'|'unsent'|'unsure';detail?:string;
 landed?:{id:string;requestId:string}};
export type ShownEntry=HomeEntry&{outgoing?:Outgoing['state']};
/** The request's chat with your message on the end while it's on its way, and the step line under it while it sends,
 * as the host's own thinking reply will draw it. Gone once the host's copy is in the chat. */
export function withOutgoing(talk:HomeEntry[],outgoing:Outgoing|null,requestId:string|null,handed:Map<string,string>):ShownEntry[]{
 if(!outgoing||outgoing.requestId!==requestId||talk.some(entry=>handed.get(entry.id)===outgoing.key))return talk;
 const you:ShownEntry={id:outgoing.key,kind:'you',created_at:outgoing.at,text:outgoing.text,outgoing:outgoing.state,...(outgoing.images.length?{images:outgoing.images}:{})};
 if(outgoing.state!=='sending')return [...talk,you];
 return [...talk,you,{id:`${outgoing.key}:reply`,kind:'reply',created_at:outgoing.at,state:'thinking',parts:[],outgoing:'sending'}];
}
/** Said when Enter lands while Dervo is still answering the last message. */
const WAIT_HINT=()=>t('Dervo is still answering. Send this when it’s done.');
/** How a refused orchestrator_send reads. ``unsure`` when the host may still be on it: b18/stopping-host's bridge
 * (308d8fa5, src/bridge.rs busy_still) answers a request slow past 10 s with "Dervo is still busy with orchestrator
 * send. It may still finish, so check before you try it again." while the backend keeps going. Everything else is
 * ``unsent``, including its busy_skipped "Dervo is busy with …, so this wasn’t done", which means nothing was sent.
 * local() hands this the text already in the window's language (hostFailure), so the Portuguese line has to match too:
 * read as not sent, a slow send would go back in the box and a second Enter would send it twice. Matched by code
 * (isHostMessage), so it holds in every language the catalog has a line for. */
export function sendFailure(detail:string):'unsent'|'unsure'{
 return isHostMessage(detail,'host_busy_still')?'unsure':'unsent';
}

/** A moment as the caption says it: the clock alone within the last day ("Since 11:20 PM", read in the morning), the
 * spoken day and time beyond that. */
export function sinceClock(at:number,now=Date.now()/1000):string{
 if(now-at>=0&&now-at<DAY)return formatDate(at*1000,{hour:'numeric',minute:'2-digit'});
 return spoken(at,now);
}

/** The counts from the records pickup. A reply without them (an older host, or a fixture) is counted from its rows
 * instead, so one missing field can't take the window down (19 Sep, found merging the shell). */
export function pickupCounts(found:Pickup):PickupCounts{
 if(found.counts)return found.counts;
 const kinds=found.items.map(item=>item.kind);
 const moved=found.items.filter(item=>item.kind!=='running'&&!!found.since&&!!item.moved&&item.moved>found.since).length;
 return {threads:found.items.length+(found.more??0),running:kinds.filter(kind=>kind==='running').length,moved,
  finished:kinds.filter(kind=>kind==='review'||kind==='finished').length,stopped:kinds.filter(kind=>kind==='stopped'||kind==='failed').length,asking:0};
}

/** The pane's one name (F1, 20 Sep). It used to change with the moment - "Happening now" while runs worked, "Where you
 * left off" otherwise - and now it doesn't, because the control in the same header opens this pane in full and the two
 * depths have to read as one place. What the moment is still gets said once, in the caption. */
export const PANE_TITLE=t('Catch up');

/** The title and the caption beside it, from the counts. ``known`` is how many threads the sidebar lists: the pickup
 * leaves out threads nobody has written in, so with 44 of them and none started the caption says so instead of
 * "No threads yet" (UX2). */
export function heading(found:Pickup,compact:boolean,now=Date.now()/1000,known=0):{title:string;caption:string}{
 const counts=pickupCounts(found);
 const title=PANE_TITLE;
 const since=found.since?t('Since {when}',{when:sinceClock(found.since,now)}):null;
 if(compact)return {title,caption:since??t('{count} threads',{count:counts.threads})};
 if(counts.running)return {title,caption:[t('{running} running',{running:counts.running}),counts.finished?t('{finished} finished',{finished:counts.finished}):'',counts.stopped?t('{stopped} stopped',{stopped:counts.stopped}):''].filter(Boolean).join(' · ')};
 if(counts.moved&&since)return {title,caption:`${since} · ${t('{count} threads moved',{count:counts.moved})}`};
 if(!counts.threads)return {title,caption:known>0?t('{count} threads, none started yet',{count:known}):t('No threads yet')};
 return {title,caption:`${t('Nothing running')} · ${t('{count} threads',{count:counts.threads})}`};
}

/** The summary Dervo wrote when you came back this time, to show as its first message. None while it's thinking, when
 * it failed, or when the newest one is from an earlier visit: the records lead stands on its own. */
export function arrivalEntry(entries:HomeEntry[],since:number|null):HomeEntry|null{
 const arrival=[...entries].reverse().find(entry=>entry.kind==='reply'&&entry.arrival);
 if(!arrival||arrival.state!=='ready'||!arrival.text)return null;
 if(since&&arrival.created_at<since)return null;
 return arrival;
}

/** The summary Dervo couldn't write when you came back: the newest arrival failed, and it's from this visit. Said once in
 * the pane, under the records it leaves standing (organisation-phase2 item 3). */
export function failedArrival(entries:HomeEntry[],since:number|null):boolean{
 const arrival=[...entries].reverse().find(entry=>entry.kind==='reply'&&entry.arrival);
 return !!arrival&&arrival.state==='failed'&&(!since||arrival.created_at>=since);
}
export const ARRIVAL_FAILED=t('Couldn’t write a summary just now. The list above is from your records.');
/** A draft Home logs as a bug without asking a model: "bug:" or "dervo bug:" first, any case, after trimming. */
export function bugDraft(text:string):boolean{return /^(?:dervo\s+)?bug:/i.test(text.trim());}
/** The arrival line, with the records it leaves standing or without. */
export function arrivalFailedLine(withItems:boolean):string{
 return withItems?ARRIVAL_FAILED:t('Couldn’t write a summary just now.');
}

/** The lost-runs line for this project after a relaunch, or null. The host's own sentence when every lost run is here;
 * otherwise the same words for this project's share, so another project's runs never show on this Home. ``channelId`` is
 * set when exactly one run was lost here: the line's action opens it. */
export function lostRunsLine(status:LostStatus|undefined|null,projectId:string):{text:string;channelId:string|null}|null{
 const all=Array.isArray(status?.lostRuns)?status!.lostRuns!:[];
 const settled=all.filter(run=>run?.projectId===projectId&&run.paused);
 const here=all.filter(run=>run?.projectId===projectId&&!run.paused);
 if(!here.length&&!settled.length)return null;
 // The host's own sentence when every lost run is in this project (it says paused for paused ones, beta.18).
 if(status?.lostRunsDetail&&here.length+settled.length===all.length)return {text:status.lostRunsDetail,channelId:here.length===1?here[0].channelId:!here.length&&settled.length===1?settled[0].channelId:null};
 // Beta.18: runs Dervo settled as paused from their own record (backend lost_runs.settle) are said as paused.
 if(!here.length&&settled.length===1)return {text:t('Dervo restarted while a run was going in {name}. It was between steps, so it paused there. Continue picks it up.',{name:settled[0].name}),channelId:settled[0].channelId};
 if(!here.length)return {text:t('Dervo restarted while {length} runs were going here. Each was between steps, so it paused there. Continue picks each one up.',{length:settled.length}),channelId:null};
 const channelId=here.length===1?here[0].channelId:null;
 // What's left here is what the provider's record couldn't settle: open the thread to see where it got to.
 if(here.length===1)return {text:t('Dervo closed while a run was going in {name}, so it can’t say how it ended. Open that thread to see where it got to.',{name:here[0].name}),channelId};
 return {text:t('Dervo closed while {length} runs were going here, so it can’t say how they ended. Open each thread to see where it got to.',{length:here.length}),channelId:null};
}

/** A reply's own lines: what it changed, in order, then its notes. Each change keeps its index, which Undo names. */
export function replyLines(entry:HomeEntry):{key:string;text:string;change:number|null;undo:boolean;undone:boolean}[]{
 const changes=(Array.isArray(entry.changes)?entry.changes:[]).map((change,index)=>({key:`c${index}`,text:change?.line??'',change:index as number|null,
  undo:!!change?.can_undo&&!change.undone,undone:!!change?.undone})).filter(line=>line.text);
 const notes=(Array.isArray(entry.notes)?entry.notes:[]).filter(note=>typeof note==='string'&&note.trim()).map((note,index)=>({key:`n${index}`,text:note,change:null,undo:false,undone:false}));
 return [...changes,...notes];
}
/** Threads a reply's change lines already name as filed or moved, so their job line doesn't say "in X" a second time. */
export function filedByChanges(entry:HomeEntry):Set<string>{
 return new Set((Array.isArray(entry.changes)?entry.changes:[]).filter(change=>(change.kind==='filed'||change.kind==='moved')&&!change.undone&&change.thread).map(change=>change.thread!.id));
}

/** Whether Home should ask you to sign in: the account is email-code based and it's signed out, expired or cancelled.
 * A local workspace, a sign-in in progress, or pending beta access isn't that. */
export function wantsSignIn(account:DervoAccountStatus|undefined):boolean{
 return account?.method==='email_code'&&(account.state==='signed_out'||account.state==='expired'||account.state==='cancelled');
}

// ---------------------------------------------------------------- jobs Dervo started
/** A job's name: the thread it went to as it's called now, or the title it was given. */
export function partTitle(part:HomePart):string{
 return part.name??part.conversation?.name??part.title??t('New thread');
}

/** The small line under a job's name: its workstream, or what stopped it. */
export function jobLine(part:HomePart,said?:Set<string>):string{
 if(part.state==='failed')return part.detail??t('It couldn’t be sent.');
 if(part.state==='unknown')return part.detail??UNKNOWN_SEND;
 if(part.state==='needs_you')return part.detail??t('Waiting for you in that thread.');
 if(part.thread_state==='unknown'&&part.detail&&part.detail!=='queued')return part.detail;
 const channel=part.channel_id??part.conversation?.id;
 if(part.idea)return channel&&said?.has(channel)?'':t('in {name}',{name:part.idea.name});
 return part.action==='add'?t('Added to this thread'):'';
}

/** Where a job stands, as one line under its name. Status lives in the Now pane now, so a sent job says what happened
 * to it here ("started", "added") and its glyph follows the thread's live activity; only what the pane can't show is
 * said: not sent, waiting for you there, waiting for a slot, or the last thing the host knew when it lost the thread
 * (#25: never "Starting" for a sent job; "Unknown" when observation is lost). */
export function jobState(part:HomePart,live:boolean,activity:ConversationActivity|null|undefined,sentAt:number,_now=Date.now()/1000)
 :{glyph:GlyphState;word:string;tone:'running'|'done'|null}{
 const state=part.state??'pending';
 if(state==='failed')return {glyph:'failed',word:t('Not sent'),tone:null};
 if(state==='needs_you')return {glyph:'needs',word:t('Needs you'),tone:null};
 if(state==='unknown')return {glyph:'stopped',word:t('Unknown'),tone:null};
 if(state==='starting')return {glyph:'running',word:t('Starting'),tone:'running'};
 if(state==='pending')return live?{glyph:'running',word:t('Starting'),tone:'running'}:{glyph:'idle',word:t('Not sent yet'),tone:null};
 if(activity?.running)return {glyph:'running',word:part.action==='add'?t('Added'):t('Started'),tone:null};
 if(activity?.status==='needs')return {glyph:'needs',word:t('Needs you'),tone:null};
 if(activity?.at&&activity.at>sentAt)return {glyph:'done',word:part.action==='add'?t('Added'):t('Started'),tone:null};
 if(part.detail==='queued')return {glyph:'queued',word:t('Waits for a slot'),tone:null};
 if(part.thread_state==='archived')return {glyph:'idle',word:t('Archived'),tone:null};
 if(part.thread_state==='needs')return {glyph:'needs',word:t('Needs you'),tone:null};
 if(part.thread_state==='finished'||part.thread_state==='running')return {glyph:part.thread_state==='running'?'running':'done',word:part.action==='add'?t('Added'):t('Started'),tone:null};
 if(part.thread_state==='stopped')return {glyph:'stopped',word:t('Stopped'),tone:null};
 return {glyph:'stopped',word:t('Unknown'),tone:null};
}

/** Home's jobs are the host's to send since 16.4 (item 7, #33): it sends them one a round from its background work,
 * whether or not Home is on screen, and the page only shows how far it got. Nothing here sends a job unless you click.
 * The job the host is on now: the one it marked starting, else the next one waiting, while the reply is queued. */
export function hostSending(entry:HomeEntry):HomePart|null{
 if(entry.kind!=='reply'||entry.state!=='ready'||!entry.queued)return null;
 const parts=entry.parts??[];
 return parts.find(part=>part.state==='starting')??parts.find(part=>(part.state??'pending')==='pending')??null;
}
/** A job left unsent with nobody sending it: the reply is from before 16.4, when the app sent jobs and closed mid-send
 * (MEM8). It waits for your Send, so nothing starts twice. */
export const UNSENT_LINE=t('Dervo closed before this was sent. Send it now, or leave it.');
/** The host's own line for a job it lost track of; this is only the fallback for a host that didn't say. */
export const UNKNOWN_SEND=t('Dervo closed while this was starting. Open the thread to check, then send it again or leave it.');

/** The offers a reply shows a Start button for: open ones, on a reply that isn't asking its own question (a rule's
 * "Go ahead" in the tray already answers those). */
export function liveOffers(entry:HomeEntry):HomeOffer[]{
 if(entry.kind!=='reply'||entry.state!=='ready'||entry.question)return [];
 return (Array.isArray(entry.offers)?entry.offers:[]).filter(offer=>offer?.state==='open'&&typeof offer.id==='string');
}
export function offerTitle(offer:HomeOffer):string{
 return (offer.action==='add'?offer.thread?.name:offer.title)||t('Offered job');
}
/** The small line under an offer's name: that it's waiting for you, and where it would go. */
export function offerLine(offer:HomeOffer):string{
 if(offer.action==='add')return t('Offered · adds to this thread');
 return offer.workstream?t('Offered · in {workstream}',{workstream:offer.workstream}):t('Offered');
}

/** Send one job: start its thread (named, and filed under its workstream) when it needs one, then the coding request,
 * as a message typed there would. It waits in that thread's queue when every task slot is busy, and the outcome says
 * so. ``clientId`` makes a repeat send of the same job land on the same request. ``clicked`` is true only when you
 * pressed Start or Send on the row: a job Home started on its own never gets full access (the host decides, H1).
 * ``launch`` is the agent, model and thinking you chose for the message (group 8): the thread is set to it before the
 * request, with the thread picker's own operations, and a thread that can't be set fails the job with the reason. */
export async function sendJob(part:HomePart,{projectId,profileId,clientId,clicked=false,launch}:{projectId:string;profileId:string;clientId:string;clicked?:boolean;launch?:Launch|null},invoke:Invoke):Promise<Outcome>{
 let channelId=part.channel_id??part.conversation?.id??null;
 const made=!channelId;
 try{
  if(!channelId){
   const title=(part.title??'').trim();
   const created=await invoke<{id:string}>('create_conversation',title?{projectId,name:title,private:false}:{projectId,automaticName:true,name:'',private:false});
   channelId=created.id;
   if(part.idea)try{await invoke('ideas_assign',{projectId,profileId,channelId,ideaId:part.idea.id});}catch{/* filing is a convenience; the job still goes */}
  }
  // Behaviour review S2 (24 Sep): only a thread Home makes for this job starts on the choice. An existing thread keeps its
  // own account and model, so a busy one still queues the job and its earlier tasks can still be continued.
  if(launch&&made)try{await applyLaunch(launch,{projectId,channelId},invoke);}
  catch(error){return {state:'failed',channelId,detail:launchFailed(launch,String(error instanceof Error?error.message:error))};}
  const result=await invoke<{id:string|null;state?:string;detail?:string}>('coding_request',{clientId:`${clientId}-${part.index}`,text:part.text,projectId,channelId,queueIfBusy:true,origin:'home',clicked});
  if(result.id===null)return {state:result.state==='busy_elsewhere'?'failed':'needs_you',channelId,detail:result.detail??null};
  return {state:'sent',channelId,detail:result.state==='queued'?'queued':null};
 }catch(error){
  return {state:'failed',channelId,detail:String(error instanceof Error?error.message:error)};
 }
}

// ---------------------------------------------------------------- push to GitHub, from the host's count
/** The push line under the lead: the branch, how many commits aren't on GitHub, and whether the button shows. Nothing
 * shows without a branch. The count is the host's; a missing count reads as zero, so the button never shows on a guess. */
export function pushLine(preview:PushPreview|undefined):{branch:string;ahead:number;behind:number;count:string;canPush:boolean;noRemote:boolean;noUpstream:boolean;why:string}|null{
 if(!preview||preview.git===false||!preview.branch)return null;
 const ahead=Number.isInteger(preview.ahead)?preview.ahead!:0,behind=Number.isInteger(preview.behind)?preview.behind!:0;
 const noRemote=!preview.remote,noUpstream=!preview.upstream;
 // The host's own call when it gives one (push's contract, 19 Sep); otherwise the plan's rule.
 const canPush=typeof preview.can_push==='boolean'?preview.can_push&&ahead>0:ahead>0&&!noRemote&&behind===0;
 return {branch:preview.branch,ahead,behind,count:ahead?t('{count} commits not on GitHub',{count:ahead}):'',canPush,noRemote,noUpstream,
  why:canPush||!ahead?'':preview.detail||(behind>0?t('GitHub has commits you don’t have. Pull in Terminal first.'):'')};
}
/** The confirm's question, with the full remote so a changed origin is visible (SEC2). */
export function pushQuestion(preview:PushPreview):string{
 const ahead=preview.ahead??0,remote=preview.remote??'origin',where=preview.url?preview.url.replace(/^https?:\/\//,'').replace(/\.git$/,''):'';
 return where?t('Push {count} commits to {target} on {where}?',{count:ahead,target:`${remote}/${preview.branch}`,where}):t('Push {count} commits to {target}?',{count:ahead,target:`${remote}/${preview.branch}`});
}
/** A commit's line in the confirm: short id, subject, and which chat made it or that it was made outside Dervo. */
export function commitLine(commit:PushCommit):{short:string;subject:string;who:string}{
 const name=commit.made_outside_dervo||!commit.thread_name?null:commit.thread_name;
 // The subject is "Dervo: <thread name>" once the push branch lands, so the name under it would say the same thing twice.
 return {short:commit.short??commit.sha.slice(0,7),subject:commit.subject,who:name===null?t('made outside Dervo'):commit.subject.includes(name)?'':name};
}
const isMain=(branch:string|null|undefined)=>branch==='main'||branch==='master';

/** The line under the composer: what sending does. Jobs start on their own and the agent asks only when unsure (owner,
 * 19 Sep, #2); an older host that still holds jobs for a click gets the old line. ``model`` is what the host says Home's
 * replies run on: said here, where you're about to spend it, and left out by a host that doesn't say (23 Sep).
 * ``provider`` is whose account that is, Claude unless the host says Codex (beta.17). */
export function composerHint(autostart:boolean,model?:string|null,chip=false,provider?:'claude'|'codex'|null):string{
 const does=autostart?t('Finds the right thread or starts a new one'):t('Finds the right thread · starts work when you ask');
 // Beside the agent chip, Home's own model read as a contradiction of it (design review S2): the picker's foot says it.
 return model&&!chip?t('{does} · Answers with {model} on this project’s {provider} account',{does,model,provider:provider==='codex'?'Codex':'Claude'}):does;
}

// Lost-run lines you hid or acted on this launch. The status read says the same thing until Dervo next starts.
const lostSeen=new Set<string>();

const Go=()=><svg className="dv-go" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M6 3.5L10.5 8 6 12.5"/></svg>;
const Arrow=()=><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M8 13V3M3.5 7.5L8 3l4.5 4.5"/></svg>;
const Clip=()=><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M13.2 7.6L8.3 12.5a3.2 3.2 0 0 1-4.5-4.5l5.6-5.6a2.1 2.1 0 0 1 3 3L6.8 11a1 1 0 0 1-1.5-1.5l4.6-4.6"/></svg>;
const Branch=()=><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false"><circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/><circle cx="11.5" cy="5" r="1.6"/><path d="M4.5 5.1v5.8M11.5 6.6c0 2.4-2.4 3.2-4.6 3.6-1 .2-2.4.6-2.4 1.5"/></svg>;
const Close=()=><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M4 4l8 8M12 4l-8 8"/></svg>;
// Two corners pushed apart: the pane opening into the full screen. Stroke only, so it takes the button's ink in both themes.
// A clock with its hand turned back: the requests you made here before. Stroke only, like the rest.
const History=()=><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M2.6 8a5.4 5.4 0 1 0 1.6-3.8"/><path d="M2.4 2.6v2.2h2.2"/><path d="M8 5.2V8l1.9 1.3"/></svg>;
/** The fold mark on a bucket that folds: points right while shut, down while open, turning in --dv-fast. */
const Chevron=({open}:{open:boolean})=><svg className="dv-bucket-chev" data-open={open||undefined} width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M6 3.5L10.5 8 6 12.5"/></svg>;

type Props={projectId:string;profileId:string;projectName:string;conversations:Conversation[];onOpen:(channelId:string)=>void;
 onHandOff:(channelId:string,text:string)=>void;onChanged:()=>Promise<unknown>|void;onSignIn?:(trigger:HTMLElement)=>void;
 /** Opens this pane in full, as the Catch up screen. Left off when the host can't read the project's records: there'd
  * be nothing on the other side. */
 onCatchUp?:(trigger:HTMLElement)=>void;onFeedback?:()=>void;onOpenTask?:(channelId:string,taskId:string,trigger:HTMLElement)=>void;
 /** How many of this project's threads are archived when none is live (historyImport.blankState, 21 Sep): the pane says
  * so instead of "Nothing yet", because the project isn't new, it was put away. */
 archived?:number;
 /** True the first time Home shows after its history was read. It marks the section (data-arrived) and nothing moves:
  * the staggered rise it used to drive was cut on 23 Sep, the nested fade Bryce rejected on the site. */
 arrived?:boolean;
 /** Opens Connected accounts, for "Connect Claude" when Home can't answer on this project's account. Without it Home
  * hands the click to the sidebar's usage line (openAccounts.ts). */
 onConnectedAccounts?:(trigger:HTMLElement)=>void;
 /** The thread menus' own model (ConnectedApp): a job row's Move opens the same workstream list, with the same Undo.
  * Moving a thread Home or the rules filed is a correction the host keeps (dd #10). Without it the rows have no Move. */
 threadMenu?:ThreadMenuModel};
type Preview=LocalImage&{dataUrl?:string};

/** Move on a job row (dd #10, 23 Sep): one click opens the thread menu's workstream list for that thread. Nothing shows
 * when the thread can't move (private, another account, workstreams not loaded). */
function MoveJob({conversation,model,title}:{conversation:Conversation;model:ThreadMenuModel;title:string}){
 if(model.cannotMove(conversation))return null;
 return <DropdownMenu>
  {/* Quiet: a correction you reach for, not the row's action. Stop and Send again stay the outlined ones. */}
  <DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" aria-label={t('Move {title} to another workstream',{title})}>{t('Move')}</button></DropdownMenuTrigger>
  <DropdownMenuContent align="end" className="dv-menu-thread dv-menu-move" aria-label={t('Move to workstream')}>
   <MoveRows kind="dropdown" conversation={conversation} model={model} filed={model.filedUnder(conversation)}/>
  </DropdownMenuContent>
 </DropdownMenu>;
}

/** Move on a reply Dervo's router sent somewhere while it wasn't quite sure: for its few seconds before the job goes,
 * one click sends it to another thread, a new one, or to Home to read. The host refuses once the job has gone. */
function RouteMove({entry,conversations,busy,onMove,onOpenChange}:{entry:HomeEntry;conversations:Conversation[];busy:boolean;
 onMove:(to:string)=>void;onOpenChange:(open:boolean)=>void}){
 const here=new Set((entry.parts??[]).map(part=>part.channel_id??part.conversation?.id).filter(Boolean));
 // Shared threads only: Home never routes to a private one, so it never offers one here either.
 const others=conversations.filter(conversation=>conversation.visibility==='shared'&&!here.has(conversation.id)).slice(0,12);
 return <p className="dv-caption dv-home-routed" role="status">
  {entry.routed?.kind==='new'?t('Not sure this needs a new thread.'):t('Not sure this is the right thread.')}{' '}
  <DropdownMenu onOpenChange={onOpenChange}>
   <DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={busy}
    aria-label={t('Move this message somewhere else')}>{t('Move')}</button></DropdownMenuTrigger>
   <DropdownMenuContent align="start" className="dv-menu-thread dv-menu-move" aria-label={t('Move this message to')}>
    <DropdownMenuItem onSelect={()=>onMove('home')}>{t('Ask Home instead')}</DropdownMenuItem>
    {entry.routed?.kind!=='new'&&<DropdownMenuItem onSelect={()=>onMove('new')}>{t('New thread')}</DropdownMenuItem>}
    {others.map(conversation=><DropdownMenuItem key={conversation.id} onSelect={()=>onMove(conversation.id)}>{conversation.name}</DropdownMenuItem>)}
   </DropdownMenuContent>
  </DropdownMenu></p>;
}

/** Opens the sidebar's Archived list and puts focus on it. The list is the sidebar's own (ConversationLifecycle.tsx); this
 * only unfolds it, the way Home hands a click to the account banner. */
export function showArchived():boolean{
 const details=document.querySelector<HTMLDetailsElement>('[data-testid="archived-conversations"]');
 if(!details)return false;
 details.open=true;
 const summary=details.querySelector<HTMLElement>('summary');
 revealArchived(details);
 summary?.focus({preventScroll:true});
 return true;
}

export function Home({projectId,profileId,projectName,conversations,onOpen:openOut,onHandOff,onChanged,onSignIn,onCatchUp,onFeedback,onOpenTask:openTaskOut,onConnectedAccounts,threadMenu,archived=0,arrived:arrivedFromHistory=false}:Props){
 const scope=useMemo(()=>({projectId,profileId}),[projectId,profileId]);
 const client=useQueryClient();
 const chatKey=['home',projectId,profileId];
 const pickupKey=['project-pickup',projectId,profileId];
 const pushKey=['push-preview',projectId];
 // Re-read when one of this project's threads moves (the state spine pushes it, threadStates.ts), so a mid-flight
 // glance is current the moment something changes. The slow timer is only a safety net.
 const pickup=useQuery({queryKey:pickupKey,enabled:!!projectId&&!!profileId,queryFn:()=>local<Pickup>('project_pickup',scope),
  refetchInterval:SAFETY_MS});
 useThreadChanges(projectId,()=>void client.invalidateQueries({queryKey:pickupKey}));
 // And when the project's thread list itself changes: archiving, restoring or ⌘Z moves no thread's state word, so the
 // spine says nothing and Catch up kept the old count until a reload (RC review, 9 Oct).
 const listed=useQuery<{id:string}[]>({queryKey:['local-conversations',projectId],enabled:false});
 const listedIds=listed.data?.map(row=>row.id).sort().join(' ');
 const firstList=useRef(true);
 useEffect(()=>{if(listedIds===undefined)return;if(firstList.current){firstList.current=false;return;}void client.invalidateQueries({queryKey:pickupKey});},[listedIds]);
 // The chat too, when a thread's state word moves: one Home sent work to that's now done or needs you gets its line in
 // the request that sent it (home_news). A step moving on isn't a state change, so a busy run doesn't re-read the chat.
 useThreadChanges(projectId,()=>void client.invalidateQueries({queryKey:chatKey}),(next,before)=>next?.state!==before?.state||(next?.state==='needs_you'&&next.at!==before?.at));
 const chat=useQuery({queryKey:chatKey,enabled:!!projectId&&!!profileId,queryFn:()=>local<HomeListing>('orchestrator_list',scope)});
 // The same query the shell keeps, so Home learns you're signed out without a second poll.
 const account=useQuery({queryKey:['dervo-account'],queryFn:()=>local<DervoAccountStatus>('dervo_account_status'),staleTime:5000});
 // The shell's own status read (same key), which after a relaunch names the runs Dervo lost track of.
 const hostStatus=useQuery({queryKey:['local-status'],queryFn:()=>local<LostStatus>('status'),staleTime:60000});
 // The branch and its commits, recounted when the window gets focus and after a job finishes. A host without the push
 // ops answers with an error once, and nothing about git shows.
 const push=useQuery({queryKey:pushKey,enabled:!!projectId,queryFn:()=>local<PushPreview>('push_preview',{projectId}),retry:false,staleTime:15000,refetchOnWindowFocus:true});
 const [text,setText]=useState('');
 const [resolvedOpen,setResolvedOpen]=useState(false);
 // Reminders whose day has come have their own list after Waiting on you; the ones still to come show on their thread's
 // row (9 Oct).
 const reminders=useReminders(projectId,profileId);
 // Opening a thread from Home is coming back to it, which is what your own reminder on it asked for, so that one's done.
 const onOpen=(channelId:string)=>{reminders.cameBack(channelId);openOut(channelId);};
 const onOpenTask=openTaskOut&&((channelId:string,taskId:string,trigger:HTMLElement)=>{reminders.cameBack(channelId);openTaskOut(channelId,taskId,trigger);});
 const drafts=useRef<Record<string,string>>({});
 // ---------------------------------------------------------------- the agent for this message's jobs (group 8)
 // The same queries the shell keeps (keys match ConnectedApp), so the picker adds no polling of its own.
 const profiles=useQuery({queryKey:['local-profiles'],queryFn:()=>local<Profile[]>('profiles')});
 const projectRows=useQuery({queryKey:['local-projects'],queryFn:()=>local<{id:string;profile_id:string;model:string;context?:'work'|'personal'|null}[]>('projects')});
 const usage=useQuery({queryKey:['usage-summary'],queryFn:usageSummary,refetchInterval:30000});
 const projectRow=projectRows.data?.find(row=>row.id===projectId)??null;
 // Only accounts on the project's side can run its jobs: a Work project's Home lists work accounts only.
 const accounts=useMemo(()=>accountsFrom(profiles.data??[],usage.data?.accounts,Date.now(),projectRow?.context??'personal'),[profiles.data,usage.data,projectRow?.context]);
 const [picked,setPicked]=useState<Picked|null>(null);
 const [ignoreWords,setIgnoreWords]=useState(false);
 const heard=useMemo(()=>hear(text),[text]);
 const heardCodex=heard.agent==='codex'&&!ignoreWords?accountFor(accounts,'codex',projectRow?.profile_id):null;
 const codexAccount=accounts.find(a=>a.id===picked?.profileId&&a.provider==='codex')??heardCodex??accounts.find(a=>a.id===projectRow?.profile_id&&a.provider==='codex');
 const codexCatalog=useCatalog(codexAccount?.profile,!!codexAccount);
 const claudeVersion=useClaudeVersion(accounts.some(a=>a.provider==='claude'));
 const launch=resolveLaunch({accounts,project:projectRow,picked,heard,ignoreWords,claudeVersion,
  catalogFor:account=>account.provider==='codex'&&account.id===codexAccount?.id?codexCatalog.data?.models:undefined});
 const sendBusy=useRef(false);
 const [wrote,setWrote]=useState(false);
 const [view,setView]=useState<'home'|'recent'|'request'>('home');
 const [requestId,setRequestId]=useState<string|null>(null);
 const [historyPage,setHistoryPage]=useState(0);
 const [sending,setSending]=useState<Set<string>>(new Set());
 // Your message from the moment you press Enter until the host's copy is in the chat (send(), beta.18). ``handed`` gives
 // the host's copy and its thinking reply the keys the shown ones had, so the rows stay put when it lands.
 const [outgoing,setOutgoing]=useState<Outgoing|null>(null);
 const handed=useRef(new Map<string,string>());
 // The job this screen is sending right now, per reply: which part, and the step's words ("Starting Connectors docs").
 // Keyed by reply because two replies can send at once (a reply reopened within ten minutes and a new one).
 const [starting,setStarting]=useState<Record<string,{index:number;text:string}>>({});
 // Replies this screen asked for in this session. Only those get the looking-through step: a thinking reply found after
 // a reload was snapshotted before the app came back.
 const startedHere=useRef(new Set<string>());
 const [failure,setFailure]=useState('');
 const [,tick]=useState(0);
 const log=useRef<HTMLDivElement>(null);
 const found=pickup.data;
 const entries=chat.data?.entries??[];
 // What Home's own replies run on here, from the host before anyone types (23 Sep, UX H1). Either provider since beta.17.
 const home=chat.data?.home;
 const arrival=arrivalEntry(entries,found?.since??null);
 const requests=homeRequests(entries);
 const activeRequest=requests.find(request=>request.id===requestId);
 const talk=view==='request'?withOutgoing(activeRequest?.entries??[],outgoing,requestId,handed.current):[];
 const unanswered=requests.filter(request=>requestQuestion(request));
 const saveDraft=()=>{drafts.current[requestId||'new']=text;};
 const openRequest=(id:string)=>{saveDraft();setText(drafts.current[id]||'');setRequestId(id);setView('request');setFailure('');};
 const showHome=()=>{saveDraft();setText(drafts.current.new||'');setView('home');setRequestId(null);setFailure('');};
 useLayoutEffect(()=>{if(log.current)log.current.scrollTop=0;},[view]);
 const thinking=!!chat.data?.thinking;
 // Jobs the host is still sending (16.4, item 7). The page only shows them; the host sends them whether or not it's here.
 const hostBusy=entries.some(entry=>!!hostSending(entry));
 // While Dervo is answering or sending jobs, ask for the chat every second and a half. A timer rather than
 // refetchInterval: the send and the arrival write the chat in directly, and that doesn't restart a query's polling.
 useEffect(()=>{
  if(!thinking&&!hostBusy)return;
  const timer=setInterval(()=>void client.invalidateQueries({queryKey:chatKey}),1500);
  return ()=>clearInterval(timer);
 },[thinking,hostBusy,projectId,profileId]);
 const replyThinking=entries.some(entry=>entry.state==='thinking');
 const activity=new Map(conversations.map(conversation=>[conversation.id,conversation.activity]));
 const anyRunning=(!!found&&pickupCounts(found).running>0)||conversations.some(conversation=>conversation.activity?.running);
 // "22 min" moves on its own while something runs.
 useEffect(()=>{
  if(!anyRunning)return;
  const timer=setInterval(()=>tick(n=>n+1),30000);
  return ()=>clearInterval(timer);
 },[anyRunning]);

 // ---------------------------------------------------------------- the step where the reply will land (Presence.tsx)
 // Its words move on with the clock ("Looking through your threads", "Thinking it through", "Still thinking · 2 min"),
 // so while Dervo works this screen re-reads the clock every second. The job being sent shows at the answer's foot.
 const working=replyThinking||sending.size>0||hostBusy||outgoing?.state==='sending';
 useEffect(()=>{
  if(!working)return;
  const timer=setInterval(()=>tick(n=>n+1),1000);
  return ()=>clearInterval(timer);
 },[working]);
 const hasThreads=conversations.some(conversation=>conversation.visibility!=='private');
 const stepFor=(reply:HomeEntry,position:number)=>{
  const asked=talk[position-1];
  return workingStep({since:reply.started_at??reply.created_at,now:Date.now()/1000,fresh:startedHere.current.has(reply.id),hasThreads,images:asked?.kind==='you'?asked.images?.length??0:0});
 };

 // Opening Home: ask for a summary when you've been away or something changed. The host decides; it's cheap to ask.
 // Only once the chat's first load is in, which would otherwise land after this and put back a chat without it.
 const arrived=useRef(false);
 const loaded=chat.isSuccess||chat.isError;
 useEffect(()=>{
  if(!projectId||!profileId||!loaded||arrived.current)return;
  arrived.current=true;
  let live=true,tries=0,timer:ReturnType<typeof setTimeout>|undefined;
  const ask=()=>local<HomeListing>('orchestrator_arrive',scope).then(listing=>{
   client.setQueryData(chatKey,listing);
   if(live&&listing.waiting&&++tries<ARRIVE_TRIES)timer=setTimeout(ask,ARRIVE_RETRY);
  }).catch(()=>{/* the pickup stands */});
  void ask();
  return ()=>{live=false;clearTimeout(timer);};
 },[projectId,profileId,loaded]);

 // An answer that came back may have filed threads or named workstreams: the sidebar reads those.
 const wasThinking=useRef(false);
 useEffect(()=>{
  if(wasThinking.current&&!thinking){
   void client.invalidateQueries({queryKey:['project-ideas',projectId,profileId]});
   void client.invalidateQueries({queryKey:pickupKey});
   void onChanged();
  }
  wasThinking.current=thinking;
 },[thinking]);
 // The host finished sending a reply's jobs: the threads it made and filed show in the sidebar and the pickup.
 const wasHostBusy=useRef(false);
 useEffect(()=>{
  if(wasHostBusy.current&&!hostBusy){
   void onChanged();
   void client.invalidateQueries({queryKey:['project-ideas',projectId,profileId]});
   void client.invalidateQueries({queryKey:pickupKey});
  }
  wasHostBusy.current=hostBusy;
 },[hostBusy]);
 // A run that ended may have committed: recount what's on GitHub.
 const finishedBefore=useRef<number|null>(null);
 useEffect(()=>{
  const finished=found?pickupCounts(found).finished:null;
  if(finishedBefore.current!==null&&finished!==finishedBefore.current&&push.isSuccess)void client.invalidateQueries({queryKey:pushKey});
  finishedBefore.current=finished;
 },[found]);

 // Jobs start by themselves (19 Sep, #2): the host sends them now (16.4, item 7), so this only sets the composer's hint.
 const autostart=chat.data?.autostart!==false;
 // A job the host sent says it came from Home in its thread's next notification (#2). Remembered when this screen sees it
 // sent, at the time the host recorded; one sent while Home was closed has a plain note.
 useEffect(()=>{
  for(const entry of entries)for(const part of entry.parts??[])
   if(part.state==='sent'&&part.channel_id&&typeof part.sent_at==='number'&&Date.now()/1000-part.sent_at<600)rememberHomeJob(part.channel_id,part.sent_at);
 },[entries]);
 // The chat opens at its end and follows what you write, as a chat does. Direction A moved the pickup into the pane,
 // which never scrolls, so the 18 Sep rule (open at the top, on the pickup) no longer applies.
 useEffect(()=>{
  const node=log.current;
  if(!node||view!=='request')return;
  node.scrollTop=node.scrollHeight;
 },[wrote,talk.length,requestId]);
 // When the answer lands it replaces the step line in place. If you'd scrolled away, nothing moves. If the step line was
 // in view and the reply fits the window, the chat stays at its end, so the receipt and the job rows under a short
 // answer aren't left below the fold. A reply taller than the window keeps its top where the step line was, the way
 // ChatGPT and Claude anchor a reply, rather than jumping to its end (20 Sep review).
 const wasReplyThinking=useRef(replyThinking);
 useLayoutEffect(()=>{
  const landed=wasReplyThinking.current&&!replyThinking;
  wasReplyThinking.current=replyThinking;
  const node=log.current;
  if(!landed||!node)return;
  const reply=[...node.querySelectorAll<HTMLElement>('.dv-msg-dervo')].at(-1);
  if(!reply)return;
  const box=node.getBoundingClientRect(),row=reply.getBoundingClientRect();
  if(row.top<box.top||row.top>=box.bottom)return;
  if(row.height<=node.clientHeight)node.scrollTop=node.scrollHeight;
 },[replyThinking]);

 const root=useRef<HTMLElement>(null);
 // The thread open in the panel where Home's chat was (16.2, #30), the row whose peek shows, and Home's own width: under
 // PANEL_SIDE_AT the panel takes the whole of Home and Catch up waits behind Esc.
 const [threadId,setThreadId]=useState<string|null>(null);
 const [peek,setPeek]=useState<{id:string;row:DOMRect}|null>(null);
 const [notice,setNotice]=useState('');
 // A request to put the cursor in the panel's reply box (Answer). Only the thread it was made for takes it: the panel
 // mounts again for each thread, and a request left standing took every later Up and Down into the box (dd #21).
 const [composerFocus,setComposerFocus]=useState(0);
 const lastPanel=useRef<PickupItem|null>(null);
 const [narrow,setNarrow]=useState(false);
 useEffect(()=>{
  const node=root.current;
  if(!node||typeof ResizeObserver==='undefined')return;
  const watch=new ResizeObserver(entries=>{const width=entries[0]?.contentRect.width;if(width)setNarrow(width<PANEL_SIDE_AT);});
  watch.observe(node);
  return ()=>watch.disconnect();
 },[]);

 // ---------------------------------------------------------------- images, the thread composer's flow under Home's key
 const imageScope=useMemo(()=>({projectId,channelId:homeImageChannel(projectId)}),[projectId]);
 const [images,setImages]=useState<Preview[]>([]);
 const [imagesReady,setImagesReady]=useState(false);
 const [imageBusy,setImageBusy]=useState(false);
 const [imageError,setImageError]=useState('');
 const fileInput=useRef<HTMLInputElement>(null);
 const imagesRef=useRef(images);imagesRef.current=images;
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return ()=>{alive.current=false;};},[]);
 // One read on opening: a host that keeps images for Home answers with the drafts left here; one that doesn't refuses,
 // and the paperclip stays hidden rather than promising something that can't be sent.
 useEffect(()=>{
  let live=true;
  setImagesReady(false);setImages([]);
  imageRequest<{images:LocalImage[]}>(imageScope,'image_drafts').then(async result=>{
   if(!live)return;
   const rows:Preview[]=result.images.filter(row=>row.state==='ready');
   setImages(rows);setImagesReady(true);
   for(const row of rows){
    const preview=await tilePreview(imageScope,row).catch(()=>null);
    if(live&&preview)setImages(current=>current.map(image=>image.id===row.id?preview:image));
   }
  }).catch(()=>{if(live)setImagesReady(false);});
  return ()=>{live=false;};
 },[imageScope]);
 async function choose(files:File[]){
  if(!imagesReady||imageBusy||!files.length)return;
  if(imagesRef.current.length+files.length>4){setImageError(t('Attach up to four images. Remove one before adding another.'));return;}
  setImageBusy(true);setImageError('');
  try{
   for(const file of files){
    const bytes=await prepareImage(file);
    if(!alive.current)return;
    // Made while the chunks go, so the first one isn't held for it.
    const thumb=thumbnailOf(file);
    const name=file.name.replace(/[\\/\x00-\x1f]/g,'_').slice(0,60)||'Screenshot.png';
    const row=await uploadImage(imageScope,name,bytes,crypto.randomUUID(),()=>alive.current,
     progress=>{if(alive.current)setImages(current=>current.some(image=>image.id===progress.id)?current.map(image=>image.id===progress.id?{...image,...progress}:image):[...current,progress]);});
    // The picture follows when it's ready; the next file and the send don't wait for it.
    void thumb.then(made=>tilePreview(imageScope,row,made)).then(preview=>{if(alive.current)setImages(current=>current.map(image=>image.id===row.id?preview:image));}).catch(()=>undefined);
   }
  }catch(error){if(alive.current)setImageError(error instanceof Error?error.message:t('The image couldn’t upload. Try again.'));}
  finally{if(alive.current)setImageBusy(false);}
 }
 async function removeImage(id:string){
  if(imageBusy)return;
  setImageBusy(true);setImageError('');
  try{await imageRequest(imageScope,'image_discard',{id});if(alive.current)setImages(current=>current.filter(row=>row.id!==id));}
  catch{if(alive.current)setImageError(t('The image couldn’t be removed. Check your attachments before sending.'));}
  finally{if(alive.current)setImageBusy(false);}
 }

 /** Your Send or Send again on one job row: the host sent it once already, or never did (a reply from before 16.4), so
  * this goes as your click with the job's own request id, and a job that did land isn't sent twice. */
 async function run(reply:HomeEntry,only?:number,clicked=false){
  const parts=(reply.parts??[]).filter(part=>part.state!=='sent'&&(only===undefined||part.index===only));
  setSending(previous=>new Set(previous).add(reply.id));
  try{
   for(const part of parts){
    setStarting(previous=>({...previous,[reply.id]:{index:part.index,text:startingLine(part)}}));
    const outcome=await sendJob(part,{...scope,clientId:reply.id,clicked,launch:reply.launch??null},local);
    if(outcome.state==='sent'&&outcome.channelId)rememberHomeJob(outcome.channelId);
    try{await local('orchestrator_result',{...scope,entryId:reply.id,part:part.index,...outcome});}
    catch(error){setFailure(String(error instanceof Error?error.message:error));}
    await client.invalidateQueries({queryKey:chatKey});
   }
  }finally{
   setStarting(previous=>{const next={...previous};delete next[reply.id];return next;});
   setSending(previous=>{const next=new Set(previous);next.delete(reply.id);return next;});
   await onChanged();
   await client.invalidateQueries({queryKey:['project-ideas',projectId,profileId]});
   await client.invalidateQueries({queryKey:pickupKey});
  }
 }

 // ---------------------------------------------------------------- Start on an offer (beta.16.1, item 9)
 // Only the offer's id goes to the host, which starts its own record as offered and writes the reply with the job. That
 // job is then sent by the host as your click (16.4, item 7). No offer text passes through here, so none of it can become a
 // message of yours (onHandOff sends its text as one).
 const [offering,setOffering]=useState('');
 async function startOffer(entry:HomeEntry,offer:HomeOffer){
  if(offering||sending.size||replyThinking)return;
  setOffering(offer.id);setFailure('');
  try{
   const listing=await local<HomeListing>('orchestrator_offer',{...scope,entryId:entry.id,offerId:offer.id,clientId:crypto.randomUUID()});
   // The host sends the job as your click (take_offers marks it), even if you leave; this screen only shows it going.
   client.setQueryData(chatKey,listing);
   setWrote(value=>!value);
  }catch(error){setFailure(String(error instanceof Error?error.message:error));}
  finally{setOffering('');}
 }

 // ---------------------------------------------------------------- Stop on a job Home started (23 Sep, UX H2)
 // Jobs still start on their own (19 Sep, #2); this is the undo, where the job appeared. The same stop as the palette's
 // "Stop <thread>": the thread's working tasks, each marked as stopping so no other Stop sends it twice.
 const [stopping,setStopping]=useState<Set<string>>(new Set());
 const forget=(channelId:string)=>setStopping(previous=>{const next=new Set(previous);next.delete(channelId);return next;});
 async function stopJob(channelId:string){
  if(stopping.has(channelId))return;
  setStopping(previous=>new Set(previous).add(channelId));
  const failures:string[]=[];
  try{
   const rows=await local<CodingTask[]>('tasks',{projectId,channelId});
   for(const target of conversationStopTargets(undefined,rows,[])){
    try{await local(target.operation,{projectId,channelId,...target.args});markStopping([rows.find(row=>row.id===target.args.taskId)??(target.args.taskId?{id:target.args.taskId}:null)]);}
    catch(error){failures.push(String(error instanceof Error?error.message:error));}
   }
  }catch(error){failures.push(String(error instanceof Error?error.message:error));}
  if(failures.length){setFailure(failures.join(' '));forget(channelId);}
  await onChanged();
 }
 // A thread that stopped running is done stopping: the next run in it gets its own Stop.
 useEffect(()=>{
  setStopping(previous=>{const next=new Set([...previous].filter(id=>activity.get(id)?.running));return next.size===previous.size?previous:next;});
 },[conversations]);

 const signedOut=wantsSignIn(account.data);
 // The shell shows its own account banner with a Sign in button at the top of every screen. One Sign in per screen: the
 // composer's button shows only when no banner is there (an older shell), or when the shell wired Home's own route.
 const [bannerShown,setBannerShown]=useState(false);
 useEffect(()=>{
  if(!signedOut){setBannerShown(false);return;}
  const look=()=>setBannerShown(!!document.querySelector('[aria-label="Dervo account access"] button'));
  look();
  const timer=setTimeout(look,300);
  return ()=>clearTimeout(timer);
 },[signedOut]);
 const signInButton=signedOut&&(!!onSignIn||!bannerShown);
 // Where send() was called from, and what's in the box now, for the answer that lands after you may have moved on.
 const place=useRef({view,requestId});place.current={view,requestId};
 const typedNow=useRef(text);typedNow.current=text;
 /** Enter sends at once (beta.18): your message shows in the chat and the box clears before the host answers, which
  * takes as long as starting the model call and rebuilding the chat. The step line sits under it straight away. When the
  * host's listing lands, its copy takes over the same rows (``handed``) and the chat follows the request it made. */
 /** ``voice``: a spoken request (HomeVoice.tsx). It brings its own clientId, the utterance id, so a replay after a crash
  * is the same message to the host; it never touches the box or the images; and a busy Home answers 'busy' quietly so
  * voice can send it once Dervo is free, in the order you said them. */
 async function send(words=text.trim(),voice?:{clientId:string}):Promise<SendOutcome>{
  if(!voice&&onFeedback&&isFeedbackCommand(words)){onFeedback();return 'sent';}
  if(!words||signedOut)return 'failed';
  // A message sent while Dervo is still answering the last one goes now and waits its turn on the host, which answers it
  // with the last answer in view (8 Oct, Bryce: a rapid stream of messages, as Claude Projects takes them). Only the
  // moment this window is itself handing a message or a job over still says wait.
  if(sendBusy.current||sending.size){if(!voice)setFailure(WAIT_HINT());return 'busy';}
  if(launch.problem&&!bugDraft(words)){setFailure(launch.problem);return 'failed';}
  if(!voice&&(imageBusy||images.some(row=>row.state!=='ready'))){setFailure(t('Finish uploading your images, or remove one, before sending.'));return 'failed';}
  setFailure('');
  const attached=voice?[]:images,sent=attached.map(row=>row.id);
  const clientId=voice?.clientId??crypto.randomUUID(),key=`sending:${clientId}`,asked=view==='request'?requestId:null;
  sendBusy.current=true;
  startedHere.current.add(`${key}:reply`);
  setOutgoing({key,text:words,images:sent,requestId:asked,at:Date.now()/1000,state:'sending'});
  setView('request');setWrote(true);
  if(!voice&&words===text.trim()){setText('');delete drafts.current[requestId||'new'];setIgnoreWords(false);}
  if(sent.length)setImages(current=>current.filter(row=>!sent.includes(row.id)));
  if(!voice)keepFocus();
  try{
   // A bug: line starts no job, so it carries no choice. Anything else carries yours when you made one.
   const choice=bugDraft(words)?null:launch.choice;
   const listing=await local<HomeListing>('orchestrator_send',{...scope,text:words,requestId:asked,clientId,...(sent.length?{images:sent}:{}),...(choice?{launch:choice}:{})});
   for(const entry of listing.entries)if(entry.kind==='reply'&&entry.state==='thinking')startedHere.current.add(entry.id);
   const newest=[...listing.entries].reverse().find(entry=>entry.kind==='you'),at=newest?listing.entries.indexOf(newest):-1;
   if(newest){
    handed.current.set(newest.id,key);
    const reply=listing.entries[at+1];
    if(reply?.kind==='reply')handed.current.set(reply.id,`${key}:reply`);
   }
   client.setQueryData(chatKey,listing);
   // The chat moves over to the host's copy once it renders (the layout effect below), never a frame before.
   if(newest)setOutgoing(current=>current?.key===key?{...current,landed:{id:newest.id,requestId:newest.request_id||newest.id}}:current);
   else setOutgoing(current=>current?.key===key?null:current);
   return 'sent';
  }catch(error){
   // A spoken request that didn't go isn't put in the box: it stays in voice's journal, and voice says so.
   if(voice){const unsure=sendFailure(String(error instanceof Error?error.message:error))==='unsure';if(unsure)setOutgoing(current=>current?.key===key?{...current,state:'unsure'}:current);else setOutgoing(current=>current?.key===key?null:current);return unsure?'unsure':'failed';}
   sendFailed(error,{key,text:words,attached,requestId:asked});
   return 'failed';
  }
  finally{sendBusy.current=false;}
 }
 const sendRef=useRef(send);sendRef.current=send;
 const voiceSend=useMemo(()=>(words:string,spoken:{clientId:string})=>sendRef.current(words,spoken),[]);
 const voice=useHomeVoice({entries,onSend:voiceSend,scope:`${projectId}:${profileId}`});
 useTalkKey(voice,!signedOut);
 /** The one place a refused send lands, so the bridge's two busy answers (b18/stopping-host) only need sendFailure().
  * Not sent: the words go back in the box (or stay on screen marked not sent, when you've typed since) and the images
  * back in the tray. May still go through: the message stays as sent and isn't offered again, so it can't go twice. */
 function sendFailed(error:unknown,lost:{key:string;text:string;attached:Preview[];requestId:string|null}){
  const detail=String(error instanceof Error?error.message:error);
  if(sendFailure(detail)==='unsure'){
   setOutgoing(current=>current?.key===lost.key?{...current,state:'unsure',detail}:current);
   void client.invalidateQueries({queryKey:chatKey});
   return;
  }
  if(lost.attached.length)setImages(current=>[...lost.attached.filter(row=>!current.some(image=>image.id===row.id)),...current]);
  setFailure(detail);
  if(typedNow.current.trim()){setOutgoing(current=>current?.key===lost.key?{...current,state:'unsent',detail}:current);return;}
  setText(lost.text);
  setOutgoing(current=>current?.key===lost.key?null:current);
  keepFocus();
  // A new request that didn't go: back to Home, where you wrote it, rather than an empty request.
  if(lost.requestId===null&&place.current.view==='request'&&place.current.requestId===null)setView('home');
 }
 function putBack(words:string){
  setText(current=>current.trim()?`${current}\n${words}`:words);
  setOutgoing(null);setFailure('');
  keepFocus(true);
 }
 /** The box keeps the cursor through a send. A click on Send (or Space on it) leaves focus on a button that disables as the
  * box clears, and a button that takes itself away drops focus to the page: put it back in the box. ``force``: the caller
  * removed what had focus. */
 function keepFocus(force=false){
  const active=document.activeElement;
  if(force||!active||active===document.body||active.closest('.dv-btn-send'))document.getElementById('dervo-home-ask')?.focus();
 }
 // The Enter that was held back is no longer waiting on anything once Dervo has answered.
 useEffect(()=>{if(!replyThinking&&!sending.size)setFailure(current=>current===WAIT_HINT()?'':current);},[replyThinking,sending.size]);
 // The host's copy is in the chat: show its request, if you're still where you sent from, before the frame paints.
 useLayoutEffect(()=>{
  const landed=outgoing?.landed;
  if(!landed||!entries.some(entry=>entry.id===landed.id))return;
  if(place.current.view==='request'&&place.current.requestId===outgoing.requestId)setRequestId(landed.requestId);
  setOutgoing(null);
 },[entries,outgoing]);
 // A send that may still go through: look for it in the chat every second and a half, for two minutes.
 useEffect(()=>{
  if(outgoing?.state!=='unsure'||outgoing.landed)return;
  const found=entries.find(entry=>entry.kind==='you'&&entry.text===outgoing.text&&entry.created_at>=outgoing.at-5);
  if(found){
   handed.current.set(found.id,outgoing.key);
   setOutgoing({...outgoing,landed:{id:found.id,requestId:found.request_id||found.id}});
   return;
  }
  const timer=setInterval(()=>void client.invalidateQueries({queryKey:chatKey}),1500);
  const stop=setTimeout(()=>clearInterval(timer),120000);
  return ()=>{clearInterval(timer);clearTimeout(stop);};
 },[outgoing,entries]);

 // ---------------------------------------------------------------- the question waiting on you (#16, direction A)
 // The newest ready reply may carry one. `pendingQuestion` decides that, and `readQuestion` inside it rebuilds the
 // payload from the fields it knows, so an option can't carry a destination or a grant from the model. Your pick comes
 // back as words and goes out through send() as your own message: the tray has no other route, and Home's own reopen
 // ("Reopen #9") is read from your message by the orchestrator, never from a record. The tray is turned off, rather than
 // hidden, whenever send() would refuse, so a key is never taken and dropped.
 const question=pendingQuestion(talk);
 const questionCopy=question?questionComposerCopy(question,text):null;
 const questionOff=working||imageBusy||signedOut;

 function openThread(item:PickupItem,focus:boolean){
  onOpen(item.id);
  if(focus)requestAnimationFrame(()=>document.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus());
 }
 function signIn(trigger:HTMLElement){
  if(onSignIn){onSignIn(trigger);return;}
  // Without a route of its own, Home hands the click to the shell's account banner, which opens the sign-in steps.
  document.querySelector<HTMLElement>('[aria-label="Dervo account access"] button')?.click();
 }

 // ---------------------------------------------------------------- the push, one at a time, settled before any retry
 const [confirming,setConfirming]=useState(false);
 const [pushing,setPushing]=useState<PushRecord|null>(null);
 const [pushFailure,setPushFailure]=useState('');
 const [pushBusy,setPushBusy]=useState(false);
 const line=pushLine(push.data);
 // A push from an earlier session that is still running or unknown is picked up from the preview's latest record.
 const latest=push.data?.latest;
 useEffect(()=>{
  if(pushing===null&&latest&&(latest.state==='running'||latest.state==='unknown'))setPushing(latest);
 },[latest?.id,latest?.state]);
 // While a push runs, ask the host how it went; a running record older than the timeout is the host's to call unknown.
 useEffect(()=>{
  if(pushing?.state!=='running')return;
  const timer=setInterval(()=>{
   local<PushRecord>('push_status',{projectId}).then(record=>{
    if(!record||record.state==='running')return;
    setPushing(record);
    if(record.state==='pushed')void client.invalidateQueries({queryKey:pushKey});
   }).catch(()=>{/* keep asking */});
  },1500);
  return ()=>clearInterval(timer);
 },[pushing?.state,projectId]);
 async function startPush(){
  const preview=push.data;
  const token=preview?.confirm_token??preview?.confirmToken;
  if(!preview||!token||pushBusy)return;
  setPushBusy(true);setPushFailure('');
  try{
   const record=await local<PushRecord>('push_start',{projectId,confirmToken:token,setUpstream:!preview.upstream});
   setPushing(record?.state?record:{state:'running'});
   setConfirming(false);
  }catch(error){setPushFailure(String(error instanceof Error?error.message:error));}
  finally{setPushBusy(false);}
 }
 async function recount(){
  if(pushBusy)return;
  setPushBusy(true);
  try{
   const record=await local<PushRecord>('push_recount',{projectId});
   setPushing(record);
   if(record.state==='pushed'||record.state==='not_pushed')void client.invalidateQueries({queryKey:pushKey});
  }catch(error){setPushFailure(String(error instanceof Error?error.message:error));}
  finally{setPushBusy(false);}
 }
 // ---------------------------------------------------------------- what Dervo did on its own
 // Undo one change a reply made. The host answers with the whole chat; a refusal is its own sentence, shown in Undo's place.
 const [undoing,setUndoing]=useState('');
 const [undoFailed,setUndoFailed]=useState<Record<string,string>>({});
 async function undoChange(entry:HomeEntry,change:number){
  const key=`${entry.id}:${change}`;
  if(undoing)return;
  setUndoing(key);
  try{
   const listing=await local<HomeListing>('orchestrator_undo',{...scope,entryId:entry.id,change});
   client.setQueryData(chatKey,listing);
   await onChanged();
   await client.invalidateQueries({queryKey:['project-ideas',projectId,profileId]});
  }catch(error){setUndoFailed(previous=>({...previous,[key]:String(error instanceof Error?error.message:error)}));}
  finally{setUndoing('');}
 }
 // Move a routed reply's job before it goes (RouteMove). A refusal (it went already) shows in Move's place.
 const [moving,setMoving]=useState('');
 const [moveOpen,setMoveOpen]=useState('');  // the reply whose Move menu is open: it stays while you choose
 const [moveFailed,setMoveFailed]=useState<Record<string,string>>({});
 async function moveRouted(entry:HomeEntry,to:string){
  if(moving)return;
  setMoving(entry.id);
  try{
   const listing=await local<HomeListing>('orchestrator_move',{...scope,entryId:entry.id,to});
   client.setQueryData(chatKey,listing);
   await onChanged();
  }catch(error){setMoveFailed(previous=>({...previous,[entry.id]:String(error instanceof Error?error.message:error)}));}
  finally{setMoving('');}
 }
 // ---------------------------------------------------------------- brought in from this Mac (group 10)
 // One line while the batch this project came in with can still be taken back: nothing has run or been written in any
 // of its projects. Undo removes every project of the batch; the folders and the tools' histories stay as they are.
 const brought=useQuery({queryKey:['onboarding-batch',projectId],enabled:!!projectId,queryFn:()=>batchFor(projectId),retry:false,staleTime:15000});
 const [undoingBatch,setUndoingBatch]=useState(false),[batchFailure,setBatchFailure]=useState('');
 async function undoBrought(){
  const batch=brought.data;
  if(!batch||undoingBatch)return;
  setUndoingBatch(true);setBatchFailure('');
  try{await undoBatch(batch.batchId);await client.invalidateQueries({queryKey:['local-projects']});}
  catch(error){setBatchFailure(String(error instanceof Error?error.message:error));void brought.refetch();}
  finally{setUndoingBatch(false);}
 }
 // Work folders onboarding kept waiting for a work account (b16-work): said by name, with the one way on. Once a work
 // account is signed in, Bring them in makes them Work projects on it, in a batch of their own that Undo can take back.
 const waiting=useQuery({queryKey:['onboarding-waiting'],queryFn:waitingWork,retry:false,staleTime:15000});
 const waitingNames=waiting.data?.names??[];
 const workSide=useWorkSide(waitingNames.length?profiles.data??[]:[]);
 const [bringingWaiting,setBringingWaiting]=useState(false),[waitingFailure,setWaitingFailure]=useState('');
 async function bringWaitingIn(){
  if(bringingWaiting)return;
  setBringingWaiting(true);setWaitingFailure('');
  try{await bringWaiting(await workSide.prepare());await client.invalidateQueries({queryKey:['local-projects']});await waiting.refetch();}
  catch(error){setWaitingFailure(String(error instanceof Error?error.message:error));}
  finally{setBringingWaiting(false);}
 }
 // ---------------------------------------------------------------- quiet threads put away today (b16/lifecycle)
 // One line in the catch-up on the day a tidy happens, counting what's still put away. Its key sits under the
 // sidebar's archived list, so the sidebar's tidy, Undo and the fold's restores refresh it. See them opens that fold.
 const tidied=useQuery({queryKey:['archived-conversations',projectId,'tidied-today'],enabled:!!projectId,retry:false,queryFn:()=>tidiedToday(projectId)});
 // The days that tidy used, as the host recorded them; the setting only for a tidy recorded before that (behaviour
 // review N7, 25 Sep: switching to Never after a tidy made the line say "two weeks").
 const usedDays=typeof tidied.data?.days==='number'?tidied.data.days:undefined;
 const tidyDays=useQuery({queryKey:['tidy-setting',projectId],enabled:!!tidied.data?.count&&usedDays===undefined,retry:false,queryFn:()=>tidySetting(projectId)});
 const tidyLine=typeof tidied.data?.count==='number'?homeTidyLine(tidied.data.count,usedDays??(tidyDays.data?tidyDays.data.days??tidyDays.data.default:undefined)):'';
 const lost=lostRunsLine(hostStatus.data,projectId);
 const lostTasks=(hostStatus.data?.lostRuns??[]).filter(run=>run.projectId===projectId&&run.channelId&&run.taskId);
 const lostKey=lost?`${projectId}:${lost.text}:${lostTasks.map(run=>run.taskId).join(',')}`:'';
 const [,hideLost]=useState(0);
 const lostShown=!!lost&&!lostSeen.has(lostKey);
 function doneWithLost(){lostSeen.add(lostKey);hideLost(n=>n+1);}
 const arrivalFailed=failedArrival(entries,found?.since??null);

 // ---------------------------------------------------------------- a card that arrives while you're here
 // The needs card enters once, when it arrives with Home on screen, never on opening Home (design review, 23 Sep, the
 // same "things appear and fade in" Bryce cut from the site). The cards there once the first fresh read lands are what
 // was already waiting; only one that turns up after that moves. Laid out before paint, so it never shows then jumps.
 const knownCards=useRef<Set<string>|null>(null);
 const cardArrived=useRef(new Map<string,number>());
 useLayoutEffect(()=>{
  if(!found||(knownCards.current===null&&pickup.isFetching))return;
  const ids=sections(found.items).needs.map(item=>item.id);
  if(knownCards.current===null){knownCards.current=new Set(ids);return;}
  const fresh=ids.filter(id=>!knownCards.current!.has(id));
  if(!fresh.length)return;
  for(const id of fresh){knownCards.current.add(id);cardArrived.current.set(id,Date.now());}
  tick(n=>n+1);
 },[found,pickup.isFetching]);
 const entering=(id:string)=>{const at=cardArrived.current.get(id);return at!==undefined&&Date.now()-at<ENTER_FOR;};

 function pushAgain(){setPushing(null);setPushFailure('');setConfirming(false);void client.invalidateQueries({queryKey:pushKey});}

 // ---------------------------------------------------------------- what's on screen
 const now=Date.now()/1000;
 const compact=compactFor(talk,wrote,now);
 const lastReply=[...talk].reverse().find(entry=>entry.kind==='reply'&&!entry.news);
 const head=found?heading(found,false,now,conversations.length):{title:t('Where you left off'),caption:''};
 // Every thread archived: the caption counts them, since "No threads yet" would contradict the line under it.
 if(found&&archived>0&&!conversations.length&&!pickupCounts(found).threads)head.caption=t('{archived} archived',{archived});
 const parts=found?sections(found.items):{needs:[],running:[],since:[]};
 const bk=found?buckets(found.items):{waiting:[],working:[],idle:[],resolved:[]};
 // The threads have been read, or couldn't be: what waits on you can be drawn without the threads landing above it.
 const read=!!found||pickup.isError;
 const openId=parts.needs[0]?.id??null;
 const accentId=openId;
 const paneShown=true;

 // ---------------------------------------------------------------- the useful row, its peek and the thread panel (#30)
 const order=found?paneOrder(found.items):[];
 const stepOf=(item:PickupItem)=>item.kind==='running'?(activity.get(item.id)?.step??item.now):null;
 const whenOf=(item:PickupItem)=>{
  const since=item.kind==='running'?(activity.get(item.id)?.since??item.running_since):null;
  return item.kind==='running'&&since?lasted(since,now):item.moved?spoken(item.moved,now):'';
 };
 const peekId=`dervo-home-peek-${projectId}`;
 const peekTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const quietFocus=useRef(false);
 function hoverPeek(id:string,row:HTMLElement){
  if(threadId)return;
  clearTimeout(peekTimer.current);
  peekTimer.current=setTimeout(()=>setPeek({id,row:row.getBoundingClientRect()}),PEEK_AFTER);
 }
 function closePeek(){clearTimeout(peekTimer.current);setPeek(null);}
 // Keyboard focus opens it at once. A click focuses the row too, and that opens the panel, not a peek.
 function focusPeek(id:string,button:HTMLElement){
  if(threadId||quietFocus.current){quietFocus.current=false;return;}
  let keyboard=true;
  try{keyboard=button.matches(':focus-visible');}catch{/* an engine without :focus-visible: treat it as keyboard */}
  if(keyboard)setPeek({id,row:(button.closest('.dv-row')??button).getBoundingClientRect()});
 }
 useEffect(()=>()=>clearTimeout(peekTimer.current),[]);
 useEffect(()=>{
  if(!peek)return;
  const onKey=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();closePeek();}};
  const onScroll=()=>closePeek();
  document.addEventListener('keydown',onKey);
  document.addEventListener('scroll',onScroll,true);
  return ()=>{document.removeEventListener('keydown',onKey);document.removeEventListener('scroll',onScroll,true);};
 },[peek]);
 function openPanel(id:string,focus=false){closePeek();setNotice('');setThreadId(id);setComposerFocus(n=>focus?n+1:0);reminders.cameBack(id);}
 function stepTo(id:string){setNotice('');setComposerFocus(0);setThreadId(id);}
 function closePanel(){
  const id=threadId;
  setThreadId(null);setNotice('');
  // Back on the row you came from, without its peek springing open.
  requestAnimationFrame(()=>{
   const button=rowNode(root.current,id,'')?.querySelector<HTMLElement>('button');
   if(button){quietFocus.current=true;button.focus({preventScroll:true});}
  });
 }
 // Acting on one thread opens the next thing that needs you, once this one no longer does.
 async function acted(id:string){
  const fresh=await pickup.refetch();
  const items=fresh.data?.items??[];
  const current=items.find(item=>item.id===id);
  if(current&&(asking(current)||current.needs))return;
  const next=nextNeeding(paneOrder(items),id);
  const name=items.find(item=>item.id===next)?.name;
  setNotice(next&&name?t('Next: {name}',{name}):t('Nothing else needs you.'));
  if(next){setComposerFocus(0);setThreadId(next);}
 }
 useEffect(()=>{
  if(!threadId)return;
  const onKey=(event:KeyboardEvent)=>{
   if(event.defaultPrevented||event.metaKey||event.ctrlKey||event.altKey)return;
   if(document.querySelector('[role="dialog"],[role="menu"],[role="listbox"]'))return;
   const target=event.target as HTMLElement|null;
   if(target?.closest?.('textarea,input,select,[contenteditable="true"]'))return;
   if(event.key==='Escape'){event.preventDefault();closePanel();return;}
   if(event.key==='ArrowDown'||event.key==='ArrowUp'||event.key==='j'||event.key==='k'){
    const next=stepThread(order,threadId,event.key==='ArrowDown'||event.key==='j'?1:-1);
    if(next){event.preventDefault();stepTo(next);}
   }
  };
  document.addEventListener('keydown',onKey);
  return ()=>document.removeEventListener('keydown',onKey);
 },[threadId,order.map(item=>item.id).join(',')]);
 useEffect(()=>{
  if(threadId)rowNode(root.current,threadId,'.dv-home-side ')?.scrollIntoView({block:'nearest'});
 },[threadId]);
 const panelItem=threadId?found?.items.find(item=>item.id===threadId)??(lastPanel.current?.id===threadId?lastPanel.current:null):null;
 if(panelItem)lastPanel.current=panelItem;

 const paneRow=(item:PickupItem,open:boolean)=>{
  const accent=item.id===accentId;
  const running=item.kind==='running';
  const action=actionOf(item,open);
  const when=whenOf(item);
  const sentence=rowSentence(item);
  const name=<>{item.name}{item.private?<span className="dv-caption"> {t('· private')}</span>:null}</>;
  // The card that asks you: its sentence, the question itself, and Answer, which opens it here with the cursor in its
  // composer. Remind me tomorrow is the quiet third (9 Oct): not now, but don't let it sit.
  if(open)return <article key={item.id} className="dv-row dv-open" data-kind={item.kind} data-row={item.id} data-enter={entering(item.id)||undefined}>
   <StatusGlyph state={glyphOf(item)} accent={accent}/>
   <div>
    <div className="dv-row-name">{name}</div>
    <div className="dv-row-line">{item.result??openLine(item,now)}</div>
    {item.question&&item.next&&<p className="dv-row-question">{item.result?item.next:<>“{item.next}”</>}</p>}
    {action&&<div className="dv-row-actions">
     <button type="button" className="dv-btn dv-btn-primary" onClick={()=>openPanel(item.id,action.focus)}>{action.label}</button>
     <button type="button" className="dv-btn dv-btn-ghost" onClick={()=>onOpen(item.id)}>{t('Open thread')}</button>
     <RemindLater control={reminders} channelId={item.id}/>
    </div>}
   </div>
   <div className="dv-row-end">{when&&<span className="dv-when">{when}</span>}</div>
  </article>;
  // A reminder of yours is one more part of the line: one still to come, or one due today on a thread that waits on you
  // (it has no row of its own under Reminders then, so the thread isn't listed twice).
  const ahead=reminders.ahead(item.id),today=!ahead&&bk.waiting.some(other=>other.id===item.id)&&reminders.dueMine(item.id);
  const own=rowMeta(item,accent,stepOf(item));
  // A run with nothing else to say still says Working, ahead of the reminder (state review, 9 Oct).
  const meta:MetaPart[]=[...(running&&!own.length&&!sentence?[{text:t('Working'),tone:'live' as const}]:own),
   ...(ahead||today?[{text:t('Reminder set for {day}',{day:ahead?aheadLine(ahead.on):t('today')}),tone:null}]:[])];
  const selected=item.id===threadId;
  // Every other row: one sentence and a quiet line. The whole row opens the thread here; hover or focus peeks.
  return <article key={item.id} className="dv-row dv-home-row" data-kind={item.kind} data-row={item.id} data-selected={selected||undefined}
   onMouseEnter={event=>hoverPeek(item.id,event.currentTarget)} onMouseLeave={closePeek}>
   <StatusGlyph state={glyphOf(item)} accent={accent}/>
   <div>
    <button type="button" className="dv-row-name dv-home-row-open" aria-current={selected?'true':undefined}
     aria-describedby={peek?.id===item.id?peekId:undefined} onClick={()=>openPanel(item.id)}
     onFocus={event=>focusPeek(item.id,event.currentTarget)} onBlur={closePeek}>{name}</button>
    {sentence&&<p className="dv-row-did">{sentence}</p>}
    {meta.length>0&&<p className="dv-row-meta">{meta.map((part,index)=><span key={index} data-tone={part.tone??undefined}>{part.text}</span>)}</p>}
   </div>
   <div className="dv-row-end">{when&&<span className="dv-when">{when}</span>}</div>
  </article>;
 };
 // Beta.17 (#27): a parent's subthreads under it in each section, each opening here as any row does (homeFamilies.tsx).
 const paneKids=(items:PickupItem[],open:string|null,where:'needs'|'running'|'since')=>paneSheet(items,conversations,{open,where,row:paneRow,onOpen:openPanel,
  kid:item=>({id:item.id,name:item.name,glyph:glyphOf(item),line:rowSentence(item)??item.did,when:whenOf(item)})});
 // One bucket: its name and count, then its rows. An empty Waiting on you or Working says what lands there, once, quietly.
 // Resolved folds behind its header unless it's all there is, so finished work never pushes live work down. ``rows``:
 // what waits on you that isn't a thread's run, Home's own questions and reminders whose day has come (9 Oct), after the
 // threads and counted with them.
 const BUCKET_NAME={waiting:t('Waiting on you'),working:t('Working'),idle:t('Idle'),resolved:t('Resolved')};
 const BUCKET_WHERE={waiting:'needs',working:'running',idle:'since',resolved:'since'} as const;
 const bucket=(key:keyof Buckets,items:PickupItem[],open:string|null,empty:string,after:ReactNode=null,rows:ReactNode[]=[])=>{
  // Never folded beside an open thread: Up and Down walk every row there, so none of them may be hidden.
  const folds=!threadId&&key==='resolved'&&items.length>0&&(bk.waiting.length+bk.working.length+bk.idle.length)>0;
  const shut=folds&&!resolvedOpen;
  const count=items.length+rows.length;
  return <section key={key} className="dv-now-section dv-bucket" aria-label={BUCKET_NAME[key]} data-bucket={key} data-empty={count?undefined:''}>
   {folds?<button type="button" className="dv-label dv-now-label dv-bucket-head" aria-expanded={!shut} onClick={()=>setResolvedOpen(!resolvedOpen)}>
     <Chevron open={!shut}/>{BUCKET_NAME[key]}{' '}<span className="dv-now-count">{count}</span></button>
    :<div className="dv-bucket-bar"><div className="dv-label dv-now-label dv-bucket-head">{BUCKET_NAME[key]}{count>0&&<>{' '}<span className="dv-now-count">{count}</span></>}</div>
     {/* The Catch up screen takes what's waiting one decision at a time, each with its own actions. It replaces "Go
         through these" in the page head (Bryce, 8 Oct), and sits on the bucket it walks. */}
     {key==='waiting'&&items.length>0&&onCatchUp&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-bucket-act" onClick={event=>onCatchUp(event.currentTarget)}>{t('Open in Catch up')}</button>}
    </div>}
   {count?!shut&&<div className="dv-sheet">{items.length>0&&paneKids(items,open,BUCKET_WHERE[key])}{rows}</div>:empty&&<p className="dv-bucket-empty">{empty}</p>}
   {after}
  </section>;
 };
 // Home's own questions (a request it can't route without you), as rows of Waiting on you after the threads. Each
 // opens its request here.
 const openRouting=(id:string)=>{if(threadId)setThreadId(null);openRequest(id);};
 const waitingRows=unanswered.map(request=><article key={`ask:${request.id}`} className="dv-row dv-home-row" data-kind="routing">
   <StatusGlyph state="needs"/>
   <div>
    <button type="button" className="dv-row-name dv-home-row-open" aria-label={t('Answer to continue: {title}',{title:request.title})}
     onClick={()=>openRouting(request.id)}>{request.title}</button>
    <p className="dv-row-did">{requestQuestion(request)!.ask}</p>
    <p className="dv-row-meta"><span data-tone="ask">{t('Home asks you')}</span><span>{t('Answer to continue')}</span></p>
   </div>
   <div className="dv-row-end"><span className="dv-when">{spoken(request.entries.at(-1)?.created_at??request.createdAt,now)}</span></div>
  </article>);
 // Reminders whose day has come, after Waiting on you: Bryce's three things are what needs you, reminders and
 // suggestions, and a reminder is a note, not a thread blocked on you, so it's counted on its own (design review, 9 Oct:
 // one screen showed three different counts). Yours on a thread that already waits on you is said on that thread's
 // row instead, so the thread isn't listed twice. Opening a reminder's thread is all its row does.
 const reminderRows=reminders.due.filter(reminder=>!(reminder.mine&&bk.waiting.some(item=>item.id===reminder.channelId)))
  .map(reminder=><ReminderRow key={`reminder:${reminder.id}`} reminder={reminder} saving={reminders.busy===`done:${reminder.id}`}
   disabled={!!reminders.busy} onOpen={onOpen} onDone={row=>reminders.done(row,'row')}/>);
 const remindersList=(after:ReactNode)=>reminderRows.length>0&&<section key="reminders" className="dv-now-section dv-bucket" aria-label={t('Reminders')} data-bucket="reminders">
  <div className="dv-bucket-bar"><div className="dv-label dv-now-label dv-bucket-head">{t('Reminders')}{' '}<span className="dv-now-count">{reminderRows.length}</span></div></div>
  <div className="dv-sheet">{reminderRows}</div>
  {after}
 </section>;
 const peekItem=peek&&!panelItem?found?.items.find(item=>item.id===peek.id)??null:null;

 const pushBlock=line&&<div className="dv-push" data-testid="home-push">
  <div className="dv-push-line">
   <span className="dv-push-branch"><Branch/><b>{line.branch}</b></span>
   {pushing?.state==='running'?<span className="dv-caption" role="status">{t('Pushing…')}</span>
    :pushing?.state==='pushed'?<span className="dv-caption" role="status">{pushing.detail||t('Pushed to {target}.',{target:`${pushing.remote??push.data?.remote??'origin'}/${pushing.branch??line.branch}`})}</span>
    :pushing?.state==='unknown'?<><span className="dv-caption" role="alert">{pushing.detail||t('Push result unknown.')}</span><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={pushBusy} onClick={()=>void recount()}>{t('Check GitHub')}</button></>
    :pushing?.state==='failed'?<><span className="dv-caption" role="alert">{pushing.detail||t('The push didn’t go through.')}</span><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={pushAgain}>{t('Try again')}</button></>
    :pushing?.state==='not_pushed'?<><span className="dv-caption">{pushing.detail||t('GitHub doesn’t have it.')} {t('You can push again.')}</span><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={pushAgain}>{t('Push again')}</button></>
    :line.noRemote?<span className="dv-caption">{t('This project has no GitHub remote.')}</span>
    :line.canPush?<><span className="dv-caption">{line.count}</span>{!confirming&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>{setConfirming(true);setPushFailure('');}}>{t('Push')}</button>}</>
    :line.why?<span className="dv-caption">{line.why}</span>
    :null}
  </div>
  {confirming&&push.data&&(pushing===null||pushing.state==='failed'||pushing.state==='not_pushed')&&<div className="dv-push-confirm" role="group" aria-label={t('Push to GitHub')}>
   <p className="dv-push-question">{pushQuestion(push.data)}</p>
   {(push.data.commits??[]).length>0&&<ul className="dv-push-commits">{(push.data.commits??[]).map(commit=>{const row=commitLine(commit);
    return <li key={commit.sha}><code>{row.short}</code><span className="dv-push-subject">{row.subject}</span>{row.who&&<span className="dv-caption">{row.who}</span>}</li>;})}</ul>}
   {isMain(push.data.branch)&&<p className="dv-caption dv-push-note">{t('This goes straight to main on GitHub.')}</p>}
   {line.noUpstream&&<p className="dv-caption dv-push-note">{t('{branch} isn’t on GitHub yet. This sets it up as {target}.',{branch:line.branch,target:`${push.data.remote??'origin'}/${line.branch}`})}</p>}
   {line.behind>0&&<p className="dv-caption dv-push-note" role="alert">{t('GitHub has commits you don’t have. Pull in Terminal first.')}</p>}
   {pushFailure&&<p className="dv-caption dv-push-note" role="alert">{pushFailure}</p>}
   <div className="dv-row-actions">
    <button type="button" className="dv-btn dv-btn-primary" disabled={pushBusy||line.behind>0} onClick={()=>void startPush()}>{t('Push')}</button>
    <button type="button" className="dv-btn dv-btn-ghost" disabled={pushBusy} onClick={()=>setConfirming(false)}>{t('Not now')}</button>
   </div>
  </div>}
 </div>;

 const pane=<section className="dv-now" aria-label={head.title} data-open>
  <div className="dv-now-head">
   {/* The page title every page has (34px, like Knowledge and Settings), with what the moment is on its own line under
       it, as the landing page draws this screen (design review, 23 Sep, item 3). */}
   <div className="dv-now-title">
    {/* A greeting, as the Claude Projects panel opens (design, 8 Oct). The region keeps its name, Catch up. */}
    <h2 className="dv-display-1">{found?greeting():head.title}</h2>
    {head.caption&&<p className="dv-caption">{head.caption}</p>}
   </div>
   {/* The same destination at its second depth (F1, 20 Sep). Not a sidebar row: Home's whole job is catching you up,
       so two ways in would leave a new person asking what the difference is. "Go through these", not "Open in full":
       this pane is the glance and withholds actions, and what's on the other side is the queue where you act on them. */}
   {/* And only while there is something on the other side: with no records the queue is empty, and "Go through these"
       beside "No threads yet" or "22 threads, none started yet" promised a page with nothing on it (21 Sep). */}
   <div className="dv-now-tools">
    {/* "Go through these" left the head (Bryce, 8 Oct: it read as weird). The queue opens from Waiting on you as Open
        in Catch up, and from ⌘K. Your earlier requests are a quiet icon at the head's end. */}
    <button type="button" className="dv-btn dv-btn-quiet dv-now-history" aria-label={t('Recent requests')} title={t('Recent requests')}
     onClick={()=>{setHistoryPage(0);setView('recent');}}><History/></button>
   </div>
  </div>
  {paneShown&&<>
   {lostShown&&<ActivityLine className="dv-now-activity" text={lost!.text} live
    action={!onOpenTask&&lost!.channelId?{label:t('Open thread'),onClick:()=>{doneWithLost();onOpen(lost!.channelId!);}}:{label:t('Hide'),onClick:doneWithLost}}/>}
   {lostShown&&onOpenTask&&<section className="dv-now-section" aria-label={t('Tasks needing recovery')}>
    <p className="dv-caption">{t('These tasks still need checking, even if the thread has newer replies.')}</p>
    <ul>{lostTasks.map(run=><li key={run.taskId}><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-label={t('Open lost task {taskId} in {name}',{taskId:run.taskId,name:run.name})} onClick={event=>onOpenTask(run.channelId,run.taskId,event.currentTarget)}>{t('Check task in {name}',{name:run.name})}</button></li>)}</ul>
   </section>}
   {pickup.isError&&!found&&<p className="dv-now-lead" role="alert">{t('Your threads couldn’t be read just now.')}{' '}
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void pickup.refetch()}>{t('Try again')}</button></p>}
   {!found&&!pickup.isError&&<p className="dv-now-lead dv-muted">{t('Reading your threads…')}</p>}
   {found&&found.items.length>0&&<p className="dv-now-lead"><Inline text={found.lead}/></p>}
   {/* What needs you leads Home (9 Oct, Bryce and tester Harrison: reminders, suggestions and what needs you): your
       threads that wait on you, then Home's own questions; reminders whose day has come; live work; then what agents
       proposed that nobody started. The rest of the project comes after. All of it waits for the threads to be read
       (or not), so nothing lands above a list and pushes it down (design review, 9 Oct). */}
   {read&&(bk.waiting.length>0||waitingRows.length>0)&&bucket('waiting',bk.waiting,openId,'',<ReminderFailure control={reminders} place="card"/>,waitingRows)}
   {read&&remindersList(<ReminderFailure control={reminders} place="row"/>)}
   {found&&(bk.working.length>0||(typeof found.awake==='string'&&!!found.awake))&&bucket('working',bk.working,null,'',typeof found.awake==='string'&&found.awake?<p className="dv-caption dv-now-awake" data-testid="home-awake">{found.awake}</p>:null)}
   <HomeSuggestions projectId={projectId} profileId={profileId} shown={read} onOpen={onOpen} onOpenTask={onOpenTask}/>
   {found&&<>
    {tidyLine&&<ActivityLine className="dv-now-activity dv-now-tidied" text={tidyLine} action={{label:t('See them'),onClick:()=>void showArchived()}}/>}
    {arrival?.text&&<div className="dv-home-arrival">{blocks(arrival.text).map((block,index)=>block.kind==='p'?<p key={index} className="dv-read"><Inline text={block.text}/></p>:<ul key={index}>{block.items.map((item,n)=><li key={n}>{item}</li>)}</ul>)}</div>}
    {brought.data&&(brought.data.undoable||batchFailure)&&<div className="dv-ob-home" data-testid="home-brought-in">
     <ActivityLine className="dv-now-activity" text={batchFailure||broughtLine(brought.data)}
      action={brought.data.undoable&&!batchFailure?{label:undoingBatch?t('Undoing…'):t('Undo'),describe:t('Undo: remove every project brought in from this Mac'),disabled:undoingBatch,onClick:()=>void undoBrought()}:null}/>
    </div>}
    {waitingNames.length>0&&<div className="dv-ob-home" data-testid="home-work-waiting">
     <ActivityLine className="dv-now-activity" text={waitingFailure||waitingLine(waitingNames)}
      action={workSide.state==='ready'?{label:bringingWaiting?t('Bringing in…'):t('Bring them in'),describe:t('Bring in {names} on your work account',{names:waitingNames.join(', ')}),disabled:bringingWaiting,onClick:()=>void bringWaitingIn()}
       :onConnectedAccounts?{label:workSide.state==='signed_out'||workSide.state==='problem'?t('Sign in to your work account'):t('Add a work account'),onClick:()=>onConnectedAccounts(document.activeElement as HTMLElement)}:null}/>
    </div>}
    {pushBlock}
    {found.items.length===0&&(archived>0&&!conversations.length
     ?<div className="dv-quiet dv-now-quiet" data-testid="home-archived">
      <p className="dv-now-lead">{archivedLine(archived).what}</p>
      <p className="dv-caption">{archivedLine(archived).then}</p>
      <div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={showArchived}>{t('Show archived')}</button></div>
     </div>
     :<div className="dv-quiet dv-now-quiet">
      {/* Threads with no records: after a history read they are full of things you asked in the terminal, so "nothing
          has been asked" read as false on the one screen the import lands on. What's true is that Dervo hasn't run
          anything in them yet (21 Sep). */}
      <p className="dv-now-lead">{conversations.length?t('Nothing has run in Dervo yet.'):t('Nothing yet.')}</p>
      <p className="dv-caption">{t('Start with something tiny, like “{example}” Ask below, then carry on. Dervo shows you when it’s done or needs you.',{example:t(MICROTASK_EXAMPLES[0])})}</p>
     </div>)}
    {/* Empty buckets aren't drawn (review, 8 Oct): an empty line under a label read as placeholder text, and the head's
        sentence already says when nothing needs you. Working stays when it has the awake line to carry. */}
    {bk.idle.length>0&&bucket('idle',bk.idle,null,'')}
    {bk.resolved.length>0&&bucket('resolved',bk.resolved,null,'')}
    {found.more>0&&<p className="dv-caption dv-now-more">{t('{count} more haven’t moved.',{count:found.more})}</p>}
    <OutsideSessions projectId={projectId} onOpen={onOpen} onCreated={onChanged}/>
    {arrivalFailed&&<ActivityLine className="dv-now-activity" text={arrivalFailedLine(found.items.length>0)}/>}
   </>}
  </>}
 </section>;

 // While a thread is open, Catch up stays on the right as the list alone: every row compact, the open one marked by a
 // soft background and nothing on its edge (Bryce, 27 Sep). The lead and the rest stay on Home.
 const needing=(found?found.items.filter(item=>asking(item)||item.needs).length:0)+waitingRows.length;
 const sidePane=found&&<section className="dv-now dv-now-side" aria-label={head.title}>
  <div className="dv-now-head">
   <div className="dv-now-title"><h2 className="dv-display-2">{head.title}</h2></div>
   <p className="dv-caption">{needing?t('{count} need you',{count:needing}):t('Nothing needs you')}</p>
  </div>
  {(['waiting','working','idle','resolved'] as const).map(key=>key==='waiting'
   ?<Fragment key="waiting">{(bk.waiting.length>0||waitingRows.length>0)&&bucket('waiting',bk.waiting,null,'',null,waitingRows)}{remindersList(null)}</Fragment>
   :bk[key].length>0&&bucket(key,bk[key],null,''))}
  {found.more>0&&<p className="dv-caption dv-now-more">{t('{count} more haven’t moved.',{count:found.more})}</p>}
 </section>;

 const panel=panelItem&&(()=>{
  const at=order.findIndex(item=>item.id===panelItem.id);
  const facts=peekFacts(panelItem).filter(fact=>fact.label!=='Checks'&&!(fact.label==='Next'&&asking(panelItem)));
  return <HomePanel key={panelItem.id} item={panelItem} projectId={projectId} glyph={glyphOf(panelItem)} accent={panelItem.id===accentId} asks={asking(panelItem)}
   sentence={rowSentence(panelItem)??panelItem.did} step={stepOf(panelItem)} facts={facts} when={whenOf(panelItem)}
   position={{at:at+1,of:order.length}} focusComposer={composerFocus} notice={notice} stopping={stopping.has(panelItem.id)}
   onClose={closePanel} onStep={delta=>{const next=stepThread(order,panelItem.id,delta);if(next)stepTo(next);}}
   onOpenThread={(id,focus)=>openThread({id} as PickupItem,!!focus)} onOpenTask={onOpenTask} onStop={id=>void stopJob(id)}
   onActed={id=>void acted(id)} onChanged={()=>client.invalidateQueries({queryKey:['project-pickup',projectId,profileId]}).then(()=>onChanged())}
   reminder={reminders}/>;
 })();

 return <section className="dv-home" aria-label={t('Home')} ref={root} data-arrived={arrivedFromHistory||undefined} data-panel={panelItem?'':undefined} data-narrow={narrow||undefined}>
  {panel?<>
   <div className="dv-home-main">
    {narrow&&<div className="dv-now-strip dv-home-strip" role="group" aria-label={t('Catch up')}>
     <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={closePanel}>{t('Catch up')}</button>
     <span className="dv-caption">{needing?t('{count} need you',{count:needing}):t('Nothing needs you')} · {t('{at} of {all}',{at:order.findIndex(item=>item.id===panelItem!.id)+1,all:order.length})}</span>
    </div>}
    {panel}
   </div>
   {!narrow&&<aside className="dv-home-side">{sidePane}</aside>}
  </>:<>
  <div className="dv-home-main">
   <div className="dv-column dv-home-scroll" ref={log}>
    <div className="dv-measure">
     {view==='home'&&pane}
     {view!=='home'&&<div className="dv-home-navigation"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={showHome}>{t('Back to Home')}</button>
      {view==='request'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>setView('recent')}>{t('Recent requests')}</button>}</div>}
     {view==='recent'&&<section aria-label={t('Recent requests')} className="dv-home-history">
      <h2 className="dv-display-2">{t('Recent requests')}</h2><p className="dv-caption">{t('Your requests to Home, with the threads they led to.')}</p>
      {!requests.length&&<p className="dv-read">{t('No requests yet.')}</p>}
      {requests.slice(historyPage*10,historyPage*10+10).map(request=><button key={request.id} type="button" className="dv-home-request-row" onClick={()=>openRequest(request.id)}>
       <small>{spoken(request.createdAt)}</small><b>{request.title}</b><span>{requestSummary(request)}</span>
      </button>)}
      {requests.length>10&&<div className="dv-row-actions"><button className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!historyPage} onClick={()=>setHistoryPage(page=>page-1)}>{t('Newer')}</button>
       <span className="dv-caption">{t('{from}–{to} of {all}',{from:historyPage*10+1,to:Math.min((historyPage+1)*10,requests.length),all:requests.length})}</span>
       <button className="dv-btn dv-btn-ghost dv-btn-sm" disabled={(historyPage+1)*10>=requests.length} onClick={()=>setHistoryPage(page=>page+1)}>{t('Older')}</button></div>}
     </section>}
     {view==='request'&&<section className="dv-home-chat" aria-label={t('Ask Dervo')}>
      {chat.isError&&<p className="dv-caption" role="alert">{t('Requests couldn’t load. Catch up still uses your work records.')}{' '}
       <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void chat.refetch()}>{t('Try again')}</button></p>}
      {!talk.length&&!chat.isPending&&!chat.isError&&<div className="dv-home-empty">
       <p className="dv-read">{t('Ask about your threads, or say what to do next. Dervo starts threads and files them for you.')}</p>
      </div>}
      {talk.length>0&&<div className="dv-chat" data-live={compact||undefined}>
       {talk.map((entry,position)=>entry.kind==='you'
        ?<div key={handed.current.get(entry.id)??entry.id} className="dv-msg-you-wrap" data-outgoing={entry.outgoing}><p className="dv-msg-you">{entry.text}</p>
         {!!entry.images?.length&&<SentImages projectId={projectId} channelId={imageScope.channelId} ids={entry.images}/>}
         {entry.outgoing==='unsent'&&<p className="dv-caption dv-msg-you-note" role="alert">{t('Not sent.')}{' '}
          <button type="button" className="dv-btn-quiet" onClick={()=>putBack(entry.text??'')}>{t('Edit and send again')}</button></p>}
         {entry.outgoing==='unsure'&&<p className="dv-caption dv-msg-you-note" role="status">{t('Dervo is still busy, so this may still go through. It stays here so it can’t be sent twice.')}</p>}</div>
        // No name row. While it's written, one step line sits where the answer will land; the answer replaces it.
        :<div key={handed.current.get(entry.id)??entry.id} className="dv-msg-dervo" data-state={entry.state??'legacy'} data-outgoing={entry.outgoing}>
         {entry.state==='thinking'&&(entry.held
          // Waiting its turn, not being worked on: says so, still, rather than a step line that would read as thinking.
          ?<p className="dv-caption" role="status">{t('Waits for Home’s earlier answers.')}</p>
          :<StepLine {...stepFor(entry,position)}/>)}
         {entry.state==='failed'&&<p className="dv-read dv-home-failed" role="alert">{failureLine(entry.detail,talk[position-1]?.kind==='you')}{' '}
          {talk[position-1]?.kind==='you'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={sending.size>0} onClick={()=>void send(talk[position-1].text??'')}>{t('Send again')}</button>}</p>}
         {entry.news&&entry.text&&<ActivityLine className="dv-home-news" text={entry.text}
          action={entry.news.thread?.id?{label:t('Open'),describe:t('Open {name}',{name:entry.news.thread.name||entry.text}),onClick:()=>onOpen(entry.news!.thread.id)}:null}/>}
         {entry.text&&!entry.news&&blocks(entry.text).map((block,index)=>block.kind==='p'
          ?<p key={index} className="dv-read"><Inline text={block.text}/></p>
          :<ul key={index} className="dv-read dv-home-list">{block.items.map((item,n)=><li key={n}><Inline text={item}/></li>)}</ul>)}
         {entry.state==='ready'&&<ActivityLines className="dv-home-activity" more={t('more changes')} items={replyLines(entry).map(line=>{
          const key=`${entry.id}:${line.change}`,failed=line.change!==null?undoFailed[key]:undefined;
          return {key:line.key,text:failed??line.text,done:line.undone?t('Undone'):null,
           action:line.undo&&!failed?{label:t('Undo'),describe:t('Undo: {text}',{text:line.text}),disabled:!!undoing,onClick:()=>void undoChange(entry,line.change!)}:null};
         })}/>}
         {(!!entry.parts?.length||!!entry.mentions?.length||liveOffers(entry).length>0)&&<div className="dv-jobs">
          {(entry.parts??[]).map(part=>{
           const channel=part.channel_id??part.conversation?.id??null,onIt=hostSending(entry);
           const live=sending.has(entry.id)||!!entry.queued||part.state==='starting';
           const unsent=(part.state??'pending')==='pending'&&!live;
           const job=jobState(part,live,channel?activity.get(channel):null,entry.created_at,now);
           const line=unsent?UNSENT_LINE:keptLine(part,entry.launch,conversations.find(c=>c.id===channel))??jobLine(part,filedByChanges(entry));
           const opens=!!channel&&part.state!=='failed'&&!unsent;
           // Unknown: Dervo closed mid-send and the thread's records don't show it. Open the thread to check, or send it
           // again with the same request id (it lands once).
           const again=part.state==='unknown'?<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={sending.size>0||hostBusy}
            onClick={()=>void run(entry,part.index,true)}>{t('Send again')}</button>:null;
           // While this very job is being sent, the reply's foot says "Starting …": the row doesn't say it twice.
           const said=(live&&(part.state??'pending')==='pending'&&starting[entry.id]?.index===part.index)||(onIt===part&&!sending.has(entry.id))?'':job.word.toLowerCase();
           const small=[said,line&&line!==part.text?line:''].filter(Boolean).join(' · ');
           // While its thread runs, the step and the clock; once it's ended, the record's summary (JobLive.tsx).
           // The summary stands in for the word only when the job finished: a job waiting on you keeps "Needs you" beside
           // its glyph (design review, 23 Sep, 3).
           const lately=unsent?null:liveJob(part,channel?activity.get(channel):null,entry.created_at);
           const progress=lately&&(lately.kind==='running'||job.glyph==='done')?lately:null;
           const note=progress?<LiveJobLine live={progress} rest={line&&line!==part.text?line:''}/>:small&&<small>{small}</small>;
           const body=<><StatusGlyph state={job.glyph}/><span className="dv-job-text"><b>{partTitle(part)}</b>
            {note}</span></>;
           // Move sits on every row whose thread exists, running or not (dd #10): Home filed it, you can fix it in one go.
           const thread=opens&&threadMenu?conversations.find(c=>c.id===channel)??null:null;
           const move=thread&&threadMenu?<MoveJob conversation={thread} model={threadMenu} title={partTitle(part)}/>:null;
           // While its thread runs, the row holds the way in and a Stop beside it (UX H2). Two buttons, so no nesting.
           if(opens&&part.state==='sent'&&activity.get(channel!)?.running){
            const ending=stopping.has(channel!);
            return <div key={part.index} className="dv-job" data-state="sent" data-live>
             <StatusGlyph state={job.glyph}/>
             <button type="button" className="dv-job-text dv-job-open" onClick={()=>onOpen(channel!)}><b>{partTitle(part)}</b>{note}</button>
             <span className="dv-job-acts">{move}
              <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-label={ending?t('Stopping {job}',{job:partTitle(part)}):t('Stop {job}',{job:partTitle(part)})}
               disabled={ending} onClick={()=>void stopJob(channel!)}>{ending?t('Pausing…'):t('Pause')}</button></span>
            </div>;
           }
           if(opens&&(move||again))return <div key={part.index} className="dv-job" data-state={part.state??'pending'}>
            <StatusGlyph state={job.glyph}/>
            <button type="button" className="dv-job-text dv-job-open" onClick={()=>part.state==='needs_you'?onHandOff(channel!,part.text):onOpen(channel!)}>
             <b>{partTitle(part)}</b>{note}</button>
            <span className="dv-job-acts">{move}{again}</span>
           </div>;
           if(opens)return <button key={part.index} type="button" className="dv-job" data-state={part.state??'pending'}
            onClick={()=>part.state==='needs_you'?onHandOff(channel!,part.text):onOpen(channel!)}>{body}<Go/></button>;
           return <div key={part.index} className="dv-job" data-state={part.state??'pending'} role={part.state==='failed'?'alert':undefined}>
            {unsent?<><StatusGlyph state={job.glyph}/><span className="dv-job-text"><b>{partTitle(part)}</b><small>{line}</small></span></>:body}
            {(unsent||part.state==='failed'||part.state==='unknown')?<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={sending.size>0||hostBusy} onClick={()=>void run(entry,part.index,true)}>{unsent?t('Send'):t('Send again')}</button>:<span/>}</div>;
          })}
          {/* What the reply offered: the same row as a job, waiting on your Start. A yes typed below starts it too. */}
          {liveOffers(entry).map(offer=><div key={offer.id} className="dv-job" data-state="offered">
           <StatusGlyph state="idle"/><span className="dv-job-text"><b>{offerTitle(offer)}</b><small>{offerLine(offer)}</small></span>
           <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" aria-label={t('Start {job}',{job:offerTitle(offer)})}
            disabled={!!offering||sending.size>0||replyThinking} onClick={()=>void startOffer(entry,offer)}>{offering===offer.id?t('Starting…'):t('Start')}</button>
          </div>)}
          {(entry.mentions??[]).filter(mention=>!(entry.parts??[]).some(part=>(part.channel_id??part.conversation?.id)===mention.id)).map(mention=>{
           const live=activity.get(mention.id);
           const glyph:GlyphState=live?.running?'running':live?.status==='needs'?'needs':'idle';
           return <button key={mention.id} type="button" className="dv-job" onClick={()=>onOpen(mention.id)}>
            <StatusGlyph state={glyph}/><span className="dv-job-text"><b>{mention.name}</b>{(live?.running?live.step:live?.summary)&&<small>{live?.running?live.step:live?.summary}</small>}</span><Go/></button>;
          })}
         </div>}
         {entry.state==='ready'&&(moveFailed[entry.id]
          ?<p className="dv-caption" role="alert">{moveFailed[entry.id]}</p>
          :entry.routed?.move&&((entry.not_before??0)>now||moveOpen===entry.id)&&(entry.parts??[]).every(part=>(part.state??'pending')==='pending')
          &&<RouteMove entry={entry} conversations={conversations} busy={!!moving} onMove={to=>void moveRouted(entry,to)}
           onOpenChange={open=>setMoveOpen(open?entry.id:'')}/>)}
         {/* The foot: the job this screen is sending, then the receipt, said once. The newest reply shows its receipt;
             older ones show theirs on hover or focus. */}
         {entry.state==='ready'&&!entry.news&&(sending.has(entry.id)&&starting[entry.id]
          ?<StepLine text={starting[entry.id].text} waited={null} foot/>
          :hostSending(entry)?<StepLine text={startingLine(hostSending(entry)!)} waited={null} foot/>
          :<p className="dv-home-receipt" data-latest={entry===lastReply||undefined}>{receiptLine(entry.looked,entry.created_at,now)}</p>)}
        </div>)}
      </div>}
     </section>}
     {view!=='request'&&chat.isError&&<p role="alert" className="dv-caption">{t('Requests couldn’t load.')} <button className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void chat.refetch()}>{t('Try again')}</button></p>}
    </div>
   </div>

   {/* Directly above the composer and nothing between them: the tray reads its own nextElementSibling to tell the
       composer it sits on from every other field on the screen, and the two share one outline. */}
   {/* On Recent requests the composer is hidden, but voice keeps its strip, so what it hears can still be undone. */}
   {view==='recent'&&<VoiceStrip voice={voice}/>}
   {view!=='recent'&&<>
   <VoiceStrip voice={voice}/>
   {question&&<QuestionTray question={question} composerText={text} disabled={questionOff}
    onClearComposer={()=>setText('')} onAnswer={answer=>void send(answer.text)}/>}
   {/* On Home itself the box is a one-line shortcut under the lists (9 Oct: Home leads with what needs you, the prompt
       stays); in a request it's the chat's own composer. It grows as you type either way. */}
   <div className="dv-composer dv-home-composer" data-view={view} onPasteCapture={event=>{const files=Array.from(event.clipboardData.files);if(files.length&&imagesReady){event.preventDefault();void choose(files);}}}
    onDragOverCapture={event=>{if(imagesReady&&event.dataTransfer.types.includes('Files'))event.preventDefault();}}
    onDropCapture={event=>{const files=Array.from(event.dataTransfer.files);if(files.length&&imagesReady){event.preventDefault();void choose(files);}}}>
    {images.length>0&&<div className="dv-home-tray" aria-label={t('Attached images')}>{images.map(row=><div key={row.id} className="dv-home-thumb" data-state={row.state}>
     {row.dataUrl?<img src={row.dataUrl} alt={row.name}/>:<span className="dv-home-thumb-blank" aria-hidden="true"/>}
     <span className="dv-caption dv-home-thumb-name" title={row.name}>{row.state==='ready'?row.name:t('Uploading…')}</span>
     <button type="button" className="dv-home-thumb-remove" aria-label={t('Remove {name}',{name:row.name})} disabled={imageBusy} onClick={()=>void removeImage(row.id)}><Close/></button>
    </div>)}</div>}
    {imageError&&<p className="dv-caption dv-home-failure" role="alert">{imageError} <button type="button" className="dv-btn-quiet" onClick={()=>setImageError('')}>{t('Dismiss')}</button></p>}
    <label className="sr-only" htmlFor="dervo-home-ask">{t('Ask Dervo about {name}',{name:projectName})}</label>
    {/* Enter is the composer's only while there is something to send. With nothing typed it stays the page's, so the
        tray above still gets it: taking it here and sending nothing would swallow the key that confirms a grant. */}
    <textarea id="dervo-home-ask" className="dv-field" rows={1} value={text} disabled={signedOut}
     placeholder={signedOut?t('Sign in to ask Dervo'):questionCopy?.placeholder||(view==='request'?t('Follow up on this request…'):t('What would you like to do?'))}
     onChange={event=>setText(event.target.value)}
     onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&text.trim()){event.preventDefault();void send();}}}/>
    {failure&&<p className="dv-caption dv-home-failure" role="alert">{failure} <button type="button" className="dv-btn-quiet" onClick={()=>setFailure('')}>{t('Dismiss')}</button></p>}
    <div className="dv-foot">
     {imagesReady&&!signedOut&&<>
      <input ref={fileInput} type="file" multiple accept="image/png,image/jpeg,image/webp" aria-label={t('Attach images')} className="sr-only" tabIndex={-1} disabled={imageBusy} onChange={event=>{const files=Array.from(event.target.files??[]);event.target.value='';void choose(files);}}/>
      <button type="button" className="dv-btn-icon dv-home-clip" aria-label={t('Attach an image')} title={t('Attach an image')} disabled={imageBusy||images.length>=4} onClick={()=>fileInput.current?.click()}><Clip/></button>
     </>}
     {signedOut
      ?<><span className="dv-hint">{t('Your threads stay on this Mac. Sign in to ask Dervo about them.')}</span>
       {signInButton&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm dv-home-signin" onClick={event=>signIn(event.currentTarget)}>{t('Sign in')}</button>}</>
      // The foot doesn't change while Dervo works: the step line in the chat says so. Send waits in its place, quietly.
      :<>{accounts.length>0&&<HomeLaunchPicker accounts={accounts} view={launch} catalog={launch.provider==='codex'?codexCatalog.data?.models:undefined} claudeVersion={claudeVersion} homeModel={home?.model??null} hasPick={!!picked}
        homeSetting={home?{home,projectId,profileId,onSaved:listing=>client.setQueryData(chatKey,listing)}:undefined}
        disabled={working} onPick={pick=>{setPicked(pick);setIgnoreWords(true);}} onIgnoreWords={()=>setIgnoreWords(true)} onReset={()=>{setPicked(null);setIgnoreWords(true);}}/>}
       <span className="dv-hint">{imageBusy?t('Preparing your image…'):launch.problem&&text.trim()&&!bugDraft(text)?launch.problem:launch.note&&text.trim()&&!bugDraft(text)?launch.note:questionCopy?.hint??(view==='request'?t('Following up on this request'):composerHint(autostart,home?.model,accounts.length>0,home?.provider))}</span>
       <VoiceButton voice={voice} disabled={signedOut}/>
       <button type="button" className="dv-btn-send" aria-label={t('Send')} data-working={working||undefined} disabled={!text.trim()||working||imageBusy||(!!launch.problem&&!bugDraft(text))} onClick={()=>void send()}><Arrow/></button></>}
    </div>
   </div>
   </>}
  </div>
  {peekItem&&peek&&<HomePeek id={peekId} item={peekItem} sentence={rowSentence(peekItem)} facts={peekFacts(peekItem)} glyph={glyphOf(peekItem)} accent={peekItem.id===accentId}
   row={peek.row} bounds={root.current?.getBoundingClientRect()??{left:0,right:window.innerWidth}}/>}
  </>}
 </section>;
}
