import {decode,encode,fromUtf8,utf8} from './b64.ts';

/** What the Mac shows as a QR code. Everything the phone needs to find the Mac and prove it saw the screen.
 * ``code`` is one-time: the Mac forgets it after one phone pairs or after it expires. */
export type PairingOffer={v:1;relay:string;room:string;key:string;mac:string;code:string;name:string;expires:number};

const SCHEME='dervo://pair?d=';

export function offerLink(offer:PairingOffer):string{return SCHEME+encode(utf8(JSON.stringify(offer)));}

/** The offer in a scanned link, or null with nothing guessed. Only wss relays (ws only on this device's loopback, for
 * development) so a code can never send the phone somewhere in the clear. */
export function readOffer(link:string,now=Date.now()):PairingOffer|null{
  if(typeof link!=='string'||!link.startsWith(SCHEME)||link.length>4096)return null;
  const bytes=decode(link.slice(SCHEME.length));
  if(!bytes)return null;
  let offer:PairingOffer;
  try{offer=JSON.parse(fromUtf8(bytes));}catch{return null;}
  if(!offer||offer.v!==1)return null;
  for(const field of ['relay','room','key','mac','code','name'] as const)if(typeof offer[field]!=='string'||!offer[field])return null;
  if(typeof offer.expires!=='number'||offer.expires<now)return null;
  if(!safeRelay(offer.relay))return null;
  if(decode(offer.mac)?.length!==32||decode(offer.key)?.length!==32||decode(offer.code)?.length!==32)return null;
  if(!/^[A-Za-z0-9_-]{16,64}$/.test(offer.room))return null;
  return {v:1,relay:offer.relay,room:offer.room,key:offer.key,mac:offer.mac,code:offer.code,name:offer.name.slice(0,80),expires:offer.expires};
}

export function safeRelay(url:string):boolean{
  try{
    const parsed=new URL(url);
    if(parsed.protocol==='wss:')return true;
    return parsed.protocol==='ws:'&&['127.0.0.1','localhost','[::1]'].includes(parsed.hostname);
  }catch{return false;}
}
