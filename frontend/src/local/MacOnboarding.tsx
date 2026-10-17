import {useEffect,useRef,useState} from 'react';
import {StatusGlyph} from './StatusGlyph';
import {candidateLine,nameFor,namesFor,tildePath,type HistoryCandidate} from './historyImport';
import {FOLDER_PILES,LOOK_MS,WHY_LEAD,WHY_REST,bringIn,bringLabel,carryPicks,collapseNumbers,groupGuess,initialPicks,lookAtMac,nothingFound,personalOnly,quieterLine,readHistory,restorePick,rowGuess,
 sideName,suggestPlan,threadsFor,threadsQuestion,toolsLabel,unreadLine,looseLine,waitingLine,type Context,type Pick,type Shortlist,type SideAccount,type Survey,type SurveyProject} from './onboarding';
import {AddWorkAccount,WorkAdded,type AccountProfile,type WorkSide} from './WorkAccount';
import {WorkTag} from './WorkMark';
import {formatList,formatNumber,t,tr} from '@/i18n';
import {isHostMessage} from '@/i18n/hostMessages';
import './MacOnboarding.css';

/** The whole-Mac onboarding's screens after the Ask card says yes (onboarding-v2.html: Look, Collapse, Suggest, Threads).
 * Every number is the host's own result; the counters only replay it, over about two seconds, the same honesty rule
 * ImportHistory.tsx follows. Nothing is made until Bring in, and message text is read only after the second yes. */

/** The personal account: ``who`` is "Claude Code · you@example.com" for the Personal group's head. */
export type OnboardingAgent={ready:boolean;name:string;who?:string;line:string;prepare:()=>Promise<SideAccount>};
/** The work side and how to add a work account in place. Left out, there's no work account and none can be added here. */
export type OnboardingWork={side:WorkSide;profiles:AccountProfile[];onChanged:()=>Promise<unknown>};
const NO_WORK:WorkSide={state:'none',who:'',line:t('No work account yet'),prepare:async()=>{throw new Error(t('Add a work account first.'));}};
type Props={agent:OnboardingAgent;work?:OnboardingWork;onDone:(projectId:string|null)=>void;
 /** Back to the Ask card. */
 onBack:()=>void;
 /** Nothing from this Mac: first run carries on with one folder and no history offer. */
 onStartEmpty:()=>void;
 /** Choose one folder now, from the nothing-found screen. Left out, that button goes Back. */
 onChooseFolder?:()=>void;
 /** For tests and the design shots: a look the screen shows instead of asking the host, and no replay. ``lookedAt``
  * (ms) is when that look was taken, so a shot can show one that's gone stale; left out, it's now. */
 survey?:Survey;still?:boolean;lookedAt?:number};
type Stage='looking'|'collapse'|'suggest'|'threads'|'bringing'|'failed';
const REPLAY_MS=2200;
const reduced=()=>typeof window!=='undefined'&&!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const ago=(at:number|null,now=Date.now()/1000)=>{if(!at)return '';const days=Math.floor((now-at)/86400);return days<=0?t('today'):days===1?t('yesterday'):t('{days} days ago',{days});};
/** A line of Bring in's progress, and the mark beside it. */
type Line={text:string;state:'running'|'idle'|'done'};

/** A number that counts up to its value once, or shows it at once when motion is reduced or the screen is held. */
function Counter({value,run,still}:{value:number;run:boolean;still?:boolean}){
 const [shown,setShown]=useState(still||!run?value:0);
 useEffect(()=>{
  if(still||reduced()||!run){setShown(value);return;}
  const start=performance.now();let frame=0;
  const step=(now:number)=>{const t=Math.min(1,(now-start)/REPLAY_MS);setShown(Math.round(value*(1-Math.pow(1-t,3))));if(t<1)frame=requestAnimationFrame(step);};
  frame=requestAnimationFrame(step);return ()=>cancelAnimationFrame(frame);
 },[value,run,still]);
 return <b className="dv-ob-num">{formatNumber(shown)}</b>;
}

/** The why, once, where the choice is made. */
const SPLIT=<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M16 3h5v5"/><path d="M8 3H3v5"/><path d="M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3"/><path d="m15 9 6-6"/></svg>;
export const WhyLine=()=><p className="dv-ob-why-split" role="note">{SPLIT}<span><b>{WHY_LEAD}</b> {WHY_REST}</span></p>;

export function MacOnboarding({agent,work,onDone,onBack,onStartEmpty,onChooseFolder,survey:given,still,lookedAt:givenAt}:Props){
 const [survey,setSurvey]=useState<Survey|null>(given??null);
 // The look's age (NEXT-UP #36). The host refuses one over an hour old, and Back only moves between these screens, so
 // without a way to look again in place the only way out was relaunching Dervo.
 const [lookedAt,setLookedAt]=useState<number|null>(given?givenAt??Date.now():null);
 const [stale,setStale]=useState<null|'old'|'gone'>(null);
 const [rescanning,setRescanning]=useState(false);
 const [rescanNote,setRescanNote]=useState('');
 // Sessions a tool ran, off unless ticked (NEXT-UP #38), and names for the ones the rules couldn't name (NEXT-UP #37).
 const [withTools,setWithTools]=useState(false);
 const [names,setNames]=useState<Record<string,string>>({});
 const [stage,setStage]=useState<Stage>(given?(still?'collapse':'looking'):'looking');
 const [error,setError]=useState('');
 const [picks,setPicks]=useState<Pick[]>(given?initialPicks(given):[]);
 const [aside,setAside]=useState(false);
 const [editing,setEditing]=useState<string|null>(null);
 const [lists,setLists]=useState<Record<string,Shortlist>>({});
 const [chosen,setChosen]=useState<Record<string,string[]>>({});
 const [reading,setReading]=useState<string|null>(null);
 const [progress,setProgress]=useState<Line[]>([]);
 const [adding,setAdding]=useState(false),[added,setAdded]=useState<AccountProfile|null>(null);
 const personalAccount=useRef<SideAccount|null>(null);
 const heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{requestAnimationFrame(()=>heading.current?.focus());},[stage]);
 async function look(){
  setStage('looking');setError('');
  try{const found=await lookAtMac();setSurvey(found);setPicks(initialPicks(found));setLookedAt(Date.now());}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));setStage('failed');}
 }
 useEffect(()=>{if(!given)void look();},[]);
 /** Whether the look is past the host's hour now. Checked on a timer and again before each step, since a Mac that slept
  * doesn't run the timer. */
 const expired=()=>lookedAt!==null&&Date.now()-lookedAt>=LOOK_MS;
 useEffect(()=>{
  if(lookedAt===null)return;
  const left=lookedAt+LOOK_MS-Date.now();
  if(left<=0){setStale('old');return;}
  setStale(null);
  const timer=setTimeout(()=>setStale('old'),left);
  const woke=()=>{if(expired())setStale('old');};
  window.addEventListener('focus',woke);
  return ()=>{clearTimeout(timer);window.removeEventListener('focus',woke);};
 },[lookedAt]);
 /** A host refusal that a new look fixes: the look is over an hour old, or the host no longer has it. */
 const lookFailed=(failure:unknown):null|'old'|'gone'=>isHostMessage(failure,'look_stale')?'old':isHostMessage(failure,'look_gone')?'gone':null;
 /** Look at this Mac again in place, on whichever screen you're on: what you'd picked carries over by key, and threads
  * already listed are read again from the new look so they're current. */
 async function rescan(){
  if(rescanning)return;
  setRescanning(true);setError('');setRescanNote('');
  try{
   const found=await lookAtMac();
   const carried=carryPicks(picks,found);
   setSurvey(found);setPicks(carried.picks);setLookedAt(Date.now());setStale(null);
   const keys=new Set(carried.picks.map(pick=>pick.key));
   const reread=Object.keys(lists).filter(key=>keys.has(key));
   setLists({});
   setChosen(current=>Object.fromEntries(Object.entries(current).filter(([key])=>keys.has(key))));
   setRescanNote(carried.gone.length?t('Looked at this Mac again. {names} isn’t in the new look, so it’s left out.',{names:formatList(carried.gone)})
    :t('Looked at this Mac again. Your picks are as you left them.'));
   if(reread.length)await readThreads(found,withTools,carried.picks);
  }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setRescanning(false);}
 }
 // The replay: the host's result, counted up, then the collapse line.
 useEffect(()=>{
  if(stage!=='looking'||!survey)return;
  const timer=setTimeout(()=>setStage('collapse'),reduced()||still?0:REPLAY_MS+400);
  return ()=>clearTimeout(timer);
 },[stage,survey]);

 const byKey=new Map<string,SurveyProject>((survey?.projects??[]).map(p=>[p.key,p]));
 const side=work?.side??NO_WORK,workReady=side.state==='ready';
 const plan=suggestPlan(picks,agent,{ready:workReady,adding});
 const ticked=plan.now;
 const only=personalOnly(picks);
 const threadCount=Object.values(chosen).reduce((n,ids)=>n+ids.length,0);
 const update=(key:string,change:Partial<Pick>)=>setPicks(rows=>rows.map(row=>row.key===key?{...row,...change}:row));
 const move=(key:string,context:Context)=>update(key,{context,picked:true});
 /** The account each side starts on, asked for once: the personal one may be made from your existing Claude Code. */
 async function accountFor(context:Context):Promise<SideAccount>{
  if(context==='work')return side.prepare();
  personalAccount.current??=await agent.prepare();
  return personalAccount.current;
 }

 /** Read the ticked folders' threads. ``fresh`` is a look just taken (rescan) or the tool-run choice just changed: every
  * folder is read again, and what you'd ticked that's still listed stays ticked. */
 async function readThreads(look:Survey|null=survey,include=withTools,fresh?:Pick[],tickTools=false){
  if(!look)return;
  if(look===survey&&expired()){setStale('old');return;}
  setError('');
  const keys=fresh?new Set(fresh.map(pick=>pick.key)):null;
  for(const pick of ticked){
   if(keys?!keys.has(pick.key):lists[pick.key])continue;
   setReading(pick.name);
   try{
    const account=await accountFor(pick.context!);
    const found=await threadsFor(look.surveyId,pick.key,pick.context!,account.profileId,include);
    const listed=new Set(found.candidates.map(c=>c.sessionId));
    setLists(current=>({...current,[pick.key]:found}));
    // Read again, what you'd ticked that's still listed stays ticked. Right after you ask for tool runs (``tickTools``),
    // the open ones are ticked like any other open thread; a later read leaves your ticks alone.
    setChosen(current=>({...current,[pick.key]:fresh&&current[pick.key]?[...current[pick.key].filter(id=>listed.has(id)),
     ...(tickTools?found.candidates.filter(c=>c.toolRun&&c.recommended&&!current[pick.key].includes(c.sessionId)).map(c=>c.sessionId):[])]
     :found.candidates.filter(c=>c.recommended).map(c=>c.sessionId)}));
   }catch(failure){
    const lost=lookFailed(failure);
    if(lost){setStale(lost);setError('');break;}
    setError(`${pick.name}: ${failure instanceof Error?failure.message:String(failure)}`);
   }
  }
  setReading(null);
 }
 /** Tool-run sessions in or out: the lists already read are read again with the new choice. */
 async function toggleTools(on:boolean){
  setWithTools(on);
  const read=ticked.filter(pick=>lists[pick.key]);
  if(read.length)await readThreads(survey,on,read,on);
 }

 async function bring(withThreads:boolean){
  if(!survey||!plan.go)return;
  if(expired()){setStale('old');return;}
  setStage('bringing');setError('');setProgress([]);
  try{
   const going=[...plan.now,...plan.waiting];
   const personal=going.some(p=>p.context==='personal')?await accountFor('personal'):null;
   const workAccount=plan.now.some(p=>p.context==='work')?await accountFor('work'):null;
   const batch=await bringIn(survey.surveyId,going.map(p=>({key:p.key,name:p.name.trim()||byKey.get(p.key)?.name||t('Project'),context:p.context!})),personal,workAccount);
   const said:Line[]=batch.projects.map(p=>({text:p.context==='work'?t('Made {name}, under Work',{name:p.name}):t('Made {name}',{name:p.name}),state:'done'}));
   if(batch.waiting?.length)said.push({text:waitingLine(batch.waiting.map(w=>w.name)),state:'idle'});
   setProgress(said);
   if(withThreads)for(const project of batch.projects){
    const ids=project.key?chosen[project.key]??[]:[];
    if(!ids.length)continue;
    const listed=project.key?lists[project.key]?.candidates??[]:[];
    const picked=ids.slice(0,50);
    setProgress(current=>[...current,{text:t('Reading {name}',{name:project.name}),state:'running'}]);
    try{const read=await readHistory(project.id,picked,{names:namesFor(listed,picked,names),includeToolRuns:listed.some(c=>c.toolRun&&picked.includes(c.sessionId))});
     const made=read.workstreams.reduce((n,w)=>n+w.threads.length,0)+(read.unfiled?.length??0);
     setProgress(current=>[...current.slice(0,-1),{text:`${project.name}: ${t('{count} threads',{count:made})}`,state:'done'}]);}
    catch(failure){setProgress(current=>[...current.slice(0,-1),{text:t('{name}: couldn’t read its threads. {why}',{name:project.name,why:failure instanceof Error?failure.message:String(failure)}),state:'done'}]);}
   }
   onDone(batch.projects[0]?.id??null);
  }catch(failure){
   const lost=lookFailed(failure);
   if(lost)setStale(lost);
   else setError(failure instanceof Error?failure.message:String(failure));
   setStage(withThreads?'threads':'suggest');
  }
 }
 /** Over an hour old or gone from the host: what happened and Scan this Mac again, in place on Suggest and Threads. */
 const staleBox=stale&&<div className="dv-ob-stale" role="status">
  <p>{stale==='gone'?t('Dervo doesn’t have this look at your Mac any more, so it needs a new one before it reads or brings anything in.')
   :t('This look at your Mac is more than an hour old, so Dervo needs a new one before it reads or brings anything in.')}</p>
  <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={rescanning} onClick={()=>void rescan()}>{rescanning?t('Looking…'):t('Scan this Mac again')}</button>
 </div>;
 const rescanLine=!stale&&rescanNote&&<p className="dv-caption" role="status">{rescanNote}</p>;

 if(stage==='failed')return <section className="dv-ob" aria-label={t('Looking at this Mac')}>
  <h2 className="dv-display-2" ref={heading} tabIndex={-1}>{t('Dervo couldn’t look at this Mac.')}</h2>
  <p className="dv-err" role="alert">{error}</p>
  <div className="dv-ob-actions"><button type="button" className="dv-btn dv-btn-quiet" onClick={onBack}>{t('Back')}</button><button type="button" className="dv-btn dv-btn-primary" onClick={()=>void look()}>{t('Try again')}</button></div>
 </section>;

 // ---------------------------------------------------------------- Look, then the collapse
 if(stage==='looking'||stage==='collapse'){
  const counts=survey?.counts??{files:0,folders:0,working:0,quieter:0,loose:0};
  const done=stage==='collapse';
  const [files,folders,things]=collapseNumbers(counts);
  if(done&&survey&&nothingFound(survey))return <section className="dv-ob" aria-label={t('Looking at this Mac')}>
   <h2 className="dv-display-2" ref={heading} tabIndex={-1}>{t('Nothing from Claude Code or Codex on this Mac yet.')}</h2>
   <p className="dv-lede">{t('That’s fine. Choose the folder you want to work in, and Dervo starts there.')}</p>
   {/* It opens the folder picker itself (audit, issue 9), rather than going back to the card that offers it. */}
   <div className="dv-ob-actions"><button type="button" className="dv-btn dv-btn-quiet" onClick={onBack}>{t('Back')}</button>
    <button type="button" className="dv-btn dv-btn-primary" onClick={onChooseFolder??onBack}>{t('Choose one folder')}</button></div>
  </section>;
  return <section className="dv-ob" aria-label={t('Looking at this Mac')} aria-busy={!done}>
   {done?<h2 className="dv-display-2 dv-ob-collapse" ref={heading} tabIndex={-1}><span>{files}</span> <span>{folders}</span> <span className="dv-ob-keep">{things}</span></h2>
    :<h2 className="dv-display-2" ref={heading} tabIndex={-1}>{t('Looking at folder names')}</h2>}
   {done&&quieterLine(counts)&&<p className="dv-lede">{quieterLine(counts)}</p>}
   {done&&survey&&unreadLine(survey)&&<p className="dv-lede" role="status">{unreadLine(survey)}</p>}
   {!done&&<div className="dv-ob-counters" aria-live="off">
    <p><Counter value={counts.files} run={!!survey} still={still}/> {t('conversation files')}</p>
    <p><Counter value={counts.folders} run={!!survey} still={still}/> {t('folders')}</p>
    <p><Counter value={counts.working} run={!!survey} still={still}/> {t('things you’re working on')}</p>
   </div>}
   <div className="dv-ob-columns">
    <div><p className="dv-label">{t('Setting aside')}</p>
     <ul className="dv-ob-piles">{(survey?.set_aside??[]).map(pile=><li key={pile.rule} data-rule={pile.rule}>{pile.label}</li>)}</ul></div>
    <div><p className="dv-label">{t('Keeping')}</p>
     <ul className="dv-ob-kept">{(survey?.projects??[]).filter(p=>p.ticked).map(p=><li key={p.key}><StatusGlyph state="done"/>{p.name}</li>)}</ul></div>
   </div>
   <p className="dv-caption dv-ob-fine">{t('Folder names, dates and counts only. Nothing leaves this Mac and no model is called.')}</p>
   <div className="dv-ob-actions"><button type="button" className="dv-btn dv-btn-quiet" onClick={onStartEmpty}>{t('Start empty')}</button>
    <button type="button" className="dv-btn dv-btn-primary" disabled={!done} onClick={()=>setStage('suggest')}>{t('See what Dervo suggests')}</button></div>
  </section>;
 }

 // ---------------------------------------------------------------- Bringing in
 if(stage==='bringing')return <section className="dv-ob" aria-label={t('Bringing in')} aria-busy="true">
  <h2 className="dv-display-2" ref={heading} tabIndex={-1}>{t('Bringing it in')}</h2>
  <ul className="dv-ob-progress" aria-live="polite">{progress.map((line,n)=><li key={n}><StatusGlyph state={line.state==='running'&&n<progress.length-1?'done':line.state}/>{line.text}</li>)}</ul>
  <p className="dv-caption dv-ob-fine">{t('Nothing is sent and no agent starts.')}</p>
 </section>;

 // ---------------------------------------------------------------- Threads, the second yes
 if(stage==='threads'){
  const read=ticked.every(p=>lists[p.key]);
  const open=ticked.reduce((n,p)=>n+(lists[p.key]?.candidates.filter(c=>c.recommended).length??0),0);
  const toolRuns=ticked.reduce((n,p)=>n+(lists[p.key]?.toolRuns??0),0);
  return <section className="dv-ob" aria-label={t('Name the threads')}>
   <h2 className="dv-display-2" ref={heading} tabIndex={-1}>{read?t('{count} open threads',{count:open}):t('Name the threads too?')}</h2>
   <p className="dv-lede">{read?t('Open ones are ticked. Finished ones are here if you want them. Nothing starts running, and every thread says where it came from.'):threadsQuestion(ticked.length)}</p>
   {!read&&<div className="dv-ob-group"><ul className="dv-ob-list">{ticked.map(p=>{const project=byKey.get(p.key);return <li key={p.key} className="dv-ob-row" data-compact>
    <span className="dv-ob-name">{p.name}{p.context==='work'&&!only&&<WorkTag/>}</span><span className="dv-caption">{project?t('{count} conversations',{count:project.conversations}):''}</span></li>;})}</ul></div>}
   {staleBox}{rescanLine}
   {read&&(toolRuns>0||withTools)&&<label className="dv-ob-tools">
    <input type="checkbox" checked={withTools} disabled={!!reading||!!stale} onChange={event=>void toggleTools(event.target.checked)}/>
    <span><b>{t('Include sessions run by a tool ({count})',{count:toolRuns})}</b>
     <small>{t('Runs a script or another app started, like codex exec. They come in marked as the tool’s, not your words.')}</small></span></label>}
   {read&&ticked.map(p=>{const list=lists[p.key];const picked=new Set(chosen[p.key]??[]);
    return <fieldset key={p.key} className="dv-ob-threads"><legend>{p.name}{p.context==='work'&&<> <WorkTag/></>}<span className="dv-caption"> · {t('{count} threads ticked',{count:picked.size})}</span></legend>
     {list.detail&&<p className="dv-caption">{list.detail}</p>}
     {!list.candidates.length&&!list.detail&&<p className="dv-caption">{t('No conversations here to bring in. It comes in empty, with the offer waiting inside it.')}</p>}
     {list.candidates.map((c:HistoryCandidate)=><label key={c.sessionId} className="dv-ob-thread" data-unnamed={c.unnamed||undefined} data-tool={c.toolRun||undefined}>
      <input type="checkbox" checked={picked.has(c.sessionId)} disabled={!picked.has(c.sessionId)&&picked.size>=list.limit}
       onChange={event=>setChosen(current=>({...current,[p.key]:event.target.checked?[...(current[p.key]??[]),c.sessionId]:(current[p.key]??[]).filter(id=>id!==c.sessionId)}))}/>
      <span>{c.unnamed?<b className="dv-ob-unnamed">{t('Couldn’t safely generate a name')}</b>:<b>{c.name}</b>}
       <small>{candidateLine(c,c.recommended?`${t('open|a thread still going')}${c.last_active?` · ${ago(c.last_active)}`:''}`:c.reason)}</small>
       {c.unnamed&&<><span className="dv-ob-excerpt">{c.excerpt}</span>
        <input className="dv-input dv-ob-name-it" value={names[c.sessionId]??''} maxLength={100} placeholder={nameFor(c,'')} aria-label={t('Name for this conversation')}
         onChange={event=>{const value=event.target.value;setNames(current=>({...current,[c.sessionId]:value}));}}/></>}</span></label>)}
    </fieldset>;})}
   {error&&<p className="dv-err" role="alert">{error}</p>}
   {read&&<p className="dv-caption dv-ob-fine">{t('Filed into workstreams by rule, not by a model. Move anything and it stays moved.')}</p>}
   {/* Pinned like Suggest's: once the threads are listed, Bring in would sit below the window (design review S4, 25 Sep). */}
   <div className="dv-ob-bar">
   {reading&&<p className="dv-caption" role="status"><StatusGlyph state="running"/> {t('Reading {name}…',{name:reading})}</p>}
   <div className="dv-ob-actions">
    <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setStage('suggest')}>{t('Back')}</button>
    {!read&&<button type="button" className="dv-btn dv-btn-ghost" disabled={!!reading||!!stale} onClick={()=>void bring(false)}>{t('Bring in projects only')}</button>}
    {!read?<button type="button" className="dv-btn dv-btn-primary" disabled={!!reading||!!stale} onClick={()=>void readThreads()}>{reading?t('Reading…'):t('Read and name them')}</button>
     :<button type="button" className="dv-btn dv-btn-primary" disabled={!!reading||!!stale} onClick={()=>void bring(true)}>{bringLabel(ticked.length,threadCount)}</button>}
   </div>
   </div>
   {!read&&<p className="dv-caption dv-ob-fine">{t('Projects only means each one comes in empty, with this offer waiting inside it.')}</p>}
  </section>;
 }


 // ---------------------------------------------------------------- Suggest
 // Work and Personal (b16-work): each group names the account it runs on, each row says why it's on its side with Move
 // to one click away, and nothing held stops the rest. Someone with only Personal projects sees one card and no sides.
 const row=(pick:Pick)=>{
  const project=byKey.get(pick.key);
  const id=`dv-ob-${pick.key}`;
  const to:Context|null=pick.context?(pick.context==='work'?'personal':'work'):null;
  const guess=only?null:rowGuess(pick,project);
  const renameButton=<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm dv-ob-rename-btn" onClick={()=>setEditing(pick.key)} aria-label={t('Rename {name}',{name:pick.name})}>{t('Rename')}</button>;
  const moveButton=to&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm dv-ob-move" onClick={()=>move(pick.key,to)}
   aria-label={t('Move {name} to {side}',{name:pick.name,side:sideName(to)})}>{t('Move to {side}',{side:sideName(to)})}</button>;
  return <li key={pick.key} className="dv-ob-row" data-ticked={pick.ticked||undefined} data-editing={editing===pick.key||undefined}>
   <input id={id} type="checkbox" checked={pick.ticked} onChange={event=>update(pick.key,{ticked:event.target.checked})} aria-describedby={`${id}-why`}/>
   <div className="dv-ob-row-name">
    {editing===pick.key?<input className="dv-input dv-ob-rename" aria-label={t('Name for {name}',{name:project?.path??pick.name})} value={pick.name} maxLength={80} autoFocus
      onChange={event=>update(pick.key,{name:event.target.value})} onBlur={()=>setEditing(null)} onKeyDown={event=>{if(event.key==='Enter'||event.key==='Escape')setEditing(null);}}/>
     :<label htmlFor={id} className="dv-ob-name">{pick.name}</label>}
    <span className="dv-ob-path">{project?.path??''}</span>
    {/* With both sides showing, Move to is always in view on the name line, and Rename appears beside it on hover. */}
    {!only&&<span className="dv-ob-name-acts">{renameButton}{moveButton}</span>}
   </div>
   <p className="dv-ob-why" id={`${id}-why`}>{project?.reason??t('Brought back from the set-aside folders.')}</p>
   {project&&<p className="dv-ob-meta">{project.tools.map(tool=><span key={tool} className="dv-ob-tool">{toolsLabel([tool])}</span>)}
    {project.last&&<span>{t('last {when}',{when:ago(project.last)})}</span>}{project.branch&&<span>· {project.branch}</span>}{guess&&<span className="dv-ob-guess-row">{guess}</span>}</p>}
   {!pick.context&&<p className="dv-ob-pick" role="group" aria-label={t('Is {name} work or personal?',{name:pick.name})}>
    {(['work','personal'] as const).map(context=><button key={context} type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>move(pick.key,context)}
     aria-label={t('Put {name} under {side}',{name:pick.name,side:sideName(context)})}>{sideName(context)}</button>)}</p>}
   {only&&<span className="dv-ob-row-acts">{renameButton}{moveButton}</span>}
  </li>;
 };
 const runsOn=(who:string)=>{const [tool,...rest]=who.split(' · ');return <span className="dv-ob-runs">{tr('Runs on {who}',{who:<>{tool}{rest.length>0&&<> · <b>{rest.join(' · ')}</b></>}</>})}</span>;};
 const workRows=picks.filter(p=>p.context==='work');
 const leaveOut=()=>setPicks(rows=>rows.map(row=>row.context==='work'?{...row,ticked:false}:row));
 const signedOut=side.state==='signed_out'||side.state==='problem';
 const held=!workReady&&side.state!=='checking'&&workRows.some(p=>p.ticked)&&<div className="dv-ob-held-box" role="note"><StatusGlyph state="needs" accent/><div>
  <p>{signedOut?<><b>{t('Your work account, {identity}, needs signing in again.',{identity:side.profile?.identity??''})}</b> {t('Sign in, move any that are really yours to Personal, or leave them out.')}</>
   :<><b>{t('These need a work account.')}</b> {t('Add one now, move any that are really yours to Personal, or leave them out.')}</>} {t('Until then nothing in them is read or sent, and they never use your personal account.')}</p>
  {!plan.addWork&&<div className="dv-ob-acts">
   {work&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>setAdding(true)}>{signedOut?t('Sign in again'):t('Add a work account')}</button>}
   <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={leaveOut}>{t('Leave them out')}</button></div>}
 </div></div>;
 const workBody=adding&&work?<AddWorkAccount profiles={work.profiles} onChanged={work.onChanged} onCancel={()=>setAdding(false)} onVerified={profile=>{setAdded(profile);setAdding(false);}}/>
  :added&&workReady?<WorkAdded profile={work?.profiles.find(p=>p.id===side.profile?.id)??added} runs={workRows.filter(p=>p.ticked).map(p=>p.name)} onChange={()=>{setAdded(null);setAdding(true);}}/>
  :held;
 const groups:{context:Context|null;label:string}[]=only?[{context:'personal',label:t('Projects')}]
  :[{context:null,label:t('Work or personal?')},{context:'work',label:t('Work')},{context:'personal',label:t('Personal')}];
 const piles=survey?.set_aside??[];
 const asideFolders=piles.filter(pile=>FOLDER_PILES.has(pile.rule)).reduce((n,pile)=>n+pile.count,0);
 const asideThreads=piles.filter(pile=>!FOLDER_PILES.has(pile.rule)).reduce((n,pile)=>n+pile.count,0);
 // Everything ticked is Work and the work account's card is open: Bring in waits for it, and says so.
 const status=plan.addWork&&adding?t('Bring in once your work account is signed in.'):plan.status;
 return <section className="dv-ob" aria-label={t('Suggested projects')}>
  <h2 className="dv-display-2" ref={heading} tabIndex={-1}>{t('Here’s what Dervo would bring in')}</h2>
  <p className="dv-lede">{only?t('The ticked ones have the most going on. Each says why it’s here. Nothing’s created until you say so.')
   :t('The ticked ones have the most going on. Each says why it’s here and which account it would run on. Nothing’s created until you say so.')}</p>
  {!only&&picks.length>0&&<WhyLine/>}
  {groups.map(group=>{const rows=picks.filter(p=>p.context===group.context);if(!rows.length)return null;
   // Rows keep the place the look gave them: unticking one doesn't move it to Quieter under your pointer.
   const main=rows.filter(p=>p.restored?false:byKey.get(p.key)?.ticked),quiet=rows.filter(p=>!main.includes(p));
   const isWork=group.context==='work',mine=group.context==='personal';
   return <div key={group.context??'unsure'} className="dv-ob-group" role="group" aria-label={group.label} data-unsure={!group.context||undefined}>
    <div className="dv-ob-group-head"><h3 className="dv-title">{group.label}</h3>
     {isWork&&(workReady?runsOn(side.who):<span className="dv-ob-runs" data-held={side.state==='checking'?undefined:true}>{side.line}</span>)}
     {mine&&agent.ready&&runsOn(agent.who??agent.name)}
     {!group.context&&rows.length>1&&<span className="dv-ob-bulk">{t('Put all under')}{(['work','personal'] as const).map(context=>
      <button key={context} type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={()=>setPicks(all=>all.map(p=>p.context?p:{...p,context,picked:true}))}>{sideName(context)}</button>)}</span>}</div>
    <p className="dv-ob-guess">{groupGuess(group.context,rows.length,only)}</p>
    {isWork&&workBody}
    {main.length>0&&<ul className="dv-ob-list">{main.map(row)}</ul>}
    {quiet.length>0&&<><p className="dv-label dv-ob-quiet-head">{t('Quieter')}</p><ul className="dv-ob-list">{quiet.map(row)}</ul></>}
   </div>;})}
  {looseLine(survey?.loose_ends)&&<p className="dv-caption dv-ob-loose-line">{looseLine(survey?.loose_ends)}</p>}
  {piles.length>0&&<div className="dv-ob-aside">
   <p className="dv-caption">{t('{what} set aside, by rule.',{what:formatList([asideFolders?t('{count} folders',{count:asideFolders}):'',asideThreads?t('{count} threads',{count:asideThreads}):''].filter(Boolean))})}{' '}
    <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" aria-expanded={aside} onClick={()=>setAside(open=>!open)}>{aside?t('Hide them'):t('Show them')}</button></p>
   {aside&&<div className="dv-ob-piles-open">{(survey?.set_aside??[]).map(pile=><details key={pile.rule} className="dv-ob-pile">
    <summary><b>{pile.label}</b></summary>
    <ul>{pile.folders.map(folder=><li key={folder.path}><span className="dv-ob-path">{tildePath(folder.path)}</span><span className="dv-caption">{t('{count} conversations',{count:folder.conversations})}</span>
     {folder.key&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={picks.some(p=>p.key===folder.key)} onClick={()=>setPicks(rows=>restorePick(rows,folder))}>
      {picks.some(p=>p.key===folder.key)?t('In the list'):t('Bring back')}</button>}</li>)}
     {pile.more>0&&<li className="dv-caption">{t('and {count} more folders',{count:pile.more})}</li>}</ul>
   </details>)}</div>}
  </div>}
  {staleBox}{rescanLine}
  {error&&<p className="dv-err" role="alert">{error}</p>}
  <p className="dv-caption dv-ob-fine">{t('Nothing leaves this Mac and no model is called.')}</p>
  {/* Design review S5: Bring in, and why it's held, stay in view at the bottom of the window however long the list. */}
  <div className="dv-ob-bar">
   {status&&<p className="dv-caption dv-ob-held" id="dv-ob-held" role="status">{status}</p>}
   <div className="dv-ob-actions">
    {plan.aside&&<span className="dv-caption dv-ob-work-stays">{plan.aside}</span>}
    <button type="button" className="dv-btn dv-btn-quiet" onClick={onStartEmpty}>{t('Start empty')}</button>
    {plan.addWork&&work&&!adding?<button type="button" className="dv-btn dv-btn-primary" onClick={()=>setAdding(true)}>{signedOut?t('Sign in to your work account'):t('Add a work account')}</button>
     :<button type="button" className="dv-btn dv-btn-primary" disabled={!plan.go} aria-describedby={!plan.go&&status?'dv-ob-held':undefined}
      onClick={()=>setStage('threads')}>{bringLabel(plan.now.length)}</button>}
   </div>
  </div>
 </section>;
}
