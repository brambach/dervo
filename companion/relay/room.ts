import {checkProof,newChallenge,roomKeyHash,type Challenge,type Envelope} from '../protocol/crypto.ts';
import {readToRelay,type FromRelay,type Role} from '../protocol/wire.ts';

/** One pairing's room on the relay: a Mac and the phones paired with it. The same class runs in the Cloudflare Durable
 * Object and in the local dev server, so what's tested here is what would ship.
 *
 * What the room stores, all of it here: a hash of the room key, the Mac's and phones' public keys, each phone's push
 * token, when the Mac was last connected, and sealed envelopes waiting for a device that's away. It can't read an
 * envelope and never holds a key that could. */

export type Socket={send(text:string):void;close(code?:number,reason?:string):void};
export type Storage={get<T>(key:string):Promise<T|undefined>;put(key:string,value:unknown):Promise<void>;delete(key:string):Promise<void>;
  /** Asks to have ``sweep`` called at this time (a Durable Object alarm). Optional: tests call ``sweep`` themselves. */
  setAlarm?(at:number):Promise<void>};
/** Sends one content-free notice to a phone. Never given anything from inside an envelope. */
/** ``needs``: something needs you. ``done``: something finished. */
export type Pusher=(token:string,kind:'needs'|'done')=>Promise<void>;

/** A mailbox's index. Each envelope is stored under its own key (``env:<pk>:<nonce>``), so no one stored value grows past
 * a Durable Object's size limit however full the box gets. */
/** One held envelope: its nonce, when the relay took it, its size, and who sent it. */
type Ref={n:string;at:number;bytes:number;from?:string};
type Box={q:Ref[];home:Ref|null};
type State={keyHash:string|null;mac:string|null;allowed:string[];push:Record<string,string>;macSeen:number|null;
  /** Bytes held per mailbox, so the room's total stays under LIMITS.roomBytes. */
  held?:Record<string,number>;
  /** Phones the Mac removed, and when. For LIMITS.heldMs such a phone can still sign in by its key to collect the sealed
   * removal the Mac left for it, and nothing else. */
  removed?:Record<string,number>};

export const LIMITS={
  authMs:5_000,
  /** Connections still answering the challenge, at once. */
  waiting:6,
  queueToMac:200,queueToPhone:100,
  /** Bytes held per mailbox. Oldest queued envelopes go first past this. */
  boxBytes:2*1024*1024,
  /** Bytes held across the whole room. Nothing is held until the Mac has allowed a phone. */
  roomBytes:8*1024*1024,
  /** Signed-in connections per room. The Mac is always let in. */
  sockets:24,
  /** Connections per paired phone (a reconnect can overlap the old socket for a moment). */
  perPhone:3,
  /** Phones the Mac hasn't allowed: how many at once, and for how long. Enough to pair, not enough to crowd the room. */
  strangers:4,
  strangerMs:60_000,
  /** Seven days: long enough for a trip, short enough that nothing sealed lingers. */
  heldMs:7*24*3600_000,
  framesPer10s:120,
  /** A phone that isn't paired yet can send the Mac a few hellos a minute and nothing else. */
  strangerPerMinute:5,
  /** And all of them together, so opening many connections doesn't buy more. */
  strangersPerMinute:20,
  pushEveryMs:60_000,
};

/** removed: a phone the Mac removed, signed in only to collect its held queue. It can say got and ping, nothing else. */
type Peer={role:Role;pk:string;frames:number[];stranger:number[];at:number;removed?:boolean};
type Waiting={challenge:Challenge;secret:string;room:string};

export class Room{
  private state:State|null=null;
  private peers=new Map<Socket,Peer|Waiting>();
  private strangers:number[]=[];
  private lastPush=new Map<string,number>();
  /** Frames are handled one at a time, in arrival order, across every socket in the room. */
  private chain:Promise<unknown>=Promise.resolve();
  private run(work:()=>Promise<unknown>|unknown){
    const next=this.chain.then(work,work);
    this.chain=next.catch(()=>{});
    return next.then(()=>{},()=>{});
  }
  private storage:Storage;private pusher:Pusher|null;private now:()=>number;
  constructor(storage:Storage,pusher:Pusher|null=null,now:()=>number=()=>Date.now()){this.storage=storage;this.pusher=pusher;this.now=now;}

  private async load():Promise<State>{
    if(!this.state)this.state=(await this.storage.get<State>('state'))??{keyHash:null,mac:null,allowed:[],push:{},macSeen:null};
    return this.state;
  }
  private async save(){await this.storage.put('state',this.state);}
  private async box(pk:string):Promise<Box>{return (await this.storage.get<Box>('box:'+pk))??{q:[],home:null};}
  private async putBox(pk:string,box:Box){
    if(!box.q.length&&!box.home)await this.storage.delete('box:'+pk);else await this.storage.put('box:'+pk,box);
    const state=await this.load();
    const held={...state.held};
    const bytes=sizeOf(box);
    if(bytes)held[pk]=bytes;else delete held[pk];
    if(JSON.stringify(held)!==JSON.stringify(state.held??{})){state.held=held;await this.save();}
  }
  private envKey(pk:string,n:string){return `env:${pk}:${n}`;}
  private async drop(pk:string,refs:Ref[]){for(const ref of refs)await this.storage.delete(this.envKey(pk,ref.n));}
  private async clearBox(pk:string){
    const box=await this.box(pk);
    await this.drop(pk,[...box.q,...(box.home?[box.home]:[])]);
    await this.storage.delete('box:'+pk);
    const state=await this.load();
    if(state.held?.[pk]!==undefined){delete state.held[pk];await this.save();}
  }
  /** Removes anything held longer than LIMITS.heldMs. Returns the box with only fresh envelopes. */
  private async prune(pk:string):Promise<Box>{
    const box=await this.box(pk);
    const cutoff=this.now()-LIMITS.heldMs;
    const old=box.q.filter(ref=>ref.at<=cutoff);
    if(box.home&&box.home.at<=cutoff){old.push(box.home);box.home=null;}
    if(old.length){
      await this.drop(pk,old);
      box.q=box.q.filter(ref=>ref.at>cutoff);
      await this.putBox(pk,box);
    }
    return box;
  }
  private async held(pk:string,ref:Ref):Promise<Envelope|undefined>{return this.storage.get<Envelope>(this.envKey(pk,ref.n));}
  /** Purges every mailbox of envelopes past their seven days, even for a device that never comes back. The Worker
   * calls it from the Durable Object alarm. */
  sweep(){return this.run(async()=>{
    const state=await this.load();
    let next:number|null=null;
    const cutoff=this.now()-LIMITS.heldMs;
    for(const [pk,at] of Object.entries(state.removed??{}))if(at<=cutoff){delete state.removed![pk];await this.clearBox(pk);}
    await this.save();
    for(const pk of new Set([...(state.mac?[state.mac]:[]),...state.allowed,...Object.keys(state.removed??{})])){
      const box=await this.prune(pk);
      for(const ref of [...box.q,...(box.home?[box.home]:[])])next=Math.min(next??Infinity,ref.at+LIMITS.heldMs);
    }
    if(next!==null)await this.storage.setAlarm?.(next+1000);
  });}
  private say(socket:Socket,frame:FromRelay){try{socket.send(JSON.stringify(frame));}catch{/* A dead socket is cleaned up on close. */}}
  private fail(socket:Socket,code:string,detail:string){this.say(socket,{t:'error',code,detail});try{socket.close(1008,code);}catch{/* already closed */}}
  private sockets(role:Role,pk?:string):Socket[]{
    return [...this.peers].filter(([,peer])=>peer&&!isWaiting(peer)&&peer.role===role&&(!pk||peer.pk===pk)).map(([socket])=>socket);
  }
  private presence(state:State){
    const macOnline=this.sockets('mac').length>0;
    for(const socket of this.sockets('phone'))this.say(socket,{t:'presence',macOnline,macSeen:state.macSeen});
  }

  /** A new connection. It gets a challenge and has LIMITS.authMs to answer it (the caller enforces the timer through
   * ``expire``). */
  connect(socket:Socket,room:string){
    if([...this.peers.values()].filter(isWaiting).length>=LIMITS.waiting)return this.fail(socket,'busy','Too many connections at once. Try again in a moment.');
    const waiting={...newChallenge(),room};
    this.peers.set(socket,waiting);
    this.say(socket,{t:'challenge',challenge:waiting.challenge});
  }
  /** The host calls this at LIMITS.authMs and again at LIMITS.strangerMs after a connection opens. */
  expire(socket:Socket){return this.run(async()=>{
    const peer=this.peers.get(socket);
    if(isWaiting(peer))return this.fail(socket,'auth_timeout','Say who you are first.');
    if(peer?.role!=='phone'||this.now()-peer.at<LIMITS.strangerMs)return;
    const state=await this.load();
    if(!state.allowed.includes(peer.pk))this.fail(socket,'stranger_timeout','Pairing took too long. Scan the code again.');
  });}
  private strangerSockets(state:State){
    return [...this.peers.values()].filter(peer=>peer&&!isWaiting(peer)&&peer.role==='phone'&&!peer.removed&&!state.allowed.includes(peer.pk)).length;
  }

  close(socket:Socket){return this.run(()=>this.closed(socket));}
  message(socket:Socket,raw:string){return this.run(()=>this.received(socket,raw));}

  private async closed(socket:Socket){
    const peer=this.peers.get(socket);
    this.peers.delete(socket);
    if(peer&&!isWaiting(peer)&&peer.role==='mac'&&!this.sockets('mac').length){
      const state=await this.load();
      state.macSeen=this.now();
      await this.save();
      this.presence(state);
    }
  }

  private async received(socket:Socket,raw:string){
    if(!this.peers.has(socket))return;
    const frame=readToRelay(raw);
    if(!frame)return this.fail(socket,'bad_frame','That frame isn\'t one the relay reads.');
    const peer=this.peers.get(socket);
    if(!peer)return;
    if(isWaiting(peer)){
      if(frame.t!=='auth')return this.fail(socket,'auth_first','Say who you are first.');
      if(!checkProof(peer.challenge,peer.secret,frame.role,peer.room,frame.pk,frame.proof))return this.fail(socket,'bad_proof','That key didn\'t sign in.');
      return this.auth(socket,frame.role,frame.pk,frame.key);
    }
    const now=this.now();
    peer.frames=peer.frames.filter(at=>now-at<10_000);
    if(peer.frames.length>=LIMITS.framesPer10s)return this.fail(socket,'rate','Too many frames. Reconnect in a moment.');
    peer.frames.push(now);
    // A removed phone only collects what's held for it. It never reaches the Mac or spends the pairing budget.
    if(peer.removed&&frame.t!=='got'&&frame.t!=='ping')return this.fail(socket,'unpaired','This phone was removed on your Mac.');
    const state=await this.load();
    switch(frame.t){
      case 'auth':return this.fail(socket,'auth_twice','Already connected.');
      case 'ping':return this.say(socket,{t:'pong'});
      case 'allow':{
        if(peer.role!=='mac')return this.fail(socket,'not_mac','Only the Mac names its phones.');
        const dropped=state.allowed.filter(pk=>!frame.phones.includes(pk));
        state.allowed=frame.phones;
        const removed={...state.removed};
        for(const pk of frame.phones)delete removed[pk];
        for(const pk of dropped){
          delete state.push[pk];
          // Its queue stays (sealed, and the Mac's removal notice is in it); its Home copy goes now.
          const box=await this.box(pk);
          if(box.home){await this.drop(pk,[box.home]);box.home=null;await this.putBox(pk,box);}
          removed[pk]=this.now();
          for(const phone of this.sockets('phone',pk))this.fail(phone,'unpaired','This phone was removed on your Mac.');
        }
        state.removed=removed;
        return this.save();
      }
      case 'rekey':{
        if(peer.role!=='mac')return this.fail(socket,'not_mac','Only the Mac changes the room key.');
        // The Mac says its key after every sign-in. The same key changes nothing, so a phone pairing right now stays.
        if(state.keyHash===roomKeyHash(frame.key))return;
        state.keyHash=roomKeyHash(frame.key);
        // Unpaired phones that came in with the old key go.
        for(const [other,p] of this.peers)if(p&&!isWaiting(p)&&p.role==='phone'&&!p.removed&&!state.allowed.includes(p.pk))this.fail(other,'bad_key','This pairing code isn\'t valid any more.');
        return this.save();
      }
      case 'push':{
        if(peer.role!=='phone'||!state.allowed.includes(peer.pk))return;
        if(frame.token)state.push[peer.pk]=frame.token;else delete state.push[peer.pk];
        return this.save();
      }
      case 'got':{
        const box=await this.box(peer.pk);
        const got=new Set(frame.n);
        const gone=box.q.filter(ref=>got.has(ref.n));
        box.q=box.q.filter(ref=>!got.has(ref.n));
        if(box.home&&got.has(box.home.n)){gone.push(box.home);box.home=null;}
        await this.drop(peer.pk,gone);
        return this.putBox(peer.pk,box);
      }
      case 'send':return this.route(socket,peer,state,frame.env);
    }
  }

  private async auth(socket:Socket,role:Role,pk:string,key:string){
    const state=await this.load();
    const hash=roomKeyHash(key);
    if(!state.keyHash){
      // Only a Mac opens a room. A phone can't create one by guessing an id.
      if(role!=='mac')return this.fail(socket,'no_room','This pairing isn\'t open on the relay. Show the code on your Mac again.');
      state.keyHash=hash;state.mac=pk;
      await this.save();
    }else if(role==='mac'&&state.mac!==pk)return this.fail(socket,'other_mac','Another Mac owns this room.');
    // The Mac and its paired phones sign in by key alone. The room key only lets an unpaired phone in to pair, and the
    // Mac changes it with each new pairing code, so an old QR or a removed phone can't hold the room's pairing slots.
    else if(role==='phone'&&!state.allowed.includes(pk)&&!isRemoved(state,pk,this.now())&&state.keyHash!==hash)return this.fail(socket,'bad_key','This pairing code isn\'t valid any more.');
    if(role==='mac'){
      // One Mac connection at a time; the newest wins, so a reconnect after sleep never fights the old socket.
      for(const old of this.sockets('mac'))this.fail(old,'replaced','Connected again from this Mac.');
      state.macSeen=this.now();
      await this.save();
    }else{
      const signedIn=[...this.peers.values()].filter(peer=>peer&&!isWaiting(peer)).length;
      if(signedIn>=LIMITS.sockets)return this.fail(socket,'full','Too many connections to this pairing.');
      if(state.allowed.includes(pk)){
        if(this.sockets('phone',pk).length>=LIMITS.perPhone)return this.fail(socket,'full','This phone is already connected.');
      }
      // One connection per unpaired key, so one phone (or a removed one) can't hold every pairing slot.
      else if(this.sockets('phone',pk).length>=1)return this.fail(socket,'full','This phone is already connected.');
      else if(!isRemoved(state,pk,this.now())&&this.strangerSockets(state)>=LIMITS.strangers)return this.fail(socket,'full','Too many phones are pairing. Try again in a minute.');
    }
    const removed=role==='phone'&&!state.allowed.includes(pk)&&isRemoved(state,pk,this.now());
    this.peers.set(socket,{role,pk,frames:[],stranger:[],at:this.now(),...(removed?{removed}:{})});
    const macOnline=this.sockets('mac').length>0;
    this.say(socket,{t:'ready',macOnline,macSeen:state.macSeen,allowed:role==='mac'||state.allowed.includes(pk)});
    if(role==='mac')this.presence(state);
    if(role==='mac'||state.allowed.includes(pk)||isRemoved(state,pk,this.now()))await this.deliverHeld(socket,pk);
  }

  private async deliverHeld(socket:Socket,pk:string){
    const box=await this.prune(pk);
    for(const ref of [...(box.home?[box.home]:[]),...box.q]){
      const env=await this.held(pk,ref);
      // How long it waited here, on the relay's clock, so the recipient can tell an action's age without trusting
      // the sender's clock.
      if(env)this.say(socket,{t:'env',env:{...env,wake:undefined,news:undefined},held:Math.max(0,this.now()-ref.at)});
    }
  }

  private async route(socket:Socket,peer:Peer,state:State,env:Envelope){
    if(env.from!==peer.pk)return this.fail(socket,'not_you','An envelope has to come from the key you connected with.');
    const now=this.now();
    if(peer.role==='phone'){
      if(env.to!==state.mac)return this.say(socket,{t:'error',code:'bad_to','detail':'A phone only writes to its Mac.'});
      const macs=this.sockets('mac');
      if(!state.allowed.includes(peer.pk)){
        // Pairing: live only, a handful a minute, never stored.
        peer.stranger=peer.stranger.filter(at=>now-at<60_000);
        this.strangers=this.strangers.filter(at=>now-at<60_000);
        if(peer.stranger.length>=LIMITS.strangerPerMinute||this.strangers.length>=LIMITS.strangersPerMinute)return this.fail(socket,'rate','Too many pairing tries. Scan the code again in a minute.');
        peer.stranger.push(now);this.strangers.push(now);
        if(!macs.length)return this.say(socket,{t:'error',code:'mac_offline',detail:'Your Mac isn\'t connected. Open Dervo on it and scan again.'});
        for(const mac of macs)this.say(mac,{t:'env',env:{...env,keep:undefined,wake:undefined,news:undefined}});
        return;
      }
      return this.deliver(state.mac!,macs,env,LIMITS.queueToMac);
    }
    // From the Mac: to a paired phone, or live to a phone that's pairing right now.
    const phones=this.sockets('phone',env.to);
    // A removed phone's mailbox still takes the Mac's sealed notice, so it learns it was removed when it comes back.
    if(isRemoved(state,env.to,now)&&env.keep==='q')return this.deliver(env.to,phones,{...env,wake:undefined,news:undefined},LIMITS.queueToPhone);
    if(!state.allowed.includes(env.to)){
      for(const phone of phones)this.say(phone,{t:'env',env:{...env,keep:undefined,wake:undefined,news:undefined}});
      return;
    }
    await this.deliver(env.to,phones,env,LIMITS.queueToPhone);
    if((env.wake||env.news)&&!phones.length)await this.wake(state,env.to,env.wake?'needs':'done');
  }

  private async deliver(to:string,live:Socket[],env:Envelope,cap:number){
    // The push hints are for this relay only.
    for(const socket of live)this.say(socket,{t:'env',env:{...env,wake:undefined,news:undefined}});
    // Queued envelopes are held until the recipient says it got them, even when a socket looks live: a sleeping Mac or a
    // suspended phone can leave a socket open that nobody reads.
    if(!env.keep)return;
    const box=await this.prune(to);
    const ref:Ref={n:env.n,at:this.now(),bytes:env.c.length+env.n.length+env.from.length+env.to.length,from:env.from};
    const gone:Ref[]=[];
    if(env.keep==='home'){if(box.home)gone.push(box.home);box.home=ref;}
    else box.q.push(ref);
    // Oldest out first. The Mac's snapshot carries current state, so a dropped old ack loses nothing that matters.
    const state=await this.load();
    const others=Object.entries(state.held??{}).reduce((sum,[pk,bytes])=>pk===to?sum:sum+bytes,0);
    const limit=Math.min(LIMITS.boxBytes,LIMITS.roomBytes-others);
    // A sender over the limit loses its own oldest first, so one device can't push another's message out of a
    // mailbox they share (two phones writing to one Mac).
    while(box.q.length>cap||(box.q.length&&sizeOf(box)>limit)){
      const own=box.q.findIndex(item=>item.from===env.from&&item!==ref);
      gone.push(box.q.splice(own>=0?own:0,1)[0]);
    }
    if(sizeOf(box)>limit&&box.home===ref){gone.push(ref);box.home=null;}
    if(!gone.includes(ref))await this.storage.put(this.envKey(to,env.n),env);
    await this.drop(to,gone.filter(item=>item!==ref));
    await this.putBox(to,box);
    await this.storage.setAlarm?.(ref.at+LIMITS.heldMs+1000);
  }

  private async wake(state:State,phone:string,kind:'needs'|'done'){
    const token=state.push[phone];
    if(!token||!this.pusher)return;
    // Each kind has its own minute, so a "finished" notice never uses up the one for a thread that needs you.
    const key=`${phone}:${kind}`;
    const last=this.lastPush.get(key);
    if(last!==undefined&&this.now()-last<LIMITS.pushEveryMs)return;
    this.lastPush.set(key,this.now());
    try{await this.pusher(token,kind);}catch{/* A missed notice is fine: the phone sees everything when it opens. */}
  }
}

function isRemoved(state:State,pk:string,now:number){const at=state.removed?.[pk];return at!==undefined&&now-at<LIMITS.heldMs;}

function sizeOf(box:Box){return box.q.reduce((sum,item)=>sum+item.bytes,box.home?.bytes??0);}

function isWaiting(peer:Peer|Waiting|undefined):peer is Waiting{return !!peer&&'challenge' in peer;}

/** Storage in memory, for the dev server and tests. */
export class MemoryStorage implements Storage{
  data=new Map<string,string>();
  setAlarm?:(at:number)=>Promise<void>;
  async get<T>(key:string){const v=this.data.get(key);return v===undefined?undefined:JSON.parse(v) as T;}
  async put(key:string,value:unknown){this.data.set(key,JSON.stringify(value));}
  async delete(key:string){this.data.delete(key);}
}
