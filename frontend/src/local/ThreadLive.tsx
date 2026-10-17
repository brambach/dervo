import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import {RunningOrb} from './RunningOrb';
import {StatusGlyph} from './StatusGlyph';
import {PICKUP_FRESH_MS,threadSummary,type SummaryFrom,type ThreadPickupData} from './ThreadPickup';
import {t} from '@/i18n';

/** The thread's live line in its head (design, 8 Oct; the mockup's header pill): the state's mark, its word and a time,
 * read from the same conversation pickup the strip under it reads, so it adds no host call. The strip then keeps only
 * its sentence, and the state is said once. No rust here: the sidebar row already carries the one accent. Clicking it
 * shows or hides the second column, like the panel button. */
/** The words before the time in "Finished {at}", in the reader's language, so the head can show the time alone. */
function finishedPrefix(){return t('Finished {at}',{at:'\u0000'}).split('\u0000')[0]??'';}

export function ThreadLive({projectId,channelId,task,who,question,open,preview=false,poll=false,onToggle}:{projectId:string;channelId:string;
 task?:SummaryFrom['task'];who?:string;question?:SummaryFrom['question'];open:boolean;preview?:boolean;poll?:boolean;onToggle?:()=>void}){
 const pickup=useQuery({queryKey:['conversation-pickup',projectId,channelId],enabled:!!projectId&&!!channelId,
  // No timer of its own while ThreadPickup polls this key: a second 15 s timer would drift and double the host calls
  // (the host answers one request at a time). ``poll`` is for when the strip isn't on screen.
  refetchInterval:poll?15000:false,staleTime:PICKUP_FRESH_MS,
  queryFn:()=>local<ThreadPickupData>('conversation_pickup',{projectId,channelId})});
 const summary=pickup.data?threadSummary(pickup.data,{task,who,question}):null;
 if(!summary)return null;
 // The decision bar under the report says Ready for review, with the one rust mark; the head says what happened.
 const word=pickup.data?.kind==='review'?t('Finished'):summary.word;
 const said=[word,summary.caption].filter(Boolean).join(', ');
 const body=<>
  {summary.mark==='orb'?<RunningOrb/>:<StatusGlyph state={pickup.data?.kind==='review'?'done':summary.mark}/>}
  <span className="dv-live-word" data-state={summary.state}>{word}</span>
  {summary.caption&&<span className="dv-live-time">{pickup.data?.kind==='review'?summary.caption.replace(finishedPrefix(),''):summary.caption}</span>}
 </>;
 return onToggle
  ?<button type="button" className="dv-live" data-testid="thread-live" aria-expanded={open} title={open?(preview?t('Hide Preview (⌘\\)'):t('Hide Progress (⌘\\)')):t('Show Progress (⌘\\)')}
    aria-label={t('{state}. Progress',{state:said})} onClick={onToggle}>{body}</button>
  // Plain text, not a live region: its time changes on every read, and a status role would read it out each time.
  :<span className="dv-live" data-testid="thread-live">{body}</span>;
}
