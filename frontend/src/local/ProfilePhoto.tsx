import {useEffect,useRef,useState} from 'react';
import {UserRound} from 'lucide-react';
import type {DervoAccountStatus} from './api';
import {t} from '@/i18n';

export function photoImage(data:DervoAccountStatus|undefined):string|undefined {
 const value=data?.state==='signed_in'?data.photo?.data_url:null;
 return typeof value==='string'&&value.length<120000&&/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value)?value:undefined;
}
export function ProfileAvatar({image,name,large=false}:{image?:string;name?:string;large?:boolean}){
 const initials=name?.trim().split(/\s+/).slice(0,2).map(word=>word[0]).join('').toUpperCase();
 return <span className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary/10 font-semibold text-primary ${large?'size-20 text-xl ring-4 ring-background':'size-8 text-xs'}`}>
  {image?<img src={image} alt="" className="size-full object-cover"/>:initials?<span aria-hidden="true">{initials}</span>:<UserRound aria-hidden="true" className={large?'size-7':'size-4'}/>}
 </span>;
}

export async function preparePhoto(file:File):Promise<string>{
 if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>8*1024*1024)throw Error(t('Choose a PNG, JPEG or WebP image under 8 MB.'));
 const bitmap=await createImageBitmap(file);
 try{
  if(!bitmap.width||!bitmap.height||bitmap.width>12000||bitmap.height>12000)throw Error(t('Choose a smaller photo.'));
  const canvas=document.createElement('canvas');canvas.width=canvas.height=128;
  const context=canvas.getContext('2d');if(!context)throw Error(t('The photo preview couldn’t open.'));
  const side=Math.min(bitmap.width,bitmap.height);
  context.drawImage(bitmap,(bitmap.width-side)/2,(bitmap.height-side)/2,side,side,0,0,128,128);
  const image=canvas.toDataURL('image/png');
  if(!image.startsWith('data:image/png;base64,')||image.length>110000)throw Error(t('Choose a smaller photo.'));
  return image;
 }finally{bitmap.close();}
}

export function ProfilePhoto({data,busy,onAction}:{data:DervoAccountStatus;busy:boolean;onAction:(op:string,args:Record<string,unknown>)=>Promise<unknown>}){
 const [candidate,setCandidate]=useState<string>(),[preparing,setPreparing]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState<string>();
 const input=useRef<HTMLInputElement>(null),revision=useRef(0);
 const subject=data.identity?.subject;
 useEffect(()=>{revision.current++;setCandidate(undefined);setError(undefined);setPreparing(false);setSaving(false);return()=>{revision.current++;};},[subject]);
 useEffect(()=>{
  if(!saving||data.can_poll)return;
  setSaving(false);
  if(data.photo?.state==='ready')setCandidate(undefined);
  else setError(data.photo?.state==='error'?undefined:t('Your photo hasn’t been confirmed. Check photo before trying again.'));
 },[saving,data.can_poll,data.photo]);
 async function choose(file:File|undefined){
  if(!file)return;
  const current=++revision.current;setPreparing(true);setError(undefined);
  try{const next=await preparePhoto(file);if(current===revision.current)setCandidate(next);}
  catch{if(current===revision.current)setError(t('This photo couldn’t open. Choose a PNG, JPEG or WebP image under 8 MB.'));}
  finally{if(current===revision.current)setPreparing(false);}
 }
 async function save(){
  if(!candidate||!subject||busy||preparing)return;
  const current=revision.current;
  const result=await onAction('dervo_account_save_photo',{subject,png:candidate.split(',')[1]});
  if(result&&current===revision.current)setSaving(true);
 }
 const current=photoImage(data),disabled=busy||preparing||saving||!data.can_save_profile;
 const shown=candidate??current,initials=data.identity?.display_name?.trim().split(/\s+/).slice(0,2).map(word=>word[0]).join('').toUpperCase();
 // One row on Account (settings/account.html): the avatar, what the photo is for, and the one thing to do next. A chosen
 // photo stays here until Save photo; Cancel puts the old one back.
 return <>
  <div className="dv-set dv-lead">
   <span className="dv-avatar dv-avatar-lg" aria-hidden="true">{shown?<img src={shown} alt=""/>:initials||<UserRound size={24}/>}</span>
   <div><p className="dv-set-name">{t('Photo')}</p><p className="dv-set-line">{candidate?t('Square crop. Saved only when you choose Save photo.'):t('Only you see it. It’s kept with your Dervo account.')}</p></div>
   <div className="dv-set-end">
    {candidate?<><button type="button" className="dv-btn dv-btn-quiet" disabled={busy||saving} onClick={()=>{revision.current++;setCandidate(undefined);}}>{t('Cancel')}</button>
      <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={disabled} onClick={()=>void save()}>{saving||busy?t('Saving…'):t('Save photo')}</button></>
     :<>{current&&<button type="button" className="dv-btn dv-btn-quiet" disabled={disabled} onClick={()=>void onAction('dervo_account_remove_photo',{subject})}>{t('Remove photo')}</button>}
      <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={disabled} onClick={()=>input.current?.click()}>{preparing?t('Preparing photo…'):current?t('Change photo'):t('Choose photo')}</button></>}
   </div>
  </div>
  <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" aria-label={t('Choose profile photo')} className="hidden" hidden disabled={disabled} onChange={event=>{const file=event.target.files?.[0];event.target.value='';void choose(file);}}/>
  {(data.photo?.state==='error'||(data.photo?.state==='cached'&&!!data.photo.error))&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{t('Your photo couldn’t be confirmed.')}</p></div>
   <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={disabled} onClick={()=>void onAction('dervo_account_photo',{subject})}>{t('Check photo')}</button></div></div>}
  {error&&<p role="alert" className="dv-field-note">{error}</p>}
 </>;
}
