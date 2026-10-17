import {animate} from 'motion';

/** The send moment (#30, owner: "it doesn't just appear"). On Enter your words travel from the composer into their place
 * in the thread as one motion. FLIP: the composer's text box is measured before the editor clears (armed on the Enter
 * keydown or the send click, before the box empties), the landed bubble is measured once React has committed the
 * row, and the bubble animates from the first box to its own with transform only. The composer clears at once and its
 * box shrinks back to one line (the one height animated here). If the list scrolls to make room, the scroll is shown as a
 * glide: the list's content is offset by the jump and eased back to zero, transform only, so the thread never jumps.
 * Nothing here changes what is sent or where it lands; a missed measurement just means the row appears in place. */
export type Box={left:number;top:number;width:number;height:number};
// Beta.18 (Bryce, 6 Oct: the send "doesn't really do it for me"): about a quarter shorter, so the bubble has landed
// before the eye goes looking for it. The reply row waits for it in ChatMotion.css (dv-reply-in).
export const TRAVEL_MS=260;
export const SHRINK_MS=200;
export const GLIDE_MS=240;
const EASE:[number,number,number,number]=[.2,.7,.2,1];
/** A jump larger than this is a different view, not a new row; it isn't glided. */
export const GLIDE_MAX_PX=800;
/** An armed measurement older than this belongs to another send. */
export const ARM_TTL_MS=1500;

/** Where the bubble starts, relative to where it landed: the composer text's top-left over the bubble's. Null when there
 * is nothing to travel from or to (a box off screen or of no size) or when motion is reduced. */
export function sendMotionPlan(from:Box|null|undefined,to:Box|null|undefined,reduced:boolean):{dx:number;dy:number}|null{
  if(reduced||!from||!to)return null;
  if(from.width<=0||from.height<=0||to.width<=0||to.height<=0)return null;
  if(to.top+to.height<0||to.top>1e5)return null;
  const dx=from.left-to.left,dy=from.top-to.top;
  if(Math.abs(dx)<1&&Math.abs(dy)<1)return null;
  return {dx,dy};
}
/** How far the list moved to make room, if that move should be shown as a glide: a scroll of a row or two, either way
 * (the list pins its floor as the row lands and the composer shrinks, so the move can go up as well as down), while the
 * reader was already at the bottom. Zero means no glide (no move, a far jump, or a reader who was reading older messages
 * and mustn't be pulled). The sign is the offset the content starts from before easing to zero. */
export function glideOffset(before:number,after:number,atBottom:boolean):number{
  const delta=after-before;
  if(!atBottom||!Number.isFinite(delta)||delta===0||Math.abs(delta)>GLIDE_MAX_PX)return 0;
  return delta;
}
export function nearBottom(scrollTop:number,scrollHeight:number,clientHeight:number,threshold=32):boolean{
  return scrollHeight-scrollTop-clientHeight<=threshold;
}

/** The row you sent keeps one identity from the moment it appears to the moment the host's own copy replaces it, so the
 * travel plays on one element: the sending row carries ``sent:<id>`` as its render key, the saved copy with the same
 * words takes over that key, and the sending row steps aside as soon as the copy is there (before, the two could show
 * together for a render). ``keys`` maps the words of recent sends to their key; entries older than a minute are ignored. */
export type SentKey={key:string;at:number};
export const SENT_KEY_TTL_MS=60_000;
export function sentRowKeys<T extends {id:string;pubkey?:string;body:string;renderKey?:string}>(messages:T[],keys:Map<string,SentKey>,now=Date.now()):T[]{
  if(!keys.size)return messages;
  const live=(body:string)=>{const entry=keys.get(body);return entry&&now-entry.at<=SENT_KEY_TTL_MS?entry.key:undefined;};
  // The saved copy is the last own row with those words that isn't the sending row itself.
  const saved=new Map<string,number>();
  messages.forEach((message,index)=>{if(message.pubkey==='local-user'&&!message.id.startsWith('sending:')&&live(message.body))saved.set(message.body,index);});
  const out:T[]=[];
  messages.forEach((message,index)=>{
    if(message.pubkey!=='local-user'){out.push(message);return;}
    const key=live(message.body);
    if(!key){out.push(message);return;}
    if(message.id.startsWith('sending:')){if(!saved.has(message.body))out.push({...message,renderKey:key});return;}
    out.push(saved.get(message.body)===index?{...message,renderKey:key}:message);
  });
  return out;
}
/** The pictures a sending row carried, by the saved row that took its place (sentRowKeys): the saved row can arrive a
 * poll before its task does, and the pictures would blink out until then (send pipeline, 9 Oct). */
export function handedOverImages(rows:{id:string;renderKey?:string}[],sending:{id:string;images?:string[]}[]):Map<string,string[]>{
  const out=new Map<string,string[]>();
  for(const row of rows){
    if(!row.renderKey||row.id.startsWith('sending:'))continue;
    const from=sending.find(item=>`sent:${item.id}`===row.renderKey);
    if(from?.images?.length)out.set(row.id,from.images);
  }
  return out;
}
export function rememberSent(keys:Map<string,SentKey>,body:string,key:string,now=Date.now()){
  for(const [text,entry] of keys)if(now-entry.at>SENT_KEY_TTL_MS)keys.delete(text);
  keys.set(body,{key,at:now});
}

type Armed={text:Box;wrapHeight:number;scrollTop:number;atBottom:boolean;at:number};
const box=(rect:DOMRect):Box=>({left:rect.left,top:rect.top,width:rect.width,height:rect.height});
const reduced=()=>typeof window!=='undefined'&&!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
export const SCROLLER='[data-buzz-conversation-scroll]';
let armed:Armed|null=null;
let glideWatch:(()=>void)|null=null;

/** A conversation switch cancels any measurement waiting for the previous conversation's row. */
export function cancelSendMotion(){armed=null;glideWatch?.();}

/** Measure the composer before the editor clears. Called on the Enter keydown and the send click, in the capture phase. */
export function armSendMotion(composer:HTMLElement|null,scroller:HTMLElement|null=document.querySelector(SCROLLER)){
  const text=composer?.querySelector<HTMLElement>('[data-testid="message-input"]');
  if(!text||reduced()){armed=null;return;}
  const wrap=composer?.querySelector<HTMLElement>('[data-testid="message-input-scroll"]');
  armed={text:box(text.getBoundingClientRect()),wrapHeight:wrap?.getBoundingClientRect().height??0,scrollTop:scroller?.scrollTop??0,
    atBottom:scroller?nearBottom(scroller.scrollTop,scroller.scrollHeight,scroller.clientHeight):true,at:performance.now()};
}
/** After the sent row has committed: the bubble travels, the composer box shrinks, and any scroll the list makes to show
 * the row is glided. ``row`` is the sent row, or null to use the newest own row. The list renders its rows a beat after
 * React commits, so this looks again on the next frames before giving up. Returns whether a travel started. */
export function playSendMotion(row:HTMLElement|string|null,composer:HTMLElement|null,scroller:HTMLElement|null=document.querySelector(SCROLLER),retries=6):boolean{
  const plan=armed&&performance.now()-armed.at<ARM_TTL_MS?armed:null;
  armed=null;
  if(!plan||reduced())return false;
  const target=typeof row==='string'
    ?Array.from(document.querySelectorAll<HTMLElement>('[data-testid="message-row"]')).find(node=>node.dataset.messageId===row)
    :row??newestOwnRow();
  const body=target?.querySelector<HTMLElement>('[data-testid="message-body"]');
  const move=body?sendMotionPlan(plan.text,box(body.getBoundingClientRect()),false):null;
  if(body&&move){
    target!.setAttribute('data-dv-sent','');
    void animate(body,{transform:[`translate(${move.dx}px, ${move.dy}px)`,'translate(0px, 0px)']},{duration:TRAVEL_MS/1000,ease:EASE})
      .finished.then(()=>{body.style.transform='';target!.removeAttribute('data-dv-sent');},()=>{body.style.transform='';});
  }else if(!target){
    // Not rendered yet: keep the measurement for the next few frames, then let it go.
    if(retries>0){armed=plan;requestAnimationFrame(()=>{if(armed===plan)playSendMotion(row,composer,scroller,retries-1);});}
    return false;
  }
  const wrap=composer?.querySelector<HTMLElement>('[data-testid="message-input-scroll"]');
  if(wrap&&plan.wrapHeight>0&&typeof wrap.animate==='function'){
    const now=wrap.getBoundingClientRect().height;
    // The one height here: the composer box back to one line. Web Animations, so it runs with the travel, off the main thread where the engine allows.
    if(plan.wrapHeight-now>1)wrap.animate([{height:`${plan.wrapHeight}px`},{height:`${now}px`}],{duration:SHRINK_MS,easing:`cubic-bezier(${EASE.join(',')})`});
  }
  if(scroller)watchGlide(scroller,plan);
  return !!move;
}
function newestOwnRow():HTMLElement|null{
  const rows=document.querySelectorAll<HTMLElement>('[data-testid="message-row"][data-pubkey="local-user"]');
  return rows.length?rows[rows.length-1]:null;
}
/** The list's next scroll within the send moment is shown as a glide instead of a jump. */
function watchGlide(scroller:HTMLElement,plan:Armed){
  glideWatch?.();
  const inner=scroller.firstElementChild as HTMLElement|null;
  if(!inner)return;
  let live=true;
  const stop=()=>{if(!live)return;live=false;scroller.removeEventListener('scroll',onScroll);clearTimeout(timer);glideWatch=null;};
  const onScroll=()=>{
    const offset=glideOffset(plan.scrollTop,scroller.scrollTop,plan.atBottom);
    stop();
    if(!offset)return;
    void animate(inner,{transform:[`translateY(${offset}px)`,'translateY(0px)']},{duration:GLIDE_MS/1000,ease:EASE}).finished.then(()=>{inner.style.transform='';},()=>{inner.style.transform='';});
  };
  const timer=setTimeout(stop,600);
  scroller.addEventListener('scroll',onScroll);
  glideWatch=stop;
}
/** Test seam: what is armed right now. */
export function armedSendMotion():Readonly<Armed>|null{return armed;}
