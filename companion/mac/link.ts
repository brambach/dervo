import {newKeyPair,open,pairingDigits,prove,randomToken,seal,type Envelope,type KeyPair} from '../protocol/crypto.ts';
import {offerLink,type PairingOffer} from '../protocol/pairing.ts';
import {ACTION_TTL,HOME_CHANNEL,readPhoneMessage,type Action,type AckState,type HomeSnapshot,type MacMessage,type ThreadDetail} from '../protocol/messages.ts';
import {asksOf,detailKey,scopeOnPhone,buildSnapshot,buildSnapshotFromStates,fitSnapshot,fitThread,homeChatOf,projectName,stopTargets,threadOf,type HostHomeEntry,type HostThreadState,type HostConversation,type HostFeedRow,type HostLostRun,type HostProject,type HostTask,type HostTaskDetail} from '../protocol/home.ts';
import {MAX_PHONES,readFromRelay,type ToRelay} from '../protocol/wire.ts';

/** The Mac side of Dervo on your phone. It reads Home through the same host operations the desktop window uses, seals
 * it for each paired phone, and runs the few things a phone may ask for (send a message, stop a run) through those same
 * operations. Nothing new is added to the host. It runs wherever ``call`` reaches the host: the desktop window today,
 * the host itself once the live state spine lands.
 *
 * What it keeps (``LinkStore``): its key pair, the room, the paired phones' public keys, a pending pairing code, and
 * which envelopes and actions it has already handled, so nothing runs twice. */

export type HostCall=<T=unknown>(operation:string,args?:Record<string,unknown>)=>Promise<T>;
export type SocketLike={send(text:string):void;close():void;onopen:(()=>void)|null;onmessage:((event:{data:unknown})=>void)|null;onclose:(()=>void)|null;onerror:(()=>void)|null};
export type Phone={pk:string;device:string;pairedAt:number;
  /** You let this phone approve scopes and accept results (Settings, Phone, on the Mac). Off until you turn it on. */
  approvals?:boolean};
/** A phone that sent the right code and waits for you to allow it on the Mac. ``digits`` show on both screens. */
export type Asking={pk:string;device:string;digits:string;expires:number};
export type LinkState={keys:KeyPair;room:string;roomKey:string;relay:string;phones:Phone[];
  pairing:{code:string;expires:number}|null;
  /** Envelope nonces and action ids already handled, with when, kept for ACTION_TTL.send plus a margin. */
  seen:Record<string,number>;done:Record<string,{at:number;state:AckState;detail:string}>;
  /** Removed phones still to be told, when the relay wasn't reachable at the time. Sent on the next connect. */
  notices?:string[];
  /** Actions started on the host and not yet recorded as done, by key, with when. One still here after a restart may
   * or may not have run, so it's answered as unknown, never run again. */
  running?:Record<string,number>};
export type LinkStore={load():Promise<LinkState|null>;save(state:LinkState):Promise<void>};

export const PAIRING_MS=10*60_000;
const SEEN_MS=ACTION_TTL.send+3600_000;
/** How far ahead of this Mac's clock an action's ``macAt`` may be: the phone's reckoning drifts only if its clock is
 * changed between hearing from the Mac and the tap. */
const CLOCK_SKEW_MS=5*60_000;
/** Home goes out at least this often even when nothing changed, so the phone can tell a fresh copy from a stale one. */
export const HEARTBEAT_MS=30_000;
/** A phone that said something within this long is open on someone's screen. The phone says hello every minute. */
export const ACTIVE_MS=90_000;
/** How often Home is read while no phone is open: often enough to wake a phone when a thread starts waiting on you. */
export const IDLE_POLL_MS=30_000;
/** The fallback path's lost runs (``status``) are read at most this often. */
const STATUS_MS=30_000;
const PING_MS=25_000;
const PONG_MS=10_000;

export type LinkOptions={host:HostCall;store:LinkStore;relay:string;mac:string;
  connect:(url:string)=>SocketLike;now?:()=>number;pollMs?:number;log?:(line:string)=>void};

export class MacLink{
  private state!:LinkState;
  private socket:SocketLike|null=null;
  private ready=false;
  private stopped=false;
  private backoff=1000;
  private timer:ReturnType<typeof setTimeout>|null=null;
  private lastHome='';
  private lastNeeds=new Set<string>();
  private lastFinished:Set<string>|null=null;
  private you='local-user';
  private names=new Map<string,string>();
  /** Every thread id a Home read has listed, named or not. */
  private known=new Set<string>();
  private frames:Promise<void>=Promise.resolve();
  private asking:Asking|null=null;
  private listeners=new Set<()=>void>();
  /** Set when the relay says another connection owns this room; this one stops instead of fighting it. */
  private halted:string|null=null;
  /** The relay's challenge is answered once per connection, before it says ready, and never again. */
  private answered=false;
  private openThreads=new Map<string,{projectId:string;channelId:string}>();
  /** What each phone was last sent for its open thread, so an unchanged one isn't sent again. */
  private lastThread=new Map<string,string>();
  /** ``task_detail`` for results waiting on review, kept while the task looks the same and for at most a minute, so an
   * open thread doesn't read a whole result's evidence every few seconds. Accepting always reads it fresh. */
  private details=new Map<string,{key:string;at:number;detail:HostTaskDetail}>();
  private lastSent=0;
  private activeAt=new Map<string,number>();
  private lostRuns:{at:number;runs:HostLostRun[]}|null=null;
  private active(){
    const cutoff=this.now()-ACTIVE_MS;
    for(const [pk,at] of this.activeAt)if(at<cutoff){this.activeAt.delete(pk);this.openThreads.delete(pk);this.lastThread.delete(pk);}
    return this.activeAt.size>0;
  }
  private dirty=false;
  private pinger:ReturnType<typeof setInterval>|null=null;
  private pongTimer:ReturnType<typeof setTimeout>|null=null;
  private now:()=>number;
  private options:LinkOptions;
  constructor(options:LinkOptions){this.options=options;this.now=options.now??Date.now;}

  async start(){
    const saved=await this.options.store.load();
    this.state=saved&&saved.relay===this.options.relay?saved:{keys:newKeyPair(),room:randomToken(18),roomKey:randomToken(),relay:this.options.relay,phones:[],pairing:null,seen:{},done:{}};
    // Older saves could hold more phones than the relay lists; the newest stay, the rest are told.
    const phones=[...this.state.phones].sort((a,b)=>a.pairedAt-b.pairedAt);
    while(phones.length>MAX_PHONES)this.evict(phones.shift()!.pk);
    this.state.phones=phones;
    await this.persist();
    try{this.you=(await this.options.host<{actor?:string}>('status'))?.actor??this.you;}catch{/* the default actor */}
    this.dial();
    this.poll();
  }

  stop(){
    this.stopped=true;
    if(this.timer)clearTimeout(this.timer);
    this.quietPings();
    this.socket?.close();
  }

  get phones(){return this.state.phones.map(phone=>({...phone}));}
  /** Let a paired phone approve scopes and accept results, or stop it. Answers and messages never need this. */
  async setApprovals(pk:string,on:boolean){
    const phone=this.state.phones.find(item=>item.pk===pk);
    if(!phone)return false;
    phone.approvals=on;
    await this.persist();
    this.lastThread.delete(pk);
    const open=this.openThreads.get(pk);
    if(open)await this.openThread(pk,open.projectId,open.channelId);
    this.changed();
    return true;
  }
  get connected(){return this.ready;}
  /** Why the link stopped on its own (another Dervo connected to this room), or null. */
  get problem(){return this.halted;}
  /** The phone waiting for you to allow it, if any. */
  get waiting():Asking|null{
    if(this.asking&&this.asking.expires<this.now())this.asking=null;
    return this.asking?{...this.asking}:null;
  }
  subscribe(listener:()=>void){this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};}
  private changed(){for(const listener of this.listeners)listener();}

  /** Allow the phone that's asking. Only after this can it read Home or act. */
  async allowPhone(pk:string){
    const asking=this.waiting;
    if(!asking||asking.pk!==pk)return false;
    this.asking=null;
    const others=this.state.phones.filter(phone=>phone.pk!==pk).sort((a,b)=>a.pairedAt-b.pairedAt);
    // The relay names at most MAX_PHONES phones. Past that, the longest-paired one makes room and is told so.
    while(others.length>=MAX_PHONES)this.evict(others.shift()!.pk);
    this.state.phones=[...others,{pk,device:asking.device,pairedAt:this.now()}];
    this.markActive(pk);
    await this.persist();
    this.allow();
    // Held, so a phone that locked while you walked to the Mac still learns it's paired when it's back.
    this.sealTo(pk,{t:'paired',mac:this.options.mac},{keep:'q'});
    this.lastHome='';
    await this.publish();
    this.changed();
    this.options.log?.(`paired ${asking.device}`);
    return true;
  }
  /** Drop a phone to make room, and tell it, held, so it learns when it's back. */
  private evict(pk:string){
    if(this.ready)this.sealTo(pk,{t:'unpaired'},{keep:'q'});else this.state.notices=[...new Set([...(this.state.notices??[]),pk])];
    this.openThreads.delete(pk);this.activeAt.delete(pk);this.lastThread.delete(pk);
    this.forgetActions(pk);
    this.options.log?.('removed the longest-paired phone to make room for a new one');
  }
  denyPhone(){
    const asking=this.asking;
    this.asking=null;
    if(asking)this.sealTo(asking.pk,{t:'unpaired'});
    this.changed();
  }
  /** Before the relay changes: tell every phone it's removed, so none waits on a room nobody uses any more. */
  async unpairAll(){
    // Held for phones that are away. The old room's list stays as it is, so the relay keeps what it holds for them
    // until they come back or it expires; telling it to drop them would delete the very message that removes them.
    for(const phone of this.state.phones){this.sealTo(phone.pk,{t:'unpaired'},{keep:'q'});this.forgetActions(phone.pk);}
    this.state.phones=[];
    await this.persist();
    this.changed();
  }

  /** A fresh one-time code for the QR on the Mac. Showing a new one cancels the last. */
  async pairingOffer():Promise<{link:string;offer:PairingOffer}>{
    const code=randomToken();
    const expires=this.now()+PAIRING_MS;
    this.state.pairing={code,expires};
    // A new room key with each code: only someone who sees this QR can come in to pair, and older QRs stop working.
    this.state.roomKey=randomToken();
    await this.persist();
    this.send({t:'rekey',key:this.state.roomKey});
    const offer:PairingOffer={v:1,relay:this.state.relay,room:this.state.room,key:this.state.roomKey,mac:this.state.keys.publicKey,code,name:this.options.mac,expires};
    return {link:offerLink(offer),offer};
  }

  async unpair(pk:string){
    if(!this.state.phones.some(phone=>phone.pk===pk))return;
    this.state.phones=this.state.phones.filter(phone=>phone.pk!==pk);
    this.openThreads.delete(pk);
    this.forgetActions(pk);
    // The relay holds this for a phone that's away, even after it's off the list, so it learns when it's back.
    if(this.ready)this.sealTo(pk,{t:'unpaired'},{keep:'q'});
    else this.state.notices=[...new Set([...(this.state.notices??[]),pk])];
    await this.persist();
    this.allow();
    this.changed();
  }

  // ---------- relay connection ----------

  private dial(){
    if(this.stopped||this.halted)return;
    const socket=this.options.connect(`${this.state.relay}/v1/rooms/${this.state.room}`);
    this.socket=socket;
    this.answered=false;
    // The relay opens with a challenge; the answer proves this Mac holds its secret key, which no QR code carries.
    socket.onopen=()=>{};
    // One frame at a time, in order: two copies of an action arriving together must not both pass the "already done"
    // check while the first is still running on the host.
    socket.onmessage=event=>{
      // A replaced socket can still deliver a last frame (a "replaced" error, say); only the current one is heard.
      if(this.socket!==socket)return;
      const raw=String(event.data);this.frames=this.frames.then(()=>this.onFrame(raw)).catch(()=>{});
    };
    const lost=()=>{
      if(this.socket!==socket)return;
      this.socket=null;this.ready=false;
      this.quietPings();
      this.changed();
      if(this.stopped||this.halted)return;
      setTimeout(()=>this.dial(),this.backoff*(1+Math.random()/2));
      this.backoff=Math.min(this.backoff*2,30_000);
    };
    socket.onclose=lost;
    socket.onerror=()=>{try{socket.close();}catch{/* closing anyway */}lost();};
  }

  /** A socket left open by sleep can look connected while nothing gets through. A ping every 25 s with no pong in 10 s
   * closes it, and the reconnect brings back what the relay held. */
  private startPings(){
    this.quietPings();
    this.pinger=setInterval(()=>{
      this.send({t:'ping'});
      if(!this.pongTimer)this.pongTimer=setTimeout(()=>{this.pongTimer=null;const dead=this.socket;try{dead?.close();}catch{/* closing anyway */}dead?.onclose?.();},PONG_MS);
    },PING_MS);
  }
  private quietPings(){
    if(this.pinger)clearInterval(this.pinger);
    if(this.pongTimer)clearTimeout(this.pongTimer);
    this.pinger=null;this.pongTimer=null;
  }

  private send(frame:ToRelay){try{this.socket?.send(JSON.stringify(frame));}catch{/* the reconnect resends what matters */}}
  private allow(){if(this.ready)this.send({t:'allow',phones:this.state.phones.slice(-MAX_PHONES).map(phone=>phone.pk)});}
  private sealTo(pk:string,message:MacMessage,extra:Pick<Envelope,'keep'|'wake'|'news'>={}){
    if(!this.ready)return;
    this.send({t:'send',env:seal(message,this.state.keys,pk,extra)});
  }

  private async onFrame(raw:string){
    const frame=readFromRelay(raw);
    if(!frame)return;
    if(frame.t==='pong'){if(this.pongTimer)clearTimeout(this.pongTimer);this.pongTimer=null;return;}
    if(frame.t==='challenge'){
      // One answer per connection, before ready, and never to a key that belongs to this pairing: a relay asking again,
      // or naming a phone's key, is not a sign-in.
      const ours=new Set([this.state.keys.publicKey,...this.state.phones.map(phone=>phone.pk),...(this.asking?[this.asking.pk]:[])]);
      if(this.answered||this.ready||ours.has(frame.challenge.pk)){this.options.log?.('relay: refused a challenge');try{this.socket?.close();}catch{/* closing anyway */}return;}
      this.answered=true;
      try{this.send({t:'auth',role:'mac',pk:this.state.keys.publicKey,key:this.state.roomKey,proof:prove(frame.challenge,this.state.keys,'mac',this.state.room)});}
      catch{try{this.socket?.close();}catch{/* closing anyway */}}
      return;
    }
    if(frame.t==='error'){
      this.options.log?.(`relay: ${frame.code}`);
      // Another Dervo signed in to this room with this Mac's key (a second window, say). Stop rather than take turns.
      if(frame.code==='replaced'||frame.code==='other_mac'){this.halted=frame.code;this.changed();}
      return;
    }
    if(frame.t==='ready'){
      this.ready=true;this.backoff=1000;
      this.startPings();
      this.allow();
      this.send({t:'rekey',key:this.state.roomKey});
      for(const pk of this.state.notices??[])this.sealTo(pk,{t:'unpaired'},{keep:'q'});
      if(this.state.notices?.length){this.state.notices=[];await this.persist();}
      this.lastHome='';  // the relay's copy may be older than ours: send it again
      this.changed();
      await this.publish();
      return;
    }
    if(frame.t!=='env')return;
    const env=frame.env;
    // The receipt goes after the envelope is handled and recorded, so a crash in between leaves the relay's copy.
    if(this.state.seen[env.n]){
      // A copy the relay delivers again (Dervo closed before its receipt) still gets an answer: the recorded one, or
      // "can't tell" for one that was running. Never a second run.
      const again=this.state.phones.some(phone=>phone.pk===env.from)?readPhoneMessage(open(env,this.state.keys,env.from)):null;
      if(again?.t==='action'&&(this.state.done[`${env.from}:${again.id}`]||this.state.running?.[`${env.from}:${again.id}`]!==undefined))await this.act(env.from,again.id,again.macAt,again.action,env.n);
      this.send({t:'got',n:[env.n]});return;
    }
    const paired=this.state.phones.find(phone=>phone.pk===env.from);
    const message=readPhoneMessage(open(env,this.state.keys,env.from));
    if(!message){this.send({t:'got',n:[env.n]});return;}
    this.state.seen[env.n]=this.now();
    if(!paired){
      if(message.t==='pair')await this.pair(env.from,message.code,message.device);
      // A phone that was removed while it was away still thinks it's paired. Tell it, sealed, so it can trust it.
      else if(message.t==='hello'||message.t==='action'||message.t==='open')this.sealTo(env.from,{t:'unpaired'});
      await this.persist();
      return;
    }
    // A phone coming back after a while gets Home again even if nothing changed: its copy may be older.
    const returning=!this.activeAt.has(env.from);
    this.markActive(env.from);
    switch(message.t){
      // A paired phone still asking to pair missed the word: say it again.
      case 'pair':this.sealTo(env.from,{t:'paired',mac:this.options.mac},{keep:'q'});this.lastHome='';await this.publish();break;
      case 'hello':if(returning)this.lastHome='';await this.publish();break;
      case 'unpair':await this.unpair(env.from);break;
      case 'open':
        this.openThreads.set(env.from,{projectId:message.projectId,channelId:message.channelId});
        this.lastThread.delete(env.from);
        await this.openThread(env.from,message.projectId,message.channelId);
        break;
      case 'close':this.openThreads.delete(env.from);this.lastThread.delete(env.from);break;
      case 'action':
        // Dated by ``macAt``: this Mac's own clock at the tap, as the phone reckons it from the Mac's sealed times. The
        // relay can't change it, and the phone's clock being off doesn't matter.
        await this.act(env.from,message.id,message.macAt,message.action,env.n);break;
    }
    await this.persist();
    this.send({t:'got',n:[env.n]});
  }

  /** The right code starts a request you allow on the Mac (``allowPhone``). Whoever sends the code first holds it, so
   * the Mac shows six digits that your own phone shows too. */
  private async pair(pk:string,code:string,device:string){
    const pairing=this.state.pairing;
    if(!pairing||pairing.expires<this.now()||pairing.code!==code)return;  // silent: a guess learns nothing
    this.state.pairing=null;
    this.asking={pk,device,digits:pairingDigits(this.state.keys.publicKey,pk,code),expires:this.now()+PAIRING_MS};
    await this.persist();
    this.changed();
    this.options.log?.(`asking to pair ${device}`);
  }

  // ---------- Home ----------

  /** Read Home now instead of at the next poll: the desktop calls this on the host's ``dervo-host-event``. */
  nudge(){void this.publish();}

  /** A phone just opened: switch from the slow read to the fast one now, not when the slow timer next fires. */
  private markActive(pk:string){
    const wasIdle=!this.active();
    this.activeAt.set(pk,this.now());
    if(wasIdle&&this.timer&&!this.stopped){clearTimeout(this.timer);this.poll();}
  }

  private poll(){
    if(this.stopped)return;
    // Fast while a phone is open, slow otherwise, so a paired phone in a pocket costs the host almost nothing.
    this.timer=setTimeout(async()=>{await this.publish();this.poll();},this.active()?this.options.pollMs??2000:IDLE_POLL_MS);
  }

  /** Null until the first read finds out whether the host has the state spine's ``thread_states``. */
  private spine:boolean|null=null;

  /** Whether the Mac is staying awake for the runs Home shows (keep_awake.py), as the Rust link sends it. */
  private async withAwake(home:HomeSnapshot):Promise<HomeSnapshot>{
    try{
      const awake=await this.options.host<{enabled?:boolean;holding?:boolean;supported?:boolean}>('keep_awake_status');
      return awake.supported===true?{...home,awake:{enabled:awake.enabled===true,holding:awake.holding===true}}:home;
    }catch{return home;/* a host that can't say */}
  }

  async snapshot():Promise<HomeSnapshot>{
    const host=this.options.host;
    const projects=await host<HostProject[]>('projects');
    if(this.spine!==false){
      try{
        const {threads}=await host<{threads:HostThreadState[]}>('thread_states',{since:0});
        this.spine=true;
        for(const record of threads){this.known.add(record.id);if(record.name)this.names.set(record.id,record.name);}
        return buildSnapshotFromStates({mac:this.options.mac,now:this.now(),projects,threads});
      }catch(error){
        // Only an older host's refusal switches to the fallback; anything else is a real failure to report.
        // The Python host says "Unsupported"; the desktop bridge rejects an operation it doesn't know before the host sees
        // it ("unknown variant").
        if(this.spine===null&&/Unsupported|unknown variant|invalid args/i.test(error instanceof Error?error.message:String(error)))this.spine=false;
        else throw error;
      }
    }
    const conversations:Record<string,HostConversation[]>={};
    for(const project of projects){
      try{conversations[project.id]=await host<HostConversation[]>('conversations',{projectId:project.id});}
      catch{conversations[project.id]=[];/* an archived project, say */}
    }
    if(!this.lostRuns||this.now()-this.lostRuns.at>=STATUS_MS){
      let runs:HostLostRun[]=[];
      try{runs=(await host<{lostRuns?:HostLostRun[]}>('status')).lostRuns??[];}catch{/* none known */}
      this.lostRuns={at:this.now(),runs};
    }
    const lostRuns=this.lostRuns.runs;
    for(const list of Object.values(conversations))for(const row of list){this.known.add(row.id);this.names.set(row.id,row.name);}
    return buildSnapshot({mac:this.options.mac,now:this.now(),projects,conversations,lostRuns});
  }

  private publishing=false;
  /** Sends the open thread again to each phone viewing one, so replies show while you watch. Home's chat is read on
   * every poll, because its reply lands without moving any thread's row. */
  private async refreshThreads(homeOnly=false){
    for(const [pk,open] of this.openThreads)if(!homeOnly||open.channelId===HOME_CHANNEL)await this.openThread(pk,open.projectId,open.channelId);
  }
  /** Sends Home to every paired phone when it changed. A thread that just started waiting on you also wakes a phone
   * that's away, with a notice that says nothing about which thread. */
  async publish(){
    if(!this.ready||!this.state.phones.length)return;
    // A change that lands mid-read isn't lost: the read runs once more when this one ends.
    if(this.publishing){this.dirty=true;return;}
    this.publishing=true;
    try{
      do{this.dirty=false;await this.publishOnce();}while(this.dirty&&this.ready);
    }finally{this.publishing=false;}
  }

  private async publishOnce(){
    try{
      const home=fitSnapshot(await this.withAwake(await this.snapshot()));
      const key=JSON.stringify({...home,at:0});
      const moved=key!==this.lastHome;
      if(!moved){
        if(this.active())await this.refreshThreads(true);
        // Unchanged: a heartbeat while a phone is open, sent live only, so the relay stores nothing for it.
        if(this.active()&&this.now()-this.lastSent>=HEARTBEAT_MS){
          this.lastSent=this.now();
          for(const phone of this.state.phones)this.sealTo(phone.pk,{t:'snapshot',home});
        }
        return;
      }
      this.lastHome=key;
      this.lastSent=this.now();
      const needs=new Set(home.rows.filter(row=>row.bucket==='needs').map(row=>row.id));
      const wake=[...needs].some(id=>!this.lastNeeds.has(id));
      this.lastNeeds=needs;
      // A thread that just finished gets a quieter push. Not on the first read, when every finished thread is "new".
      const finished=new Set(home.rows.filter(row=>row.bucket==='finished').map(row=>row.id));
      const news=this.lastFinished!==null&&[...finished].some(id=>!this.lastFinished!.has(id));
      this.lastFinished=finished;
      for(const phone of this.state.phones)this.sealTo(phone.pk,{t:'snapshot',home},{keep:'home',...(wake?{wake}:news?{news}:{})});
      if(moved)await this.refreshThreads();
    }catch(error){this.options.log?.(`snapshot failed: ${error instanceof Error?error.message:error}`);}
  }

  private async openThread(pk:string,projectId:string,channelId:string){
    const host=this.options.host;
    if(channelId===HOME_CHANNEL)return this.openHome(pk,projectId);
    try{
      const feed=await host<{snapshot?:{history?:HostFeedRow[]}}>('conversation_feed',{projectId,channelId,cursor:null,force:true});
      const tasks=await host<HostTask[]>('tasks',{projectId,channelId});
      // A result waiting on review needs its evidence to tell whether the phone can accept it.
      const details:Record<string,HostTaskDetail>={};
      for(const task of tasks.filter(item=>item.state==='ready_for_review')){
        const key=detailKey(task),kept=this.details.get(task.id);
        if(kept&&kept.key===key&&this.now()-kept.at<60_000){details[task.id]=kept.detail;continue;}
        details[task.id]=await host<HostTaskDetail>('task_detail',{projectId,channelId,taskId:task.id});
        this.details.set(task.id,{key,at:this.now(),detail:details[task.id]});
      }
      for(const [id,kept] of this.details)if(this.now()-kept.at>=60_000)this.details.delete(id);
      const thread=threadOf({projectId,channelId,name:this.names.get(channelId)??'Thread',now:this.now(),history:feed.snapshot?.history??[],tasks,you:this.you});
      const asks=asksOf(tasks,details);
      const approvals=this.state.phones.find(phone=>phone.pk===pk)?.approvals===true;
      this.sendThread(pk,{...thread,...(asks.length?{asks}:{}),...(approvals?{approvals:true}:{})});
    }catch(error){
      this.options.log?.(`thread read failed: ${error instanceof Error?error.message:error}`);
      // A thread this Mac has never listed isn't here, and the phone says so. Any other failure sends nothing, and the
      // phone says it couldn't load the conversation.
      if(this.known.size&&!this.known.has(channelId))this.sendThread(pk,{projectId,channelId,name:'',messages:[],at:this.now(),missing:true});
    }
  }

  private sendThread(pk:string,full:ThreadDetail){
    const thread=fitThread(full);
    const key=JSON.stringify({...thread,at:0});
    if(this.lastThread.get(pk)===key)return;
    this.lastThread.set(pk,key);
    this.sealTo(pk,{t:'thread',thread});
  }

  /** The project's Home chat, read from the same coordinator the desktop's Home uses. */
  private projectsRead:{at:number;projects:HostProject[]}|null=null;
  private async homeScope(projectId:string){
    // Read at most every 30 s while Home's chat is open; a project's account rarely changes.
    if(!this.projectsRead||this.now()-this.projectsRead.at>=STATUS_MS||!this.projectsRead.projects.some(item=>item.id===projectId))
      this.projectsRead={at:this.now(),projects:await this.options.host<HostProject[]>('projects')};
    const project=this.projectsRead.projects.find(item=>item.id===projectId);
    if(!project?.profile_id)throw new Error('This project isn\'t on this Mac any more.');
    return {project,scope:{projectId,profileId:project.profile_id}};
  }
  private async openHome(pk:string,projectId:string){
    try{
      const {project,scope}=await this.homeScope(projectId);
      const listing=await this.options.host<{entries?:HostHomeEntry[]}>('orchestrator_list',scope);
      this.sendThread(pk,homeChatOf({projectId,name:projectName(project),now:this.now(),entries:listing.entries??[]}));
    }catch(error){this.options.log?.(`Home read failed: ${error instanceof Error?error.message:error}`);}
  }

  // ---------- actions ----------

  private async act(pk:string,id:string,macAt:number|undefined,action:Action,n?:string){
    // Keyed by phone too, so one phone's id can never answer for another's.
    const key=`${pk}:${id}`;
    const done=this.state.done[key];
    if(done){this.sealTo(pk,{t:'ack',id,state:done.state,detail:done.detail},{keep:'q'});return;}
    let result:{state:AckState;detail:string};
    if(this.state.running?.[key]!==undefined){
      // Dervo closed while this was running on the host. It may have gone through, so it's never run again.
      result={state:'refused',detail:'Dervo closed while doing this, so it can\'t tell whether it went through. Check the thread before trying again.'};
    }else if(macAt===undefined){
      result={state:'refused',detail:'Dervo on your phone is too old to send this, so it wasn\'t run. Update Dervo on your phone, then try again.'};
    }else if(macAt-this.now()>CLOCK_SKEW_MS){
      result={state:'refused',detail:'This phone\'s time changed while it waited, so this wasn\'t run. Try again.'};
    }else if(this.now()-macAt>ACTION_TTL[action.kind]){
      result={state:'expired',detail:action.kind==='stop'?'Your Mac was away, so this Stop wasn\'t run. Check the thread and stop it again if it\'s still going.':'This message waited too long for your Mac, so it wasn\'t sent.'};
    }else{
      this.state.running={...this.state.running,[key]:this.now()};
      await this.persist();
      result=await this.run(action,id,macAt,pk);
    }
    if(this.state.running)delete this.state.running[key];
    // Recorded before the ack goes out: a phone that asks again gets the same answer, and nothing runs twice.
    // Kept SEEN_MS past the later of now and ``macAt``, set in the same save: once a copy is forgotten, it's older than
    // any action may be on this Mac's clock, so a replay is answered expired.
    const kept=macAt===undefined?this.now():Math.max(this.now(),macAt);
    this.state.done[key]={at:Math.max(this.state.done[key]?.at??0,kept),...result};
    if(n)this.state.seen[n]=Math.max(this.state.seen[n]??0,kept);
    await this.persist();
    this.sealTo(pk,{t:'ack',id,...result},{keep:'q'});
    this.lastHome='';
    await this.publish();
  }

  /** A removed phone's answers go with it. Envelope nonces stay until time clears them, and any copy that comes back is
   * dated by ``macAt`` on this Mac's clock anyway, so nothing old can run. */
  private forgetActions(pk:string){
    for(const key of Object.keys(this.state.done))if(key.startsWith(`${pk}:`))delete this.state.done[key];
  }

  /** ``tapped`` is when you tapped it, on this Mac's clock. */
  private async run(action:Action,id:string,tapped:number,pk:string):Promise<{state:AckState;detail:string}>{
    const host=this.options.host;
    const scope={projectId:action.projectId,channelId:action.channelId};
    try{
      if(action.kind==='home'){
        const {scope:home}=await this.homeScope(action.projectId);
        // Idempotent by client id on the host too: a repeat returns the first record and never asks twice.
        await host('orchestrator_send',{...home,text:action.text,clientId:`phone-${id}`});
        return {state:'done',detail:'Sent to Dervo.'};
      }
      if(action.kind==='send'){
        // The phone's action id is the request's client id, so the host itself refuses a second copy too.
        const result=await host<{id:string|null;state?:string;detail?:string}>('coding_request',{clientId:`phone-${id}`,text:action.text,...scope,queueIfBusy:true});
        if(!result||result.id===null)return {state:'refused',detail:result?.detail||'Dervo didn\'t take this message.'};
        return result.state==='queued'?{state:'queued',detail:'Queued. It goes in when the current run ends.'}:{state:'done',detail:'Sent.'};
      }
      if(action.kind==='answer'){
        // Only your words go to the agent; an answer never grants anything (task_questions.py). The action id is the
        // client id, so the host returns its saved answer to a second copy rather than sending it twice.
        const view=await host<{delivery?:string;detail?:string}>('task_question_answer',{...scope,taskId:action.taskId,questionId:action.questionId,clientId:`phone-${id}`,answer:action.answer});
        if(action.answer.kind==='skip')return {state:'done',detail:'Skipped.'};
        if(view?.delivery==='queued')return {state:'queued',detail:'Answer saved. It goes in when the agent is free.'};
        if(view?.delivery==='not_sent'||view?.delivery==='unknown')return {state:'refused',detail:view.detail||'Your answer is saved on your Mac, but it didn\'t reach the agent. Open the thread there to send it.'};
        return {state:'done',detail:'Answered.'};
      }
      if(action.kind==='scope'||action.kind==='review'){
        if(this.state.phones.find(phone=>phone.pk===pk)?.approvals!==true)
          return {state:'refused',detail:'This phone can\'t approve yet. On your Mac, open Dervo, then Settings, Phone, and turn on approvals for it.'};
        const current=(await host<HostTask[]>('tasks',scope)).find(item=>item.id===action.taskId);
        if(action.kind==='scope'){
          // The host's deny takes no version, so a decision held up in the mail can't touch a scope that changed since.
          if(current?.state!=='needs_permission'||current.scope_version!==action.scopeVersion)
            return {state:'refused',detail:'This scope changed or was already decided on your Mac, so nothing was done. Check the thread.'};
          // Declining is fine from anywhere; approving more than the run's own files needs the Mac's sheet, which shows it.
          if(action.decision==='approve'&&!scopeOnPhone(current))
            return {state:'refused',detail:'This run asks for more than its own files, so approve it on your Mac, where you can see what it covers.'};
          if(action.decision==='deny'){await host('task_deny',{...scope,taskId:action.taskId});return {state:'done',detail:'Declined. Nothing runs.'};}
          await host('task_approve',{...scope,taskId:action.taskId,scopeVersion:action.scopeVersion});
          return {state:'done',detail:'Approved. The run is starting.'};
        }
        // The host doesn't refuse a second decision on the same attempt by itself, so a result that's no longer waiting
        // (decided on the Mac, or by another phone) is answered here. The same rules that showed the card decide again
        // now: Accept only where the phone offered it, on the attempt you saw.
        if(current?.state!=='ready_for_review'||current.review==='rejected')return {state:'refused',detail:'This result was already decided. Check the thread for where it is now.'};
        const detail=await host<HostTaskDetail>('task_detail',{...scope,taskId:action.taskId});
        const ask=asksOf([current],{[current.id]:detail}).find(item=>item.kind==='review');
        if(ask?.kind!=='review'||ask.attemptNumber!==action.attemptNumber)return {state:'refused',detail:'This result changed on your Mac since you looked. Check the thread.'};
        if(action.decision==='approved'&&!ask.phone)return {state:'refused',detail:'This result needs the review sheet on your Mac to accept it.'};
        const task=await host<{commit?:{sha?:string;error?:string;skipped?:string}|null}>('task_review',{...scope,taskId:action.taskId,attemptNumber:action.attemptNumber,decision:action.decision});
        if(action.decision==='rejected'){
          // The rejection is recorded by now, so a note that fails doesn't turn it into a failed decision.
          if(action.text)try{await host('task_continue',{...scope,taskId:action.taskId,text:action.text});}
          catch{return {state:'done',detail:'Marked for changes, but your note didn\'t send. Say what to change in the thread.'};}
          return {state:'done',detail:action.text?'Sent back with your changes.':'Marked for changes. Say what to change in the thread.'};
        }
        if(task?.commit?.sha)return {state:'done',detail:'Accepted and committed.'};
        if(task?.commit?.error)return {state:'done',detail:`Accepted. The commit didn't go through: ${task.commit.error}`};
        return {state:'done',detail:'Accepted.'};
      }
      // Only runs that had started when you tapped Stop: a late Stop never stops the run after it.
      const targets=stopTargets(await host<HostTask[]>('tasks',scope),tapped);
      if(!targets.length)return {state:'refused',detail:'Nothing is running in this thread now.'};
      const failures:string[]=[];
      for(const taskId of targets){
        try{await host('task_stop',{...scope,taskId});}catch(error){failures.push(error instanceof Error?error.message:String(error));}
      }
      return failures.length?{state:'refused',detail:failures.join(' ')}:{state:'done',detail:'Stopping. Edits are kept.'};
    }catch(error){
      return {state:'refused',detail:error instanceof Error?error.message:String(error)};
    }
  }

  private async persist(){
    const cutoff=this.now()-SEEN_MS;
    for(const [key,at] of Object.entries(this.state.seen))if(at<cutoff)delete this.state.seen[key];
    for(const [key,done] of Object.entries(this.state.done))if(done.at<cutoff)delete this.state.done[key];
    for(const [key,at] of Object.entries(this.state.running??{}))if(at<cutoff)delete this.state.running![key];
    if(this.state.pairing&&this.state.pairing.expires<this.now())this.state.pairing=null;
    await this.options.store.save(this.state);
  }
}
