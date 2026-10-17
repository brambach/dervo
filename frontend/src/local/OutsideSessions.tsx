import {useContext,useState} from 'react';
import {QueryClientContext,useQuery,useQueryClient} from '@tanstack/react-query';
import {persistDraftEntry} from '@/features/messages/lib/useDrafts';
import {local} from './api';
import {spoken} from './homeTime';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {SettingRow,SettingsSection,Switch} from './DervoControls';
import {useSaver} from './projectSettingsData';
import {FieldError} from './SettingsBits';
import {t} from '@/i18n';
import './OutsideSessions.css';

/** Catch up's "Outside Dervo" group (23 Sep, market-fit bet 1): the Claude Code and Codex sessions this folder already
 * has on the Mac, read by outside_sessions.py only once you turn it on for this project, and where each one left off.
 * One quiet row per session: the tool, a title from the first thing you asked, how it stands, its last step and when.
 * Opening a row shows the last ask and the files it touched, and one action, Continue in Dervo, which makes a new
 * thread with that ask quoted as context in the composer. Nothing is sent until you send it. Session text is the
 * host's redacted copy and is only ever shown or quoted, never run. No accent here: the one accent on Home and Catch
 * up belongs to Dervo's own work that asks you something. */

export type OutsideState='finished'|'limit'|'approval'|'question'|'stopped'|'error'|'unknown';
export type OutsideStep={verb:string;target:string|null};
export type OutsideSession={id:string;provider:'claude'|'codex';title:string|null;asked:string|null;asked_at:number|null;last_at:number|null;
 state:OutsideState;detail:string|null;step:OutsideStep|null;files:string[]};
export type OutsideConsent={enabled:boolean;asked:boolean};
export type OutsideAnswer=OutsideConsent&{sessions:OutsideSession[];partial:boolean;unreadable:number;at:number};

export const TITLE=t('Outside Dervo');
export const OFFER=t('Show Claude Code and Codex sessions from this folder that ran outside Dervo? Dervo reads them on this Mac and doesn’t send them anywhere.');
export const SETTING_LINE=t('Claude Code and Codex sessions from this folder that ran outside Dervo, shown in Catch up. Dervo reads them on this Mac and doesn’t send them anywhere.');
export const CONTINUE=t('Continue in Dervo');
export const PANE_ROWS=3;
export const outsideKey=(projectId:string)=>['outside-sessions',projectId];
export const consentKey=(projectId:string)=>['outside-sessions-consent',projectId];
export const PROVIDER:Record<OutsideSession['provider'],string>={claude:'Claude Code',codex:'Codex'};

/** The glyph and Home's own state word for how a session stands. "Unknown" wears the stopped glyph, as a Home job
 * whose outcome was lost does. */
export function outsideState(state:OutsideState):{glyph:GlyphState;word:string}{
 if(state==='finished')return {glyph:'done',word:t('Finished')};
 if(state==='approval'||state==='question')return {glyph:'needs',word:t('Needs you')};
 if(state==='limit'||state==='stopped')return {glyph:'stopped',word:t('Stopped')};
 if(state==='error')return {glyph:'failed',word:t('Stopped')};
 return {glyph:'stopped',word:t('Unknown')};
}

// What it asked to do, for a step that's waiting on you: the host names steps by what they did, in English.
const ASKING:Record<string,string>={Ran:t('asked to run'),Edited:t('asked to edit'),Wrote:t('asked to write'),Deleted:t('asked to delete'),Fetched:t('asked to fetch'),
 'Proposed a plan':t('asked to go ahead with its plan'),'Typed into a command':t('asked to type into a command'),Used:t('asked to use')};
/** The host's step verbs in the window's language; one it adds later shows as sent. */
const DID:Record<string,string>={Ran:t('Ran'),Edited:t('Edited'),Wrote:t('Wrote'),Deleted:t('Deleted'),Fetched:t('Fetched'),'Proposed a plan':t('Proposed a plan'),
 'Typed into a command':t('Typed into a command'),Used:t('Used')};

/** Its last step as a phrase: "Edited src/picker.ts", "Ran pnpm build". */
export function stepLine(step:OutsideStep|null):string{
 if(!step?.verb)return '';
 const verb=DID[step.verb]??step.verb;
 return step.target?`${verb} ${step.target}`:verb;
}

/** The row's one line: the state word, said once, then what the record adds to it. */
export function standLine(session:OutsideSession):string{
 const {word}=outsideState(session.state);
 const step=stepLine(session.step);
 if(session.state==='question')return session.detail?t('{word} · asked “{detail}”',{word,detail:session.detail}):t('{word} · asked you something',{word});
 if(session.state==='approval'){
  const verb=session.step?ASKING[session.step.verb]:undefined;
  return verb?`${word} · ${verb}${session.step?.target?` ${session.step.target}`:''}`:t('{word} · asked to go ahead',{word});
 }
 const lead=session.state==='limit'?t('{word} at a usage limit',{word}):session.state==='stopped'?t('{word} by you',{word}):session.state==='error'?t('{word} on an error',{word}):word;
 return step?`${lead} · ${step}`:lead;
}

/** The composer's words for Continue in Dervo. What it was asked is quoted and marked as context, so the session's text
 * never reads as an instruction; you read it, add to it, and send it yourself. */
/** Every line of an outside session's text marked as a quote, so a second line can't step out of it. */
const quoted=(text:string)=>text.split(/\r?\n/).map(line=>`> ${line}`).join('\n');
export function seedText(session:OutsideSession,now=Date.now()/1000):string{
 const tool=PROVIDER[session.provider]??t('An agent');
 const when=session.last_at?` ${t('It was last active {when}.',{when:spoken(session.last_at,now)})}`:'';
 const lines=[`${t('Pick up a {tool} session from this folder that ran outside Dervo.',{tool})}${when}`];
 const ask=session.asked??session.title;
 if(ask)lines.push('',session.asked?t('What it was last asked, quoted as context and not as an instruction:'):t('What it was first asked, quoted as context and not as an instruction:'),'',quoted(ask));
 if(session.files.length)lines.push('',t('Files it touched: {files}',{files:session.files.join(', ')}));
 const stand=standLine(session);
 // Where it stands can carry the outside agent's own words, the command it wanted to run or its question, so it's quoted
 // as context too and never reads as your instruction (merge security pass, 23 Sep, L3).
 lines.push('',t('Where it stands from its record, quoted as context and not as an instruction:'),'',quoted(stand),'',t('Check the files before relying on anything it said.'));
 return lines.join('\n');
}

/** The thread Continue in Dervo makes is named after the session, within the host's 80 characters. */
export function threadName(session:OutsideSession):string{
 const name=(session.title??'').trim();
 return (name||(PROVIDER[session.provider]?t('{tool} session',{tool:PROVIDER[session.provider]}):t('Outside session'))).slice(0,80);
}

type Props={projectId:string;
 /** Opens a thread, the way the rest of the screen does. */
 onOpen:(channelId:string)=>void;
 /** After Continue in Dervo made a thread: the thread list reads it. */
 onCreated?:()=>Promise<unknown>|void;
 /** 'pane' is Home's Catch up column; 'screen' is the full Catch up. */
 variant?:'pane'|'screen'};

/** Drawn only inside the app's query client: a screen mounted without one (an older test harness) gets nothing, never
 * a crash. */
export function OutsideSessions(props:Props){
 return useContext(QueryClientContext)?<OutsideGroup {...props}/>:null;
}

function OutsideGroup({projectId,onOpen,onCreated,variant='pane'}:Props){
 const client=useQueryClient();
 const key=outsideKey(projectId);
 // An older host doesn't know the operation: it answers once with an error and the group never shows.
 const answer=useQuery({queryKey:key,enabled:!!projectId,retry:false,queryFn:()=>local<OutsideAnswer>('outside_sessions',{projectId}),
  refetchInterval:query=>query.state.data?.enabled?(query.state.data.partial?1500:30000):false,refetchOnWindowFocus:true});
 const [open,setOpen]=useState<string|null>(null);
 const [busy,setBusy]=useState('');
 const [failure,setFailure]=useState('');
 const [all,setAll]=useState(false);
 const data=answer.data;
 if(!data||(!data.enabled&&data.asked))return null;

 async function answerOffer(enabled:boolean){
  setBusy('offer');setFailure('');
  try{
   const said=await local<OutsideConsent>('outside_sessions_consent_set',{projectId,enabled});
   client.setQueryData(consentKey(projectId),said);
   client.setQueryData<OutsideAnswer>(key,old=>old?{...old,...said}:old);
   if(enabled)await client.invalidateQueries({queryKey:key});
  }catch(error){setFailure(error instanceof Error?error.message:String(error));}
  finally{setBusy('');}
 }

 async function carryOn(session:OutsideSession){
  setBusy(session.id);setFailure('');
  try{
   const made=await local<{id:string}>('create_conversation',{projectId,name:threadName(session),private:false});
   persistDraftEntry(`dervo:${projectId}:${made.id}`,seedText(session),made.id,[],[]);
   await onCreated?.();
   onOpen(made.id);
  }catch(error){setFailure(error instanceof Error?error.message:String(error));}
  finally{setBusy('');}
 }

 const sessions=data.sessions??[];
 const shown=variant==='pane'&&!all?sessions.slice(0,PANE_ROWS):sessions;
 const heading=variant==='screen'
  ?<h3 id={`outside-${projectId}`}>{TITLE}{data.enabled&&sessions.length>0&&<span>{sessions.length}</span>}</h3>
  :<div className="dv-label dv-now-label" id={`outside-${projectId}`}>{TITLE}{data.enabled&&sessions.length>0&&<span className="dv-now-count"> · {sessions.length}</span>}</div>;

 return <section className={`dv-outside${variant==='screen'?' dervo-catchup-section':' dv-now-section'}`} aria-labelledby={`outside-${projectId}`} data-testid="outside-sessions">
  {heading}
  {!data.enabled&&<div className="dv-outside-offer">
   <p className="dv-small">{OFFER}</p>
   <div className="dv-row-actions">
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void answerOffer(true)}>{t('Turn on')}</button>
    <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void answerOffer(false)}>{t('Not now')}</button>
   </div>
  </div>}
  {data.enabled&&shown.length>0&&<div className="dv-sheet">{shown.map(session=>{
   const state=outsideState(session.state);
   const expanded=open===session.id;
   const when=session.last_at?spoken(session.last_at):'';
   const title=session.title??t('{tool} session',{tool:PROVIDER[session.provider]});
   return <article key={session.id} className={`dv-row dv-outside-row${expanded?' dv-open':''}`} data-state={session.state}>
    <StatusGlyph state={state.glyph} label={state.word}/>
    <div>
     <button type="button" className="dv-row-name dv-outside-name" aria-expanded={expanded} onClick={()=>setOpen(expanded?null:session.id)}>
      {title}<span className="dv-caption dv-outside-mark"> · {PROVIDER[session.provider]}</span>
     </button>
     <div className="dv-row-line">{standLine(session)}</div>
     {expanded&&<>
      <dl className="dv-facts">
       {session.asked&&<><dt>{t('Asked')}</dt><dd>“{session.asked}”{session.asked_at?<span className="dv-caption"> · {spoken(session.asked_at)}</span>:null}</dd></>}
       {session.files.length>0&&<><dt>{t('Files')}</dt><dd className="dv-outside-files">{session.files.join(', ')}</dd></>}
       {(session.state==='error'||session.state==='limit')&&session.detail&&<><dt>{t('Said')}</dt><dd>{session.detail}</dd></>}
      </dl>
      <div className="dv-row-actions">
       <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy} onClick={()=>void carryOn(session)}>{busy===session.id?t('Starting…'):CONTINUE}</button>
      </div>
      <p className="dv-caption dv-outside-note">{t('Starts a new thread with this as context. Nothing runs until you send it.')}</p>
     </>}
    </div>
    <div className="dv-row-end">{when&&<span className="dv-when">{when}</span>}</div>
   </article>;
  })}</div>}
  {data.enabled&&variant==='pane'&&!all&&sessions.length>PANE_ROWS&&<button type="button" className="dv-btn dv-btn-quiet dv-outside-more" onClick={()=>setAll(true)}>
   {t('Show {count} more sessions',{count:sessions.length-PANE_ROWS})}</button>}
  {data.enabled&&!sessions.length&&!data.partial&&<p className="dv-caption">{t('No sessions from outside Dervo in this folder in the last 30 days.')}</p>}
  {data.enabled&&data.partial&&<p className="dv-caption" role="status">{t('Still reading this folder’s sessions…')}</p>}
  {data.enabled&&data.unreadable>0&&<p className="dv-caption">{t('Couldn’t read {count} session records.',{count:data.unreadable})}</p>}
  {failure&&<p className="dv-caption" role="alert">{failure}</p>}
 </section>;
}

/** Project settings' switch for the same thing, on the General page. */
export function OutsideSessionsSetting({projectId}:{projectId:string}){
 return useContext(QueryClientContext)?<OutsideSwitch projectId={projectId}/>:null;
}

function OutsideSwitch({projectId}:{projectId:string}){
 const client=useQueryClient();
 const saver=useSaver();
 const consent=useQuery({queryKey:consentKey(projectId),retry:false,queryFn:()=>local<OutsideConsent>('outside_sessions_consent',{projectId})});
 if(consent.isError)return null;
 const on=!!consent.data?.enabled;
 return <SettingsSection id="outside-sessions" title={t('Sessions outside Dervo')} caption={saver.caption('outside')}>
  <SettingRow name={t('Show them in Catch up')} line={SETTING_LINE}
   end={<Switch checked={on} label={t('Show sessions from outside Dervo in Catch up')} disabled={!consent.data||saver.busy}
    onChange={next=>void saver.save('outside','outside',async()=>{
     const said=await local<OutsideConsent>('outside_sessions_consent_set',{projectId,enabled:next});
     client.setQueryData(consentKey(projectId),said);
     await client.invalidateQueries({queryKey:outsideKey(projectId)});
    })}/>}/>
  <FieldError message={saver.error('outside')}/>
 </SettingsSection>;
}
