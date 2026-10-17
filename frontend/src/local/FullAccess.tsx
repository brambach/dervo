import {useEffect,useState} from 'react';
import {TerminalSquare} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {local} from './api';
import {t} from '@/i18n';

export type FullAccessView={provider?:string;computer_available?:boolean;computer_access?:boolean;
 /** Agents run commands themselves (beta.15): on for whoever has full access until it's turned off in Access. */
 commands?:boolean;auto_commit?:boolean;enabled:boolean;active:boolean;available:boolean;unavailable:string|null;detail:string;
 /** The warning's token, which turning it on sends back, and whether an older record waits for that confirm (security N4). */
 warning?:string;reconfirm?:boolean;
 /** Your MCP servers and claude.ai connectors as last listed (null while still listing), and the ones this project
  * allows: null is every one, a list is exactly those. */
 connectors?:{names:string[]|null;allowed:string[]|null}|null;
 /** Who turned it on and when (seconds), from the host record. */
 actor?:string|null;created_at?:number|null};

/** "claude.ai Wispr Flow" reads as "Wispr Flow": every connector here comes from your account, so the prefix says nothing. */
export const connectorLabel=(name:string)=>name.replace(/^claude\.ai\s+/,'');

/** The list to save when one connector is ticked or unticked. Ticking the last one back on goes back to "every
 * connector", so one you add later is allowed too until you pick again. An allowed connector missing from today's list
 * (disconnected for now) stays allowed. */
export function toggledConnectors(names:string[],allowed:string[]|null,name:string):string[]|null{
 const current=allowed??names;
 const next=current.includes(name)?current.filter(value=>value!==name):[...current,name];
 return names.every(value=>next.includes(value))?null:next;
}

/** Which connectors Claude Code may use in this project. Each tick saves at once; unticked ones are hidden from it. */
export function ConnectorPicker({projectId,channelId,connectors,onChanged}:{projectId:string;channelId:string;connectors:NonNullable<FullAccessView['connectors']>;onChanged:()=>Promise<unknown>}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const names=connectors.names;
 // Dervo lists them in the background (about 9 seconds the first time); check back until the list is there.
 useEffect(()=>{if(names!==null)return;const timer=setTimeout(()=>void onChanged(),3000);return ()=>clearTimeout(timer);},[names,onChanged]);
 async function toggle(name:string){
  if(!names)return;
  setBusy(true);setError('');
  try{await local('coding_connectors_set',{projectId,channelId,connectors:toggledConnectors(names,connectors.allowed,name)});await onChanged();}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setBusy(false);}
 }
 const on=(name:string)=>connectors.allowed===null||connectors.allowed.includes(name);
 return <div className="space-y-2 rounded-xl border p-3" role="group" aria-label={t('Connectors Claude Code can use here')} data-testid="connector-picker">
  <div className="flex items-baseline justify-between gap-3">
   <h4 className="text-sm font-semibold">{t('Connectors Claude Code can use here')}</h4>
   {names&&names.length>0&&<span className="text-xs text-muted-foreground">{t('{at} of {all}',{at:names.filter(on).length,all:names.length})}</span>}
  </div>
  {names===null&&<p role="status" className="text-xs text-muted-foreground">{t('Looking for your connectors. This takes a few seconds the first time.')}</p>}
  {names&&!names.length&&<p className="text-xs text-muted-foreground">{t('No MCP servers or claude.ai connectors found for this Claude account.')}</p>}
  {names&&names.length>0&&<div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
   {names.map(name=><label key={name} className={`flex items-center gap-2 text-sm ${on(name)?'':'text-muted-foreground'}`}>
    <input type="checkbox" className="size-3.5 accent-[hsl(var(--primary))]" checked={on(name)} disabled={busy} onChange={()=>void toggle(name)}/>
    <span className="min-w-0 truncate">{connectorLabel(name)}</span>
   </label>)}
  </div>}
  <p className="text-xs leading-relaxed text-muted-foreground">{t('From your Claude account’s connectors and MCP servers. Unticked ones are hidden from Claude Code in this project.')}{connectors.allowed!==null?` ${t('A connector you add later stays off here until you tick it.')}`:''}</p>
  {error&&<p role="alert" className="text-xs text-destructive">{error}</p>}
 </div>;
}

/** Full access (Bryce, 17 Sep 2026): Claude Code's own shell and file tools in this project, so a task can build, run
 * previews and check its own work. Off by default, a second click to turn on, and the warning is always on screen. */
export function FullAccessSection({projectId,channelId,view,onChanged}:{projectId:string;channelId:string;view?:FullAccessView;onChanged:()=>Promise<unknown>}){
 const [confirming,setConfirming]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 if(!view)return null;
 const provider=view.provider==='codex'?'Codex':'Claude Code';
 async function set(enabled:boolean){
  setBusy(true);setError('');
  try{await local('coding_full_access_set',{projectId,channelId,enabled,...(enabled?{warning:view?.warning}:{})});setConfirming(false);await onChanged();}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setBusy(false);}
 }
 return <section className="space-y-3 border-t pt-5" aria-label={t('Full access')} data-testid="full-access">
  <div className="flex items-start justify-between gap-4">
   <div className="min-w-0">
    <h3 className="flex items-center gap-2 font-semibold"><TerminalSquare className="size-4" aria-hidden="true"/>{t('Full access')}</h3>
    <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t('Let {provider} run commands in this project: build, test and start a preview to check its own work.',{provider})}</p>
   </div>
   <button type="button" role="switch" aria-checked={view.enabled} aria-label={t('Let {provider} run commands in this project',{provider})} disabled={busy||(!view.enabled&&!view.available)}
    onClick={()=>view.enabled?void set(false):setConfirming(true)}
    className={`relative mt-1 h-5 w-9 shrink-0 rounded-full transition-colors motion-reduce:transition-none disabled:opacity-50 ${view.enabled?'bg-primary':'bg-muted-foreground/35'}`}>
    <span className={`absolute left-0.5 top-0.5 size-4 rounded-full bg-background shadow transition-transform motion-reduce:transition-none ${view.enabled?'translate-x-4':''}`}/>
   </button>
  </div>
  <p role="note" className="rounded-xl border border-destructive/25 bg-destructive/5 p-3 text-xs leading-relaxed" data-testid="full-access-warning">{view.detail}</p>
  {view.enabled&&view.active&&view.connectors&&<ConnectorPicker projectId={projectId} channelId={channelId} connectors={view.connectors} onChanged={onChanged}/>}
  {view.enabled&&!view.active&&view.unavailable&&<p role="status" className="text-xs text-muted-foreground">{t('On, but not in use right now: {why}',{why:view.unavailable})}</p>}
  {!view.enabled&&!view.available&&view.unavailable&&<p role="status" className="text-xs text-muted-foreground">{view.unavailable}</p>}
  {!view.enabled&&view.available&&view.reconfirm&&<p role="status" className="text-xs text-muted-foreground">{t('Off since this update, which changed the warning. Turn it on again to confirm it.')}</p>}
  {confirming&&<div role="group" aria-label={t('Turn on full access')} className="flex flex-wrap items-center gap-2 rounded-xl bg-muted/40 p-3 text-sm">
   <span className="min-w-0 flex-1">{t('Turn on full access for this project? New requests here will run {provider}’s own commands.',{provider})}</span>
   <Button size="sm" variant="destructive" disabled={busy} onClick={()=>void set(true)}>{busy?t('Turning on…'):t('Turn on full access')}</Button>
   <Button size="sm" variant="ghost" disabled={busy} onClick={()=>setConfirming(false)}>{t('Cancel')}</Button>
  </div>}
  {error&&<p role="alert" className="text-xs text-destructive">{error}</p>}
 </section>;
}
