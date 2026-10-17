import {useMemo,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {DndContext,DragOverlay,PointerSensor,useDraggable,useDroppable,useSensor,useSensors,type DragEndEvent,type DragStartEvent} from '@dnd-kit/core';
import {Button} from '@/shared/ui/button';
import {ContextMenu,ContextMenuContent,ContextMenuItem,ContextMenuLabel,ContextMenuTrigger} from '@/shared/ui/context-menu';
import {local,type Conversation} from './api';
import {providerAuthor,modelName} from './providerLabel';
import {IdeaMenu,IdeaName,NewIdea,ArchivedIdeas} from './RoomIdeas';
import './ProjectRoom.css';
import {formatList,t,tr} from '@/i18n';

/** The project room: what every conversation in this project is doing, filed under the idea it belongs to.
 *
 * Nothing here is computed from conversation text. Every card is the host's own activity index (`conversations`),
 * which carries the live step of a running task and the one sentence a settled one is worth, and the ideas come from
 * `ideas_list`. A conversation with no idea sits in its own section rather than being filed by a guess.
 */

export type Idea={id:string;name:string;summary:string;state:'planned'|'building'|'done';target:string|null;
 archived_at:string|null;revision:number;created_at:string;updated_at:string;conversations:{id:string;name:string}[]};
export type IdeaListing={ideas:Idea[];archived:Idea[];unassigned:{id:string;name:string}[];states:Idea['state'][];
 suggestions?:Record<string,string>;overlaps?:Overlap[];limits:{name:number;summary:number};detail:string};
export type Overlap={idea_id:string;file:string;conversations:string[]};
export type Route={conversation:{id:string;name:string}|null;matched:string[]};
type Profile={id:string;provider:string;identity:string;category:string};

export const STATE_WORDS:Record<Idea['state'],string>={planned:t('Planned'),building:t('Building'),done:t('Done')};

export type Card={id:string;name:string;state:'running'|'needs'|'idle';says:string;agent:string;unread:number;helpers:string};

/** Who is working in this conversation: the provider, the model it's bound to and which of your accounts, from the
 * conversation's own binding. An account Dervo can't resolve says so rather than borrowing another row's name. */
export function agentOf(conversation:Conversation,profiles:Profile[]):string{
 const profile=profiles.find(entry=>entry.id===conversation.profile_id);
 const model=modelName(conversation.model);
 return [profile?providerAuthor(profile.provider):t('Account not found'),model,profile?.category].filter(Boolean).join(' · ');
}

/** One card, from the activity index only. A running conversation shows the step it's on; anything else shows the
 * sentence its newest task recorded. A conversation that has never run a task says nothing at all. */
export function cardOf(conversation:Conversation,profiles:Profile[]):Card{
 const activity=conversation.activity;
 const state:Card['state']=activity?.running?'running':activity?.status==='needs'?'needs':'idle';
 const says=(state==='running'?activity?.step??activity?.goal??t('Working'):activity?.summary??'')||'';
 return {id:conversation.id,name:conversation.name,state,says,agent:agentOf(conversation,profiles),unread:activity?.unread??0,
  helpers:helperLine(activity?.helpers)};
}

/** The helpers this conversation's newest task asked, in one line: "Asked fixer (working) and Independent review (answered)". */
export function helperLine(helpers?:{who:string;state:string}[]|null):string{
 if(!helpers?.length)return '';
 const parts=helpers.map(helper=>`${helper.who} (${helper.state})`);
 return t('Asked {helpers}',{helpers:formatList(parts)});
}

export type Section={key:string;name:string;idea:Idea|null;cards:Card[];where:string};

/** Where the work in a section is, counted rather than described. An empty section says it's empty. */
export function whereLine(cards:Card[]):string{
 const running=cards.filter(card=>card.state==='running').length;
 const needs=cards.filter(card=>card.state==='needs').length;
 const parts=[running&&t('{running} running',{running}),needs&&t('{needs} waiting for you',{needs})].filter(Boolean) as string[];
 if(parts.length)return parts.join(' · ');
 return cards.length?t('{count} conversations, nothing running',{count:cards.length}):t('No conversations yet');
}

/** Ideas in their saved order, then everything filed under no idea. A conversation an idea still lists but the
 * project no longer has (archived or deleted between the two reads) is dropped rather than drawn as an empty card. */
export function board(listing:IdeaListing|undefined,conversations:Conversation[],profiles:Profile[]):Section[]{
 const shared=conversations.filter(conversation=>conversation.visibility==='shared');
 const byId=new Map(shared.map(conversation=>[conversation.id,conversation]));
 const filed=new Set<string>();
 const sections:Section[]=(listing?.ideas??[]).map(idea=>{
  const cards=idea.conversations.map(entry=>byId.get(entry.id)).filter(Boolean)
   .map(conversation=>{filed.add(conversation!.id);return cardOf(conversation!,profiles);});
  return {key:idea.id,name:idea.name,idea,cards,where:whereLine(cards)};
 });
 const rest=shared.filter(conversation=>!filed.has(conversation.id)).map(conversation=>cardOf(conversation,profiles));
 if(rest.length)sections.push({key:'unfiled',name:t('Not in an idea'),idea:null,cards:rest,
  where:t('{count} conversations, waiting to be filed',{count:rest.length})});
 return sections;
}

/** Two running conversations of one idea editing the same file, in one sentence per file. Their results each hold only
 * the files that conversation changed, so the later approval can still overwrite the earlier one; that's the risk. */
export function overlapLines(overlaps:Overlap[]|undefined,ideaId:string):string[]{
 return (overlaps??[]).filter(entry=>entry.idea_id===ideaId).map(entry=>{
  const who=formatList(entry.conversations);
  return entry.conversations.length===2?t('{who} are both editing {file}.',{who,file:entry.file}):t('{who} are all editing {file}.',{who,file:entry.file});
 });
}

/** The counts in the room's header, across every shared conversation of the project. */
export function roomState(sections:Section[]):{running:number;needs:number;idle:number}{
 const cards=sections.flatMap(section=>section.cards);
 return {running:cards.filter(card=>card.state==='running').length,needs:cards.filter(card=>card.state==='needs').length,
  idle:cards.filter(card=>card.state==='idle').length};
}

type Props={projectId:string;profileId:string;projectName:string;conversations:Conversation[];profiles:Profile[];
 onOpen:(channelId:string)=>void;
 /** The orchestrator chat, shown beside the board. The room has no composer of its own: you talk to the project there. */
 chat?:React.ReactNode};

/** What a screen reader hears. Dragging is pointer-only here, so the instructions point at the keyboard route, the
 * card's context menu, instead of the library's default "press the space bar". */
const DRAG_HELP=t('Open this conversation with Enter. To move it to another idea, open its context menu and choose Move to, or drag it with a pointer.');
function announce(sections:Section[]){
 const name=(id:string|number)=>sections.flatMap(section=>section.cards).find(card=>card.id===id)?.name??t('Conversation');
 const place=(id?:string|number)=>id===undefined?'':id==='unfiled'?t('Not in an idea'):sections.find(section=>section.idea?.id===id)?.name??'';
 return {onDragStart:({active}:{active:{id:string|number}})=>t('Picked up {name}.',{name:name(active.id)}),
  onDragOver:({active,over}:{active:{id:string|number};over:{id:string|number}|null})=>over?t('{name} is over {place}.',{name:name(active.id),place:place(over.id)}):t('{name} is not over an idea.',{name:name(active.id)}),
  onDragEnd:({active,over}:{active:{id:string|number};over:{id:string|number}|null})=>over?t('{name} moved to {place}.',{name:name(active.id),place:place(over.id)}):t('{name} was put back.',{name:name(active.id)}),
  onDragCancel:({active}:{active:{id:string|number}})=>t('{name} was put back.',{name:name(active.id)})};
}

function DropSection({id,children}:{id:string;children:React.ReactNode}){
 const {setNodeRef,isOver,active}=useDroppable({id});
 return <div ref={setNodeRef} className="dervo-room-cards" data-drop={active?(isOver?'over':'ready'):undefined}>{children}</div>;
}

/** A card's content. In the room the name is the button that opens the conversation, stretched over the whole card,
 * so the card reads as one target while the suggestion stays its own control rather than a button inside a button.
 * The drag overlay draws the same content with no controls at all. */
function CardBody({card,suggested,onOpen,onFile}:{card:Card;suggested?:Idea;onOpen?:()=>void;onFile?:()=>void}){
 return <>
  <span className="dervo-room-top">
   <span className="dervo-room-dot" data-live={card.state==='running'||undefined} aria-hidden="true"/>
   {onOpen?<button type="button" className="dervo-room-name dervo-room-open" onClick={onOpen} aria-describedby="dervo-room-drag-help">{card.name}</button>
    :<span className="dervo-room-name">{card.name}</span>}
   {card.unread>0&&<span className="dervo-room-unread" aria-label={t('{unread} unread',{unread:card.unread})}>{card.unread}</span>}
  </span>
  {card.says&&<span className="dervo-room-says">{card.says}</span>}
  {card.helpers&&<span className="dervo-room-helpers">{card.helpers}</span>}
  {card.state==='needs'&&<span className="dervo-room-ask">{t('Review and approve')}</span>}
  {suggested&&onFile&&<button type="button" className="dervo-room-suggest" onClick={onFile}>{tr('File under {name} {suggested}',{name:suggested.name,suggested:<i>{t('suggested')}</i>})}</button>}
  <span className="dervo-room-who">{card.agent}</span>
 </>;
}

function RoomCard({card,ideas,here,suggested,onOpen,onMove}:{card:Card;ideas:Idea[];here:string|null;suggested?:Idea;
 onOpen:(id:string)=>void;onMove:(channelId:string,ideaId:string|null)=>void}){
 // Pointer listeners only: keyboard users move a card from its context menu, so the card adds no second tab stop.
 const {listeners,setNodeRef,isDragging}=useDraggable({id:card.id});
 return <ContextMenu>
  <ContextMenuTrigger asChild>
   <article ref={setNodeRef} className="dervo-room-card" data-state={card.state} data-dragging={isDragging||undefined} {...listeners}>
    <CardBody card={card} suggested={suggested} onOpen={()=>onOpen(card.id)} onFile={suggested?()=>onMove(card.id,suggested.id):undefined}/>
   </article>
  </ContextMenuTrigger>
  <ContextMenuContent className="dervo-room-menu">
   <ContextMenuItem onSelect={()=>onOpen(card.id)}>{t('Open conversation')}</ContextMenuItem>
   <ContextMenuLabel>{t('Move to')}</ContextMenuLabel>
   {ideas.filter(idea=>idea.id!==here).map(idea=><ContextMenuItem key={idea.id} onSelect={()=>onMove(card.id,idea.id)}>{idea.name}</ContextMenuItem>)}
   {here!==null&&<ContextMenuItem onSelect={()=>onMove(card.id,null)}>{t('No idea')}</ContextMenuItem>}
  </ContextMenuContent>
 </ContextMenu>;
}

export function ProjectRoom({projectId,profileId,projectName,conversations,profiles,onOpen,chat}:Props){
 const scope=useMemo(()=>({projectId,profileId}),[projectId,profileId]);
 const client=useQueryClient();
 const key=['project-ideas',projectId,profileId];
 const ideas=useQuery({queryKey:key,enabled:!!projectId&&!!profileId,queryFn:()=>local<IdeaListing>('ideas_list',scope)});
 const setListing=(listing:IdeaListing)=>client.setQueryData(key,listing);
 const [moved,setMoved]=useState<Record<string,string|null>>({});
 // A drop shows at once; the host's answer replaces it, and a refusal puts the card back where the host says it is.
 const listing=useMemo(()=>{
  if(!ideas.data||!Object.keys(moved).length)return ideas.data;
  const without=(idea:Idea)=>idea.conversations.filter(entry=>!(entry.id in moved));
  return {...ideas.data,ideas:ideas.data.ideas.map(idea=>({...idea,conversations:[...without(idea),
   ...Object.entries(moved).filter(([,target])=>target===idea.id).map(([id])=>({id,name:''}))]}))};
 },[ideas.data,moved]);
 const sections=useMemo(()=>board(listing,conversations,profiles),[listing,conversations,profiles]);
 const counts=roomState(sections);
 const [failure,setFailure]=useState('');
 const [editing,setEditing]=useState<{idea:string;field:'name'|'summary'|'target'}|null>(null);
 const [dragging,setDragging]=useState<Card|null>(null);
 const sensors=useSensors(useSensor(PointerSensor,{activationConstraint:{distance:6}}));

 async function move(channelId:string,ideaId:string|null){
  setMoved(previous=>({...previous,[channelId]:ideaId}));
  try{setListing(await local<IdeaListing>('ideas_assign',{...scope,channelId,ideaId}));}
  catch(error){setFailure(String(error));void ideas.refetch();}
  finally{setMoved(previous=>{const next={...previous};delete next[channelId];return next;});}
 }
 function dropped(event:DragEndEvent){
  setDragging(null);
  const channelId=String(event.active.id),over=event.over?.id;
  if(over===undefined)return;
  const destination=over==='unfiled'?null:String(over);
  const from=sections.find(section=>section.cards.some(card=>card.id===channelId))?.idea?.id??null;
  if(destination!==from)void move(channelId,destination);
 }
 const liveIdeas=listing?.ideas??[];
 const suggestionFor=(channelId:string)=>{const id=listing?.suggestions?.[channelId];return id?liveIdeas.find(entry=>entry.id===id):undefined;};
 const limits=listing?.limits??{name:60,summary:2000};
 const report=(message:string)=>setFailure(message);

 return <section className="dervo-room" aria-label={t('{projectName} orchestrator',{projectName})}>
  <header className="dervo-room-head">
   <div><p className="dervo-room-kicker">{t('Orchestrator')}</p><h1>{projectName}</h1></div>
   <p className="dervo-room-state">
    <span><b>{counts.running}</b> {t('running')}</span><span><b>{counts.needs}</b> {t('waiting for you')}</span><span><b>{counts.idle}</b> {t('idle')}</span>
   </p>
  </header>

  {failure&&<p className="dervo-room-failure" role="alert">{failure}<button type="button" className="dervo-room-switch" onClick={()=>setFailure('')}>{t('Dismiss')}</button></p>}

  <div className={chat?'dervo-room-split':'dervo-room-single'}>
  {chat}
  <p id="dervo-room-drag-help" className="sr-only">{DRAG_HELP}</p>
  <DndContext sensors={sensors} accessibility={{screenReaderInstructions:{draggable:DRAG_HELP},announcements:announce(sections)}} onDragStart={(event:DragStartEvent)=>setDragging(sections.flatMap(section=>section.cards).find(card=>card.id===event.active.id)??null)}
   onDragCancel={()=>setDragging(null)} onDragEnd={dropped}>
   <div className="dervo-room-board">
    {ideas.isError&&<p className="dervo-room-empty" role="alert">{t('This project’s ideas couldn’t load, so every conversation is listed together below.')} <Button variant="outline" size="sm" onClick={()=>void ideas.refetch()}>{t('Try again')}</Button></p>}
    {!sections.length&&<p className="dervo-room-empty">{ideas.isPending?t('Reading this project…'):t('No conversations yet. Tell the orchestrator what you want and it starts the first one.')}</p>}
    {sections.map(section=>{
     const own=section.idea;
     const edit=own&&editing?.idea===own.id?editing.field:null;
     const setEdit=(field:'name'|'summary'|'target'|null)=>setEditing(field&&own?{idea:own.id,field}:null);
     return <section key={section.key} className="dervo-room-idea" aria-label={section.name}>
      <div className="dervo-room-ihead">
       {own?<IdeaName idea={own} scope={scope} limits={limits} editing={edit} setEditing={setEdit} onListing={setListing} onError={report}/>:<h2>{section.name}</h2>}
       {edit!=='name'&&<span className="dervo-room-where">{section.where}</span>}
       {own&&edit!=='name'&&<span className="dervo-room-target">
        <span className={`dervo-chip dervo-room-chip is-${own.state}`}>{STATE_WORDS[own.state]}</span>
        {own.target&&<span>{t('for {target}',{target:own.target})}</span>}
       </span>}
       {own&&<IdeaMenu idea={own} scope={scope} limits={limits} onListing={setListing} onError={report} editing={edit} setEditing={setEdit}/>}
      </div>
      {own?.summary&&edit!=='summary'&&<p className="dervo-room-summary">{own.summary}</p>}
      <DropSection id={own?.id??'unfiled'}>
       {section.cards.map(card=><RoomCard key={card.id} card={card} ideas={liveIdeas} here={own?.id??null}
        suggested={own?undefined:suggestionFor(card.id)} onOpen={onOpen} onMove={(channelId,ideaId)=>void move(channelId,ideaId)}/>)}
       {!section.cards.length&&<p className="dervo-room-drop-hint">{t('Drag a conversation here, or ask the orchestrator for work that fits this idea.')}</p>}
      </DropSection>
      {own&&overlapLines(listing?.overlaps,own.id).length>0&&<div className="dervo-room-overlap" role="note">
       {overlapLines(listing?.overlaps,own.id).map(line=><p key={line}>{line}</p>)}
       <p>{t('Each result holds only the files its conversation changed, so approving one after the other can overwrite the first.')}</p>
      </div>}
     </section>;
    })}
    {liveIdeas.length>0&&!sections.some(section=>!section.idea)&&dragging&&<section className="dervo-room-idea" aria-label={t('Not in an idea')}>
     <div className="dervo-room-ihead"><h2>{t('Not in an idea')}</h2></div><DropSection id="unfiled"><p className="dervo-room-drop-hint">{t('Drop here to take it out of its idea.')}</p></DropSection>
    </section>}
    <div className="dervo-room-foot">
     <NewIdea scope={scope} limits={limits} onListing={setListing} onError={report}/>
     <ArchivedIdeas archived={listing?.archived??[]} scope={scope} onListing={setListing} onError={report}/>
    </div>
   </div>
   <DragOverlay dropAnimation={{duration:160,easing:'cubic-bezier(.2,.8,.2,1)'}}>
    {dragging?<div className="dervo-room-card is-lifted" data-state={dragging.state}><CardBody card={dragging}/></div>:null}
   </DragOverlay>
  </DndContext>
  </div>
 </section>;
}
