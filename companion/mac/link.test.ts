import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import WebSocket from 'ws';
import {MacLink,type HostCall,type LinkState,type SocketLike} from './link.ts';
import {startRelay} from '../relay/dev-server.ts';
import {readOffer} from '../protocol/pairing.ts';
import {PhoneLink,stateFromOffer,type PhoneState} from '../protocol/phone.ts';
import {newKeyPair,prove,seal} from '../protocol/crypto.ts';

const connect=(url:string)=>new WebSocket(url) as unknown as SocketLike;
async function until<T>(what:string,check:()=>T|undefined|null|false,ms=8000):Promise<T>{
  const end=Date.now()+ms;
  for(;;){const value=check();if(value)return value;if(Date.now()>end)throw new Error('Timed out waiting for '+what);await new Promise(r=>setTimeout(r,25));}
}
function memoryStore(){let saved:LinkState|null=null;return {load:async()=>saved&&structuredClone(saved),save:async(state:LinkState)=>{saved=structuredClone(state);}};}
function phoneFor(link:string,now?:()=>number){
  const offer=readOffer(link);
  assert.ok(offer,'the QR link reads');
  let saved:PhoneState|null=null;
  const phone=new PhoneLink(stateFromOffer(offer!),{connect,save:state=>{saved=state;},device:'Test iPhone',now});
  return {phone,saved:()=>saved};
}

/** The production Python host and real SQLite, as the desktop window and local-ui.test.mjs drive it. */
async function realHost(){
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
  const data=await mkdtemp(path.join(tmpdir(),'dervo-phone-e2e-'));
  const worker=spawn('python3',['-E','-s','-B',path.join(root,'backend/host.py'),data],{env:{PATH:process.env.PATH,HOME:data},stdio:['pipe','pipe','ignore']});
  const lines=createInterface({input:worker.stdout!});
  const pending:{resolve:(v:unknown)=>void;reject:(e:Error)=>void}[]=[];
  lines.on('line',line=>{const r=JSON.parse(line);const next=pending.shift()!;r.error?next.reject(new Error(r.error)):next.resolve(r.result);});
  const calls:string[]=[];
  // One request at a time, like the bridge.
  let chain:Promise<unknown>=Promise.resolve();
  const call:HostCall=(operation,args={})=>{
    const run=()=>new Promise<any>((resolve,reject)=>{calls.push(operation);pending.push({resolve,reject});worker.stdin!.write(JSON.stringify({operation,args})+'\n');});
    const next=chain.then(run,run);chain=next.catch(()=>{});return next;
  };
  return {call,calls,root,close:async()=>{worker.stdin!.end();await new Promise(r=>worker.once('exit',r));await rm(data,{recursive:true,force:true});}};
}

test('end to end: real host, relay, Mac link and phone link',async t=>{
  const host=await realHost();
  const relay=await startRelay();
  t.after(async()=>{await relay.close();await host.close();});
  const profile=await host.call<{id:string}>('add_profile',{provider:'codex',identity:'phone-e2e@example.test',category:'personal'});
  const project=await host.call<{id:string}>('_picked_project',{path:host.root,profile_id:profile.id,model:'unverified-phone-test'});
  const first=await host.call<{id:string}>('create_conversation',{projectId:project.id,name:'Export fix',private:false});
  await host.call<{id:string}>('create_conversation',{projectId:project.id,name:'Pricing copy',private:false});
  await host.call('queue',{clientId:'seed-1',text:'Can you look at the export?',parentId:null,projectId:project.id,channelId:first.id});

  const store=memoryStore();
  const mac=new MacLink({host:host.call,store,relay:relay.url,mac:'Test Mac',connect,pollMs:200});
  await mac.start();
  t.after(()=>mac.stop());
  await until('the Mac on the relay',()=>mac.connected);
  const {link}=await mac.pairingOffer();
  const {phone,saved}=phoneFor(link);
  phone.start();
  t.after(()=>phone.stop());

  await t.test('pairing over the relay, allowed on the Mac, then Home from the real host',async()=>{
    const asking=await until('asking on the Mac',()=>mac.waiting);
    assert.equal(asking.digits,phone.digits,'both screens show the same six digits');
    assert.equal(asking.device,'Test iPhone');
    await new Promise(r=>setTimeout(r,200));
    assert.equal(phone.state.paired,false,'nothing until you allow it');
    assert.equal(mac.phones.length,0);
    assert.ok(await mac.allowPhone(asking.pk));
    await until('paired',()=>phone.state.paired);
    assert.equal(mac.phones.length,1);
    assert.equal(mac.phones[0].device,'Test iPhone');
    const home=await until('home',()=>phone.state.home?.rows.length===2&&phone.state.home);
    assert.deepEqual(home.rows.map(r=>r.name).sort(),['Export fix','Pricing copy']);
    assert.equal(home.mac,'Test Mac');
    assert.equal(home.projects[0].id,project.id);
    assert.ok(saved()?.paired,'the phone saved its pairing');
    assert.equal(phone.state.code,null,'the one-time code is gone from the phone');
  });

  await t.test('opening a thread reads its messages from the host',async()=>{
    phone.open(project.id,first.id);
    const thread=await until('thread',()=>(phone.live.thread?.messages.length??0)>0&&phone.live.thread);
    assert.deepEqual(thread.messages.map(m=>[m.from,m.text]),[['you','Can you look at the export?']]);
  });

  await t.test('a send reaches the host as a coding request and the host\'s own answer comes back',async()=>{
    const before=host.calls.filter(c=>c==='coding_request').length;
    const sent=phone.act({kind:'send',projectId:project.id,channelId:first.id,text:'Run the tests please'});
    const settled=await until('ack',()=>phone.state.outbox.find(p=>p.id===sent.id&&p.state!=='waiting'));
    // With no signed-in account the real host refuses it, in its own words. Which words depends on the checkout: with no
    // account config it says setup needs attention; with the public one (as on a Mac with Dervo) beta access does.
    assert.equal(settled.state,'refused');
    assert.match(settled.detail,/account setup needs attention|beta access needs confirmation/);
    assert.equal(host.calls.filter(c=>c==='coding_request').length,before+1);
  });

  await t.test('Home\'s chat opens, and a message goes to its coordinator once',async()=>{
    phone.open(project.id,'home');
    await until('home chat',()=>phone.live.thread?.channelId==='home'&&phone.live.thread);
    const before=host.calls.filter(c=>c==='orchestrator_send').length;
    const sent=phone.act({kind:'home',projectId:project.id,channelId:'home',text:'Start a thread to fix the export'});
    const settled=await until('ack',()=>phone.state.outbox.find(p=>p.id===sent.id&&p.state!=='waiting'));
    assert.equal(host.calls.filter(c=>c==='orchestrator_send').length,before+1);
    if(settled.state==='done'){
      const chat=await until('your message in Home',()=>phone.live.thread?.messages.find(m=>m.from==='you'&&m.text==='Start a thread to fix the export'));
      assert.ok(chat);
    }else assert.equal(settled.state,'refused');
    phone.close();
  });

  await t.test('Stop with nothing running says so',async()=>{
    const stop=phone.act({kind:'stop',projectId:project.id,channelId:first.id});
    const settled=await until('ack',()=>phone.state.outbox.find(p=>p.id===stop.id&&p.state!=='waiting'));
    assert.deepEqual([settled.state,settled.detail],['refused','Nothing is running in this thread now.']);
  });

  await t.test('a replayed envelope or a repeated action id never runs twice',async()=>{
    const count=()=>host.calls.filter(c=>c==='coding_request').length;
    const before=count();
    const action={t:'action',id:'replay-1',at:Date.now(),macAt:phone.macTime(),action:{kind:'send',projectId:project.id,channelId:first.id,text:'once'}};
    const env=seal(action,phone.state.keys,phone.state.offer.mac,{keep:'q'});
    const raw=new WebSocket(`${relay.url}/v1/rooms/${phone.state.offer.room}`);
    const challenge=await new Promise<any>(r=>raw.once('message',data=>r(JSON.parse(String(data)).challenge)));
    raw.send(JSON.stringify({t:'auth',role:'phone',pk:phone.state.keys.publicKey,key:phone.state.offer.key,proof:prove(challenge,phone.state.keys,'phone',phone.state.offer.room)}));
    await new Promise(r=>raw.once('message',r));
    raw.send(JSON.stringify({t:'send',env}));
    raw.send(JSON.stringify({t:'send',env}));
    raw.send(JSON.stringify({t:'send',env:seal(action,phone.state.keys,phone.state.offer.mac,{keep:'q'})}));  // same id, new envelope
    await until('one run',()=>count()>=before+1);
    await new Promise(r=>setTimeout(r,400));
    assert.equal(count(),before+1);
    raw.close();
  });

  await t.test('while the Mac is away the phone says so, and a message waits sealed until it\'s back',async()=>{
    mac.stop();
    await until('offline',()=>!phone.live.macOnline);
    assert.ok(phone.live.macSeen);
    const homeBefore=phone.state.home;
    const queued=phone.act({kind:'send',projectId:project.id,channelId:first.id,text:'Do this when you wake up'});
    await new Promise(r=>setTimeout(r,200));
    assert.equal(phone.state.outbox.find(p=>p.id===queued.id)?.state,'waiting');
    assert.equal(phone.state.home,homeBefore,'the last Home stays, marked by when the Mac was last seen');
    const back=new MacLink({host:host.call,store,relay:relay.url,mac:'Test Mac',connect,pollMs:200});
    await back.start();
    t.after(()=>back.stop());
    const settled=await until('ack after wake',()=>phone.state.outbox.find(p=>p.id===queued.id&&p.state!=='waiting'));
    assert.equal(settled.state,'refused');  // the host's answer, as above: it ran once the Mac was back
    assert.ok(phone.live.macOnline);
  });

  await t.test('a phone whose clock is minutes off still has its Stop and send taken as fresh',async t=>{
    for(const off of [-3*60_000,4*60_000]){
      const skew=new PhoneLink(phone.state,{connect,save:()=>{},device:'Test iPhone',now:()=>Date.now()+off});
      skew.start();
      t.after(()=>skew.stop());
      await until('connected',()=>skew.live.connected);
      const stop=skew.act({kind:'stop',projectId:project.id,channelId:first.id});
      const settled=await until('ack',()=>skew.state.outbox.find(p=>p.id===stop.id&&p.state!=='waiting'));
      assert.notEqual(settled.state,'expired',`clock off by ${off/60_000} min`);
      skew.stop();
    }
  });

  await t.test('a send made while the phone was offline goes when it reconnects',async()=>{
    const count=()=>host.calls.filter(c=>c==='coding_request').length;
    const before=count();
    const offline=new PhoneLink(phone.state,{connect,save:()=>{},device:'Test iPhone'});
    const sent=offline.act({kind:'send',projectId:project.id,channelId:first.id,text:'Typed on the train'});
    offline.start();
    const settled=await until('ack',()=>offline.state.outbox.find(p=>p.id===sent.id&&p.state!=='waiting'));
    assert.equal(settled.state,'refused');
    assert.equal(count(),before+1);
    offline.stop();
  });

  await t.test('a wrong or reused code pairs nothing',async()=>{
    const {link:fresh}=await mac.pairingOffer().catch(()=>({link:''}));
    void fresh;
    const offer=readOffer(link)!;  // the first code, already used
    const stranger=new PhoneLink(stateFromOffer(offer),{connect,save:()=>{},device:'Stranger'});
    stranger.start();
    await new Promise(r=>setTimeout(r,600));
    assert.equal(stranger.state.paired,false);
    assert.equal(stranger.state.home,null);
    assert.equal(mac.waiting,null,'nobody is asking');
    stranger.stop();
  });
});

test('fake host: a phone you deny never pairs, and one removed while away finds out when it\'s back',async t=>{
  const relay=await startRelay();
  t.after(()=>relay.close());
  const host:HostCall=async operation=>{
    if(operation==='projects')return [] as any;
    if(operation==='status')return {actor:'local-user'} as any;
    // What the desktop bridge says about an operation it doesn't know, before the host sees it.
    throw new Error('invalid args `operation` for command `local_request`: unknown variant `thread_states`');
  };
  const mac=new MacLink({host,store:memoryStore(),relay:relay.url,mac:'Mac',connect,pollMs:100});
  await mac.start();
  t.after(()=>mac.stop());
  await until('connected',()=>mac.connected);
  const denied=phoneFor((await mac.pairingOffer()).link).phone;
  denied.start();
  t.after(()=>denied.stop());
  await until('asking',()=>mac.waiting);
  mac.denyPhone();
  await until('told no',()=>denied.live.error==='unpaired');
  assert.equal(denied.state.paired,false);

  const {phone}=phoneFor((await mac.pairingOffer()).link);
  phone.start();
  await mac.allowPhone((await until('asking',()=>mac.waiting)).pk);
  await until('home over the fallback',()=>phone.state.home);
  phone.stop();
  await mac.unpair(phone.state.keys.publicKey);
  const back=new PhoneLink(phone.state,{connect,save:()=>{},device:'Test iPhone'});
  back.start();
  t.after(()=>back.stop());
  await until('forgot its pairing',()=>!back.state.paired&&back.live.error==='unpaired');
  assert.equal(back.state.home,null);
});

test('fake host: a working thread can be stopped from the phone, and a new question wakes it',async t=>{
  const relay=await startRelay();
  t.after(()=>relay.close());
  let running=true,question:string|null=null;
  const stopped:string[]=[];
  const host:HostCall=async(operation,args={})=>{
    switch(operation){
      case 'status':return {actor:'local-user',lostRuns:[]} as any;
      case 'projects':return [{id:'p1',name:'dervo app'}] as any;
      case 'conversations':return [{id:'c1',name:'Feel premium',project_id:'p1',activity:{running,step:running?'Timing the send':null,pending_question:question,at:100}}] as any;
      case 'tasks':return (running?[{id:'t1',state:'working'}]:[{id:'t1',state:'stopped'}]) as any;
      case 'task_stop':stopped.push(String(args.taskId));running=false;return {} as any;
      default:throw new Error('Unsupported operation or fields');
    }
  };
  const mac=new MacLink({host,store:memoryStore(),relay:relay.url,mac:'Mac',connect,pollMs:100});
  await mac.start();
  t.after(()=>mac.stop());
  await until('connected',()=>mac.connected);
  const {phone}=phoneFor((await mac.pairingOffer()).link);
  phone.start();
  t.after(()=>phone.stop());
  await mac.allowPhone((await until('asking',()=>mac.waiting)).pk);
  const working=await until('working',()=>phone.state.home?.rows[0]?.bucket==='working'&&phone.state.home.rows[0]);
  assert.equal(working.line,'Timing the send');
  assert.equal(working.stoppable,true);
  const stop=phone.act({kind:'stop',projectId:'p1',channelId:'c1'});
  const settled=await until('ack',()=>phone.state.outbox.find(p=>p.id===stop.id&&p.state!=='waiting'));
  assert.deepEqual([settled.state,stopped],['done',['t1']]);
  await until('idle',()=>phone.state.home?.rows[0]?.bucket==='idle');
  question='Should an archived idea keep its threads?';
  const needs=await until('needs',()=>phone.state.home?.rows[0]?.bucket==='needs'&&phone.state.home.rows[0]);
  assert.equal(needs.question,question);
});

test('fake host: changing the relay tells an away phone it was removed, and an idle Mac reads Home slowly',async t=>{
  const relay=await startRelay();
  t.after(()=>relay.close());
  let clock=Date.now();
  const calls:string[]=[];
  const host:HostCall=async operation=>{
    calls.push(operation);
    if(operation==='projects')return [] as any;
    if(operation==='status')return {actor:'local-user'} as any;
    throw new Error('Unsupported operation or fields');
  };
  const mac=new MacLink({host,store:memoryStore(),relay:relay.url,mac:'Mac',connect,pollMs:50,now:()=>clock});
  await mac.start();
  t.after(()=>mac.stop());
  await until('connected',()=>mac.connected);
  const {phone}=phoneFor((await mac.pairingOffer()).link);
  phone.start();
  await mac.allowPhone((await until('asking',()=>mac.waiting)).pk);
  await until('paired',()=>phone.state.paired);
  phone.stop();
  // Nobody has the app open: after a while the Mac stops reading Home every pollMs.
  clock+=120_000;
  await new Promise(r=>setTimeout(r,200));
  const before=calls.filter(c=>c==='projects').length;
  await new Promise(r=>setTimeout(r,500));
  assert.ok(calls.filter(c=>c==='projects').length-before<=1,'slow while idle');

  await mac.unpairAll();
  await new Promise(r=>setTimeout(r,100));
  mac.stop();
  const back=new PhoneLink(phone.state,{connect,save:()=>{},device:'Test iPhone'});
  back.start();
  t.after(()=>back.stop());
  await until('removed, by the held sealed message',()=>!back.state.paired&&back.live.error==='unpaired');
});

test('the Mac answers one relay challenge per connection, and never one naming a key in this pairing',async()=>{
  const sockets:{sent:string[];closed:boolean;socket:SocketLike}[]=[];
  const connectFake=()=>{
    const entry={sent:[] as string[],closed:false,socket:null as unknown as SocketLike};
    entry.socket={send:text=>entry.sent.push(text),close:()=>{entry.closed=true;},onopen:null,onmessage:null,onclose:null,onerror:null};
    sockets.push(entry);
    return entry.socket;
  };
  const host:HostCall=async operation=>{if(operation==='projects')return [] as any;if(operation==='status')return {} as any;throw new Error('Unsupported');};
  const store=memoryStore();
  const mac=new MacLink({host,store,relay:'ws://127.0.0.1:1',mac:'Mac',connect:connectFake,pollMs:50});
  await mac.start();
  const {newChallenge}=await import('../protocol/crypto.ts');
  const deliver=async(frame:unknown)=>{sockets[0].socket.onmessage?.({data:JSON.stringify(frame)});await new Promise(r=>setTimeout(r,20));};
  const auths=()=>sockets[0].sent.filter(text=>JSON.parse(text).t==='auth').length;
  await deliver({t:'challenge',challenge:newChallenge().challenge});
  assert.equal(auths(),1);
  await deliver({t:'challenge',challenge:newChallenge().challenge});
  assert.equal(auths(),1,'a second challenge is not answered');
  assert.ok(sockets[0].closed);
  mac.stop();
  // A challenge naming the Mac's own key is a relay trying to get a box-shaped answer. Refused.
  const saved=await store.load();
  const again=new MacLink({host,store,relay:'ws://127.0.0.1:1',mac:'Mac',connect:connectFake,pollMs:50});
  await again.start();
  sockets.shift();
  await deliver({t:'challenge',challenge:{pk:saved!.keys.publicKey,n:newChallenge().challenge.n}});
  assert.equal(auths(),0);
  assert.ok(sockets[0].closed);
  again.stop();
});


test('fake host: a phone that was away when you clicked Allow still pairs, and one that scanned while the Mac was away asks again',async t=>{
  const relay=await startRelay();
  t.after(()=>relay.close());
  const host:HostCall=async operation=>{
    if(operation==='projects')return [] as any;
    if(operation==='status')return {actor:'local-user'} as any;
    throw new Error('invalid args `operation` for command `local_request`: unknown variant `'+operation+'`');
  };
  const store=memoryStore();
  const mac=new MacLink({host,store,relay:relay.url,mac:'Mac',connect,pollMs:100});
  await mac.start();
  t.after(()=>mac.stop());
  await until('connected',()=>mac.connected);
  const {phone}=phoneFor((await mac.pairingOffer()).link);
  phone.start();
  const asking=await until('asking',()=>mac.waiting);
  // The phone locks while you walk to the Mac.
  phone.stop();
  await mac.allowPhone(asking.pk);
  const back=new PhoneLink(phone.state,{connect,save:()=>{},device:'Test iPhone'});
  back.start();
  t.after(()=>back.stop());
  await until('paired after coming back',()=>back.state.paired);
  await until('Home',()=>back.state.home);

  // A second phone scans while the Mac is away, then the Mac comes back.
  const offer=(await mac.pairingOffer()).link;
  mac.stop();
  const second=phoneFor(offer).phone;
  second.start();
  t.after(()=>second.stop());
  await until('told the Mac is away',()=>second.live.error==='mac_offline');
  // Dervo opens again on the Mac.
  const again=new MacLink({host,store,relay:relay.url,mac:'Mac',connect,pollMs:100});
  await again.start();
  t.after(()=>again.stop());
  await until('asking again',()=>again.waiting?.pk===second.state.keys.publicKey);
  assert.equal(second.live.error,null);
});

test('actions are dated on the Mac\'s clock: a withheld or replayed copy never runs, whatever the phone\'s clock says',async t=>{
  let clock=Date.now();
  const phoneKeys=newKeyPair();
  const store=memoryStore();
  const mac0=newKeyPair();
  await store.save({keys:mac0,room:'r'.repeat(24),roomKey:'k'.repeat(43),relay:'ws://nowhere',phones:[{pk:phoneKeys.publicKey,device:'iPhone',pairedAt:clock}],pairing:null,seen:{},done:{}});
  const runs:string[]=[];
  const host:HostCall=async operation=>{runs.push(operation);return {} as any;};
  const quiet:SocketLike={send(){},close(){},onopen:null,onmessage:null,onclose:null,onerror:null};
  const mac=new MacLink({host,store,relay:'ws://nowhere',mac:'Mac',connect:()=>quiet,now:()=>clock,pollMs:3600_000});
  await mac.start();
  t.after(()=>mac.stop());
  const sends=()=>runs.filter(op=>op==='coding_request').length;
  // ``phone`` is how far off the phone's own clock is; ``macAt`` is what it reckons this Mac's clock is at the tap.
  const envelope=(id:string,{phone=0,macAt=clock as number|null}={})=>seal(
    {t:'action',id,at:clock+phone,sent:clock+phone,...(macAt!==null?{macAt}:{}),action:{kind:'send',projectId:'p',channelId:'c',text:'hi'}},
    phoneKeys,mac0.publicKey);
  // A relay that lies about how long it held a copy (held 0) is the worst case.
  const deliver=(env:unknown)=>(mac as any).onFrame(JSON.stringify({t:'env',env,held:0}));
  const answer=async(id:string)=>(await store.load())!.done[`${phoneKeys.publicKey}:${id}`];

  await deliver(envelope('fast',{phone:3*86_400_000}));
  await deliver(envelope('slow',{phone:-2*3600_000}));
  assert.equal(sends(),2,'a phone days fast or hours slow still sends');
  await deliver(envelope('old',{macAt:null}));
  assert.equal((await answer('old'))?.state,'refused','without macAt nothing runs');
  await deliver(envelope('jump',{macAt:clock+10*60_000}));
  assert.equal((await answer('jump'))?.state,'refused','a reckoning far ahead of this Mac is refused');
  assert.equal(sends(),2);

  // The relay withholds a copy from a phone 3 days fast and delivers it 25 hours later.
  const withheld=envelope('withheld',{phone:3*86_400_000});
  const ran=envelope('ran',{phone:3*86_400_000});
  await deliver(ran);
  assert.equal(sends(),3);
  clock+=25*3600_000+60_000;
  await (mac as any).persist();
  assert.equal(await answer('ran'),undefined,'its record is forgotten');
  await deliver(withheld);
  assert.equal((await answer('withheld'))?.state,'expired');
  await deliver(ran);
  assert.equal((await answer('ran'))?.state,'expired','a replay after the record is forgotten');
  assert.equal(sends(),3,'neither ran');
  await mac.unpair(phoneKeys.publicKey);
  assert.equal(Object.keys((await store.load())!.done).filter(key=>key.startsWith(phoneKeys.publicKey)).length,0,'a removed phone\'s answers go');
});

test('a Mac that has been quiet for 10 minutes still takes a Stop tapped now, from a relaunched phone days fast',async t=>{
  let clock=Date.now();
  const phoneKeys=newKeyPair();
  const store=memoryStore();
  const mac0=newKeyPair();
  await store.save({keys:mac0,room:'r'.repeat(24),roomKey:'k'.repeat(43),relay:'ws://nowhere',phones:[{pk:phoneKeys.publicKey,device:'iPhone',pairedAt:clock}],pairing:null,seen:{},done:{}});
  const stopped:string[]=[];
  const host:HostCall=async(operation,args:any)=>{
    // One run started 12 minutes ago, one a minute after the tap. Stop is for the first only.
    if(operation==='tasks')return [
      {id:'before',state:'working',messages:[{role:'user',text:'go',created_at:(clock-12*60_000)/1000}]},
      {id:'after',state:'working',messages:[{role:'user',text:'next',created_at:(clock+60_000)/1000}]},
    ] as any;
    if(operation==='task_stop')stopped.push(args.taskId);
    return {} as any;
  };
  const quiet:SocketLike={send(){},close(){},onopen:null,onmessage:null,onclose:null,onerror:null};
  const mac=new MacLink({host,store,relay:'ws://nowhere',mac:'Mac',connect:()=>quiet,now:()=>clock,pollMs:3600_000});
  await mac.start();
  t.after(()=>mac.stop());

  // The phone, 3 days fast, last heard the Mac's time 10 minutes ago, then was closed and opened again.
  const fast=3*86_400_000;
  const saved:PhoneState={keys:phoneKeys,offer:{v:1,relay:'ws://nowhere',room:'r'.repeat(24),key:'k'.repeat(43),mac:mac0.publicKey,name:'Mac'},
    code:null,paired:true,home:null,outbox:[],macClock:{mac:clock-10*60_000,phone:clock+fast-10*60_000}};
  const relaunched=new PhoneLink(saved,{connect:()=>quiet as any,save:()=>{},device:'iPhone',now:()=>clock+fast});
  const tap=relaunched.act({kind:'stop',projectId:'p',channelId:'c'});
  assert.equal(tap.macAt,clock,'it reckons the Mac\'s clock now, not 10 minutes ago');
  await (mac as any).onFrame(JSON.stringify({t:'env',held:0,env:seal({t:'action',id:tap.id,at:tap.at,sent:tap.at,macAt:tap.macAt,action:tap.action},phoneKeys,mac0.publicKey)}));
  assert.equal((await store.load())!.done[`${phoneKeys.publicKey}:${tap.id}`]?.state,'done');
  assert.deepEqual(stopped,['before']);
});

test('fake host: the phone answers a question, and approves and accepts only once you allow it on the Mac',async t=>{
  const relay=await startRelay();
  t.after(()=>relay.close());
  const calls:{op:string;args:any}[]=[];
  let scopeState='needs_permission',reviewState='ready_for_review',questionState='pending',unrecorded:string[]=[];
  const host:HostCall=async(operation,args:any={})=>{
    calls.push({op:operation,args});
    switch(operation){
      case 'status':return {actor:'local-user',lostRuns:[]} as any;
      case 'projects':return [{id:'p1',name:'dervo app'}] as any;
      case 'conversations':return [{id:'c1',name:'Ideas storage',project_id:'p1',activity:{status:'needs',pending_question:'Keep its threads?',at:100}}] as any;
      case 'conversation_feed':return {snapshot:{history:[]}} as any;
      case 'tasks':return [
        {id:'q',state:'working',questions:[{id:'q1',ask:'Should an archived idea keep its threads?',state:questionState,canAnswer:questionState==='pending',canSkip:true,
          options:[{id:'o1',label:'Keep them',detail:'They stay listed'},{id:'o2',label:'Archive them too',detail:''}]}]},
        {id:'s',state:scopeState,scope_version:3,title:'Move ideas into SQLite',files:['backend/ideas.py']},
        {id:'r',state:reviewState,title:'Fix the live board',check_profile:'node-test'},
        {id:'w',state:'needs_permission',scope_version:1,title:'Tidy the whole project',files:[],project_access:true},
      ] as any;
      case 'task_detail':return {evidence:{attempts:[{number:2,state:'verified',test:{exit_status:0},diff:{'a.ts':'','b.ts':''},unrecorded}]}} as any;
      case 'task_question_answer':questionState='answered';return {delivery:'sent'} as any;
      case 'task_approve':scopeState='working';return {} as any;
      case 'task_review':reviewState='idle';return {commit:{sha:'abc'}} as any;
      default:throw new Error('Unsupported operation or fields');
    }
  };
  const mac=new MacLink({host,store:memoryStore(),relay:relay.url,mac:'Mac',connect,pollMs:100});
  await mac.start();
  t.after(()=>mac.stop());
  await until('connected',()=>mac.connected);
  const {phone}=phoneFor((await mac.pairingOffer()).link);
  phone.start();
  t.after(()=>phone.stop());
  await mac.allowPhone((await until('asking',()=>mac.waiting)).pk);
  await until('home',()=>phone.state.home?.rows.length);
  phone.open('p1','c1');
  const thread=await until('asks',()=>phone.live.thread?.asks?.length===4&&phone.live.thread);
  assert.deepEqual(thread.asks!.map(ask=>ask.kind),['question','scope','review','scope']);
  assert.deepEqual(thread.asks!.filter(ask=>ask.kind==='scope').map(ask=>ask.kind==='scope'&&ask.phone),[true,false],'a scope beyond its own files is approved on the Mac');
  assert.equal(thread.approvals,undefined,'approvals are off until you turn them on');
  const review=thread.asks![2];
  assert.ok(review.kind==='review'&&review.phone&&review.checked&&review.files===2&&review.attemptNumber===2);
  const settle=async(action:Parameters<typeof phone.act>[0])=>{
    const pending=phone.act(action);
    return until('ack',()=>phone.state.outbox.find(p=>p.id===pending.id&&p.state!=='waiting'));
  };

  const answered=await settle({kind:'answer',projectId:'p1',channelId:'c1',taskId:'q',questionId:'q1',answer:{kind:'option',optionId:'o1'}});
  assert.deepEqual([answered.state,answered.detail],['done','Answered.']);
  const answer=calls.find(c=>c.op==='task_question_answer')!.args;
  assert.deepEqual(answer,{projectId:'p1',channelId:'c1',taskId:'q',questionId:'q1',clientId:`phone-${answered.id}`,answer:{kind:'option',optionId:'o1'}});

  const barred=await settle({kind:'scope',projectId:'p1',channelId:'c1',taskId:'s',scopeVersion:3,decision:'approve'});
  assert.equal(barred.state,'refused');
  assert.match(barred.detail,/can't approve yet/);
  assert.ok(!calls.some(c=>c.op==='task_approve'));

  const detailReads=calls.filter(c=>c.op==='task_detail').length;
  await mac.setApprovals(phone.state.keys.publicKey,true);
  await until('approvals on',()=>phone.live.thread?.approvals===true);
  assert.equal(calls.filter(c=>c.op==='task_detail').length,detailReads,'a result that looks the same isn\'t read again');
  const broad=await settle({kind:'scope',projectId:'p1',channelId:'c1',taskId:'w',scopeVersion:1,decision:'approve'});
  assert.deepEqual([broad.state,broad.detail],['refused','This run asks for more than its own files, so approve it on your Mac, where you can see what it covers.']);
  assert.ok(!calls.some(c=>c.op==='task_approve'));
  const stale=await settle({kind:'scope',projectId:'p1',channelId:'c1',taskId:'s',scopeVersion:2,decision:'deny'});
  assert.equal(stale.state,'refused','a decision about an older scope never touches the current one');
  assert.ok(!calls.some(c=>c.op==='task_deny'));
  const approved=await settle({kind:'scope',projectId:'p1',channelId:'c1',taskId:'s',scopeVersion:3,decision:'approve'});
  assert.deepEqual([approved.state,calls.find(c=>c.op==='task_approve')!.args],['done',{projectId:'p1',channelId:'c1',taskId:'s',scopeVersion:3}]);
  unrecorded=['c.ts'];
  const sheet=await settle({kind:'review',projectId:'p1',channelId:'c1',taskId:'r',attemptNumber:2,decision:'approved'});
  assert.deepEqual([sheet.state,sheet.detail],['refused','This result needs the review sheet on your Mac to accept it.'],'the Mac checks Accept itself, whatever the phone sent');
  const older=await settle({kind:'review',projectId:'p1',channelId:'c1',taskId:'r',attemptNumber:1,decision:'rejected'});
  assert.equal(older.state,'refused','a decision about an older attempt is turned away');
  assert.ok(!calls.some(c=>c.op==='task_review'));
  unrecorded=[];
  const accepted=await settle({kind:'review',projectId:'p1',channelId:'c1',taskId:'r',attemptNumber:2,decision:'approved'});
  assert.deepEqual([accepted.state,accepted.detail],['done','Accepted and committed.']);
  const again=await settle({kind:'review',projectId:'p1',channelId:'c1',taskId:'r',attemptNumber:2,decision:'approved'});
  assert.equal(again.state,'refused','a second decision is turned away');
  assert.equal(calls.filter(c=>c.op==='task_review').length,1);
});
