import {useSyncExternalStore} from 'react';
import {AppState,Platform} from 'react-native';
import * as SecureStore from 'expo-secure-store';
import {File,Paths} from 'expo-file-system';
import {PhoneLink,cacheOf,stateFromOffer,type PhoneCache,type PhoneState,type SocketLike} from '../../companion/protocol/phone.ts';
import {readOffer} from '../../companion/protocol/pairing.ts';
import {stopPush} from './push';

/** The one phone link for the app, and what it saves.
 *
 * The keys and the pairing go in the Keychain (SecureStore, this device only, after first unlock, so a notice can be
 * handled while the phone is locked). The last Home and your pending sends go in a file in the app's own folder, which
 * iOS encrypts at rest; they're too large for the Keychain. Nothing goes anywhere else. */

const SECRET='dervo.phone.pairing';
/** A pairing you removed on this phone while it couldn't reach the relay: kept only until the removal gets through. */
const LEAVING='dervo.phone.leaving';
const CACHE='dervo-phone-cache.json';
type Secret=Pick<PhoneState,'keys'|'offer'|'code'|'paired'>;
type Cache=PhoneCache;

const web=Platform.OS==='web';
const keychain={keychainAccessible:SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY};

async function readSecret():Promise<Secret|null>{
  try{
    const raw=web?globalThis.localStorage?.getItem(SECRET):await SecureStore.getItemAsync(SECRET,keychain);
    return raw?JSON.parse(raw):null;
  }catch{return null;}
}
async function writeSecret(secret:Secret|null){
  const raw=secret&&JSON.stringify(secret);
  if(web){if(raw)globalThis.localStorage?.setItem(SECRET,raw);else globalThis.localStorage?.removeItem(SECRET);return;}
  if(raw)await SecureStore.setItemAsync(SECRET,raw,keychain);else await SecureStore.deleteItemAsync(SECRET,keychain);
}
function readCache():Cache|null{
  try{
    if(web){const raw=globalThis.localStorage?.getItem(CACHE);return raw?JSON.parse(raw):null;}
    const file=new File(Paths.cache,CACHE);
    return file.exists?JSON.parse(file.textSync()):null;
  }catch{return null;}
}
function writeCache(cache:Cache|null){
  try{
    if(web){if(cache)globalThis.localStorage?.setItem(CACHE,JSON.stringify(cache));else globalThis.localStorage?.removeItem(CACHE);return;}
    // The caches folder isn't in iCloud or device backups, so thread names and pending messages don't leave the phone.
    const file=new File(Paths.cache,CACHE);
    if(cache)file.write(JSON.stringify(cache));else if(file.exists)file.delete();
  }catch{/* The next change writes it again. */}
}

let link:PhoneLink|null=null;
let loaded=false;
let version=0;
const listeners=new Set<()=>void>();
function bump(){version++;for(const listener of listeners)listener();}

let lastSecret='';
function save(state:PhoneState){
  // The Keychain is written only when the pairing itself changes, not on every Home that arrives.
  const secret=JSON.stringify(state.paired||state.code?{keys:state.keys,offer:state.offer,code:state.code,paired:state.paired}:null);
  if(secret!==lastSecret){lastSecret=secret;writeSecret(state.paired||state.code?{keys:state.keys,offer:state.offer,code:state.code,paired:state.paired}:null).catch(()=>{lastSecret='';/* the next change writes it again */});}
  writeCache(cacheOf(state));
}

function device(){return Platform.OS==='ios'?'iPhone':Platform.OS==='web'?'Browser preview':'Phone';}

function attach(state:PhoneState){
  link?.stop();
  link=new PhoneLink(state,{connect:url=>new WebSocket(url) as unknown as SocketLike,save,device:device()});
  link.subscribe(bump);
  link.start();
}

type Leaving=Pick<PhoneState,'keys'|'offer'>;
/** Changes to the leaving list run one at a time, so two removals finishing together can't undo each other. */
let leavingQueue:Promise<void>=Promise.resolve();
function updateLeaving(change:(list:Leaving[])=>Leaving[]){
  const next=leavingQueue.then(async()=>store(LEAVING,change((await stored<Leaving[]>(LEAVING))??[])));
  leavingQueue=next.catch(()=>{});  // one failed write never holds up the next
  return next;
}
/** Tell the Mac this phone is gone once there's a connection, then forget the keys for good. */
function leave(pairing:Leaving){
  const leaver=new PhoneLink({...pairing,code:null,paired:true,home:null,outbox:[]},{connect:url=>new WebSocket(url) as unknown as SocketLike,save:()=>{},device:device()});
  const off=leaver.subscribe(()=>{
    if(!leaver.live.connected)return;
    off();
    leaver.unpair();
    void updateLeaving(list=>list.filter(item=>item.keys.publicKey!==pairing.keys.publicKey));
  });
  leaver.start();
}
async function store(key:string,value:unknown){
  try{
    const raw=value===null?null:JSON.stringify(value);
    if(web){if(raw)globalThis.localStorage?.setItem(key,raw);else globalThis.localStorage?.removeItem(key);return;}
    if(raw)await SecureStore.setItemAsync(key,raw,keychain);else await SecureStore.deleteItemAsync(key,keychain);
  }catch{/* at worst the Mac keeps listing it until you remove it there */}
}
async function stored<T>(key:string):Promise<T|null>{
  try{const raw=web?globalThis.localStorage?.getItem(key):await SecureStore.getItemAsync(key,keychain);return raw?JSON.parse(raw):null;}catch{return null;}
}

export async function load(){
  if(loaded)return;
  // Only the keys and where to reach the Mac are kept for this, never Home or your messages.
  for(const pairing of await stored<Leaving[]>(LEAVING)??[])leave(pairing);
  const secret=await readSecret();
  if(secret)attach({...secret,...(readCache()??{home:null,outbox:[]})});
  loaded=true;
  bump();
  // Back in the foreground: reconnect if needed, and ask for a fresh Home now rather than at the next beat.
  AppState.addEventListener('change',next=>{if(next==='active')refresh();});
}

/** Pair from a scanned (or pasted) code. False when the code isn't a Dervo pairing code or has expired. */
export function pair(text:string):boolean{
  const offer=readOffer(text.trim());
  if(!offer)return false;
  const state=stateFromOffer(offer);
  save(state);
  attach(state);
  bump();
  return true;
}

/** Reconnect if needed and ask the Mac for Home (and the open thread) again. */
export function refresh(){link?.wake();link?.hello();}

export function forget(){
  void stopPush();
  // Offline, the removal can't reach the Mac now, so it's kept and sent the next time there's a connection.
  if(link&&link.state.paired&&!link.live.connected){
    const pairing:Leaving={keys:link.state.keys,offer:link.state.offer};
    void updateLeaving(list=>[...list,pairing]).then(()=>leave(pairing));
    link.stop();
  }
  else link?.unpair();
  link=null;
  writeSecret(null).catch(()=>{});
  writeCache(null);
  bump();
}

export const current=()=>link;
export function useLink(){
  useSyncExternalStore(callback=>{listeners.add(callback);return ()=>{listeners.delete(callback);};},()=>version,()=>version);
  return {loaded,link,state:link?.state??null,live:link?.live??null};
}
