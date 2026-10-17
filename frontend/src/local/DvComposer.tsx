import {useCallback,useEffect,useId,useLayoutEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {ArrowUp,Pause,Paperclip} from 'lucide-react';
import {toast} from 'sonner';
import {loadDraftEntry,persistDraftEntry} from '@/features/messages/lib/useDrafts';
import type {ChannelSuggestion} from '@/features/messages/lib/useChannelLinks';
import {shownInPlace} from '@/features/messages/ui/useMentionSendFlow.helpers';
import type {ComposerTextPaste,InsertComposerTextPaste} from '@/features/messages/ui/composerTextPaste';
import {useChannelNavigation} from '@/shared/context/ChannelNavigationContext';
import {t} from '@/i18n';
import {COMPOSER_TEXT_EVENT} from './composerText';

const SUGGESTIONS=6;

export type ComposerAgent={handle:string;name:string};
export type DvComposerProps={
 channelId?:string|null;
 draftKey?:string;
 placeholder?:string;
 disabled?:boolean;
 isSending?:boolean;
 /** Words, then mention pubkeys and media tags, which Dervo's thread never fills (agents and threads are read from the words). */
 onSend:(text:string,mentionPubkeys:string[],mediaTags?:string[][])=>Promise<void>;
 /** A slash command the screen handles itself (/bug, /feedback). True means it did, and nothing is sent. */
 onLocalCommand?:(text:string)=>boolean;
 /** While an agent works: with nothing typed, the round button is Pause and this runs. Typed words send as usual. */
 onStop?:()=>void;
 /** The model chip and the access tools, first in the foot. */
 toolbarLeadingActions?:ReactNode;
 /** The file tray (LocalImageComposer), drawn above the words. */
 attachmentContent?:ReactNode;
 onPickAttachment?:()=>void;
 /** A long paste the file tray may turn into a text file. True means it took it, and the words are left as they were. */
 onTextPaste?:(paste:ComposerTextPaste)=>boolean;
 /** Hands over a way to put words in the box: a paste the tray gave back, or Edit on a sent message (``replace``). */
 onPasteTextReady?:(insert:InsertComposerTextPaste|null)=>void;
 /** A draft handed over to send by itself: sent once on mount when it matches ``draftKey``. */
 autoSubmitDraftKey?:string|null;
 onAutoSubmitComplete?:()=>void;
 /** The line beside a suggested thread under #: what it's doing, or that it's private. */
 channelSuggestionNote?:(suggestion:ChannelSuggestion)=>string|null;
 /** This project's agents, offered under @. */
 agents?:readonly ComposerAgent[];
 /** Your last message here, which ↑ in an empty box brings back to change and send again. */
 recall?:string;
 formClassName?:string;
 containerClassName?:string;
};

type Suggestion={id:string;insert:string;label:string;note:string|null};
type Query={kind:'@'|'#';start:number;text:string};

/** The thread's message box, as Dervo draws it (the Claude app's shape, Home's box): plain words in a field that grows
 * with them, the file tray above, and a foot with the model, the paperclip and one round button that sends, or pauses
 * the agent while nothing is typed. `@` offers this project's agents and `#` its other threads; both go in as plain
 * text, which is how the host reads them (agents.ts MENTION, backend/chat_links.py).
 *
 * It replaces the inherited chat's rich-text MessageComposer on the thread screen (9 Oct). The send path is unchanged:
 * the screen's onSend gets the words, and the tray around it (LocalImageComposer) adds the files. */
export function DvComposer({channelId=null,draftKey,placeholder,disabled=false,isSending=false,onSend,onLocalCommand,onStop,toolbarLeadingActions,attachmentContent,
 onPickAttachment,onTextPaste,onPasteTextReady,autoSubmitDraftKey=null,onAutoSubmitComplete,channelSuggestionNote,agents,recall,formClassName,containerClassName}:DvComposerProps){
 const key=draftKey??channelId??'';
 const [text,setTextState]=useState(()=>key?loadDraftEntry(key)?.content??'':'');
 const textRef=useRef(text);
 const box=useRef<HTMLTextAreaElement>(null);
 // One send at a time: a second Enter, or a click while the first is on its way, does nothing (the submit lock).
 const locked=useRef(false);
 const [sending,setSending]=useState(false);
 // Bumped by every edit you make, so a failed send only puts its words back into a box you haven't touched since.
 const revision=useRef(0);
 const setText=useCallback((next:string)=>{textRef.current=next;setTextState(next);},[]);

 // ---------------------------------------------------------------- the draft, saved with every edit
 // At once, not after a pause: quitting, a version switch or the tray's remount read the store straight after a keystroke.
 const save=useCallback(()=>{
  if(key)try{persistDraftEntry(key,textRef.current,channelId??key,[],[]);}catch{/* storage is full or off: the box still holds the words */}
 },[key,channelId]);
 const edited=useCallback((next:string)=>{revision.current++;setText(next);save();},[save,setText]);
 useEffect(()=>{box.current?.dispatchEvent(new CustomEvent(COMPOSER_TEXT_EVENT,{bubbles:true,detail:text}));},[text]);

 // A restored draft opens with the caret after its last word, ready to carry on (and a paste lands there, not before it).
 useLayoutEffect(()=>{const field=box.current;if(field)field.setSelectionRange(field.value.length,field.value.length);},[]);

 // ---------------------------------------------------------------- the field grows with the words, to 40% of the window
 const sizes=useMemo(()=>typeof CSS!=='undefined'&&typeof CSS.supports==='function'&&CSS.supports('field-sizing','content'),[]);
 useLayoutEffect(()=>{
  const field=box.current;
  if(!field||sizes)return;
  field.style.height='auto';
  field.style.height=`${field.scrollHeight}px`;
 },[text,sizes]);

 // ---------------------------------------------------------------- @ and # suggestions
 const {channels}=useChannelNavigation();
 const [query,setQuery]=useState<Query|null>(null);
 const [active,setActive]=useState(0);
 const listId=useId();
 const suggestions=useMemo<Suggestion[]>(()=>{
  if(!query)return[];
  const needle=query.text.toLowerCase();
  const rank=(name:string)=>{const lower=name.toLowerCase();return lower.startsWith(needle)?0:lower.includes(needle)?1:-1;};
  const rows=query.kind==='@'
   ?(agents??[]).map(agent=>({id:`@${agent.handle}`,insert:`@${agent.handle}`,label:agent.name&&agent.name!==agent.handle?`@${agent.handle} · ${agent.name}`:`@${agent.handle}`,note:null,score:Math.max(rank(agent.handle),rank(agent.name))}))
   :channels.map(channel=>({id:channel.id,insert:`#${channel.name}`,label:`#${channel.name}`,note:channelSuggestionNote?.({id:channel.id,name:channel.name,channelType:'stream'})??null,score:rank(channel.name)}));
  return rows.filter(row=>row.score>=0).sort((a,b)=>a.score-b.score).slice(0,SUGGESTIONS).map(({score:_score,...row})=>row);
 },[query,agents,channels,channelSuggestionNote]);
 const open=suggestions.length>0;
 /** What's being typed right before the caret: "@na" or "#api te", started at a word's beginning. A # name may hold
  * spaces, so it reads back to the # unless a line break comes first. */
 function readQuery(value:string,caret:number):Query|null{
  const before=value.slice(0,caret);
  const at=/(?:^|\s)@([A-Za-z0-9-]{0,32})$/.exec(before);
  if(at)return {kind:'@',start:caret-at[1].length-1,text:at[1]};
  const hash=/(?:^|\s)#([^\n#]{0,60})$/.exec(before);
  // "# " with a space straight after is a heading being written, not a thread being named.
  if(hash&&!/^\s/.test(hash[1])&&!/\s{2}$/.test(hash[1]))return {kind:'#',start:caret-hash[1].length-1,text:hash[1]};
  return null;
 }
 function look(){const field=box.current;if(!field)return;const next=readQuery(field.value,field.selectionStart??field.value.length);setQuery(next);setActive(0);}
 function choose(row:Suggestion){
  const field=box.current;
  if(!field||!query)return;
  const caret=field.selectionStart??field.value.length;
  field.focus();
  field.setSelectionRange(query.start,caret);
  // insertText keeps the browser's own undo, so ⌘Z takes the name back out.
  insertText(field,`${row.insert} `);
  setQuery(null);
 }

 // ---------------------------------------------------------------- sending
 const busy=disabled||isSending||sending;
 const submit=useCallback(async()=>{
  const words=textRef.current,trimmed=words.trim();
  if(onLocalCommand?.(trimmed))return;
  if(!trimmed||locked.current||disabled||isSending)return;
  locked.current=true;setSending(true);setQuery(null);
  // The box empties as the message goes, as the Claude app's does. The draft stays saved until the send lands.
  const cleared=++revision.current;
  setText('');
  try{
   await onSend(trimmed,[],undefined);
   // The saved draft goes only if it's still these words: anything typed since, here or in this thread reopened, stays.
   if(key)try{if(loadDraftEntry(key)?.content===words)persistDraftEntry(key,'',channelId??key,[],[]);}catch{/* nothing left to keep */}
  }catch(error){
   // Your words come back. Into the empty box if you haven't typed since, or above what you've typed if you have.
   const now=textRef.current;
   const back=revision.current===cleared||!now.trim()?words:`${words}\n\n${now}`;
   setText(back);save();
   if(!shownInPlace(error))toast.error(error instanceof Error&&error.message?error.message:t('The message couldn’t be sent. Your words are back in the box.'));
  }finally{
   locked.current=false;setSending(false);
   // A click on Send leaves you in the box, ready for the next message, as Enter does.
   box.current?.focus();
  }
 },[onLocalCommand,disabled,isSending,onSend,key,channelId,setText,save]);
 const submitRef=useRef(submit);submitRef.current=submit;

 // A handed-over draft goes once, on mount, after it has loaded into the box (useState above reads it first).
 const autoComplete=useRef(onAutoSubmitComplete);autoComplete.current=onAutoSubmitComplete;
 useEffect(()=>{
  if(autoSubmitDraftKey===null||autoSubmitDraftKey!==key)return;
  autoComplete.current?.();
  const timer=window.setTimeout(()=>void submitRef.current(),0);
  return()=>window.clearTimeout(timer);
  // Mount only: a key that stays the same across renders doesn't send again.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 },[]);

 // ---------------------------------------------------------------- words put in from outside: a paste given back, or Edit
 useEffect(()=>{
  if(!onPasteTextReady)return;
  onPasteTextReady(paste=>{
   const field=box.current;
   if(!field||!field.isConnected)throw Error(t('The message box is no longer open.'));
   field.focus();
   const same=!paste.replace&&field.value===paste.document;
   if(paste.replace)field.setSelectionRange(0,field.value.length);
   else if(same)field.setSelectionRange(paste.from,paste.to);
   else field.setSelectionRange(field.value.length,field.value.length);
   insertText(field,paste.text);
   // Edit takes the whole box, so there's no earlier selection to keep: it worked.
   return paste.replace?true:same;
  });
  return()=>onPasteTextReady(null);
 },[onPasteTextReady]);

 const typed=!!text.trim();
 const pauseNow=useCallback(()=>{onStop?.();box.current?.focus();},[onStop]);
 const pause=!!onStop&&!typed&&!isSending&&!sending;
 return <div className={containerClassName}>
  <form className={`dv-dvc ${formClassName??''}`} data-testid="message-composer" data-disabled={disabled||undefined}
   onSubmit={event=>{event.preventDefault();void submit();}}>
   {attachmentContent}
   {open&&<ul className="dv-dvc-suggest" id={listId} role="listbox" aria-label={query?.kind==='@'?t('Agents in this project'):t('Other threads in this project')}>
    {suggestions.map((row,index)=><li key={row.id} id={`${listId}-${index}`} role="option" aria-selected={index===active} data-active={index===active||undefined}
     onMouseDown={event=>{event.preventDefault();choose(row);}} onMouseEnter={()=>setActive(index)}>
     <span className="dv-dvc-suggest-name">{row.label}</span>{row.note&&<span className="dv-dvc-suggest-note">{row.note}</span>}
    </li>)}
   </ul>}
   <div className="dv-dvc-scroll" data-testid="message-input-scroll">
    <textarea ref={box} className="dv-dvc-field" data-testid="message-input" rows={1} value={text} disabled={disabled} placeholder={placeholder}
     aria-label={t('Message')} aria-description={placeholder||undefined} aria-autocomplete="list" aria-expanded={open} aria-controls={open?listId:undefined}
     aria-activedescendant={open?`${listId}-${active}`:undefined}
     onChange={event=>{edited(event.target.value);look();}}
     onSelect={look} onBlur={()=>setQuery(null)}
     onPaste={event=>{
      // Files are the tray's (it catches them first); a long plain paste may become a text file there.
      if(event.clipboardData.files.length)return;
      const pasted=event.clipboardData.getData('text/plain');
      const field=event.currentTarget;
      if(!pasted)return;
      event.preventDefault();
      if(onTextPaste?.({text:pasted,from:field.selectionStart,to:field.selectionEnd,document:field.value}))return;
      // Plain words go in through the same editing command as a typed name, so one ⌘Z takes the whole paste back out.
      insertText(field,pasted);
     }}
     onKeyDown={event=>{
      // 229 is WebKit's composing key: the Enter that commits a Japanese or Chinese word must not send it.
      if(event.nativeEvent.isComposing||event.keyCode===229)return;
      if(open){
       if(event.key==='ArrowDown'){event.preventDefault();setActive(index=>(index+1)%suggestions.length);return;}
       if(event.key==='ArrowUp'){event.preventDefault();setActive(index=>(index-1+suggestions.length)%suggestions.length);return;}
       if((event.key==='Enter'||event.key==='Tab')&&!event.shiftKey){event.preventDefault();choose(suggestions[active]);return;}
       if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setQuery(null);return;}
      }
      if(event.key==='Enter'&&!event.shiftKey){
       if(typed){event.preventDefault();void submit();return;}
       // With nothing typed, Enter is the page's first, so a question tray above still gets it (as Home's box does). The
       // window hears it after the box; if nothing there took it, it doesn't leave a blank line behind either.
       const native=event.nativeEvent;
       const settle=(later:KeyboardEvent)=>{if(later===native&&!later.defaultPrevented)later.preventDefault();};
       window.addEventListener('keydown',settle);
       window.setTimeout(()=>window.removeEventListener('keydown',settle),0);
       return;
      }
      const plain=!event.altKey&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey;
      if(event.key==='ArrowUp'&&plain&&!text.trim()&&recall){
       event.preventDefault();
       // Through the editing command, so ⌘Z puts the empty box back.
       event.currentTarget.select();insertText(event.currentTarget,recall);
      }
     }}/>
   </div>
   <div className="dv-dvc-foot" data-testid="message-composer-toolbar">
    {toolbarLeadingActions}
    {onPickAttachment&&<button type="button" className="dv-dvc-clip" aria-label={t('Attach file')} title={t('Attach file')} disabled={disabled}
     onClick={onPickAttachment}><Paperclip aria-hidden="true"/></button>}
    {pause
     ?<button type="button" className="dv-dvc-send" data-testid="stop-run" aria-label={t('Pause')} title={t('Pause the running work (esc)')} disabled={disabled} onClick={pauseNow}>
      <Pause aria-hidden="true"/></button>
     :<button type="submit" className="dv-dvc-send" data-testid="send-message" aria-label={sending||isSending?t('Sending'):t('Send message')}
      disabled={!typed||busy} data-sending={sending||isSending||undefined}>
      {sending||isSending?<span className="dv-dvc-spin" aria-hidden="true"/>:<ArrowUp aria-hidden="true"/>}</button>}
   </div>
  </form>
 </div>;
}

/** Puts words where the selection is, through the browser's own editing so ⌘Z undoes it and React hears the change.
 * A webview that refuses the command gets the words set directly (no undo step then). */
function insertText(field:HTMLTextAreaElement,words:string){
 let done=false;
 // The command writes wherever focus is, so it's only used while the box has it (a disabled box can't take focus).
 if(document.activeElement===field)try{done=document.execCommand('insertText',false,words);}catch{done=false;}
 if(done)return;
 const start=field.selectionStart,end=field.selectionEnd;
 field.setRangeText(words,start,end,'end');
 field.dispatchEvent(new Event('input',{bubbles:true}));
}
