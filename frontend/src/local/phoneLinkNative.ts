import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';

/** Dervo on your phone, Mac side, as the app runs it (src/phone, b19/phone-link): in the app's own process, so it keeps
 * going with the window closed, with its keys in the Keychain. The window only shows it and passes your clicks on. */

/** A paired phone. ``pairedAt`` is ms since epoch. */
/** ``approvals``: it may approve a run's scope and accept results. Off until you turn it on. */
export type PairedPhone={pk:string;device:string;pairedAt:number;approvals?:boolean};
/** A phone that sent the right code and waits for Allow. ``digits`` show on both screens; ``expires`` is ms. */
export type AskingPhone={pk:string;device:string;digits:string;expires:number};
export type PhoneLinkStatus={
  /** Null until a relay is set, and the link is off. */
  relay:string|null;
  connected:boolean;
  /** Set when another Dervo signed in to this room ("replaced" or "other_mac"); this one stopped. */
  problem:string|null;
  phones:PairedPhone[];
  waiting:AskingPhone|null};
/** The QR's link (dervo://pair?d=…) and what it holds. The code works once and expires in 10 minutes. */
export type PairingOffer={link:string;offer:{v:1;relay:string;room:string;key:string;mac:string;code:string;name:string;expires:number}};

export const PHONE_LINK_EVENT='dervo-phone-link';

export const phoneLinkStatus=()=>invoke<PhoneLinkStatus>('phone_link_status');
/** wss:// (or ws:// on this Mac's loopback, for development). A different relay removes every paired phone first,
 * sealed, so each learns it; null turns the link off. */
export const setPhoneRelay=(relay:string|null)=>invoke<PhoneLinkStatus>('phone_link_set_relay',{relay});
export const phonePairingOffer=()=>invoke<PairingOffer>('phone_pairing_offer');
/** False when that phone is no longer the one asking (it expired or another took its place). */
export const allowPhone=(pk:string)=>invoke<boolean>('phone_allow',{pk});
export const denyPhone=()=>invoke<void>('phone_deny');
export const unpairPhone=(pk:string)=>invoke<void>('phone_unpair',{pk});
/** Let a paired phone approve scopes and accept results, or stop it. Answers and messages never need this. */
export const setPhoneApprovals=(pk:string,on:boolean)=>invoke<boolean>('phone_set_approvals',{pk,on});

/** Calls ``changed`` with each new status: a phone asking, allowed or removed, the relay connecting or dropping. */
export function onPhoneLink(changed:(status:PhoneLinkStatus)=>void):()=>void{
  let alive=true,stop:(()=>void)|null=null;
  try{
    void listen<PhoneLinkStatus>(PHONE_LINK_EVENT,event=>{if(alive)changed(event.payload);})
      .then(unlisten=>{if(alive)stop=unlisten;else unlisten();}).catch(()=>{/* no Tauri here */});
  }catch{/* no Tauri here */}
  return()=>{alive=false;stop?.();};
}
