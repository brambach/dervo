import {useEffect,useState} from 'react';
import {Check,Copy,Pencil} from 'lucide-react';
import {t} from '@/i18n';

/** Under a message in a thread, as the Claude app has them: Copy on every message, and Edit on yours, which puts the words
 * back in the composer to change and send again. They show on hover or focus, and Copy says Copied for a moment.
 * These replace the inherited chat bar (Reply, Copy link, reactions), whose Reply a coding thread ignored and whose
 * floating pill was clipped above the bubble (polisher, 8 Oct). */
/** The clipboard API, then the older copy command for a webview that refuses it. */
async function copyText(text:string){
 try{await navigator.clipboard.writeText(text);return;}catch{/* falls through */}
 const box=document.createElement('textarea');box.value=text;box.setAttribute('readonly','');box.style.position='fixed';box.style.opacity='0';
 const before=document.activeElement as HTMLElement|null;document.body.append(box);box.select();
 try{if(!document.execCommand('copy'))throw new Error();}finally{box.remove();before?.focus?.();}
}
export function MessageActions({text,own,onEdit}:{text:string;own:boolean;onEdit?:()=>void}){
 const [copied,setCopied]=useState<''|'done'|'failed'>('');
 useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(''),1500);return()=>clearTimeout(timer);},[copied]);
 if(!text.trim())return null;
 const copyName=copied==='done'?t('Copied'):copied==='failed'?t('Couldn’t copy'):t('Copy message');
 return <div className="dv-msg-actions" data-own={own||undefined} data-held={copied?true:undefined}>
  {/* The name stays put; the status line below says Copied, so it's announced once. */}
  <button type="button" className="dv-btn-icon" aria-label={t('Copy message')} title={copyName}
   onClick={()=>{void copyText(text).then(()=>setCopied('done')).catch(()=>setCopied('failed'));}}>
   {copied==='done'?<Check className="dv-copied" aria-hidden="true"/>:<Copy aria-hidden="true"/>}
  </button>
  <span className="sr-only" role="status">{copied==='done'?t('Copied'):copied==='failed'?t('Couldn’t copy'):''}</span>
  {own&&onEdit&&<button type="button" className="dv-btn-icon" aria-label={t('Edit and send again')} title={t('Edit and send again')} onClick={onEdit}>
   <Pencil aria-hidden="true"/>
  </button>}
 </div>;
}
