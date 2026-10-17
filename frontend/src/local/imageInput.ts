import {local} from './api';
import {t} from '@/i18n';
export type ImageScope={projectId:string;channelId:string};
export type LocalImage={id:string;name:string;state:'uploading'|'ready';size:number;received:number;width:number|null;height:number|null};
const MAX_BYTES=2*1024*1024,CHUNK_BYTES=48*1024;
export function imageSize(width:number,height:number,edge=2048){
 if(!Number.isFinite(width)||!Number.isFinite(height)||width<1||height<1||width>12000||height>12000||width*height>36_000_000)throw Error(t('Choose a smaller image, up to 36 megapixels.'));
 const scale=Math.min(1,edge/Math.max(width,height));
 return {width:Math.max(1,Math.round(width*scale)),height:Math.max(1,Math.round(height*scale))};
}
export type ImagePreview=LocalImage&{dataUrl?:string;textPreview?:string;thumbnail?:boolean};
export const THUMB_SHORT=192,THUMB_EDGE=640;
/** A small PNG of a picture for its tile (base64), made here from the file the window already has, or null: a document,
 * or a picture this window can't draw. The host keeps it next to the upload (image_thumb_put). */
export async function thumbnailOf(file:Blob):Promise<string|null>{
 if(!file.type.startsWith('image/')||typeof createImageBitmap!=='function')return null;
 try{
  const bitmap=await createImageBitmap(file);
  try{
   const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
   if(!context)return null;
   // 192px on the short side, for a 96px tile at 2x, and never past THUMB_EDGE on the long one.
   const scale=Math.min(1,THUMB_SHORT/Math.min(bitmap.width,bitmap.height),THUMB_EDGE/Math.max(bitmap.width,bitmap.height));
   canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
   context.drawImage(bitmap,0,0,canvas.width,canvas.height);
   const blob=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/png'));
   if(!blob)return null;
   const bytes=new Uint8Array(await blob.arrayBuffer());
   let text='';for(let at=0;at<bytes.length;at+=0x8000)text+=String.fromCharCode(...bytes.subarray(at,at+0x8000));
   return btoa(text);
  }finally{bitmap.close();}
 }catch{return null;}
}
/** What a tile draws once its upload is ready: the small picture for an image (handing the host ``thumb`` first, when
 * there is one; a refused preview only means the full one is sent instead), and the text for a document. */
export async function tilePreview(scope:ImageScope,row:LocalImage,thumb?:string|null):Promise<ImagePreview>{
 if(thumb)await imageRequest(scope,'image_thumb_put',{id:row.id,data:thumb}).catch(()=>undefined);
 return imageRequest<ImagePreview>(scope,row.width?'image_thumb':'image_get',{id:row.id});
}
export async function prepareImage(file:File):Promise<Uint8Array>{
 if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>8*1024*1024)throw Error(t('Choose a PNG, JPEG or WebP image under 8 MB.'));
 const bitmap=await createImageBitmap(file);
 try{
  const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
  if(!context)throw Error(t('The image preview couldn’t open.'));
  for(let edge=2048;edge>=640;edge=Math.floor(edge*.8)){
   const size=imageSize(bitmap.width,bitmap.height,edge);canvas.width=size.width;canvas.height=size.height;
   context.drawImage(bitmap,0,0,size.width,size.height);
   const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(Error(t('The image couldn’t be prepared.'))),'image/png'));
   if(blob.size<=MAX_BYTES)return new Uint8Array(await blob.arrayBuffer());
  }
  throw Error(t('This image is too detailed to attach. Crop the part you want to show and try again.'));
 }finally{bitmap.close();}
}
export function imageRequest<T>(scope:ImageScope,operation:string,args:Record<string,unknown>={}):Promise<T>{return local<T>(operation,{...scope,...args});}
export async function uploadImage(scope:ImageScope,name:string,bytes:Uint8Array,clientId:string,active:()=>boolean,onProgress:(image:LocalImage)=>void):Promise<LocalImage>{
 if(bytes.length>MAX_BYTES)throw Error(t('This image exceeds 2 MB after resizing.'));
 const hash=await crypto.subtle.digest('SHA-256',new Uint8Array(bytes));
 const sha256=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
 async function request(operation:string,args:Record<string,unknown>){
  if(!active())throw Error(t('Image upload paused.'));
  try{return await imageRequest<LocalImage>(scope,operation,args);}
  catch(error){if(!active())throw error;return imageRequest<LocalImage>(scope,operation,args);}
 }
 let row=await request('image_begin',{clientId,name,size:bytes.length,sha256});onProgress(row);
 if(row.state==='ready')return row;
 if(!Number.isInteger(row.received)||row.received<0||row.received>bytes.length)throw Error(t('The saved upload couldn’t be resumed.'));
 for(let offset=row.received;offset<bytes.length;offset+=CHUNK_BYTES){
  const part=bytes.subarray(offset,offset+CHUNK_BYTES);
  row=await request('image_chunk',{id:row.id,offset,data:btoa(String.fromCharCode(...part))});onProgress(row);
 }
 row=await request('image_finish',{id:row.id});onProgress(row);return row;
}

export function pendingImageRequest(scope:ImageScope):{text:string;images:string[]}|null{
 const value=localStorage.getItem(`dervo-coding-pending:${scope.channelId}`);
 if(!value)return null;
 try{
  const saved=JSON.parse(value);
  if(saved.projectId!==scope.projectId||saved.channelId!==scope.channelId||typeof saved.text!=='string'||!Array.isArray(saved.images)||saved.images.length>4||saved.images.some((id:unknown)=>typeof id!=='string'))return null;
  return {text:saved.text,images:saved.images};
 }catch{return null;}
}

export const DOCUMENT_ACCEPT='.txt,.md,.csv,.tsv,.json,.yaml,.yml,.xml,.html,.css,.js,.ts,.tsx,.jsx,.py,.sql,.sh,.rs,.toml,.log,.pdf,.docx,.xlsx,.pptx';
export async function prepareAttachment(file:File):Promise<Uint8Array>{
 if(['image/png','image/jpeg','image/webp'].includes(file.type))return prepareImage(file);
 const extension='.'+file.name.split('.').at(-1)?.toLowerCase();
 if(!DOCUMENT_ACCEPT.split(',').includes(extension))throw Error(t('Attach an image, text, CSV, code, PDF, DOCX, XLSX or PPTX file.'));
 if(file.size<1||file.size>MAX_BYTES)throw Error(t('Attach a nonempty file up to 2 MB.'));
 return new Uint8Array(await file.arrayBuffer());
}
