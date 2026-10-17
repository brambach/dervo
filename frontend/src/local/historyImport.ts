import {local,type Conversation} from './api';
import type {ArchivedConversation} from './ConversationLifecycle';
import {formatDate,formatList,t} from '@/i18n';

/** Reading what's already here (notes/PLAN-import.md; the mockup is evidence/design-tests/onboarding/index.html).
 *
 * A project you've just added has nothing in it, so the one thing Dervo is for (knowing where everything's up to) has
 * nothing to know about. But Claude Code and Codex already wrote their transcripts for that folder to this Mac. On a
 * blank project Dervo offers to read them, name and file each thread with the same rules a live thread gets (P1 and P3,
 * no model), and arrive on Home with the open ones already there.
 *
 * This file is the seam. The reader is the host's (backend/history_import.py, answered through
 * backend/history_import_host.py since 21 Sep); nothing here parses a transcript. The shapes below are what the screens
 * are coded against and what the host returns; the fixture in historyImportFixture.ts fills them for the tests and the
 * design shots only. The two operations are `history_look` and `history_read`, both `{projectId}`. */

/** What's on disk for this project's folder, before anything is read: counts and times only, no message content.
 * The Found screen shows these before asking. `null` from the host means nothing was written for this folder.
 * `conversations` here is what the look could tell from the head of each file (something typed); the read refines
 * it, since only a full read knows about replies, forks and copies. */
export type HistoryLook={folder:string;conversations:number;sessions:number;sources:('claude'|'codex')[];first:number|null;last:number|null};
export type HistorySource='claude'|'codex';
/** The rungs of file_by_rules in the order it tries them (project_ideas.py), named as the host names them. Two of them
 * make a workstream ("topic, new workstream" and "new workstream"): `madeNew` is the test for that. */
export type HistoryRule='name overlap'|'filed by hand'|'topic'|'topic, new workstream'|'thread overlap'|'follow-up'|'similar name'|'new workstream'
 |'at the cap, member word'|'at the cap, most recent';
export const madeNew=(rule:HistoryRule|string|null|undefined)=>!!rule&&rule.endsWith('new workstream');
/** A thread the reader made. `id` is the conversation the host created for it, so it opens like any other. `sessions`
 * counts the transcripts folded into it (forks, Codex's copies). `rule` is the rung that placed this thread. */
export type HistoryThread={id:string;name:string;last_active:number;open:boolean;source:HistorySource;sessions:number;rule?:HistoryRule|null;toolRun?:boolean};
/** A workstream, with the rule that placed its first thread: the filing that made it or first found it. */
export type HistoryWorkstream={id:string;name:string;rule:HistoryRule;threads:HistoryThread[]};
/** The newest thread that stopped on a question to you. `choices` are the reader's when it can offer them; with none,
 * the Answer screen takes a typed answer. `did` is what the agent did before it asked, when the transcript says.
 * The host answers null for now (history_import_host.py says why), so the Sorting screen goes straight to Home. */
export type HistoryQuestion={thread_id:string;thread_name:string;workstream:string;asked_at:number;asked:string;did:string|null;question:string;choices:string[]};
/** What one read produced. `counts` is the collapse line (conversations made, sessions read, and `working`, the
 * things you're actually working on, which is the workstream count).
 * `set_aside` is how many sessions had nothing to pick up. `read` is what the reader looked at, in order, and whether
 * each survived: the Reading screen replays it. `already` is how many sessions a thread already carried the id of
 * (a second read makes nothing); `unnamed` how many the rules couldn't name; `unfiled` the threads made but placed in
 * no workstream, which the host only does when nothing may be made. */
export type HistoryImport={folder:string;counts:{conversations:number;sessions:number;working:number};set_aside:number;
 read:{label:string;kept:boolean}[];workstreams:HistoryWorkstream[];question:HistoryQuestion|null;at:number;
 already?:number;unnamed?:number;unfiled?:HistoryThread[]};

/** Counts and times for this project's folder. A host without the reader refuses, and the caller treats that as nothing
 * to offer. */
export const lookHistory=(projectId:string)=>local<HistoryLook|null>('history_look',{projectId});
/** Read, name and file. One call, one result: the host makes the conversations and workstreams before it answers. */
/** One conversation the picker offers. `unnamed`: the naming rules abstained on its first message, so `name` is empty
 * and the screen says so and lets you name it (NEXT-UP #37). `toolRun`: a tool or script ran it (Codex `exec`, `mcp`;
 * `ranBy` is which), offered only when you ask for those (NEXT-UP #38). */
export type HistoryCandidate={sessionId:string;name:string;source:HistorySource;last_active:number|null;reason:string;recommended:boolean;excerpt:string;
 unnamed?:boolean;toolRun?:boolean;ranBy?:string|null};
/** `toolRuns` is how many tool-run sessions the folder has to offer, asked for or not, so the option shows only where it does something. */
export type HistoryPreview={candidates:HistoryCandidate[];limit:number;toolRuns?:number};
export const previewHistory=(projectId:string,includeToolRuns=false)=>local<HistoryPreview>('history_preview',{projectId,...(includeToolRuns?{includeToolRuns}:{})});
/** `names` names the picked conversations the rules couldn't; `includeToolRuns` has to come with any tool-run session picked. */
export type ReadOptions={names?:Record<string,string>;includeToolRuns?:boolean};
export const readHistory=(projectId:string,sessionIds?:string[],options:ReadOptions={})=>local<HistoryImport>('history_read',{projectId,...(sessionIds?{sessionIds}:{}),
 ...(sessionIds&&options.names&&Object.keys(options.names).length?{names:options.names}:{}),...(sessionIds&&options.includeToolRuns?{includeToolRuns:true}:{})});

/** A picked conversation's name for the host: what you typed, else a plain one with its date so two don't read alike. */
export function nameFor(candidate:HistoryCandidate,typed:string|undefined):string{
 const given=(typed??'').replace(/\s+/g,' ').trim().slice(0,100);
 if(given)return given;
 return candidate.last_active?t('Unnamed conversation, {date}',{date:formatDate(candidate.last_active*1000,{day:'numeric',month:'short'})}):t('Unnamed conversation');
}
/** The names to send: one for each picked conversation the rules couldn't name. */
export function namesFor(candidates:HistoryCandidate[],picked:Iterable<string>,typed:Record<string,string>):Record<string,string>{
 const ids=new Set(picked);
 return Object.fromEntries(candidates.filter(c=>c.unnamed&&ids.has(c.sessionId)).map(c=>[c.sessionId,nameFor(c,typed[c.sessionId])]));
}
/** The line under a candidate's name: the tool, who ran it when a tool did, and the reader's reason. */
export function candidateLine(candidate:HistoryCandidate,reason:string):string{
 const tool=candidate.source==='claude'?'Claude Code':'Codex';
 return [tool,candidate.toolRun?t('Run by a tool ({source}), not you',{source:candidate.ranBy??'exec'}):'',reason].filter(Boolean).join(' · ');
}
/** Answer the open question in its thread: the same request a typed message would send (Home's sendJob shape), so the
 * agent picks the thread up where the transcript stopped. */
export const answerHistory=(projectId:string,channelId:string,text:string)=>
 local<{id:string|null;state?:string;detail?:string}>('coding_request',{clientId:`history-${channelId}`,text,projectId,channelId,queueIfBusy:true,origin:'home',clicked:true});

// ---------------------------------------------------------------- is this workspace blank?
/** Three states, from the host's two lists (21 Sep, Bryce: "I just archived everything, pretty much"). `conversations`
 * is the live list the sidebar reads, which the host already filters against conversation_archives;
 * `conversations_archived` is the archived rows with their archived_at. So `blank` means both are empty, never a count
 * of visible rows: a project with forty archived threads is `archived`, and re-reading its history would bring back
 * what was put away on purpose. */
export type BlankState='loading'|'blank'|'archived'|'live';
/** Threads Dervo made for you, not work you started: the overview it writes for a new project and the starter
 * thread beside it. The host marks both at creation (`conversation_origins`), so neither has to be guessed at by
 * name, which would break the moment someone renamed one. A project holding only these is still blank. */
const yours=(threads:{origin?:string|null}[])=>threads.filter(thread=>!thread.origin);

export function blankState(live:Conversation[]|undefined,archived:ArchivedConversation[]|undefined):BlankState{
 if(!live)return 'loading';
 if(yours(live).length)return 'live';
 if(!archived)return 'loading';
 // A project whose only archived thread is its own overview reads as blank, not as work you put away on purpose.
 return yours(archived).length?'archived':'blank';
}

/** Whether you've said no to reading this project's history, kept per viewer like the last-open thread. Storage
 * failures only lose the convenience: the offer shows again. */
const declinedKey=(projectId:string)=>`dervo-history-declined:${projectId}`;
export function historyDeclined(projectId:string):boolean{try{return localStorage.getItem(declinedKey(projectId))==='1';}catch{return false;}}
export function rememberHistoryDeclined(projectId:string){try{localStorage.setItem(declinedKey(projectId),'1');}catch{/* Convenience only. */}}

// ---------------------------------------------------------------- copy, from the data
export const tildePath=(path:string)=>path.replace(/^\/Users\/[^/]+(?=\/|$)/,'~');
/** Who wrote the history, as the Found screen names it. */
export function sourcesLabel(sources:HistorySource[]):string{
 const names=[...new Set(sources)].map(source=>source==='codex'?'Codex':'Claude Code');
 if(!names.length)return t('Claude Code and Codex');
 return formatList(names);
}
/** A moment in the Found lede: "16 minutes ago", "yesterday", "22 Aug". */
export function agoLabel(at:number,now=Date.now()/1000):string{
 const gap=Math.max(0,now-at);
 if(gap<60)return t('just now');
 if(gap<3600)return t('{minutes} min ago',{minutes:Math.round(gap/60)});
 if(gap<86400)return t('{hours} h ago',{hours:Math.round(gap/3600)});
 const days=Math.round((new Date(now*1000).setHours(0,0,0,0)-new Date(at*1000).setHours(0,0,0,0))/86400000);
 if(days===1)return t('yesterday');
 if(days<7)return formatDate(at*1000,{weekday:'long'});
 return formatDate(at*1000,{day:'numeric',month:'short'});
}
/** The Found screen's one sentence: how much there is, where, and how far back it goes. One folder, so the folder
 * takes the place the mockup gave to "310 projects". */
export function foundLede(look:HistoryLook,now=Date.now()/1000):{count:string;where:string;span:string}{
 const count=t('{count} conversations',{count:look.conversations});
 const span=look.first&&look.last?t('the first from {first} and the last {last}',{first:agoLabel(look.first,now),last:agoLabel(look.last,now)})
  :look.last?t('the last {last}',{last:agoLabel(look.last,now)}):'';
 return {count,where:tildePath(look.folder),span};
}
/** The Sorting screen's lead once everything has landed. */
export function sortingLead(found:HistoryImport):{bold:string;rest:string}{
 const threads=found.workstreams.reduce((n,ws)=>n+ws.threads.length,0);
 if(!threads&&found.already)return {bold:t('Nothing new.'),rest:t('{count} threads were already here.',{count:found.already})};
 const made=found.workstreams.filter(ws=>madeNew(ws.rule)).length;
 const bold=`${t('{count} threads',{count:threads})}, ${t('{count} workstreams',{count:found.workstreams.length})}.`;
 // A first import makes every workstream new, so there is no "everything else" to speak of.
 const rest=made===found.workstreams.length?t('All {count} were made because nothing existing fitted.',{count:made})
  :made?t('{count} were made because nothing existing fitted. Everything else went to a group by name or topic.',{count:made})
  :t('Every one went to a group by name or topic.');
 return {bold,rest};
}
/** The three numbers the collapse rests on, as the Reading screen's counters. */
export function collapseLine(counts:HistoryImport['counts']):string{
 return `${t('{count} conversations',{count:counts.conversations})} · ${t('{count} sessions',{count:counts.sessions})} · ${t('{count} things you’re actually working on',{count:counts.working})}`;
}
/** The line under the counters once the stream is done. */
export function setAsideLine(found:HistoryImport):string{
 if(!found.set_aside)return t('Kept every session');
 const already=found.already?t(', {already} already here',{already:found.already}):'';
 return `${t('Set aside {count} sessions with nothing to pick up',{count:found.set_aside})}${already}`;
}
/** The Answer screen's "From X, 19 Sep at 6:58 AM." */
export function askedWhen(at:number):string{
 const date=new Date(at*1000);
 return t('{date} at {time}',{date:formatDate(date,{day:'numeric',month:'short'}),time:formatDate(date,{hour:'numeric',minute:'2-digit'})});
}
/** A thread row's time on the Sorting screen: the clock today, the weekday this week, a date before that. */
export function threadWhen(at:number,now=Date.now()/1000):string{
 const date=new Date(at*1000),today=new Date(now*1000);
 const days=Math.round((new Date(today.toDateString()).getTime()-new Date(date.toDateString()).getTime())/86400000);
 if(days<=0)return formatDate(date,{hour:'numeric',minute:'2-digit'});
 if(days<7)return formatDate(date,{weekday:'short'});
 return formatDate(date,{day:'numeric',month:'short'});
}
/** The Home pane's line when every thread is archived (state 2): what happened, then what to do. */
export function archivedLine(count:number):{what:string;then:string}{
 return {what:count===1?t('Every thread here is archived, one of them.'):t('Every thread here is archived, {count} of them.',{count}),then:t('Restore one from the sidebar’s Archived list, or start a new thread.')};
}
