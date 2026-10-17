import {useEffect,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuLabel,DropdownMenuRadioGroup,DropdownMenuRadioItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {t} from '@/i18n';

/** The controls every redesigned settings page is built from (design sweep, 19 Sep). The look is the dv- classes in
 * DervoComponents.css; these add only the behaviour: which keys do what, and what saves when. Each control changes the
 * moment it's used and hands the new value to its caller, which saves it. */

/** A switch. Click, Space or Return flips it. */
export function Switch({checked,onChange,label,disabled,describedBy}:{checked:boolean;onChange:(next:boolean)=>void;label:string;disabled?:boolean;describedBy?:string}){
  return <button type="button" role="switch" className="dv-switch" aria-checked={checked} aria-label={label} aria-describedby={describedBy} disabled={disabled}
    onClick={()=>onChange(!checked)}/>;
}

export type Option<T extends string>={value:T;label:ReactNode;disabled?:boolean;title?:string};

/** Moves along a row of options by arrow keys, skipping disabled ones: the index to land on, or -1 for a key that isn't
 * a move. Home and End jump to the ends. */
export function nextIndex(key:string,from:number,disabled:boolean[]):number{
  const count=disabled.length;
  if(!count)return -1;
  const step=key==='ArrowRight'||key==='ArrowDown'?1:key==='ArrowLeft'||key==='ArrowUp'?-1:0;
  if(key==='Home'||key==='End'){
    const order=[...Array(count).keys()];
    return (key==='Home'?order:order.reverse()).find(i=>!disabled[i])??-1;
  }
  if(!step)return -1;
  for(let n=1;n<=count;n++){const i=(from+step*n+count*n)%count;if(!disabled[i])return i;}
  return -1;
}

/** A segmented control: one of a few, all in view. A radio group, so it's one tab stop; arrows move and choose, as
 * macOS and the ARIA pattern do. */
export function Segmented<T extends string>({value,options,onChange,label,disabled}:{value:T;options:Option<T>[];onChange:(next:T)=>void;label:string;disabled?:boolean}){
  const buttons=useRef<(HTMLButtonElement|null)[]>([]);
  const off=options.map(option=>!!disabled||!!option.disabled);
  const current=options.findIndex(option=>option.value===value);
  const stop=current>=0&&!off[current]?current:off.indexOf(false);
  function keys(event:KeyboardEvent,index:number){
    const next=nextIndex(event.key,index,off);
    if(next<0)return;
    event.preventDefault();
    buttons.current[next]?.focus();
    if(options[next].value!==value)onChange(options[next].value);
  }
  return <div className="dv-seg" role="radiogroup" aria-label={label}>
    {options.map((option,index)=><button key={option.value} ref={element=>{buttons.current[index]=element;}} type="button" role="radio"
      aria-checked={option.value===value} tabIndex={index===stop?0:-1} disabled={off[index]} title={option.title}
      onClick={()=>{if(option.value!==value)onChange(option.value);}} onKeyDown={event=>keys(event,index)}>{option.label}</button>)}
  </div>;
}

/** A row in a group of choice rows: name, one line, and an optional trailing note. `body` is what the chosen row shows
 * when it lifts open as the page's card. */
export type Choice<T extends string>={value:T;name:ReactNode;line?:ReactNode;end?:ReactNode;disabled?:boolean;body?:ReactNode};

/** Choice rows: one of several, each with its line. A radio group: one tab stop, arrows move focus, Space or Return
 * chooses (the rows can carry consequences, so moving alone doesn't change anything). */
/** `lift` names the row that lifts open when it isn't the chosen one: a level being confirmed before it's saved. */
export function ChoiceRows<T extends string>({value,choices,onChange,label,compact,open,lift}:{value:T|null;choices:Choice<T>[];onChange:(next:T)=>void;label:string;compact?:boolean;open?:boolean;lift?:T|null}){
  const rows=useRef<(HTMLDivElement|null)[]>([]);
  const off=choices.map(choice=>!!choice.disabled);
  const current=choices.findIndex(choice=>choice.value===value);
  const stop=current>=0?current:off.indexOf(false);
  function choose(index:number){if(!off[index]&&choices[index].value!==value)onChange(choices[index].value);}
  function keys(event:KeyboardEvent,index:number){
    if(event.target!==event.currentTarget)return;
    if(event.key===' '||event.key==='Enter'){event.preventDefault();choose(index);return;}
    const next=nextIndex(event.key,index,choices.map(()=>false));
    if(next<0)return;
    event.preventDefault();
    rows.current[next]?.focus();
  }
  return <div className={`dv-choices${compact?' dv-compact':''}`} role="radiogroup" aria-label={label}>
    {choices.map((choice,index)=>{
      const checked=choice.value===value,lifted=!!open&&choice.value===(lift??value)&&!!choice.body;
      return <div key={choice.value} ref={element=>{rows.current[index]=element;}} role="radio" aria-checked={checked} aria-disabled={choice.disabled||undefined}
        tabIndex={index===stop?0:-1} className={`dv-choice${lifted?' dv-open':''}`} onClick={()=>choose(index)} onKeyDown={event=>keys(event,index)}>
        <span className="dv-dot" aria-hidden="true"/>
        <div><p className="dv-c-name">{choice.name}</p>{choice.line&&<p className="dv-c-line">{choice.line}</p>}</div>
        <span className="dv-c-end">{choice.end}</span>
        {lifted&&<div className="dv-c-body" onClick={event=>event.stopPropagation()} onKeyDown={event=>event.stopPropagation()}>{choice.body}</div>}
      </div>;
    })}
  </div>;
}

export type SelectGroup<T extends string>={label?:string;options:(Option<T>&{sub?:ReactNode})[]};

/** A select: a button that says the current value, with a kit menu under it. The menu opens on click, Return, Space or
 * the down arrow, focuses the current value, and Esc closes it with focus back on the button. `actions` go last, after
 * a rule (for example "Add an account"). */
export function Select<T extends string>({value,groups,onChange,label,disabled,actions,placeholder=t('Choose')}:{value:T|null;groups:SelectGroup<T>[];onChange:(next:T)=>void;label:string;disabled?:boolean;
  actions?:{label:ReactNode;onSelect:()=>void}[];placeholder?:string}){
  const all=groups.flatMap(group=>group.options);
  const chosen=all.find(option=>option.value===value);
  return <DropdownMenu>
    <DropdownMenuTrigger asChild disabled={disabled}>
      <button type="button" className="dv-select" aria-label={label}>
        <span className="dv-select-value">{chosen?chosen.label:placeholder}{chosen?.sub&&<> <span className="dv-sub">{chosen.sub}</span></>}</span>
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4.5 6.5L8 10l3.5-3.5"/></svg>
      </button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end">
      <DropdownMenuRadioGroup value={value??''} onValueChange={next=>onChange(next as T)}>
        {groups.map((group,index)=><div key={group.label??index} role="group" aria-label={group.label}>
          {index>0&&!group.label&&<DropdownMenuSeparator/>}
          {group.label&&<DropdownMenuLabel>{group.label}</DropdownMenuLabel>}
          {group.options.map(option=><DropdownMenuRadioItem key={option.value} value={option.value} disabled={option.disabled}>
            <span>{option.label}{option.sub&&<span className="dv-sub"> {option.sub}</span>}</span>
          </DropdownMenuRadioItem>)}
        </div>)}
      </DropdownMenuRadioGroup>
      {!!actions?.length&&<><DropdownMenuSeparator/>{actions.map((action,index)=><DropdownMenuItem key={index} onSelect={action.onSelect}>{action.label}</DropdownMenuItem>)}</>}
    </DropdownMenuContent>
  </DropdownMenu>;
}

/** A text field that saves on Return or when you leave it, and puts the saved value back on Esc. Esc with nothing to put
 * back is left for the page (in Settings, it goes back). `onCommit` returns an error to show under the field, or
 * nothing; the field keeps what you typed until it's saved. */
export function TextField({value,onCommit,label,placeholder,disabled,className='',describedBy}:{value:string;onCommit:(next:string)=>Promise<string|void>|string|void;label:string;
  placeholder?:string;disabled?:boolean;className?:string;describedBy?:string}){
  const [draft,setDraft]=useState(value),[error,setError]=useState(''),[saving,setSaving]=useState(false);
  const saved=useRef(value);
  useEffect(()=>{if(saved.current!==value){saved.current=value;setDraft(value);}},[value]);
  async function commit(){
    if(draft===saved.current||saving)return;
    setSaving(true);setError('');
    try{const problem=await onCommit(draft);if(problem)setError(problem);else saved.current=draft;}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setSaving(false);}
  }
  return <>
    <input className={`dv-input ${className}`} value={draft} aria-label={label} placeholder={placeholder} disabled={disabled} aria-invalid={error?true:undefined}
      aria-describedby={[describedBy,error?`${label}-error`:''].filter(Boolean).join(' ')||undefined}
      onChange={event=>setDraft(event.target.value)} onBlur={()=>void commit()}
      onKeyDown={event=>{
        if(event.key==='Enter'){event.preventDefault();void commit();}
        if(event.key==='Escape'&&draft!==saved.current){event.preventDefault();setDraft(saved.current);setError('');}
      }}/>
    {error&&<p id={`${label}-error`} role="alert" className="dv-field-note">{error}</p>}
  </>;
}

/** A tag: a short fact about a row ("Paid credits", "Later"). */
export function Tag({children}:{children:ReactNode}){return <span className="dv-tag">{children}</span>;}

/** A setting: its name and one line on the left, its control on the right, or below when it's wide. */
export function SettingRow({name,line,end,below,disabled,why}:{name:ReactNode;line?:ReactNode;end?:ReactNode;below?:ReactNode;disabled?:boolean;why?:ReactNode}){
  return <div className="dv-set" aria-disabled={disabled||undefined}>
    <div><p className="dv-set-name">{name}</p>{line&&<p className="dv-set-line">{line}</p>}{why&&<p className="dv-why">{why}</p>}</div>
    {end&&<div className="dv-set-end">{end}</div>}
    {below&&<div className="dv-set-below">{below}</div>}
  </div>;
}

/** A section of a settings page: its title, an optional caption on the right, then its rows. */
export function SettingsSection({title,caption,children,id}:{title:string;caption?:ReactNode;children:ReactNode;id:string}){
  return <section className="dv-sec" aria-labelledby={id}>
    <div className="dv-sec-head"><h2 className="dv-title" id={id}>{title}</h2>{caption&&<span className="dv-caption">{caption}</span>}</div>
    {children}
  </section>;
}
