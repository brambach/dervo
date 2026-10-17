import {Fragment,isValidElement,useEffect,useRef,useState,type MouseEvent,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import {Archive,BookOpen,ChevronDown,ChevronRight,ChevronsUpDown,ClipboardList,FolderInput,Home,Lock,LogOut,MessageSquare,MoreHorizontal,Package,Pin,PinOff,Plug,Plus,Search,Settings} from 'lucide-react';
import {ContextMenu,ContextMenuTrigger,ContextMenuContent,ContextMenuItem} from '@/shared/ui/context-menu';
import {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator} from '@/shared/ui/dropdown-menu';
import {ProfileAvatar} from './ProfilePhoto';
import {ProjectPicker} from './ProjectPicker';
import {ThreadMenuBody,editableTarget,type ThreadMenuModel,type UndoEntry} from './ThreadMenu';
import {providerAuthor} from './providerLabel';
import type {DisplayProject} from './projectDisplay';
import {ActivityLine} from './ActivityLine';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {local,organisationRequest,type Conversation,type OrganisationState,type UsageAccount} from './api';
import {ArchivedFold,revealArchived} from './ArchivedFold';
import {pinThread,pinnedThreads,tidiedLine,tidyOnce,tidySetting,undoTidy,type ArchivedThread,type TidyDays} from './tidy';
import type {Freed} from './ConversationLifecycle';
import {archiveKey} from './useArchiveShortcut';
import './ActivityLine.css';
import {useUpdateNotice} from './updateNotice';
import {subthreads} from './subthreadState';
export {subthreads} from './subthreadState';
import './DervoSidebar.css';
import {t} from '@/i18n';

/** How many quiet threads a group shows before folding the rest behind "N more". Threads that need you, are running
 * or have results you haven't opened always show. */
export const QUIET_SHOWN=3;
export type ThreadGroup={key:string;title:string;threads:Conversation[];folded:Conversation[]};
const rankOf=(c:Conversation)=>c.activity?.status==='needs'||(c.notesWaiting??0)>0?0:c.activity?.running?1:(c.activity?.unread??0)>0?2:3;

/** How long a workstream's threads must all have been quiet before the group folds to its header (fresh-eyes item 8: at
 * twenty threads the sidebar should still show what's live without scrolling). Three days, from the host's `at`. */
export const STALE_DAYS=3;
/** A thread that has been quiet for three days: nothing waits on you, nothing runs, nothing unopened, and the host's
 * last activity time is at least three days old. A thread without a time can't be called stale, so it never folds
 * this way: a brand-new thread has no record yet. */
export function staleThread(activity:Conversation['activity'],now=Date.now()):boolean{
  if(!activity)return false;
  if(activity.status==='needs'||activity.running||(activity.unread??0)>0)return false;
  const at=activity.at;
  if(typeof at!=='number'||at<1e9||at>4e9)return false;
  return now-at*1000>=STALE_DAYS*86400*1000;
}
/** A group folds to its header only when every thread in it is stale. One running, needs-you or unopened thread keeps
 * the whole group open, whatever the rest have been doing. */
export function staleGroup(group:Pick<ThreadGroup,'threads'|'folded'>,now=Date.now(),kids:ReadonlyMap<string,Conversation[]>=new Map()):boolean{
  const all=[...group.threads,...group.folded];
  return all.length>0&&all.every(c=>[c,...(kids.get(c.id)??[])].every(row=>staleThread(row.activity,now)));
}

/** The loudest of a parent's subthreads, for its count chip when they're folded: needs you, then failed, stopped,
 * running, done. Idle when none has anything to say. */
const LOUDEST:GlyphState[]=['needs','failed','stopped','running','done'];
export function rollup(kids:Conversation[]):GlyphState{
  const states=new Set(kids.map(c=>rowGlyph(c).state));
  return LOUDEST.find(state=>states.has(state))??'idle';
}
/** A row's glyph: the host's activity, and a note or a subthread review waiting for you there (beta.17) reads as
 * "needs you" unless the thread is running or already needs you for its own reason. */
export function rowGlyph(c:Conversation):{state:GlyphState;label:string}{
  const found=threadGlyph(c.activity),waiting=c.notesWaiting??0;
  if(!waiting||found.state==='running'||found.state==='needs')return found;
  return {state:'needs',label:waiting===1?t('Something waits for you here'):t('{waiting} things wait for you here',{waiting})};
}
/** What the chip says for its subthreads, read by a screen reader and on hover. */
export function rollupLine(kids:Conversation[]):string{
  const glyphs=kids.map(c=>rowGlyph(c).state);
  const needs=glyphs.filter(g=>g==='needs').length,running=glyphs.filter(g=>g==='running').length;
  const what=needs?t('{count} need you',{count:needs}):running?t('{running} running',{running}):glyphs.every(g=>g==='done'||g==='failed'||g==='stopped')?t('all finished'):'';
  return `${kids.length} ${kids.length===1?'subthread':'subthreads'}${what?`, ${what}`:''}`;
}

/** Quiet threads read newest first, so the ones that fold are the oldest (19 Sep, map F12: creation order folded the
 * newest quiet ones first). Loud rows keep the host's order among themselves. */
const when=(c:Conversation)=>typeof c.activity?.at==='number'?c.activity.at:0;
function arrange(key:string,title:string,threads:Conversation[],rank:(c:Conversation)=>number=rankOf):ThreadGroup{
  const sorted=threads.map((c,i)=>[c,i] as const).sort((x,y)=>rank(x[0])-rank(y[0])||(rank(x[0])===3?when(y[0])-when(x[0]):0)||x[1]-y[1]).map(([c])=>c);
  const loud=sorted.filter(c=>rank(c)<3),quiet=sorted.filter(c=>rank(c)===3);
  const shown=Math.max(0,QUIET_SHOWN-loud.length);
  return {key,title,threads:[...loud,...quiet.slice(0,shown)],folded:quiet.slice(shown)};
}

/** The sidebar as the project's structure (Bryce, 18 Sep): the threads Home works through, grouped under the
 * workstreams it files them in. Inside a group: what needs you, then what's running, then unopened results, then quiet
 * threads, a few shown and the rest folded. Groups with something waiting come first; "Not filed yet" comes after the
 * workstreams; private threads, which Home never reads, sit apart at the end. Nothing here reads a
 * conversation; the host's activity index is the only source. */
export function sidebarThreads(conversations:Conversation[],accountKinds:Record<string,string>={}){
  // Subthreads ride under their parent, wherever Home filed them, and a parent ranks as loud as its loudest subthread,
  // so one that runs or needs you never folds away behind "N more".
  const kids=subthreads(conversations),nested=new Set([...kids.values()].flat().map(c=>c.id));
  const rank=(c:Conversation)=>Math.min(rankOf(c),...(kids.get(c.id)??[]).map(rankOf));
  const all=conversations;
  conversations=conversations.filter(c=>!nested.has(c.id));
  const shared=conversations.filter(c=>c.visibility!=='private');
  // Personal and work stay apart (AGENTS.md): a workstream with threads on both becomes two groups, each titled with
  // its kind. Claude and Codex on the same kind of account share one group, as the mockups draw it (19 Sep).
  const kind=(c:Conversation)=>accountKinds[c.profile_id]==='work'?'work':'personal';
  const kinds=new Map<string,Set<string>>();
  for(const c of shared)if(c.organisationGroup)kinds.set(c.organisationGroup,(kinds.get(c.organisationGroup)??new Set()).add(kind(c)));
  const buckets=new Map<string,{title:string;threads:Conversation[]}>();
  for(const c of shared){
    if(!c.organisationGroup)continue;
    const key=JSON.stringify([c.organisationGroup,kind(c)]);
    const title=kinds.get(c.organisationGroup)!.size>1?`${c.organisationGroup} · ${kind(c)==='work'?t('Work'):t('Personal')}`:c.organisationGroup;
    const entry=buckets.get(key)??{title,threads:[]};
    entry.threads.push(c);buckets.set(key,entry);
  }
  const loudest=(group:ThreadGroup)=>Math.min(4,...[...group.threads,...group.folded].map(rank));
  const groups=[...buckets.entries()].map(([key,entry])=>arrange(key,entry.title,entry.threads,rank))
    .map((group,index)=>[group,index] as const).sort((x,y)=>loudest(x[0])-loudest(y[0])||x[1]-y[1]).map(([group])=>group);
  const loose=shared.filter(c=>!c.organisationGroup);
  if(loose.length)groups.push(arrange('none',t('Not filed yet'),loose,rank));
  const privates=conversations.filter(c=>c.visibility==='private');
  return {groups,private:privates.length?arrange('private',t('Private'),privates,rank):null,kids,
    // Home's count is what it can show you: shared threads only. A private one still gets its own glyph.
    needs:all.filter(c=>c.visibility!=='private'&&c.activity?.status==='needs'&&c.activity.attention!==false).length};
}

/** Where opening a project lands: the orchestrator, the project's front door (Bryce, 18 Sep), whenever the project's
 * records are readable. It lists what's waiting for you, so Catch up no longer needs to be the landing. Without readable
 * records it can't open, so stay on a conversation of this project that's already open, or the last one you had open
 * here. Null means nothing to change. */
export function projectLanding(rows:Conversation[],{ready,channelId,last}:{ready:boolean;channelId:string;last:string|null}):{screen:'orchestrator'}|{channel:string}|null{
  if(ready)return {screen:'orchestrator'};
  if(rows.some(c=>c.id===channelId))return null;
  return last&&rows.some(c=>c.id===last)?{channel:last}:null;
}

/** The glyph a thread row carries, from the host's activity index alone: running, then how the newest task ended
 * (`ended` is the task's own state word), then whether it waits on you or finished unseen. A failed run and one whose
 * outcome Dervo couldn't confirm still need you, but they say why with their own mark; a finished run waiting for your
 * review is a sage check, as on Home. Only a plain "needs you" can carry the one accent. */
export function threadGlyph(activity:Conversation['activity']):{state:GlyphState;label:string}{
  // A question waiting on you wins over Running, as in the thread and its Progress panel (Bryce, 26 Sep).
  if(activity?.pending_question)return {state:'needs',label:t('Asked you something')};
  if(activity?.running)return {state:'running',label:t('Running')};
  // A stop you pressed is settled, not an error (fix-handoff: ended 'stopped', status null). Unread, it still says so.
  // Beta.18: a run Dervo settled as paused after a restart (backend/lost_runs.py) waits for your Continue. Not your pause.
  if(activity?.ended==='restart_paused')return {state:'stopped',label:t('Paused when Dervo restarted')};
  if(activity?.ended==='left')return {state:'stopped',label:t('Left stopped')};  // Leave it stopped (Mark as stopped until beta.18)
  if(activity?.ended==='stopped'&&(activity.unread??0)>0)return {state:'stopped',label:t('You paused this')};
  if(activity?.status==='needs'){
    if(activity.ended==='blocked')return {state:'failed',label:t('Stopped with an error')};
    // Beta.17.1 (backend/run_ending.py): only 'blocked' is a failure now. The rest say what ended them.
    if(activity.ended==='limit')return {state:'stopped',label:t('Stopped at a limit')};
    if(activity.ended==='not_started')return {state:'stopped',label:t('Didn’t start')};
    if(activity.ended==='snag')return {state:'needs',label:t('Finished, a step didn’t complete')};
    if(activity.ended==='disconnected')return {state:'unknown',label:t('Stopped, outcome not confirmed')};
    if(activity.ended==='ready_for_review')return {state:'done',label:t('Ready for review')};
    if(activity.ended==='asking')return {state:'needs',label:t('Asked you something')};
    if(activity.ended==='needs_permission')return {state:'needs',label:t('Waiting for your permission')};
    return {state:'needs',label:t('Needs you')};
  }
  if((activity?.unread??0)>0)return {state:'done',label:activity?.ended==='answered'?t('Answered'):activity?.ended==='approved'?t('Approved'):t('Finished')};
  // Read and settled: the quiet ring, so the sidebar isn't a column of ticks, but named for how it ended, so its label
  // agrees with the thread's own history (polish, 8 Oct: the row said Idle beside "Answered").
  if(activity?.ended==='answered')return {state:'idle',label:t('Answered')};
  if(activity?.ended==='approved')return {state:'idle',label:t('Approved')};
  if(activity?.ended==='completed')return {state:'idle',label:t('Finished')};
  return {state:'idle',label:t('Idle')};
}

/** A thread's time, short: the clock time for today, the weekday for this week, the date otherwise, "now" while it runs. */
export function threadWhen(activity:Conversation['activity'],now=Date.now()):string{
  if(activity?.running)return 'now';
  const at=activity?.at;
  if(typeof at!=='number'||at<1e9||at>4e9)return '';
  const when=new Date(at*1000),today=new Date(now);
  if(when.toDateString()===today.toDateString())return when.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}).replace(/\s?[AP]M$/i,'');
  if(now-at*1000<6*86400*1000)return when.toLocaleDateString([],{weekday:'short'});
  return when.toLocaleDateString([],{day:'numeric',month:'short'});
}

/** A project folder, short: the home directory reads "~". */
export function shortPath(cwd?:string|null):string{
  if(!cwd)return '';
  return cwd.replace(/^\/Users\/[^/]+(?=\/|$)/,'~').replace(/^\/home\/[^/]+(?=\/|$)/,'~');
}

const money=(value:number)=>`$${value.toFixed(2)}`;
/** A window reset, as seconds since the epoch: the time today, or the weekday and time later. Anything else is left out. */
function resetLabel(at:number|null,now=Date.now()):string{
  if(typeof at!=='number'||at<1e9||at>4e9)return '';
  const when=new Date(at*1000),time=when.toLocaleTimeString([],{hour:'numeric',minute:when.getMinutes()?'2-digit':undefined});
  return when.toDateString()===new Date(now).toDateString()?time:`${when.toLocaleDateString([],{weekday:'short'})} ${time}`;
}
const WINDOWS:Record<number,string>={300:'5-hour window',10080:'weekly window'};
const CHATGPT_PLANS:Record<string,string>={plus:t('ChatGPT Plus'),prolite:t('ChatGPT Pro Lite'),pro:t('ChatGPT Pro'),team:t('ChatGPT Team'),business:t('ChatGPT Business'),enterprise:t('ChatGPT Enterprise'),edu:t('ChatGPT Edu'),free:t('ChatGPT Free')};
/** Which account a usage row is: the name you gave it ("Personal", "Acme") when it has one, otherwise the
 * plan for a ChatGPT login when Codex reported one, "Personal" for a Claude login, "API key" for a key. Two rows can
 * share a provider, so this is what tells them apart (Bryce, 18 Sep; the lead's account names, 19 Sep). */
export function accountLabel(account:Pick<UsageAccount,'provider'|'method'|'rate_limits'|'name'>):string{
  const named=account.name?.trim();
  if(named)return named;
  if(account.method==='api_key')return t('API key');
  if(account.provider==='codex'){
    const plan=account.rate_limits?.planType;
    return (typeof plan==='string'&&CHATGPT_PLANS[plan.toLowerCase()])||'ChatGPT';
  }
  return t('Personal');
}
/** Each row's account label, as the model picker shows it: two unnamed accounts of one provider that would both read
 * "Personal" show their sign-in instead, so the rows can be told apart until you name them on Connected accounts. */
export function usageLabels(accounts:UsageAccount[]):Map<string,string>{
  const label=(a:UsageAccount)=>accountLabel(a);
  const clash=(a:UsageAccount)=>!a.name?.trim()&&accounts.filter(b=>b.provider===a.provider&&label(b)===label(a)).length>1;
  return new Map(accounts.map(a=>[a.profile_id,clash(a)&&a.identity?a.identity:label(a)]));
}
/** What one account's row says, from the host's summary alone. The bar and number show what's left, so a used-up account
 * reads 0%. The row is one line (fresh-eyes item 8): provider, account, bar, percent. The reset or the spend is in the
 * summary, which the row shows on hover and reads out. A Claude subscription that hasn't reported its windows yet shows
 * today's runs and no bar. */
export function usageLine(account:UsageAccount,now=Date.now(),label?:string){
  const provider=account.provider==='codex'?'Codex':account.provider==='claude'?'Claude':providerAuthor(account.provider);
  const which=label??accountLabel(account);
  const name=`${provider}${account.method==='api_key'?' key':''}`;
  const runs=`${account.runs_today} ${account.runs_today===1?'run':'runs'} today`;
  // The row's note is the account alone; the reset and the spend live in the title and for screen readers.
  const note=which;
  let percent:number|null=null,value='',low=false,summary:string;
  if(account.percent!==null&&account.basis==='rate_limits'){
    const resets=resetLabel(account.resets_at,now),window=WINDOWS[account.window_mins??0]??'window';
    percent=Math.max(0,100-account.percent);value=`${percent}%`;low=account.low;
    summary=`${percent}% of the ${window} left${resets?`, resets ${resets}`:''}. ${runs.charAt(0).toUpperCase()+runs.slice(1)}.`;
  }else if(account.percent!==null&&account.basis==='spend_limit'){
    percent=Math.max(0,100-account.percent);value=`${percent}%`;low=account.low;
    summary=`About ${money(account.cost_month_usd??0)} of ${money(account.monthly_limit_usd??0)} spent this month, by the provider’s own estimate. ${runs.charAt(0).toUpperCase()+runs.slice(1)}.`;
  }else{
    // The runs stand where the bar would, so they're on the line itself (UX6: "0 runs today" twice).
    summary=`${runs}${account.tokens_today?`, ${account.tokens_today.toLocaleString()} tokens`:''}. ${account.provider==='claude'?t('Claude Code reports how much is left during its next run.'):t('This account reports no allowance to measure against.')}`;
    summary=summary.charAt(0).toUpperCase()+summary.slice(1);
  }
  return {provider,which,name,percent,value,left:percent===null?'':`${percent}% left`,low,runs,note,summary,detail:`${provider} · ${which} · ${account.identity}: ${summary}`};
}

/** How the usage block shows (app #13, 23 Sep: "too big, they cover up too much of the screen"). It's always one line,
 * "Claude 64% · Codex 59%", and a click opens the usage popover with every account's bars, limits and resets. It used
 * to unfold to a row per account, with each limit window, whenever one was low or unmeasured (fresh-eyes item 8). */
export type UsageFold='rows'|'one';
export function usageFold(accounts:Pick<UsageAccount,'percent'|'basis'|'low'|'windows'>[]):UsageFold{
  return accounts.length?'one':'rows';
}
/** What's left on one account, as a single number: the tightest of its measured allowance and each limit window (the
 * 5-hour and the weekly), or null when it reports none. Low when the host says so or a window is 80% used. */
export function tightestLeft(account:Pick<UsageAccount,'percent'|'basis'|'low'|'windows'>&{window_mins?:number|null}):{left:number|null;low:boolean;window:string|null}{
  const measured=account.percent!==null&&(account.basis==='rate_limits'||account.basis==='spend_limit');
  const lefts=[...(measured?[{left:100-(account.percent as number),mins:account.basis==='rate_limits'?account.window_mins??null:null}]:[]),
    ...(account.windows??[]).map(window=>({left:100-window.percent,mins:window.window_mins}))].map(entry=>({...entry,left:Math.max(0,entry.left)}));
  if(!lefts.length)return {left:null,low:!!account.low,window:null};
  const tight=lefts.reduce((a,b)=>b.left<a.left?b:a);
  return {left:tight.left,low:!!account.low||(account.windows??[]).some(window=>window.percent>=80),window:SHORT_WINDOWS[tight.mins??0]??null};
}
/** A limit window, as short as the line has room for: the 5-hour one and the weekly one. */
const SHORT_WINDOWS:Record<number,string>={300:'5h',10080:'week'};
/** The one line: each provider and what's left. Two accounts of one provider share an entry and it shows the lower
 * figure, since 232px has no room for names; an account that reports no allowance shows its runs today. Hover and the
 * screen reader get every account in full. */
export function usageOneLine(accounts:UsageAccount[],now=Date.now()):{parts:{id:string;name:string;value:string;low:boolean}[];detail:string;low:boolean}{
  const labels=usageLabels(accounts);
  const lines=accounts.map(account=>({account,line:usageLine(account,now,labels.get(account.profile_id)),...tightestLeft(account)}));
  const parts:{id:string;name:string;value:string;low:boolean;rank:number}[]=[];
  for(const {account,line,left,low,window} of lines){
    // A low figure says which window it is, since a 5-hour window refills soon and a week doesn't (UI review, 23 Sep).
    const value=left===null?`${account.runs_today} ${account.runs_today===1?'run':'runs'}`:`${left}%${low&&window?` (${window})`:''}`,rank=left??Infinity;
    const seen=parts.find(part=>part.name===line.provider);
    if(!seen){parts.push({id:account.profile_id,name:line.provider,value,low,rank});continue;}
    if(rank<seen.rank)Object.assign(seen,{id:account.profile_id,value,low,rank});
    else seen.low=seen.low||low;
  }
  return {parts:parts.map(({rank:_,...part})=>part),detail:lines.map(({line})=>line.detail).join('\n'),low:parts.some(part=>part.low)};
}

/** The one calm line a row shows when Dervo changed it on its own, from the host's organisation record: who did it is
 * `source.kind`, never a guess from timing (a move you made yourself is 'manual' and gets no line). Undo goes through
 * organisation_undo at the revision the change left. Null when nothing Dervo did is worth a line. */
export function automaticNote(state:Pick<OrganisationState,'source'|'canUndo'|'revision'|'group'>,was:{name:string;group:string|null},now:{name:string;group:string|null}):{line:string;revision:number|null}|null{
  const kind=state.source?.kind??'';
  const named=was.name!==now.name,filed=was.group!==now.group;
  const revision=state.canUndo?state.revision:null;
  if(kind==='home'){
    if(!filed||!now.group)return null;
    return {line:was.group?t('Home moved it to {group}.',{group:now.group}):t('Home filed it under {group}.',{group:now.group}),revision};
  }
  if(kind==='chat'||kind==='coding'||kind==='coordinator'){
    if(named&&filed&&now.group)return {line:t('Named and filed under {group} from your message.',{group:now.group}),revision};
    if(named)return {line:t('Named from your message. Rename it any time.'),revision};
    if(filed&&now.group)return {line:t('Filed under {group} from your message.',{group:now.group}),revision};
    return null;
  }
  if(kind==='migration')return named||filed?{line:t('Named and filed from its first message when Dervo started.'),revision}:null;
  if(kind==='history')return filed&&now.group?{line:t('Filed under {group} when its history was read.',{group:now.group}),revision}:null;
  if(kind==='guide')return named?{line:t('Renamed by the agent from what it’s working on.'),revision}:null;
  if(kind==='archive')return filed&&!now.group?{line:t('Its workstream was archived. Dervo files it again at its next message.'),revision:null}:null;
  return null;
}
export type RowNote={id:string;line:string;revision:number|null;error?:string};
export const NOTE_MS=8000;

export type ProjectAction='orchestrator'|'history'|'search'|'catchup'|'knowledge'|'bugs'|'settings'|'coding'|'agents'|'connection'|'task'|'preview';
/** A thread being renamed in its row: the name as it was, whether it's saving, and why it didn't save. */
export type Renaming={id:string;busy?:boolean;error?:string};
type SidebarThread=Conversation&{gone?:UndoEntry};
type Props={
  projects:DisplayProject[];projectId:string;projectsLoading:boolean;projectsFailed:boolean;onRetryProjects:()=>void;onSelectProject:(id:string)=>void;onAddProject:(trigger:HTMLElement)=>void;
  ready:boolean;active?:'orchestrator'|'history'|'knowledge'|'bugs'|'reports'|'search'|'settings';
  conversations:Conversation[];channelId:string;conversationActive:boolean;onSelectConversation:(id:string)=>void;
  onAction:(action:ProjectAction,trigger:HTMLElement)=>void;onCreate:(isPrivate:boolean)=>void;onJump:(trigger:HTMLElement)=>void;
  accounts:UsageAccount[];onUsage:(trigger:HTMLElement)=>void;usageLifted?:string|null;onWorkspaceSettings:(trigger:HTMLElement)=>void;onConnectedAccounts:(trigger:HTMLElement)=>void;onReleases?:()=>void;onReports?:()=>void;onFeedback:()=>void;
  canSignOut:boolean;onSignOut:()=>Promise<unknown>;
  threadMenu:ThreadMenuModel;renaming?:Renaming|null;onRename:(conversation:Conversation,name:string|null)=>void;onUndo:()=>void;rowErrors?:Record<string,string>;arrived?:string|null;renamed?:string|null;
  accountKinds?:Record<string,string>;
  archived?:ReactNode;accountName?:string;accountPhoto?:string;accountNote:string;knowledgeCount?:number;
  /** Something new was kept since you last looked at Knowledge (b16/memory): a dot in place of the count. */
  knowledgeUnread?:boolean;
  /** Restore and delete from the Archived fold. Without them the sidebar uses the ones the `archived` element was given. */
  onArchivedRestored?:(conversation:Conversation)=>Promise<unknown>;onArchivedDelete?:(conversation:Conversation)=>Promise<unknown>;
  /** Beta.16.1 item 3: archive the threads selected with ⌘-click and Shift-click, in one go with one Undo. Without it,
   * rows don't select. */
  onArchiveMany?:(conversations:Conversation[])=>Promise<unknown>;
  /** "Delete all archived" at the foot of the Archived fold. Without it the fold has no such row. */
  onDeleteArchived?:(conversations:ArchivedThread[])=>Promise<Freed&{count:number}>;
};
type Handlers={onRestored?:(conversation:Conversation)=>Promise<unknown>;onDelete?:(conversation:Conversation)=>Promise<unknown>};
/** Tester reports for an admin, as the host counts them (next_up.py): how many you haven't seen, and the project they show in. */
type ReportCount={state:string;unseen:number;project:string|null};


export function DervoSidebar(props:Props){
  const {projects,projectId,conversations,channelId,conversationActive,active,ready}=props;
  const {groups,private:privates,needs,kids}=sidebarThreads(conversations,props.accountKinds);
  const [opened,setOpened]=useState<Set<string>>(new Set());
  // Which parents have their subthreads folded, kept per project. A parent starts open, and opens again when a new
  // subthread arrives under it; otherwise it stays how you left it (Bryce's pick, 2 Oct).
  const [foldedKids,setFoldedKids]=useState<Set<string>>(()=>readFoldedKids(projectId));
  const knownKids=useRef<Map<string,Set<string>>>(new Map());
  useEffect(()=>{setFoldedKids(readFoldedKids(projectId));knownKids.current=new Map();},[projectId]);
  useEffect(()=>{
    const arrived:string[]=[];
    for(const [parent,rows] of kids){
      const was=knownKids.current.get(parent),now=new Set(rows.map(c=>c.id));
      if(was&&[...now].some(id=>!was.has(id)))arrived.push(parent);
      knownKids.current.set(parent,now);
    }
    if(arrived.some(id=>foldedKids.has(id)))setKidsFolded(arrived,false);
  },[conversations]);
  function setKidsFolded(parents:string[],fold:boolean){
    setFoldedKids(previous=>{
      const next=new Set(previous);
      for(const id of parents){if(fold)next.add(id);else next.delete(id);}
      writeFoldedKids(projectId,next);
      return next;
    });
  }
  const meTrigger=useRef<HTMLButtonElement>(null),opening=useRef(false);
  // A row Dervo changed on its own says so for a few seconds, with Undo: the rules naming and filing a thread at its
  // first message, Home filing or moving one, the agent renaming one, a workstream archived from under one. Rows are
  // compared with what they were; the host's record then says who changed it, so a move you made gets no line.
  const seen=useRef<Map<string,{name:string;group:string|null}>>(new Map());
  const [notes,setNotes]=useState<RowNote[]>([]);
  const queries=useQueryClient();
  useEffect(()=>{seen.current=new Map();setNotes([]);},[projectId]);
  // A note explains a change you just watched happen; going somewhere else puts it away. An error stays until you
  // dismiss it, so it can be read. The same array back when nothing goes, so the sidebar doesn't redraw for nothing.
  useEffect(()=>{setNotes(rows=>rows.some(row=>!row.error)?rows.filter(row=>!!row.error):rows);},[channelId,active]);
  // Quiet threads tidy themselves when the app opens (b16/lifecycle): once per project per launch, never while you're
  // looking. What went is said in one line above the Archived fold, with Undo and See them.
  const [tidied,setTidied]=useState<{projectId:string;batch:string;count:number}|null>(null);
  const [foldOpen,setFoldOpen]=useState(false),[tidyError,setTidyError]=useState('');
  useEffect(()=>{setFoldOpen(false);setTidyError('');},[projectId]);
  useEffect(()=>{
    if(!ready||!projectId)return;
    let live=true;
    void tidyOnce(projectId,channelId?[channelId]:[]).then(result=>{
      if(!live||!result?.batch||!result.count)return;
      setTidied({projectId,batch:result.batch,count:result.count});
      void queries.invalidateQueries({queryKey:['local-conversations',projectId]});
      void queries.invalidateQueries({queryKey:['archived-conversations',projectId]});
    });
    return()=>{live=false;};
  },[projectId,ready]);
  // The line counts what's still put away from that tidy, from the fold's own query, so opening or restoring one of
  // them takes it off the count, and the line goes once none are left.
  const archivedNow=useQuery({queryKey:['archived-conversations',projectId],enabled:!!tidied&&tidied.projectId===projectId,
    queryFn:()=>local<ArchivedThread[]>('conversations_archived',{projectId})});
  const stillTidied=tidied&&Array.isArray(archivedNow.data)?archivedNow.data.filter(row=>row.tidy_batch===tidied.batch).length:tidied?.count??0;
  const tidiedHere=tidied?.projectId===projectId&&stillTidied>0?{...tidied,count:stillTidied}:null;
  async function undoTidied(){
    if(!tidiedHere)return;
    setTidyError('');
    try{
      await undoTidy(projectId,tidiedHere.batch);
      setTidied(null);
      await queries.invalidateQueries({queryKey:['local-conversations',projectId]});
      await queries.invalidateQueries({queryKey:['archived-conversations',projectId]});
    }catch(failure){setTidyError(`Couldn’t bring them back. ${failure instanceof Error?failure.message:String(failure)}`);}
  }
  const pins=useQuery({queryKey:['conversation-pins',projectId],enabled:ready&&!!projectId,retry:false,queryFn:()=>pinnedThreads(projectId)});
  const pinned=new Set(Array.isArray(pins.data)?pins.data:[]);
  async function setPinned(conversation:Conversation,next:boolean){
    try{await pinThread(projectId,conversation.id,next);}
    catch(failure){setNotes(rows=>[...rows.filter(row=>row.id!==conversation.id),{id:conversation.id,line:'',revision:null,error:`Couldn’t ${next?'pin':'unpin'} it. ${failure instanceof Error?failure.message:String(failure)}`}]);}
    await queries.invalidateQueries({queryKey:['conversation-pins',projectId]});
  }
  const setting=useQuery({queryKey:['tidy-setting',projectId],enabled:ready&&!!projectId,retry:false,queryFn:()=>tidySetting(projectId)});
  const days:TidyDays=setting.data&&typeof setting.data==='object'&&'days' in setting.data?setting.data.days:14;
  // Until ConnectedApp passes the fold's handlers by name, they're the ones its ArchivedConversations element carries.
  const given:Handlers=isValidElement(props.archived)?props.archived.props as Handlers:{};
  const onArchivedRestored=props.onArchivedRestored??given.onRestored??(async()=>{await queries.invalidateQueries({queryKey:['local-conversations',projectId]});});
  const onArchivedDelete=props.onArchivedDelete??given.onDelete;
  // Next up's count: tester reports an admin hasn't seen, on the project they show in (next_up_reports_here).
  const admin=!!props.onReports;
  const reports=useQuery({queryKey:['tester-reports-count'],enabled:admin&&ready,retry:false,refetchInterval:60000,
    queryFn:()=>local<ReportCount>('tester_reports',{reload:false})});
  const unseenReports=admin&&reports.data?.project===projectId?reports.data.unseen:0;
  useEffect(()=>{
    const previous=seen.current,next=new Map<string,{name:string;group:string|null}>();
    const changed:{id:string;was:{name:string;group:string|null};now:{name:string;group:string|null}}[]=[];
    for(const c of conversations){
      if((c as SidebarThread).gone)continue;
      const now={name:c.name,group:c.organisationGroup??null},was=previous.get(c.id);
      next.set(c.id,now);
      if(was&&(was.name!==now.name||was.group!==now.group)&&props.renaming?.id!==c.id&&props.renamed!==c.id)changed.push({id:c.id,was,now});
    }
    seen.current=next;
    if(previous.size===0)return;
    for(const change of changed)void explain(change);
  },[conversations]);
  async function explain({id,was,now}:{id:string;was:{name:string;group:string|null};now:{name:string;group:string|null}}){
    let state:OrganisationState;
    try{state=await organisationRequest<OrganisationState>('organisation_get',{projectId,channelId:id});}
    catch{return;}
    const note=automaticNote(state,was,now);
    if(!note)return;
    const row:RowNote={id,...note};
    setNotes(rows=>[...rows.filter(other=>other.id!==id),row]);
    // By identity, not by its words: a second note with the same line used to outlive its own timer (polish, 8 Oct).
    setTimeout(()=>setNotes(rows=>rows.filter(other=>other!==row||!!other.error)),NOTE_MS);
  }
  async function undoNote(note:RowNote){
    if(note.revision===null)return;
    try{
      await organisationRequest('organisation_undo',{projectId,channelId:note.id,expectedRevision:note.revision});
      setNotes(rows=>rows.filter(row=>row.id!==note.id));
      seen.current.delete(note.id);  // the row comes back changed; that change is yours, so no second line
      await queries.invalidateQueries({queryKey:['local-conversations',projectId]});
    }catch(failure){setNotes(rows=>rows.map(row=>row.id===note.id?{...row,error:`Couldn’t undo. ${failure instanceof Error?failure.message:String(failure)}`}:row));}
  }
  const current=(id:string)=>conversationActive&&id===channelId;
  // Multi-select (beta.16.1 item 3, Bryce 26 Sep: "its annoying having to click on every chat and hotkey"), as a Mac list
  // does it: ⌘-click adds or takes away a row, Shift-click takes the rows from the last one clicked, and the thread you
  // have open counts as the first. A plain click opens a thread and ends the selection, and so does Escape.
  const [picked,setPicked]=useState<string[]>([]),[anchor,setAnchor]=useState<string|null>(null),[archivingMany,setArchivingMany]=useState(false);
  useEffect(()=>{setPicked([]);setAnchor(null);},[projectId]);
  // A newer build (beta.15, group 9): a dot on the account button and on its Settings item until you're on it.
  const update=useUpdateNotice().dot;
  const [meOpen,setMeOpen]=useState(false),[confirmSignOut,setConfirmSignOut]=useState(false),[signingOut,setSigningOut]=useState(false),[signOutError,setSignOutError]=useState('');
  // One accent per view (thread-review.html, thread-handoff.html). When the thread you're in waits on you, its row and
  // the decision in the main area are that accent, so no other row is rust. Otherwise it's the first thread whose mark
  // is a plain "needs you" and that you haven't opened since.
  const withKids=(rows:Conversation[])=>rows.flatMap(c=>[c,...(kids.get(c.id)??[])]);
  const shownOrder=withKids([...groups,...(privates?[privates]:[])].flatMap(group=>[...group.threads,...group.folded])).filter(c=>!(c as SidebarThread).gone);
  const openNeeds=shownOrder.find(c=>current(c.id)&&(c.activity?.status==='needs'||(c.notesWaiting??0)>0));
  const accentId=openNeeds?.id??shownOrder.find(c=>!current(c.id)&&rowGlyph(c).state==='needs'&&c.activity?.attention!==false)?.id;
  // The rows on screen, in order, for Shift-click. A thread folded behind "N more" can't be picked by range.
  const visibleOrder=[...groups,...(privates?[privates]:[])].flatMap(group=>shownIn(group)).flatMap(c=>[c,...kidsShown(c).rows]).filter(c=>!(c as SidebarThread).gone);
  const selected=visibleOrder.filter(c=>picked.includes(c.id));
  const selecting=!!props.onArchiveMany;
  function pick(conversation:Conversation,event:MouseEvent){
    const open=conversationActive&&visibleOrder.some(c=>c.id===channelId)?channelId:null;
    const base=picked.length?picked:open?[open]:[];
    if(event.shiftKey){
      const from=anchor??open??conversation.id;
      const ids=visibleOrder.map(c=>c.id),a=ids.indexOf(from),b=ids.indexOf(conversation.id);
      if(a<0||b<0){setPicked([conversation.id]);setAnchor(conversation.id);return;}
      const range=ids.slice(Math.min(a,b),Math.max(a,b)+1);
      setPicked([...new Set([...(event.metaKey||event.ctrlKey?base:[]),...range])]);
      if(!anchor)setAnchor(from);
      return;
    }
    setPicked(base.includes(conversation.id)?base.filter(id=>id!==conversation.id):[...base,conversation.id]);
    setAnchor(conversation.id);
  }
  function clearPicked(){setPicked([]);setAnchor(null);}
  async function archivePicked(){
    if(!props.onArchiveMany||!selected.length||archivingMany)return;
    setArchivingMany(true);
    try{await props.onArchiveMany(selected);clearPicked();}
    finally{setArchivingMany(false);}
  }
  const archivePickedNow=useRef(archivePicked);archivePickedNow.current=archivePicked;
  useEffect(()=>{
    if(!selected.length)return;
    // Capture, so ⌘⇧A takes the selection and ConnectedApp's own shortcut (the open thread only) sees it handled.
    const onKey=(event:KeyboardEvent)=>{
      if(document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"], [data-dv-surface="dialog"]'))return;
      if(archiveKey(event)){event.preventDefault();event.stopPropagation();void archivePickedNow.current();return;}
      if(event.key==='Escape'&&!event.defaultPrevented&&!editableTarget(event.target)){event.preventDefault();clearPicked();}
    };
    window.addEventListener('keydown',onKey,true);
    return()=>window.removeEventListener('keydown',onKey,true);
  },[selected.length]);
  async function signOut(){
    if(signingOut)return;
    setSigningOut(true);setSignOutError('');
    try{await props.onSignOut();setConfirmSignOut(false);setMeOpen(false);}
    catch(failure){setSignOutError(failure instanceof Error?failure.message:String(failure));}
    finally{setSigningOut(false);}
  }
  /** The row's trailing figure: its time, tabular. */
  function meta(conversation:Conversation){
    const when=threadWhen(conversation.activity);
    if(pinned.has(conversation.id))return <span className="dv-meta dv-sb-pinned" title={t('Pinned. Never tidied away.')}><Pin aria-hidden="true"/>{when}<span className="sr-only">{t('Pinned')}</span></span>;
    // Always a slot, even with no time, so the ⋯ that fades in over it never lands on the name (design, 8 Oct).
    return <span className="dv-meta" aria-hidden={!when||undefined}>{when}</span>;
  }
  /** A parent's subthreads as they show: all of them when it's open; when it's folded, only one that needs you or is
   * the one you have open, like the open thread in a folded workstream. */
  function kidsShown(parent:Conversation):{rows:Conversation[];open:boolean;all:Conversation[]}{
    const all=kids.get(parent.id)??[];
    const open=!foldedKids.has(parent.id);
    return {all,open,rows:open?all:all.filter(c=>current(c.id)||rowGlyph(c).state==='needs'||picked.includes(c.id)||!!(c as SidebarThread).gone)};
  }
  const row=(item:Conversation)=>{
    const family=kidsShown(item);
    if(!family.all.length)return single(item);
    const {all,open,rows}=family,loudest=rollup(all),said=rollupLine(all);
    const chip=<button type="button" className="dv-sb-kids" aria-expanded={open} data-roll={open?undefined:loudest}
      aria-label={`${said}. ${open?t('Hide them'):t('Show them')}`} title={said} onClick={()=>setKidsFolded([item.id],open)}>
      {!open&&loudest!=='idle'&&<StatusGlyph state={loudest} accent={all.some(c=>c.id===accentId)}/>}
      <span className="dv-sb-kids-n">{all.length}</span>{open?<ChevronDown size={11} aria-hidden="true"/>:<ChevronRight size={11} aria-hidden="true"/>}
    </button>;
    return <Fragment key={item.id}>
      {single(item,{chip,family:rows.length?(open?'open':'peek'):'closed'})}
      {rows.length>0&&<div role="group" aria-label={t('Subthreads of {name}',{name:item.name})} className="dv-sb-kids-rows">
        {rows.map((kid,index)=>single(kid,{child:index===rows.length-1?'last':'child'}))}
      </div>}
    </Fragment>;
  };
  const single=(item:Conversation,family?:{chip?:ReactNode;family?:'open'|'peek'|'closed';child?:'child'|'last'})=>{
    const gone=(item as SidebarThread).gone;
    if(gone)return <GoneRow key={item.id} entry={gone} onUndo={props.onUndo}/>;
    // For a few seconds after a rename the row's time reads Undo (row-rename.html, interactions.md).
    const trailing=props.renamed===item.id?<button type="button" className="dv-sb-undo" onClick={props.onUndo} aria-label={t('Undo renaming {name}',{name:item.name})}>{t('Undo')}</button>:meta(item);
    const note=notes.find(row=>row.id===item.id);
    return <ThreadRow key={item.id} conversation={item} accent={item.id===accentId} current={current(item.id)} meta={trailing} menu={props.threadMenu} chip={family?.chip} family={family?.family} child={family?.child}
      pinned={pinned.has(item.id)} onPin={next=>void setPinned(item,next)}
      renaming={props.renaming?.id===item.id?props.renaming:null} onRename={name=>props.onRename(item,name)} error={props.rowErrors?.[item.id]} arrived={props.arrived===item.id}
      selected={picked.includes(item.id)}
      onOpen={event=>{if(selecting&&(event.metaKey||event.ctrlKey||event.shiftKey)){pick(item,event);return;}if(picked.length)clearPicked();props.onSelectConversation(item.id);}}
      note={note&&<ActivityLine className="dv-sb-row-note" live note={item.id} text={note.error??note.line}
        action={note.error?{label:t('Dismiss'),describe:t('Dismiss: {line} {name}',{line:note.error,name:item.name}),onClick:()=>setNotes(rows=>rows.filter(row=>row!==note))}
          :note.revision!==null?{label:t('Undo'),describe:t('Undo: {line} {name}',{line:note.line,name:item.name}),onClick:()=>void undoNote(note)}:null}/>}/>;
  };
  /** The rows a group shows, in order. A picked row stays out from behind "N more", like the open one. */
  function shownIn(group:ThreadGroup):Conversation[]{
    const open=opened.has(group.key);
    const keep=(c:Conversation)=>[c,...(kids.get(c.id)??[])].some(row=>current(row.id)||!!(row as SidebarThread).gone||props.renaming?.id===row.id||picked.includes(row.id));
    const all=[...group.threads,...group.folded];
    if(staleGroup(group,Date.now(),kids)&&!all.some(keep)&&!open)return [];
    return open?all:[...group.threads,...group.folded.filter(keep)];
  }
  /** A group of threads: its label and count, then the rows. Quiet ones fold behind "N more", except the one you have open.
   * A group whose threads have all been quiet for three days folds to its header alone, as a button with the count
   * (item 8); a thread that's running, needs you, is unopened, is open, is being renamed or just left keeps it open. */
  const threadGroup=(group:ThreadGroup,note?:ReactNode)=>{
    const open=opened.has(group.key);
    const keep=(c:Conversation)=>[c,...(kids.get(c.id)??[])].some(row=>current(row.id)||!!(row as SidebarThread).gone||props.renaming?.id===row.id||picked.includes(row.id));
    const all=[...group.threads,...group.folded];
    const stale=staleGroup(group,Date.now(),kids)&&!all.some(keep);
    const shown=shownIn(group);
    const hidden=open?0:group.folded.filter(c=>!keep(c)).length;
    const count=withKids(all).filter(c=>!(c as SidebarThread).gone).length;
    const unfold=()=>setOpened(previous=>new Set(previous).add(group.key));
    const refold=()=>setOpened(previous=>{const next=new Set(previous);next.delete(group.key);return next;});
    if(stale&&!open)return <div key={group.key} role="group" aria-label={group.title} className="dv-sb-group" data-stale="true">
      <button type="button" className="dv-sb-group-head dv-sb-group-stale" aria-expanded={false} title={`Quiet for ${STALE_DAYS} days. Show ${count===1?'the thread':`all ${count}`}`} onClick={unfold}>
        <span className="dv-label">{group.title}</span><span className="dv-count">{count}</span><ChevronRight size={12} aria-hidden="true"/>
      </button>
    </div>;
    return <div key={group.key} role="group" aria-label={group.title} className="dv-sb-group">
      <div className="dv-sb-group-head" aria-hidden="true"><span className="dv-label">{group.title}</span>{count>0&&<span className="dv-count">{count}</span>}</div>
      {note}
      {shown.map(row)}
      {hidden>0&&<button type="button" className="dv-sb-fold" onClick={unfold}>{hidden} more</button>}
      {open&&(group.folded.length>0||stale)&&<button type="button" className="dv-sb-fold" onClick={refold}>{t('Show fewer')}</button>}
    </div>;
  };
  const leave=(run:(trigger:HTMLElement)=>void)=>()=>{opening.current=true;if(meTrigger.current)run(meTrigger.current);};
  return <nav className="dv-sb dv-sb-app" aria-label={t('Project')} data-testid="dervo-sidebar">
    <ProjectPicker projects={projects} projectId={projectId} loading={props.projectsLoading} failed={props.projectsFailed} onRetry={props.onRetryProjects}
      onSelect={props.onSelectProject} onAdd={props.onAddProject} onSettings={trigger=>props.onAction('settings',trigger)}/>

    <button type="button" className="dv-sb-search dervo-sb-jump" disabled={!ready} onClick={event=>props.onJump(event.currentTarget)}><Search size={14} aria-hidden="true"/>Search or jump to<kbd aria-hidden="true">⌘K</kbd></button>

    <div className="dv-sb-nav">
      <button type="button" className="dv-sb-item" disabled={!ready} aria-current={active==='orchestrator'?'page':undefined} onClick={event=>props.onAction('orchestrator',event.currentTarget)}>
        <Home size={16} aria-hidden="true"/><span className="dv-name">{t('Home')}</span>{needs>0&&<span className="dv-meta" aria-label={t('{count} need you',{count:needs})}>{needs}</span>}
      </button>
      {/* History left the nav (design, 8 Oct): it's rarely used, and ⌘K still opens it. */}
      <button type="button" className="dv-sb-item" disabled={!ready} aria-current={active==='knowledge'?'page':undefined} onClick={event=>props.onAction('knowledge',event.currentTarget)}>
        <BookOpen size={16} aria-hidden="true"/><span className="dv-name">{t('Knowledge')}</span>
        {props.knowledgeUnread?<span className="dv-meta dv-sb-dot" aria-label={t('New things kept since you last looked')}/>
          :!!props.knowledgeCount&&<span className="dv-meta" aria-label={t('{knowledgeCount} to review',{knowledgeCount:props.knowledgeCount})}>{props.knowledgeCount}</span>}
      </button>
      {/* One Next up per project, in place of Bugs and ideas and the admin's Dervo reports (b16/lifecycle). */}
      <button type="button" className="dv-sb-item" disabled={!ready} aria-current={active==='bugs'||active==='reports'?'page':undefined} onClick={event=>props.onAction('bugs',event.currentTarget)}>
        <ClipboardList size={16} aria-hidden="true"/><span className="dv-name">{t('Next up')}</span>{unseenReports>0&&<span className="dv-meta" aria-label={`${unseenReports} new tester ${unseenReports===1?'report':'reports'}`}>{unseenReports}</span>}
      </button>
    </div>

    <div className="dv-sb-list" data-testid="dervo-sidebar-conversations">
      {groups.map(group=>threadGroup(group))}
      {privates&&threadGroup(privates,<p className="dv-sb-group-note">{t('Home doesn’t read these.')}</p>)}
      <DropdownMenu><DropdownMenuTrigger asChild>
        <button type="button" className="dv-sb-item dv-sb-new" disabled={!ready}><Plus size={16} aria-hidden="true"/><span className="dv-name">{t('New thread')}</span></button>
      </DropdownMenuTrigger><DropdownMenuContent align="start" className="dv-menu-new" aria-label={t('New thread')}>
        <DropdownMenuItem className="dv-mi-two" onSelect={()=>props.onCreate(false)}><Plus size={16} strokeWidth={1.5}/><span className="dv-mi-t"><span>{t('Shared')}</span><small>{t('Home sees it and keeps it filed')}</small></span><span className="dv-mi-end" aria-hidden="true">⌘N</span></DropdownMenuItem>
        <DropdownMenuItem className="dv-mi-two" onSelect={()=>props.onCreate(true)}><Lock size={16} strokeWidth={1.5}/><span className="dv-mi-t"><span>{t('Private')}</span><small>{t('Home doesn’t see it')}</small></span></DropdownMenuItem>
      </DropdownMenuContent></DropdownMenu>
      {tidiedHere&&<p className="dv-activity dv-sb-tidied" role="status">
        <span className="dv-activity-text">{tidyError||tidiedLine(tidiedHere.count)}</span>
        <button type="button" className="dv-activity-action" aria-label={`Undo: ${tidiedLine(tidiedHere.count)}`} onClick={()=>void undoTidied()}>{t('Undo')}</button>
        <button type="button" className="dv-activity-action" onClick={()=>{setFoldOpen(true);requestAnimationFrame(()=>revealArchived(document.querySelector<HTMLElement>('[data-testid="archived-conversations"]')));}}>{t('See them')}</button>
      </p>}
      {projectId&&onArchivedDelete?<ArchivedFold projectId={projectId} open={foldOpen} onOpenChange={setFoldOpen} days={days}
        onOpen={conversation=>props.onSelectConversation(conversation.id)} onRestored={conversation=>onArchivedRestored(conversation)}
        onDelete={(conversation:ArchivedThread)=>onArchivedDelete(conversation)} onDeleteAll={props.onDeleteArchived}/>:props.archived}
      {selected.length>0&&<SelectionBar count={selected.length} busy={archivingMany} onArchive={()=>void archivePicked()} onClear={clearPicked}/>}
    </div>

    <div className="dv-sb-foot">
      {props.accounts.length>0&&<div className="dv-sb-usage" data-testid="dervo-sidebar-usage" data-fold={usageFold(props.accounts)}>
        {(()=>{
          // One line, whatever the accounts say (app #13): a click opens the popover with the whole picture.
          const one=usageOneLine(props.accounts);
          const lifted=props.accounts.some(account=>account.profile_id===props.usageLifted);
          return <button type="button" className="dv-usage dv-usage-one" data-low={one.low} data-profile={one.parts[0]?.id??props.accounts[0].profile_id} title={one.detail}
            aria-haspopup="dialog" aria-expanded={lifted} onClick={event=>props.onUsage(event.currentTarget)}>
            {/* "Left" once, first, so 12% isn't read as 12% used; only when every figure is a percentage, so "4 runs" never reads as left. */}
            {one.parts.every(part=>part.value.includes('%'))&&<span className="dv-usage-left">{t('Left')} </span>}
            {one.parts.map((part,index)=><span key={part.id} className="dv-usage-part" data-low={part.low||undefined}>{index>0&&<span className="dv-usage-sep" aria-hidden="true">·</span>}<b>{part.name}</b> <span className="dv-pct">{part.value}</span></span>)}
            <span className="sr-only">{one.detail}</span>
          </button>;
        })()}
      </div>}
      <DropdownMenu open={meOpen} onOpenChange={next=>{if(signingOut)return;setMeOpen(next);if(!next){setConfirmSignOut(false);setSignOutError('');}}}><DropdownMenuTrigger asChild>
        <button ref={meTrigger} type="button" className="dv-me dervo-sb-me" aria-label={update?t('Your account. A newer version of Dervo is ready.'):t('Your account')}>
          <ProfileAvatar image={props.accountPhoto} name={props.accountName}/>
          <span className="dv-me-copy"><b>{props.accountName||t('You')}</b><small>{props.accountNote}</small></span>
          <ChevronsUpDown className="dv-chev" size={13} aria-hidden="true"/>
          {update&&<span className="dv-update-dot" aria-hidden="true"/>}
        </button>
      </DropdownMenuTrigger><DropdownMenuContent side="top" align="start" className="dv-menu-account" aria-label={t('Your account')} onCloseAutoFocus={event=>{if(opening.current){event.preventDefault();opening.current=false;}}}>
        {confirmSignOut?<SignOutConfirm busy={signingOut} error={signOutError} onSignOut={()=>void signOut()}/>:<>
          {props.accountName&&<><div className="dv-mhead"><small>{t('Signed in as')}</small><b>{props.accountName}</b></div><DropdownMenuSeparator/></>}
          <DropdownMenuItem onSelect={leave(props.onWorkspaceSettings)}><Settings size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Settings')}</span><span className="dv-mi-end" aria-hidden="true">{update&&<span className="dv-update-dot"/>} ⌘,</span></DropdownMenuItem>
          <DropdownMenuItem onSelect={leave(props.onConnectedAccounts)}><Plug size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Connected accounts')}</span></DropdownMenuItem>
          {props.onReleases&&<DropdownMenuItem onSelect={()=>{opening.current=true;props.onReleases?.();}}><Package size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Releases')}</span></DropdownMenuItem>}
          <DropdownMenuItem onSelect={()=>{opening.current=true;props.onFeedback();}}><MessageSquare size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Send feedback')}</span></DropdownMenuItem>
          {props.canSignOut&&<><DropdownMenuSeparator/><DropdownMenuItem onSelect={event=>{event.preventDefault();setSignOutError('');setConfirmSignOut(true);}}><LogOut size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Sign out…')}</span></DropdownMenuItem></>}
        </>}
      </DropdownMenuContent></DropdownMenu>
    </div>
  </nav>;
}

/** Sign out, confirmed in the account menu itself (tier 2): nothing is lost, but getting back needs an email code. */
function SignOutConfirm({busy,error,onSignOut}:{busy:boolean;error:string;onSignOut:()=>void}){
  const cancel=useRef<HTMLButtonElement>(null);
  useEffect(()=>{requestAnimationFrame(()=>cancel.current?.focus());},[]);
  return <div className="dv-mconfirm" role="alertdialog" aria-labelledby="dv-signout-q">
    <p className="dv-q" id="dv-signout-q">{t('Sign out of Dervo?')}</p>
    <p className="dv-small" style={{color:'var(--dv-ink-2)',lineHeight:1.5}}>{t('Only your Dervo session is removed. Your projects, threads and connected accounts stay on this Mac. You’ll need a new email code to sign back in.')}</p>
    {error&&<p className="dv-merror" role="alert">{error}</p>}
    <div className="dv-acts">
      <DropdownMenuPrimitive.Item asChild disabled={busy}><button ref={cancel} type="button" className="dv-btn dv-btn-quiet">{t('Cancel')}</button></DropdownMenuPrimitive.Item>
      <DropdownMenuPrimitive.Item asChild onSelect={event=>{event.preventDefault();onSignOut();}}><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-disabled={busy||undefined}>{busy?t('Signing out…'):t('Sign out')}</button></DropdownMenuPrimitive.Item>
    </div>
  </div>;
}

/** A thread row: its glyph, its name (or the field that renames it), its time, and ⋯. Right-click and ⋯ open the same
 * menu, and the row stays lifted while it's open. */
function ThreadRow({conversation,accent,current,meta,menu,renaming,onRename,error,arrived,onOpen,note,pinned=false,onPin,selected=false,chip,family,child}:{conversation:Conversation;accent:boolean;current:boolean;meta:ReactNode;
  menu:ThreadMenuModel;renaming:Renaming|null;onRename:(name:string|null)=>void;error?:string;arrived:boolean;onOpen:(event:MouseEvent)=>void;note?:ReactNode;pinned?:boolean;onPin?:(next:boolean)=>void;selected?:boolean;
  /** Beta.17: a parent's count chip and whether its subthreads show under it, or that this row is a subthread. */
  chip?:ReactNode;family?:'open'|'peek'|'closed';child?:'child'|'last'}){
  const [held,setHeld]=useState<''|'dropdown'|'context'>(''),[confirming,setConfirming]=useState(false);
  const leaving=useRef(false);
  // The open thread waiting for your review reads as the decision it holds, a "needs you" mark, not a quiet check.
  const found=rowGlyph(conversation);
  const glyph=accent&&current&&found.state==='done'?{state:'needs' as const,label:found.label}:found;
  const unseen=!!conversation.activity?.unread&&!current;
  const model:ThreadMenuModel={...menu,rename:c=>{leaving.current=true;menu.rename(c);},
    openBlocker:menu.openBlocker?blocker=>{leaving.current=true;menu.openBlocker?.(blocker);}:undefined};
  const change=(kind:'dropdown'|'context')=>(open:boolean)=>{setHeld(open?kind:'');if(!open)setConfirming(false);};
  const closeFocus=(event:Event)=>{if(leaving.current){event.preventDefault();leaving.current=false;}};
  return <>
    <ContextMenu onOpenChange={change('context')}><ContextMenuTrigger asChild disabled={!!renaming}>
      <div className={`dv-sb-item dv-sb-thread${held?' dv-held':''}${renaming?' dv-renaming':''}${arrived?' dv-arrived':''}${family?' dv-sb-parent':''}${child?' dv-sb-child':''}${child==='last'?' dv-sb-child-last':''}`} aria-current={current?'page':undefined} data-conversation-id={conversation.id} data-selected={selected||undefined} data-family={family}>
        <StatusGlyph state={glyph.state} accent={accent} label={glyph.label}/>
        {renaming?<RenameField name={conversation.name} busy={!!renaming.busy} onDone={onRename}/>
          :<button type="button" className="dv-sb-open" title={conversation.name} onClick={onOpen} aria-pressed={selected||undefined}
            onMouseDown={event=>{if(event.shiftKey||event.metaKey)event.preventDefault();}}
            onKeyDown={event=>{if(event.key==='Enter'&&event.altKey){event.preventDefault();model.rename(conversation);}}}>
            <span className={`dv-name${unseen?' dv-unseen':''}`}>{conversation.name}</span>
            {unseen&&<span className="sr-only">{conversation.activity!.unread} unopened</span>}
          </button>}
        {!renaming&&chip}
        {!renaming&&meta}
        {!renaming&&<DropdownMenu onOpenChange={change('dropdown')}><DropdownMenuTrigger asChild>
          <button type="button" className="dv-sb-more" aria-label={t('Options for {name}',{name:conversation.name})}><MoreHorizontal size={14}/></button>
        </DropdownMenuTrigger><DropdownMenuContent align="start" side="right" sideOffset={6} className="dv-menu-thread" aria-label={conversation.name} onCloseAutoFocus={closeFocus}>
          {!confirming&&onPin&&<PinItem kind="dropdown" pinned={pinned} onPin={onPin}/>}
          <ThreadMenuBody kind="dropdown" conversation={conversation} model={model} confirming={confirming} onConfirm={setConfirming}/>
        </DropdownMenuContent></DropdownMenu>}
        {renaming&&!renaming.error&&<span className="dv-tip" role="tooltip">{t('↵ Save')} <span className="dv-sc">{t('esc Cancel')}</span></span>}
      </div>
    </ContextMenuTrigger><ContextMenuContent className="dv-menu-thread" aria-label={conversation.name} onCloseAutoFocus={closeFocus}>
      {!confirming&&onPin&&<PinItem kind="context" pinned={pinned} onPin={onPin}/>}
      <ThreadMenuBody kind="context" conversation={conversation} model={model} confirming={confirming} onConfirm={setConfirming}/>
    </ContextMenuContent></ContextMenu>
    {(renaming?.error||error)&&<p className="dv-sb-row-error" role="alert">{renaming?.error||error}</p>}
    {note}
  </>;
}

/** Pin, at the top of a thread's menu (design b16-lifecycle, pin): "keep this", so it's never tidied away. It doesn't
 * change the order; the row keeps its place with a small pin before its time. */
function PinItem({kind,pinned,onPin}:{kind:'dropdown'|'context';pinned:boolean;onPin:(next:boolean)=>void}){
  const Item=kind==='dropdown'?DropdownMenuItem:ContextMenuItem;
  return <Item className="dv-mi-two" onSelect={()=>onPin(!pinned)}>
    {pinned?<PinOff size={16} strokeWidth={1.5}/>:<Pin size={16} strokeWidth={1.5}/>}
    <span className="dv-mi-t"><span>{pinned?t('Unpin'):t('Pin')}</span><small>{pinned?t('Dervo can tidy it away'):t('Never tidied away')}</small></span>
  </Item>;
}

/** The name as a field: its text selected, Return saves, Escape puts it back. Leaving the field saves, as Finder does. */
function RenameField({name,busy,onDone}:{name:string;busy:boolean;onDone:(name:string|null)=>void}){
  const [value,setValue]=useState(name);
  const field=useRef<HTMLInputElement>(null),done=useRef(false);
  useEffect(()=>{requestAnimationFrame(()=>{field.current?.focus();field.current?.select();});},[]);
  function finish(next:string|null){if(done.current)return;done.current=true;onDone(next===null||next.trim()===''||next.trim()===name?null:next.trim());requestAnimationFrame(()=>{done.current=false;});}
  return <input ref={field} className="dv-rename" value={value} maxLength={100} aria-label={t('Rename {name}',{name})} disabled={busy}
    onChange={event=>setValue(event.target.value)}
    onKeyDown={event=>{event.stopPropagation();if(event.key==='Enter'){event.preventDefault();finish(value);}if(event.key==='Escape'){event.preventDefault();finish(null);}}}
    onBlur={()=>finish(value)}/>;
}

/** The row an archive or a move left: what happened, and Undo for a few seconds. */
function GoneRow({entry,onUndo}:{entry:UndoEntry;onUndo:()=>void}){
  return <div className="dv-sb-item dv-gone" role="status">
    {entry.kind==='archived'?<Archive size={16} strokeWidth={1.5} aria-hidden="true"/>:<FolderInput size={16} strokeWidth={1.5} aria-hidden="true"/>}
    <span className="dv-name" title={entry.conversation.name}>{entry.label}</span>
    <button type="button" className="dv-sb-undo" onClick={onUndo} aria-label={entry.others?.length?t('Undo: {label}',{label:entry.label}):t('Undo: {label}, {name}',{label:entry.label,name:entry.conversation.name})}>{t('Undo')}</button><span className="dv-sc" aria-hidden="true">⌘Z</span>
  </div>;
}

/** What the selection can do, pinned to the foot of the thread list while two or more rows (or one besides the open
 * thread) are picked: the count, Archive (⌘⇧A) and Clear (Escape). */
function SelectionBar({count,busy,onArchive,onClear}:{count:number;busy:boolean;onArchive:()=>void;onClear:()=>void}){
  const threads=`${count} ${count===1?'thread':'threads'}`;
  return <div className="dv-sb-selection" role="toolbar" aria-label={t('{threads} selected',{threads})}>
    <span className="dv-sb-selection-count" aria-live="polite">{count} selected</span>
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-label={t('Archive {threads}',{threads})} aria-disabled={busy||undefined} onClick={()=>{if(!busy)onArchive();}}>
      <Archive size={14} strokeWidth={1.6} aria-hidden="true"/>{busy?t('Archiving…'):t('Archive')}<span className="dv-sc" aria-hidden="true">⌘⇧A</span></button>
    <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={onClear} title={t('Clear the selection (Escape)')}>{t('Clear')}</button>
  </div>;
}

/** The parents whose subthreads are folded, per project, kept on this Mac only. */
const foldKey=(projectId:string)=>`dervo:subthreads-folded:${projectId}`;
function readFoldedKids(projectId:string):Set<string>{
  try{const saved=JSON.parse(localStorage.getItem(foldKey(projectId))??'[]');return new Set(Array.isArray(saved)?saved.filter((id:unknown)=>typeof id==='string'):[]);}
  catch{return new Set();}
}
function writeFoldedKids(projectId:string,folded:Set<string>){
  try{if(folded.size)localStorage.setItem(foldKey(projectId),JSON.stringify([...folded]));else localStorage.removeItem(foldKey(projectId));}catch{/* private mode: it just doesn't persist */}
}
