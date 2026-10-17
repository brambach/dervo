import type {Conversation} from './api';

/** Subthreads by parent (beta.17, #27; Bryce, 26 Sep: "just like Discord, where there are subthreads"). The host sets
 * ``parentId`` only when the parent is listed too, so a subthread whose parent is archived, deleted or out of sight
 * already sits at the top level. In the order the host lists them, which is the order they were started. */
export function subthreads(conversations:readonly Conversation[]):Map<string,Conversation[]>{
  const ids=new Set(conversations.map(c=>c.id)),kids=new Map<string,Conversation[]>();
  for(const c of conversations)if(c.parentId&&c.parentId!==c.id&&ids.has(c.parentId))kids.set(c.parentId,[...(kids.get(c.parentId)??[]),c]);
  return kids;
}

export type Gathered<T>={kind:'item';item:T;kids:T[]}|{kind:'parent';parentId:string;kids:T[]};
/** One Catch up section's rows with subthreads gathered under their parent: the parent's own row when it has one in
 * this section, else a row for the parent holding only its subthreads, where the first of them stood. ``alone`` keeps
 * an item in its own place (the lifted next step). */
export function gather<T extends {channelId:string}>(items:readonly T[],conversations:readonly Conversation[],alone:(item:T)=>boolean=()=>false):Gathered<T>[]{
  const parentOf=new Map<string,string>();
  for(const [parent,kids] of subthreads(conversations))for(const kid of kids)parentOf.set(kid.id,parent);
  const here=new Set(items.filter(item=>!alone(item)).map(item=>item.channelId));
  const out:Gathered<T>[]=[],holders=new Map<string,Gathered<T>>();
  for(const item of items){
    const parent=parentOf.get(item.channelId);
    if(!parent||alone(item)){
      const entry:Gathered<T>={kind:'item',item,kids:[]};
      out.push(entry);
      if(!alone(item))holders.set(item.channelId,entry);
      continue;
    }
    let holder=holders.get(parent);
    if(!holder&&!here.has(parent)){holder={kind:'parent',parentId:parent,kids:[]};holders.set(parent,holder);out.push(holder);}
    if(holder)holder.kids.push(item);
    else{
      // The parent's own row comes later in this section: hold the subthread until it does.
      const later:Gathered<T>={kind:'parent',parentId:parent,kids:[item]};
      holders.set(parent,later);out.push(later);
    }
  }
  // A parent whose own row came after its subthreads: fold that early holder into the parent's row.
  return out.flatMap(entry=>{
    if(entry.kind==='parent'&&here.has(entry.parentId)){
      const own=out.find(other=>other.kind==='item'&&other.item.channelId===entry.parentId);
      if(own){own.kids.unshift(...entry.kids);return [];}
    }
    return [entry];
  });
}
