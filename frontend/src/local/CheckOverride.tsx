import {useCallback,useEffect,useRef,useState} from 'react';
import type {CheckEntry} from './api';
import {t} from '@/i18n';

/** Which of the grant's checks this request runs, chosen in the model picker beside the thinking level. Shown only when
 * the grant allows more than one; the first is the default. */
export function CheckOverride({checks,value,onChange}:{checks:CheckEntry[]|undefined;value:string;onChange:(v:string)=>void}){
  if(!checks||checks.length<2)return null;
  return <div className="dervo-model-effort" data-testid="check-override">
    <p className="dervo-model-heading">{t('Check')} <span>{t('for this request')}</span></p>
    <div className="dervo-model-levels" role="radiogroup" aria-label={t('Check for this request')}>
      <button type="button" role="radio" aria-checked={value==='default'} title={t('{label} on {test_file}',{label:checks[0].label,test_file:checks[0].test_file??''})} onClick={()=>onChange('default')}>{checks[0].label} (default)</button>
      {checks.slice(1).map(entry=><button key={entry.profile} type="button" role="radio" aria-checked={value===entry.profile} title={t('{label} on {test_file}',{label:entry.label,test_file:entry.test_file??''})} onClick={()=>onChange(entry.profile)}>{entry.label}</button>)}
    </div>
  </div>;
}
/** The check's label for the model chip when it isn't the default. */
export function checkChoiceLabel(checks:CheckEntry[]|undefined,value:string):string|null{
  if(value==='default'||!checks)return null;
  return checks.find(entry=>entry.profile===value)?.label??null;
}
/** One value per current conversation, reset on a conversation switch and after a send from that conversation. */
export function useCheckOverride(channelId:string):{value:string;setValue:(v:string)=>void;settle:(sentFrom:string)=>void;payload:()=>string|undefined} {
  const [value,setValue]=useState('default');
  const current=useRef(channelId);
  useEffect(()=>{current.current=channelId;setValue('default');},[channelId]);
  const settle=useCallback((sentFrom:string)=>{if(current.current===sentFrom)setValue('default');},[]);
  const payload=useCallback(()=>value==='default'?undefined:value,[value]);
  return {value,setValue,settle,payload};
}
