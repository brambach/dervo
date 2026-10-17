import {useState} from 'react';
import {closeAllWebSockets} from '@/shared/api/relayWebSocketClose';
import {LANGUAGES,language,languageChoice,resolveLanguage,saveLanguageChoice,systemLanguage,t,type LanguageChoice} from '@/i18n';
import {Select,SettingRow,SettingsSection} from './DervoControls';
import {isWindowsPlatform} from '@/shared/lib/platform';

const RELOAD_TEARDOWN_TIMEOUT_MS=500;
const nameOf=(id:string)=>LANGUAGES.find(each=>each.id===id)?.name??id;

/** The window reloads into the new language the way ⌘R reloads it (src/app/useReloadShortcut.ts): sockets close first,
 * for at most half a second. Runs belong to the host, and nothing in src/local stops one when the page goes away. */
async function reloadWindow(){
  await Promise.race([closeAllWebSockets(),new Promise<void>(resolve=>window.setTimeout(resolve,RELOAD_TEARDOWN_TIMEOUT_MS))]);
  window.location.reload();
}

/** Language, first on Preferences (beta.17, Bruno's report on beta.14). It's this Mac's, like Theme, so it lives under
 * You rather than a project's General page. "Match macOS" is the default and says what it comes to. Each language is
 * named in itself, so you can find yours whatever the window shows now. A pick that changes the language reloads the
 * window, since text built when a screen loads would otherwise stay in the old one. */
export function LanguageSetting({reload=reloadWindow}:{reload?:()=>void|Promise<void>}={}){
  const [chosen,setChosen]=useState<LanguageChoice>(languageChoice());
  const [switching,setSwitching]=useState(false);
  function choose(next:LanguageChoice){
    if(next===chosen||switching)return;
    saveLanguageChoice(next);setChosen(next);
    if(resolveLanguage(next)===language())return;
    setSwitching(true);
    void reload();
  }
  return <SettingsSection title={t('Language')} id="dv-pref-language">
    <SettingRow name={t('Dervo’s screens')} line={switching?t('Switching…'):t('Only Dervo’s own words change. What agents write isn’t translated. Switching reloads the window, like ⌘R.')}
      end={<Select<LanguageChoice> label={t('Language')} value={chosen} disabled={switching} onChange={choose}
        groups={[{options:[{value:'system',label:t('Match {os}',{os:isWindowsPlatform()?'Windows':'macOS'}),sub:`· ${nameOf(systemLanguage())}`}]},{options:LANGUAGES.map(each=>({value:each.id,label:<span lang={each.id}>{each.name}</span>}))}]}/>}/>
  </SettingsSection>;
}
