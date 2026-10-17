import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useState} from 'react';
import {SettingRow,SettingsSection,Switch} from './DervoControls';
import {local} from './api';
import {isWindowsPlatform} from '@/shared/lib/platform';
import {t} from '@/i18n';

export type KeepAwake={enabled:boolean;holding:boolean;supported:boolean;line:string};
export const KEEP_AWAKE_KEY=['keep-awake'] as const;

/** Keep this Mac awake while agents work (keep_awake.py), on Preferences. On unless you turn it off. The host holds
 * macOS's idle-sleep assertion only while a run is live and lets go when the last one ends; closing the lid on battery
 * still sleeps the Mac, and the line says so. Your phone sees the same on/holding state (Home's snapshot). */
export function KeepAwakeSetting(){
  const client=useQueryClient();
  const computer=isWindowsPlatform()?'PC':'Mac';
  const state=useQuery({queryKey:KEEP_AWAKE_KEY,queryFn:()=>local<KeepAwake>('keep_awake_status',{}),refetchInterval:15000,retry:false});
  const [saving,setSaving]=useState(false),[error,setError]=useState('');
  async function change(enabled:boolean){
    if(saving)return;
    setSaving(true);setError('');
    try{client.setQueryData(KEEP_AWAKE_KEY,await local<KeepAwake>('keep_awake_set',{enabled}));}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setSaving(false);}
  }
  const data=state.data,name=t('Keep this {computer} awake while agents work',{computer});
  const line=data&&!data.supported?t('Not available on this {computer}.',{computer})
    :data?.holding?t('Keeping it awake now. Closing the lid still puts it to sleep.')
    :t('Only while an agent is running. Closing the lid still puts it to sleep.');
  return <SettingsSection title={t('Sleep')} id="dv-pref-awake">
    <div data-testid="keep-awake-setting" style={{display:'contents'}}>
      <SettingRow name={name} line={line} disabled={!!data&&!data.supported}
        end={<Switch label={name} checked={data?(data.enabled&&data.supported):true} disabled={!data||!data.supported||saving} onChange={next=>void change(next)}/>}/>
      {(error||state.isError)&&<p role="alert" className="dv-field-note">{error||t('This setting couldn’t be read. Try again in a moment.')}</p>}
    </div>
  </SettingsSection>;
}
