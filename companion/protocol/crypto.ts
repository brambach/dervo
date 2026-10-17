import nacl from 'tweetnacl';
import {decode,encode,fromUtf8,utf8} from './b64.ts';

/** A device's long-lived key pair for NaCl box (X25519, XSalsa20-Poly1305). Keys travel as base64url. */
export type KeyPair={publicKey:string;secretKey:string};
/** What the relay sees: who it's from and to, a nonce and ciphertext. Never the content. */
export type Envelope={v:1;from:string;to:string;n:string;c:string;
  /** Mailbox hint for the relay: 'q' appends to the recipient's queue, a slot name keeps only the newest one in that
   * slot (the Home snapshot), and absent means deliver live or drop. */
  keep?:'q'|'home';
  /** Asks the relay to wake the phone with a content-free push if it isn't connected: ``wake`` when something needs
   * you, ``news`` when something finished. Neither says what or where. */
  wake?:boolean;news?:boolean};

export const MAX_PLAINTEXT=256*1024;

export function newKeyPair():KeyPair{
  const pair=nacl.box.keyPair();
  return {publicKey:encode(pair.publicKey),secretKey:encode(pair.secretKey)};
}

export function randomToken(bytes=32):string{return encode(nacl.randomBytes(bytes));}

function key(text:string,length:number):Uint8Array{
  const bytes=decode(text);
  if(!bytes||bytes.length!==length)throw new Error('Malformed key');
  return bytes;
}

/** Seal a JSON value for one recipient. A fresh random nonce each time, so nothing is ever reused. */
export function seal(value:unknown,mine:KeyPair,theirPublic:string,extra:Pick<Envelope,'keep'|'wake'|'news'>={}):Envelope{
  const plain=utf8(JSON.stringify(value));
  if(plain.length>MAX_PLAINTEXT)throw new Error('Message too large to send to your phone');
  const nonce=nacl.randomBytes(nacl.box.nonceLength);
  const box=nacl.box(plain,nonce,key(theirPublic,32),key(mine.secretKey,32));
  return {v:1,from:mine.publicKey,to:theirPublic,n:encode(nonce),c:encode(box),...extra};
}

/** Open an envelope addressed to us from a sender we name. Null for anything that doesn't authenticate: a forged sender,
 * a tampered byte, the wrong recipient. Callers drop those silently. */
export function open(envelope:unknown,mine:KeyPair,expectedFrom:string):unknown|null{
  if(!isEnvelope(envelope)||envelope.to!==mine.publicKey||envelope.from!==expectedFrom)return null;
  const nonce=decode(envelope.n),box=decode(envelope.c);
  if(!nonce||nonce.length!==nacl.box.nonceLength||!box)return null;
  let plain:Uint8Array|null;
  try{plain=nacl.box.open(box,nonce,key(expectedFrom,32),key(mine.secretKey,32));}catch{return null;}
  if(!plain)return null;
  try{return JSON.parse(fromUtf8(plain));}catch{return null;}
}

export function isEnvelope(value:unknown):value is Envelope{
  const e=value as Envelope;
  return !!e&&typeof e==='object'&&e.v===1&&[e.from,e.to,e.n,e.c].every(field=>typeof field==='string'&&field.length>0&&field.length<=512*1024)
    &&(e.keep===undefined||e.keep==='q'||e.keep==='home')&&(e.wake===undefined||typeof e.wake==='boolean')&&(e.news===undefined||typeof e.news==='boolean');
}

/** Short, stable id for a public key, for logs and the relay's routing table. */
export function fingerprint(publicKey:string):string{
  return encode(nacl.hash(utf8(publicKey)).slice(0,9));
}

/** sha512 of a room key, truncated, as the relay stores it: the relay never keeps the key itself. */
export function roomKeyHash(roomKey:string):string{
  return encode(nacl.hash(utf8('dervo-room:'+roomKey)).slice(0,32));
}

/** Sign-in proof for the relay. The relay sends each new connection a throwaway public key and a nonce. The device
 * answers with an HMAC over who it says it is, keyed from its X25519 secret and the relay's throwaway key through a
 * labelled hash. That key is never a box key, so no answer can ever be used as, or help forge, a sealed envelope, whatever
 * key and nonce a hostile relay picks. Only the holder of the secret key for ``pk`` can answer, so a QR code or a removed
 * phone, which carry only public keys, can't sign in as the Mac or as another phone.
 *
 * Wire spec v2 (also the Rust link's, src/phone/proto.rs):
 *   dh        = X25519(secret, challenge.pk); refuse if all zero
 *   key       = SHA-512("dervo-relay-proof/v2" || dh)[0..32]
 *   transcript= UTF-8 of ["dervo-relay-proof/v2", role, room, pk, challenge.pk, challenge.n].join("\n")
 *   proof     = base64url(HMAC-SHA-512(key, transcript)[0..32]) */
const PROOF_LABEL='dervo-relay-proof/v2';
export type Challenge={pk:string;n:string};
export function newChallenge():{challenge:Challenge;secret:string}{
  const pair=nacl.box.keyPair();
  return {challenge:{pk:encode(pair.publicKey),n:encode(nacl.randomBytes(24))},secret:encode(pair.secretKey)};
}
function proofKey(secret:Uint8Array,theirs:Uint8Array):Uint8Array|null{
  const dh=nacl.scalarMult(secret,theirs);
  if(dh.every(byte=>byte===0))return null;  // a low-order key: anyone could compute this
  return nacl.hash(concat(utf8(PROOF_LABEL),dh)).slice(0,32);
}
function concat(...parts:Uint8Array[]){
  const out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));
  let at=0;for(const part of parts){out.set(part,at);at+=part.length;}
  return out;
}
function hmac(keyBytes:Uint8Array,message:Uint8Array):Uint8Array{
  const block=128;
  const k=new Uint8Array(block);k.set(keyBytes.length>block?nacl.hash(keyBytes):keyBytes);
  const inner=k.map(b=>b^0x36),outer=k.map(b=>b^0x5c);
  return nacl.hash(concat(outer,nacl.hash(concat(inner,message))));
}
function transcript(role:string,room:string,pk:string,challenge:Challenge){
  return utf8([PROOF_LABEL,role,room,pk,challenge.pk,challenge.n].join('\n'));
}
export function prove(challenge:Challenge,mine:KeyPair,role:'mac'|'phone',room:string):string{
  const k=proofKey(key(mine.secretKey,32),key(challenge.pk,32));
  if(!k)throw new Error('Refused a relay challenge with a low-order key');
  return encode(hmac(k,transcript(role,room,mine.publicKey,challenge)).slice(0,32));
}
export function checkProof(challenge:Challenge,secret:string,role:'mac'|'phone',room:string,claimed:string,proof:string):boolean{
  const given=decode(proof);
  if(!given||given.length!==32)return false;
  let k:Uint8Array|null;
  try{k=proofKey(key(secret,32),key(claimed,32));}catch{return false;}
  if(!k)return false;
  const want=hmac(k,transcript(role,room,claimed,challenge)).slice(0,32);
  return nacl.verify(want,given);
}

/** Six digits both screens show while pairing, bound to the Mac's key, the phone's key and the one-time code, so a phone
 * that isn't yours can't show the same digits by choosing its key. Spec: SHA-512("dervo-pair/v2\n"+macPk+"\n"+phonePk+
 * "\n"+code), first 4 bytes big-endian unsigned, mod 1,000,000, zero-padded to 6. */
export function pairingDigits(macPk:string,phonePk:string,code:string):string{
  const hash=nacl.hash(utf8(['dervo-pair/v2',macPk,phonePk,code].join('\n')));
  const number=(((hash[0]<<24)>>>0)+(hash[1]<<16)+(hash[2]<<8)+hash[3])%1_000_000;
  return String(number).padStart(6,'0');
}
