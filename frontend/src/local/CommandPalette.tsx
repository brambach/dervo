import {useEffect,useMemo,useRef,useState,type ComponentType} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {RotateCcw,BarChart3,BookOpen,BookPlus,Bug,ClipboardList,Lock,MessageSquare,Moon,PanelRight,Pin,PinOff,Plug,Plus,Search,Settings,SlidersHorizontal,Square,Sun,UserRound,Home as House,Sparkles,SunMoon,Download,UserCircle,History} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/shared/ui/dialog';
import type {Conversation} from './api';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {threadGlyph} from './DervoSidebar';
import {isProjectTerminalKey} from './terminalKeys';
import {pinThread,pinnedThreads} from './tidy';
import './CommandPalette.css';
import {t} from '@/i18n';

export type PaletteGroup='Needs you'|'Running'|'Go to'|'Actions'|'Search';
/** A group's heading in the window's language; the group itself stays the English name the code sorts by. */
const GROUP_LABELS:Record<PaletteGroup,string>={'Needs you':t('Needs you'),Running:t('Running'),'Go to':t('Go to'),Actions:t('Actions'),Search:t('Search')};
export type PaletteItem={id:string;group:PaletteGroup;label:string;detail?:string;keys?:string;icon:ComponentType<{size?:number;strokeWidth?:number}>|GlyphState;run:()=>void;conversation?:string};
export type PaletteState={conversations:Conversation[];currentId:string;hasProject:boolean;ready:boolean;guide:null|{visible:boolean};canStopCurrent:boolean;dark:boolean;retryHere?:boolean;retryOther?:string|null;pinned?:boolean};
/** The Settings pages ⌘K goes to directly (surfaces, inventory 10). */
export type SettingsTarget='accounts'|'account'|'preferences'|'updates';
export type PaletteActions={openConversation:(id:string)=>void;orchestrator?:()=>void;catchUp:()=>void;knowledge:()=>void;projectSettings:()=>void;create:(isPrivate:boolean)=>void;
  toggleGuide:()=>void;stopCurrent:()=>void;retryHere?:()=>void;retryOther?:()=>void;stopConversation:(id:string)=>void;changeModel:()=>void;usage:()=>void;addGuidance:()=>void;toggleTheme:()=>void;feedback:()=>void;workspaceSettings:()=>void;search:(query:string)=>void;
  settingsPage?:(page:SettingsTarget)=>void;overview?:()=>void;reportBug?:()=>void;bugs?:()=>void;history?:()=>void;pin?:(pinned:boolean)=>void};
const SETTINGS_PAGES:[SettingsTarget,string,ComponentType<{size?:number;strokeWidth?:number}>][]=[['accounts',t('Connected accounts'),Plug],['account',t('Account'),UserCircle],['preferences',t('Preferences'),SunMoon],['updates',t('Updates'),Download]];

/** Everything ⌘K offers, in order: what needs you, what's running, screens and conversations, then actions. Each entry
 * exists only when its action can run now, so nothing in the list leads to a refusal. */
export function paletteItems(state:PaletteState,actions:PaletteActions):PaletteItem[]{
  const items:PaletteItem[]=[];
  const name=(c:Conversation)=>c.name;
  // Threads carry their own status glyph, never an alert icon: the view behind already has the one accent.
  const icon=(c:Conversation):PaletteItem['icon']=>c.visibility==='private'?Lock:threadGlyph(c.activity).state;
  // What it's waiting on, in a few words: the mark's own words, or for a plain "needs you" the first sentence of its summary.
  const waiting=(c:Conversation,label:string)=>label!=='Needs you'&&label!==t('Needs you')?label:c.activity?.summary?.split(/(?<=\.)\s/)[0]?.replace(/\.$/,'')||t('Waiting on you');
  for(const c of state.conversations)if(c.activity?.status==='needs'){const glyph=threadGlyph(c.activity);items.push({id:`needs:${c.id}`,group:'Needs you',label:name(c),detail:waiting(c,glyph.label),icon:glyph.state,run:()=>actions.openConversation(c.id),conversation:c.id});}
  for(const c of state.conversations)if(c.activity?.running)items.push({id:`running:${c.id}`,group:'Running',label:name(c),detail:c.activity.step??t('Starting'),icon:'running',run:()=>actions.openConversation(c.id),conversation:c.id});
  if(state.hasProject){
    // Home is where you pick up where you left off (Bryce, 18 Sep), so it has Catch up's shortcut and Catch up folds into it.
    // Catch up is the pane's name since 20 Sep.
    items.push({id:'go:home',group:'Go to',label:t('Home'),detail:t('Catch up'),keys:'⌘1',icon:House,run:actions.orchestrator??actions.catchUp});
    if(actions.history)items.push({id:'go:history',group:'Go to',label:t('History'),detail:t('What each run did'),icon:History,run:actions.history});
    items.push({id:'go:knowledge',group:'Go to',label:t('Knowledge'),keys:'⌘2',icon:BookOpen,run:actions.knowledge});
    // Next up is the one list per project, in place of Bugs and ideas (b16/lifecycle). Its page is still called 'bugs'.
    if(state.ready&&actions.bugs)items.push({id:'go:next-up',group:'Go to',label:t('Next up'),icon:ClipboardList,run:actions.bugs});
    items.push({id:'go:settings',group:'Go to',label:t('Project settings'),keys:'⌘,',icon:SlidersHorizontal,run:actions.projectSettings});
    for(const c of state.conversations)if(c.id!==state.currentId)items.push({id:`go:${c.id}`,group:'Go to',label:name(c),detail:c.visibility==='private'?t('Private'):undefined,icon:icon(c),run:()=>actions.openConversation(c.id),conversation:c.id});
    items.push({id:'act:new-shared',group:'Actions',label:t('New thread'),keys:'⌘N',icon:Plus,run:()=>actions.create(false)});
    items.push({id:'act:new-private',group:'Actions',label:t('New private thread'),detail:t('Home doesn’t see it'),icon:Lock,run:()=>actions.create(true)});
  }
  const here=state.conversations.find(c=>c.id===state.currentId)?.name;
  // The Settings pages you'd otherwise reach through the account menu, listed under Go to only when you type.
  if(actions.settingsPage)for(const [page,label,Icon] of SETTINGS_PAGES)items.push({id:`go:settings:${page}`,group:'Go to',label,detail:t('Settings'),icon:Icon,run:()=>actions.settingsPage!(page),conversation:`settings:${page}`});
  if(state.retryOther&&actions.retryOther)items.push({id:'act:retry-other',group:'Actions',label:here?t('Retry {here} on {retryOther}',{here,retryOther:state.retryOther}):t('Retry on {retryOther}',{retryOther:state.retryOther}),icon:RotateCcw,run:actions.retryOther});
  if(state.retryHere&&actions.retryHere)items.push({id:'act:retry',group:'Actions',label:t('Retry here'),icon:RotateCcw,run:actions.retryHere});
  if(state.canStopCurrent)items.push({id:'act:stop-current',group:'Actions',label:t('Stop this thread'),icon:Square,run:actions.stopCurrent});
  for(const c of state.conversations)if(c.activity?.running&&c.id!==state.currentId)items.push({id:`act:stop:${c.id}`,group:'Actions',label:t('Stop {name}',{name:name(c)}),icon:Square,run:()=>actions.stopConversation(c.id)});
  // Pin keeps the thread on screen from being tidied away, as its sidebar menu does. Only once the pins have loaded.
  if(state.currentId&&state.pinned!==undefined&&actions.pin){const pinned=state.pinned;items.push({id:'act:pin',group:'Actions',label:pinned?t('Unpin this thread'):t('Pin this thread'),
    detail:pinned?t('Dervo can tidy it away'):t('Never tidied away'),icon:pinned?PinOff:Pin,run:()=>actions.pin!(!pinned)});}
  if(state.guide)items.push({id:'act:guide',group:'Actions',label:state.guide.visible?t('Hide progress'):t('Show progress'),icon:PanelRight,run:actions.toggleGuide});
  if(state.currentId)items.push({id:'act:model',group:'Actions',label:t('Change account or model…'),icon:UserRound,run:actions.changeModel});
  items.push({id:'act:usage',group:'Actions',label:t('Usage by account'),icon:BarChart3,run:actions.usage});
  if(state.ready)items.push({id:'act:guidance',group:'Actions',label:t('Add guidance'),icon:BookPlus,run:actions.addGuidance});
  if(state.ready&&actions.overview)items.push({id:'act:overview',group:'Actions',label:t('Ask for a project overview'),icon:Sparkles,run:actions.overview});
  items.push({id:'act:theme',group:'Actions',label:state.dark?t('Switch to light appearance'):t('Switch to dark appearance'),icon:state.dark?Sun:Moon,run:actions.toggleTheme});
  // ⌘⇧B: bug capture for the Dervo admin, Send feedback on Bug for everyone else (ConnectedApp.tsx).
  if(actions.reportBug)items.push({id:'act:report-bug',group:'Actions',label:t('Log a Dervo bug'),keys:'⌘⇧B',icon:Bug,run:actions.reportBug});
  items.push({id:'act:feedback',group:'Actions',label:t('Send feedback'),icon:MessageSquare,run:actions.feedback});
  items.push({id:'act:workspace',group:'Actions',label:t('Settings'),icon:Settings,run:actions.workspaceSettings});
  return items;
}

/** The thread on screen's pin, for ⌘K: `pinned` for PaletteState and `pin` for PaletteActions. It shares the sidebar's
 * query, so a pin from either place shows in both. Nothing until the pins have loaded, so the entry never guesses. */
export function useThreadPin(projectId:string,threadId:string,ready:boolean,onError?:(message:string)=>void):{pinned?:boolean;pin?:(pinned:boolean)=>void}{
  const client=useQueryClient();
  const pins=useQuery({queryKey:['conversation-pins',projectId],enabled:ready&&!!projectId,retry:false,queryFn:()=>pinnedThreads(projectId)});
  if(!threadId||!Array.isArray(pins.data))return {};
  // A refused pin leaves the list as the host has it, which the refetch shows, and says why the way the sidebar's menu
  // does (behaviour review N10, 25 Sep: ⌘K dropped the refusal).
  return {pinned:pins.data.includes(threadId),pin:next=>void pinThread(projectId,threadId,next)
    .catch(failure=>onError?.(next?t('Couldn’t pin it. {why}',{why:failure instanceof Error?failure.message:String(failure)}):t('Couldn’t unpin it. {why}',{why:failure instanceof Error?failure.message:String(failure)})))
    .finally(()=>client.invalidateQueries({queryKey:['conversation-pins',projectId]}))};
}

/** Without a query: everything except the plain conversation list, which is long and already in the sidebar. With a query:
 * every entry whose label contains it, then a row that searches saved conversations for the same words. */
export function filterPalette(items:PaletteItem[],query:string,state:{ready:boolean},search:(query:string)=>void):PaletteItem[]{
  const words=query.trim().toLowerCase();
  if(!words)return items.filter(item=>!(item.group==='Go to'&&item.conversation)&&item.id!=='act:overview');
  // A conversation already listed under Needs you or Running isn't listed again under Go to.
  const listed=new Set<string>();
  const matches=items.filter(item=>{
    if(!item.label.toLowerCase().includes(words))return false;
    if(item.conversation&&item.group==='Go to'&&listed.has(item.conversation))return false;
    if(item.conversation)listed.add(item.conversation);
    return true;
  });
  if(state.ready)matches.push({id:'search',group:'Search',label:t('Search all threads for “{query}”',{query:query.trim()}),detail:matches.length?undefined:t('What was said, not just names'),keys:matches.length?'⌘↵':'↵',icon:Search,run:()=>search(query.trim())});
  return matches;
}

function Highlight({text,query}:{text:string;query:string}){
  const words=query.trim();
  const at=words?text.toLowerCase().indexOf(words.toLowerCase()):-1;
  if(at<0)return <>{text}</>;
  return <>{text.slice(0,at)}<mark>{text.slice(at,at+words.length)}</mark>{text.slice(at+words.length)}</>;
}

/** Whether the query reads as the open thread's name: the start of the name or of one of its words, two letters or more,
 * so "a" doesn't get "You're in Fix auth". */
export function namesCurrent(current:string|undefined,query:string):current is string{
  const q=query.trim().toLowerCase();
  return !!current&&q.length>=2&&[current.toLowerCase(),...current.toLowerCase().split(/\s+/)].some(word=>word.startsWith(q));
}
export function CommandPalette({open,onOpenChange,items,ready,current,onSearch}:{open:boolean;onOpenChange:(open:boolean)=>void;items:PaletteItem[];ready:boolean;current?:string;onSearch:(query:string)=>void}){
  const [query,setQuery]=useState(''),[active,setActive]=useState(0);
  const list=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(open){setQuery('');setActive(0);}},[open]);
  const shown=useMemo(()=>filterPalette(items,query,{ready},onSearch),[items,query,ready,onSearch]);
  const index=Math.min(active,Math.max(0,shown.length-1));
  useEffect(()=>{list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest'});},[index,shown.length]);
  function choose(item:PaletteItem|undefined){
    if(!item)return;
    onOpenChange(false);
    // Let the dialog close and return focus before the action moves it somewhere else.
    requestAnimationFrame(()=>item.run());
  }
  function key(event:React.KeyboardEvent){
    if(event.key==='ArrowDown'){event.preventDefault();if(shown.length)setActive((index+1)%shown.length);}
    else if(event.key==='ArrowUp'){event.preventDefault();if(shown.length)setActive((index-1+shown.length)%shown.length);}
    else if(event.key==='Home'&&shown.length){event.preventDefault();setActive(0);}
    else if(event.key==='End'&&shown.length){event.preventDefault();setActive(shown.length-1);}
    // ⌘↵ searches every thread for what you typed, wherever the highlight is.
    else if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();choose(shown.find(item=>item.group==='Search'));}
    else if(event.key==='Enter'){event.preventDefault();choose(shown[index]);}
  }
  // Closing hands focus back to where it was when ⌘K opened, or the composer: Radix's own return found nothing to go
  // back to when the palette opened from a key or a control that re-rendered, and focus fell to the page (RC review, 9 Oct).
  // Read while rendering the opening, before the dialog's input takes focus.
  const before=useRef<HTMLElement|null>(null),wasOpen=useRef(false);
  if(open&&!wasOpen.current&&typeof document!=='undefined'){const at=document.activeElement;before.current=at instanceof HTMLElement&&at!==document.body?at:null;}
  wasOpen.current=open;
  function closeFocus(event:Event){
    event.preventDefault();
    const back=before.current?.isConnected?before.current:document.querySelector<HTMLElement>('[data-testid="message-input"]');
    back?.focus({preventScroll:true});
  }
  let group:PaletteGroup|undefined;
  // Nothing matched but the search row: say so first, then offer the full search.
  const nothing=ready&&shown.length===1&&shown[0].group==='Search';
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="dervo-palette" showCloseButton={false} aria-describedby="dervo-palette-help" onOpenAutoFocus={event=>{event.preventDefault();document.getElementById('dervo-palette-input')?.focus();}} onCloseAutoFocus={closeFocus}>
      <DialogTitle className="sr-only">{t('Search or jump to')}</DialogTitle>
      <DialogDescription id="dervo-palette-help" className="sr-only">{t('Type to filter. Arrow keys move, Enter opens, Escape closes.')}</DialogDescription>
      <div className="dervo-palette-field">
        <Search size={17} aria-hidden="true"/>
        <input id="dervo-palette-input" role="combobox" aria-expanded={shown.length>0} aria-controls="dervo-palette-list" aria-autocomplete="list" autoComplete="off" spellCheck={false}
          aria-activedescendant={shown[index]?`dervo-palette-${index}`:undefined} aria-label={t('Search or jump to')} placeholder={t('Search or jump to')}
          value={query} onChange={event=>{setQuery(event.target.value);setActive(0);}} onKeyDown={key}/>
        <span className="dervo-kbd" aria-hidden="true">esc</span>
      </div>
      {/* The empty lines sit outside the listbox: a listbox may hold only options (axe aria-required-children). */}
      {/* The open thread isn't listed, so typing its own name said no thread had it (polisher, 8 Oct). */}
      {nothing&&<p className="dervo-palette-empty" role="status">{namesCurrent(current,query)
        ?t('You’re in {name}. Nothing else is called “{query}”.',{name:current,query:query.trim()})
        :t('No thread, screen or action is called “{query}”.',{query:query.trim()})}</p>}
      <div ref={list} id="dervo-palette-list" role="listbox" aria-label={t('Results')} className="dervo-palette-list" hidden={!shown.length}>
        {shown.map((item,i)=>{
          const heading=item.group!==group&&!nothing?item.group:null;group=item.group;
          const Icon=item.icon;
          return <div key={item.id} role="presentation">
            {heading&&<div role="presentation" className="dervo-palette-group">{GROUP_LABELS[heading]}</div>}
            <div id={`dervo-palette-${i}`} role="option" aria-selected={i===index} className="dervo-palette-item" onMouseMove={()=>{if(i!==index)setActive(i);}} onClick={()=>choose(item)}>
              {typeof Icon==='string'?<StatusGlyph state={Icon}/>:<Icon size={16} strokeWidth={1.5}/>}
              <span className="dervo-palette-label"><Highlight text={item.label} query={item.group==='Search'?'':query}/></span>
              {item.detail&&<span className="dervo-palette-detail">{item.detail}</span>}
              {item.keys&&<span className="dervo-palette-keys" aria-hidden="true">{item.keys}</span>}
            </div>
          </div>;
        })}
      </div>
      {!shown.length&&<p className="dervo-palette-empty" role="status">{ready?t('No thread, screen or action is called “{query}”.',{query:query.trim()}):t('Nothing matches. Choose a project with a connected account to search threads.')}</p>}
      <div className="dervo-palette-foot" aria-hidden="true"><span><b>↑↓</b>{t('Move')}</span><span><b>↵</b>{t('Open')}</span><span><b>esc</b>{t('Close')}</span></div>
    </DialogContent>
  </Dialog>;
}

/** ⌘K opens and closes the palette; ⌘1 and ⌘2 go to Catch up and Knowledge. Ctrl works too. */
export function usePaletteShortcuts({toggle,catchUp,knowledge,ready}:{toggle:()=>void;catchUp:()=>void;knowledge:()=>void;ready:boolean}){
  const latest=useRef({toggle,catchUp,knowledge,ready});latest.current={toggle,catchUp,knowledge,ready};
  useEffect(()=>{
    function onKey(event:KeyboardEvent){
      if(event.defaultPrevented||event.isComposing||event.repeat||isProjectTerminalKey(event)||!(event.metaKey||event.ctrlKey)||event.altKey||event.shiftKey)return;
      const key=event.key.toLowerCase();
      if(key==='k'){event.preventDefault();latest.current.toggle();}
      else if(key==='1'&&latest.current.ready){event.preventDefault();latest.current.catchUp();}
      else if(key==='2'&&latest.current.ready){event.preventDefault();latest.current.knowledge();}
    }
    window.addEventListener('keydown',onKey);
    return()=>window.removeEventListener('keydown',onKey);
  },[]);
}
