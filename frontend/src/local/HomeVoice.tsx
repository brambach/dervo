import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import {useEffect,useReducer,useRef,useState} from 'react';
import type {HomeEntry} from './Home';
import {VOICE_MODES,initialVoice,replyTo,sent,setupLine,spokenReceipt,voiceReducer,type SendOutcome,type VoiceEvent,type VoiceMode,type VoiceView} from './voiceState';
import {t} from '@/i18n';
import './HomeVoice.css';

/** Voice on Home. The process hears and decides (backend/dervo_voice/loop.py); this shows what it heard, gives Undo and
 * Yes/No, and hands each released request to Home's own send, in order, with the utterance id as the clientId so a replay
 * after a crash lands on the same message. Visual layer: tokens only, for the design lane to restyle. */
type Action={kind:'event';event:VoiceEvent;now:number}|{kind:'sent';id:string}|{kind:'on';on:boolean}|{kind:'mode';mode:VoiceMode};
function reduce(view:VoiceView,action:Action):VoiceView{
 if(action.kind==='event')return voiceReducer(view,action.event,action.now);
 if(action.kind==='sent')return sent(view,action.id);
 if(action.kind==='on')return action.on?{...view,on:true,process:view.process==='off'?'starting':view.process}:{...initialVoice(view.mode)};
 return {...view,mode:action.mode};
}

const command=(message:Record<string,unknown>)=>invoke('voice_command',{message}).catch(()=>{/* not running: the supervisor says so */});
const MODE_KEY='dervo.voice.mode';
const OPEN_MIC_KEY='dervo.voice.openMicAgreed';
// Open mic is never restored on its own: turning voice on again starts in hold-to-talk or "Hey Dervo".
function savedMode():VoiceMode{try{const mode=localStorage.getItem(MODE_KEY);if(mode==='push_to_talk'||mode==='wake')return mode;}catch{/* private window */}return 'push_to_talk';}
function openMicAgreed(){try{return localStorage.getItem(OPEN_MIC_KEY)==='1';}catch{return false;}}

/** `scope` is the project and profile on screen. Each utterance is tagged with the scope it was heard in, and one heard
 * in another scope is dropped, never sent here: personal and work stay apart, and so do projects. */
export function useHomeVoice({entries,onSend,scope}:{entries:HomeEntry[];onSend:(words:string,voice:{clientId:string})=>Promise<SendOutcome>;scope:string}){
 const [view,dispatch]=useReducer(reduce,undefined,()=>initialVoice(savedMode()));
 const [now,setNow]=useState(Date.now());
 const spoken=useRef(new Set<string>());
 const awaiting=useRef(new Map<string,{text:string;at:number}>());
 const working=useRef(false);
 const [attempt,setAttempt]=useState(0);
 const [askOpenMic,setAskOpenMic]=useState(false);

 useEffect(()=>{
  let unlisten=()=>{};let alive=true;
  // Outside the app (tests, a browser preview) there's no event bridge: voice just stays off.
  listen<VoiceEvent>('dervo-voice',event=>dispatch({kind:'event',event:event.payload,now:Date.now()})).then(stop=>{
   if(!alive){stop();return;}
   unlisten=stop;
   // After a window reload the process may already be running: catch up on its state and anything sent but unconfirmed.
   void command({type:'status'});
  },()=>{});
  return ()=>{alive=false;unlisten();};
 },[]);
 // Countdowns on screen tick only while something is waiting.
 useEffect(()=>{
  if(!view.heard.length&&!view.confirm)return;
  const timer=setInterval(()=>setNow(Date.now()),250);
  return ()=>clearInterval(timer);
 },[view.heard.length,view.confirm]);

 // Tell the process where it is whenever that changes or it (re)starts, so new utterances carry this scope.
 useEffect(()=>{if(view.process==='running')void command({type:'scope',scope});},[scope,view.process]);
 // Leaving Home turns voice off, the same as the button: nothing heard here is sent somewhere else later.
 const offRef=useRef<()=>Promise<void>>(async()=>{});
 useEffect(()=>()=>{void offRef.current();},[]);

 // The outbox, first in first out. Home busy with your last message: wait and try again; it never merges or drops one.
 // One send at a time; a retry bumps `attempt`, so the effect runs again even though the outbox itself didn't change.
 useEffect(()=>{
  const next=view.outbox[0];
  if(!next||working.current)return;
  if(next.scope&&next.scope!==scope){
   void command({type:'drop',id:next.id});
   dispatch({kind:'sent',id:next.id});
   dispatch({kind:'event',event:{type:'error',detail:t('Dropped something you said in another project.')},now:Date.now()});
   return;
  }
  working.current=true;
  const again=()=>setTimeout(()=>setAttempt(n=>n+1),1200);
  onSend(next.text,{clientId:next.id}).then(outcome=>{
   working.current=false;
   if(outcome==='busy'){again();return;}
   if(outcome==='sent'){void command({type:'delivered',id:next.id});awaiting.current.set(next.id,{text:next.text,at:Date.now()/1000});}
   // 'unsure': it may have gone. Left released, so a restart sends it again under the same id and Home keeps one.
   if(outcome==='failed')void command({type:'failed',id:next.id});
   dispatch({kind:'sent',id:next.id});
  },()=>{working.current=false;again();});
 },[view.outbox,onSend,attempt,scope]);

 // Once Home has routed a spoken request, say where it went.
 useEffect(()=>{
  for(const [id,asked] of awaiting.current){
   const said=spokenReceipt(replyTo(entries,asked.text,asked.at));
   if(said===null)continue;
   awaiting.current.delete(id);
   if(spoken.current.has(id))continue;
   spoken.current.add(id);
   void command({type:'speak',text:said,key:`receipt:${id}`});
  }
 },[entries]);

 async function turnOn(mode=view.mode){
  dispatch({kind:'on',on:true});
  await invoke('voice_start',{mode}).catch(error=>dispatch({kind:'event',event:{type:'error',detail:String(error)},now:Date.now()}));
 }
 // Off means off: what's still in its undo window or waiting for a yes is taken back, the mic closes, and anything
 // released but not yet handed to Home is dropped rather than replayed on the next start.
 async function turnOff(){
  const queued=view.outbox.map(item=>item.id);
  dispatch({kind:'on',on:false});
  await command({type:'mute'});
  for(const id of queued)await command({type:'drop',id});
  await invoke('voice_stop').catch(()=>{});
 }
 offRef.current=turnOff;
 function setMode(mode:VoiceMode,agreed=false){
  // Open mic sends everything you say to Home, so the first time it's an explicit yes on screen, not a menu pick.
  if(mode==='conversation'&&!agreed&&!openMicAgreed()){setAskOpenMic(true);return;}
  if(mode==='conversation'&&agreed){try{localStorage.setItem(OPEN_MIC_KEY,'1');}catch{/* private window */}}
  setAskOpenMic(false);
  try{localStorage.setItem(MODE_KEY,mode);}catch{/* private window */}
  dispatch({kind:'mode',mode});
  if(view.on)void invoke('voice_start',{mode});
 }
 // The download finished: start voice again so it loads the models.
 useEffect(()=>{
  if(view.setup?.state!=='done'||!view.on)return;
  void invoke('voice_stop').then(()=>{dispatch({kind:'on',on:true});return invoke('voice_start',{mode:view.mode});});
 },[view.setup?.state]);
 return {view,now,turnOn,turnOff,setMode,askOpenMic,cancelOpenMic:()=>setAskOpenMic(false),
  install:()=>{dispatch({kind:'event',event:{type:'setup',state:'downloading',name:''},now:Date.now()});void invoke('voice_install').catch(error=>dispatch({kind:'event',event:{type:'setup',state:'failed',detail:String(error)},now:Date.now()}));},
  // While voice is off, pressing only turns it on: a press can't become a talk that outlives the release.
  talk:(down:boolean)=>{if(!view.on){if(down)void turnOn();return;}void command({type:down?'ptt_down':'ptt_up'});},
  undo:(id:string)=>void command({type:'undo',id}),
  confirm:(id:string,yes:boolean)=>void command({type:'confirm',id,yes}),
  stopSpeaking:()=>void command({type:'stop_speaking'})};
}

export type HomeVoiceControls=ReturnType<typeof useHomeVoice>;

/** Hold ⌥ Space anywhere in the window to talk (the global hotkey is a follow-up: it needs Tauri's global-shortcut
 * plugin). Holding a key repeats keydown; only the first counts. */
export function useTalkKey(voice:HomeVoiceControls,enabled:boolean){
 const held=useRef(false);
 useEffect(()=>{
  if(!enabled)return;
  const down=(event:KeyboardEvent)=>{if(event.code==='Space'&&event.altKey&&!event.metaKey&&!event.ctrlKey&&!event.isComposing){event.preventDefault();if(!held.current){held.current=true;voice.talk(true);}}};
  const up=(event:KeyboardEvent)=>{if(held.current&&(event.code==='Space'||event.key==='Alt')){held.current=false;voice.talk(false);}};
  window.addEventListener('keydown',down);window.addEventListener('keyup',up);
  return ()=>{window.removeEventListener('keydown',down);window.removeEventListener('keyup',up);};
 },[enabled,voice]);
}

export function VoiceStrip({voice}:{voice:HomeVoiceControls}){
 const {view,now}=voice;
 const status=voiceLine(view);
 if(!view.on&&!view.heard.length&&!view.confirm&&!view.note)return null;
 const askOpenMic=voice.askOpenMic&&view.on;
 return <div className="dv-home-voice" data-state={view.state} data-speaking={view.speaking||undefined}>
  {status&&<p className="dv-caption dv-home-voice-line" aria-live="polite"><span className="dv-home-voice-dot" aria-hidden="true"/>{status}
   {view.speaking&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={voice.stopSpeaking}>{t('Stop talking')}</button>}</p>}
  {view.heard.map(item=><div key={item.id} className="dv-home-voice-heard" role="status" aria-label={t('Heard: {words}. Sends in a few seconds. Undo available.',{words:item.text})}>
   <q className="dv-home-voice-words">{item.text}</q>
   <span className="dv-caption" aria-hidden="true">{t('Sending in {n}s',{n:String(Math.max(0,Math.ceil((item.until-now)/1000)))})}</span>
   <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>voice.undo(item.id)}>{t('Undo')}</button>
  </div>)}
  {askOpenMic&&<div className="dv-home-voice-confirm" role="group" aria-label={t('Turn on open mic')}>
   <p className="dv-home-voice-ask">{t('Open mic sends everything you say to Home until you turn it off.')}</p>
   <div className="dv-home-voice-actions">
    <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>voice.setMode('conversation',true)}>{t('Turn on open mic')}</button>
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={voice.cancelOpenMic}>{t('Cancel')}</button>
   </div>
  </div>}
  {view.confirm&&<div className="dv-home-voice-confirm" role="group" aria-label={t('Confirm before sending')}>
   <p className="dv-home-voice-ask">{t('{what}. Say yes or click Yes.',{what:view.confirm.readback.split('.')[0]})}</p>
   <q className="dv-home-voice-words">{view.confirm.text}</q>
   <div className="dv-home-voice-actions">
    <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>voice.confirm(view.confirm!.id,true)}>{t('Yes, go ahead')}</button>
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>voice.confirm(view.confirm!.id,false)}>{t('Don’t send')}</button>
    <span className="dv-caption" aria-hidden="true">{t('Cancels in {n}s',{n:String(Math.max(0,Math.ceil((view.confirm.until-now)/1000)))})}</span>
   </div>
  </div>}
  {view.unavailable&&view.unavailableReason!=='models'&&<p className="dv-caption dv-home-voice-note" role="status">{view.unavailable}</p>}
  {view.unavailable&&view.unavailableReason==='models'&&!view.setup&&<div className="dv-home-voice-heard">
   <span className="dv-home-voice-words">{t('Voice runs on this Mac and needs its models first: about 830 MB, downloaded once. Nothing you say leaves the Mac.')}</span>
   <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={voice.install}>{t('Download voice models')}</button>
  </div>}
  {view.setup&&<p className="dv-caption dv-home-voice-note" role="status">{setupLine(view.setup)}
   {view.setup.state==='failed'&&<button type="button" className="dv-btn-quiet" onClick={voice.install}>{t('Try again')}</button>}</p>}
  {view.note&&<p className="dv-caption dv-home-voice-note" role="status">{view.note}</p>}
 </div>;
}

/** One line for where voice is, never a guess: a restart or a lost mic says so. */
export function voiceLine(view:VoiceView):string|null{
 if(!view.on)return null;
 if(view.unavailable)return view.setup||view.unavailableReason!=='models'?null:t('Voice needs its models.');
 if(view.process==='starting')return t('Starting voice…');
 if(view.process==='restarting')return t('Voice is restarting…');
 if(view.process==='failed')return t('Voice couldn’t start.');
 if(view.speaking)return t('Dervo is speaking');
 if(view.state==='hearing')return t('Hearing you…');
 if(view.state==='listening')return view.mode==='conversation'?t('Open mic: listening'):t('Listening…');
 // "Hey Dervo" keeps the mic open, so it says so; hold to talk opens it only while you hold the key.
 if(view.state==='idle')return view.mode==='wake'?t('Mic on. Say “Hey Dervo”'):t('Hold ⌥ Space to talk');
 return null;
}

export function VoiceButton({voice,disabled}:{voice:HomeVoiceControls;disabled?:boolean}){
 const {view}=voice;
 // Held, not "speech heard": the release must always end the talk, wherever the pointer went.
 const held=useRef(false);
 const press=()=>{if(!held.current){held.current=true;voice.talk(true);}};
 const release=()=>{if(held.current){held.current=false;voice.talk(false);}};
 const [holding,setHolding]=useState(false);
 return <span className="dv-home-voice-controls">
  {view.on&&<select className="dv-home-voice-mode" aria-label={t('How voice listens')} value={view.mode}
   onChange={event=>{const value=event.target.value;if(value==='off')void voice.turnOff();else voice.setMode(value as VoiceMode);}}>
   {VOICE_MODES.map(mode=><option key={mode} value={mode}>{modeLabel(mode)}</option>)}
   <option value="off">{t('Turn voice off')}</option>
  </select>}
  <button type="button" className="dv-btn-icon dv-home-mic" data-on={view.on||undefined} data-held={holding||undefined} disabled={disabled}
   aria-label={!view.on?t('Turn on voice'):holding?t('Talking, release to send'):t('Hold to talk')}
   title={view.on?t('Hold to talk, or hold ⌥ Space'):t('Turn on voice')}
   onPointerDown={event=>{event.preventDefault();event.currentTarget.setPointerCapture?.(event.pointerId);setHolding(view.on);press();}}
   onPointerUp={()=>{setHolding(false);release();}} onPointerCancel={()=>{setHolding(false);release();}} onLostPointerCapture={()=>{setHolding(false);release();}}
   onKeyDown={event=>{if((event.key===' '||event.key==='Enter')&&!event.repeat){event.preventDefault();setHolding(view.on);press();}}}
   onKeyUp={event=>{if(event.key===' '||event.key==='Enter'){event.preventDefault();setHolding(false);release();}}}
   onBlur={()=>{setHolding(false);release();}}
   onContextMenu={event=>{event.preventDefault();if(view.on)void voice.turnOff();}}>
   <Mic/>
  </button>
 </span>;
}

function modeLabel(mode:VoiceMode):string{
 if(mode==='wake')return t('“Hey Dervo”');
 if(mode==='conversation')return t('Open mic');
 return t('Hold to talk');
}

function Mic(){return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" stroke="currentColor" strokeWidth="1.3"/><path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>;}
