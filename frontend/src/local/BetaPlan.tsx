import {useEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {local} from './api';
import './SettingsParts.css';
import './BetaPlan.css';
import {formatDate,t,tr} from '@/i18n';

/** Releases: what each beta actually holds and what the next one is meant to hold. Two sources and no third: the
 * hand-written plan file names the versions, the commit each build was made from and the next version's intent; every
 * feature list and every counter is read from Dervo's own checkout with git by the host. Nothing here is typed by hand,
 * so the page can't drift from the code, and a commit shows up under what landed whether or not it was planned. */

type Commit={sha:string;date:string;title:string};
type Planned={title:string;detail:string|null;state:'planned'|'building'|'done';source?:'idea'|'file';conversations?:number|null};
type Shipped={version:string;built:string|null;headline:string|null;commit:string;features:Commit[];total:number};
type Next={version:string;headline:string|null;planned:Planned[];landed:Commit[];total:number;uncommitted:number|null};
export type BetaPlanView={running:{id:string}|null;source:string|null;next:Next;shipped:Shipped[];file:string};

/** Releases is the Dervo team's page during the beta, the same rule full access uses: a signed-in Dervo admin, or a
 * source checkout where no Dervo account service is configured. The host refuses everyone else as well. */
export function releasesAllowed(account?:{state?:string;membership?:{is_admin?:boolean}|null}|null):boolean{
 return account?.state==='unavailable'||account?.membership?.is_admin===true;
}

const STATES:Record<Planned['state'],string>={planned:t('Planned'),building:t('Building'),done:t('Done')};
/** 2026-09-18 as 18 Sep. A date the host didn't record stays out of the line rather than reading as today. */
export function day(value?:string|null):string{
 const parts=/^(\d{4})-(\d{2})-(\d{2})$/.exec(value??'');
 if(!parts)return '';
 const when=new Date(Number(parts[1]),Number(parts[2])-1,Number(parts[3]));
 return Number.isNaN(when.getTime())?'':formatDate(when,{day:'numeric',month:'short'});
}
/** 0.1.0-beta.3 as beta.3, for the places a counter has room for a word and not a version. */
const short=(version:string)=>version.replace(/^\d+\.\d+\.\d+-/,'');

export type RailEntry={version:string;when:string;headline:string;next:boolean;running:boolean};
/** The rail, newest first: the version being planned, then the versions that were built. The beta you're running is the
 * one whose release id the host reports, so a build shows its own dot and a checkout shows none. */
export function releaseRail(view:BetaPlanView):RailEntry[]{
 const running=view.running?.id??'';
 return [{version:view.next.version,when:t('Next, not built yet'),headline:view.next.headline??'',next:true,running:running===view.next.version},
  ...view.shipped.map(entry=>{
   const built=day(entry.built);
   return {version:entry.version,when:[built,running===entry.version?t('running now'):''].filter(Boolean).join(' · ')||t('Built'),
    headline:entry.headline??'',next:false,running:running===entry.version};
  })];
}

export type Counter={key:string;value:string;label:string;warn?:boolean};
/** The four counters, all derived from what the host read and none of them stored. The uncommitted one is the honest
 * one: work that isn't in any release yet. Without Dervo's checkout open, git has nothing to say and they read as a dash. */
export function counters(view:BetaPlanView):Counter[]{
 const {planned,total,uncommitted}=view.next;
 const since=view.shipped[0]?.version;
 return [
  {key:'planned',value:String(planned.length),label:planned.length===1?t('feature planned'):t('features planned')},
  {key:'todo',value:String(planned.filter(item=>item.state==='planned').length),label:t('still to start')},
  {key:'landed',value:view.source?String(total):'–',label:since?t('commits since {version}',{version:short(since)}):t('commits so far')},
  {key:'uncommitted',value:typeof uncommitted==='number'?String(uncommitted):'–',label:t('uncommitted files'),warn:!!uncommitted},
 ];
}

function Commits({rows,trailer}:{rows:Commit[];trailer?:string}){
 return <div className="dervo-rel-commits">
  {rows.map(row=><div key={row.sha} className="dervo-rel-commit"><span className="dervo-rel-sha">{row.sha}</span><span className="dervo-rel-msg" title={row.title}>{row.title}</span><span className="dervo-rel-when">{day(row.date)}</span></div>)}
  {trailer&&<p className="dervo-rel-more">{trailer}</p>}
 </div>;
}

type Props={open:boolean;onClose:()=>void;returnLabel?:string};

export function BetaPlan({open,onClose,returnLabel}:Props){
 const [view,setView]=useState<BetaPlanView|null>(null),[error,setError]=useState(''),[selected,setSelected]=useState('');
 const heading=useRef<HTMLHeadingElement>(null),generation=useRef(0);
 async function load(){
  const token=++generation.current;setError('');
  try{const next=await local<BetaPlanView>('beta_plan');if(generation.current===token)setView(next);}
  catch(failure){if(generation.current===token){setView(null);setError(failure instanceof Error?failure.message:String(failure));}}
 }
 useEffect(()=>{if(!open)return;setView(null);setSelected('');void load();requestAnimationFrame(()=>heading.current?.focus());return()=>{generation.current++;};},[open]);
 if(!open)return null;
 const rail=view?releaseRail(view):[];
 const current=rail.find(entry=>entry.version===selected)??rail[0];
 const shipped=view?.shipped.find(entry=>entry.version===current?.version);
 const next=current?.next?view?.next:undefined;
 const landedLeft=next?Math.max(0,next.total-next.landed.length):0;
 const shippedLeft=shipped?Math.max(0,shipped.total-shipped.features.length):0;
 return <section className="dervo-releases" aria-label={t('Releases')} data-testid="beta-plan" onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();onClose();}}}>
  <header className="dervo-rel-top">
   <div><p className="dervo-rel-kicker">Dervo</p><h2 ref={heading} tabIndex={-1}>{t('Releases')}</h2></div>
   <Button variant="ghost" size="icon" aria-label={returnLabel??t('Close releases')} onClick={onClose}><X/></Button>
  </header>
  <div className="dervo-rel-intro"><p>{tr('What each beta actually holds, read from this checkout with git, plus what the next one is meant to include. {never}, so this page can’t drift from the code.',{never:<b>{t('Shipped features are never typed by hand')}</b>})}</p></div>
  {error&&<div role="alert" className="dervo-rel-error"><p>{error}</p><Button variant="outline" size="sm" onClick={()=>void load()}>{t('Try again')}</Button></div>}
  {!view&&!error&&<p role="status" className="dervo-rel-loading">{t('Reading the beta plan…')}</p>}
  {view&&current&&<div className="dervo-rel-body">
   <nav className="dervo-rel-rail" aria-label={t('Releases')}>
    <p className="dervo-rel-kicker">{t('Releases')} <span>{rail.length}</span></p>
    {rail.map(entry=><button key={entry.version} type="button" aria-current={entry.version===current.version?'true':undefined} onClick={()=>setSelected(entry.version)}>
     <span className="dervo-rel-line"><span className="dervo-rel-version">{entry.version}</span>{entry.running?<span className="dervo-rel-dot is-running" role="img" aria-label={t('The beta you’re running')}/>:entry.next?<span className="dervo-rel-dot is-next" aria-hidden="true"/>:null}</span>
     <span className="dervo-rel-when">{entry.when}</span>
     {entry.headline&&<span className="dervo-rel-sum">{entry.headline}</span>}
    </button>)}
   </nav>
   <article className="dervo-rel-reader" aria-label={t('Release {version}',{version:current.version})}>
    <div className="dervo-rel-head">
     <div>
      <p className="dervo-rel-kicker">{next?t('Next release · not built yet'):`${t('Shipped')}${shipped?.built?` · ${day(shipped.built)}`:''}${current.running?` · ${t('running now')}`:''}`}</p>
      <h3>{current.version}</h3>
      {current.headline&&<p className="dervo-rel-sub">{current.headline}</p>}
     </div>
    </div>

    {next&&<>
     <div className="dervo-rel-stats">{counters(view).map(counter=><div key={counter.key} className="dervo-rel-stat">
      <span className={`dervo-rel-n${counter.warn?' is-warn':''}`}>{counter.value}</span><span className="dervo-rel-l">{counter.label}</span>
     </div>)}</div>

     <section className="dervo-rel-section" aria-labelledby="rel-planned">
      <p className="dervo-rel-kicker" id="rel-planned">{t('Planned for this release')} <span>{next.planned.length}</span></p>
      {next.planned.length?<div className="dervo-rel-rows">{next.planned.map(item=><div key={item.title} className="dervo-rel-row">
       <span className={`dervo-chip dervo-rel-chip is-${item.state}`}>{STATES[item.state]}</span>
       <div className="dervo-rel-text"><p className="dervo-rel-t">{item.title}</p>{item.detail&&<p className="dervo-rel-d">{item.detail}</p>}
        {item.source==='idea'&&<p className="dervo-rel-m">{t('An idea in the project room')}{typeof item.conversations==='number'?` · ${t('{count} conversations',{count:item.conversations})}`:''}</p>}</div>
      </div>)}</div>:<p className="dervo-rel-empty">{t('Nothing is planned for {version} yet. Aim an idea at it from the project room.',{version:current.version})}</p>}
     </section>

     <section className="dervo-rel-section" aria-labelledby="rel-landed">
      <p className="dervo-rel-kicker" id="rel-landed">{t('Landed since {version}',{version:view.shipped[0]?.version??t('the first commit')})} <span>{view.source?next.total:0}</span></p>
      {next.landed.length?<Commits rows={next.landed} trailer={[landedLeft?t('{landedLeft} more',{landedLeft}):'',next.uncommitted?t('{count} uncommitted files that aren’t in any release yet',{count:next.uncommitted}):''].filter(Boolean).join(', ')||undefined}/>
       :<p className="dervo-rel-empty">{view.source?t('Nothing has landed since the last beta was cut.'):t('Open Dervo’s own project in this workspace to read the commits with git.')}</p>}
     </section>
    </>}

    {shipped&&<section className="dervo-rel-section" aria-labelledby="rel-shipped">
     <p className="dervo-rel-kicker" id="rel-shipped">{t('In this release')} <span>{shipped.total}</span></p>
     <p className="dervo-rel-built">{t('Built from')} <span className="dervo-rel-sha">{shipped.commit}</span></p>
     {shipped.features.length?<Commits rows={shipped.features} trailer={shippedLeft?t('{shippedLeft} more',{shippedLeft}):undefined}/>
      :<p className="dervo-rel-empty">{view.source?t('No commits were recorded for this version.'):t('Open Dervo’s own project in this workspace to read the commits with git.')}</p>}
    </section>}

    <p className="dervo-rel-foot">{view.source?t('Read with git from {source}. A commit appears here the moment it lands, whether or not it was planned.',{source:view.source}):t('Dervo’s own project isn’t open in this workspace, so only the plan file can be shown.')} {t('Each release build adds itself to {file} (preview builds don’t), and headlines are written there by hand. What the next one holds comes from the ideas aimed at it in the project room.',{file:view.file})}</p>
   </article>
  </div>}
 </section>;
}
