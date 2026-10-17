// The phone half of src/phone's end-to-end test (link_e2e in mod.rs): the real PhoneLink from the companion (PR #11),
// against the Rust Mac link through the dev relay. Prints one JSON line per step; the Rust test checks them and drives
// the Mac side. DERVO_COMPANION is a companion folder with its node_modules installed.
const companion=process.env.DERVO_COMPANION!;
const {default:WebSocket}=await import(`${companion}/node_modules/ws/wrapper.mjs`);
const {readOffer}=await import(`${companion}/protocol/pairing.ts`);
const {PhoneLink,stateFromOffer}=await import(`${companion}/protocol/phone.ts`);
const {prove,seal}=await import(`${companion}/protocol/crypto.ts`);
type SocketLike=any;

const out=(event:string,value:unknown={})=>console.log(JSON.stringify({event,...(value as object)}));
const connect=(url:string)=>new WebSocket(url) as unknown as SocketLike;
async function until<T>(what:string,check:()=>T|undefined|null|false,ms=15000):Promise<T>{
  const end=Date.now()+ms;
  for(;;){const value=check();if(value)return value;if(Date.now()>end)throw new Error('Timed out waiting for '+what);await new Promise(r=>setTimeout(r,25));}
}

const offer=readOffer(process.argv[2]);
if(!offer){out('error',{detail:'the offer link did not read'});process.exit(1);}
const phone=new PhoneLink(stateFromOffer(offer),{connect,save:()=>{},device:'Rust test iPhone'});
phone.start();
out('digits',{digits:phone.digits});
try{
  await until('paired',()=>phone.state.paired,20000);
  const home=await until('home',()=>(phone.state.home?.rows.length??0)>=2&&phone.state.home);
  out('home',{mac:home.mac,names:home.rows.map((r:any)=>r.name).sort(),project:home.projects[0]?.id});
  const projectId=home.projects[0].id;
  const first=home.rows.find((r:any)=>r.name==='Export fix')!;
  phone.open(projectId,first.id);
  const thread=await until('thread',()=>(phone.live.thread?.messages.length??0)>0&&phone.live.thread);
  out('thread',{messages:thread.messages.map((m:any)=>[m.from,m.text])});
  const sent=phone.act({kind:'send',projectId,channelId:first.id,text:'Run the tests please'});
  const ack=await until('send ack',()=>phone.state.outbox.find((p:any)=>p.id===sent.id&&p.state!=='waiting'));
  out('send',{state:ack.state,detail:ack.detail});
  phone.open(projectId,'home');
  await until('home chat',()=>phone.live.thread?.channelId==='home'&&phone.live.thread);
  const toHome=phone.act({kind:'home',projectId,channelId:'home',text:'Start a thread to fix the export'});
  const homeAck=await until('home ack',()=>phone.state.outbox.find((p:any)=>p.id===toHome.id&&p.state!=='waiting'));
  let mine=false;
  if(homeAck.state==='done')mine=!!await until('your message in Home',()=>phone.live.thread?.messages.find((m:any)=>m.from==='you'&&m.text==='Start a thread to fix the export'));
  out('home-send',{state:homeAck.state,detail:homeAck.detail,shown:mine});
  phone.close();
  const stop=phone.act({kind:'stop',projectId,channelId:first.id});
  const stopAck=await until('stop ack',()=>phone.state.outbox.find((p:any)=>p.id===stop.id&&p.state!=='waiting'));
  out('stop',{state:stopAck.state,detail:stopAck.detail});
  // A replayed envelope, and the same action id in a new envelope: the Mac runs it once and answers both.
  const action={t:'action',id:'replay-1',at:Date.now(),action:{kind:'stop',projectId,channelId:first.id}};
  const env=seal(action,phone.state.keys,phone.state.offer.mac,{keep:'q'});
  const raw=new WebSocket(`${phone.state.offer.relay}/v1/rooms/${phone.state.offer.room}`);
  const challenge=await new Promise<any>(r=>raw.once('message',(data:unknown)=>r(JSON.parse(String(data)).challenge)));
  raw.send(JSON.stringify({t:'auth',role:'phone',pk:phone.state.keys.publicKey,key:phone.state.offer.key,proof:prove(challenge,phone.state.keys)}));
  await new Promise(r=>raw.once('message',r));
  raw.send(JSON.stringify({t:'send',env}));
  raw.send(JSON.stringify({t:'send',env}));
  raw.send(JSON.stringify({t:'send',env:seal(action,phone.state.keys,phone.state.offer.mac,{keep:'q'})}));
  await new Promise(r=>setTimeout(r,1500));
  raw.close();
  out('replayed');
  // The Mac removes this phone: a sealed message, so the phone believes it.
  await until('unpaired',()=>!phone.state.paired,20000);
  out('unpaired');
}catch(error){out('error',{detail:String(error)});}
phone.stop();
process.exit(0);
