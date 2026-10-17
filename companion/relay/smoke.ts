// A real Mac link and phone link against a running relay: pair, allow, Home, then a removal the phone learns about after
// it comes back. Run it against `wrangler dev` before deploying and against wss://relay.trydervo.com after.
//   node --experimental-strip-types relay/smoke.ts ws://127.0.0.1:8787
import WebSocket from 'ws';
import {MacLink,type HostCall,type LinkState,type SocketLike} from '../mac/link.ts';
import {readOffer} from '../protocol/pairing.ts';
import {PhoneLink,stateFromOffer} from '../protocol/phone.ts';

const relay=process.argv[2];
if(!relay){console.error('Usage: relay/smoke.ts <ws(s)://relay>');process.exit(2);}
const connect=(url:string)=>new WebSocket(url) as unknown as SocketLike;
async function until<T>(what:string,check:()=>T|undefined|null|false,ms=15000):Promise<T>{
  const end=Date.now()+ms;
  for(;;){const value=check();if(value)return value;if(Date.now()>end)throw new Error('Timed out waiting for '+what);await new Promise(r=>setTimeout(r,50));}
}
let saved:LinkState|null=null;
const store={load:async()=>saved&&structuredClone(saved),save:async(state:LinkState)=>{saved=structuredClone(state);}};
const host:HostCall=async operation=>{
  if(operation==='projects')return [{id:'p1',name:'Smoke test'}] as any;
  if(operation==='status')return {actor:'local-user',lostRuns:[]} as any;
  if(operation==='conversations')return [{id:'c1',name:'Relay smoke',project_id:'p1'}] as any;
  if(operation==='tasks')return [] as any;
  throw new Error('invalid args `operation` for command `local_request`: unknown variant `'+operation+'`');
};
const steps:string[]=[];
const step=(text:string)=>{steps.push(text);console.log('ok  '+text);};
const mac=new MacLink({host,store,relay,mac:'Smoke Mac',connect,pollMs:500});
const phones:PhoneLink[]=[];
try{
  await mac.start();
  await until('the Mac on the relay',()=>mac.connected);step('Mac signed in by proof');
  const offer=readOffer((await mac.pairingOffer()).link)!;
  const phone=new PhoneLink(stateFromOffer(offer),{connect,save:()=>{},device:'Smoke iPhone'});phones.push(phone);
  phone.start();
  const asking=await until('the phone asking',()=>mac.waiting);
  if(asking.digits!==phone.digits)throw new Error('Pairing digits differ');step('phone asked to pair, digits match');
  await mac.allowPhone(asking.pk);
  await until('paired',()=>phone.state.paired);step('Mac allowed it');
  const home=await until('Home on the phone',()=>phone.state.home);
  if(!home.rows.some(row=>row.name==='Relay smoke'))throw new Error('Home is missing the thread');step('Home reached the phone, end to end encrypted');
  phone.stop();
  await mac.unpair(phone.state.keys.publicKey);
  const back=new PhoneLink(phone.state,{connect,save:()=>{},device:'Smoke iPhone'});phones.push(back);
  back.start();
  await until('the removal',()=>!back.state.paired&&back.live.error==='unpaired');step('a removed phone learned it was removed when it came back');
  console.log(`\nRelay smoke passed (${steps.length} steps) on ${relay}`);
}catch(error){console.error('FAIL',(error as Error).message);process.exitCode=1;}
finally{for(const p of phones)p.stop();mac.stop();setTimeout(()=>process.exit(),200);}
