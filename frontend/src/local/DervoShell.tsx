import type {ReactNode} from 'react';
import {Button} from '@/shared/ui/button';
import {DrawerPanelIcon} from '@/shared/ui/DrawerPanelIcon';
import {useOptionalSidebar} from '@/shared/ui/sidebar';
import {isMacPlatform} from '@/shared/lib/platform';
import {useIsFullscreen} from '@/shared/lib/useIsFullscreen';
import {WorkTag} from './WorkMark';
import {t} from '@/i18n';

/** The main panel's head, from thread-review.html: the project, a crumb for where you are (a thread reads its workstream,
 * then its own name, so the name is on screen even with the sidebar folded and Preview open), and the tools on the right.
 * Settings shows it bare, as the 40px band that's the window's drag area, level with the sidebar's chrome row.
 * With the sidebar closed the window chrome moves here: room for the traffic lights, then the sidebar toggle, so the
 * sidebar can always be brought back. The head is a drag region, like the chrome it replaces. */
export function DervoMainHead({title,work,crumb,here,detail,live,tools}:{title?:string;work?:boolean;crumb?:string;here?:string;detail?:string;live?:ReactNode;tools?:ReactNode}){
  const sidebar=useOptionalSidebar();
  const fullscreen=useIsFullscreen();
  const collapsed=!!sidebar&&!sidebar.open&&!sidebar.isMobile;
  const lights=collapsed&&isMacPlatform()&&!fullscreen;
  return <header className="dv-main-head" data-tauri-drag-region data-lights={lights||undefined} data-testid="dervo-main-head">
    {collapsed&&<Button type="button" variant="ghost" size="icon" className="dv-main-chrome" aria-label={t('Show sidebar')} data-sidebar="trigger" onClick={()=>sidebar.toggleSidebar()}><DrawerPanelIcon side="right"/></Button>}
    {title&&<span className="dv-title" title={detail}>{title}</span>}
    {title&&work&&<WorkTag className="dv-main-work"/>}
    {crumb&&<span className="dv-crumb" title={detail}>{crumb}{here&&<><span className="dv-crumb-sep" aria-hidden="true">/</span><span className="dv-here" aria-current="page">{here}</span></>}</span>}
    {live&&<div className="dv-main-live">{live}</div>}
    {tools&&<div className="dv-tools">{tools}</div>}
  </header>;
}

/** The Progress panel toggle's glyph: a frame with its right pane marked. */
export function PanelGlyph(){
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" focusable="false"><rect x="2.5" y="3" width="11" height="10" rx="2"/><path d="M10 3v10"/></svg>;
}

/** The ⋯ of the main head. */
export function MoreGlyph(){
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false"><circle cx="3.5" cy="8" r="1.2"/><circle cx="8" cy="8" r="1.2"/><circle cx="12.5" cy="8" r="1.2"/></svg>;
}

export function ProjectTerminalButton({open,onToggle}:{open:boolean;onToggle:()=>void}){
  return <Button type="button" variant="ghost" size="icon" data-terminal-trigger aria-label={open?t('Hide terminal'):t('Show terminal')} title={open?t('Hide terminal'):t('Show terminal')} aria-expanded={open} aria-controls="project-terminal" onClick={onToggle}>
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="m3 4 4 4-4 4M9 12h4"/></svg>
  </Button>;
}
