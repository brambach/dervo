import {useEffect} from 'react';
import {listen} from '@tauri-apps/api/event';
import {local} from './api';
import {t} from '@/i18n';
import {MacLink,type HostCall,type LinkState,type SocketLike} from '../../../companion/mac/link.ts';
import {allowPhone,denyPhone,onPhoneLink,phoneLinkStatus,phonePairingOffer,setPhoneApprovals,setPhoneRelay,unpairPhone,type PhoneLinkStatus} from './phoneLinkNative';

/** Dervo on your phone, the Mac side (companion/README.md). It reads Home through the same host operations this window
 * uses and runs a phone's send or stop through them too, so nothing new reaches the host. It connects to the relay only
 * once you've set a relay and paired a phone (or are showing a pairing code); until then it does nothing at all.
 *
 * This window's link is the fallback for a build without the app's own (src/phone, PR #14), which runs with the window
 * closed and keeps its keys in the Keychain. This one keeps them in the window's storage and only reaches Dervo's relay
 * (the window's content security policy allows nothing else). ``phoneControl`` picks one; never both run. */

const STATE='dervo-phone-link';
/** Dervo's own relay (companion/relay, live). Pairing uses it unless you set another under Phone, Relay address. */
export const DEFAULT_RELAY='wss://relay.trydervo.com';
const RELAY='dervo-phone-relay';

const store={
  load:async()=>{try{return JSON.parse(localStorage.getItem(STATE)??'null') as LinkState|null;}catch{return null;}},
  save:async(state:LinkState)=>{try{localStorage.setItem(STATE,JSON.stringify(state));}catch{/* This launch still works. */}},
};

export function relayUrl():string{
  try{return localStorage.getItem(RELAY)??'';}catch{return '';}
}
/** Changing the relay starts a new pairing room, so paired phones are told they're removed first (held for them if
 * they're away) instead of waiting forever on a room nobody uses. */
export async function setRelayUrl(url:string){
  if(link?.phones.length)await link.unpairAll().catch(()=>{});
  try{if(url)localStorage.setItem(RELAY,url);else localStorage.removeItem(RELAY);}catch{/* not kept */}
  stopLink();
}

let link:MacLink|null=null;

function hasPhones(){
  try{const saved=JSON.parse(localStorage.getItem(STATE)??'null') as LinkState|null;return !!saved?.phones.length&&saved.relay===relayUrl();}catch{return false;}
}

/** The running link, started on first use. Null while no relay is set. */
export async function phoneLink():Promise<MacLink|null>{
  const relay=relayUrl();
  if(!relay)return null;
  if(link)return link;
  link=new MacLink({host:local as HostCall,store,relay,mac:t('Your Mac'),connect:url=>new WebSocket(url) as unknown as SocketLike,pollMs:3000});
  await link.start();
  return link;
}
export function currentLink(){return link;}
export function stopLink(){link?.stop();link=null;}

// ---------- one control for both links ----------

/** Which link runs on this Mac. The app's own (Rust, PR #14) when this build has it: it keeps going with the window
 * closed and keeps its keys in the Keychain. Otherwise this window's, above. Never both. */
let native:Promise<boolean>|null=null;
export function hasNativeLink(){
  return native??=phoneLinkStatus().then(()=>true,()=>false);
}

/** What Phone settings shows and does, the same whichever link runs. */
export type PhoneControl={native:boolean;status:PhoneLinkStatus;
  subscribe(changed:()=>void):()=>void;
  setRelay(relay:string|null):Promise<void>;offer():Promise<{link:string;expires:number}>;
  allow(pk:string):Promise<boolean>;deny():Promise<void>;unpair(pk:string):Promise<void>;
  /** False when that phone isn't paired any more. */
  setApprovals(pk:string,on:boolean):Promise<boolean>};

function nativeControl(first:PhoneLinkStatus):PhoneControl{
  const listeners=new Set<()=>void>();
  const control:PhoneControl={native:true,status:first,
    subscribe(changed){
      listeners.add(changed);
      const stop=onPhoneLink(next=>{control.status=next;for(const listener of listeners)listener();});
      return ()=>{listeners.delete(changed);stop();};
    },
    async setRelay(relay){control.status=await setPhoneRelay(relay);for(const listener of listeners)listener();},
    async offer(){const {link,offer}=await phonePairingOffer();return {link,expires:offer.expires};},
    allow:pk=>allowPhone(pk),
    deny:()=>denyPhone(),
    unpair:pk=>unpairPhone(pk),
    setApprovals:(pk,on)=>setPhoneApprovals(pk,on),
  };
  return control;
}

function windowControl():PhoneControl{
  const read=():PhoneLinkStatus=>({relay:relayUrl()||null,connected:!!link?.connected,problem:link?.problem??null,phones:link?.phones??[],waiting:link?.waiting??null});
  const control:PhoneControl={native:false,status:read(),
    subscribe(changed){
      // The link starts on first use and stops when the relay changes, so this checks the current one twice a second.
      const timer=setInterval(()=>{const next=read();if(JSON.stringify(next)!==JSON.stringify(control.status)){control.status=next;changed();}},500);
      return ()=>clearInterval(timer);
    },
    async setRelay(relay){await setRelayUrl(relay??'');control.status=read();},
    async offer(){
      const running=await phoneLink();
      if(!running)throw new Error(t('Set a relay first.'));
      const {link:text,offer}=await running.pairingOffer();
      control.status=read();
      return {link:text,expires:offer.expires};
    },
    allow:async pk=>!!(await link?.allowPhone(pk)),
    deny:async()=>link?.denyPhone(),
    unpair:async pk=>link?.unpair(pk),
    setApprovals:async(pk,on)=>{const done=!!(await link?.setApprovals(pk,on));control.status=read();return done;},
  };
  return control;
}

export async function phoneControl():Promise<PhoneControl>{
  if(await hasNativeLink())return nativeControl(await phoneLinkStatus());
  return windowControl();
}

/** Mounted once in the app: starts the link at launch when a phone is already paired, and reads Home right away when
 * the host says a thread moved (the state spine's dervo-host-event), instead of waiting for the next read. */
export function usePhoneLink(){
  useEffect(()=>{
    let unlisten:(()=>void)|undefined,alive=true;
    // The app's own link reads Home and hears the host itself; this window's link runs only when the build has none.
    void hasNativeLink().then(isNative=>{
      if(isNative||!alive)return;
      if(hasPhones())void phoneLink();
      void listen('dervo-host-event',()=>link?.nudge()).then(stop=>{if(alive)unlisten=stop;else stop();}).catch(()=>{});
    });
    return ()=>{alive=false;unlisten?.();};
  },[]);
}
