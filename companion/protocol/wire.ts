import {isEnvelope,type Challenge,type Envelope} from './crypto.ts';

/** The most phones one Mac pairs with. */
export const MAX_PHONES=20;

/** The plain frames between a device and the relay. The relay routes sealed envelopes and keeps a small mailbox; it
 * learns who is paired with whom (public keys), when the Mac was last connected and the phone's push token, and
 * nothing else. */
export type Role='mac'|'phone';
export type ToRelay=
    /** Answers the relay's challenge: ``proof`` shows the device holds the secret key for ``pk``. */
  |{t:'auth';role:Role;pk:string;key:string;proof:string}
  /** The Mac names the phones that are paired. A phone not on the list can only send the Mac a live pairing hello. */
  |{t:'allow';phones:string[]}
  /** The phone's Expo push token, so the relay can wake it with a content-free notice. */
  |{t:'push';token:string|null}
  /** The Mac's new room key, sent with each new pairing code and after it signs in. Only unpaired phones need it. */
  |{t:'rekey';key:string}
  |{t:'send';env:Envelope}
  /** Mailbox receipts, by envelope nonce: the relay keeps a queued envelope until its recipient says it got it, so a
   * connection that drops mid-delivery loses nothing. Receivers drop duplicates by nonce. */
  |{t:'got';n:string[]}
  |{t:'ping'};
export type FromRelay=
  /** The first frame on every connection. */
  |{t:'challenge';challenge:Challenge}
  /** ``allowed`` is false for a phone the Mac hasn't paired (or has removed). */
  |{t:'ready';macOnline:boolean;macSeen:number|null;allowed:boolean}
  /** ``held``: how long the relay held this envelope before this delivery, in ms, on the relay's clock. */
  |{t:'env';env:Envelope;held?:number}
  |{t:'presence';macOnline:boolean;macSeen:number|null}
  |{t:'error';code:string;detail:string}
  |{t:'pong'};

export const MAX_FRAME=600*1024;

const KEY=/^[A-Za-z0-9_-]{43}$/;
export const isKey=(value:unknown):value is string=>typeof value==='string'&&KEY.test(value);

export function readToRelay(raw:string):ToRelay|null{
  if(typeof raw!=='string'||raw.length>MAX_FRAME)return null;
  let m:ToRelay;
  try{m=JSON.parse(raw);}catch{return null;}
  if(!m||typeof m!=='object')return null;
  switch(m.t){
    case 'auth':return (m.role==='mac'||m.role==='phone')&&isKey(m.pk)&&isKey(m.key)&&typeof m.proof==='string'&&m.proof.length<=200?{t:'auth',role:m.role,pk:m.pk,key:m.key,proof:m.proof}:null;
    case 'allow':return Array.isArray(m.phones)&&m.phones.length<=MAX_PHONES&&m.phones.every(isKey)?{t:'allow',phones:[...new Set(m.phones)]}:null;
    case 'push':return m.token===null||(typeof m.token==='string'&&/^ExponentPushToken\[[A-Za-z0-9_-]{10,100}\]$/.test(m.token))?{t:'push',token:m.token}:null;
    case 'send':return isEnvelope(m.env)?{t:'send',env:m.env}:null;
    case 'got':return Array.isArray(m.n)&&m.n.length<=200&&m.n.every(n=>typeof n==='string'&&n.length<=64)?{t:'got',n:m.n}:null;
    case 'ping':return {t:'ping'};
    case 'rekey':return isKey(m.key)?{t:'rekey',key:m.key}:null;
    default:return null;
  }
}

export function readFromRelay(raw:string):FromRelay|null{
  let m:FromRelay;
  try{m=JSON.parse(raw);}catch{return null;}
  if(!m||typeof m!=='object')return null;
  const seen=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?v:null;
  switch(m.t){
    case 'challenge':return m.challenge&&isKey(m.challenge.pk)&&typeof m.challenge.n==='string'&&/^[A-Za-z0-9_-]{32}$/.test(m.challenge.n)?{t:'challenge',challenge:{pk:m.challenge.pk,n:m.challenge.n}}:null;
    case 'ready':return {t:'ready',macOnline:m.macOnline===true,macSeen:seen(m.macSeen),allowed:m.allowed!==false};
    case 'presence':return {t:'presence',macOnline:m.macOnline===true,macSeen:seen(m.macSeen)};
    case 'env':return isEnvelope(m.env)?{t:'env',env:m.env,...(Number.isSafeInteger(m.held)&&(m.held as number)>=0?{held:m.held as number}:{})}:null;
    case 'error':return {t:'error',code:String(m.code).slice(0,40),detail:String(m.detail).slice(0,300)};
    case 'pong':return {t:'pong'};
    default:return null;
  }
}
