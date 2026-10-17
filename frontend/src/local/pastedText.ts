import type {ComposerTextPaste} from '@/features/messages/ui/composerTextPaste';
import type {ImageScope} from './imageInput';
import {t} from '@/i18n';

export const LONG_PASTE_CHARACTERS=4000;
export const PASTED_TEXT_MAX_BYTES=60000;
/** ``autoTried``: the automatic attach ran for this paste once; saved, so a reload or a Cancel never uploads it again. */
export type PastedText=ComposerTextPaste&{id:string;name:string;attachmentId?:string;autoTried?:boolean};
export function pastedTextBytes(text:string):Uint8Array{return new TextEncoder().encode(text);}
export function pastedTextProblem(text:string):string|null{
 if(pastedTextBytes(text).length>PASTED_TEXT_MAX_BYTES)return t('This paste has more than 60 KB of text, so it will stay in your message. Attach a smaller section if you prefer a file.');
 if(!text.trim()||/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(text))return t('This paste can’t be attached as readable text, so it will stay in your message.');
 return null;
}
const key=({projectId,channelId}:ImageScope)=>`dervo-pasted-text:${JSON.stringify([projectId,channelId])}`;
export function savePastedText(scope:ImageScope,paste:PastedText|null){
 if(paste)localStorage.setItem(key(scope),JSON.stringify(paste));else localStorage.removeItem(key(scope));
}
export function loadPastedText(scope:ImageScope):PastedText|null{
 try{
  const paste=JSON.parse(localStorage.getItem(key(scope))??'null');
  return paste&&typeof paste.text==='string'&&typeof paste.document==='string'&&typeof paste.id==='string'&&typeof paste.name==='string'&&Number.isInteger(paste.from)&&Number.isInteger(paste.to)&&paste.from>=0&&paste.to>=paste.from&&!pastedTextProblem(paste.text)?paste:null;
 }catch{return null;}
}
