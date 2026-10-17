import {useEffect,useState} from 'react';
import {loadPrefs,notificationPermission,openNotificationSettings,postNotification,savePrefs,type NotificationPrefs} from './notifications';
import {SettingRow,SettingsSection,Switch} from './DervoControls';
import {StatusGlyph} from './StatusGlyph';
import {t} from '@/i18n';

/** Notifications, on Preferences (settings/preferences.html, notifications-off.html): the three switches, then macOS's
 * permission with the one action that fixes it, and a test note. Switches save the moment they change, on this Mac. */
const PERMISSION:Record<string,{name:string;line:string}>={
  allowed:{name:t('Allowed in macOS'),line:t('Focus modes can still hold them.')},
  denied:{name:t('Turned off in macOS'),line:t('None of the notifications above can show until you allow Dervo in System Settings, Notifications.')},
  not_determined:{name:t('macOS hasn’t asked yet'),line:t('Dervo asks macOS the first time you allow them.')},
  unavailable:{name:t('Not available in this build of Dervo'),line:t('Notifications need the signed Dervo app.')},
  unknown:{name:t('macOS didn’t say'),line:t('Try again when Dervo is in front.')},
};
const SWITCHES:[keyof NotificationPrefs,string,string][]=[
  ['needs',t('When a thread needs you'),t('It’s waiting for your answer or a decision.')],
  ['finished',t('When a thread finishes'),t('A result is ready to read or review.')],
  ['backgroundOnly',t('Only when Dervo is in the background'),t('Stay quiet while you’re using Dervo. The thread you’re reading never notifies.')],
];

export function NotificationSettings(){
  const [prefs,setPrefs]=useState<NotificationPrefs>(loadPrefs);
  const [permission,setPermission]=useState<string|null>(null),[busy,setBusy]=useState(''),[result,setResult]=useState(''),[error,setError]=useState('');
  useEffect(()=>{
    const read=()=>void notificationPermission(false).then(setPermission).catch(()=>setPermission('unknown'));
    read();
    // Coming back from System Settings: check again, so the row turns to Allowed without a reload.
    window.addEventListener('focus',read);
    return ()=>window.removeEventListener('focus',read);
  },[]);
  function change(patch:Partial<NotificationPrefs>){const next={...prefs,...patch};setPrefs(next);savePrefs(next);}
  async function run(key:string,work:()=>Promise<void>){
    if(busy)return;
    setBusy(key);setError('');setResult('');
    try{await work();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy('');}
  }
  const known=permission?PERMISSION[permission]??PERMISSION.unknown:null;
  const denied=permission==='denied',blocked=permission==='unavailable'||denied;
  const testWhy=permission==='unavailable'?t('Not available in this build of Dervo.'):denied?t('Allow notifications in macOS first.'):undefined;
  return <SettingsSection title={t('Notifications')} id="dv-pref-notify">
    <div data-testid="notification-settings" style={{display:'contents'}}>
      {SWITCHES.map(([key,name,line])=><SettingRow key={key} name={name} line={line} end={<Switch label={name} checked={prefs[key]} onChange={next=>change({[key]:next})}/>}/>)}
      <div className={`dv-set${denied?' dv-lead':''}`}>
        {denied&&<StatusGlyph state="needs" accent/>}
        <div><p className="dv-set-name">{known?known.name:t('Checking…')}</p>{known&&<p className="dv-set-line">{known.line}</p>}</div>
        <div className="dv-set-end">
          {permission==='not_determined'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void run('allow',async()=>setPermission(await notificationPermission(true)))}>{busy==='allow'?t('Waiting for macOS…'):t('Allow notifications')}</button>}
          {denied&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void run('open',async()=>{await openNotificationSettings();})}>{t('Open System Settings')}</button>}
          {!blocked&&permission!==null&&permission!=='not_determined'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void run('test',sendTest)}>{busy==='test'?t('Sending…'):t('Send a test')}</button>}
        </div>
      </div>
      {blocked&&<SettingRow name={t('Send a test notification')} disabled line={testWhy}
        end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled>{t('Send test')}</button>}/>}
      {result&&<p role="status" className="dv-field-note">{result}</p>}
      {error&&<p role="alert" className="dv-field-note">{error}</p>}
    </div>
  </SettingsSection>;
  async function sendTest(){
    const sent=await postNotification({id:`test:${Date.now()}`,title:t('Dervo notifications work'),body:t('You’ll see notes like this when a thread finishes or needs you.'),projectId:'',channelId:''});
    setResult(sent?t('Sent. If nothing appeared, check Focus and macOS notification settings.'):t('Nothing was sent. Notifications aren’t available here.'));
  }
}
