import {useEffect,useState} from 'react';
import {QRCodeSVG} from 'qrcode.react';
import {SettingRow,SettingsSection,Switch} from './DervoControls';
import {DEFAULT_RELAY,phoneControl,type PhoneControl} from './phoneLink';
import {t} from '@/i18n';

/** Phone, on Preferences: pair a phone by showing a one-time code, see paired phones and remove one. Functional first
 * pass; the "Make Dervo's design exceptional" thread owns how it looks. */
/** Relays Phone settings accepts: Dervo's own, once it's deployed, and, for the app's own link only, a development relay
 * on this Mac. The window's content security policy (tauri.conf.json connect-src) allows only Dervo's. */
export function allowedRelay(url:string,native:boolean):boolean{
  try{
    const parsed=new URL(url);
    if(parsed.protocol==='wss:')return parsed.host==='relay.trydervo.com';
    // A development relay on this Mac: the app's own link can reach it; this window can't.
    return native&&parsed.protocol==='ws:'&&['127.0.0.1','localhost'].includes(parsed.hostname)&&parsed.pathname==='/';
  }catch{return false;}
}

export function PhoneSettings(){
  const [control,setControl]=useState<PhoneControl|null>(null);
  const [,tick]=useState(0);
  useEffect(()=>{
    let alive=true,stop=()=>{};
    void phoneControl().then(next=>{if(!alive)return;setControl(next);setDraft(next.status.relay??DEFAULT_RELAY);stop=next.subscribe(()=>tick(n=>n+1));});
    const timer=setInterval(()=>tick(n=>n+1),1000);
    return ()=>{alive=false;stop();clearInterval(timer);};
  },[]);
  const status=control?.status;
  const relay=status?.relay??'';
  const [draft,setDraft]=useState('');
  const [code,setCode]=useState<{link:string;expires:number}|null>(null);
  const [error,setError]=useState('');
  const phones=status?.phones??[];
  const asking=status?.waiting??null;
  // A code only lasts until a phone uses it: once the phone count goes up, take it off the screen.
  const [pairedAtShow,setPairedAtShow]=useState(0);
  const showing=code&&code.expires>Date.now()&&phones.length===pairedAtShow&&!asking?code:null;
  const fail=(failure:unknown)=>setError(failure instanceof Error?failure.message:String(failure));

  async function show(){
    if(!control)return;
    setError('');
    try{
      // Pairing works straight away on Dervo's relay; the address below is only for running your own.
      if(!control.status.relay){await control.setRelay(DEFAULT_RELAY);setDraft(DEFAULT_RELAY);}
      const next=await control.offer();
      setPairedAtShow(control.status.phones.length);
      setCode(next);
    }catch(failure){setCode(null);fail(failure);}
  }
  function saveRelay(){
    if(!control)return;
    const next=draft.trim().replace(/\/+$/,'');
    if(next&&!allowedRelay(next,control.native)){setError(control.native?t('Use Dervo’s relay, or a development relay on this Mac.'):t('Use Dervo’s relay.'));return;}
    if(phones.length&&!window.confirm(t('Changing the relay removes your paired phones. You can pair them again.')))return;
    setError('');setCode(null);setDraft(next);
    void control.setRelay(next||null).then(()=>tick(n=>n+1),fail);
  }
  const minutes=showing?Math.max(1,Math.ceil((showing.expires-Date.now())/60000)):0;

  return <SettingsSection id="settings-phone" title={t('Phone')} caption={t('Everything stays on this Mac. Your phone gets a sealed copy of Home.')}>
    <SettingRow name={t('Pair a phone')} line={t('Open Dervo on your phone and scan the code. It works once and expires in 10 minutes.')}
      end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!control} onClick={()=>void show()}>{showing?t('New code'):t('Show code')}</button>}
      below={showing&&<div style={{display:'flex',gap:16,alignItems:'center'}}>
        <div style={{background:'#fff',padding:12,borderRadius:12}}><QRCodeSVG value={showing.link} size={176} level="M" title={t('Pairing code')}/></div>
        <p className="dv-set-line">{t('Expires in {minutes} min.',{minutes})}</p>
      </div>}/>
    {asking&&control&&<SettingRow name={t('{device} wants to pair',{device:asking.device})}
      line={t('Allow it only if your phone shows {digits}.',{digits:`${asking.digits.slice(0,3)} ${asking.digits.slice(3)}`})}
      end={<span style={{display:'flex',gap:8}}>
        <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>{void control.deny().catch(fail);setCode(null);}}>{t('Don’t allow')}</button>
        <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>void control.allow(asking.pk).then(()=>setCode(null),fail)}>{t('Allow')}</button>
      </span>}/>}
    {phones.map(phone=><SettingRow key={phone.pk} name={phone.device}
      line={phone.approvals?t('Paired. It can answer agents, approve runs and accept results.'):t('Paired. It can answer agents and send messages.')}
      end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void control?.unpair(phone.pk).then(()=>tick(n=>n+1),fail)}>{t('Remove')}</button>}
      below={control&&<div style={{display:'flex',gap:12,alignItems:'center',justifyContent:'space-between'}}>
        <p className="dv-set-line" id={`phone-approvals-${phone.pk}`}>{t('Approve from this phone. It can start a run you approve and accept a finished result, which commits it on this Mac.')}</p>
        <Switch checked={phone.approvals===true} label={t('Approve from {device}',{device:phone.device})} describedBy={`phone-approvals-${phone.pk}`}
          onChange={on=>{setError('');void control.setApprovals(phone.pk,on).then(()=>tick(n=>n+1),fail);}}/>
      </div>}/>)}
    {relay&&<SettingRow name={t('Connection')} line={status?.problem?t('Dervo is connected to this pairing from somewhere else, so this one stopped.'):status?.connected?t('Connected'):t('Not connected')}/>}
    <SettingRow name={t('Relay address')} line={t('Advanced. Leave this as Dervo’s relay unless you run your own.')}
      end={<span style={{display:'flex',gap:8}}>
        <input className="dv-input" aria-label={t('Relay address')} value={draft} placeholder={DEFAULT_RELAY} onChange={event=>setDraft(event.target.value)} style={{width:220}}/>
        <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!control||draft.trim().replace(/\/+$/,'')===(relay||DEFAULT_RELAY)} onClick={saveRelay}>{t('Save')}</button>
      </span>}/>
    {error&&<p className="dv-why" role="alert">{error}</p>}
  </SettingsSection>;
}
