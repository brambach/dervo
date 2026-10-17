import type {Bucket,HomeRow,HomeSnapshot} from '../../../companion/protocol/messages.ts';
import type {Live} from '../../../companion/protocol/phone.ts';
import {ago} from './time';

export const BUCKETS:{key:Bucket;label:string;empty:string|null;open:boolean}[]=[
  {key:'needs',label:'Waiting on you',empty:'Nothing needs you.',open:true},
  {key:'working',label:'Working',empty:'Nothing running.',open:true},
  {key:'unknown',label:'Lost sight of',empty:null,open:true},
  {key:'finished',label:'Finished since you looked',empty:null,open:true},
  {key:'idle',label:'Idle',empty:null,open:false},
];

export function grouped(rows:HomeRow[]){
  const groups=new Map<Bucket,HomeRow[]>(BUCKETS.map(bucket=>[bucket.key,[]]));
  for(const row of rows)groups.get(row.bucket)!.push(row);
  return groups;
}

const plural=(n:number,one:string,many:string)=>`${n} ${n===1?one:many}`;

/** One true sentence from the records, like the line under Home's greeting on the Mac. */
export function sentence(home:HomeSnapshot):string{
  const count=(bucket:Bucket)=>home.rows.filter(row=>row.bucket===bucket).length;
  const needs=count('needs'),working=count('working'),finished=count('finished'),lost=count('unknown');
  const parts:string[]=[];
  if(needs)parts.push(`${plural(needs,'thread needs','threads need')} you`);
  if(working)parts.push(`${needs?working:plural(working,'thread','threads')} ${working===1?'is':'are'} working`);
  if(finished)parts.push(`${finished} finished since you looked`);
  if(lost)parts.push(`Dervo lost sight of ${lost}`);
  if(!parts.length)return home.rows.length?'Nothing is moving right now.':'No threads yet. Start one on your Mac.';
  const text=parts.length===1?parts[0]:`${parts.slice(0,-1).join(', ')} and ${parts.at(-1)}`;
  return text[0].toUpperCase()+text.slice(1)+'.';
}

export type Freshness={tone:'live'|'away'|'lost';text:string};

/** What the phone can honestly say about how current Home is. Never "live" unless the Mac is connected right now. */
/** The Mac sends Home at least every 30 s while it's connected. Older than this means its reads stopped. */
export const STALE_HOME_MS=90_000;

/** ``got`` is when this phone received ``home``, on its own clock; the Mac's ``home.at`` is never compared with it. */
export function freshness(home:HomeSnapshot|null,live:Live|null,now=Date.now(),got:number|null=null):Freshness{
  const asOf=got??home?.at??0;
  const mac=home?.mac??'your Mac';
  if(live?.error==='not_allowed')return {tone:'lost',text:`${mac} doesn't list this phone any more. If you removed it there, pair it again.`};
  if(!live?.connected)return {tone:'lost',text:home?`Can't reach the relay. Showing Home as of ${ago(asOf,now)}.`:'Connecting…'};
  if(!live.macOnline){
    // The phone can't tell asleep from offline or shut down, so it says both. What it can say is what the Mac last
    // reported: whether it was keeping itself awake for a run.
    const seen=live.macSeen?ago(live.macSeen,now):'a while ago';
    const running=!!home?.rows.some(row=>row.bucket==='working');
    const hint=!running?'':home?.awake?.enabled===false?' Runs pause while it sleeps. To keep them going, turn on "Keep this Mac awake while agents work" in Dervo\'s settings there.'
      :home?.awake?.holding?' It was keeping itself awake for a run, so the lid may be closed or it lost its connection.':'';
    return {tone:'away',text:`${mac} is asleep or offline. Last seen ${seen}. Messages and answers you send wait for it.${hint}`};
  }
  if(home&&now-asOf>STALE_HOME_MS)return {tone:'lost',text:`${mac} is connected but Home hasn't updated since ${ago(asOf,now)}.`};
  return {tone:'live',text:home?`Live from ${mac}`:`Loading Home from ${mac}…`};
}
