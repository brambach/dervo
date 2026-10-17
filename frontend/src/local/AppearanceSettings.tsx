import {useRef,type KeyboardEvent} from 'react';
import {useTheme} from '@/shared/theme/ThemeProvider';
import {SettingRow,SettingsSection,Switch,nextIndex} from './DervoControls';
import {setPalette,usePalette} from './dervoPalette';
import {isWindowsPlatform} from '@/shared/lib/platform';
import {t} from '@/i18n';

/** Theme and window, on Preferences (settings/preferences.html). Each tile draws its theme, so Dark looks dark in light
 * mode, and choosing one applies at once, window-wide. The same light/dark switch is in ⌘K. Dervo is the forest theme
 * (beta.16.1 item 10): Dark with the forest palette over it (dervoPalette.ts). The system tile names the OS, as
 * Language's Match does (beta.18: Match Windows on a PC). */
type Mode='light'|'dark'|'dervo'|'system';
const MODES:{id:Mode;label:()=>string}[]=[{id:'light',label:()=>t('Light')},{id:'dark',label:()=>t('Dark')},{id:'dervo',label:()=>'Dervo'},
  {id:'system',label:()=>t('Match {os}',{os:isWindowsPlatform()?'Windows':'macOS'})}];
const Preview=({theme}:{theme:'light'|'dark'|'forest'})=><span className={`dv-pv dv-${theme}`} aria-hidden="true">
  <span className="dv-pv-s"><i/><i/><i/></span><span className="dv-pv-m"><i/><i/><i/><i/></span></span>;

export function ThemeTiles(){
  const theme=useTheme();
  const palette=usePalette();
  const selected:Mode=theme.followSystem?'system':theme.isDark?(palette==='forest'?'dervo':'dark'):'light';
  const tiles=useRef<(HTMLButtonElement|null)[]>([]);
  function choose(id:Mode){
    if(id===selected)return;
    setPalette(id==='dervo'?'forest':null);
    theme.applyAppearance({theme:id==='dark'||id==='dervo'?'buzz-dark':'buzz',accent:theme.accentColor,followSystem:id==='system'});
  }
  function keys(event:KeyboardEvent,index:number){
    const next=nextIndex(event.key,index,MODES.map(()=>false));
    if(next<0)return;
    event.preventDefault();tiles.current[next]?.focus();choose(MODES[next].id);
  }
  return <SettingsSection title={t('Theme')} caption={t('Also in ⌘K')} id="dv-pref-theme">
    <div className="dv-tiles" role="radiogroup" aria-labelledby="dv-pref-theme">
      {MODES.map((mode,index)=><button key={mode.id} ref={element=>{tiles.current[index]=element;}} type="button" role="radio" className="dv-tile"
        aria-checked={selected===mode.id} tabIndex={selected===mode.id?0:-1} onClick={()=>choose(mode.id)} onKeyDown={event=>keys(event,index)}>
        {mode.id==='system'?<span className="dv-pv-stack"><Preview theme="light"/><Preview theme="dark"/></span>:<Preview theme={mode.id==='dervo'?'forest':mode.id}/>}
        {mode.label()}
      </button>)}
    </div>
  </SettingsSection>;
}

/** Glass window: the desktop shows through Dervo's window. Mac builds only; the strength slider shows while it's on. */
export function GlassWindow(){
  const theme=useTheme();
  if(!theme.glassBackgroundSupported)return null;
  return <SettingsSection title={t('Window')} id="dv-pref-window">
    <SettingRow name={t('Glass window')} line={t('Lets your desktop show through Dervo’s window. Off keeps the meadow behind the sidebar.')}
      end={<Switch label={t('Glass window')} checked={theme.glassBackground} onChange={next=>theme.setGlassBackground(next)}/>}/>
    {theme.glassBackground&&<SettingRow name={t('Glass strength')} line={t('Saved as you drag.')}
      end={<input className="dv-range" aria-label={t('Glass strength')} type="range" min="30" max="90" value={theme.glassOpacity} onChange={event=>theme.setGlassOpacity(Number(event.target.value))}/>}/>}
  </SettingsSection>;
}

/** Kept for callers that show the whole Appearance block: the tiles, then the window. */
export function AppearanceSettings(){return <><ThemeTiles/><GlassWindow/></>;}
