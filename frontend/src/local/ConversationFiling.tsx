import {Button} from '@/shared/ui/button';
import type {Conversation} from './api';
import type {IdeaListing} from './ProjectRoom';
import {t,tr} from '@/i18n';

/** After you mention another conversation, Dervo may offer to file this one beside it: "File this conversation under
 * Engineering?". Only when the mention points at exactly one idea, this conversation isn't in it already, it isn't
 * private, and you haven't said Not now to that idea here. Filing is the room's own path, ideas_assign. */

/** The conversations a message names with #, longest name first so "#api tests" beats "#api". The same matching the
 * pills in the message use, so what you see highlighted is what counts. */
export function mentionedConversations(text:string,conversations:Conversation[],selfId:string):Conversation[]{
 const lower=text.toLowerCase();
 const taken:[number,number][]=[];
 const found:Conversation[]=[];
 for(const conversation of [...conversations].sort((a,b)=>b.name.length-a.name.length)){
  if(conversation.id===selfId)continue;
  const needle='#'+conversation.name.toLowerCase();
  let from=0;
  for(;;){
   const at=lower.indexOf(needle,from);
   if(at<0)break;
   const end=at+needle.length,before=lower[at-1],after=lower[end];
   const bounded=(at===0||!/[\w#&/]/.test(before))&&(end===lower.length||/[\s,;.!?:)\]}]/.test(after));
   if(bounded&&!taken.some(([a,b])=>at<b&&end>a)){taken.push([at,end]);if(!found.includes(conversation))found.push(conversation);}
   from=end;
  }
 }
 return found;
}

export const dismissKey=(channelId:string,ideaId:string)=>`dervo-file-not-now:${channelId}:${ideaId}`;
function dismissed(channelId:string,ideaId:string):boolean{
 try{return localStorage.getItem(dismissKey(channelId,ideaId))==='1';}catch{return false;}
}

export type FilingSuggestion={ideaId:string;ideaName:string};

/** The one idea worth offering, or null. Every "don't" in the brief is a condition here. */
export function filingSuggestion({text,channelId,visibility,conversations,listing,isDismissed=dismissed}:{text:string;channelId:string;
 visibility:'shared'|'private';conversations:Conversation[];listing?:IdeaListing|null;isDismissed?:(channelId:string,ideaId:string)=>boolean}):FilingSuggestion|null{
 if(visibility!=='shared'||!listing)return null;
 const ideaOf=new Map<string,{id:string;name:string}>();
 for(const idea of listing.ideas)for(const entry of idea.conversations)ideaOf.set(entry.id,{id:idea.id,name:idea.name});
 const ideas=new Map<string,string>();
 for(const conversation of mentionedConversations(text,conversations,channelId)){
  const idea=ideaOf.get(conversation.id);
  if(idea)ideas.set(idea.id,idea.name);
 }
 if(ideas.size!==1)return null;
 const [[ideaId,ideaName]]=[...ideas];
 if(ideaOf.get(channelId)?.id===ideaId||isDismissed(channelId,ideaId))return null;
 return {ideaId,ideaName};
}

export function FilingPrompt({suggestion,busy,onFile,onNotNow}:{suggestion:FilingSuggestion;busy:boolean;onFile:()=>void;onNotNow:()=>void}){
 return <div className="dervo-filing" role="group" aria-label={t('File this conversation under {ideaName}',{ideaName:suggestion.ideaName})}>
  <p>{tr('File this conversation under {idea}?',{idea:<b>{suggestion.ideaName}</b>})}</p>
  <Button size="sm" variant="outline" disabled={busy} onClick={onFile}>{busy?t('Filing…'):t('File')}</Button>
  <Button size="sm" variant="ghost" disabled={busy} onClick={onNotNow}>{t('Not now')}</Button>
 </div>;
}
