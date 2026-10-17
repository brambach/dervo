import assert from 'node:assert/strict';
import {test} from 'node:test';
import {newKeyPair,open,seal} from './crypto.ts';
import {PhoneLink,cacheOf,type PhoneState,type SocketLike} from './phone.ts';

/** A paired phone with no relay: frames are fed in by hand, and what it sends is collected. */
function rig(){
  let clock=1_000_000_000_000;
  const mac=newKeyPair();
  const keys=newKeyPair();
  const sent:any[]=[];
  const socket:SocketLike={send:text=>sent.push(JSON.parse(text)),close(){},onopen:null,onmessage:null,onclose:null,onerror:null};
  const state:PhoneState={keys,offer:{v:1,relay:'ws://nowhere',room:'r'.repeat(24),key:'k'.repeat(43),mac:mac.publicKey,name:'Mac'},
    code:null,paired:true,home:null,outbox:[]};
  const phone=new PhoneLink(state,{connect:()=>socket,save:()=>{},device:'iPhone',now:()=>clock});
  (phone as any).socket=socket;
  phone.live={...phone.live,connected:true,macOnline:true};
  const fromMac=(message:unknown,held?:number)=>(phone as any).onFrame(JSON.stringify({t:'env',env:seal(message,mac,keys.publicKey),...(held!==undefined?{held}:{})}));
  const home=(at:number)=>({t:'snapshot',home:{at,mac:'Mac',projects:[],rows:[]}});
  const actions=()=>sent.filter(frame=>frame.t==='send').map(frame=>open(frame.env,mac,keys.publicKey) as any).filter(m=>m?.t==='action');
  return {phone,fromMac,home,actions,tick:(ms:number)=>{clock+=ms;},now:()=>clock};
}

test('the phone reckons the Mac\'s clock from its latest Home, and the relay\'s held time can\'t push it later',()=>{
  const {phone,fromMac,home,tick}=rig();
  const M=5_000_000_000_000;  // the Mac's clock, nowhere near the phone's
  fromMac(home(M),86_400_000);  // a relay claiming it held this for a day
  assert.equal(phone.state.macClock?.mac,M);
  tick(60_000);
  assert.equal(phone.macTime(),M+60_000,'a minute later on the Mac, not a day and a minute');
  assert.equal(phone.act({kind:'stop',projectId:'p',channelId:'c'}).macAt,M+60_000);
});

test('a Mac clock that steps back is followed by a live Home, but not by an older held copy',()=>{
  const {phone,fromMac,home,tick}=rig();
  const M=5_000_000_000_000;
  fromMac(home(M));
  tick(1000);
  fromMac(home(M-3600_000),5000);  // an older copy the relay held
  assert.equal(phone.state.home?.at,M);
  assert.equal(phone.macTime(),M+1000);
  fromMac(home(M-90_000));  // live, or a stale copy with held stripped: within the step-back margin, ignored
  assert.equal(phone.macTime(),M+1000);
  fromMac(home(M-3600_000));  // live: the Mac's clock went back an hour
  assert.equal(phone.state.home?.at,M-3600_000);
  assert.equal(phone.macTime(),M-3600_000);
});

test('a stale Home or thread never moves the Mac time back, so a Stop is never dated too early',()=>{
  const {phone,fromMac,home,tick}=rig();
  const M=5_000_000_000_000;
  fromMac(home(M));
  tick(120_000);
  fromMac(home(M+30_000));  // newer than the last Home, but 90 s stale by now
  assert.equal(phone.macTime(),M+120_000);
  (phone as any).live={...phone.live,thread:{projectId:'p',channelId:'c',name:'t',messages:[],at:0}};
  fromMac({t:'thread',thread:{projectId:'p',channelId:'c',name:'t',messages:[],at:M+60_000}});
  assert.equal(phone.macTime(),M+120_000);
  fromMac({t:'thread',thread:{projectId:'p',channelId:'c',name:'t',messages:[],at:M+180_000}});  // genuinely newer
  assert.equal(phone.macTime(),M+180_000);
});

test('a send tapped before any Home waits, then goes dated from when it was tapped',()=>{
  const {phone,fromMac,home,actions,tick}=rig();
  const pending=phone.act({kind:'send',projectId:'p',channelId:'c',text:'hi'});
  assert.equal(pending.macAt,undefined);
  assert.equal(actions().length,0,'nothing goes undated');
  tick(20_000);
  const M=5_000_000_000_000;
  fromMac(home(M));
  const [action]=actions();
  assert.equal(action?.id,pending.id);
  assert.equal(action.macAt,M-20_000,'the tap was 20 s before this Home');
  assert.equal(phone.state.outbox[0].macAt,M-20_000,'kept, so a resend carries the same time');
});

test('the cached part keeps the Mac time, and removal clears it',()=>{
  const {phone,fromMac,home}=rig();
  fromMac(home(5_000_000_000_000));
  assert.deepEqual(cacheOf(phone.state)?.macClock,phone.state.macClock);
  fromMac({t:'unpaired'});
  assert.equal(phone.state.macClock,undefined);
  assert.equal(cacheOf(phone.state),null);
});
