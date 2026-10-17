/* The pieces History, a run's record and a file's trace share: the agent's mark and byline, record text with its code
 * spans, and a link's sentence with the other run's name in the shared pale green highlight. */
import {Home as HomeIcon} from 'lucide-react';
import {providerMark} from './messageAvatars';
import {bylineParts,clock,codeParts,type HistoryLink,type Said,type Who} from './runHistory';

export type HistoryScope={projectId:string;profileId:string};
export type HistoryActions={onRecord:(taskId:string,runId?:string)=>void;onTrace:(path:string)=>void;onOpenThread:(channelId:string)=>void;onOpenTask:(channelId:string,taskId:string)=>void};

/** A run's agent as Dervo draws it: the provider's own mark in the pine square, or Home's house. */
export function WhoMark({provider,home=false}:{provider:string|null|undefined;home?:boolean}){
 if(home)return <span className="dervo-history-who is-home" aria-hidden="true"><HomeIcon size={11} strokeWidth={2}/></span>;
 return <img className="dervo-history-who" src={providerMark(provider)} alt="" aria-hidden="true"/>;
}

/** The byline: mark, agent, then model, account and access, each once. */
export function HistoryByline({who,home=false,extra}:{who:Who;home?:boolean;extra?:string}){
 const parts=home?[]:bylineParts(who);
 return <span className="dervo-history-byline">
  <WhoMark provider={who.provider} home={home}/>
  <span className="dervo-history-agent">{home?'Home':who.providerLabel}</span>
  {parts.length>0&&<><span className="dervo-history-sep" aria-hidden="true">·</span><span className="dervo-history-plain">{parts.join(' · ')}</span></>}
  {extra&&<><span className="dervo-history-sep" aria-hidden="true">·</span><span className="dervo-history-plain">{extra}</span></>}
 </span>;
}

/** An agent's words, quoted, with who wrote them when the record says. */
export function Quoted({said}:{said:Pick<Said,'text'|'from'>}){
 return <><q>{said.text}</q>{said.from&&<>{' '}<span className="dervo-history-from">{said.from}</span></>}</>;
}

/** Your answer to an agent's question: your words, or the option of its you picked, which stays marked as its. */
export function answerFrom(picked?:boolean){return picked?'You picked one of its options':'Your answer';}

/** Record text with its backtick spans shown as code. */
export function Coded({text}:{text:string}){
 return <>{codeParts(text).map((part,index)=>part.code?<code key={index}>{part.text}</code>:<span key={index}>{part.text}</span>)}</>;
}

/** A link's sentence with the other run's name in the shared highlight, and the time when the record has one. */
export function LinkText({link,onOpen}:{link:HistoryLink;onOpen?:(link:HistoryLink)=>void}){
 const name=link.name;
 const at=link.at?` at ${clock(link.at)}`:'';
 const mark=(text:string)=>onOpen&&link.taskId?<button type="button" className="dervo-history-mark" onClick={()=>onOpen(link)}>{text}</button>:<mark className="dervo-history-mark">{text}</mark>;
 if(!name||!link.text.includes(name))return <>{link.text}{at}.</>;
 const at0=link.text.indexOf(name);
 return <>{link.text.slice(0,at0)}{mark(name)}{link.text.slice(at0+name.length)}{at}.</>;
}
