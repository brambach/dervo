import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {StatusGlyph} from './StatusGlyph';
import {CATEGORY_WORDS,VERSION_SHAPE,addReport,addToPlan,itemLine,markReport,movePlanItem,readPlan,reportLine,shipPlanItem,shippedLine,showReportsHere,
 testerReports,updatedLine,workOnPlanItem,type MoveTo,type PlanItem,type PlanView,type TesterReport} from './nextUpPlan';
import './NextUp.css';
import {t,tr} from '@/i18n';

/** Next up (design b16-lifecycle, nextup): one plan per project in place of Dervo reports and Bugs and ideas (Bryce,
 * 24 Sep). It's NEXT-UP.md at the project's root, so agents in any tool read and update it; this page shows it in the
 * Knowledge page's shape and makes the few edits it offers through the host.
 *
 * Next, In progress, Not decided, Tester reports (admins, in the one project chosen for them) and Shipped, one folded
 * row per version. Nothing here is computed from chat, and the file stays the record: an edit that races an agent's is
 * refused and the page reloads.
 *
 * The props match the Bugs and ideas page it replaces, so ConnectedApp opens it from the same place until it names it. */
type Props={projectId:string;profileId?:string;projectName?:string;open:boolean;onClose:()=>void;returnLabel?:string;
 /** Start a thread: the host made the thread and drafted the request; open it with that text in the composer, unsent. */
 onWork?:(threadId:string,text:string)=>void;
 onOpenThread?:(threadId:string)=>void;
 personalFeatures?:boolean;onOpenSource?:(projectId:string,itemId:string)=>void;focusItemId?:string};

const reason=(failure:unknown)=>failure instanceof Error?failure.message:String(failure);
const PLUS=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>;

export function NextUp({projectId,projectName,open,onClose,returnLabel=t('Back to project'),onWork,onOpenThread}:Props){
 const client=useQueryClient();
 const key=['next-up',projectId];
 const plan=useQuery({queryKey:key,enabled:open&&!!projectId,retry:false,refetchInterval:open?5000:false,queryFn:()=>readPlan(projectId)});
 const view=plan.data;
 const admin=!!view?.admin;
 const reports=useQuery({queryKey:['tester-reports'],enabled:open&&admin&&!!view?.reports_here,retry:false,
  refetchInterval:query=>query.state.data?.state==='loading'?800:60000,queryFn:()=>testerReports(false)});
 const [busy,setBusy]=useState(''),[error,setError]=useState('');
 const [opened,setOpened]=useState(''),[adding,setAdding]=useState('');
 const [shipping,setShipping]=useState<{key:string;version:string}|null>(null);
 const heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{if(open)requestAnimationFrame(()=>heading.current?.focus());},[open]);

 async function run(label:string,action:()=>Promise<PlanView>,after?:(next:PlanView)=>void){
  if(busy)return false;
  setBusy(label);setError('');
  try{const next=await action();client.setQueryData(key,next);after?.(next);return true;}
  catch(failure){setError(reason(failure));void plan.refetch();return false;}
  finally{setBusy('');}
 }
 const revision=view?.revision??null;
 const move=(item:PlanItem,to:MoveTo)=>run('move',()=>movePlanItem(projectId,revision,item.key,to),()=>setOpened(''));
 async function ship(item:PlanItem,version:string){
  if(!VERSION_SHAPE.test(version.trim())){setError(t('Name the version it shipped in, like 0.1.0-beta.16.'));return;}
  if(await run('ship',()=>shipPlanItem(projectId,revision,item.key,version.trim()))){setShipping(null);setOpened('');}
 }
 async function work(item:PlanItem){
  if(busy)return;
  setBusy('work');setError('');
  try{const reply=await workOnPlanItem(projectId,revision,item.key);client.setQueryData(key,reply);onWork?.(reply.thread.id,reply.text);}
  catch(failure){setError(reason(failure));void plan.refetch();}
  finally{setBusy('');}
 }
 async function add(){
  const title=adding.trim();
  if(!title)return;
  if(await run('add',()=>addToPlan(projectId,revision,title)))setAdding('');
 }
 async function report(action:'add'|'seen',row:TesterReport){
  if(action==='add'){
   if(await run('report',()=>addReport(projectId,revision,row.id)))await client.invalidateQueries({queryKey:['tester-reports']});
  }else{
   setError('');
   try{await markReport(row.id,true);}catch(failure){setError(reason(failure));}
   await client.invalidateQueries({queryKey:['tester-reports']});
  }
  void client.invalidateQueries({queryKey:['tester-reports-count']});
 }
 function openFile(){void local('project_file_open',{projectId,path:view?.file??'NEXT-UP.md'}).catch(failure=>setError(reason(failure)));}

 if(!open)return null;
 const name=view?.name??projectName??t('this project');
 const sections=view?.sections??{next:[],progress:[],undecided:[]};
 const empty=!!view&&!sections.next.length&&!sections.progress.length&&!sections.undecided.length&&!view.shipped.length;
 const reportRows=reports.data?.reports??[];
 const unseen=reportRows.filter(row=>!row.seen).length;

 const acts=(item:PlanItem,extra?:ReactNode)=><span className="dv-nu-acts">{extra}
  <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={event=>{event.stopPropagation();setShipping(null);setOpened(opened===item.key?'':item.key);}}>{opened===item.key?t('Done'):t('Edit')}</button></span>;
 /** An open line's own actions: where else it can go, and Shipped with its version. */
 const editing=(item:PlanItem,here:'next'|'progress')=><div className="dv-row-actions">
  {here!=='next'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void move(item,'next')}>{t('Move to Next')}</button>}
  {here!=='progress'&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void move(item,'progress')}>{t('In progress')}</button>}
  <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void move(item,'undecided')}>{t('Not decided')}</button>
  {shipping?.key===item.key
   ?<span className="dv-nu-ship"><input className="dv-input" value={shipping.version} placeholder="0.1.0-beta.16" aria-label={t('Version {title} shipped in',{title:item.title})} autoFocus
     onChange={event=>setShipping({key:item.key,version:event.target.value})}
     onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void ship(item,shipping.version);}if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setShipping(null);}}}/>
     <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void ship(item,shipping.version)}>{t('Mark shipped')}</button></span>
   :<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>setShipping({key:item.key,version:''})}>{t('Shipped…')}</button>}
  <span className="dv-nu-spacer"/>
  <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void move(item,'drop')}>{t('Drop')}</button>
 </div>;
 const details=(item:PlanItem)=><>
  {item.notes.length>1&&<div className="dv-nu-notes">{item.notes.slice(1).map((note,i)=><p key={i}>{note}</p>)}</div>}
  {item.sent.length>0&&<details className="dv-nu-sent" open><summary>{t('Sent with it')}</summary><dl>{item.sent.map(([k,v],i)=><div key={i} className="dv-nu-pair"><dt>{k}</dt><dd>{v}</dd></div>)}</dl></details>}
 </>;
 const threadLink=(item:PlanItem)=>item.thread_id&&onOpenThread
  ?<button type="button" className="dv-nu-link" onClick={()=>onOpenThread(item.thread_id!)}>{item.thread}</button>:<span>{item.thread}</span>;

 return <section aria-label={t('Next up')} className="dv-nextup" onKeyDown={event=>{if(event.key==='Escape'&&!event.defaultPrevented){event.stopPropagation();if(opened)setOpened('');else onClose();}}}>
  <header className="dv-main-head dv-nextup-bar"><span className="dv-title">{projectName??name}</span><span className="dv-crumb">{t('Next up')}</span>
   <div className="dv-tools"><button type="button" className="dv-btn dv-btn-quiet" onClick={onClose}>{returnLabel}</button></div></header>
  <div className="dv-column dv-nextup-body"><div className="dv-measure">
   <section className="dv-nu-head" aria-label={t('Next up')}>
    <h1 ref={heading} className="dv-display-1" tabIndex={-1}>{t('Next up')}</h1>
    <p className="dv-nu-lead">{tr('What’s next for {name}, in one plan. It’s {file} in the project, so agents in Claude Code and Codex read it and keep it current too.',{name,file:<span className="dv-nu-file">{view?.file??'NEXT-UP.md'}</span>})}</p>
    {view?.exists&&<p className="dv-nu-meta"><span>{updatedLine(view.modified)}</span><button type="button" className="dv-btn dv-btn-quiet" onClick={openFile}>{t('Open the file')}</button></p>}
    {error&&<p className="dv-nu-error" role="alert">{error}</p>}
    {plan.isError&&<p className="dv-nu-error" role="alert">{t('Next up couldn’t load. {why}',{why:reason(plan.error)})} <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void plan.refetch()}>{t('Try again')}</button></p>}
    {plan.isPending&&<p className="dv-nu-line">{t('Reading NEXT-UP.md…')}</p>}
   </section>

   {view&&<>
    {/* A section title, not a second display heading under the page's (polish, 8 Oct). */}
    {empty&&<div className="dv-quiet dv-nu-quiet"><h2 className="dv-title">{t('Nothing planned yet')}</h2>
     <p className="dv-small">{t('Add something to decide later, below, or log it with ⌘⇧B from anywhere. Dervo makes {file} the first time.',{file:view.file})}</p></div>}

    {sections.next.length>0&&<section className="dv-sec" aria-labelledby="dv-nu-next">
     <div className="dv-sec-head"><h2 className="dv-title" id="dv-nu-next">{t('Next')}</h2><span className="dv-nu-count">{sections.next.length}</span><span className="dv-caption">{t('Decided, not started')}</span></div>
     <div className="dv-sheet">{sections.next.map((item,index)=><div key={item.key} className={`dv-row dv-nu-row${opened===item.key?' dv-open':''}`}>
      <span className="dv-num">{index+1}</span>
      <div><div className="dv-row-name">{item.title}</div>{itemLine({...item,day:null})&&<div className="dv-row-line">{itemLine({...item,day:null})}</div>}
       {opened===item.key&&<>{details(item)}{editing(item,'next')}</>}</div>
      <div className="dv-row-end">{acts(item,onWork&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void work(item)}>{t('Start a thread')}</button>)}
       {item.day&&<span className="dv-when">{item.day}</span>}</div>
     </div>)}</div>
    </section>}

    {sections.progress.length>0&&<section className="dv-sec" aria-labelledby="dv-nu-progress">
     <div className="dv-sec-head"><h2 className="dv-title" id="dv-nu-progress">{t('In progress')}</h2><span className="dv-nu-count">{sections.progress.length}</span></div>
     <div className="dv-sheet">{sections.progress.map(item=><div key={item.key} className={`dv-row dv-nu-row${opened===item.key?' dv-open':''}`}>
      <StatusGlyph state={item.thread_state==='running'?'running':item.thread_state==='needs'?'needs':'idle'} label={item.thread_state==='running'?t('Running'):undefined}/>
      <div><div className="dv-row-name">{item.title}</div>
       <div className="dv-row-line">{item.thread?<>{threadLink(item)}{item.thread_state==='running'?` · ${t('running')}`:item.thread_state==='needs'?` · ${t('needs you')}`:item.thread_id?'':` · ${t('thread not found')}`}</>:itemLine(item)}</div>
       {opened===item.key&&<>{details(item)}{editing(item,'progress')}</>}</div>
      <div className="dv-row-end">{acts(item)}{item.day&&<span className="dv-when">{item.day}</span>}</div>
     </div>)}</div>
    </section>}

    <section className="dv-sec" aria-labelledby="dv-nu-undecided">
     <div className="dv-sec-head"><h2 className="dv-title" id="dv-nu-undecided">{t('Not decided')}</h2>{sections.undecided.length>0&&<span className="dv-nu-count">{sections.undecided.length}</span>}<span className="dv-caption">{t('From ⌘⇧B and Home')}</span></div>
     <div className="dv-sheet">
      {sections.undecided.map(item=>{
       const isOpen=opened===item.key;
       return <div key={item.key} className="dv-row dv-nu-row dv-nu-undecided" data-open={isOpen||undefined}>
        <StatusGlyph state="idle"/>
        <div><div className="dv-row-name dv-nu-name">{item.title}</div>
         <div className="dv-row-line">{itemLine({...item,notes:isOpen?[]:item.notes})}{!isOpen&&(item.sent.length>0||item.notes.length>1)&&<> · <button type="button" className="dv-nu-link" onClick={()=>setOpened(item.key)}>{item.sent.length?t('Sent with it'):t('More')}</button></>}</div>
         {isOpen&&<>{item.notes[0]&&<div className="dv-nu-notes"><p>{item.notes[0]}</p></div>}{details(item)}
          <div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void move(item,'next')}>{t('Move to Next')}</button>
           <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void move(item,'drop')}>{t('Drop')}</button></div></>}</div>
        <div className="dv-row-end">{!isOpen&&<span className="dv-nu-acts"><button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void move(item,'next')}>{t('Move to Next')}</button></span>}
         {!isOpen&&item.day&&<span className="dv-when">{item.day}</span>}</div>
       </div>;
      })}
      <label className="dv-add-line">{PLUS}
       <input value={adding} maxLength={120} placeholder={t('Add something to decide later')} aria-label={t('Add to Not decided')} disabled={busy==='add'}
        onChange={event=>setAdding(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.nativeEvent.isComposing){event.preventDefault();void add();}}}/>
       <span className="dv-hint">{t('Return to add')}</span></label>
     </div>
    </section>

    {admin&&(view.reports_here?<section className="dv-sec" aria-labelledby="dv-nu-testers">
     <div className="dv-sec-head"><h2 className="dv-title" id="dv-nu-testers">{t('Tester reports')}</h2>{unseen>0&&<span className="dv-nu-count">{t('{count} new',{count:unseen})}</span>}<span className="dv-caption">{t('Only you see these. Discord still gets them.')}</span></div>
     <div className="dv-sheet">
      {reports.data?.state==='loading'&&!reportRows.length&&<p className="dv-nu-line">{t('Reading tester reports…')}</p>}
      {(reports.isError||reports.data?.state==='error')&&<p className="dv-nu-line" role="alert">{t('Tester reports couldn’t load.')} <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void testerReports(true).then(()=>reports.refetch())}>{t('Try again')}</button></p>}
      {reports.data?.state==='ready'&&!reportRows.length&&<p className="dv-nu-line">{t('No tester reports in the last 30 days.')}</p>}
      {reportRows.map(row=><div key={row.id} className="dv-row dv-nu-row">
       <StatusGlyph state={row.seen?'done':'needs'} label={row.seen?undefined:t('New')}/>
       <div><div className="dv-row-name dv-nu-name">{row.message}</div><div className="dv-row-line">{reportLine(row)}</div>
        {!row.seen&&<div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void report('add',row)}>{t('Add to Next up')}</button>
         <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void report('seen',row)}>{t('Mark seen')}</button></div>}</div>
       <div className="dv-row-end"><span className="dv-nu-cat">{CATEGORY_WORDS[row.category]}</span></div>
      </div>)}
     </div>
    </section>
    :<p className="dv-nu-line dv-nu-choose">{view.reports_chosen?t('Tester reports go to one project at a time, and right now that’s another one.'):t('Tester reports go to one project at a time. Pick this one to see them here.')}
     <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void run('here',()=>showReportsHere(projectId,true))}>{view.reports_chosen?t('Show them here instead'):t('Show them here')}</button></p>)}

    {view.shipped.length>0&&<section className="dv-sec" aria-labelledby="dv-nu-shipped">
     <div className="dv-sec-head"><h2 className="dv-title" id="dv-nu-shipped">{t('Shipped')}</h2></div>
     <div className="dv-sheet">{view.shipped.map(group=><details key={group.version} className="dv-nu-shipped">
      <summary className="dv-row"><StatusGlyph state="done"/><span className="dv-nu-text"><span className="dv-row-name">{t('In {version}',{version:group.version})}</span><span className="dv-row-line">{shippedLine(group)}</span></span>
       <span className="dv-row-end">{group.day&&<span className="dv-when">{group.day}</span>}</span></summary>
      <ul className="dv-nu-shipped-items">{group.items.map(item=><li key={item.key}>{item.title}{item.day?<span className="dv-when"> · {item.day}</span>:null}</li>)}</ul>
     </details>)}</div>
    </section>}

    {view.other.length>0&&<p className="dv-nu-line">{t('Also in {file}: {items}.',{file:view.file,items:view.other.join(', ')})} <button type="button" className="dv-btn dv-btn-quiet" onClick={openFile}>{t('Open the file')}</button></p>}
   </>}
  </div></div>
 </section>;
}
