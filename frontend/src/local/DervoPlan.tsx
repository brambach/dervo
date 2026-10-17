import {useState} from 'react';
import type {DervoAccountStatus,DervoPlan as Plan} from './api';
import {SettingRow,SettingsSection} from './DervoControls';
import {StatusWord,dayLabel} from './SettingsKit';
import type {GlyphState} from './StatusGlyph';
import {t} from '@/i18n';

/* Paid plans and the free trial (8 Oct 2026). The host starts the trial and opens Paddle checkout or billing in the
 * browser; access itself comes from the server, so nothing here grants it. The prices shown must match the Paddle prices
 * (dervo/pricing-and-positioning.md in the project files), locked by Bryce on 8 Oct 2026: $15 a month, $144 a year, 7-day
 * trial. Paddle's checkout shows the exact amount with local tax. */
export const PRICES={month:'$15',year:'$144'} as const;

/** The plan, as one row: a status word and one line saying what it means and what happens next. A missing date drops its
 * clause rather than leaving a blank. */
export function planSummary(plan:Plan,now=Date.now()):{state:GlyphState;word:string;line:string}{
 const day=(seconds:number|null|undefined)=>typeof seconds==='number'&&Number.isFinite(seconds)&&seconds>0?dayLabel(seconds,{time:false,now}):null;
 const sub=plan.subscription;
 const cycle=sub?.interval==='year'?t('Dervo Pro, yearly'):t('Dervo Pro, monthly');
 if(plan.plan==='removed')return {state:'failed',word:t('Removed'),line:t('This account no longer has access to Dervo. Your saved work is still here.')};
 if(plan.plan==='beta')return {state:'done',word:t('Beta'),line:t('You’re a beta tester, so every feature is on for this account.')};
 if(sub&&(plan.plan==='paid'||plan.plan==='trial')){
  const until=day(sub.access_until),ends=day(sub.cancel_at),renews=day(sub.period_ends_at);
  if(sub.status==='past_due')return {state:'needs',word:t('Payment failed'),line:until?t('{plan}. Update your payment method in billing to keep access after {day}.',{plan:cycle,day:until}):t('{plan}. Update your payment method in billing to keep access.',{plan:cycle})};
  if(sub.status==='paused')return {state:'stopped',word:t('Paused'),line:t('{plan} is paused. Resume it in billing to start agent work again.',{plan:cycle})};
  if(sub.status==='trialing'){
   if(sub.cancel_at)return {state:'stopped',word:t('Free trial'),line:ends?t('Your free trial ends {day} and won’t turn into {plan}. You can change that in billing.',{day:ends,plan:cycle}):t('Your free trial won’t turn into {plan}. You can change that in billing.',{plan:cycle})};
   return {state:'running',word:t('Free trial'),line:renews?t('Free until {day}, then {plan}. Cancel any time before then in billing.',{day:renews,plan:cycle}):t('Free for now, then {plan}. Cancel any time in billing.',{plan:cycle})};
  }
  if(sub.cancel_at)return {state:'stopped',word:ends?t('Ends {day}',{day:ends}):t('Ending'),line:t('{plan}. It won’t renew. You can turn renewal back on in billing.',{plan:cycle})};
  return {state:'done',word:t('Active'),line:renews?t('{plan}. Renews {day}.',{plan:cycle,day:renews}):t('{plan}.',{plan:cycle})};
 }
 if(plan.plan==='trial'){const ends=day(plan.trial.ends_at);return {state:'running',word:t('Free trial'),line:ends?t('Your free trial ends {day}. Choose a plan any time to keep starting agent work after that.',{day:ends}):t('Choose a plan any time to keep starting agent work after your trial.')};}
 if(sub?.status==='canceled')return {state:'stopped',word:t('Ended'),line:t('Your Dervo Pro plan has ended. Choose a plan to start agent work again. Your projects and threads are still here.')};
 if(plan.trial.available)return plan.trial.mode==='card'
  ?{state:'idle',word:t('Not started'),line:t('Try Dervo free for {days} days, then {month} a month or {year} a year. Cancel any time before the trial ends and you won’t be charged.',{days:plan.trial.days,month:PRICES.month,year:PRICES.year})}
  :{state:'idle',word:t('Not started'),line:t('Try Dervo free for {days} days. No card needed. Your Claude and Codex plans stay yours.',{days:plan.trial.days})};
 return {state:'stopped',word:t('Trial ended'),line:t('Choose a plan to keep starting agent work. Your projects and threads are still here.')};
}

/** Calendar days left in a trial (0 is today, 1 tomorrow), when it's close to ending (3 days or less), for the notice on
 * Home. Null otherwise. Counted by date, not by 24 hours, so it agrees with the end date Settings shows: 25 hours
 * before a trial ending tomorrow morning is "tomorrow", not "in 2 days" (RC review, 9 Oct). */
export function trialEnding(plan:Plan|null|undefined,now=Date.now()):number|null{
 if(!plan||plan.plan!=='trial'||plan.subscription)return null;
 const ends=plan.trial.ends_at;
 if(typeof ends!=='number'||ends*1000<=now)return null;
 const day=(at:number)=>{const d=new Date(at);return new Date(d.getFullYear(),d.getMonth(),d.getDate()).getTime();};
 const left=Math.round((day(ends*1000)-day(now))/86400000);
 return left<=3?left:null;
}

/** Plan, in Account settings. Shown only when the server describes a plan, so older servers keep the Beta access row. */
export function DervoPlanSection({data,busy,onAction}:{data:DervoAccountStatus;busy:boolean;onAction:(operation:string,args?:Record<string,unknown>)=>Promise<unknown>}){
 const [asked,setAsked]=useState<string|null>(null);
 const plan=data.plan;
 if(!plan)return null;
 const summary=planSummary(plan);
 const billing=data.billing;
 const waiting=billing?.state==='opening'||billing?.state==='starting';
 const act=(operation:string,args:Record<string,unknown>={},label=operation)=>{setAsked(label);void onAction(operation,args).finally(()=>setAsked(null));};
 // The host only reports that checkout or billing is opening, not which button; the button that was pressed says so.
 const opening=(label:string)=>asked===label||(waiting&&asked===null&&billing?.action==='manage'&&label==='manage');
 const choose=data.can_buy&&plan.plan!=='beta';
 const cardTrial=plan.trial.available&&plan.trial.mode==='card';
 const chooseLine=cardTrial
  ?t('Dervo Pro. Your first {days} days are free, then it renews monthly or yearly. Yearly saves 20%.',{days:plan.trial.days})
  :t('Dervo Pro. Yearly saves 20%. Cancel any time.');
 return <SettingsSection title={t('Plan')} id="dv-account-plan" caption={plan.environment==='sandbox'?t('Test mode: no real charges'):undefined}>
  <SettingRow name={t('Plan')} line={summary.line}
   end={data.can_start_trial
    ?<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||waiting} onClick={()=>act('dervo_plan_start_trial',{},'trial')}>{asked==='trial'||billing?.state==='starting'?t('Starting…'):t('Start free trial')}</button>
    :<StatusWord state={summary.state}>{summary.word}</StatusWord>}/>
  {choose&&<SettingRow name={cardTrial?t('Start your free trial'):t('Choose a plan')} line={chooseLine}
   end={<><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||waiting} onClick={()=>act('dervo_billing_open',{action:'checkout',interval:'month'},'month')}>{opening('month')?t('Opening…'):t('Monthly, {price}',{price:PRICES.month})}</button>
    <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||waiting} onClick={()=>act('dervo_billing_open',{action:'checkout',interval:'year'},'year')}>{opening('year')?t('Opening…'):t('Yearly, {price}',{price:PRICES.year})}</button></>}/>}
  {data.can_manage_billing&&<SettingRow name={t('Billing')} line={t('Change your plan, payment method or renewal, and get invoices.')}
   end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||waiting} onClick={()=>act('dervo_billing_open',{action:'manage',interval:null},'manage')}>{opening('manage')?t('Opening…'):t('Manage billing')}</button>}/>}
  {billing?.state==='opened'&&<div role="status"><SettingRow name={billing.action==='manage'?t('Billing opened in your browser'):t('Checkout opened in your browser')}
   line={billing.action==='manage'?t('Finish there. Dervo picks up changes every minute, or check now.'):t('Finish there. Dervo checks for your payment every minute, or check now.')}
   end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>act('dervo_plan_refresh',{},'refresh')}>{asked==='refresh'?t('Checking…'):t('Check now')}</button>}/></div>}
  {billing?.state==='error'&&billing.detail&&<p className="dv-field-note" role="alert">{billing.detail}</p>}
 </SettingsSection>;
}
