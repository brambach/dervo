import {useState} from 'react';
import {Button} from '@/shared/ui/button';
import {ConnectionPreflight} from './ConnectionPreflight';
import {AddAccountForm,limitLabel} from './AddAccount';
import {local} from './api';
import {providerAuthor} from './providerLabel';
import {t} from '@/i18n';

type Profile={id:string;provider:string;identity:string;category:string;method?:string;monthly_limit_usd?:number|null};
/** One connected account in Settings, on Connected accounts. A subscription row keeps its sign-in controls. An API-key row shows how it
 * pays and its monthly limit, and can be removed once no project or conversation uses it; the host refuses otherwise. */
export function AccountRow({profile,onChanged}:{profile:Profile;onChanged:()=>Promise<unknown>}){
  const [confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const keyed=profile.method==='api_key';
  async function remove(){
    if(busy)return;
    setBusy(true);setError('');
    try{await local('profile_key_remove',{profileId:profile.id});setConfirm(false);await onChanged();}
    catch(e){setError(String(e));}
    finally{setBusy(false);}
  }
  return <div className="rounded-xl border p-4" data-testid="account-row" data-method={profile.method??'subscription'}>
    <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0"><p className="text-sm">{providerAuthor(profile.provider)} · {profile.identity}</p>
        <p className="text-xs text-muted-foreground">{keyed?`${t('API key · pay per use')} · ${profile.monthly_limit_usd==null?t('no spend limit'):t('{limit} monthly limit, doesn’t stop runs',{limit:limitLabel(profile.monthly_limit_usd)})}`:t('Subscription')}</p></div>
      {keyed&&(confirm
        ?<span className="flex items-center gap-2 text-xs"><span>{t('Remove this key?')}</span><Button size="sm" variant="destructive" disabled={busy} onClick={()=>void remove()}>{busy?t('Removing…'):t('Remove')}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={()=>{setConfirm(false);setError('');}}>{t('Keep')}</Button></span>
        :<Button size="sm" variant="outline" onClick={()=>setConfirm(true)}>{t('Remove')}</Button>)}
    </div>
    <ConnectionPreflight profileId={profile.id} provider={profile.provider} method={profile.method}/>
    {error&&<p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
  </div>;
}
export function AddAccountSection({onChanged}:{onChanged:()=>Promise<unknown>}){
  const [open,setOpen]=useState(false);
  return open
    ?<AddAccountForm defaultProvider="claude" onAdded={async()=>{await onChanged();setOpen(false);}} onCancel={()=>setOpen(false)}/>
    :<Button variant="outline" onClick={()=>setOpen(true)}>{t('Add account')}</Button>;
}
