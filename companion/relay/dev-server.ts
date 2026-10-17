import {WebSocketServer} from 'ws';
import {MemoryStorage,Room,LIMITS,type Pusher,type Socket} from './room.ts';

/** The relay on this machine, for development and the end-to-end test: the same Room class as the Worker, rooms in
 * memory, no push. ``node --experimental-strip-types relay/dev-server.ts [port]``. Loopback only. */
export function startRelay(port=0,pusher:Pusher|null=null):Promise<{url:string;close:()=>Promise<void>;rooms:Map<string,Room>}>{
  const rooms=new Map<string,Room>();
  const server=new WebSocketServer({host:'127.0.0.1',port,maxPayload:700*1024});
  server.on('connection',(ws,request)=>{
    const match=/^\/v1\/rooms\/([A-Za-z0-9_-]{16,64})$/.exec(request.url??'');
    if(!match){ws.close(1008,'not_found');return;}
    let room=rooms.get(match[1]);
    if(!room){room=new Room(new MemoryStorage(),pusher);rooms.set(match[1],room);}
    const socket:Socket={send:text=>ws.send(text),close:(code,reason)=>ws.close(code,reason)};
    room.connect(socket,match[1]);
    const timers=[setTimeout(()=>void room!.expire(socket),LIMITS.authMs),setTimeout(()=>void room!.expire(socket),LIMITS.strangerMs)];
    ws.on('message',data=>void room!.message(socket,data.toString()));
    ws.on('close',()=>{for(const timer of timers)clearTimeout(timer);void room!.close(socket);});
  });
  return new Promise(resolve=>server.on('listening',()=>{
    const address=server.address();
    const actual=typeof address==='object'&&address?address.port:port;
    resolve({url:`ws://127.0.0.1:${actual}`,rooms,close:()=>new Promise(done=>{for(const c of server.clients)c.terminate();server.close(()=>done());})});
  }));
}

if(import.meta.url===`file://${process.argv[1]}`){
  const relay=await startRelay(Number(process.argv[2]??8787));
  console.log(`Dervo dev relay on ${relay.url}`);
}
