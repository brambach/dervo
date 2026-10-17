import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {SettingRow,SettingsSection} from './DervoControls';
import {Confirm,Disclosure,OpenCard,StatusWord,dayLabel} from './SettingsKit';
import {t} from '@/i18n';

/** The waitlist, on the Testers page, for admins only. Pick a few people, read exactly who gets an email, send, and see
 * what happened to each. An invitation approves that email's first verified sign-in, so nobody edits a row by hand. The
 * host checks you're an admin, the website checks your session with the account server, and the server checks again.
 * Inviting someone twice sends nothing: a second email takes Resend. */
export type WaitlistPerson={email:string;source?:'website'|'admin';signed_up_at:number;status:'waiting'|'invited'|'accepted'|'declined'|'revoked';invited_at:number|null;expires_at:number|null;delivery:'pending'|'sent'|'failed'|null};
export type WaitlistResult={email:string;result:string;expires_at:number|null};
export type InvitationPreview={from:string;reply_to:string;subject:string;text:string};
export type Waitlist={email_preview?:InvitationPreview|null;state:'idle'|'loading'|'saving'|'sending'|'ready'|'error';people:WaitlistPerson[];more:boolean;detail:string|null;results:WaitlistResult[];results_id:number};
type Action='invite'|'resend'|'revoke';

export const BATCH=10;
/** An invitation with less than this left is flagged on its row and listed first. */
const SOON=7*86400;
/** How much of an invitation is left, for the admin. When one runs out nobody is told: the person can still sign in, stays
 * pending and waits. So the row says it in words, and the list puts the ones about to lapse (or already gone) on top. */
export function invitationLeft(expiresAt:number|null,now=Date.now()):{state:'open'|'soon'|'expired';words:string;flag?:string}|null{
  if(!expiresAt)return null;
  const left=expiresAt-Math.floor(now/1000),date=dayLabel(expiresAt,{time:false,now});
  if(left<=0)return {state:'expired',words:t('expired {date}',{date}),flag:t('Expired')};
  if(left>SOON)return {state:'open',words:t('expires {date}',{date})};
  const days=Math.ceil(left/86400);
  return {state:'soon',words:days>1?t('expires {date}, in {days} days',{date,days}):t('expires {date}',{date}),
    flag:days>1?t('Expires in {days} days',{days}):t('Expires within a day')};
}
/** The magnifier Settings search uses, so the two search fields read as one control. */
const SEARCH=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><circle cx="6.8" cy="6.8" r="4.1"/><path d="M9.8 9.8l3.5 3.5"/></svg>;
/** Whether a person matches the search: any part of their email, ignoring case. An empty search matches everyone. */
export const matchesSearch=(person:{email:string},query:string)=>!query.trim()||person.email.toLowerCase().includes(query.trim().toLowerCase());
export const waitlistQuery={queryKey:['dervo-waitlist'] as const,queryFn:()=>local<Waitlist>('dervo_waitlist',{reload:false}),
  refetchInterval:(query:{state:{data?:Waitlist}})=>['idle','loading','saving','sending'].includes(query.state.data?.state??'idle')?400:false};

const WORKED=['added','already_listed','sent','revoked','already_accepted','already_approved','already_invited'];
/** What happened to one person, as a sentence. Anything that didn't send says whether it's safe to try again. */
export function resultLine(result:WaitlistResult):string{
  switch(result.result){
    case 'added':return 'Address added. No email was sent and access wasn’t approved.';
    case 'already_listed':return 'This address is already on the list. Nothing changed and no email was sent.';
    case 'delivery_review':return 'Beta access is approved. Email delivery needs review before another email is sent.';
    case 'sent':return `Invitation sent.${result.expires_at?` It expires ${dayLabel(result.expires_at,{time:false})}.`:''}`;
    case 'revoked':return 'Invitation withdrawn. Signing in with this email stays pending.';
    case 'already_invited':return 'Already invited, so nothing was sent. Use Resend to email them again.';
    case 'already_accepted':case 'already_approved':return 'Already has beta access, so nothing was sent.';
    case 'member_revoked':return 'Their access was revoked, so nothing was sent. Approve them under Approved instead.';
    case 'needs_resend':return 'Their last invitation is too old to retry or was withdrawn. Use Resend to send a new one.';
    case 'not_on_waitlist':return 'No longer on the waitlist. Nothing was sent.';
    case 'rate_limited':return 'Too many invitations this hour. Nothing was sent. Try again later.';
    case 'send_budget_reached':return 'Access is recorded. Today’s email limit is reached, so the email hasn’t been sent. Try again tomorrow.';
    case 'send_failed':return 'Access is recorded, but email delivery wasn’t confirmed. Retry uses the same invitation.';
    case 'grant_failed':return 'Dervo couldn’t record the invitation, so nothing was sent.';
    case 'not_admin':return 'Your admin access couldn’t be confirmed. Nothing was sent.';
    default:return 'That didn’t go through. Try again.';
  }
}

export function WaitlistInvites(){
  const client=useQueryClient();
  const waitlist=useQuery(waitlistQuery);
  const [chosen,setChosen]=useState<string[]>([]),[asking,setAsking]=useState<{action:Action;emails:string[];retry?:boolean}|null>(null);
  const [seen,setSeen]=useState(0),[error,setError]=useState(''),[address,setAddress]=useState(''),[submitting,setSubmitting]=useState(false);
  const [query,setQuery]=useState('');
  const data=waitlist.data;
  async function run(operation:string,args:Record<string,unknown>){
    setError('');setSubmitting(true);
    try{client.setQueryData(waitlistQuery.queryKey,await local<Waitlist>(operation,args));return true;}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));return false;}
    finally{setSubmitting(false);}
  }
  if(!data)return <SettingsSection title="Waitlist" id="dv-waitlist">
    {waitlist.isError?<Retry line="Dervo couldn’t load the waitlist." onRetry={()=>void waitlist.refetch()}/>:<p className="dv-sec-note" role="status">Loading the waitlist…</p>}
  </SettingsSection>;
  const busy=submitting||['sending','saving','loading'].includes(data.state);
  const canSend=!!data.email_preview;
  // An empty list only means nobody is waiting once it has actually loaded. Before that, or after a failed load, it means nothing.
  const settled=data.state==='ready'||data.state==='sending'||data.people.length>0;
  const waiting=data.people.filter(p=>p.status==='waiting'),invited=data.people.filter(p=>p.status==='invited');
  const accepted=data.people.filter(p=>p.status==='accepted'),withdrawn=data.people.filter(p=>p.status==='revoked'||p.status==='declined');
  const picked=chosen.filter(email=>waiting.some(p=>p.email===email));
  // The search narrows every group at once. A person you picked stays picked while the search hides them.
  const shown=<T extends {email:string}>(people:T[])=>people.filter(person=>matchesSearch(person,query));
  const shownWaiting=shown(waiting),hidden=picked.filter(email=>!shownWaiting.some(p=>p.email===email)).length;
  const searching=!!query.trim();
  // The invitations closest to running out come first, then the rest by address.
  const byExpiry=(a:WaitlistPerson,b:WaitlistPerson)=>(a.expires_at??Infinity)-(b.expires_at??Infinity)||a.email.localeCompare(b.email);
  const invitedShown=shown(invited).sort(byExpiry);
  const lapsing=invitedShown.filter(p=>invitationLeft(p.expires_at)?.state==='soon').length,lapsed=invitedShown.filter(p=>invitationLeft(p.expires_at)?.state==='expired').length;
  const lapseNote=[lapsing?t('{count} expire within a week',{count:lapsing}):'',lapsed?t('{count} expired',{count:lapsed}):''].filter(Boolean).join(', ');
  const toggle=(email:string,on:boolean)=>setChosen(on?[...picked,email]:picked.filter(e=>e!==email));
  const send=async()=>{if(!asking||busy||(!canSend&&asking.action!=='revoke'))return;const sent=await run('dervo_waitlist_invite',{action:asking.action,emails:asking.emails});setAsking(null);if(sent)setChosen([]);};
  const reload=()=>void run('dervo_waitlist',{reload:true});
  const count=(n:number,word:string)=>`${n} ${word}${n===1?'':'s'}`;
  // The invitation card opens where it was asked for: under Invite selected, or in place of the row whose email is retried.
  const inviteCard=(key?:string)=>asking?.action==='invite'?<OpenCard key={key} title={asking.retry?'Retry the invitation email?':`Invite ${asking.emails.length===1?'1 person':`${asking.emails.length} people`}?`} onEscape={()=>setAsking(null)}>
      <p className="dv-card-text">{asking.retry?'Retry uses the original email and invitation expiry date. It doesn’t renew the invitation or change beta access. If delivery is too old to safely retry, Dervo will ask you to review it.':'Each of these addresses gets one email with the beta link. Beta access is tied to that address. They have 14 days to accept the invitation, then their approved beta access continues.'}</p>
      <ul className="dv-card-text" data-testid="waitlist-confirm-list">{asking.emails.map(email=><li key={email}>{email}</li>)}</ul>
      {!asking.retry&&<EmailPreview value={data.email_preview}/>}
      <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>setAsking(null)}>Cancel</button>
        <button type="button" className="dv-btn dv-btn-primary" disabled={busy||!canSend} onClick={()=>void send()}>{busy?'Sending…':asking.retry?'Retry email':`Send ${count(asking.emails.length,'invitation')}`}</button></div>
    </OpenCard>:null;
  const retrying=(email:string)=>asking?.action==='invite'&&asking.retry&&asking.emails[0]===email;
  const refresh=data.state!=='error'&&<button type="button" className="dv-btn dv-btn-quiet" disabled={busy||!!asking} onClick={reload}>{data.state==='loading'?'Refreshing…':'Refresh'}</button>;
  return <SettingsSection title="Waitlist" caption={settled?`${waiting.length} waiting`:undefined} id="dv-waitlist">
    {(data.state==='error'||error||data.detail)&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{error||data.detail||'Dervo couldn’t load the waitlist.'}</p></div>
      {data.state==='error'&&<div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={reload}>Reload</button></div>}</div>}
    <p className="dv-sec-note">Website signups and addresses you add here share this list. Adding an address doesn't send an email or approve beta access.</p>
    <form className="dv-invite-add" onSubmit={async event=>{event.preventDefault();if(busy||asking)return;if(await run('dervo_waitlist_invite',{action:'add',emails:[address.trim().toLowerCase()]}))setAddress('');}}>
      <label htmlFor="dv-invite-email">Email address<input id="dv-invite-email" className="dv-input" type="email" autoComplete="off" placeholder="name@example.com" maxLength={254} required value={address} disabled={busy||!!asking} onChange={event=>setAddress(event.target.value)}/></label>
      <button type="submit" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||!!asking||!address.trim()}>{data.state==='saving'?'Adding…':'Add address'}</button>
    </form>
    {!canSend&&settled&&<p className="dv-sec-note" role="status">The invitation email preview isn't available. Refresh after the invitation service is updated before sending.</p>}
    {data.people.length>0&&<div className="dv-waitlist-search">
      <span className="dv-waitlist-field">{SEARCH}<input type="search" className="dv-input" aria-label="Search the waitlist" placeholder={`Search ${data.people.length} ${data.people.length===1?'address':'addresses'}`} autoComplete="off" spellCheck={false}
        value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==='Escape'&&query){event.preventDefault();event.stopPropagation();setQuery('');}}}/></span>
      {searching&&<span className="dv-waitlist-count" role="status">{shown(data.people).length} {shown(data.people).length===1?'match':'matches'}</span>}
    </div>}
    {!waiting.length&&(settled?<div className="dv-set"><div><p className="dv-set-line">Nobody is waiting. New waitlist signups appear here.</p></div><div className="dv-set-end">{refresh}</div></div>
      :data.state!=='error'&&<p className="dv-sec-note" role="status">Loading the waitlist…</p>)}
    {waiting.length>0&&(shownWaiting.length?<ul className="dv-waitlist" aria-label="Waiting to be invited">{shownWaiting.map(person=>{
      const on=picked.includes(person.email);
      return <li key={person.email} className="dv-waitlist-row" data-selected={on||undefined}>
        <label className="dv-pick"><input type="checkbox" aria-label={`Select ${person.email}`} disabled={busy||!!asking||(!on&&picked.length>=BATCH)}
          checked={on} onChange={event=>toggle(person.email,event.target.checked)}/><span className="dv-waitlist-email">{person.email}</span></label>
        <span className="dv-waitlist-meta" title={`${person.source==='admin'?'Added by an admin':'Joined the website waitlist'} ${dayLabel(person.signed_up_at,{time:false})}`}>
          {person.source==='admin'?'Added':'Website'} · {dayLabel(person.signed_up_at,{time:false})}</span>
      </li>;})}</ul>
      :<p className="dv-sec-note" role="status">Nobody waiting matches “{query.trim()}”.</p>)}
    {waiting.length>0&&<div className="dv-set"><div><p className="dv-set-line">{picked.length?`${picked.length} selected${hidden?`, ${hidden} hidden by the search`:''}.`:'Select the people to invite.'} Up to {BATCH} at a time.</p></div>
      <div className="dv-set-end">{refresh}<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||!canSend||!picked.length||!!asking} onClick={()=>setAsking({action:'invite',emails:picked})}>Invite selected</button></div></div>}
    {asking?.action==='invite'&&!asking.retry&&inviteCard()}
    {data.results.length>0&&data.results_id!==seen&&<OpenCard title="What happened" label="Invitation results" onEscape={()=>setSeen(data.results_id)}>
      <div data-testid="waitlist-results">{data.results.map(result=><SettingRow key={result.email} name={result.email} line={resultLine(result)}
        end={<StatusWord state={WORKED.includes(result.result)?'done':'failed'}>{['already_accepted','already_approved'].includes(result.result)?'Approved':WORKED.includes(result.result)?'Done':'Check'}</StatusWord>}/>)}</div>
      <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>setSeen(data.results_id)}>Done</button></div>
    </OpenCard>}
    {data.more&&<p className="dv-sec-note" role="status">Showing the newest {data.people.length} sign-ups. Earlier ones aren’t listed.</p>}
    {invitedShown.length>0&&<Disclosure summary={`${invitedShown.length} invited, not signed in yet`+(lapseNote?` · ${lapseNote}`:'')} open>
      {lapseNote&&<p className="dv-sec-note">{t('An invitation that runs out isn’t a dead end: they can still sign in and wait under Asking to join for you to approve them. Resend gives them a new 14-day invitation.')}</p>}
      {invitedShown.map(person=>{
        const unsent=person.delivery!=='sent',left=invitationLeft(person.expires_at);
        if(retrying(person.email))return inviteCard(person.email);
        if(asking&&asking.action!=='invite'&&asking.emails[0]===person.email)return asking.action==='resend'
          ?<Confirm key={person.email} title={`Email ${person.email} again?`} line={left?.state==='expired'?t('Their invitation has run out. This sends another email with a new 14-day invitation.'):'This sends another email with a new 14-day invitation. The earlier email may already have arrived.'} keep="Cancel" act="Resend"
            busy={busy} busyLabel="Sending…" onKeep={()=>setAsking(null)} onAct={()=>void send()}/>
          :<Confirm key={person.email} title={`Withdraw the invitation for ${person.email}?`} line="Signing in with this email stays pending. You can invite them again." keep="Keep" act="Withdraw"
            busy={busy} busyLabel="Withdrawing…" onKeep={()=>setAsking(null)} onAct={()=>void send()}/>;
        // Retry reuses the stored email within the provider's duplicate prevention window. Resend creates a new invitation.
        return <SettingRow key={person.email} name={person.email}
          line={unsent?'Approved to join. Email delivery isn’t confirmed.'+(left?` ${t('It {when}.',{when:left.words})}`:''):`Invited ${person.invited_at?dayLabel(person.invited_at,{time:false}):''}${left?` · ${left.words}`:''}`}
          end={<>{left?.flag&&<StatusWord state={left.state==='expired'?'stopped':'needs'}>{left.flag}</StatusWord>}<button type="button" className="dv-btn dv-btn-quiet" disabled={busy||!!asking} onClick={()=>setAsking({action:'revoke',emails:[person.email]})}>Withdraw</button>
            <button type="button" className="dv-btn dv-btn-quiet" disabled={busy||!canSend||!!asking} onClick={()=>setAsking({action:'resend',emails:[person.email]})}>Resend</button>
            {unsent&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||!canSend||!!asking} onClick={()=>setAsking({action:'invite',emails:[person.email],retry:true})}>Retry email</button>}</>}/>;
      })}
    </Disclosure>}
    {shown(accepted).length>0&&<Disclosure summary={`${shown(accepted).length} joined the beta`} open={searching||undefined}>
      {shown(accepted).map(person=>retrying(person.email)?inviteCard(person.email):<SettingRow key={person.email} name={person.email} line={person.delivery&&person.delivery!=='sent'?'Invitation accepted. Email delivery isn’t confirmed. Manage beta access under Approved.':'Invitation accepted. Manage beta access under Approved.'}
        end={person.delivery&&person.delivery!=='sent'&&<button type="button" className="dv-btn dv-btn-quiet" disabled={busy||!canSend||!!asking} onClick={()=>setAsking({action:'invite',emails:[person.email],retry:true})}>Retry email</button>}/>)}
    </Disclosure>}
    {shown(withdrawn).length>0&&<Disclosure summary={`${shown(withdrawn).length} withdrawn`} open={searching||undefined}>
      {shown(withdrawn).map(person=><SettingRow key={person.email} name={person.email} line="Invitation withdrawn."
        end={<button type="button" className="dv-btn dv-btn-quiet" disabled={busy||!canSend||!!asking} onClick={()=>setAsking({action:'resend',emails:[person.email]})}>Invite again</button>}/>)}
    </Disclosure>}
    {asking?.action==='resend'&&<EmailPreview value={data.email_preview}/>}
    {asking?.action==='resend'&&withdrawn.some(p=>p.email===asking.emails[0])&&<Confirm title={`Invite ${asking.emails[0]} again?`} line="They get an email with a new 14-day invitation."
      keep="Cancel" act="Invite" busy={busy} busyLabel="Sending…" onKeep={()=>setAsking(null)} onAct={()=>void send()}/>}
  </SettingsSection>;
}

function Retry({line,onRetry}:{line:string;onRetry:()=>void}){
  return <div className="dv-set" role="alert"><div><p className="dv-set-line">{line}</p></div>
    <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onRetry}>Try again</button></div></div>;
}

function EmailPreview({value}:{value?:InvitationPreview|null}){
 if(!value)return null;
 return <Disclosure summary="Read the invitation email" open>
  <div className="dv-invite-preview"><p>From: {value.from}</p><p>Replies to: {value.reply_to}</p><p>Subject: {value.subject}</p>
   <p className="dv-sec-note">Everyone gets this message with their own email address and invitation expiry date.</p>
   <div className="dv-invite-email">{value.text}</div>
  </div>
 </Disclosure>;
}
