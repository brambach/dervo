import {useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import {t} from '@/i18n';
import './KnowledgeMove.css';

/** Your rules on the Knowledge page (design sweep, knowledge-a.html and knowledge-a-rule.html): a number, the rule, the
 * date it was saved. One row at a time opens onto a white card with the full text, its versions as rows, where it came
 * from and what you can do with it. Changing which version is used, and archiving, confirm in place on that card; the
 * page owns those confirmations and passes them in as ``decision``. */
export type KnowledgeSource={channelId:string;messageId?:string;taskId?:string;runId?:string};
export type Version={id:string;text:string;provenance:string;source:KnowledgeSource|null;created_at:string};
export type RuleEvent={kind?:string;at?:string;version_id?:string|null;archived_version_id?:string};
export type Rule={id:string;revision:number;active_version_id:string|null;versions:Version[];events:unknown[];procedure_id?:string};
export type NotebookSelection={ruleId:string;versionId:string;history:boolean;reading:boolean};
/** What the next task does with a rule in use, from ``knowledge_list.deliveries`` (beta.11, fix-memory F3). */
export type Delivery={sent:boolean;reason:string|null};
const NOT_SENT:Record<string,string>={source_private:'the chat it came from is private now',source_changed:'the chat it came from changed account or model since',
 source_unavailable:'its source no longer checks out'};
/** The one line about a rule's delivery, or nothing when it simply goes out: "Not sent to tasks: the chat it came from is
 * private now." or "The chat it came from was deleted. Tasks still get it." */
export function deliveryLine(delivery:Delivery|undefined):string{
 if(!delivery)return '';
 if(delivery.sent)return delivery.reason==='source_deleted'?'The chat it came from was deleted. Tasks still get it.':'';
 return `Not sent to tasks: ${NOT_SENT[delivery.reason??'']??NOT_SENT.source_unavailable}.`;
}
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
/** A saved date as the design system writes it: "12 Sep", or "12 Sep 2026" with the year. */
export function ruleDate(value:string,{year=false}:{year?:boolean}={}){const saved=new Date(value);if(Number.isNaN(saved.getTime()))return value;return `${saved.getDate()} ${MONTHS[saved.getMonth()]}${year?` ${saved.getFullYear()}`:''}`;}
export function ruleTitle(text:string){const first=text.trim().split('\n').find(Boolean)??'Untitled rule';return first.length>120?first.slice(0,117)+'…':first;}
/** The version a row shows: the one in use, else the newest saved one. */
export function shownVersion(rule:Rule):Version|undefined{return rule.versions.find(version=>version.id===rule.active_version_id)??rule.versions.at(-1);}
export function ruleEvents(rule:Rule):RuleEvent[]{return rule.events.map(value=>value&&typeof value==='object'?value as RuleEvent:{});}

/** The newest saved version of a rule when it's waiting for you: saved after the one in use (or a rule never used yet),
 * and not dismissed, used or archived since. A rollback to an older version doesn't count as waiting. */
export function waitingVersion(rule:Rule):Version|undefined{
 const newest=rule.versions.at(-1);
 if(!newest||newest.id===rule.active_version_id)return undefined;
 const events=ruleEvents(rule);
 const about=events.map((event,index)=>({event,index})).filter(({event})=>event.version_id===newest.id).at(-1);
 if(about&&!['proposed','reopened'].includes(about.event.kind??''))return undefined;
 if(events.some((event,index)=>event.kind==='archived'&&index>(about?.index??-1)))return undefined;
 return newest;
}
/** An archived rule: out of use and not waiting, but once in use. Restore brings back the version it had. */
export function archivedVersion(rule:Rule):Version|undefined{
 if(rule.active_version_id||waitingVersion(rule))return undefined;
 const events=ruleEvents(rule);
 const archived=[...events].reverse().find(event=>event.kind==='archived');
 const id=archived?.archived_version_id??[...events].reverse().find(event=>event.kind==='approved')?.version_id;
 return id?rule.versions.find(version=>version.id===id):undefined;
}
/** Where a rule came from, in the few words its card line has room for. The full provenance is under Record. */
export function ruleOrigin(provenance:string):string{
 if(/^Added in Dervo/i.test(provenance))return 'added in Dervo';
 if(/^Suggested by/i.test(provenance))return 'from a lesson';
 if(/^Noticed by an agent/i.test(provenance))return 'from an agent’s note';
 if(/^Original procedure/i.test(provenance))return 'from Dervo’s procedure';
 const first=provenance.trim().replace(/\.$/,'');
 return first.length>60?first.slice(0,57)+'…':first;
}
function approvedAt(rule:Rule,versionId:string):string|undefined{
 return [...ruleEvents(rule)].reverse().find(event=>event.kind==='approved'&&event.version_id===versionId)?.at;
}

/** What Move agent-made rules to notes starts a row on, from ``knowledge_list.move_presets``. */
export type MovePreset='move'|'drop'|'keep';
export type RuleMove={ruleId:string;expectedRevision:number;do:'move'|'drop';text:string|null};
export const NOTE_LIMIT=400;  // project_memory.ENTRY_LIMIT

/** Move agent-made rules to notes (beta.17.1, #41 step 8; Bryce, 5 Oct: "Yes, move all 12"). Up to beta.16 an agent's
 * lesson or a line Home caught became a rule once approved, and rules go ahead of notes to every run, out of date or
 * not. This lists the rules in use that came that way, each preset to Move, Drop or Keep as rule, and does them in one
 * batch with one Undo. Move keeps the rule's words, or yours: a note is 400 characters at most, so a longer rule opens
 * to be shortened before it can move. A rule you wrote is never listed, and one you kept as a rule isn't listed again:
 * ``onMove`` gets its version too. */
export function MoveRules({rows,presets,busy,onMove}:{rows:{rule:Rule;version:Version;number:number}[];presets:Record<string,MovePreset>;busy:boolean;
 onMove:(moves:RuleMove[],kept:string[])=>void}){
 const [open,setOpen]=useState(false);
 const [choices,setChoices]=useState<Record<string,MovePreset>>({});
 const [texts,setTexts]=useState<Record<string,string>>({});
 const [editing,setEditing]=useState('');
 if(!rows.length)return null;
 const choice=(id:string)=>choices[id]??presets[id]??'move';
 const words=(row:{rule:Rule;version:Version})=>texts[row.rule.id]??row.version.text;
 const tooLong=(row:{rule:Rule;version:Version})=>choice(row.rule.id)==='move'&&words(row).trim().length>NOTE_LIMIT;
 const moving=rows.filter(row=>choice(row.rule.id)==='move').length,dropping=rows.filter(row=>choice(row.rule.id)==='drop').length;
 const blocked=rows.some(tooLong)||rows.some(row=>choice(row.rule.id)==='move'&&!words(row).trim());
 const go=moving+dropping?t('Move {moving} and drop {dropping}',{moving,dropping}):t('Keep them as rules');
 if(!open)return <div className="dv-kn-dup dv-kn-move" role="group" aria-label={t('Rules agents made')} data-testid="move-rules">
  <p><b>{t('{count} of your rules came from agents or from Home.',{count:rows.length})}</b>{' '}
   {t('Move them to notes, so Your rules holds what you write. A note still goes to a request it matches.')}</p>
  <div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-kn-pill" disabled={busy} onClick={()=>setOpen(true)}>{t('Review them')}</button></div>
 </div>;
 return <div className="dv-kn-dup dv-kn-move" role="group" aria-label={t('Rules agents made')} data-testid="move-rules">
  <p><b>{t('Move agent-made rules to notes.')}</b> {t('Each starts on what Dervo suggests. A rule that limits pushing or deploying starts on Keep as rule.')}</p>
  <ul className="dv-kn-moves">{rows.map(row=>{
   const id=row.rule.id,now=choice(id),long=tooLong(row),text=words(row);
   const pick=(value:MovePreset)=>setChoices(current=>({...current,[id]:value}));
   const option=(value:MovePreset,label:string)=><button type="button" className="dv-kn-choice" aria-pressed={now===value} disabled={busy} onClick={()=>pick(value)}>{label}</button>;
   return <li key={id} className="dv-kn-moverow" data-choice={now}>
    <span className="dv-num">{row.number}</span>
    <span className="dv-kn-movetext" title={text}>{ruleTitle(text)}</span>
    {/* Edit sits on the row, so twelve rows stay twelve lines (5 Oct render: a line under each took the card to 744 px). */}
    <span className="dv-kn-moveedit-cell">{now==='move'&&editing!==id&&!long&&<button type="button" className="dv-btn dv-btn-quiet dv-kn-moveedit-open" disabled={busy}
     aria-label={t('Edit the note for rule {n}',{n:row.number})} onClick={()=>setEditing(id)}>{t('Edit')}</button>}</span>
    <span className="dv-kn-choices" role="group" aria-label={t('Rule {n}',{n:row.number})}>
     {option('move',t('Move'))}{option('drop',t('Drop'))}{option('keep',t('Keep as rule'))}
    </span>
    {now==='move'&&(editing===id||long)&&<label className="dv-kn-moveedit">
     <textarea aria-label={t('Note for rule {n}',{n:row.number})} value={text} rows={3} onChange={event=>setTexts(current=>({...current,[id]:event.target.value}))}/>
     <span className={long?'dv-caption dv-kn-alert':'dv-caption'}>{long?t('{count} of {limit} characters. Shorten it to move it.',{count:text.trim().length,limit:NOTE_LIMIT}):t('{count} of {limit} characters',{count:text.trim().length,limit:NOTE_LIMIT})}</span>
    </label>}
   </li>;})}</ul>
  <div className="dv-row-actions">
   <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||blocked}
    onClick={()=>onMove(rows.filter(row=>choice(row.rule.id)!=='keep').map(row=>({ruleId:row.rule.id,expectedRevision:row.rule.revision,do:choice(row.rule.id) as 'move'|'drop',
     text:choice(row.rule.id)==='move'&&texts[row.rule.id]!==undefined?texts[row.rule.id].trim():null})),
     rows.filter(row=>choice(row.rule.id)==='keep').map(row=>row.version.id))}>{go}</button>
   <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{setOpen(false);setEditing('');}}>{t('Not now')}</button>
  </div>
 </div>;
}

type Props={rules:Rule[];busy:boolean;selection:NotebookSelection;onSelectionChange:(selection:NotebookSelection)=>void;focusRequest?:number;
 onEdit:(rule:Rule,version:Version)=>void;onUseVersion:(rule:Rule,version:Version)=>void;onArchive:(rule:Rule)=>void;
 onOpenTaskSource?:(channelId:string,taskId:string)=>void;decision?:ReactNode;lines?:Record<string,string>;
 deliveries?:Record<string,Delivery>;onUnlink?:(rule:Rule,version:Version)=>void};

export function KnowledgeHistory({rules,busy,selection,onSelectionChange,focusRequest=0,onEdit,onUseVersion,onArchive,onOpenTaskSource,decision,lines={},deliveries={},onUnlink}:Props){
 const heading=useRef<HTMLDivElement>(null),buttons=useRef(new Map<string,HTMLButtonElement>());
 const selected=selection.reading?rules.find(rule=>rule.id===selection.ruleId):undefined;
 useLayoutEffect(()=>{if(focusRequest&&selected)heading.current?.focus();},[focusRequest]);
 function open(rule:Rule){onSelectionChange({ruleId:rule.id,versionId:'',history:false,reading:true});requestAnimationFrame(()=>heading.current?.focus());}
 function close(rule:Rule){onSelectionChange({...selection,reading:false,history:false});requestAnimationFrame(()=>buttons.current.get(rule.id)?.focus());}
 return <>{rules.map((rule,index)=>{
  const shown=shownVersion(rule);
  const delivery=deliveries[rule.id],deliveryNote=deliveryLine(delivery);
  const rowLine=lines[rule.id]||deliveryNote;
  if(selected?.id!==rule.id||!shown)return <button key={rule.id} type="button" className="dv-row" data-testid="rule-row" ref={button=>{if(button)buttons.current.set(rule.id,button);else buttons.current.delete(rule.id);}} onClick={()=>open(rule)} aria-expanded={false}>
   <span className="dv-num">{index+1}</span>
   <span className="dv-row-text"><span className="dv-row-name">{ruleTitle(shown?.text??'')}</span>{rowLine&&<span className={`dv-row-line${!lines[rule.id]&&delivery&&!delivery.sent?' dv-kn-alert':''}`}>{rowLine}</span>}</span>
   <span className="dv-row-end"><span className="dv-when">{lines[rule.id]?'Now':shown?ruleDate(shown.created_at):''}</span></span>
  </button>;
  const number=rule.versions.findIndex(saved=>saved.id===shown.id)+1;
  const title=ruleTitle(shown.text);
  const source=shown.source;
  return <article key={rule.id} className="dv-row dv-open" aria-label={`Rule ${index+1}`} data-testid="rule-open"
   onKeyDown={event=>{if(event.key==='Escape'&&!decision){event.stopPropagation();close(rule);}}}>
   <span className="dv-num">{index+1}</span>
   <div>
    <div className="dv-row-name" ref={heading} tabIndex={-1}>{title}</div>
    {shown.text.trim()!==title&&<p className="dv-kn-text">{shown.text}</p>}
    <p className="dv-row-line">In use · version {number} of {rule.versions.length} · {ruleOrigin(shown.provenance)} · {ruleDate(shown.created_at)}</p>
    {deliveryNote&&<p className={`dv-row-line${delivery&&!delivery.sent?' dv-kn-alert':''}`} data-testid="rule-delivery" role={delivery&&!delivery.sent?'alert':undefined}>{deliveryNote}{delivery&&!delivery.sent&&onUnlink&&<> <button type="button" className="dv-kn-link" disabled={busy||!!decision} onClick={()=>onUnlink(rule,shown)}>Keep it without its source</button></>}</p>}
    {rule.versions.length>1&&<div className="dv-kn-versions" aria-label="Versions">{[...rule.versions].reverse().map((saved,position)=>{
     const n=rule.versions.length-position,inUse=saved.id===rule.active_version_id,approved=approvedAt(rule,saved.id);
     return <div key={saved.id} className="dv-kn-version">
      <span className="dv-kn-v">Version {n}</span>
      <span className="dv-kn-t">{saved.text}<small>{inUse?`Approved ${ruleDate(approved??saved.created_at)}`:`Saved ${ruleDate(saved.created_at)}, not in use`}</small></span>
      {inUse?<span className="dv-kn-state">In use</span>:<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||!!decision} onClick={()=>onUseVersion(rule,saved)}>Use version {n}</button>}
     </div>;
    })}</div>}
    {decision}
    {!decision&&<div className="dv-row-actions">
     <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>onEdit(rule,shown)}>Edit</button>
     {source?.taskId&&onOpenTaskSource&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>onOpenTaskSource(source.channelId,source.taskId!)}>Open the task</button>}
     <span className="dv-kn-spacer"/>
     <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>onArchive(rule)}>Archive</button>
    </div>}
    <details className="dv-disclose"><summary>Record<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg></summary>
     <dl><dt>Rule</dt><dd>{rule.id}</dd><dt>Version</dt><dd>{shown.id}</dd><dt>Saved</dt><dd>{shown.created_at}</dd><dt>Where from</dt><dd>{shown.provenance}</dd>
      {source&&<><dt>Conversation</dt><dd>{source.channelId}</dd>{source.taskId?<><dt>Task</dt><dd>{source.taskId}</dd><dt>Run</dt><dd>{source.runId}</dd></>:<><dt>Message</dt><dd>{source.messageId}</dd></>}</>}
      <dt>Revision</dt><dd>{rule.revision}</dd></dl>
     <ul>{ruleEvents(rule).map((event,position)=>{
      const at=rule.versions.findIndex(saved=>saved.id===(event.version_id??event.archived_version_id))+1;
      const kind={approved:'Approved',archived:'Archived',proposed:'Saved',dismissed:'Dismissed',reopened:'Waiting again'}[event.kind??'']??'Recorded';
      return <li key={position}>{kind}{at>0?`, version ${at}`:''} · {event.at?ruleDate(event.at,{year:true}):'date not recorded'}</li>;
     })}</ul>
    </details>
   </div>
   <div className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>close(rule)}>Close</button></div>
  </article>;
 })}</>;
}
