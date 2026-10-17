import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {toast} from 'sonner';
import {local} from './api';
import {SettingRow,SettingsSection,Switch} from './DervoControls';
import {Disclosure} from './SettingsKit';
import type {UsageSharing} from './usage';
import {formatDate,t} from '@/i18n';

/** Usage sharing: the switch on Preferences, the one-time notice, and the admins' Usage, People and Errors sections on Testers.
 * What's sent is decided by the host's allowlists (backend/usage_events.py, backend/error_reports.py); this copy says the
 * same thing in words. */
export const sharingQuery={queryKey:['usage-sharing'] as const,queryFn:()=>local<UsageSharing>('usage_sharing')};
export const SHARING_NAME=t('Share usage and error reports with Dervo');
export const SHARING_LINE=t('Which screens you open, how your tasks end and where Dervo hits an error, so the beta team can see what works and fix what breaks. Never your prompts, code, files, names or messages.');
export const NOTICE_LINE=t('Dervo shares which screens you open, how tasks end and where its errors happen with the beta team, never what’s in them.');
/** Everything that's sent, as the disclosure lists it. Kept in step with EVENTS in backend/usage_events.py. */
export const SENT:[string,string][]=[
  [t('With every event'),t('Its name, the Dervo version, a random ID made for this Mac, and your Dervo account.')],
  [t('Where you go'),t('Dervo opening or updating, and the screen you open: Home, a thread, Knowledge, Search, Catch up or a Settings page.')],
  [t('First run'),t('Which steps you reach and finish: sign in, approval, folder, Start, project created.')],
  [t('Models'),t('The model picker opening, and an account or model change: Claude or Codex, the model family, subscription or API key.')],
  [t('Work'),t('A thread created. A Home request and how many threads it started. A task starting and ending: provider, model family, how long it took, whether it completed, failed or stopped, and a failure code such as “usage limit”.')],
  [t('Everything else'),t('Continue in the other provider, a Knowledge rule added, a lesson kept or dismissed, feedback opened.')],
  [t('Errors'),t('When something breaks: the kind of error, such as “KeyError”, the file, function and line in Dervo’s own code where it happened, the screen or step that was running, Claude or Codex for a task, and how many times it happened.')],
  [t('Never'),t('Prompts, messages, replies, your file names and paths, project, thread or repo names, emails, error messages or keys.')],
];

export function UsageSharingSettings(){
  const client=useQueryClient();
  const sharing=useQuery(sharingQuery);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function change(enabled:boolean){
    if(busy)return;
    setBusy(true);setError('');
    try{client.setQueryData(sharingQuery.queryKey,await local<UsageSharing>('usage_sharing_set',{enabled}));}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy(false);}
  }
  return <SettingsSection title={t('Usage sharing')} id="dv-pref-usage">
    <div data-testid="usage-sharing" style={{display:'contents'}}>
      <SettingRow name={SHARING_NAME} line={SHARING_LINE}
        end={<Switch label={SHARING_NAME} checked={sharing.data?.enabled===true} disabled={!sharing.data||busy} onChange={next=>void change(next)}/>}
        below={<Disclosure summary={t('Everything that’s sent')}><dl className="dv-rules">{SENT.map(([name,line])=><div key={name} style={{display:'contents'}}><dt>{name}</dt><dd>{line}</dd></div>)}</dl>
          <p className="dv-set-line" style={{marginTop:'var(--dv-s2)'}}>{t('It goes to Dervo’s beta server, the one you sign in with. Turning this off stops it at once: nothing more is recorded, and nothing waiting is sent.')}</p></Disclosure>}/>
      {sharing.isError&&<p role="alert" className="dv-field-note">{t('Dervo couldn’t read this setting. Reopen Preferences to try again.')}</p>}
      {error&&<p role="alert" className="dv-field-note">{error}</p>}
    </div>
  </SettingsSection>;
}

/** The notice, once: in first run for a new install, or on the first launch of an update. It's marked seen as it shows,
 * and stays until you close it. `hold` keeps it back while the welcome (Welcome.tsx) is up, so the first screen a person
 * sees isn't a consent card over it; it shows on the form that follows. */
export function useUsageNotice(onPreferences:()=>void,hold=false){
  const client=useQueryClient();
  const sharing=useQuery(sharingQuery);
  const shown=useRef(false),open=useRef(onPreferences);
  open.current=onPreferences;
  useEffect(()=>{
    if(hold||shown.current||!sharing.data?.notice||!sharing.data.enabled)return;
    shown.current=true;
    toast(NOTICE_LINE,{id:'dervo-usage-notice',duration:Infinity,action:{label:t('Preferences'),onClick:()=>open.current()},cancel:{label:'OK',onClick:()=>{}}});
    void local<UsageSharing>('usage_notice_seen').then(next=>client.setQueryData(sharingQuery.queryKey,next)).catch(()=>{});
  },[hold,sharing.data?.notice,sharing.data?.enabled]);
}

// ---------------------------------------------------------------------------------------------- Testers page (admins)
export type UsageReport={since:number;days:number;active:number;daily:{day:string;users:number;events:number}[];
  events:{day:string;event:string;users:number;count:number}[];screens:{day:string;screen:string;users:number;count:number}[];
  funnel:{step:string;reached:number;completed:number}[];tasks:{provider:string;outcome:string;count:number;users:number;median_seconds:number|null}[]};
type ReportState={state:'loading'|'ready'|'error';report:UsageReport|null};
export const reportQuery={queryKey:['usage-report'] as const,queryFn:()=>local<ReportState>('usage_report',{reload:false}),
  refetchInterval:(query:{state:{data?:ReportState}})=>query.state.data?.state==='loading'?400:false};

const SETTINGS_LABELS:Record<string,string>={model:t('Account and model'),access:t('Access'),checks:t('Checks and review'),helpers:t('Helper agents'),
  general:t('General'),account:t('Account'),preferences:t('Preferences'),accounts:t('Connected accounts'),updates:t('Updates'),testers:t('Testers')};
const SCREEN_LABELS:Record<string,string>={home:t('Home'),thread:t('A thread'),knowledge:t('Knowledge'),search:t('Search'),catch_up:t('Catch up'),releases:t('Releases')};
export function screenLabel(screen:string){
  if(screen.startsWith('settings.'))return `Settings, ${SETTINGS_LABELS[screen.slice(9)]??screen.slice(9)}`;
  return SCREEN_LABELS[screen]??screen;
}
const STEP_LABELS:Record<string,string>={sign_in:t('Sign in'),pending:t('Approval'),folder:t('Folder'),start:t('Start'),project_created:t('Project created')};
/** One first-run row. Approval only has the people who had to wait for it: a tester approved at sign-in never passes through it. */
export function funnelLine(row:{step:string;reached:number;completed:number}){
  if(row.step==='project_created')return t('{completed} created',{completed:row.completed});
  if(row.step==='pending')return t('{reached} waited · {completed} approved',{reached:row.reached,completed:row.completed});
  return t('{reached} reached · {completed} finished',{reached:row.reached,completed:row.completed});
}
const PROVIDERS:Record<string,string>={claude:'Claude',codex:'Codex'};
const plural=(n:number,one:string,many:string)=>`${n} ${n===1?one:many}`;
/** The report's lists read at the disclosure's size, with room for a Settings page's name. */
const LIST={fontSize:'var(--dv-fs-small)',gridTemplateColumns:'184px minmax(0, 1fr)'};

/** Views per screen across the window, most first. */
export function topScreens(report:UsageReport,limit=6){
  const totals=new Map<string,number>();
  for(const row of report.screens)totals.set(row.screen,(totals.get(row.screen)??0)+row.count);
  return [...totals].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,limit).map(([screen,count])=>({screen,label:screenLabel(screen),count}));
}

export function duration(seconds:number){
  if(seconds<60)return `${seconds} s`;
  const minutes=Math.floor(seconds/60),rest=seconds%60;
  if(minutes<60)return rest?t('{minutes} min {rest} s',{minutes,rest}):t('{minutes} min',{minutes});
  return `${Math.floor(minutes/60)} h ${minutes%60} min`;
}

/** One line per provider: how its tasks ended, and how long a completed one usually took. */
export function taskLines(report:UsageReport){
  const providers=[...new Set(report.tasks.map(row=>row.provider))].filter(p=>p in PROVIDERS).sort();
  return providers.map(provider=>{
    const of=(outcome:string)=>report.tasks.find(row=>row.provider===provider&&row.outcome===outcome);
    const counts=['completed','failed','stopped'].map(outcome=>`${of(outcome)?.count??0} ${outcome}`).join(' · ');
    const median=of('completed')?.median_seconds;
    return {provider,label:PROVIDERS[provider],line:median!=null?`${counts}. Median ${duration(median)} to complete.`:counts};
  });
}

/** Active testers by day, newest first: "Thu 3 · Wed 2". Days are UTC, as the server counts them. */
export function dayLine(report:UsageReport){
  return [...report.daily].sort((a,b)=>b.day.localeCompare(a.day))
    .map(row=>`${new Date(`${row.day}T00:00:00Z`).toLocaleDateString('en',{weekday:'short',timeZone:'UTC'})} ${row.users}`).join(' · ');
}

export function TestersUsage(){
  const client=useQueryClient();
  const report=useQuery(reportQuery);
  const data=report.data;
  async function reload(){
    try{client.setQueryData(reportQuery.queryKey,await local<ReportState>('usage_report',{reload:true}));}
    catch{client.setQueryData(reportQuery.queryKey,{state:'error',report:data?.report??null});}
  }
  const failed=report.isError||data?.state==='error';
  const shown=data?.report;
  return <SettingsSection title={t('Usage')} caption={t('Last 7 days')} id="dv-testers-usage">
    {failed&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{t('Dervo couldn’t load usage. Check your connection, then try again.')}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={data?.state==='loading'} onClick={()=>void reload()}>{t('Try again')}</button></div></div>}
    {!shown&&!failed&&<p className="dv-sec-note" role="status">{t('Loading usage…')}</p>}
    {shown&&(shown.daily.length===0?<p className="dv-sec-note">{t('Nothing yet. Testers on beta.9 or later with usage sharing on show up here.')}</p>:<>
      <SettingRow name={plural(shown.active,t('tester active'),t('testers active'))} line={`By day: ${dayLine(shown)}. Days are UTC.`}/>
      <SettingRow name={t('Top screens')} line={t('Views, all testers together.')}
        below={<dl className="dv-rules" style={LIST}>{topScreens(shown).map(row=><div key={row.screen} style={{display:'contents'}}><dt>{row.label}</dt><dd>{plural(row.count,'view','views')}</dd></div>)}</dl>}/>
      <SettingRow name={t('First run')} line={t('People who reached and finished each step, since usage sharing began.')}
        below={<dl className="dv-rules" style={LIST}>{shown.funnel.map(row=><div key={row.step} style={{display:'contents'}}><dt>{STEP_LABELS[row.step]??row.step}</dt><dd>{funnelLine(row)}</dd></div>)}</dl>}/>
      <SettingRow name={t('Tasks')} line={t('How tasks ended, by provider.')}
        below={taskLines(shown).length?<dl className="dv-rules" style={LIST}>{taskLines(shown).map(row=><div key={row.provider} style={{display:'contents'}}><dt>{row.label}</dt><dd>{row.line}</dd></div>)}</dl>
          :<p className="dv-set-line">{t('No tasks have ended yet.')}</p>}/>
    </>)}
  </SettingsSection>;
}

// ---------------------------------------------------------------------------------------------- Testers page, People
/** Counts per tester (dervo_admin_usage_people): who besides the admins is actually doing things. Admins only. */
export type Person={user_id:string;email:string|null;status:'approved'|'pending'|'revoked'|null;is_admin:boolean;last_seen:number|null;
  app_version:string|null;active_days:number;opens:number;threads:number;home_requests:number;tasks_started:number;
  tasks_completed:number;tasks_failed:number;claude_tasks:number;codex_tasks:number;knowledge:number;feedback_opened:number};
export type PeopleReport={since:number;days:number;people:Person[]};
type PeopleState={state:'loading'|'ready'|'error';report:PeopleReport|null};
export const peopleQuery={queryKey:['usage-people'] as const,queryFn:()=>local<PeopleState>('usage_people',{reload:false}),
  refetchInterval:(query:{state:{data?:PeopleState}})=>query.state.data?.state==='loading'?400:false};

/** Sent at least one row in the window. */
export function activeIn(person:Person){return person.active_days>0;}

/** "2 of 5 testers used Dervo", admins left out, so it answers whether anyone besides you is. */
export function peopleSummary(report:PeopleReport){
  const testers=report.people.filter(person=>!person.is_admin);
  const active=testers.filter(activeIn).length;
  return `${active} of ${plural(testers.length,'tester','testers')} used Dervo`;
}

export function seenLine(person:Person){
  if(person.last_seen==null)return t('Never seen. Sharing may be off, or they haven’t opened beta.9 or later.');
  const seen=formatDate(new Date(person.last_seen*1000),{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'},'en-US');
  return `Last seen ${seen}${person.app_version?` on ${person.app_version}`:''}`;
}

/** What they did in the window, only the parts that aren't zero. */
export function doneLine(person:Person){
  const parts=[plural(person.active_days,'day','days'),plural(person.opens,'open','opens')];
  if(person.threads)parts.push(plural(person.threads,'thread','threads'));
  if(person.home_requests)parts.push(plural(person.home_requests,t('Home request'),t('Home requests')));
  if(person.tasks_started){
    const by=[person.claude_tasks&&`${person.claude_tasks} Claude`,person.codex_tasks&&`${person.codex_tasks} Codex`].filter(Boolean).join(', ');
    parts.push(`${plural(person.tasks_started,'task','tasks')}${by?` (${by})`:''}, ${person.tasks_completed} completed, ${person.tasks_failed} failed`);
  }
  if(person.knowledge)parts.push(t('{knowledge} kept in Knowledge',{knowledge:person.knowledge}));
  if(person.feedback_opened)parts.push(`feedback opened ${plural(person.feedback_opened,'time','times')}`);
  return parts.join(' · ');
}

export function TestersPeople(){
  const client=useQueryClient();
  const report=useQuery(peopleQuery);
  const data=report.data;
  async function reload(){
    try{client.setQueryData(peopleQuery.queryKey,await local<PeopleState>('usage_people',{reload:true}));}
    catch{client.setQueryData(peopleQuery.queryKey,{state:'error',report:data?.report??null});}
  }
  const failed=report.isError||data?.state==='error';
  const shown=data?.report;
  const active=shown?shown.people.filter(activeIn):[];
  const quiet=shown?shown.people.filter(person=>!activeIn(person)&&!person.is_admin):[];
  return <SettingsSection title={t('People')} caption={t('Last 7 days, by tester')} id="dv-testers-people">
    {failed&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{t('Dervo couldn’t load people. Check your connection, then try again.')}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={data?.state==='loading'} onClick={()=>void reload()}>{t('Try again')}</button></div></div>}
    {!shown&&!failed&&<p className="dv-sec-note" role="status">{t('Loading people…')}</p>}
    {shown&&<>
      <SettingRow name={peopleSummary(shown)} line={t('Admins aren’t counted. Only testers with usage sharing on, on beta.9 or later, show activity.')}/>
      {active.map(person=><SettingRow key={person.user_id} name={`${person.email??t('No email')}${person.is_admin?' (admin)':''}`}
        line={`${seenLine(person)}.`} below={<p className="dv-set-line" style={{fontSize:'var(--dv-fs-small)'}}>{doneLine(person)}</p>}/>)}
      {quiet.length>0&&<SettingRow name={plural(quiet.length,t('tester not seen this week'),t('testers not seen this week'))} line={t('Signed up, but nothing sent in the last 7 days.')}
        below={<Disclosure summary={t('Who')}><dl className="dv-rules" style={LIST}>{quiet.map(person=><div key={person.user_id} style={{display:'contents'}}>
          <dt>{person.email??t('No email')}</dt><dd>{person.status==='pending'?t('Waiting for approval, so nothing can be sent yet.'):seenLine(person)}</dd></div>)}</dl></Disclosure>}/>}
    </>}
  </SettingsSection>;
}

// ---------------------------------------------------------------------------------------------- Testers page, Errors
export type ErrorGroup={source:'host'|'renderer'|'task';error_type:string;file:string|null;function:string|null;line:number|null;
  operation:string|null;provider:string|null;count:number;users:number;first_seen:number;last_seen:number;app_version:string|null};
export type ErrorReport={since:number;days:number;errors:ErrorGroup[]};
type ErrorState={state:'loading'|'ready'|'error';report:ErrorReport|null};
export const errorQuery={queryKey:['error-report'] as const,queryFn:()=>local<ErrorState>('error_report',{reload:false}),
  refetchInterval:(query:{state:{data?:ErrorState}})=>query.state.data?.state==='loading'?400:false};
/** Groups shown before Show all. */
const ERRORS_SHOWN=12;

/** Where it happened: the Dervo source line, or the crash screen for the renderer. */
export function errorPlace(group:ErrorGroup){
  if(group.file)return t('{file}, {function}(), line {line}',{file:group.file,function:group.function??'',line:group.line??''});
  return group.source==='renderer'?t('The app’s crash screen'):t('Not in Dervo’s own code');
}

/** What was running: a host request by its operation name, a screen by its label, a task by its provider. */
export function errorDuring(group:ErrorGroup){
  if(group.source==='renderer')return group.operation?`On ${screenLabel(group.operation)}`:t('Before a screen showed');
  if(group.source==='task')return `${PROVIDERS[group.provider??'']??'A'} task${group.operation==='task_start'?', starting':''}`;
  return group.operation?t('During {operation}',{operation:group.operation}):t('Outside a request');
}

export function errorCounts(group:ErrorGroup){
  const seen=formatDate(new Date(group.last_seen*1000),{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'},'en-US');
  return `${plural(group.count,'time','times')} · ${plural(group.users,'tester','testers')} · last ${seen}${group.app_version?` on ${group.app_version}`:''}`;
}

export function TestersErrors(){
  const client=useQueryClient();
  const report=useQuery(errorQuery);
  const [all,setAll]=useState(false);
  const data=report.data;
  async function reload(){
    try{client.setQueryData(errorQuery.queryKey,await local<ErrorState>('error_report',{reload:true}));}
    catch{client.setQueryData(errorQuery.queryKey,{state:'error',report:data?.report??null});}
  }
  const failed=report.isError||data?.state==='error';
  const shown=data?.report;
  const groups=shown?(all?shown.errors:shown.errors.slice(0,ERRORS_SHOWN)):[];
  return <SettingsSection title={t('Errors')} caption={t('Last 7 days, newest first')} id="dv-testers-errors">
    {failed&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{t('Dervo couldn’t load error reports. Check your connection, then try again.')}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={data?.state==='loading'} onClick={()=>void reload()}>{t('Try again')}</button></div></div>}
    {!shown&&!failed&&<p className="dv-sec-note" role="status">{t('Loading error reports…')}</p>}
    {shown&&(shown.errors.length===0?<p className="dv-sec-note">{t('No errors reported. Testers on beta.11 or later with sharing on show up here.')}</p>:<>
      {groups.map(group=><SettingRow key={[group.source,group.error_type,group.file,group.function,group.line,group.operation,group.provider].join('|')}
        name={group.error_type} line={`${errorPlace(group)}. ${errorDuring(group)}.`}
        below={<p className="dv-set-line" style={{fontSize:'var(--dv-fs-small)'}}>{errorCounts(group)}</p>}/>)}
      {shown.errors.length>ERRORS_SHOWN&&<div className="dv-set"><div><p className="dv-set-line">{all?t('All {length} kinds shown.',{length:shown.errors.length}):`${shown.errors.length-ERRORS_SHOWN} older kinds not shown.`}</p></div>
        <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>setAll(!all)}>{all?t('Show fewer'):t('Show all')}</button></div></div>}
    </>)}
  </SettingsSection>;
}
