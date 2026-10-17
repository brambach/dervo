import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {local,CLAUDE_EFFORTS} from './api';
import {providerAuthor} from './providerLabel';
import {t} from '@/i18n';

type Binding={profile_id:string;provider:string;model:string;identity:string;visibility:string};
type Catalog={state:string;detail:string;models:{model:string;defaultReasoningEffort?:string;supportedReasoningEfforts?:{reasoningEffort:string}[]}[]};

export function ReviewerSetup({projectId,channelId,binding,onSaved}:{projectId:string;channelId:string;binding:Binding;onSaved:()=>Promise<unknown>}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[effort,setEffort]=useState('');
  const flight=useRef(false);
  const codex=binding.provider==='codex';
  const catalog=useQuery({queryKey:['reviewer-catalog',binding.profile_id],enabled:open&&codex,
    queryFn:()=>local<Catalog>('model_status',{profileId:binding.profile_id}),
    refetchInterval:q=>q.state.data?.state==='checking'?500:false});
  const entry=catalog.data?.state==='catalog'?catalog.data.models.find(m=>m.model===binding.model):undefined;
  const efforts=codex?(entry?.supportedReasoningEfforts??[]).map(e=>e.reasoningEffort):[...CLAUDE_EFFORTS];
  const suggested=codex?entry?.defaultReasoningEffort:'low';
  useEffect(()=>{if(!effort&&suggested&&efforts.includes(suggested))setEffort(suggested);},[suggested,efforts.join('|'),effort]);
  async function load(){setError('');try{await local('model_check',{profileId:binding.profile_id});await catalog.refetch();}catch(e){setError(String(e));}}
  async function save(){if(flight.current)return;flight.current=true;setBusy(true);setError('');try{
    await local('collab_reviewer_setup',{projectId,channelId,profileId:binding.profile_id,model:binding.model,effort});
    await onSaved();setOpen(false);
  }catch(e){setError(String(e));}finally{flight.current=false;setBusy(false);}}
  return <section className="rounded-2xl border bg-muted/20 p-5" aria-label={t('Quick reviewer setup')}>
    <div className="flex items-start gap-3"><div className="space-y-2"><h3 className="font-semibold">{t('A second look before you move on.')}</h3>
      <p className="text-sm leading-relaxed text-muted-foreground">{t('A reviewer checks the finished work, flags problems and lets your coding agent make one correction. You’ll see the verdict in the task.')}</p></div></div>
    {!open?<Button className="mt-4" disabled={binding.visibility!=='shared'} onClick={()=>{setOpen(true);if(codex)void load();}}>{t('Set up review')}</Button>:<div className="mt-5 space-y-4">
      <div className="rounded-xl border bg-background p-4 text-sm"><strong>{providerAuthor(binding.provider)} · {binding.model}</strong><p className="mt-1 text-muted-foreground">{binding.identity}</p><p className="mt-3 leading-relaxed">{t('Uses this account and model in a separate, read-only review conversation. Dervo creates it for you. Each review uses additional allowance on this account.')}</p></div>
      <fieldset disabled={busy} className="space-y-2"><legend className="mb-2 text-sm font-medium">{t('Review effort')}</legend><div className="flex flex-wrap gap-2">{efforts.map(value=><button type="button" key={value} aria-pressed={effort===value} className={`rounded-xl border px-3 py-2 text-sm capitalize ${effort===value?'border-primary bg-primary/10':'bg-background hover:bg-muted'}`} onClick={()=>setEffort(value)}>{value}</button>)}</div></fieldset>
      {codex&&!efforts.length&&<div className="space-y-2 text-sm"><p role="status">{catalog.data?.state==='checking'?t('Checking your Codex model options…'):t('Review options aren’t available for this model yet.')}</p><Button variant="outline" disabled={catalog.data?.state==='checking'} onClick={()=>void load()}>{t('Reload model options')}</Button></div>}
      <p className="text-xs leading-relaxed text-muted-foreground">{t('Applies to new coding requests in this project. Enabling review doesn’t start a task or approve any result.')}</p>
      <div className="flex gap-2"><Button disabled={busy||!efforts.includes(effort)} onClick={()=>void save()}>{busy?t('Setting up…'):t('Enable review')}</Button><Button variant="ghost" disabled={busy} onClick={()=>setOpen(false)}>{t('Cancel')}</Button></div>
      {(error||catalog.error)&&<p role="alert" className="text-sm text-destructive">{error||String(catalog.error)}</p>}
    </div>}
    {binding.visibility!=='shared'&&<p className="mt-3 text-sm">{t('Use a project conversation to enable review. Private conversations stay private.')}</p>}
  </section>;
}
