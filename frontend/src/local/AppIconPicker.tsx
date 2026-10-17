import {useEffect, useRef, useState, type KeyboardEvent} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {local} from './api';
import {SettingsSection, nextIndex} from './DervoControls';
import './AppIconPicker.css';
import {t} from '@/i18n';

/** One icon choice as the host lists it (backend/app_icons.py): nine for everyone, and Obsidian for founders. */
export type IconChoice={id:string;name:string;founder:boolean;available:boolean};
export type IconStatus={founder:'founder'|'not_founder'|'checking'|'unknown'|'signed_out';detail:string;icons:IconChoice[]};
type Chosen={icon:string;dock:boolean};

/** The picture for an icon: the 128 px large art scripts/build-icons.py writes to frontend/public/app-icons. */
export const iconImage=(id:string)=>`/app-icons/${encodeURIComponent(id)}.png`;

/** The line under the picker: where the choice shows, and what a development run can't do. */
export function whereLine(dock:boolean){
  return dock?t('Shows in the Dock and the app switcher, and stays in the Dock while Dervo is closed. Finder, Spotlight and Launchpad keep the original.')
    :t('This build has no Dock tile plug-in, so the choice shows only while Dervo is open.');
}

/** App icon, in Settings > Preferences (beta.15, group 7; app-icons.html and founder-icons.html). Nine icons are free for
 * everyone. Obsidian is the founder icon: testers approved before the public launch get it for good, on any Mac they sign
 * in on. Choosing one asks the native app, which checks with the host before it saves anything (src/app_icon.rs). The
 * tiles are one radio group: arrows move and choose, as Theme's do. Icons this account can't use are skipped. */
export function AppIconPicker(){
  const [status,setStatus]=useState<IconStatus|null>(null);
  const [chosen,setChosen]=useState<Chosen|null>(null);
  const [error,setError]=useState(''),[saving,setSaving]=useState<string|null>(null);
  const tiles=useRef<(HTMLButtonElement|null)[]>([]);
  useEffect(()=>{
    let disposed=false,timer:ReturnType<typeof setTimeout>;
    async function read(){
      try{
        const next=await local<IconStatus>('app_icon_status');
        if(disposed)return;
        setStatus(next);
        // The founder check runs on its own; read again shortly while it does.
        if(next.founder==='checking')timer=setTimeout(()=>void read(),700);
      }catch{if(!disposed)setError(t('Dervo couldn’t list the app icons. Close Settings and open it again.'));}
    }
    void read();
    invoke<Chosen>('app_icon_get').then(next=>{if(!disposed)setChosen(next);}).catch(()=>{if(!disposed)setChosen({icon:'cream',dock:false});});
    return ()=>{disposed=true;clearTimeout(timer);};
  },[]);
  const icons=status?.icons??[];
  const selected=chosen?.icon??'cream';
  async function choose(icon:IconChoice){
    if(!icon.available||icon.id===selected||saving)return;
    setSaving(icon.id);setError('');
    try{setChosen(await invoke<Chosen>('app_icon_set',{icon:icon.id}));}
    catch(failure){setError(String(failure).replace(/^Error: /,'')||t('Dervo couldn’t change the icon.'));}
    finally{setSaving(null);}
  }
  function keys(event:KeyboardEvent,index:number){
    const next=nextIndex(event.key,index,icons.map(icon=>!icon.available));
    if(next<0)return;
    event.preventDefault();tiles.current[next]?.focus();void choose(icons[next]);
  }
  const tile=(icon:IconChoice,index:number)=><button key={icon.id} ref={element=>{tiles.current[index]=element;}} type="button" role="radio"
    className="dv-icon-tile" data-founder={icon.founder||undefined} aria-checked={selected===icon.id} aria-disabled={!icon.available||undefined}
    tabIndex={selected===icon.id?0:-1} title={icon.available?undefined:status?.detail} onClick={()=>void choose(icon)} onKeyDown={event=>keys(event,index)}>
    <img src={iconImage(icon.id)} alt="" width={56} height={56} draggable={false}/>
    <span>{saving===icon.id?t('Changing…'):icon.name}</span>
  </button>;
  const regular=icons.filter(icon=>!icon.founder), founders=icons.filter(icon=>icon.founder);
  return <SettingsSection title={t('App icon')} caption={t('Dock')} id="dv-pref-icon">
    <div className="dv-icon-picker" role="radiogroup" aria-labelledby="dv-pref-icon" aria-describedby="dv-pref-icon-where">
      <div className="dv-icon-grid">{regular.map(icon=>tile(icon,icons.indexOf(icon)))}</div>
      {founders.length>0&&<div className="dv-icon-founders" data-state={status?.founder}>
        <div className="dv-icon-founders-head"><b>{t('Founder')}</b><span>{status?.detail}</span></div>
        <div className="dv-icon-grid">{founders.map(icon=>tile(icon,icons.indexOf(icon)))}</div>
      </div>}
    </div>
    <p className="dv-field-note" id="dv-pref-icon-where">{chosen?whereLine(chosen.dock):''}</p>
    {error&&<p className="dv-field-note" role="alert">{error}</p>}
  </SettingsSection>;
}
