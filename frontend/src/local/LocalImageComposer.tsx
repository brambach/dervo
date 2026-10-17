import {useCallback,useEffect,useRef,useState} from 'react';
import {useQueries,useQueryClient} from '@tanstack/react-query';
import {ImagePlus,FileText,X,RotateCcw} from 'lucide-react';
import {DvComposer,type DvComposerProps} from './DvComposer';
import {Button} from '@/shared/ui/button';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/shared/ui/dialog';
import {imageRequest,prepareAttachment,DOCUMENT_ACCEPT,uploadImage,pendingImageRequest,thumbnailOf,tilePreview,type ImageScope,type LocalImage} from './imageInput';
import type {ComposerTextPaste,InsertComposerTextPaste} from '@/features/messages/ui/composerTextPaste';
import {LONG_PASTE_CHARACTERS,loadPastedText,savePastedText,pastedTextBytes,pastedTextProblem,type PastedText} from './pastedText';
import {formatNumber,t} from '@/i18n';
import {isBusyAnswer} from './hostBusy';

type Props=Omit<DvComposerProps,'onSend'|'channelId'>&ImageScope&{
 focusWhenReady?:boolean;
 /** Hands over a way to put words back in the box (Edit on a sent message) in place of the draft, which ⌘Z restores. */
 onInsertReady?:(insert:((text:string)=>boolean)|null)=>void;
 accessReady?:boolean;
 onSend:(text:string,mentions:string[],media:string[][]|undefined,images:string[])=>Promise<void>;
};
type Preview=LocalImage&{dataUrl?:string;textPreview?:string};
type Retry={name:string;bytes:Uint8Array;clientId:string;thumb?:Promise<string|null>};
export function LocalImageComposer({projectId,channelId,onSend,focusWhenReady=false,accessReady=true,...props}:Props){
 return <ScopedImageComposer key={JSON.stringify([projectId,channelId])} {...props} projectId={projectId} channelId={channelId} onSend={onSend} focusWhenReady={focusWhenReady} accessReady={accessReady}/>;
}
function ScopedImageComposer({projectId,channelId,onSend,focusWhenReady=false,accessReady=true,onInsertReady,...props}:Props){
 const container=useRef<HTMLDivElement>(null),focusAttempted=useRef(false);
 const scope={projectId,channelId},input=useRef<HTMLInputElement>(null),alive=useRef(true),working=useRef(false);
 const retried=useRef(0),loadedRef=useRef(false),queryClient=useQueryClient();
 const [images,setImages]=useState<Preview[]>([]),[loaded,setLoadedState]=useState(false),[busy,setBusy]=useState(false),[sending,setSending]=useState(false),[uploading,setUploading]=useState(false),[waiting,setWaiting]=useState(false),[error,setError]=useState(''),[retry,setRetryState]=useState<Retry|undefined>();
 const retryRef=useRef<Retry|undefined>(undefined);
 function setRetry(next:Retry|undefined|((current:Retry|undefined)=>Retry|undefined)){const value=typeof next==='function'?next(retryRef.current):next;retryRef.current=value;setRetryState(value);}
 const setLoaded=(value:boolean)=>{loadedRef.current=value;setLoadedState(value);};
 const [pasted,setPasted]=useState<PastedText|null>(()=>loadPastedText(scope));
 const [autoSuppressed,setAutoSuppressed]=useState(!!pasted);
 const pastedRef=useRef(pasted);pastedRef.current=pasted;
 const insertPaste=useRef<InsertComposerTextPaste|null>(null);
 const insertReady=useRef(onInsertReady);insertReady.current=onInsertReady;
 // No Edit into a box that's waiting (access, a paste card, a send): the words would land where nothing can send them.
 const editBlocked=useRef(false);
 const pasteReady=useCallback((insert:InsertComposerTextPaste|null)=>{insertPaste.current=insert;insertReady.current?.(insert?(text:string)=>!editBlocked.current&&insert({text,from:0,to:0,document:'',replace:true}):null);},[]);
 function rememberPaste(value:PastedText|null){savePastedText(scope,value);pastedRef.current=value;setPasted(value);}
 // A draft handed over to send by itself (from the project room) waits until this composer could send it by hand:
 // its images have loaded, nothing is uploading and access is ready. The editor then mounts once more with the
 // auto-submit set, because the composer's auto-submit fires only on mount, and keeps that mount afterwards.
 const autoReady=!!props.autoSubmitDraftKey&&loaded&&accessReady&&!busy&&!uploading&&!retry&&!pasted&&!autoSuppressed&&!props.disabled;
 const [autoMount,setAutoMount]=useState(false);
 useEffect(()=>{if(autoReady)setAutoMount(true);},[autoReady]);
 const access=useRef(accessReady),reloadWhenIdle=useRef(accessReady);access.current=accessReady;
 useEffect(()=>{
  if(!focusWhenReady||focusAttempted.current||props.disabled||props.isSending||busy||!loaded||!accessReady)return;
  const frame=requestAnimationFrame(()=>{
   if(!container.current||container.current.closest('[hidden]')||document.querySelector('[role="dialog"][data-state="open"]'))return;
   focusAttempted.current=true;
   const active=document.activeElement;
   if(active&&active!==document.body&&!container.current.contains(active))return;
   container.current.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus({preventScroll:true});
  });
  return()=>cancelAnimationFrame(frame);
 },[focusWhenReady,props.disabled,props.isSending,busy,loaded,accessReady]);
 const imagesRef=useRef(images);imagesRef.current=images;
 const pending=pendingImageRequest(scope);
 // Uploading doesn't lock the composer: you can type, add more files, remove a tile (which cancels its upload) and send,
 // which waits for the uploads and then goes, as the Claude app does (send pipeline, 9 Oct). Anything else does.
 const locked=!accessReady||!!props.disabled||!!props.isSending||busy||!!pending;
 const waiters=useRef<(()=>void)[]>([]);
 function release(){working.current=false;for(const wake of waiters.current.splice(0))wake();}
 /** Each file still uploading, or waiting to, by its tile's id (``local:`` + client id, then the host's id once it starts). */
 const clientOf=useRef(new Map<string,string>()),cancelled=useRef(new Set<string>()),draining=useRef(false);
 editBlocked.current=locked||!!pasted||!loaded;
 async function load(){
  if(working.current||!access.current)return;
  working.current=true;setBusy(true);setLoaded(false);
  try{
   const result=await imageRequest<{images:LocalImage[]}>(scope,'image_drafts');
   if(!alive.current||!access.current)return;
   const pending=pendingImageRequest(scope);
   const rows:LocalImage[]=pending?pending.images.map(id=>result.images.find(row=>row.id===id)??{id,name:t('Attached file'),state:'ready',size:0,received:0,width:null,height:null}):result.images;
   setImages(rows);setError('');
   for(const row of rows){
    if(!alive.current||!access.current)return;
    if(row.state==='ready'){
     const preview=await tilePreview(scope,row);
     if(alive.current&&access.current)setImages(current=>current.map(image=>image.id===row.id?preview:image));
    }
   }
   if(alive.current&&access.current){retried.current=0;setLoaded(true);}
  }catch(failure){
   // The host was busy with something long when the thread opened (a busy answer): ask again shortly, a few times, rather
   // than leaving the composer locked behind "Attached files didn’t load." until you press Try again.
   if(isBusyAnswer(failure)&&retried.current<3&&alive.current&&access.current){retried.current++;setTimeout(()=>{if(alive.current&&access.current&&!loadedRef.current)void load();},1500);}
   else if(alive.current&&access.current)setError(t('Attached files didn’t load.'));
  }
  finally{release();if(alive.current)setBusy(false);}
 }
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{
  reloadWhenIdle.current=accessReady;
  if(!accessReady){setLoaded(false);setError('');}
 },[accessReady]);
 useEffect(()=>{
  if(accessReady&&!busy&&!uploading&&!working.current&&reloadWhenIdle.current){reloadWhenIdle.current=false;void load();}
 },[accessReady,busy,uploading]);
 // A chosen file shows at once as a tile with its own picture (``local:`` + its client id), as the Claude app does; the
 // host's row takes that tile's place, keeping the picture, when the upload starts (polisher, 8 Oct).
 function progress(row:LocalImage,clientId?:string){
  if(clientId&&cancelled.current.has(clientId))return;
  if(clientId)clientOf.current.set(row.id,clientId);
  if(alive.current)setImages(current=>{
  const local=clientId?current.findIndex(image=>image.id===`local:${clientId}`):-1;
  if(local>=0)return current.map((image,index)=>index===local?{...row,...(image.dataUrl?{dataUrl:image.dataUrl}:{})}:image);
  return current.some(image=>image.id===row.id)?current.map(image=>image.id===row.id?{...image,...row}:image):[...current,row];});}
 const queued=useRef<{file:File;name:string;clientId:string;tile:Preview}[]>([]);
 const objectUrls=useRef<string[]>([]);
 useEffect(()=>()=>{for(const url of objectUrls.current)URL.revokeObjectURL(url);},[]);
 // A tile's own picture is let go once the host's preview takes over, or its upload is dropped.
 function forgetUrl(url?:string){if(!url?.startsWith('blob:'))return;URL.revokeObjectURL(url);objectUrls.current=objectUrls.current.filter(kept=>kept!==url);}
 // A long paste that became a file, kept here until it's sent, so removing its tile can put the words back.
 const pasteTexts=useRef(new Map<string,{name:string;text:string}>());
 const [removedPaste,setRemovedPaste]=useState<{name:string;text:string}|null>(null);
 async function upload(candidate:Retry){
  setRetry(candidate);
  const row=await uploadImage(scope,candidate.name,candidate.bytes,candidate.clientId,()=>alive.current&&access.current&&!cancelled.current.has(candidate.clientId),row=>{
   progress(row,candidate.clientId);
   if(alive.current&&access.current&&pastedRef.current?.id===candidate.clientId)rememberPaste({...pastedRef.current,attachmentId:row.id});
  });
  forgetClient(candidate.clientId);
  if(!alive.current)return;
  setRetry(undefined);
  const paste=pastedRef.current;if(paste?.id===candidate.clientId){pasteTexts.current.set(row.id,{name:paste.name,text:paste.text});rememberPaste(null);}
  // The upload is done and can be sent; the host's small picture takes the tile over when it's ready, without holding
  // the next file or the send (reviewer, 9 Oct). Until then the tile keeps its own picture.
  void (async()=>{
   const preview=await tilePreview(scope,row,await candidate.thumb?.catch(()=>null)).catch(()=>null);
   if(!alive.current||!preview)return;
   const local=imagesRef.current.find(image=>image.id===row.id)?.dataUrl;
   setImages(current=>current.map(image=>image.id===row.id?preview:image));forgetUrl(local);
  })();
 }
 /** Files chosen, pasted or dropped, even while another is uploading: each gets its tile now and joins the queue, rather than
  * being dropped because the composer was busy (reviewer, 8 Oct). */
 async function choose(files:File[]){
  if(!files.length||pasted||!loaded||!accessReady||props.disabled||props.isSending||pending)return;
  if(imagesRef.current.length+files.length>4){setError(t('Attach up to four files. Remove one before adding another.'));return;}
  const rows=files.map(file=>{
   const clientId=crypto.randomUUID();
   // Keep only the basename; the host checks it again.
   const name=file.name.replace(/[\\/\x00-\x1f]/g,'_').slice(0,60)||'Screenshot.png';
   let dataUrl:string|undefined;
   if(['image/png','image/jpeg','image/webp'].includes(file.type)&&file.size<=8*1024*1024){try{dataUrl=URL.createObjectURL(file);objectUrls.current.push(dataUrl);}catch{/* no preview, still uploads */}}
   return {file,name,clientId,tile:{id:`local:${clientId}`,name,state:'uploading' as const,size:file.size,received:0,width:null,height:null,dataUrl}};
  });
  imagesRef.current=[...imagesRef.current,...rows.map(row=>row.tile)];
  setImages(current=>[...current,...rows.map(row=>row.tile)]);setError('');
  for(const row of rows)clientOf.current.set(row.tile.id,row.clientId);
  queued.current.push(...rows);
  if(!working.current)await drain();
 }
 async function drain(){
  working.current=true;draining.current=true;setUploading(true);
  try{
   while(queued.current.length){
    const next=queued.current[0];
    if(!alive.current)return;
    try{
     const bytes=await prepareAttachment(next.file);
     if(!alive.current)return;
     // Made while the chunks go, so the first one isn't held for it.
     const thumb=cancelled.current.has(next.clientId)?undefined:thumbnailOf(next.file);
     if(!cancelled.current.has(next.clientId))await upload({name:next.name,bytes,clientId:next.clientId,thumb});
    }catch(e){if(!cancelled.current.has(next.clientId))throw e;}
    queued.current=queued.current.filter(row=>row!==next);
    forgetClient(next.clientId);
   }
  }catch(e){
   // The one that failed keeps its host row and Retry; the ones behind it never started, so their tiles go.
   const dropped=queued.current.splice(0).map(row=>{forgetUrl(row.tile.dataUrl);return row.tile.id;});
   if(alive.current){setImages(current=>current.filter(row=>!dropped.includes(row.id)));setError(e instanceof Error?e.message:t('The file couldn’t upload. Try again.'));}
  }
  finally{draining.current=false;release();if(alive.current)setUploading(false);}
 }
 async function retryUpload(){
  if(!retry||working.current||locked)return;
  working.current=true;setBusy(true);setError('');
  try{await upload(retry);}catch(e){if(alive.current)setError(e instanceof Error?e.message:t('The file couldn’t upload.'));}
  finally{release();if(alive.current)setBusy(false);}
 }
 async function cancelFailedUpload(){
  if(!retry||working.current||locked)return;
  working.current=true;setBusy(true);setError('');
  try{
   const result=await imageRequest<{id:string|null}>(scope,'image_cancel',{clientId:retry.clientId});
   if(alive.current){setImages(current=>current.filter(row=>row.id!==result.id));setRetry(undefined);if(pastedRef.current?.id===retry.clientId)rememberPaste({...pastedRef.current,id:crypto.randomUUID(),attachmentId:undefined});}
  }catch{if(alive.current)setError(t('The upload couldn’t be cancelled. Try again before sending.'));}
  finally{release();if(alive.current)setBusy(false);}
 }
 function forgetClient(clientId:string){for(const [tile,client] of clientOf.current)if(client===clientId)clientOf.current.delete(tile);}
 /** A tile still uploading, or waiting to: it goes now, the upload stops before its next chunk, and the host records the
  * cancel so nothing it already has can finish or be sent (image_cancel). The files behind it carry on. */
 async function cancelUpload(id:string,clientId:string){
  cancelled.current.add(clientId);
  queued.current=queued.current.filter(row=>row.clientId!==clientId||row===queued.current[0]);
  for(const row of imagesRef.current)if(row.id===id||row.id===`local:${clientId}`)forgetUrl(row.dataUrl);
  setImages(current=>current.filter(row=>row.id!==id&&row.id!==`local:${clientId}`));
  setRetry(current=>current?.clientId===clientId?undefined:current);setError('');
  if(pastedRef.current?.id===clientId)rememberPaste({...pastedRef.current,id:crypto.randomUUID(),attachmentId:undefined});
  requestAnimationFrame(()=>{if(!document.activeElement||document.activeElement===document.body)container.current?.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus();});
  try{await imageRequest(scope,'image_cancel',{clientId});}
  catch{if(alive.current)setError(t('The file couldn’t be removed. Check your attachments before sending.'));}
 }
 async function remove(id:string){
  if(locked)return;
  const uploadingClient=clientOf.current.get(id);
  if(uploadingClient)return cancelUpload(id,uploadingClient);
  // A finished tile while others upload goes without holding up the queue.
  const alongside=draining.current;
  if(working.current&&!alongside)return;
  if(!alongside){working.current=true;setBusy(true);}
  setError('');
  try{await imageRequest(scope,'image_discard',{id});if(alive.current){forgetUrl(imagesRef.current.find(row=>row.id===id)?.dataUrl);setImages(current=>current.filter(row=>row.id!==id));
   const words=pasteTexts.current.get(id);if(words){pasteTexts.current.delete(id);setRemovedPaste(words);}
   // The tile and its button go: keep the keyboard in the composer rather than on the page.
   requestAnimationFrame(()=>{if(!document.activeElement||document.activeElement===document.body)container.current?.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus();});if(!alongside)setRetry(undefined);if(pastedRef.current?.attachmentId===id)rememberPaste({...pastedRef.current,id:crypto.randomUUID(),attachmentId:undefined});}}
  catch{if(alive.current)setError(t('The file couldn’t be removed. Check your attachments before sending.'));}
  finally{if(!alongside){release();if(alive.current)setBusy(false);}}
 }
 async function send(text:string,mentions:string[],media?:string[][]){
  if(!access.current)throw Error(t('Check your Dervo account in Settings before sending.'));
  if(pastedRef.current)throw Error(t('Choose how to use your pasted text before sending.'));
  if(sending)throw Error(t('This message is already sending.'));
  // A send during an upload waits for it (and anything else in hand) and then goes; the tiles show the progress.
  if(working.current){
   setWaiting(true);
   try{
    // Then one turn for the tiles' last updates to land, so the check below reads them.
    while(working.current){await new Promise<void>(wake=>waiters.current.push(wake));await new Promise(wake=>setTimeout(wake,0));}
   }
   finally{if(alive.current)setWaiting(false);}
  }
  if(!alive.current)throw Error(t('Finish uploading your attachments, or remove an interrupted upload before sending.'));
  if(!loadedRef.current||working.current||imagesRef.current.some(row=>row.state!=='ready')||retryRef.current)throw Error(t('Finish uploading your attachments, or remove an interrupted upload before sending.'));
  const captured=imagesRef.current,sent=captured.map(row=>row.id);
  // The thread shows what was sent straight away: SentImages reads these instead of asking the host again.
  for(const row of captured)if(row.dataUrl||row.textPreview){
   const key=['sent-attachment',projectId,channelId,row.id];
   queryClient.setQueryData(key,{name:row.name,thumb:row.dataUrl});
   if(row.dataUrl)void shrinkPicture(row.dataUrl).then(thumb=>queryClient.setQueryData(key,{name:row.name,thumb}));
  }
  for(const id of sent)pasteTexts.current.delete(id);setRemovedPaste(null);
  working.current=true;setBusy(true);setSending(true);
  setImages(current=>current.filter(row=>!sent.includes(row.id)));
  try{await onSend(text,mentions,media,sent);}
  catch(failure){if(alive.current)setImages(current=>[...captured,...current.filter(row=>!sent.includes(row.id))]);throw failure;}
  finally{release();if(alive.current){setBusy(false);setSending(false);}}
 }
 function deferPaste(paste:ComposerTextPaste):boolean{
  // Four files already: the text goes in the message as typed text would, not a card that holds the composer.
  if(paste.text.length<LONG_PASTE_CHARACTERS||locked||!loaded||pasted)return false;
  const problem=pastedTextProblem(paste.text);
  if(problem){setError(problem);return false;}
  if(imagesRef.current.length>=4)return false;
  // A second paste is "Pasted text 2.txt", so two tiles (and their Remove buttons) don't read the same.
  const base=t('Pasted text.txt').replace(/\.txt$/,'');
  const earlier=imagesRef.current.filter(row=>row.name.startsWith(base)).length;
  try{rememberPaste({...paste,id:crypto.randomUUID(),name:earlier?t('Pasted text {number}.txt',{number:earlier+1}):t('Pasted text.txt')});setAutoSuppressed(true);setError('');return true;}
  catch{setError(t('The pasted-text choice couldn’t be saved, so the text will stay in your message.'));return false;}
 }
 async function attachPaste(){
  const pasted=pastedRef.current;
  if(!pasted||working.current||locked||retry)return;
  if(imagesRef.current.filter(row=>row.id!==pasted.attachmentId).length>=4){setError(t('Attach up to four files. Remove one, or paste this text in your message.'));return;}
  working.current=true;setBusy(true);setError('');
  try{await upload({name:pasted.name,bytes:pastedTextBytes(pasted.text),clientId:pasted.id});}
  catch(e){if(alive.current)setError(e instanceof Error?e.message:t('The pasted text couldn’t upload. Try again.'));}
  finally{release();if(alive.current)setBusy(false);}
 }
 // A long paste becomes a text file straight away, as the Claude app does, instead of a question that held the composer
 // (polisher, 8 Oct). Each paste is tried once, and that's saved with it: a reload, a thread switch or a Cancel shows
 // the card with its choices and the reason, never a second upload nobody asked for.
 useEffect(()=>{
  if(!pasted||pasted.attachmentId||pasted.autoTried||!loaded||locked||uploading||retry||!accessReady)return;
  try{rememberPaste({...pasted,autoTried:true});}catch{return;}
  void attachPaste();
 });
 const attachingPaste=!!pasted&&!!pasted.autoTried&&busy&&!error;
 function pasteInMessage(){
  if(!pasted||locked||retry||!insertPaste.current)return;
  try{
   // Clear persistence first, so a storage failure can't offer a second insertion of text already added.
   savePastedText(scope,null);
   const same=insertPaste.current(pasted);pastedRef.current=null;setPasted(null);setError(same?'':t('Your draft changed, so the pasted text was added at its end.'));
  }catch{try{savePastedText(scope,pasted);}catch{}setError(t('The text couldn’t be pasted. It’s kept here so you can try again.'));}
 }
 function discardPaste(){
  try{rememberPaste(null);setError('');}catch{setError(t('The paste couldn’t be discarded. It’s kept here so you can try again.'));}
 }
 function pasteRemovedBack(){
  if(!removedPaste||locked||!insertPaste.current)return;
  try{insertPaste.current({text:removedPaste.text,from:0,to:0,document:''});setRemovedPaste(null);setError('');}
  catch{setError(t('The text couldn’t be pasted. It’s kept here so you can try again.'));}
 }
 const tray=<>
  {removedPaste&&<p role="status" className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">{t('Removed {name}.',{name:removedPaste.name})}
   <Button type="button" size="sm" variant="ghost" disabled={locked} onClick={pasteRemovedBack}>{t('Paste in message')}</Button>
   <Button type="button" size="sm" variant="ghost" onClick={()=>setRemovedPaste(null)}>{t('Dismiss')}</Button></p>}
  {pasted&&!attachingPaste&&<section className="mb-3 rounded-lg border p-3" aria-label={t('Pasted text')}>
   <p className="mb-1 text-sm font-medium">{t('Use this paste as a text file?')}</p>
   <p className="mb-2 text-xs text-muted-foreground">{t('{bytes} bytes. Your message is unchanged. Nothing is sent until you send the message.',{bytes:formatNumber(pastedTextBytes(pasted.text).length)})}</p>
   <details className="mb-2 text-xs"><summary>{t('Preview pasted text')}</summary><pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap">{pasted.text}</pre></details>
   <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" disabled={locked||!!retry} onClick={()=>void attachPaste()}>{t('Attach pasted text')}</Button>
    <Button type="button" size="sm" variant="ghost" disabled={locked||!!retry||!!pasted.attachmentId} onClick={pasteInMessage}>{t('Paste in message')}</Button>
    <Button type="button" size="sm" variant="ghost" disabled={locked||!!retry||!!pasted.attachmentId} onClick={discardPaste}>{t('Discard paste')}</Button></div>
  </section>}
  {!accessReady&&<p role="status" className="mb-2 text-xs text-muted-foreground">{t('Attachments are waiting for Dervo account access. Check your account in Settings.')}</p>}
  {pending&&!sending&&!props.isSending&&<p role="status" className="mb-2 text-xs text-muted-foreground">{t('The previous send hasn’t been confirmed. Send the same message again to check it. Its attachments are kept here.')}</p>}
  {images.length>0&&<div className="mb-3 flex flex-wrap gap-2" aria-label={t('Attached files')}>{images.map(row=><div key={row.id} className="relative w-28 overflow-hidden rounded-xl border bg-muted/30">
   <div className="relative flex h-20 items-center justify-center">{row.dataUrl?<img src={row.dataUrl} alt={row.name} className={`h-full w-full object-cover transition-opacity duration-150 ${row.state==='ready'?'':'opacity-60'}`}/>:<FileText className="size-6 text-muted-foreground" aria-hidden="true"/>}
    {row.state!=='ready'&&(busy||uploading)&&<span className="absolute inset-x-2 bottom-1.5 h-1 overflow-hidden rounded-full bg-background/80" aria-hidden="true"><span className="block h-full rounded-full bg-foreground/60 transition-[width] duration-150" style={{width:`${Math.max(8,Math.round(100*(row.size?row.received/row.size:0)))}%`}}/></span>}</div>
   <p className="truncate px-2 pt-1 text-xs" title={row.name}>{row.name}</p>
   <p className="px-2 pb-2 text-2xs text-muted-foreground">{row.state==='ready'?(row.width?`${row.width} × ${row.height}`:t('Text ready')):(busy||uploading)?t('Uploading…'):t('Upload interrupted')}</p>
   <button type="button" aria-label={t('Remove {name}',{name:row.name})} disabled={locked} onClick={()=>void remove(row.id)} className="absolute right-1 top-1 rounded-full bg-background/95 p-1 shadow-sm disabled:opacity-40"><X className="size-3.5"/></button>
  </div>)}</div>}
  {(busy||uploading)&&!sending&&!images.length&&<p role="status" className="mb-2 text-xs text-muted-foreground">{t('Preparing your attachments…')}</p>}
  {error&&<div role="alert" className="mb-2 text-xs text-destructive">{error} {!loaded&&<button type="button" className="ml-2 underline" onClick={()=>void load()}>{t('Try again')}</button>}</div>}
  {retry&&!busy&&!uploading&&<><Button type="button" size="sm" variant="outline" className="mb-2" onClick={()=>void retryUpload()}><RotateCcw className="mr-2 size-3"/>{t('Retry file upload')}</Button><Button type="button" size="sm" variant="ghost" className="mb-2 ml-2" onClick={()=>void cancelFailedUpload()}>{t('Cancel file upload')}</Button></>}
  {waiting&&<p role="status" className="mb-2 text-xs text-muted-foreground">{t('Sends when your attachments finish uploading.')}</p>}
  {images.length>0&&!waiting&&<p className="mb-2 text-xs text-muted-foreground">{t('Add a message about these files. They’re shared with your project agent when you send.')}</p>}
 </>;
 return <div ref={container} onPasteCapture={event=>{
  if(pasted){event.preventDefault();event.stopPropagation();setError(t('Choose what to do with the current paste first, then paste again. Your current paste is kept here.'));return;}
  const files=Array.from(event.clipboardData.files);if(files.length){event.preventDefault();event.stopPropagation();void choose(files);}
 }}
  onDragOverCapture={event=>{if(event.dataTransfer.types.includes('Files')){event.preventDefault();event.stopPropagation();}}}
  onDropCapture={event=>{const files=Array.from(event.dataTransfer.files);if(files.length){event.preventDefault();event.stopPropagation();void choose(files);}}}>
  <input ref={input} type="file" multiple accept={`image/png,image/jpeg,image/webp,${DOCUMENT_ACCEPT}`} aria-label={t('Attach files')} className="hidden" disabled={locked||!loaded} onChange={event=>{const files=Array.from(event.target.files??[]);event.target.value='';void choose(files);}}/>
  <DvComposer {...props} key={autoMount?'auto':'plain'} disabled={props.disabled||!!pasted} autoSubmitDraftKey={autoMount?props.autoSubmitDraftKey:null} channelId={channelId} onSend={send} isSending={props.isSending||sending} onTextPaste={deferPaste} onPasteTextReady={pasteReady} onPickAttachment={()=>input.current?.click()} attachmentContent={tray}/>
 </div>;
}

/** What was sent with a request, as the Claude app shows it: the pictures themselves under your message, and a named chip
 * for a document. A click opens it full size. Each is read once and kept: a sent attachment never changes. */
// Thumbnails share the one host pipe with everything else: two at a time, so opening a long thread can't queue every
// picture ahead of the history and the feed (code review, 8 Oct).
let thumbsLoading=0;const thumbsWaiting:(()=>void)[]=[];
async function oneThumbAtATime<T>(load:()=>Promise<T>):Promise<T>{
 while(thumbsLoading>=2)await new Promise<void>(resolve=>thumbsWaiting.push(resolve));
 thumbsLoading++;
 try{return await load();}finally{thumbsLoading--;thumbsWaiting.shift()?.();}
}
/** What a sent attachment keeps in memory: its name and a small picture, not the full image or a document's whole text. */
type SentThumb={name:string;thumb?:string};
/** A picture made 192px on its short side for a 96px tile. The host sends its stored thumbnail (image_thumb) where it has
 * one, so this only shrinks an older upload's full picture. The full image is read again only when it's opened. Where the
 * picture can't be decoded here, it's kept as it came. */
async function shrinkPicture(dataUrl:string):Promise<string>{
 try{
  const picture=new Image();picture.src=dataUrl;
  await Promise.race([picture.decode(),new Promise((_,reject)=>setTimeout(()=>reject(new Error()),3000))]);
  const scale=Math.min(1,192/Math.min(picture.naturalWidth,picture.naturalHeight));
  if(scale>=1)return dataUrl;
  const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(picture.naturalWidth*scale));canvas.height=Math.max(1,Math.round(picture.naturalHeight*scale));
  const pen=canvas.getContext('2d');if(!pen)return dataUrl;
  pen.drawImage(picture,0,0,canvas.width,canvas.height);
  return canvas.toDataURL('image/png');
 }catch{return dataUrl;}
}
async function readThumb(scope:ImageScope,id:string):Promise<SentThumb>{
 const preview=await oneThumbAtATime(()=>imageRequest<Preview&{thumbnail?:boolean}>(scope,'image_thumb',{id}));
 // The host's own thumbnail is already small; only a full picture needs shrinking here.
 return {name:preview.name,thumb:preview.dataUrl?preview.thumbnail?preview.dataUrl:await shrinkPicture(preview.dataUrl):undefined};
}
type Opened={id:string;name:string;dataUrl?:string;textPreview?:string;loading:boolean;failed?:boolean};
export function SentImages({projectId,channelId,ids}:ImageScope&{ids:string[]}){
 const [open,setOpen]=useState<Opened>();
 // Full size on open: the small picture shows at once and the full one replaces it when it arrives.
 function openFull(id:string,shown:SentThumb){
  setOpen({id,name:shown.name,dataUrl:shown.thumb,loading:true});
  void imageRequest<Preview>({projectId,channelId},'image_get',{id})
   .then(full=>setOpen(current=>current?.id===id?{id,name:full.name,dataUrl:full.dataUrl??current.dataUrl,textPreview:full.textPreview,loading:false}:current))
   .catch(()=>setOpen(current=>current?.id===id?{...current,loading:false,failed:true}:current));
 }
 // Read only once it scrolls near the view.
 const box=useRef<HTMLDivElement>(null),[near,setNear]=useState(typeof IntersectionObserver==='undefined');
 useEffect(()=>{
  if(near||!box.current)return;
  const watch=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);watch.disconnect();}},{rootMargin:'400px 0px'});
  watch.observe(box.current);return()=>watch.disconnect();
 },[near]);
 const opener=useRef<HTMLButtonElement|null>(null);
 const rows=useQueries({queries:ids.map(id=>({queryKey:['sent-attachment',projectId,channelId,id],staleTime:Infinity,enabled:near,
  retry:(count:number,error:unknown)=>isBusyAnswer(error)?count<4:count<1,retryDelay:1000,
  queryFn:()=>readThumb({projectId,channelId},id)}))});
 return <div ref={box} className="dervo-sent-images my-2 flex flex-wrap items-center gap-2" data-testid="sent-images">
  {ids.map((id,index)=>{const row=rows[index];const preview=row?.data;
   const name=preview?.name??(ids.length>1?t('Attachment {number}',{number:index+1}):t('Attachment'));
   if(row?.isError)return <Button key={id} type="button" size="sm" variant="outline" onClick={()=>void row.refetch()}><ImagePlus className="mr-2 size-4"/>{t('This file couldn’t open. Try again.')}</Button>;
   if(!preview)return <span key={id} className="dv-sent-thumb dv-sent-loading" role="img" aria-label={t('Loading {name}',{name})}/>;
   return <button key={id} type="button" className={preview.thumb?'dv-sent-thumb':'dv-sent-file'} aria-label={t('Open {name}',{name})} title={name} onClick={event=>{opener.current=event.currentTarget;openFull(id,preview);}}>
    {preview.thumb?<img src={preview.thumb} alt="" draggable={false} decoding="async" loading="lazy"/>:<><FileText aria-hidden="true"/><span>{name}</span></>}
   </button>;})}
  <Dialog open={!!open} onOpenChange={value=>{if(!value)setOpen(undefined);}}><DialogContent className="max-w-4xl" onCloseAutoFocus={event=>{
  // Back to the thumbnail it came from. Just after a send the row it sat in is replaced by the saved one, so find the
  // same picture's button there.
  const label=opener.current?.getAttribute('aria-label');
  const back=opener.current?.isConnected?opener.current:[...document.querySelectorAll<HTMLButtonElement>('.dervo-sent-images button')].find(button=>button.getAttribute('aria-label')===label);
  if(back){event.preventDefault();back.focus();}}}><DialogTitle>{open?.name??t('Attached file')}</DialogTitle><DialogDescription className={open?.dataUrl?'sr-only':undefined}>{t('The attachment sent with this request. Document previews contain extracted text, without images or layout.')}</DialogDescription>{open?.failed&&<p role="alert" className="text-sm text-destructive">{t('This file couldn’t open. Try again.')}</p>}{open?.loading&&!open.dataUrl&&<p role="status" className="text-sm text-muted-foreground">{t('Loading {name}',{name:open.name})}</p>}{open?.textPreview&&<pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap text-sm">{open.textPreview}</pre>}{open?.dataUrl&&<img src={open.dataUrl} alt={open.name} className="max-h-[70vh] w-full object-contain"/>}</DialogContent></Dialog>
 </div>;
}
