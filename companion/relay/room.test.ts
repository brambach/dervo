import assert from 'node:assert/strict';
import {test} from 'node:test';
import {MemoryStorage,Room,LIMITS,type Socket} from './room.ts';
import {newKeyPair,prove,randomToken,seal,type Challenge,type KeyPair} from '../protocol/crypto.ts';
import type {FromRelay} from '../protocol/wire.ts';

const ROOM='room-0123456789abcdef';

class Fake implements Socket{
  frames:FromRelay[]=[];closed:string|null=null;
  send(text:string){this.frames.push(JSON.parse(text));}
  close(_code?:number,reason?:string){this.closed=reason??'closed';}
  /** Frames after the relay's challenge. */
  after(){return this.frames.filter(f=>f.t!=='challenge');}
  challenge(){return (this.frames.find(f=>f.t==='challenge') as {challenge:Challenge}).challenge;}
  envs(){return this.frames.filter(f=>f.t==='env').map(f=>(f as {env:{n:string;keep?:string}}).env);}
  errors(){return this.frames.filter(f=>f.t==='error').map(f=>(f as {code:string}).code);}
}
function setup(clock={t:1000}){
  const pushes:string[]=[];
  const kinds:string[]=[];
  const storage=new MemoryStorage();
  const room=new Room(storage,async(token,kind)=>{pushes.push(token);kinds.push(kind);},()=>clock.t);
  const key=randomToken();
  const join=async(role:'mac'|'phone',pair:KeyPair,k=key)=>{
    const s=new Fake();room.connect(s,ROOM);
    await room.message(s,JSON.stringify({t:'auth',role,pk:pair.publicKey,key:k,proof:prove(s.challenge(),pair,role,ROOM)}));
    return s;
  };
  const say=(s:Fake,frame:unknown)=>room.message(s,JSON.stringify(frame));
  return {room,storage,key,join,say,pushes,kinds,clock};
}

test('only a Mac opens a room, and the key has to match after that',async()=>{
  const {join}=setup();
  const mac=newKeyPair(),phone=newKeyPair(),other=newKeyPair();
  const early=await join('phone',phone);
  assert.deepEqual(early.errors(),['no_room']);assert.ok(early.closed);
  const m=await join('mac',mac);
  assert.equal(m.after()[0].t,'ready');
  const wrong=await join('phone',phone,randomToken());
  assert.deepEqual(wrong.errors(),['bad_key']);
  const second=await join('mac',other);
  assert.deepEqual(second.errors(),['other_mac'],'one Mac per room');
});

test('frames before auth, and envelopes from someone else\'s key, are refused',async()=>{
  const {room,join,say}=setup();
  const s=new Fake();room.connect(s,ROOM);
  await say(s,{t:'ping'});
  assert.deepEqual(s.errors(),['auth_first']);
  const mac=newKeyPair(),phone=newKeyPair(),liar=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const p=await join('phone',phone);
  await say(p,{t:'send',env:seal({t:'hello'},liar,mac.publicKey)});
  assert.deepEqual(p.errors(),['not_you']);
  assert.equal(m.envs().length,0);
});

test('a stranger phone can only reach a connected Mac live, a few times a minute, and nothing is stored',async()=>{
  const {join,say,storage}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  const p=await join('phone',phone);
  for(let i=0;i<LIMITS.strangerPerMinute;i++)await say(p,{t:'send',env:seal({t:'pair'},phone,mac.publicKey,{keep:'q'})});
  assert.equal(m.envs().length,LIMITS.strangerPerMinute);
  assert.equal(m.envs()[0].keep,undefined,'never stored');
  await say(p,{t:'send',env:seal({t:'pair'},phone,mac.publicKey)});
  assert.deepEqual(p.errors(),['rate']);
  assert.equal([...storage.data.keys()].filter(k=>k.startsWith('box:')||k.startsWith('env:')).length,0);
  m.close();
});

test('a paired phone\'s messages wait for an away Mac and are kept until it says it got them',async()=>{
  const {room,join,say}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  await room.close(m);
  const p=await join('phone',phone);
  assert.deepEqual(p.after()[0],{t:'ready',macOnline:false,macSeen:1000,allowed:true});
  const env=seal({t:'action'},phone,mac.publicKey,{keep:'q'});
  await say(p,{t:'send',env});
  const live=seal({t:'hello'},phone,mac.publicKey);
  await say(p,{t:'send',env:live});  // not kept: dropped while away
  const back=await join('mac',mac);
  assert.deepEqual(back.envs().map(e=>e.n),[env.n]);
  assert.ok(p.frames.some(f=>f.t==='presence'&&f.macOnline),'the phone hears the Mac is back');
  await room.close(back);
  const again=await join('mac',mac);
  assert.deepEqual(again.envs().map(e=>e.n),[env.n],'redelivered: no receipt yet');
  await say(again,{t:'got',n:[env.n]});
  await room.close(again);
  const third=await join('mac',mac);
  assert.equal(third.envs().length,0);
});

test('the Mac\'s Home snapshot keeps only the newest copy, and held envelopes expire after a week',async()=>{
  const {room,join,say,clock}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const first=seal({t:'snapshot',n:1},mac,phone.publicKey,{keep:'home'});
  const second=seal({t:'snapshot',n:2},mac,phone.publicKey,{keep:'home'});
  const ack=seal({t:'ack'},mac,phone.publicKey,{keep:'q'});
  for(const env of [first,second,ack])await say(m,{t:'send',env});
  const p=await join('phone',phone);
  assert.deepEqual(p.envs().map(e=>e.n),[second.n,ack.n]);
  await room.close(p);
  clock.t+=LIMITS.heldMs+1;
  const later=await join('phone',phone);
  assert.equal(later.envs().length,0);
});

test('removing a phone on the Mac closes it and deletes everything held for it',async()=>{
  const {join,say,storage}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home'})});
  const p=await join('phone',phone);
  await say(p,{t:'push',token:'ExponentPushToken[abcdefghijklmn]'});
  await say(m,{t:'allow',phones:[]});
  assert.deepEqual(p.errors(),['unpaired']);
  assert.ok(p.closed);
  assert.equal([...storage.data.keys()].some(k=>k.includes(phone.publicKey)),false);
  assert.ok(!JSON.stringify([...storage.data.values()]).includes('ExponentPushToken'),'push token forgotten');
});

test('a phone that\'s away gets one content-free push a minute when the Mac asks, never one for a live phone',async()=>{
  const {room,join,say,pushes,clock}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const p=await join('phone',phone);
  await say(p,{t:'push',token:'ExponentPushToken[abcdefghijklmn]'});
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',wake:true})});
  assert.equal(pushes.length,0,'connected: no push');
  await room.close(p);
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',wake:true})});
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',wake:true})});
  assert.deepEqual(pushes,['ExponentPushToken[abcdefghijklmn]']);
  clock.t+=LIMITS.pushEveryMs;
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',wake:true})});
  assert.equal(pushes.length,2);
});

test('a finished thread sends the quieter push, and the phone never sees either flag',async()=>{
  const {room,join,say,kinds,clock}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const p=await join('phone',phone);
  await say(p,{t:'push',token:'ExponentPushToken[abcdefghijklmn]'});
  await room.close(p);
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',news:true})});
  // A thread that needs you a moment after one finished still gets its notice.
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',wake:true})});
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',news:true})});
  assert.deepEqual(kinds,['done','needs'],'one of each kind a minute');
  clock.t+=LIMITS.pushEveryMs;
  const back=await join('phone',phone);
  const held=back.frames.find((frame:any)=>frame.t==='env') as any;
  assert.ok(held&&held.env.wake===undefined&&held.env.news===undefined);
  // And live: a connected phone gets no push and never sees the flags either.
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home',wake:true})});
  const live=back.frames.filter((frame:any)=>frame.t==='env').at(-1) as any;
  assert.ok(live&&live.env.wake===undefined&&live.env.news===undefined&&kinds.length===2);
});

test('the relay stores nothing it could read',async()=>{
  const {join,say,storage,key}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  await say(m,{t:'send',env:seal({t:'snapshot',home:{secret:'Fix the payroll export'}},mac,phone.publicKey,{keep:'home'})});
  const dump=JSON.stringify([...storage.data.values()]);
  assert.ok(!dump.includes('payroll'));
  assert.ok(!dump.includes(key),'room key kept only as a hash');
  assert.ok(!dump.includes(mac.secretKey)&&!dump.includes(phone.secretKey));
});

test('a reconnecting Mac replaces its old socket instead of fighting it',async()=>{
  const {join}=setup();
  const mac=newKeyPair();
  const a=await join('mac',mac);
  const b=await join('mac',mac);
  assert.deepEqual(a.errors(),['replaced']);
  assert.equal(b.after()[0].t,'ready');
});

test('signing in needs the secret key, so a QR code or a removed phone can\'t pose as the Mac or a phone',async()=>{
  const {room,join,say,key}=setup();
  const mac=newKeyPair(),phone=newKeyPair(),thief=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  for(const [role,pk] of [['mac',mac.publicKey],['phone',phone.publicKey]] as const){
    const s=new Fake();room.connect(s,ROOM);
    // The thief has the room key and the public key from the QR, and answers with its own secret key.
    await say(s,{t:'auth',role,pk,key,proof:prove(s.challenge(),thief,role,ROOM)});
    assert.deepEqual(s.errors(),['bad_proof']);
  }
  // A proof made for one connection doesn't work on another.
  const a=new Fake();room.connect(a,ROOM);
  const b=new Fake();room.connect(b,ROOM);
  await say(b,{t:'auth',role:'mac',pk:mac.publicKey,key,proof:prove(a.challenge(),mac,'mac',ROOM)});
  assert.deepEqual(b.errors(),['bad_proof']);
  assert.equal(m.closed,null,'the real Mac stays connected');
});

test('queued envelopes wait for a receipt even when the recipient looks connected',async()=>{
  const {room,join,say}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const p=await join('phone',phone);
  const env=seal({t:'ack'},mac,phone.publicKey,{keep:'q'});
  await say(m,{t:'send',env});
  assert.deepEqual(p.envs().map(e=>e.n),[env.n],'delivered live');
  // The phone's socket was half open and nothing read it.
  await room.close(p);
  const back=await join('phone',phone);
  assert.deepEqual(back.envs().map(e=>e.n),[env.n],'held until the phone says it got it');
});

test('every envelope is stored on its own, and the sweep purges ones held a week without anyone coming back',async()=>{
  const {room,join,say,storage,clock}=setup();
  const alarms:number[]=[];
  storage.setAlarm=async at=>{alarms.push(at);};
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  for(let i=0;i<3;i++)await say(m,{t:'send',env:seal({i},mac,phone.publicKey,{keep:'q'})});
  await say(m,{t:'send',env:seal({},mac,phone.publicKey,{keep:'home'})});
  const held=()=>[...storage.data.keys()].filter(k=>k.startsWith('env:'));
  assert.equal(held().length,4);
  assert.ok(alarms.length>0,'an alarm is set for the purge');
  clock.t+=LIMITS.heldMs+1;
  await room.sweep();
  assert.equal(held().length,0);
  assert.equal(storage.data.has('box:'+phone.publicKey),false);
});

test('a phone the Mac removed signs in as not allowed',async()=>{
  const {join,say}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[]});
  const p=await join('phone',phone);
  assert.deepEqual(p.after()[0],{t:'ready',macOnline:true,macSeen:1000,allowed:false});
});

test('a removed phone or an old QR can\'t crowd out the Mac: strangers are capped and timed out',async()=>{
  const {room,join,say,clock}=setup();
  const mac=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[]});
  const strangers:Fake[]=[];
  for(let i=0;i<LIMITS.strangers+3;i++)strangers.push(await join('phone',newKeyPair()));
  assert.equal(strangers.filter(s=>s.errors().includes('full')).length,3);
  // A refused socket is closed; the host reports that back to the room.
  for(const s of strangers.filter(s=>s.closed))await room.close(s);
  // Half-open connections that never answer the challenge are capped too.
  const waiting:Fake[]=[];
  for(let i=0;i<LIMITS.waiting+2;i++){const s=new Fake();room.connect(s,ROOM);waiting.push(s);}
  assert.equal(waiting.filter(s=>s.errors().includes('busy')).length,2);
  for(const s of waiting)await room.close(s);
  // The Mac reconnecting still gets in.
  await room.close(m);
  const back=await join('mac',mac);
  assert.equal(back.after()[0].t,'ready');
  clock.t+=LIMITS.strangerMs;
  for(const s of strangers.slice(0,LIMITS.strangers))await room.expire(s);
  assert.ok(strangers.slice(0,LIMITS.strangers).every(s=>s.errors().includes('stranger_timeout')));
});

test('the room holds at most LIMITS.roomBytes across every mailbox',async()=>{
  const {join,say,storage}=setup();
  const mac=newKeyPair();
  const phones=Array.from({length:8},()=>newKeyPair());
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:phones.map(p=>p.publicKey)});
  const big='x'.repeat(200*1024);
  for(const phone of phones)for(let i=0;i<15;i++)await say(m,{t:'send',env:seal({big},mac,phone.publicKey,{keep:'q'})});
  const held=[...storage.data.entries()].filter(([k])=>k.startsWith('env:')).reduce((sum,[,v])=>sum+v.length,0);
  assert.ok(held<=LIMITS.roomBytes*1.05,`held ${held} bytes`);
  assert.ok(held>LIMITS.roomBytes/2,'still holds a useful amount');
});

test('a proof is bound to its role, room and connection, and a low-order key never signs in',async()=>{
  const {room,join,say,key}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const asMac=new Fake();room.connect(asMac,ROOM);
  // A phone's own proof, offered as the Mac's.
  await say(asMac,{t:'auth',role:'mac',pk:phone.publicKey,key,proof:prove(asMac.challenge(),phone,'phone',ROOM)});
  assert.deepEqual(asMac.errors(),['bad_proof']);
  const otherRoom=new Fake();room.connect(otherRoom,ROOM);
  await say(otherRoom,{t:'auth',role:'phone',pk:phone.publicKey,key,proof:prove(otherRoom.challenge(),phone,'phone','some-other-room-id')});
  assert.deepEqual(otherRoom.errors(),['bad_proof']);
  const zero=new Fake();room.connect(zero,ROOM);
  await say(zero,{t:'auth',role:'phone',pk:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',key,proof:'A'.repeat(43)});
  assert.deepEqual(zero.errors(),['bad_proof']);
});

test('a new pairing code\'s room key shuts out old QRs, but not the Mac or paired phones',async()=>{
  const {join,say,key}=setup();
  const mac=newKeyPair(),phone=newKeyPair(),stranger=newKeyPair(),lingering=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const early=await join('phone',lingering);  // came in with the old QR and is still pairing
  const fresh=randomToken();
  await say(m,{t:'rekey',key:fresh});
  assert.deepEqual(early.errors(),['bad_key'],'unpaired phones on the old key go');
  assert.deepEqual((await join('phone',stranger,key)).errors(),['bad_key']);
  assert.equal((await join('phone',stranger,fresh)).after()[0].t,'ready');
  assert.equal((await join('phone',phone,key)).after()[0].t,'ready','a paired phone signs in by its key');
  assert.equal((await join('mac',mac,key)).after()[0].t,'ready');
  const p=await join('phone',phone);
  await say(p,{t:'rekey',key:randomToken()});
  assert.deepEqual(p.errors(),['not_mac']);
});

test('a removed phone collects the Mac\'s sealed removal later, and one key gets one unpaired connection',async()=>{
  const {room,join,say,key}=setup();
  const mac=newKeyPair(),phone=newKeyPair();
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:[phone.publicKey]});
  const notice=seal({t:'unpaired'},mac,phone.publicKey,{keep:'q'});
  await say(m,{t:'send',env:notice});
  await say(m,{t:'allow',phones:[]});
  await say(m,{t:'rekey',key:randomToken()});
  // Away through all that, then back with the old room key.
  const back=await join('phone',phone,key);
  assert.deepEqual(back.after()[0],{t:'ready',macOnline:true,macSeen:1000,allowed:false});
  assert.deepEqual(back.envs().map(e=>e.n),[notice.n]);
  const twice=await join('phone',phone,key);
  assert.deepEqual(twice.errors(),['full']);
  // It collects, and that's all: it can't write to the Mac, spend the pairing budget or take a stranger slot.
  await say(back,{t:'got',n:[notice.n]});
  await say(back,{t:'ping'});
  assert.deepEqual(back.errors(),[]);
  const before=m.envs().length;
  await say(back,{t:'send',env:seal({t:'hello'},phone,mac.publicKey)});
  assert.deepEqual(back.errors(),['unpaired']);
  assert.equal(m.envs().length,before,'nothing reached the Mac');
  await room.close(back);
});

test('removed phones never use up the pairing slots or pairing tries a new phone needs',async()=>{
  const {room,join,say,key}=setup();
  const mac=newKeyPair();
  const gone=Array.from({length:LIMITS.strangers+1},()=>newKeyPair());
  const m=await join('mac',mac);
  await say(m,{t:'allow',phones:gone.map(p=>p.publicKey)});
  await say(m,{t:'allow',phones:[]});
  const back=[];
  for(const phone of gone){
    const s=await join('phone',phone,randomToken());
    assert.equal(s.after()[0].t,'ready');
    back.push(s);
    for(let i=0;i<LIMITS.strangerPerMinute+1;i++)await say(s,{t:'send',env:seal({t:'hello'},phone,mac.publicKey)});
  }
  for(const s of back)await room.close(s);
  assert.equal(m.envs().length,0);
  // Rejoined and still connected, they don't count as pairing phones either.
  for(const phone of gone)await join('phone',phone,randomToken());
  const fresh=newKeyPair();
  const p=await join('phone',fresh,key);
  assert.equal(p.after()[0].t,'ready');
  await say(p,{t:'send',env:seal({t:'pair'},fresh,mac.publicKey)});
  assert.deepEqual(p.errors(),[]);
  assert.equal(m.envs().length,1,'the new phone reached the Mac');
});

