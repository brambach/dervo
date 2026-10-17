import type {CSSProperties} from 'react';
import './RunningOrb.css';

/** The Running orb (app #14). Bryce picked B on 25 Sep 2026, the halftone sunrise in colour
 * (evidence/design-tests/b16-orb-3/). A sunrise drawn as light in a dot screen: each dot's size follows how bright the
 * scene is at that point. By day the dots are ink on paper, so they shrink where it's bright and the sun is a clearing
 * with a soft wheat disc behind it. At night the dots are light, so the sun is a gold cluster rising over dark hills.
 * One sunrise every 8 seconds. It sits beside the word that says Running, so it never carries the state alone. Only
 * the summary strip mounts it (design, 25 Sep): the Progress panel keeps the working smile.
 *
 * b161/window: the orb used to animate about 190 SVG attributes with SMIL. WebKit re-lays out the page for each SMIL
 * change, so a running thread cost about 9,700 layouts a second and a quarter of a core while nothing else moved
 * (evidence/b161-window/NOTES.md). Now each theme's sunrise is drawn once as a film strip of FRAMES stills in a static
 * SVG image, and one CSS steps() animation slides it. The page repaints a 16px box FRAMES/CYCLE times a second and
 * never lays out. Reduced motion stops the animation in CSS, on frame 0, which is the still pose. */

const CYCLE=8,FRAMES=64,POSE=2.6,GRID=11,SUN_R=2.7,RSCALE=0.6,SIZE=16;
const PALETTE={lt:{hill:'#5e7351',sky:'#8f9a8e',glow:'#c2a560'},dk:{hill:'#adc19b',sky:'#9aa596',glow:'#dcc58c'}} as const;
type Theme=keyof typeof PALETTE;
const f2=(n:number)=>String(Math.round(n*100)/100);
const rise=(t:number)=>{const u=(((t%CYCLE)+CYCLE)%CYCLE)/CYCLE;return 0.5-0.5*Math.cos(u*2*Math.PI);};
const horizon=(x:number)=>10.5+0.85*Math.sin((x+1.2)/2.6);
const sunY=(h:number)=>14.4-8.4*h;
/** How bright the scene is at a point: dark hills lit along the ridge, a mid-grey sky, and a pure-light sun with a halo. */
function light(x:number,y:number,t:number){
 const h=rise(t),d=Math.hypot(x-8.8,y-sunY(h));
 if(d<SUN_R)return 1;
 if(y>horizon(x))return 0.04+0.3*h*Math.max(0,1-(y-horizon(x))/1.1);
 const sky=0.48+0.12*(y/16)+0.08*h;
 return Math.min(1,sky+0.42*Math.exp(-(((d-SUN_R)/1.5)**2)));
}
function zone(x:number,y:number,t:number):'hill'|'glow'|'sky'{
 const d=Math.hypot(x-8.8,y-sunY(rise(t)));
 return y>horizon(x)?'hill':d<SUN_R+1.6?'glow':'sky';
}
const HORIZON_CLIP=`M0 0H16${Array.from({length:17},(_,k)=>`L${16-k} ${f2(horizon(16-k))}`).join('')}Z`;

/** One theme's sunrise as a static SVG: FRAMES stills side by side, frame k at k/FRAMES of a cycle after the pose. */
export function orbStrip(theme:Theme){
 const pitch=16/GRID,rmax=pitch*RSCALE,frames:string[]=[];
 for(let k=0;k<FRAMES;k++){
  const t=POSE+(k/FRAMES)*CYCLE;
  let body=theme==='lt'?`<circle clip-path="url(#h)" fill="#dcc690" fill-opacity=".85" cx="8.8" cy="${f2(sunY(rise(t)))}" r="${f2(SUN_R-0.2)}"/>`:'';
  for(let i=0;i<GRID;i++)for(let j=0;j<GRID;j++){
   const x=pitch*(i+0.5),y=pitch*(j+0.5);
   if(Math.hypot(x-8,y-8)>7.4)continue;
   const l=light(x,y,t),r=theme==='lt'?rmax*Math.sqrt(1-l):rmax*l**1.5;
   if(r<0.01)continue;
   body+=`<circle cx="${f2(x)}" cy="${f2(y)}" r="${f2(r)}" fill="${PALETTE[theme][zone(x,y,t)]}"/>`;
  }
  frames.push(`<g transform="translate(${k*SIZE} 0)">${body}</g>`);
 }
 return `<svg xmlns="http://www.w3.org/2000/svg" width="${FRAMES*SIZE}" height="${SIZE}" viewBox="0 0 ${FRAMES*SIZE} ${SIZE}"><defs><clipPath id="h"><path d="${HORIZON_CLIP}"/></clipPath></defs>${frames.join('')}</svg>`;
}

/** Both strips, drawn on first use and shared by every orb as blob URLs so the page holds one copy of each. */
let strips:{lt:string;dk:string}|null=null;
function stripUrls(){
 if(strips)return strips;
 const url=(theme:Theme)=>{
  const svg=orbStrip(theme);
  return typeof URL.createObjectURL==='function'&&typeof Blob!=='undefined'?URL.createObjectURL(new Blob([svg],{type:'image/svg+xml'})):`data:image/svg+xml,${encodeURIComponent(svg)}`;
 };
 strips={lt:url('lt'),dk:url('dk')};
 return strips;
}

export function RunningOrb({label}:{label?:string}){
 const {lt,dk}=stripUrls();
 const style={'--dv-orb-lt':`url("${lt}")`,'--dv-orb-dk':`url("${dk}")`,'--dv-orb-frames':FRAMES,'--dv-orb-cycle':`${CYCLE}s`} as CSSProperties;
 return <span className="dv-orb" data-testid="running-orb" style={style} role={label?'img':undefined} aria-label={label} aria-hidden={label?undefined:true}/>;
}
