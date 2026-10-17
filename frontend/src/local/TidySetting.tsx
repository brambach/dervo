import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Archive} from 'lucide-react';
import {DropdownMenuItem,DropdownMenuSeparator,DropdownMenuSub,DropdownMenuSubContent,DropdownMenuSubTrigger} from '@/shared/ui/dropdown-menu';
import {TIDY_CHOICES,TIDY_DEFAULT,TIDY_SMART,setTidySetting,tidyLabel,tidySetting,type TidyDays} from './tidy';
import './ArchivedFold.css';
import {t} from '@/i18n';

/** "Archive quiet threads · 14 days" in the project menu, above Project settings (design b16-lifecycle, setting): Smart,
 * After 7, 14 (the default) or 30 days, or Never, and one line on what never goes. The value shows in the row, so you
 * see it without opening it. Smart (beta.16.1 item 7) comes first, as the quickest.
 *
 * ProjectPicker.tsx draws the project menu and isn't this thread's file, so this is the piece it renders (asked of its
 * owner): `<TidySettingMenu projectId={selected}/>` just above Project settings. */
export function TidySettingMenu({projectId}:{projectId:string}){
 const client=useQueryClient();
 const setting=useQuery({queryKey:['tidy-setting',projectId],enabled:!!projectId,retry:false,queryFn:()=>tidySetting(projectId)});
 const days=setting.data?.days===undefined?TIDY_DEFAULT:setting.data.days;
 async function choose(next:TidyDays){
  const saved=await setTidySetting(projectId,next);
  client.setQueryData(['tidy-setting',projectId],saved);
 }
 const tick=(on:boolean)=>on?<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>
  :<span style={{width:16,flex:'none'}} aria-hidden="true"/>;
 return <DropdownMenuSub>
  <DropdownMenuSubTrigger disabled={!projectId||setting.isError}><Archive size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Archive quiet threads')}</span>
   <span className="dv-mi-end"><span className="dv-val">{setting.data?tidyLabel(days):''}</span></span></DropdownMenuSubTrigger>
  <DropdownMenuSubContent className="dv-menu-tidy" aria-label={t('Archive quiet threads')}>
   <DropdownMenuItem role="menuitemradio" aria-checked={days===TIDY_SMART} className="dv-mi-two" onSelect={()=>void choose(TIDY_SMART)}>{tick(days===TIDY_SMART)}
    <span className="dv-mi-t"><span>{t('Smart')}</span><small>{t('Finished after a day, others after 7')}</small></span></DropdownMenuItem>
   {TIDY_CHOICES.map(choice=><DropdownMenuItem key={choice} role="menuitemradio" aria-checked={days===choice} className={choice===TIDY_DEFAULT?'dv-mi-two':undefined}
    onSelect={()=>void choose(choice)}>{tick(days===choice)}{choice===TIDY_DEFAULT?<span className="dv-mi-t"><span>{t('After {days} days',{days:choice})}</span><small>{t('The default')}</small></span>:<span className="dv-mi-t">{t('After {days} days',{days:choice})}</span>}</DropdownMenuItem>)}
   <DropdownMenuItem role="menuitemradio" aria-checked={days===null} onSelect={()=>void choose(null)}>{tick(days===null)}<span className="dv-mi-t">{t('Never')}</span></DropdownMenuItem>
   <DropdownMenuSeparator/>
   <p className="dv-menu-note">{days===TIDY_SMART?t('Finished means approved and committed, rejected, or a text answer.'):t('Only threads nobody opened or wrote in.')} {t('Pinned threads, running work, questions and unreviewed changes stay.')}</p>
  </DropdownMenuSubContent>
 </DropdownMenuSub>;
}
