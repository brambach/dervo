import {Fragment,type ReactElement} from 'react';
import type {Conversation} from './api';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {gather,subthreads} from './subthreadState';
import './homeFamilies.css';
import {t} from '@/i18n';

/** Home's Catch up pane with subthreads under their parent (beta.17, #27; mocks catchup-running and catchup-review in
 * evidence/design-tests/b17-threads). Each section of the pane gathers a parent's subthreads under the parent's own row
 * there, or under a row for the parent where the first of them stood; the open card keeps its own place. Home.tsx
 * calls ``paneSheet`` in place of mapping its rows (evidence/b17-threads/home-pane-families.patch). */
export type PaneKid={id:string;name:string;glyph:GlyphState;line:string|null;when:string};
export type PaneLike={id:string;name:string;kind:string};

export function paneSheet<T extends PaneLike>(items:readonly T[],conversations:readonly Conversation[],{open,row,kid,onOpen,where}:{
 open:string|null;row:(item:T,open:boolean)=>ReactElement;kid:(item:T)=>PaneKid;onOpen:(id:string)=>void;where:'needs'|'running'|'since'}):ReactElement[]{
 const keyed=items.map(item=>({channelId:item.id,item}));
 const families=subthreads(conversations);
 return gather(keyed,conversations,entry=>entry.item.id===open).map(entry=>{
  if(entry.kind==='item'){
   const own=row(entry.item.item,entry.item.item.id===open);
   if(!entry.kids.length)return own;
   return <Fragment key={entry.item.item.id}>{own}<PaneKids kids={entry.kids.map(k=>kid(k.item))} onOpen={onOpen}/></Fragment>;
  }
  const parent=conversations.find(c=>c.id===entry.parentId),all=families.get(entry.parentId)??[];
  const runningNow=all.filter(c=>c.activity?.running).length;
  const sub=where==='running'?t('{runningNow} of {length} running',{runningNow,length:all.length}):where==='needs'?t('{count} need you',{count:entry.kids.length}):t('{length} finished',{length:entry.kids.length});
  return <article key={`family:${entry.parentId}`} className="dv-row dv-home-row dv-now-family" data-kind={where} data-row={entry.parentId}>
   <StatusGlyph state={parent?.activity?.running?'running':'idle'}/>
   <div>
    <button type="button" className="dv-row-name dv-home-row-open" onClick={()=>onOpen(entry.parentId)}>{parent?.name??t('Its parent thread')}<span className="dv-now-sub">{sub}</span></button>
    <PaneKids kids={entry.kids.map(k=>kid(k.item))} onOpen={onOpen} inside/>
    {where==='running'&&runningNow>0&&<p className="dv-row-meta"><span>{t('{parent} gets a review of them once they’ve all finished',{parent:parent?.name??t('Its parent')})}</span></p>}
   </div>
  </article>;
 });
}

function PaneKids({kids,onOpen,inside=false}:{kids:PaneKid[];onOpen:(id:string)=>void;inside?:boolean}){
 return <div className="dv-now-kids" role="list" data-inside={inside||undefined}>{kids.map(kid=>
  <button key={kid.id} type="button" role="listitem" className="dv-now-kid" onClick={()=>onOpen(kid.id)}>
   <StatusGlyph state={kid.glyph}/>
   <span className="dv-now-kid-copy"><span className="dv-now-kid-name">{kid.name}{kid.when&&<span className="dv-when">{kid.when}</span>}</span>{kid.line&&<span className="dv-now-kid-line">{kid.line}</span>}</span>
  </button>)}</div>;
}
