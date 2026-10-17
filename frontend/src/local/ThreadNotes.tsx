import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {StickyNote} from 'lucide-react';
import {local} from './api';
import {SAFETY_MS,useThreadChanges} from './threadStates';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import './ThreadNotes.css';
import {formatList,t,tr} from '@/i18n';

/** Threads that work together (beta.17, #27; mocks in evidence/design-tests/b17-threads). Above the thread's composer:
 * a note another thread's agent left here, and the review of this thread's finished subthreads, while they wait for you.
 *
 * A note is that thread's words, quoted and captioned as theirs. This thread's agent doesn't see it until you pass it
 * on, which starts a run here with the note quoted, as from its sender, never as your message (backend/thread_notes.py).
 * The review is the same with Dervo as the sender. Bryce, 2 Oct: it starts on its own when the last subthread finishes,
 * so the card shows only when that's off, when it couldn't start, or when a subthread's outcome is unknown. Once one is
 * passed on, the run's opening row in the timeline carries it (NoteQuote), so the card goes.
 *
 * Bryce, 5 Oct (beta.17.1): notes pass on by themselves too, with their own switch on the note card. It's a second
 * switch, not one shared with reviews: a review only reads, but a note's run can change files with its sender's access,
 * so a project may want one without the other. A note card shows when that's off, while an automatic pass waits for a
 * busy thread (marked "Passed on automatically"), or when a note waits for you anyway (held: its sender asked for your
 * decision, a run that started without you left it, or the thread's outcome is unknown). */
export type ReviewChild={name:string;channelId:string;glyph:GlyphState;line:string;state:string};
export type NoteHold='asked'|'chain'|'hops'|'archived'|'unknown';
export type ThreadNote={id:string;kind:'note'|'review';fromName:string|null;fromChannelId:string|null;text:string|null;createdAt:number;
 state:'waiting'|'passed'|'dismissed'|'withdrawn';passedAt:number|null;trigger:'auto'|'click'|null;delivery:string|null;taskId:string|null;
 detail:string|null;canPass:boolean;canDismiss:boolean;held?:NoteHold|null;late?:boolean;uncertain?:boolean;children?:ReviewChild[]};
export type ThreadNotesView={notes:ThreadNote[];auto:boolean;autoPass?:boolean};
/** Why a note that could have gone by itself waits for you, in one sentence before the usual line. */
export function heldLine(note:ThreadNote):string|null{
 const from=note.fromName??t('another thread');
 return note.held==='asked'?t('{from} asked for your decision, so this didn’t pass on by itself.',{from})
  :note.held==='chain'?t('A run that started without you left this, so it waits for you instead of starting another.')
  :note.held==='hops'?t('Notes already went back and forth between these chats without you, so this one waits for you.')
  :note.held==='archived'?t('This thread is archived, so this waits for you.')
  :note.held==='unknown'?t('This thread’s last run stopped when Dervo closed, so this waits for you.')
  :null;
}

/** What shows above the composer: what waits for you, and one passed on that waits for this thread's run to end. */
export function waitingNotes(view:ThreadNotesView|undefined):ThreadNote[]{
 return (view?.notes??[]).filter(note=>note.state==='waiting'||(note.state==='passed'&&note.delivery==='queued'));
}
const clock=(at:number)=>new Date(at*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
const names=(kids:ReviewChild[])=>formatList(kids.map(k=>k.name));
/** The review card's title: how many finished, or which one finished again after the earlier review started. */
export function reviewTitle(note:ThreadNote):string{
 const kids=note.children??[];
 if(note.late)return t('{names} finished again',{names:names(kids)});
 return kids.length===1?t('Its subthread finished'):t('Its {length} subthreads finished',{length:kids.length});
}

export function ThreadNotes({projectId,channelId,agentName,onOpenThread,onChanged}:{projectId:string;channelId:string;agentName:string;
 onOpenThread:(channelId:string)=>void;onChanged:()=>Promise<unknown>|void}){
 const client=useQueryClient(),key=['thread-notes',projectId,channelId];
 // A note arriving, passed or dismissed moves this thread's row (thread_notes bumps it), and the state spine pushes
 // that, so it re-reads then. The slow timer is a safety net: polling every 4 s made this the app's busiest poller
 // while nothing happened (polish sweep, 8 Oct).
 const query=useQuery({queryKey:key,queryFn:()=>local<ThreadNotesView>('thread_notes',{projectId,channelId}),refetchInterval:SAFETY_MS,retry:false});
 useThreadChanges(projectId,ids=>{if(ids.includes(channelId))void client.invalidateQueries({queryKey:key});});
 const [busy,setBusy]=useState(''),[error,setError]=useState<{id:string;text:string}|null>(null);
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[projectId,channelId]);
 const shown=waitingNotes(query.data);
 if(!shown.length)return null;
 async function act(note:ThreadNote,operation:'thread_note_pass'|'thread_note_dismiss'){
  if(busy)return;
  setBusy(note.id);setError(null);
  try{
   await local(operation,{projectId,channelId,noteId:note.id});
   await Promise.all([client.invalidateQueries({queryKey:key}),client.invalidateQueries({queryKey:['local-conversations',projectId]}),onChanged()]);
  }catch(failure){if(alive.current)setError({id:note.id,text:failure instanceof Error?failure.message:String(failure)});}
  finally{if(alive.current)setBusy('');}
 }
 async function setAuto(operation:'subthread_review_set'|'thread_note_auto_set',next:boolean){
  setError(null);
  const id=operation==='subthread_review_set'?'auto':'autoPass';
  try{await local(operation,{projectId,auto:next});await client.invalidateQueries({queryKey:key});}
  catch(failure){setError({id,text:failure instanceof Error?failure.message:String(failure)});}
 }
 // The switch goes on the last note card only, so two notes waiting don't show it twice.
 const lastNote=[...shown].reverse().find(note=>note.kind==='note')?.id;
 return <div className="dv-thread-notes" data-testid="thread-notes">
  {shown.map(note=>note.kind==='review'
   ?<ReviewCard key={note.id} note={note} auto={!!query.data?.auto} busy={busy===note.id} error={error?.id===note.id||error?.id==='auto'?error.text:''}
     onPass={()=>void act(note,'thread_note_pass')} onDismiss={()=>void act(note,'thread_note_dismiss')} onAuto={next=>void setAuto('subthread_review_set',next)} onOpenThread={onOpenThread}/>
   :<NoteCard key={note.id} note={note} agentName={agentName} busy={busy===note.id} error={error?.id===note.id||(note.id===lastNote&&error?.id==='autoPass')?error.text:''}
     autoPass={note.id===lastNote?query.data?.autoPass!==false:undefined} onAutoPass={next=>void setAuto('thread_note_auto_set',next)}
     onPass={()=>void act(note,'thread_note_pass')} onDismiss={()=>void act(note,'thread_note_dismiss')} onOpenThread={onOpenThread}/>)}
 </div>;
}

function NoteCard({note,agentName,busy,error,autoPass,onAutoPass,onPass,onDismiss,onOpenThread}:{note:ThreadNote;agentName:string;busy:boolean;error:string;
 autoPass?:boolean;onAutoPass:(next:boolean)=>void;onPass:()=>void;onDismiss:()=>void;onOpenThread:(channelId:string)=>void}){
 const from=note.fromName??t('another thread');
 const queued=note.state==='passed';
 const automatic=queued&&note.trigger==='auto';
 const held=queued?null:heldLine(note);
 const why=t('{agent} hasn’t seen this. Passing it on starts a run here with the note quoted, as from {from}.',{agent:agentName,from});
 return <section className="dv-thread-note" aria-label={t('Note from {from}',{from})} data-testid="thread-note" data-state={note.state} data-trigger={note.trigger??undefined}>
  <div className="dv-thread-note-head"><StickyNote size={16} strokeWidth={1.5} aria-hidden="true"/><span className="dv-thread-note-from">{tr('Note from {from}',{from:<b>{from}</b>})}</span>
   {automatic&&<span className="dv-thread-note-mark">{t('Passed on automatically')}</span>}<time className="dv-when">{clock(note.createdAt)}</time></div>
  <blockquote>{note.text}</blockquote>
  {queued?<p className="dv-thread-note-why" role="status">{automatic?t('{agent} gets it when this thread’s current run ends, quoted as from {from}.',{agent:agentName,from})
    :note.detail??t('Passed on. It starts when this thread’s current run ends.')}</p>
   :<p className="dv-thread-note-why">{held?`${held} ${why}`:why}</p>}
  {(error||(!queued&&note.detail))&&<p className="dv-thread-note-error" role="alert">{error||note.detail}</p>}
  {!queued&&<div className="dv-row-actions">
   <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||!note.canPass} onClick={onPass}>{busy?t('Passing it on…'):t('Pass it on')}</button>
   <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={busy||!note.canDismiss} onClick={onDismiss}>{t('Dismiss')}</button>
   {note.fromChannelId&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={()=>onOpenThread(note.fromChannelId!)}>{t('Open {from}',{from})}</button>}
  </div>}
  {autoPass!==undefined&&<label className="dv-suggest-switch dv-caption"><input type="checkbox" checked={autoPass} onChange={event=>onAutoPass(event.currentTarget.checked)}/> {t('Pass notes on automatically')}</label>}
 </section>;
}

function ReviewCard({note,auto,busy,error,onPass,onDismiss,onAuto,onOpenThread}:{note:ThreadNote;auto:boolean;busy:boolean;error:string;
 onPass:()=>void;onDismiss:()=>void;onAuto:(next:boolean)=>void;onOpenThread:(channelId:string)=>void}){
 const kids=note.children??[];
 const queued=note.state==='passed';
 const line=queued?(note.detail??t('Its review starts when this thread’s current run ends.'))
  :note.uncertain?note.detail??t('One of them stopped when Dervo closed, so this review waits for you.')
  :note.late?t('They finished after the earlier review started, so they weren’t in it.')
  :t('Review them here, together, or open each one.');
 return <div className="dv-sheet dv-handoff dv-subthread-review" data-testid="subthread-review" data-state={note.state}>
  <div className="dv-row dv-open"><StatusGlyph state={queued?'queued':'needs'}/><div>
   <div className="dv-row-name">{reviewTitle(note)}</div>
   <div className="dv-row-line" role={queued?'status':undefined}>{line}</div>
   <dl className="dv-facts">{kids.map(kid=><div key={kid.channelId} className="dv-facts-row">
    <dt><StatusGlyph state={kid.glyph}/><button type="button" className="dv-subthread-name" onClick={()=>onOpenThread(kid.channelId)} title={t('Open {name}',{name:kid.name})}>{kid.name}</button></dt>
    <dd>{kid.line}</dd></div>)}</dl>
   {(error||(!queued&&!note.uncertain&&note.detail))&&<p className="dv-thread-note-error" role="alert">{error||note.detail}</p>}
   {!queued&&<>
    <div className="dv-row-actions">
     <button type="button" className="dv-btn dv-btn-primary" disabled={busy||!note.canPass} onClick={onPass}>{busy?t('Starting…'):t('Review them here')}</button>
     <button type="button" className="dv-btn dv-btn-quiet" disabled={busy||!note.canDismiss} onClick={onDismiss}>{t('Not now')}</button>
    </div>
    <p className="dv-subthread-review-caption">{t('Starts a read-only run in this thread. Each subthread’s request, result, changed files and checks go in quoted, not as your words. Nothing is approved or changed.')}</p>
    <label className="dv-suggest-switch dv-caption"><input type="checkbox" checked={auto} onChange={event=>onAuto(event.currentTarget.checked)}/> {t('Review finished subthreads automatically')}</label>
   </>}
  </div><div className="dv-row-end"><time className="dv-when">{clock(note.createdAt)}</time></div></div>
 </div>;
}

/** The opening of a run a note or review started, in the timeline: the sender's words quoted and captioned as theirs,
 * so it never reads as your message (MessageRow draws it from the row's dervo-note tag, threadNoteState.noteRow). */
export function NoteQuote({kind,from,quote,sent}:{kind:string;from:string;quote:string;sent:string}){
 if(kind==='review')return <figure className="dv-note-quote" data-kind="review">
  <figcaption><StickyNote size={14} strokeWidth={1.5} aria-hidden="true"/>{t('Review of {quote}',{quote})}</figcaption>
  <details><summary>{t('Show what was sent')}</summary><pre>{sent}</pre></details>
 </figure>;
 return <figure className="dv-note-quote" data-kind="note">
  <figcaption><StickyNote size={14} strokeWidth={1.5} aria-hidden="true"/>{t('Note from {from}',{from})}</figcaption>
  <blockquote>{quote}</blockquote>
 </figure>;
}
