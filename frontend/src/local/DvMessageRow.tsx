import {lazy,memo,Suspense,useCallback,useMemo,useState,type AnimationEvent} from 'react';
import type {TimelineMessage} from '@/features/messages/types';
import {Markdown} from '@/shared/ui/markdown';
import {ReceivedResponse} from '@/shared/ui/ReceivedResponse';
import {parseImetaTags} from '@/shared/ui/markdown/parseImeta';
import {useChannelNavigation} from '@/shared/context/ChannelNavigationContext';
import {tagsEqual} from '@/features/messages/lib/messageRowEquality';
import {KIND_STREAM_MESSAGE_DIFF} from '@/shared/constants/kinds';
import {formatFullDateTime,formatTime} from '@/features/messages/lib/dateFormatters';
import {AnsweredQuestion} from './AnsweredQuestion';
import {QUESTION_TAG} from './workerQuestionState';
import {NoteQuote} from './ThreadNotes';
import {NOTE_TAG} from './threadNoteState';
import {ReplyRunMeta} from './ReplyTrailView';
import {t} from '@/i18n';

const DiffMessage=lazy(()=>import('@/features/messages/ui/DiffMessage'));
const DiffMessageExpanded=lazy(()=>import('@/features/messages/ui/DiffMessageExpanded'));

/** One message in a thread, as Dervo draws it (the Claude app's shape): yours as a bubble on the right, the agent's as a
 * small mark, its name and model, then the answer in reading type across the measure. Nothing on it is clickable but
 * its links: no avatar card, no presence, no reactions. Copy and Edit hang under it (MessageActions), and the timeline
 * passes them in as the row's footer.
 *
 * It replaces the inherited chat's MessageRow on Dervo's thread screen (9 Oct). The data-testids and data-pubkey stay
 * as they were, because the send motion, the reply entrance and the thread styles read them. */
export const DvMessageRow=memo(function DvMessageRow({message,continuation=false,highlighted=false,searchQuery,playEntrance=false,onEntranceComplete}:{
 message:TimelineMessage;
 /** The same author straight after their last message: the byline isn't repeated. */
 continuation?:boolean;
 highlighted?:boolean;
 searchQuery?:string;
 playEntrance?:boolean;
 onEntranceComplete?:(messageId:string)=>void;
}){
 const own=message.pubkey==='local-user';
 const {nonDmChannelNames:channelNames}=useChannelNavigation();
 const imetaByUrl=useMemo(()=>message.tags?parseImetaTags(message.tags):undefined,[message.tags]);
 const [diffOpen,setDiffOpen]=useState(false);
 const tag=(name:string)=>message.tags?.find(row=>row[0]===name)?.[1];
 const entered=useCallback((event:AnimationEvent<HTMLElement>)=>{
  if(playEntrance&&event.animationName==='motion-enter-conversation')onEntranceComplete?.(message.id);
 },[message.id,onEntranceComplete,playEntrance]);
 // Your answer to an agent's question carries the question, drawn above your words as the asker's.
 const answered=message.tags?.find(row=>row[0]===QUESTION_TAG&&row.length===3);
 // A run a note or review started opens with the sender's words, quoted as theirs (beta.17).
 const note=message.tags?.find(row=>row[0]===NOTE_TAG&&row.length===5);
 const showHead=!own&&!continuation;
 const authorId=`dv-msg-author-${message.id}`;
 return <article className={`dv-msg${playEntrance?' motion-enter-conversation':''}`} data-testid="message-row" data-message-id={message.id}
  data-pubkey={message.pubkey||undefined} data-own={own||undefined} data-continuation={continuation&&!own||undefined}
  data-highlighted={highlighted||undefined} onAnimationEnd={entered}
  aria-labelledby={showHead?authorId:undefined} aria-label={showHead?undefined:own?t('You'):message.author}>
  {showHead&&<header className="dv-msg-head" data-testid="message-header">
   <Mark url={message.avatarUrl??null} name={message.author}/>
   <span className="dv-msg-author" id={authorId}>{message.author}</span>
   {/* A coding run's reply shows its model where the time would be (16.2); the time stays on hover. */}
   {message.replyRun?.model?<ReplyRunMeta run={message.replyRun}/>
    :<time className="dv-msg-time" data-testid="message-timestamp" dateTime={new Date(message.createdAt*1000).toISOString()} title={formatFullDateTime(message.createdAt)}>{formatTime(message.createdAt)}</time>}
   {message.personaDisplayName&&message.personaDisplayName!==message.author&&<span className="dv-msg-persona">{message.personaDisplayName}</span>}
  </header>}
  <div className="dv-msg-body" data-testid="message-body">
   {message.kind===KIND_STREAM_MESSAGE_DIFF
    ?<Suspense fallback={<p className="dv-caption">{t('Loading the change…')}</p>}>
     <DiffMessage commitSha={tag('commit')} content={message.body} description={tag('description')} filePath={tag('file')}
      onExpand={()=>setDiffOpen(true)} repoUrl={tag('repo')} searchQuery={searchQuery} truncated={tag('truncated')==='true'}/>
    </Suspense>
    :<>
     {answered&&<AnsweredQuestion asker={answered[1]} ask={answered[2]}/>}
     {note&&<NoteQuote kind={note[1]} from={note[2]} quote={note[3]} sent={note[4]}/>}
     <ReceivedResponse text={message.body} presentation={message.responsePresentation} run={message.replyRun}>
      {(visible,transient,streamingCompatible)=><Markdown className="max-w-full" content={visible} transient={transient} streamingCompatible={streamingCompatible}
       interactive messageId={message.id} channelNames={channelNames} imetaByUrl={imetaByUrl} searchQuery={searchQuery}/>}
     </ReceivedResponse>
    </>}
   {(message.pending||message.edited)&&<p className="dv-msg-status" data-testid={message.pending?'message-send-status':undefined}>
    {message.pending?t('Sending…'):t('Edited')}</p>}
   {diffOpen&&<Suspense fallback={<p className="dv-caption">{t('Loading the change…')}</p>}>
    <DiffMessageExpanded content={message.body} filePath={tag('file')} onClose={()=>setDiffOpen(false)}/>
   </Suspense>}
  </div>
 </article>;
},(prev,next)=>prev.message.id===next.message.id&&prev.message.pubkey===next.message.pubkey&&prev.message.body===next.message.body
 &&prev.message.author===next.message.author&&prev.message.avatarUrl===next.message.avatarUrl&&prev.message.createdAt===next.message.createdAt
 &&prev.message.kind===next.message.kind&&prev.message.pending===next.message.pending&&prev.message.edited===next.message.edited
 &&prev.message.personaDisplayName===next.message.personaDisplayName
 &&prev.message.responsePresentation?.identity===next.message.responsePresentation?.identity
 &&prev.message.responsePresentation?.label===next.message.responsePresentation?.label
 &&prev.message.replyRun?.key===next.message.replyRun?.key&&tagsEqual(prev.message.tags,next.message.tags)
 &&prev.continuation===next.continuation&&prev.highlighted===next.highlighted&&prev.searchQuery===next.searchQuery
 &&prev.playEntrance===next.playEntrance&&prev.onEntranceComplete===next.onEntranceComplete);

/** The author's mark: the agent's own picture (Claude Code's, Codex's, a helper's), or its first letter. Decorative,
 * since the name sits right beside it. */
function Mark({url,name}:{url:string|null;name:string}){
 const [broken,setBroken]=useState(false);
 return url&&!broken
  ?<img className="dv-msg-mark" data-testid="message-avatar" src={url} alt="" draggable={false} onError={()=>setBroken(true)}/>
  :<span className="dv-msg-mark" data-testid="message-avatar" aria-hidden="true">{name.trim().charAt(0).toUpperCase()}</span>;
}
