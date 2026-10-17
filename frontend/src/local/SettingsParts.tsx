import {useState,type ReactNode} from 'react';
import {Check,Copy} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import './SettingsParts.css';
import {t} from '@/i18n';

/** A1 shared settings parts: one group with a title, rows of label, detail and control, and a copy button. Every settings
 * screen in the redesign is built from these so spacing, type and colour match in both themes. */
export function SettingsGroup({title,description,children,danger=false}:{title:string;description?:string;children:ReactNode;danger?:boolean}){
  return <section className={`dervo-settings-group${danger?' is-danger':''}`} aria-label={title}>
    <header><h2>{title}</h2>{description&&<p>{description}</p>}</header>
    <div className="dervo-settings-rows">{children}</div>
  </section>;
}

export function SettingsRow({label,detail,children,below}:{label:ReactNode;detail?:ReactNode;children?:ReactNode;below?:ReactNode}){
  return <div className="dervo-settings-row">
    <div className="dervo-settings-row-main">
      <div className="dervo-settings-row-text"><strong>{label}</strong>{detail&&<span>{detail}</span>}</div>
      {children&&<div className="dervo-settings-row-control">{children}</div>}
    </div>
    {below&&<div className="dervo-settings-row-below">{below}</div>}
  </div>;
}

export function CopyButton({text,label=t('Copy')}:{text:string;label?:string}){
  const [state,setState]=useState<'idle'|'copied'|'failed'>('idle');
  async function copy(){
    try{await navigator.clipboard.writeText(text);setState('copied');setTimeout(()=>setState('idle'),1600);}
    catch{setState('failed');}
  }
  return <Button type="button" size="sm" variant="ghost" onClick={()=>void copy()} aria-label={state==='copied'?t('Copied'):label}>
    {state==='copied'?<Check size={14} aria-hidden="true"/>:<Copy size={14} aria-hidden="true"/>}
    <span>{state==='copied'?t('Copied'):state==='failed'?t('Select and copy it instead'):label}</span>
  </Button>;
}
