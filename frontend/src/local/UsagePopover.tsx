import {useRef,type RefObject} from 'react';
import {Popover,PopoverAnchor,PopoverContent} from '@/shared/ui/popover';
import type {UsageAccount,UsageSummary} from './api';
import {accountLabel} from './DervoSidebar';
import {Meter} from './SettingsKit';
import {formatDate,t} from '@/i18n';

/** What one account has left, the way Connected accounts and the usage popover show it: a bar, the number, and the
 * limit and reset on one line. Everything comes from what the provider reported (the host's usage summary). */
const WINDOWS:Record<number,string>={300:'5-hour limit',10080:t('Weekly limit')};
const money=(value:number)=>`$${value.toFixed(2)}`;
const whole=(value:number)=>Number.isInteger(value)?`$${value}`:money(value);
/** A reset time: the time today, or the weekday and time later ("2:40 PM", "Wed 10:00 PM"). */
export function resetText(at:number|null,now=Date.now()):string{
  if(typeof at!=='number'||at<1e9||at>4e9)return '';
  const when=new Date(at*1000),time=formatDate(when,{hour:'numeric',minute:'2-digit'});
  return when.toDateString()===new Date(now).toDateString()?time:`${formatDate(when,{weekday:'short'})} ${time}`;
}
export const runsText=(count:number)=>t('{count} runs today',{count});
export function accountMeter(account:UsageAccount,now=Date.now()):{percent:number|null;value:string;caption:string;title:string}{
  const month=account.cost_month_usd!==null&&account.basis!=='spend_limit'?t('{count} runs this month, about {cost} by the provider’s estimate.',{count:account.runs_month,cost:money(account.cost_month_usd)}):t('{count} runs this month.',{count:account.runs_month});
  const title=`${runsText(account.runs_today).replace(/^./,c=>c.toUpperCase())}. ${month}`;
  if(account.percent!==null&&account.basis==='rate_limits'){
    const left=Math.max(0,100-account.percent),resets=resetText(account.resets_at,now);
    const label=WINDOWS[account.window_mins??0]??t('Limit');
    return {percent:left,value:t('{left}% left',{left}),caption:`${label}${resets?` · ${t('resets {when}',{when:resets})}`:''}${account.low?` · ${t('Low')}`:''}`,title};
  }
  if(account.percent!==null&&account.basis==='spend_limit'){
    const limit=account.monthly_limit_usd??0,spent=account.cost_month_usd??0;
    return {percent:Math.max(0,100-account.percent),value:t('{amount} left',{amount:money(Math.max(0,limit-spent))}),caption:`${t('{spent} of {limit} this month',{spent:money(spent),limit:whole(limit)})}${account.low?` · ${t('Low')}`:''}`,title};
  }
  const runs=runsText(account.runs_today).replace(/^./,c=>c.toUpperCase());
  return {percent:null,value:'',caption:`${runs}. ${account.provider==='claude'?t('Claude Code reports what’s left during its next run.'):t('This account reports no allowance to measure against.')}`,title};
}

const PROVIDERS:[string,string][]=[['claude','Claude'],['codex','Codex']];

/** Usage, as a popover beside the sidebar's usage lines (settings/usage-b.html): each account by its name, what's left,
 * the limit and when it resets, and today's runs. The account menu and ⌘K open the same popover here. Esc or a click
 * outside closes it, with focus back on what opened it. */
export function UsagePopover({open,onOpenChange,anchor,summary,failed,onRetry,onManage,returnFocus}:{open:boolean;onOpenChange:(open:boolean)=>void;
  anchor:RefObject<{getBoundingClientRect:()=>DOMRect}|null>;summary?:UsageSummary;failed:boolean;onRetry:()=>void;onManage:()=>void;returnFocus?:RefObject<HTMLElement|null>}){
  const heading=useRef<HTMLHeadingElement>(null);
  const accounts=summary?.accounts??[];
  return <Popover open={open} onOpenChange={onOpenChange}>
    <PopoverAnchor virtualRef={anchor as RefObject<{getBoundingClientRect:()=>DOMRect}>}/>
    <PopoverContent side="top" align="start" sideOffset={6} className="dv-usage-pop" aria-labelledby="dv-usage-pop-h" data-testid="usage-popover"
      onOpenAutoFocus={event=>{event.preventDefault();heading.current?.focus();}}
      onCloseAutoFocus={event=>{const target=returnFocus?.current;if(target?.isConnected){event.preventDefault();target.focus();}}}>
      <div className="dv-pop-head"><h2 className="dv-title" id="dv-usage-pop-h" ref={heading} tabIndex={-1} style={{outline:'none'}}>{t('Usage')}</h2><span className="dv-caption">{t('As each provider reported it')}</span></div>
      {failed?<div role="alert" className="dv-pop-grp"><p className="dv-card-text">{t('Dervo couldn’t load usage.')}</p><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" style={{alignSelf:'flex-start'}} onClick={onRetry}>{t('Try again')}</button></div>
      :!summary?<p className="dv-card-text" role="status">{t('Checking…')}</p>
      :!accounts.length?<p className="dv-card-text">{t('No accounts yet. Add one on Connected accounts.')}</p>
      :PROVIDERS.map(([provider,label])=>{
        const rows=accounts.filter(account=>account.provider===provider);
        if(!rows.length)return null;
        return <div key={provider} className="dv-pop-grp" role="group" aria-label={label}><span className="dv-label">{label}</span>
          {rows.map(account=>{
            const meter=accountMeter(account);
            const windows=(account.windows??[]).map(window=>accountMeter({...account,...window,low:window.percent>=80}));
            return <div key={account.profile_id} className="dv-u" data-low={account.low||undefined} title={meter.title}><b>{accountLabel(account)}</b>
              {windows.length>0?<div className="dv-usage-windows">{windows.map((window,index)=><Meter key={index} percent={window.percent!} value={window.value} caption={window.caption}/>)}</div>
                :meter.percent===null?<span/>:<Meter percent={meter.percent} value={meter.value} caption=""/>}
              <small>{windows.length>0?runsText(account.runs_today):meter.percent===null?meter.caption:`${meter.caption} · ${runsText(account.runs_today)}`}</small></div>;
          })}
        </div>;
      })}
      <div className="dv-pop-foot"><span className="dv-caption">{t('Esc closes')}</span><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" data-manage onClick={onManage}>{t('Connected accounts')}</button></div>
    </PopoverContent>
  </Popover>;
}
