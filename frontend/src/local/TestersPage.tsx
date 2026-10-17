import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {Segmented,SettingRow,SettingsSection} from './DervoControls';
import {Confirm,Disclosure,OpenCard,dayLabel,dayStart} from './SettingsKit';

/** Testers, in Settings, for admins only (settings/testers.html). People asking to join with Approve and Decline, approved
 * testers with an optional end date set in place, the revoked list, and every change as a plain dated line. The host
 * checks you're an admin before asking the server, and the server's own functions check again. */
export type Tester={user_id:string;email:string|null;status:'pending'|'approved'|'revoked';expires_at:number|null;signed_up_at:number|null;updated_at:number|null;is_admin:boolean;is_you:boolean};
export type TesterChange={id:number;changed_at:number;actor_id:string;actor_email:string|null;subject_id:string;subject_email:string|null;previous_status:string|null;new_status:string;expires_at:number|null;by_you:boolean};
export type Testers={state:'idle'|'loading'|'saving'|'ready'|'error';people:Tester[];audit:TesterChange[];more:boolean;detail:string|null;busy:string|null;
  /** The server lists at most 500 people, newest sign-ups first. True when it hit that, so older sign-ups may be missing. */
  cut?:boolean};

export const testersQuery={queryKey:['dervo-testers'] as const,queryFn:()=>local<Testers>('dervo_testers',{reload:false}),
  refetchInterval:(query:{state:{data?:Testers}})=>['idle','loading','saving'].includes(query.state.data?.state??'idle')?400:false};

type End='none'|'two_weeks'|'thirty_days'|'pick';
const DAY=86400;
/** The end date an option means, as seconds, from the start of today so "Two weeks" is a whole day. */
export function endDate(choice:End,picked:string,now=Date.now()):number|null{
  if(choice==='none')return null;
  if(choice==='pick'){const at=picked?Date.parse(`${picked}T23:59:00`):NaN;return Number.isFinite(at)?Math.floor(at/1000):NaN;}
  const start=new Date(now);start.setHours(23,59,0,0);
  return Math.floor(start.getTime()/1000)+(choice==='two_weeks'?14:30)*DAY;
}
const who=(row:{email:string|null})=>row.email??'An account with no email';

export function TestersPage(){
  const client=useQueryClient();
  const testers=useQuery(testersQuery);
  const [ending,setEnding]=useState<string|null>(null),[revoking,setRevoking]=useState<string|null>(null);
  const [choice,setChoice]=useState<End>('none'),[picked,setPicked]=useState(''),[error,setError]=useState('');
  const data=testers.data;
  async function run(operation:string,args:Record<string,unknown>){
    setError('');
    try{client.setQueryData(testersQuery.queryKey,await local<Testers>(operation,args));}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  }
  const set=(tester:Tester,status:'approved'|'revoked',expiresAt:number|null=null)=>run('dervo_tester_set',{userId:tester.user_id,status,expiresAt});
  if(!data)return testers.isError?<Retry onRetry={()=>void testers.refetch()}/>:<p className="dv-sec-note" role="status">Loading testers…</p>;
  const busy=data.state==='saving'||data.state==='loading';
  const asking=data.people.filter(p=>p.status==='pending').sort((a,b)=>(a.signed_up_at??0)-(b.signed_up_at??0)),approved=data.people.filter(p=>p.status==='approved'),revoked=data.people.filter(p=>p.status==='revoked');
  const acting=(tester:Tester,word:string,done:string)=>data.busy===tester.user_id?word:done;
  const end=ending?approved.find(p=>p.user_id===ending):undefined;
  const until=endDate(choice,picked);
  const untilLine=until===null?'No end date. They keep access until you revoke it.':Number.isNaN(until)?'Pick the day access ends.':<>Access ends on <b>{dayLabel(until,{time:false})}</b>.</>;
  return <>
    {(data.state==='error'||error)&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{error||data.detail}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void run('dervo_testers',{reload:true})}>Try again</button></div></div>}
    {data.cut&&<p className="dv-sec-note" role="status" data-testid="testers-cut">Showing the newest {data.people.length} sign-ups. Anyone who signed up before them isn’t listed here, including people still asking to join.</p>}
    <SettingsSection title="Asking to join" caption={asking.length?String(asking.length):undefined} id="dv-testers-asking">
      {!asking.length&&<p className="dv-sec-note">Nobody is waiting. New sign-ups appear here.</p>}
      {asking.map(tester=><SettingRow key={tester.user_id} name={who(tester)} line={tester.signed_up_at?`Signed up ${dayLabel(tester.signed_up_at)}`:'Signed up'}
        end={<><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void set(tester,'revoked')}>Decline</button>
          <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void set(tester,'approved')}>{acting(tester,'Approving…','Approve')}</button></>}/>)}
    </SettingsSection>
    <SettingsSection title="Approved" caption={String(approved.length)} id="dv-testers-approved">
      {approved.map(tester=>{
        const endLine=tester.expires_at?`until ${dayLabel(tester.expires_at,{time:false})}`:'no end date';
        if(end&&end.user_id===tester.user_id)return <OpenCard key={tester.user_id} title={`End date for ${who(tester)}`} caption={tester.updated_at?`Approved ${dayLabel(tester.updated_at,{time:false})}`:undefined} onEscape={()=>setEnding(null)}>
          <Segmented label="When access ends" value={choice} onChange={setChoice} options={[{value:'none',label:'No end date'},{value:'two_weeks',label:'Two weeks'},{value:'thirty_days',label:'30 days'},{value:'pick',label:'Pick a date'}]}/>
          {choice==='pick'&&<label className="dv-field"><span>Last day</span><input className="dv-input" type="date" aria-label="Last day of access" value={picked} onChange={event=>setPicked(event.target.value)}/></label>}
          <p className="dv-card-text">{untilLine} Their projects stay on their Mac, and you can approve them again.</p>
          <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>setEnding(null)}>Cancel</button>
            <button type="button" className="dv-btn dv-btn-primary" disabled={busy||Number.isNaN(until)} onClick={()=>void set(tester,'approved',until).then(()=>setEnding(null))}>{data.busy===tester.user_id?'Saving…':'Save end date'}</button></div>
        </OpenCard>;
        if(revoking===tester.user_id)return <Confirm key={tester.user_id} title={`Revoke ${who(tester)}?`} line="They keep their projects, and you can approve them again."
          keep="Keep" act="Revoke" busy={data.busy===tester.user_id} busyLabel="Revoking…" onKeep={()=>setRevoking(null)} onAct={()=>void set(tester,'revoked').then(()=>setRevoking(null))}/>;
        return <SettingRow key={tester.user_id} name={who(tester)}
          line={tester.is_admin?`Admin · ${endLine}`:`${tester.updated_at?`Approved ${dayLabel(tester.updated_at,{time:false})} · `:''}${endLine}`}
          end={tester.is_you?undefined:<><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>{setRevoking(null);setChoice(tester.expires_at?'pick':'none');setPicked(tester.expires_at?new Date(tester.expires_at*1000).toISOString().slice(0,10):'');setEnding(tester.user_id);}}>End date</button>
            <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>{setEnding(null);setRevoking(tester.user_id);}}>Revoke</button></>}/>;
      })}
      {revoked.length>0&&<Disclosure summary={`${revoked.length} revoked`}>
        {revoked.map(tester=><SettingRow key={tester.user_id} name={who(tester)} line={tester.updated_at?`Revoked ${dayLabel(tester.updated_at,{time:false})}`:'Revoked'}
          end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void set(tester,'approved')}>{acting(tester,'Approving…','Approve')}</button>}/>)}
      </Disclosure>}
    </SettingsSection>
    <SettingsSection title="Changes" caption="Every change is recorded" id="dv-testers-changes">
      {!data.audit.length&&<p className="dv-sec-note">No changes yet.</p>}
      {data.audit.map(change=><div key={change.id} className="dv-log"><time dateTime={new Date(change.changed_at*1000).toISOString()}>{dayStart(change.changed_at)}</time><span>{changeLine(change)}</span></div>)}
      {data.more&&<button type="button" className="dv-btn dv-btn-quiet" style={{alignSelf:'flex-start'}} disabled={busy} onClick={()=>void run('dervo_testers_older',{})}>Show older</button>}
    </SettingsSection>
  </>;
}

/** One audit row as a sentence: "You approved alex@example.com. No end date." */
export function changeLine(change:TesterChange){
  const actor=change.by_you?'You':change.actor_email??'An admin';
  const subject=<b>{change.subject_email??'an account with no email'}</b>;
  if(change.new_status==='approved')return <>{actor} approved {subject}. {change.expires_at?`Until ${dayLabel(change.expires_at,{time:false})}.`:'No end date.'}</>;
  if(change.new_status==='revoked')return <>{actor} {change.previous_status==='pending'?'declined':'revoked'} {subject}.</>;
  return <>{actor} set {subject} back to waiting.</>;
}

function Retry({onRetry}:{onRetry:()=>void}){
  return <div className="dv-set" role="alert"><div><p className="dv-set-line">Dervo couldn’t load testers.</p></div>
    <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onRetry}>Try again</button></div></div>;
}
