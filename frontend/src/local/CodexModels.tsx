import {useEffect,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {local} from './api';
import {t} from '@/i18n';

type Catalog={state:string;detail:string;models:{model:string;displayName?:string}[]};
export function CodexModels({profileId,model,onSelect,cards=false,disabled=false}:{profileId:string;model:string;onSelect:(value:string)=>void;cards?:boolean;disabled?:boolean}) {
  const [error,setError]=useState('');
  const catalog=useQuery({queryKey:['codex-models',profileId],queryFn:()=>local<Catalog>('model_status',{profileId}),
    refetchInterval:q=>q.state.data?.state==='checking'?500:false});
  async function load(){setError('');try{await local('model_check',{profileId});await catalog.refetch();}catch(e){setError(String(e));}}
  useEffect(()=>{if(cards)void load();},[profileId,cards]);
  return <section className="space-y-2" aria-label={t('Codex model catalog')}>
    <Button variant="outline" disabled={disabled||catalog.data?.state==='checking'} onClick={()=>void load()}>{t('Load Codex models')}</Button>
    {catalog.data?.state!=='unchecked'&&<p role="status">{catalog.data?.detail}</p>}
    {(error||catalog.error)&&<p role="alert">{error||String(catalog.error)}</p>}
    {cards&&!!catalog.data?.models.length&&<div className="dervo-setup-models" role="group" aria-label={t('Available Codex models')}>{catalog.data.models.map(m=><button type="button" key={m.model} disabled={disabled} aria-pressed={model===m.model} onClick={()=>onSelect(m.model)}><strong>{m.displayName??m.model}</strong><span>{m.model}</span></button>)}</div>}
    {!cards&&!!catalog.data?.models.length&&<select aria-label={t('Available Codex model')} value={model} onChange={e=>onSelect(e.target.value)} className="w-full rounded border bg-background p-2">
      <option value="">{t('Choose an available model')}</option>{catalog.data.models.map(m=><option key={m.model} value={m.model}>{m.displayName??m.model} · {m.model}</option>)}
    </select>}
  </section>;
}
