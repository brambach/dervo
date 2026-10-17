import type {HomeEntry} from './Home';
import {plainSummary} from './homeRequests';
import {t} from '@/i18n';

/** Voice on Home: what the window knows about the voice process (src/voice.rs, backend/dervo_voice). Pure, so the
 * rules (undo window, spoken yes, sending in order) are tested without a microphone. HomeVoice.tsx draws it. */
export type VoiceMode='push_to_talk'|'wake'|'conversation';
export type VoiceEvent={type:string;[key:string]:unknown};
export type Heard={id:string;text:string;until:number;replay?:boolean};
export type Confirm={id:string;text:string;what:string;readback:string;until:number};
export type VoiceView={
 on:boolean;
 process:'off'|'starting'|'running'|'restarting'|'failed';
 state:'off'|'idle'|'listening'|'hearing';
 speaking:boolean;
 mode:VoiceMode;
 /** Heard and shown, inside the undo window. */
 heard:Heard[];
 /** An outward request (push, merge, deploy...) waiting for a spoken or clicked yes. */
 confirm:Confirm|null;
 /** Released by the voice process: Home sends these in order, one at a time. */
 outbox:{id:string;text:string;scope?:string}[];
 note:string|null;
 unavailable:string|null;
 /** Why: 'models' offers the download; anything else ('libraries', 'audio') is a build or device problem, said as is. */
 unavailableReason:string|null;
 latency:number|null;
 /** The model download, only after your click (backend/dervo_voice/setup.py). */
 setup:{state:'downloading'|'checking'|'failed'|'done';name?:string;bytes?:number;of?:number;detail?:string}|null;
};

/** Hold to talk (the mic or ⌥ Space); "Hey Dervo", where a follow-up needs no wake word; open mic, where everything
 * you say goes to Home until you turn it off. */
export const VOICE_MODES:VoiceMode[]=['push_to_talk','wake','conversation'];

export function initialVoice(mode:VoiceMode='push_to_talk'):VoiceView{
 return {on:false,process:'off',state:'off',speaking:false,mode,heard:[],confirm:null,outbox:[],note:null,unavailable:null,unavailableReason:null,latency:null,setup:null};
}

const text=(value:unknown)=>typeof value==='string'?value:'';
const seconds=(value:unknown,fallback:number)=>typeof value==='number'&&Number.isFinite(value)?value:fallback;

export function voiceReducer(view:VoiceView,event:VoiceEvent,now=Date.now()):VoiceView{
 const id=text(event.id);
 switch(event.type){
  case 'supervisor':{
   const state=text(event.state);
   if(state==='running')return {...view,process:'running',note:null};
   if(state==='restarting')return {...view,process:'restarting',state:'off',speaking:false,note:t('Voice stopped unexpectedly. Restarting it.')};
   if(state==='failed')return {...view,process:'failed',state:'off',note:t('Voice couldn’t start.')};
   // After giving up, "stopped" follows "failed": keep saying it failed rather than going quiet.
   if(state==='stopped')return view.process==='failed'?{...view,state:'off',speaking:false}:{...initialVoice(view.mode),heard:[],note:view.note};
   return view;
  }
  case 'status':{
   // The answer to the window asking where things are (after a reload, the process kept running): catch up.
   const released=Array.isArray(event.released)?event.released as {id:string;text:string;scope?:string}[]:[];
   const pending=Array.isArray(event.pending)?event.pending as {id:string;text:string}[]:[];
   return {...view,on:true,process:'running',state:(text(event.state) as VoiceView['state'])||view.state,mode:(text(event.mode) as VoiceMode)||view.mode,
    speaking:!!event.speaking,
    heard:[...view.heard,...pending.filter(item=>!view.heard.some(seen=>seen.id===item.id)).map(item=>({id:item.id,text:item.text,until:now+3000,replay:true}))],
    outbox:[...view.outbox,...released.filter(item=>!view.outbox.some(queued=>queued.id===item.id)).map(item=>({id:item.id,text:item.text,...(item.scope?{scope:item.scope}:{})}))]};
  }
  case 'ready':return {...view,process:'running',state:'idle',mode:(text(event.mode) as VoiceMode)||view.mode};
  case 'unavailable':return {...view,unavailable:text(event.detail)||t('Voice models aren’t installed yet.'),unavailableReason:text(event.reason)||'models'};
  case 'state':return {...view,state:(text(event.state) as VoiceView['state'])||view.state};
  case 'mode':return {...view,mode:(text(event.mode) as VoiceMode)||view.mode};
  case 'speaking':return {...view,speaking:true};
  case 'spoken':case 'barge_in':return {...view,speaking:false};
  case 'utterance':
   if(view.heard.some(item=>item.id===id))return view;
   return {...view,heard:[...view.heard,{id,text:text(event.text),until:now+seconds(event.undo_seconds,3)*1000,replay:!!event.replay}],note:null};
  case 'confirm':
   return {...view,confirm:{id,text:text(event.text),what:text(event.what),readback:text(event.readback),until:now+seconds(event.seconds,8)*1000}};
  case 'confirmed':return {...view,confirm:view.confirm?.id===id?null:view.confirm};
  case 'cancelled':
   return {...view,heard:view.heard.filter(item=>item.id!==id),confirm:view.confirm?.id===id?null:view.confirm,
    note:event.reason==='timeout'?t('I didn’t hear a yes, so I didn’t send it.'):view.note};
  case 'send':
   return {...view,heard:view.heard.filter(item=>item.id!==id),confirm:view.confirm?.id===id?null:view.confirm,
    outbox:view.outbox.some(item=>item.id===id)?view.outbox:[...view.outbox,{id,text:text(event.text),...(text(event.scope)?{scope:text(event.scope)}:{})}]};
  case 'send_failed':return {...view,note:t('That didn’t reach Home: {detail}. It’s not queued; say it again or type it.',{detail:text(event.detail)||t('unknown error')})};
  case 'undo_too_late':return {...view,note:t('Too late to undo: that one was already sent.')};
  case 'metrics':return {...view,latency:typeof event.ack_ms==='number'?event.ack_ms:view.latency};
  case 'audio':{
   const audio=text(event.audio);
   if(audio==='denied')return {...view,note:t('Dervo can’t use the microphone. Allow it in System Settings, Privacy & Security, Microphone.')};
   if(audio==='lost')return {...view,note:t('The microphone went away. Voice picks it up again when it’s back.')};
   if(audio==='ready'||audio==='changed'||audio==='closed')return {...view,note:null};
   return view;
  }
  case 'error':return {...view,note:text(event.detail)||view.note};
  case 'setup':{
   const state=text(event.state);
   if(state==='downloading'||state==='checking')return {...view,setup:{state,name:text(event.name),bytes:seconds(event.bytes,0),of:seconds(event.of,0)}};
   if(state==='failed')return {...view,setup:{state:'failed',detail:text(event.detail)}};
   if(state==='done')return {...view,setup:{state:'done'},unavailable:null,unavailableReason:null};
   if(state==='exited'&&event.code!==0&&view.setup?.state!=='failed')return {...view,setup:{state:'failed',detail:t('The download stopped.')}};
   return view;
  }
  default:return view;
 }
}

/** Done with the first message in the outbox: it went to Home, or it failed and was reported. */
export function sent(view:VoiceView,id:string):VoiceView{return {...view,outbox:view.outbox.filter(item=>item.id!==id)};}

/** What to say once Home has routed a spoken request: where it went, in a few words, else Home's own reply cut short.
 * Nothing until the reply is ready. */
export function spokenReceipt(reply:HomeEntry|undefined):string|null{
 if(!reply||reply.kind!=='reply'||reply.state==='thinking')return null;
 if(reply.state==='failed')return t('That didn’t go through. It’s on screen.');
 const started:string[]=[],added:string[]=[];
 for(const part of reply.parts??[]){
  const name=part.conversation?.name||part.title||part.name;
  if(!name)continue;
  (part.action==='start'?started:added).push(name);
 }
 const list=(names:string[])=>names.length>1?t('{first} and {last}',{first:names.slice(0,-1).join(', '),last:names.at(-1)!}):names[0];
 const lines=[started.length?t('Started {names}.',{names:list(started)}):'',added.length?t('Sent to {names}.',{names:list(added)}):''].filter(Boolean);
 if(lines.length)return lines.join(' ');
 return reply.text?plainSummary(reply.text):null;
}

/** The reply to a spoken request: the request's own entry is the newest of yours with these words since it was sent,
 * and the reply is the one answering it. */
export function replyTo(entries:HomeEntry[],words:string,since:number):HomeEntry|undefined{
 const mine=[...entries].reverse().find(entry=>entry.kind==='you'&&entry.text?.trim()===words.trim()&&entry.created_at>=since-5);
 if(!mine)return undefined;
 const at=entries.indexOf(mine);
 return entries.slice(at+1).find(entry=>entry.kind==='reply'&&(entry.answers===mine.id||(!!mine.request_id&&entry.request_id===mine.request_id)));
}

/** One line for the download, in the order the models arrive. */
export function setupLine(setup:VoiceView['setup']):string|null{
 if(!setup)return null;
 if(setup.state==='failed')return t('The voice download didn’t finish: {detail}',{detail:setup.detail||t('unknown error')});
 if(setup.state==='done')return t('Voice is ready.');
 if(setup.state==='checking')return t('Checking the download…');
 return t('Downloading voice, part {part} of 5, {done}%',{part:String(Math.max(1,SETUP_ORDER.indexOf(setup.name||'')+1)),done:String(setup.bytes&&setup.of?Math.round(setup.bytes/setup.of*100):0)});
}
/** The order backend/dervo_voice/setup.py fetches the models in. */
const SETUP_ORDER=['vad','turn','wake','asr','tts'];

export type SendOutcome='sent'|'unsure'|'busy'|'failed';
