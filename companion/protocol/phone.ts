import {newKeyPair,open,pairingDigits,prove,seal,type KeyPair} from './crypto.ts';
import {ACTION_TTL,readMacMessage,type Action,type AckState,type HomeSnapshot,type ThreadDetail} from './messages.ts';
import type {PairingOffer} from './pairing.ts';
import {readFromRelay,type ToRelay} from './wire.ts';

/** The phone's side, with no React Native in it, so the app and the tests run the same code. The app keeps a
 * ``PhoneState`` in secure storage and renders from it; this class only changes it. */

export type Pending={id:string;at:number;action:Action;state:'waiting'|AckState;detail:string;
  /** The Mac's clock at the tap, as this phone reckons it (``macTime``). The Mac judges the action's age by it. */
  macAt?:number};
export type PhoneState={keys:KeyPair;offer:Omit<PairingOffer,'code'|'expires'>;code:string|null;paired:boolean;
  home:HomeSnapshot|null;outbox:Pending[];
  /** When this phone got ``home``, on its own clock. How current Home is never compares the two devices' clocks. */
  homeAt?:number;
  /** The newest Mac time this phone has had sealed from the Mac (``mac``), and when it was current on this phone's clock
   * (``phone``). Actions are dated from it, so the phone's own clock being off never matters, and the relay can't
   * change it. */
  macClock?:{mac:number;phone:number}};
/** What the phone keeps in its caches folder between launches (not the keys): Home, pending sends, and the Mac time
 * actions are dated from. Nothing once it's unpaired. */
export type PhoneCache=Pick<PhoneState,'home'|'outbox'|'homeAt'|'macClock'>;
export function cacheOf(state:PhoneState):PhoneCache|null{
  return state.paired?{home:state.home,outbox:state.outbox,homeAt:state.homeAt,macClock:state.macClock}:null;
}
export type Live={connected:boolean;macOnline:boolean;macSeen:number|null;error:string|null;thread:ThreadDetail|null};
export type PhoneOptions={connect:(url:string)=>SocketLike;save:(state:PhoneState)=>void|Promise<void>;device:string;now?:()=>number;newId?:()=>string};
export type SocketLike={send(text:string):void;close():void;onopen:(()=>void)|null;onmessage:((event:{data:unknown})=>void)|null;onclose:(()=>void)|null;onerror:(()=>void)|null};

export function stateFromOffer(offer:PairingOffer):PhoneState{
  const {code,expires:_,...rest}=offer;
  return {keys:newKeyPair(),offer:rest,code,paired:false,home:null,outbox:[]};
}

/** Settled sends are kept a day so you can see what happened; waiting ones until the Mac answers. */
const KEEP_SETTLED_MS=24*3600_000;
/** A send nobody answered is dropped from the list after a week; by then it can only read as "not confirmed". */
const KEEP_WAITING_MS=7*24*3600_000;
/** A live Mac time this far behind the phone's reckoning is the Mac's clock really stepping back, not a stale copy. */
const MAC_STEP_MS=5*60_000;
const PING_MS=25_000;
const PONG_MS=10_000;
/** How long a connection may take to sign in before it's dropped and dialed again. */
const READY_MS=15_000;
/** On wake, a sign-in older than this is started over at once. */
const DIAL_MS=5_000;

/** Whether a send that's still waiting has outlived the time the Mac would run it. The phone can't know what happened,
 * so it says so instead of "Sending…". */
export function unconfirmed(item:Pending,now:number){return item.state==='waiting'&&now-item.at>ACTION_TTL[item.action.kind];}

export class PhoneLink{
  live:Live={connected:false,macOnline:false,macSeen:null,error:null,thread:null};
  private socket:SocketLike|null=null;
  private stopped=false;
  private backoff=1000;
  private seen=new Set<string>();
  private listeners=new Set<()=>void>();
  private pushToken:string|null=null;
  /** The relay's challenge is answered once per connection, before ready. */
  private answered=false;
  private pinger:ReturnType<typeof setInterval>|null=null;
  private pongTimer:ReturnType<typeof setTimeout>|null=null;
  state:PhoneState;
  private options:PhoneOptions;
  constructor(state:PhoneState,options:PhoneOptions){this.state=state;this.options=options;}

  private get now(){return this.options.now??Date.now;}
  subscribe(listener:()=>void){this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};}
  private changed(save=false){
    if(save)void this.options.save(this.state);
    for(const listener of this.listeners)listener();
  }

  start(){this.stopped=false;this.dial();}
  stop(){this.stopped=true;this.quietPings();this.socket?.close();this.socket=null;}
  /** Called when the app comes back to the foreground: reconnect now instead of waiting out the backoff, and check a
   * socket iOS may have left half open while the app was suspended. */
  wake(){
    if(this.stopped)return;
    if(!this.socket){this.backoff=1000;this.dial();return;}
    if(this.live.connected){this.ping(5000);return;}
    // A sign-in that's gone nowhere for a while (a dial made with no network, or cut off by suspension) starts over.
    if(this.now()-this.dialedAt>DIAL_MS)this.redial();
  }
  /** Ask the Mac for Home again (pull to refresh). */
  hello(){if(this.state.paired)this.toMac({t:'hello'});}
  /** Six digits to compare with the ones on the Mac while pairing. */
  get digits(){return pairingDigits(this.state.offer.mac,this.state.keys.publicKey,this.state.code??'');}

  private ping(timeout=PONG_MS){
    this.send({t:'ping'});
    if(this.pongTimer)return;
    this.pongTimer=setTimeout(()=>{this.pongTimer=null;try{this.socket?.close();}catch{/* closing anyway */}this.socket?.onclose?.();},timeout);
  }
  private quietPings(){
    if(this.pinger)clearInterval(this.pinger);
    if(this.pongTimer)clearTimeout(this.pongTimer);
    this.pinger=null;this.pongTimer=null;
  }

  setPushToken(token:string|null){this.pushToken=token;if(this.live.connected&&this.state.paired)this.send({t:'push',token});}

  private dialedAt=0;
  private redial(){const socket=this.socket;try{socket?.close();}catch{/* closing anyway */}socket?.onclose?.();}

  private dial(){
    if(this.stopped||this.socket)return;
    this.dialedAt=this.now();
    const socket=this.options.connect(`${this.state.offer.relay}/v1/rooms/${this.state.offer.room}`);
    this.answered=false;
    this.socket=socket;
    // The relay opens with a challenge, answered in onFrame.
    socket.onopen=()=>{};
    // A socket that was replaced can still deliver a last frame; only the current one is heard.
    socket.onmessage=event=>{if(this.socket===socket)this.onFrame(String(event.data));};
    const lost=()=>{
      if(this.socket!==socket)return;
      this.socket=null;
      this.quietPings();
      this.live={...this.live,connected:false};
      this.changed();
      if(this.stopped)return;
      setTimeout(()=>{this.dial();},this.backoff*(1+Math.random()/2));
      this.backoff=Math.min(this.backoff*2,30_000);
    };
    socket.onclose=lost;
    socket.onerror=()=>{try{socket.close();}catch{/* closing anyway */}lost();};
    // No ready in time: close and try again with the usual backoff.
    setTimeout(()=>{if(this.socket===socket&&!this.live.connected){try{socket.close();}catch{/* closing anyway */}lost();}},READY_MS);
  }

  /** Before ready the relay takes only the sign-in answer and closes on anything else, so the rest waits: the outbox
   * goes again on ready, and so does everything else that matters. */
  private send(frame:ToRelay){
    if(!this.live.connected&&frame.t!=='auth')return;
    try{this.socket?.send(JSON.stringify(frame));}catch{/* resent on reconnect */}
  }
  private toMac(message:unknown,keep?:'q'){this.send({t:'send',env:seal(message,this.state.keys,this.state.offer.mac,keep?{keep}:{})});}

  private onFrame(raw:string){
    const frame=readFromRelay(raw);
    if(!frame)return;
    switch(frame.t){
      case 'challenge':
        // Once per connection, before ready, and never for the Mac's key or our own.
        if(this.answered||this.live.connected||frame.challenge.pk===this.state.offer.mac||frame.challenge.pk===this.state.keys.publicKey){
          try{this.socket?.close();}catch{/* closing anyway */}
          return;
        }
        this.answered=true;
        try{this.send({t:'auth',role:'phone',pk:this.state.keys.publicKey,key:this.state.offer.key,proof:prove(frame.challenge,this.state.keys,'phone',this.state.offer.room)});}
        catch{try{this.socket?.close();}catch{/* closing anyway */}}
        return;
      case 'pong':
        if(this.pongTimer)clearTimeout(this.pongTimer);
        this.pongTimer=null;
        return;
      case 'ready':
        // A phone the relay no longer lists keeps backing off, so a removal that hasn't reached it yet doesn't turn into
        // a reconnect every second.
        if(frame.allowed||!this.state.paired)this.backoff=1000;
        this.quietPings();
        let beat=0;
        // A ping keeps the relay socket honest; every second one also says hello to the Mac, which reads Home often
        // only while a phone is open.
        this.pinger=setInterval(()=>{this.ping();if(++beat%2===0&&this.state.paired)this.toMac({t:'hello'});},PING_MS);
        // A phone the relay doesn't list as paired was removed, or is still pairing. Only the Mac's sealed word removes
        // it here; its hello gets that answer if the Mac really removed it.
        this.live={...this.live,connected:true,macOnline:frame.macOnline,macSeen:frame.macSeen,error:this.state.paired&&!frame.allowed?'not_allowed':null};
        if(!this.state.paired&&this.state.code)this.toMac({t:'pair',code:this.state.code,device:this.options.device});
        if(this.state.paired){
          this.toMac({t:'hello'});
          if(frame.allowed){
            if(this.pushToken)this.send({t:'push',token:this.pushToken});
            // What's waiting goes now even with the Mac away: the relay holds it. The thread waits for the Mac.
            this.resend();
            if(frame.macOnline&&this.live.thread)this.open(this.live.thread.projectId,this.live.thread.channelId);
            // A close made while the socket was down.
            if(!this.live.thread)this.toMac({t:'close'});
          }
        }
        return this.changed();
      case 'presence':
        this.live={...this.live,macOnline:frame.macOnline,macSeen:frame.macSeen};
        if(frame.macOnline&&this.state.paired){this.toMac({t:'hello'});this.catchUp();}
        // Pairing while the Mac was away: ask again now it's here. A used code is ignored, so a repeat is safe.
        if(frame.macOnline&&!this.state.paired&&this.state.code){
          if(this.live.error==='mac_offline')this.live={...this.live,error:null};
          this.toMac({t:'pair',code:this.state.code,device:this.options.device});
        }
        return this.changed();
      case 'error':
        // Plain relay errors are never trusted to remove the pairing; the Mac's sealed ``unpaired`` does that.
        this.live={...this.live,error:frame.code};
        return this.changed();
      case 'env':{
        const env=frame.env;
        const held=frame.held??0;
        this.send({t:'got',n:[env.n]});
        if(this.seen.has(env.n))return;
        this.seen.add(env.n);
        const message=readMacMessage(open(env,this.state.keys,this.state.offer.mac));
        if(!message)return;
        switch(message.t){
          case 'paired':
            this.state={...this.state,paired:true,code:null,offer:{...this.state.offer,name:message.mac}};
            if(this.pushToken)this.send({t:'push',token:this.pushToken});
            return this.changed(true);
          case 'snapshot':
            // When it was current, on this phone's clock: now, less however long the relay held it.
            // An older held copy arriving after a live one never replaces it. An older live one does: the Mac's clock went
            // back, and Home (and the Mac time actions are dated from) follows it.
            if(this.state.home&&this.state.home.at>message.home.at&&held>0)return;
            // The Mac time is taken as current when it arrives, ignoring ``held``: the relay sets that, and a large one
            // would date actions later than the Mac really is. Arriving late only makes an action look older.
            this.state={...this.state,home:message.home,homeAt:this.now()-held,macClock:this.laterMac(message.home.at,held===0)};
            // Sends tapped before the first Mac time can go now.
            if(this.state.outbox.some(item=>item.state==='waiting'&&item.macAt===undefined))this.resend();
            return this.changed(true);
          case 'thread':
            // A late reply for a thread you've left, or a different one, is dropped.
            if(!this.live.thread||this.live.thread.channelId!==message.thread.channelId||this.live.thread.projectId!==message.thread.projectId)return;
            this.live={...this.live,thread:message.thread};
            if(message.thread.at>0)this.state={...this.state,macClock:this.laterMac(message.thread.at,false)};
            return this.changed();
          case 'ack':{
            const outbox=this.state.outbox.map(item=>item.id===message.id?{...item,state:message.state,detail:message.detail}:item);
            this.state={...this.state,outbox};
            return this.changed(true);
          }
          case 'unpaired':
            this.forget();
            return this.changed(true);
        }
      }
    }
  }

  /** When the Mac is (back) online: reopen the thread on screen, and send again what it hasn't answered, with the same
   * ids. The Mac answers a repeat with its first answer and never runs it twice. Repeats into an away Mac's mailbox
   * push out this phone's own older copies first, never another device's. */
  private catchUp(){
    if(this.live.thread)this.open(this.live.thread.projectId,this.live.thread.channelId);
    this.resend();
  }
  private resend(){for(const item of this.state.outbox)if(item.state==='waiting'&&!unconfirmed(item,this.now()))this.sendAction(item);}
  private sendAction(item:Pending){
    // Tapped before this phone had any Mac time (just paired, Home not in yet): dated now that it has one, from how
    // long ago the tap was on this phone's clock. Until then it waits here rather than go undated.
    if(item.macAt===undefined){
      const clock=this.state.macClock;
      if(!clock)return;
      item.macAt=Math.round(clock.mac+Math.min(0,item.at-clock.phone));
      this.changed(true);
    }
    this.toMac({t:'action',id:item.id,at:item.at,sent:this.now(),macAt:item.macAt,action:item.action},'q');
  }
  /** A Mac time moves the reckoning only forward, so a stale copy (a relay can strip ``held`` or pick which to deliver)
   * never dates a Stop too early. The one way back is a live Home well behind it: the Mac's clock really stepped back. */
  private laterMac(at:number,live:boolean){
    const now=this.macTime();
    return now===undefined||at>now||(live&&at<now-MAC_STEP_MS)?{mac:at,phone:this.now()}:this.state.macClock;
  }
  /** The Mac's clock now, as this phone reckons it: the latest Mac time it has, plus the time since, on its own clock. */
  macTime():number|undefined{
    const clock=this.state.macClock;
    return clock?Math.round(clock.mac+Math.max(0,this.now()-clock.phone)):undefined;
  }

  private forget(){
    this.state={...this.state,paired:false,code:null,home:null,outbox:[],macClock:undefined};
    this.live={...this.live,error:'unpaired'};
    this.stop();
  }

  /** Ask the Mac to do something. It goes now if the Mac is connected, else the relay holds it (sealed) until the Mac is
   * back. A Stop that waits more than two minutes is dropped by the Mac, and the phone says so. */
  act(action:Action):Pending{
    const macAt=this.macTime();
    const pending:Pending={id:(this.options.newId??randomId)(),at:this.now(),action,state:'waiting',detail:'',...(macAt!==undefined?{macAt}:{})};
    const cutoff=this.now()-KEEP_SETTLED_MS;
    const lost=this.now()-KEEP_WAITING_MS;
    this.state={...this.state,outbox:[...this.state.outbox.filter(item=>item.state==='waiting'?item.at>lost:item.at>cutoff),pending]};
    this.sendAction(pending);
    this.changed(true);
    return pending;
  }

  /** Drop a send from the list: one the Mac turned down, or one it never confirmed that you've checked. A send still
   * in time stays, because it may yet run. */
  discard(id:string){
    const item=this.state.outbox.find(entry=>entry.id===id);
    if(!item||(item.state==='waiting'&&!unconfirmed(item,this.now())))return;
    this.state={...this.state,outbox:this.state.outbox.filter(entry=>entry.id!==id)};
    this.changed(true);
  }

  open(projectId:string,channelId:string){
    if(this.live.thread?.channelId!==channelId||this.live.thread.projectId!==projectId)this.live={...this.live,thread:{projectId,channelId,name:'',messages:[],at:0}};
    this.toMac({t:'open',projectId,channelId});
    this.changed();
  }
  close(){
    if(this.live.thread&&this.state.paired)this.toMac({t:'close'});
    this.live={...this.live,thread:null};this.changed();
  }
  /** Held by the relay if the Mac is away, so the Mac drops this phone when it's back. */
  unpair(){this.toMac({t:'unpair'},'q');this.forget();this.changed(true);}
}

function randomId(){
  const bytes=new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');
}
