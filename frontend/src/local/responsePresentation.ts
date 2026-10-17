import type {TimelineMessage} from '@/features/messages/types';
import type {LocalEvent} from './api';
import type {CodingTask} from './CodingTasks';
import type {ConversationStatus} from './TurnControls';
import {providerAuthor} from './providerLabel';
import {messageAvatar} from './messageAvatars';
import {nativeStep} from './nativeSteps';
import {progressNow} from './progressBrief';
import {replyRun,type ReplyRun} from './replyTrail';
import {t} from '@/i18n';

const LIVE=new Set(['executing','running','queued','delivering']);
const clip=(text:string)=>text.length>80?`${text.slice(0,79)}…`:text;
const basename=(path:string)=>path.split(/[\\/]/).filter(Boolean).at(-1)??path;

/** The words under a live reply: the step running now, in Progress's words, or Thinking between steps. */
export function liveStepLabel(task:CodingTask,runId:string):string {
  const live=[...(task.tokens??[])].reverse().find(token=>token.run_id===runId&&LIVE.has(token.state));
  if(!live)return t('Thinking');
  const native=nativeStep(live);
  if(native){
    if(live.tool==='native:Bash')return clip(native.subject||t('Running a command'));
    return clip(native.subject?`${native.present} ${native.file?basename(native.subject):native.subject}`:native.present);
  }
  const now=progressNow(task,Date.now()/1000);
  return now?clip(now.file?`${now.text} ${now.file}`:now.text):t('Thinking');
}

/** An agent's em dash reads as a spaced hyphen, the house style. The same rule as project_orchestrator.py applies to
 * Home's replies; here it's display only, so the saved text is untouched. */
export function plainDashes(text: string): string {
  return text.replace(/\s*\u2014\s*/g, ' - ');
}

/** A retry on the other provider starts a new task with the same request. The request already sits in the thread under
 * the task that stopped, so the copy under the task that took over is left out; the handover line says what happened. */
export function repeatedRequestRows(tasks: CodingTask[]): Set<string> {
  const hidden = new Set<string>();
  for (const task of tasks) {
    const earlier = task.route?.retry_of ? tasks.find(other => other.id === task.route!.retry_of) : undefined;
    const request = task.messages[0], original = earlier?.messages.find(message => message.role === 'user');
    if (earlier && request?.role === 'user' && original && request.text === original.text) hidden.add(`${task.id}:0`);
  }
  return hidden;
}

/** After a handoff the thread holds two providers' replies. Each task's replies carry the name and mark of the provider
 * that wrote them, from the task's own record, with their own author key so the byline changes where the provider did. */
export function handedOverRows(tasks: CodingTask[], provider?: string): Map<string, {author: string; pubkey: string; avatarUrl: string | null}> {
  const rows = new Map<string, {author: string; pubkey: string; avatarUrl: string | null}>();
  for (const task of tasks) {
    const own = task.binding?.provider;
    if (!own || own === provider) continue;
    task.messages.forEach((message, i) => {
      if (message.role === 'assistant' && message.source !== 'independent_review') rows.set(`${task.id}:${i}`, {author: providerAuthor(own), pubkey: `provider:${own}`, avatarUrl: messageAvatar('provider', {provider: own})});
    });
  }
  return rows;
}

/** Presentation only. Never writes invented messages or phases into the task/history store. */
export function presentResponses(messages: TimelineMessage[], history: LocalEvent[], tasks: CodingTask[], status?: ConversationStatus, provider?: string): TimelineMessage[] {
  const repeated = repeatedRequestRows(tasks), handed = handedOverRows(tasks, provider);
  const shown = messages.filter(row => !repeated.has(row.id)).map(row => {
    const own = handed.get(row.id);
    const named = own ? {...row, ...own} : row;
    return named.pubkey?.startsWith('provider') && named.body.includes('\u2014') ? {...named, body: plainDashes(named.body)} : named;
  });
  const result = [...shown];
  // Keep the real row's identity through completion, stop and observation loss.
  for (const task of tasks) {
    const seen = new Set<string>();
    task.messages.forEach((message, i) => {
      if (message.role !== 'assistant' || !message.run_id || seen.has(message.run_id) || message.source === 'independent_review') return;
      seen.add(message.run_id);
      const index = result.findIndex(row => row.id === `${task.id}:${i}`);
      if (index >= 0) result[index] = {...result[index], renderKey: `response:${task.id}:${message.run_id}`};
    });
    // Each run's replies carry its model and clock, and its trail or end chip (16.2 live reply).
    const runs = new Map<string, {run?: ReplyRun; rows: number[]}>();
    task.messages.forEach((message, i) => {
      if (message.role !== 'assistant' || !message.run_id || message.source === 'independent_review') return;
      if (!runs.has(message.run_id)) runs.set(message.run_id, {run: replyRun(task, message.run_id), rows: []});
      const index = result.findIndex(row => row.id === `${task.id}:${i}`);
      if (index >= 0) runs.get(message.run_id)!.rows.push(index);
    });
    for (const {run, rows} of runs.values()) {
      if (run) rows.forEach((index, n) => { result[index] = {...result[index], replyRun: n === rows.length - 1 ? {...run, tail: true, key: `${run.key}:tail`} : run}; });
    }
  }
  for (const request of history.filter(row => row.pubkey === 'local-user')) {
    const reply = history.find(row => row.pubkey === 'provider' && row.id.startsWith(`${request.id}:`));
    const index = reply ? result.findIndex(row => row.id === reply.id) : -1;
    if (index >= 0) result[index] = {...result[index], renderKey: `response:${request.id}`};
  }
  if (!status?.active || status.outcome_uncertain || status.review_active || status.interrupted || !['working', 'checking'].includes(status.state)) return result;
  const attach = (id: string, identity: string, label: string, requestId: string, author: string, first: boolean, avatarProvider?: string, run?: ReplyRun) => {
    const index = result.findIndex(message => message.id === id);
    if (index >= 0) result[index] = {...result[index], renderKey: first ? `response:${identity}` : result[index].renderKey, responsePresentation: {identity, label}, ...(run ? {replyRun: run} : {})};
    else {
      const request = result.find(message => message.id === requestId);
      if (request) {
        const group = tasks.find(task => id.startsWith(`${task.id}:`));
        const position = group ? result.reduce((last, row, i) => row.id.startsWith(`${group.id}:`) ? i : last, -1) : result.indexOf(request);
        // The waiting row is the reply's own row from the start: same author and mark, so nothing swaps when text arrives.
        result.splice(position + 1, 0, {...request, id, renderKey: `response:${identity}`, body: '', author, pubkey: 'provider', avatarUrl: messageAvatar('provider', {provider: avatarProvider}), responsePresentation: {identity, label}, replyRun: run, parentId: null, rootId: null});
      }
    }
  };
  for (const task of tasks) {
    const run = task.runs.at(-1) as {id?: string; state?: string} | undefined;
    if (task.state !== 'working' || !run?.id || !['checking', 'sending', 'running'].includes(run.state ?? '')) continue;
    const indices = task.messages.flatMap((message, index) => message.role === 'assistant' && message.run_id === run.id && message.source !== 'independent_review' ? [index] : []);
    const index = indices.at(-1) ?? task.messages.length;
    const requestIndex = task.messages.reduce((last, message, i) => message.role === 'user' ? i : last, 0);
    const trail = replyRun(task, run.id);
    attach(`${task.id}:${index}`, `${task.id}:${run.id}`, run.state === 'running' ? liveStepLabel(task, run.id) : t('Getting started'), `${task.id}:${requestIndex}`, providerAuthor(task.binding.provider), indices.length <= 1, provider ?? task.binding.provider, trail);
  }
  for (const request of history) {
    if (request.pubkey !== 'local-user' || !['sending', 'running'].includes(request.state)) continue;
    const replies = history.filter(row => row.pubkey === 'provider' && row.id.startsWith(`${request.id}:`));
    const reply = replies.filter(row => row.state === 'streaming').at(-1);
    if (!reply && replies.length) continue;
    attach(reply?.id ?? `${request.id}:waiting`, request.id, request.state === 'running' ? t('Thinking') : t('Getting started'), request.id, providerAuthor(provider), replies.length <= 1, provider);
  }
  return result;
}

/** What the reply row says while your message is still sending (16.2 item 1). Null when no new reply is starting: a
 * run is already working here (the message joins it or waits in the queue), so nothing new is drawn. */
export type StartingReply = {author: string; avatarUrl: string | null; label: string; model?: string};

/** The reply row shows the moment you press send, not when the host has answered (16.2 item 1). While a message you
 * sent is still with the host, the row under it is drawn at once, reading only that it's starting. Once the real reply
 * row follows your saved message, it takes over the same render key, so the row never remounts, enters twice or
 * flashes. ``handed`` keeps those takeovers (and the placeholders drawn) for as long as the thread is open. A refused
 * send has no pending row, so the placeholder goes with it. Presentation only: nothing is written to any store. */
export function instantReplies<T extends TimelineMessage>(messages: T[], pending: {id: string; created_at: number}[], handed: Map<string, string>, starting: StartingReply | null): T[] {
  const waiting = new Map(pending.map(row => [`sent:${row.id}`, row]));
  const out: T[] = [];
  messages.forEach((message, index) => {
    const own = handed.get(message.renderKey ?? message.id);
    out.push(own ? {...message, renderKey: own} : message);
    const sent = message.pubkey === 'local-user' ? message.renderKey : undefined;
    if (!sent?.startsWith('sent:')) return;
    const key = `reply:${sent}`, next = messages[index + 1];
    const reply = next?.pubkey?.startsWith('provider') ? next : undefined;
    // The real reply arrived under a message whose placeholder was drawn: it keeps that key from here on. Only a
    // placeholder that was actually drawn hands over, so a reply that was already on screen never changes key.
    if (reply?.responsePresentation && handed.has(`placed:${key}`) && !message.id.startsWith('sending:')) handed.set(reply.renderKey ?? reply.id, key);
    const row = waiting.get(sent);
    if (reply || !row || !starting) return;
    if (handed.size < 1000) handed.set(`placed:${key}`, key);
    const run: ReplyRun = {key: `starting:${starting.label}:${starting.model ?? ''}`, model: starting.model, startedAt: row.created_at, live: true, done: [], now: {id: key, text: starting.label}, steps: 0, helpers: 0, latest: true};
    out.push({...message, id: `${sent}:reply`, renderKey: key, body: '', author: starting.author, pubkey: 'provider', avatarUrl: starting.avatarUrl,
      responsePresentation: {identity: key, label: starting.label}, replyRun: run, parentId: null, rootId: null, reactions: [], edited: false, pending: false});
  });
  return out;
}
