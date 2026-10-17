/* One run's record (agent history, first slice): why it ran, what Dervo saw it check beside what it said, and what it
 * did, each step it reported over the calls Dervo saw under it. On the right: its state once, the runs it's linked to,
 * what it was told and the files it changed. Built toward reports/review-20260923/agent-history/b-run-record.html.
 *
 * The rule that makes it Dervo's: a line is either built from the record or it sits in quotes under "What it said".
 * The steps are the agent's words too, and the line above them says so. Approving stays in the thread, where the diff
 * and the decision bar are, so this page sends you there rather than holding a second Approve. */
import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {StatusGlyph} from './StatusGlyph';
import {Coded,HistoryByline,Quoted,WhoMark,answerFrom,type HistoryActions,type HistoryScope} from './HistoryParts';
import {checkLine,clock,ownLine,runRecord,span,took,type RecordStep,type RunRecord} from './runHistory';
import './RunRecord.css';
import {t,tr} from '@/i18n';

const CALLS_SHOWN=8;
const OUT=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5.5 10.5l5-5M6.5 5.5h4v4"/></svg>;
const MORE=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;

const size=(bytes:number|null)=>bytes===null?'':bytes<1024?t('{bytes} bytes',{bytes}):`${(bytes/1024).toFixed(1).replace(/\.0$/,'')} KB`;
const delta=(added:number|null,removed:number|null)=>[added?`+${added}`:'',removed?`−${removed}`:''].filter(Boolean).join(' ');

function Step({step}:{step:RecordStep}){
 const [all,setAll]=useState(false);
 const calls=all?step.calls:step.calls.slice(0,CALLS_SHOWN);
 return <li>
  <time className="tnum">{step.at?clock(step.at).replace(/\s?[AaPp]\.?[Mm]\.?$/,''):''}</time>
  <div>
   {step.text?<p className="dervo-record-step">{step.text}</p>:<p className="dervo-record-step is-before">{t('Before its first step')}</p>}
   {step.why&&<p className="dervo-record-why">{t('Why, it said:')} <q>{step.why}</q></p>}
   {calls.length>0&&<ul className="dervo-record-calls">{calls.map((call,index)=><li key={index} data-failed={call.failed||undefined}>
    <span className="tool">{call.tool}</span><span className="target" title={call.target}>{call.target}</span><span className="out">{call.out}</span></li>)}</ul>}
   {step.calls.length>CALLS_SHOWN&&!all&&<button type="button" className="dv-btn dv-btn-quiet dervo-record-more" onClick={()=>setAll(true)}>{t('{count} more calls',{count:step.calls.length-CALLS_SHOWN})}</button>}
  </div>
 </li>;
}

function Told({told,onRecord}:{told:RunRecord['told'];onRecord:(taskId:string)=>void}){
 if(!told.recorded)return <p className="dervo-record-none">{t('Not recorded for this run. It ran before Dervo kept what each run was told.')}</p>;
 const items=[
  ...told.repository.map(file=>({key:`r:${file.path}`,text:file.path,note:[t('From the repository'),file.bytes!==null?size(file.bytes):'',file.cut?t('cut to fit'):''].filter(Boolean).join(', ')})),
  ...(told.rules?[{key:'rules',text:t('{rules} of your rules',{rules:told.rules}),note:t('From Knowledge')}]:[]),
  ...told.notes.map((note,index)=>({key:`n:${index}`,text:note,note:t('A note an earlier run saved')})),
  ...told.skills.map((skill,index)=>({key:`s:${index}`,text:skill,note:t('A skill')})),
  ...told.recent.map((line,index)=>({key:`w:${index}`,text:line,note:t('Recent work, from Dervo’s records')})),
 ];
 return <div className="dervo-record-told">
  {told.earlier&&<p><span>{t('What happened earlier in this thread')}{told.earlier.name&&told.earlier.taskId?<>{t(', after')} <button type="button" className="dervo-history-mark" onClick={()=>onRecord(told.earlier!.taskId!)}>{told.earlier.name}</button></>:''}</span>
   <small>{told.earlier.state==='continues'?t('The same session carried on'):t('A fresh session, told a summary')}{told.earlier.reason?`. ${told.earlier.reason.replace(/\.$/,'')}`:''}</small></p>}
  {items.map(item=><p key={item.key}><span>{item.text}</span><small>{item.note}</small></p>)}
  {!items.length&&!told.earlier&&<p className="dervo-record-none">{t('Nothing beyond your request.')}</p>}
 </div>;
}

export function RunRecordPage({scope,taskId,runId,actions}:{scope:HistoryScope;taskId:string;runId?:string;actions:HistoryActions}){
 const record=useQuery({queryKey:['run-record',scope.projectId,scope.profileId,taskId,runId??''],queryFn:()=>runRecord(scope,taskId,runId??'')});
 const [homeShown,setHomeShown]=useState(false);
 if(record.isError)return <div className="dv-column dervo-record-column"><div className="dv-measure"><p role="alert" className="dervo-history-error">
  {t('This record couldn’t load. {why}',{why:record.error instanceof Error?record.error.message:String(record.error)})}{' '}
  <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void record.refetch()}>{t('Try again')}</button></p></div></div>;
 if(!record.data)return <div className="dv-column dervo-record-column"><div className="dv-measure"><p role="status" className="dv-caption">{t('Reading the record…')}</p></div></div>;
 const run=record.data;
 const accent=run.glyph==='needs';
 const when=[t('Run {runIndex} of {runCount}',{runIndex:run.runIndex,runCount:run.runCount}),span(run.startedAt,run.endedAt),run.endedAt?took(run.startedAt,run.endedAt):t('still running')].filter(Boolean).join(' · ');
 const said=run.said;
 const continued=run.links.some(link=>link.kind==='continued_by');
 const openLink=(link:{taskId?:string})=>{if(link.taskId)actions.onRecord(link.taskId);};
 const origin=run.why.origin;
 return <div className="dervo-record-body" data-testid="run-record">
  <div className="dv-column dervo-record-column"><div className="dv-measure">
   <header className="dervo-record-head">
    <span className="dv-caption tnum">{when}</span>
    <h1 className="dv-display-2" tabIndex={-1}>{run.name}</h1>
    <HistoryByline who={{...run,account:run.account?/account$/i.test(run.account)?run.account:t('{name} account',{name:run.account}):null}}/>
   </header>

   <section aria-labelledby="record-why">
    <h2 id="record-why" className="dv-label">{t('Why it ran')}</h2>
    <dl className="dervo-record-asked">
     {/* Where it came from when it wasn't your message here: a proposal, your answer to a question, or Home with no
         message of yours on record. The agent's words sit under "It said", quoted, never beside "You". */}
     {origin&&<div><dt>{t('Started')}</dt><dd>{origin.line}{origin.kind==='proposal'&&origin.name&&origin.taskId?<> {tr('From {name}.',{name:<button type="button" className="dervo-history-mark" onClick={()=>actions.onRecord(origin.taskId!)}>{origin.name}</button>})}</>:null}</dd></div>}
     {run.why.you?.text&&run.why.you.where!=='answer'&&<div><dt>{t('You')}</dt><dd><q>{run.why.you.text}</q>{run.why.you.where==='home'?` ${run.why.you.at?t('On Home, {time}.',{time:clock(run.why.you.at)}):t('On Home.')}`:run.why.you.at?` ${clock(run.why.you.at)}.`:''}</dd></div>}
     {run.why.you?.text&&run.why.you.where==='answer'&&<div><dt>{t('You')}</dt><dd><q>{run.why.you.text}</q> {answerFrom(run.why.you.picked)}{run.why.you.at?`, ${clock(run.why.you.at)}`:''}.</dd></div>}
     {origin?.quote&&<div><dt>{origin.kind==='home'?t('Home wrote'):t('It said')}</dt><dd className="dervo-record-quote"><Quoted said={{text:origin.quote,from:origin.kind==='home'?undefined:origin.quoteFrom??undefined}}/></dd></div>}
     {run.why.home&&<div><dt>{t('Home')}</dt><dd>{t('Started this thread from your message and wrote its own instruction for it.')}</dd></div>}
     {run.why.steers.map((steer,index)=>steer.text&&(steer.question
      ?<div key={index} className="dervo-record-pair"><dt>{t('Then')}</dt><dd>{steer.picked?tr('You answered its question with one of its options: {text}{at}, while it worked.',{text:<q>{steer.text}</q>,at:steer.at?` ${t('at {time}',{time:clock(steer.at)})}`:''})
        :tr('You answered its question: {text}{at}, while it worked.',{text:<q>{steer.text}</q>,at:steer.at?` ${t('at {time}',{time:clock(steer.at)})}`:''})}</dd>
       <dt>{t('It said')}</dt><dd className="dervo-record-quote"><Quoted said={{text:steer.question,from:t('Its question')}}/></dd></div>
      :<div key={index}><dt>{t('Then')}</dt><dd>{tr('You added {text}{at}, while it worked.',{text:<q>{steer.text}</q>,at:steer.at?` ${t('at {time}',{time:clock(steer.at)})}`:''})}</dd></div>))}
     {!run.why.you?.text&&!run.why.home&&!origin&&<div><dt>{t('You')}</dt><dd>{t('The record doesn’t hold the request for this run.')}</dd></div>}
    </dl>
    {run.why.home?.text&&<button type="button" className="dv-btn dv-btn-quiet dervo-record-show" aria-expanded={homeShown} onClick={()=>setHomeShown(value=>!value)}>{homeShown?t('Hide what Home wrote'):t('Show what Home wrote')}{MORE}</button>}
    {homeShown&&run.why.home?.text&&<blockquote className="dervo-record-home"><span className="dv-label">{t('Home wrote')}</span><p>{run.why.home.text}</p></blockquote>}
   </section>

   <section aria-labelledby="record-checked">
    <h2 id="record-checked" className="dv-label">{t('Checked')}</h2>
    <div className="dervo-record-compare">
     <div>
      <h3>{t('What Dervo saw')}</h3>
      <ul>
       {run.checks.dervo.map((check,index)=><li key={`d${index}`}><Coded text={checkLine(check)}/></li>)}
       {run.checks.own.map((own,index)=><li key={`o${index}`}><Coded text={ownLine(own)}/></li>)}
       {run.checks.note&&<li>{run.checks.note}</li>}
       {!run.checks.dervo.length&&!run.checks.own.length&&!run.checks.note&&<li>{t('No check ran, and it changed no files.')}</li>}
      </ul>
     </div>
     <div className="dervo-record-said">
      <h3>{t('What it said')}</h3>
      <ul>{said.length?said.map((item,index)=><li key={index}><q>{item.text}</q></li>):<li className="dervo-record-none">{t('Nothing about its checks.')}</li>}</ul>
     </div>
    </div>
   </section>

   <section aria-labelledby="record-did">
    <h2 id="record-did" className="dv-label">{t('What it did')}</h2>
    {run.steps.length?<>
     <p className="dervo-record-intro">{t('The bold lines are the steps it reported. Under each is what Dervo saw it do.')}</p>
     <ol className="dervo-record-trail">{run.steps.map((step,index)=><Step key={index} step={step}/>)}</ol>
    </>:<p className="dervo-record-none">{t('Dervo saw no tool calls in this run.')}</p>}
   </section>
  </div></div>

  <aside className="dv-panel dervo-record-panel" aria-label={t('This run')}>
   <section>
    <div className="dervo-record-state"><StatusGlyph state={run.glyph} accent={accent}/><span className="dv-state" data-accent={accent||undefined} data-state={run.glyph==='running'||run.glyph==='done'?run.glyph:undefined} role="status">{run.ended}</span></div>
    <div className="dervo-record-actions">
     {run.canReview?<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>actions.onOpenTask(run.channelId,run.taskId)}>{t('Review in the thread')}</button>
      :<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>actions.onOpenTask(run.channelId,run.taskId)}>{t('Open the thread')}</button>}
    </div>
    {run.commit&&<p className="dervo-record-commit">{tr('Committed as {sha}{at}{undone}.',{sha:<code>{run.commit.short}</code>,at:run.commit.at?` ${t('at {time}',{time:clock(run.commit.at)})}`:'',undone:run.commit.reverted?t(', then undone'):''})}</p>}
   </section>

   <section aria-labelledby="record-linked">
    <h2 id="record-linked" className="dv-label">{t('Linked')}</h2>
    <ul className="dervo-record-links">
     {run.links.map((link,index)=><li key={index}>
      <WhoMark provider={link.provider} home={link.kind==='home'}/>
      <span><b>{link.name?<LinkName name={link.name} taskId={link.taskId} onOpen={openLink}/>:linkTitle(link.kind)}</b>
       <small>{link.at?`${clock(link.at)} · `:''}{link.text}</small></span>
     </li>)}
     {!continued&&run.runIndex===run.runCount&&<li className="dervo-record-none">{t('Nothing has continued from this run yet.')}</li>}
    </ul>
   </section>

   <section aria-labelledby="record-told">
    <h2 id="record-told" className="dv-label">{t('What it was told')}</h2>
    <Told told={run.told} onRecord={taskId=>actions.onRecord(taskId)}/>
   </section>

   {run.files.length>0&&<section aria-labelledby="record-files">
    <h2 id="record-files" className="dv-label">{t('Changed files')}</h2>
    <ul className="dv-files">{run.files.map(file=><li key={file.path} className="dv-file">
     <button type="button" className="dervo-record-file" title={t('Trace {path}',{path:file.path})} onClick={()=>actions.onTrace(file.path)}>{file.path.split('/').pop()}</button>
     <span className="dv-delta">{delta(file.added,file.removed)}</span></li>)}</ul>
   </section>}

   {run.files.length>0&&<button type="button" className="dv-btn dv-btn-quiet dervo-record-foot" onClick={()=>actions.onTrace(run.files[0].path)}>{t('Trace a file')}{OUT}</button>}
  </aside>
 </div>;
}

function LinkName({name,taskId,onOpen}:{name:string;taskId?:string;onOpen:(link:{taskId?:string})=>void}){
 return taskId?<button type="button" className="dervo-history-mark" onClick={()=>onOpen({taskId})}>{name}</button>:<>{name}</>;
}
function linkTitle(kind:string):string{
 return kind==='correction'?t('Your correction'):kind==='review'?t('Independent review'):t('Linked');
}
