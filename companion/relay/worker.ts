/// <reference types="@cloudflare/workers-types" />
import {Room,LIMITS,type Socket,type Storage} from './room.ts';

/** The relay as a Cloudflare Worker: one Durable Object per room, addressed by the room id in the pairing code.
 * Not deployed. See companion/README.md for what deploying needs and who approves it. */
export interface Env{ROOMS:DurableObjectNamespace;
  /** Optional: an Expo access token, if push security is turned on for the Expo project. */
  EXPO_ACCESS_TOKEN?:string;
  /** Cloudflare's rate limiting binding (wrangler.toml ``[[ratelimits]]``): connections per client address. Opening a room
   * costs storage on the bill, so a flood from one address is turned away before it reaches a Durable Object. */
  CONNECTS?:{limit(options:{key:string}):Promise<{success:boolean}>}}

const ROOM=/^\/v1\/rooms\/([A-Za-z0-9_-]{16,64})$/;

export default {
  async fetch(request:Request,env:Env):Promise<Response>{
    const url=new URL(request.url);
    if(url.pathname==='/health')return new Response('ok');
    const match=ROOM.exec(url.pathname);
    if(!match)return new Response('Not found',{status:404});
    if(request.headers.get('Upgrade')!=='websocket')return new Response('Expected a WebSocket',{status:426});
    const address=request.headers.get('CF-Connecting-IP')??'unknown';
    if(env.CONNECTS&&!(await env.CONNECTS.limit({key:address})).success)return new Response('Too many connections. Try again in a minute.',{status:429});
    return env.ROOMS.get(env.ROOMS.idFromName(match[1])).fetch(request);
  },
};

/** A content-free notice through Expo's push service, which holds the Apple push key. The body never says which thread
 * or what happened: the phone fills that in from the sealed snapshot once it's open. */
export async function expoPush(token:string,kind:'needs'|'done',accessToken?:string){
  await fetch('https://exp.host/--/api/v2/push/send',{method:'POST',
    headers:{'content-type':'application/json',...(accessToken?{authorization:`Bearer ${accessToken}`}:{})},
    body:JSON.stringify({to:token,title:'Dervo',body:kind==='needs'?'Something needs you.':'Something finished.',sound:'default',priority:kind==='needs'?'high':'normal'})});
}

export class RelayRoom implements DurableObject{
  private room:Room;
  constructor(state:DurableObjectState,env:Env){
    const storage:Storage={get:key=>state.storage.get(key),put:(key,value)=>state.storage.put(key,value),delete:async key=>{await state.storage.delete(key);},
      // One alarm per room; an earlier one already set is kept.
      setAlarm:async at=>{const current=await state.storage.getAlarm();if(current===null||current>at)await state.storage.setAlarm(at);}};
    this.room=new Room(storage,(token,kind)=>expoPush(token,kind,env.EXPO_ACCESS_TOKEN));
  }
  /** Purges envelopes held past seven days, for devices that never come back. */
  async alarm(){await this.room.sweep();}
  async fetch(request:Request):Promise<Response>{
    const id=/^\/v1\/rooms\/([A-Za-z0-9_-]{16,64})$/.exec(new URL(request.url).pathname)?.[1];
    if(!id)return new Response('Not found',{status:404});
    const pair=new WebSocketPair();
    const [client,server]=[pair[0],pair[1]];
    server.accept();
    const socket:Socket={send:text=>server.send(text),close:(code,reason)=>server.close(code,reason)};
    this.room.connect(socket,id);
    const timers=[setTimeout(()=>void this.room.expire(socket),LIMITS.authMs),setTimeout(()=>void this.room.expire(socket),LIMITS.strangerMs)];
    server.addEventListener('message',event=>{void this.room.message(socket,typeof event.data==='string'?event.data:'');});
    const end=()=>{for(const timer of timers)clearTimeout(timer);void this.room.close(socket);};
    server.addEventListener('close',end);
    server.addEventListener('error',end);
    return new Response(null,{status:101,webSocket:client});
  }
}
