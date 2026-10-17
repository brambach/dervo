import {useEffect,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {useQuery,useQueryClient,type QueryClient} from '@tanstack/react-query';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuLabel,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local,type Conversation,type UsageAccount} from './api';
import {SettingRow,SettingsSection,Segmented,TextField,Tag,nextIndex} from './DervoControls';
import {StatusGlyph} from './StatusGlyph';
import {RetireNote,ThinkingLevels,modelProblem,useCatalog} from './ModelPicker';
import {claudeDefaultOnThisMac,useClaudeVersion} from './AddProject';
import {HEAVY_EFFORTS,PROVIDER_NAMES,heavyLine,planLabel,claudeModelsFor,claudeOlderFor,accountCaption,accountsFrom,asksBeforeFable,claudeLine,codexLine,defaultEffort,effortLabel,effortsFor,
  fableCreditLabel,TEAM_FABLE_NOTICE,isFable,modelLabel,providerDefaultLine,thinkingLine,CODEX_STARTER,leavingLine,retirementFor,type Account,type Catalog,type Efforts,type Profile} from './models';
import {useProjectPermission,useSaver,useSettingsRefresh,type SettingsProject} from './projectSettingsData';
import {FieldError} from './SettingsBits';
import {t} from '@/i18n';

/** Account and model (settings-model.html): the account and model new threads here start with, their thinking, what
 * happens when the account runs low, and the threads that are on another account. Each thread can still switch from its
 * own picker; nothing here moves a thread except "Use project default". */

type ModelRow={id:string;label:string;line:string;tag?:string;unavailable?:string};
const CHEV=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4.5 6.5L8 10l3.5-3.5"/></svg>;
const CHEV_R=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;
const TICK=<svg className="dv-pk-tick" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>;
const PLUS=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;

/** Settings' Thinking line names the model: "High: … for Opus 5.5." A Claude Default already names it, and says whose level it is. */
const thinkingRowLine=(line:string,named:boolean,modelName:string)=>named?line:t('{line} for {model}.',{line:line.replace(/\.$/,''),model:modelName});
/** The project's Thinking is optional (16.4): without its own it uses your default, and says which. */
export function projectThinkingLine(efforts:Pick<Efforts,'project'|'personal'>,resolved:string|undefined,model:string,provider:string,modelName:string):string{
  if(efforts.project)return thinkingRowLine(thinkingLine(efforts.project,resolved,model,provider),false,modelName);
  if(efforts.personal)return t('Using your default ({level}).',{level:effortLabel(efforts.personal)});
  return `${t('No level here or as your default.')} ${thinkingRowLine(providerDefaultLine(provider,model,resolved),provider==='claude',modelName)}`;
}
/** Your default for this account and model, in every project that doesn't set its own. */
export function personalThinkingLine(personal:string|null,resolved:string|undefined,model:string,provider:string,modelName:string,account:string):string{
  const level=personal?t('{line} on {account}.',{line:thinkingRowLine(thinkingLine(personal,resolved,model,provider),false,modelName).replace(/\.$/,''),account})
    :thinkingRowLine(providerDefaultLine(provider,model,resolved),provider==='claude',modelName);
  return t('{level} Every project that doesn’t set its own uses it. Work and personal accounts keep separate defaults.',{level});
}
const remembered=(projectId:string,profileId:string)=>{try{return localStorage.getItem(`dervo-project-model:${projectId}:${profileId}`);}catch{return null;}};
const remember=(projectId:string,profileId:string,model:string)=>{try{localStorage.setItem(`dervo-project-model:${projectId}:${profileId}`,model);}catch{/* a preference, not a record */}};

/** The model a project's new threads start on when they move to ``account``: the one you last chose there, the same model
 * on the same tool, the first in the account's Codex list, or Claude's default on this Mac. Settings > Accounts' "Use for
 * new threads" and this page's account menu pick it the same way. */
export async function modelForAccount(client:QueryClient,project:{id:string;model:string},account:{id:string;provider:string},ownProvider?:string){
  let model=remembered(project.id,account.id);
  if(!model&&account.provider===ownProvider)model=project.model;
  if(!model&&account.provider==='codex'){
    const cached=client.getQueryData<Catalog>(['codex-models',account.id])??await local<Catalog>('model_status',{profileId:account.id}).catch(()=>undefined);
    model=cached?.state==='catalog'?cached.models[0]?.model??null:null;
  }
  return model??(account.provider==='claude'?await claudeDefaultOnThisMac():CODEX_STARTER);
}

function rowsFor(account:Account|undefined,model:string,catalog:Catalog|undefined,claudeVersion:string|null):ModelRow[]{
  if(!account)return [];
  if(account.provider==='claude'){
    const creditLabel=fableCreditLabel(account.profile.plan);
    return claudeModelsFor(claudeVersion).map(m=>({id:m.id,label:m.label,line:claudeLine(m.id,account.profile.plan),tag:isFable(m.id)?creditLabel:undefined}));
  }
  const rows:ModelRow[]=catalog?.state==='catalog'?catalog.models.map(m=>({id:m.model,label:m.displayName??modelLabel('codex',m.model),line:codexLine(m)})):[];
  const problem=modelProblem(account,{profile_id:account.id,provider:'codex',model},catalog);
  if(problem)rows.unshift({id:model,label:modelLabel('codex',model),line:problem.replace(t('messages here can’t start'),t('new threads can’t start')),unavailable:problem});
  return rows;
}

export function AccountAndModelPage({project,profiles,usage,conversations,onOpenThread,onAddAccount}:{project:SettingsProject;profiles:Profile[];usage?:UsageAccount[];
  conversations:Conversation[];onOpenThread:(id:string)=>void;onAddAccount:()=>void}){
  const client=useQueryClient();
  const accounts=accountsFrom(profiles,usage,Date.now(),project.context??'personal');
  const own=accounts.find(a=>a.id===project.profile_id);
  const permission=useProjectPermission(project.id);
  const saver=useSaver(),refresh=useSettingsRefresh(project.id);
  const catalog=useCatalog(own?.profile,true);
  const [asking,setAsking]=useState<string|null>(null),[more,setMore]=useState(false);
  const agents=useQuery({queryKey:['collab-agents'],staleTime:30000,queryFn:()=>local<{name:string;channel_id:string}[]>('collab_agents',{})});
  const claudeVersion=useClaudeVersion(own?.provider==='claude');
  const rows=rowsFor(own,project.model,catalog.data,claudeVersion),older=claudeOlderFor(claudeVersion);
  const listed=rows.some(r=>r.id===project.model)||older.some(m=>m.id===project.model);
  const other=own?accounts.find(a=>a.provider!==own.provider):undefined;
  const levels=own?effortsFor(own.provider,project.model,catalog.data?.models):[];
  const efforts=(permission.data as {efforts?:Efforts}|undefined)?.efforts;
  const heavy=levels.filter(l=>HEAVY_EFFORTS.includes(l)),effort=efforts?.project??null,personal=efforts?.personal??null;
  const [morePersonal,setMorePersonal]=useState(false);
  // A lone heavy level shows in the row with its note under it, instead of a More button (Harry, 27 Sep).
  const shown=more||heavy.length===1,shownPersonal=morePersonal||heavy.length===1;
  const resolved=own?defaultEffort(own.provider,project.model,catalog.data?.models):undefined;
  const modelName=own?modelLabel(own.provider,project.model,catalog.data?.models):project.model;
  const autoSwitch=permission.data?.route?.auto_switch===true;
  const agentThreads=new Set((agents.data??[]).map(a=>a.channel_id));
  const elsewhere=conversations.filter(c=>c.profile_id!==project.profile_id&&!agentThreads.has(c.id));
  // The project's default, and its threads on this account, on a model leaving Codex (GPT-5.5, 14 Oct). Each moves to
  // the named replacement only when you click; helper agents keep theirs, as they do for an account change.
  const models=catalog.data?.state==='catalog'?catalog.data.models:undefined;
  const leaving=own?.provider==='codex'?retirementFor(project.model,models):null;
  const retiring=own?.provider==='codex'?conversations.filter(c=>c.profile_id===own.id&&!agentThreads.has(c.id)&&retirementFor(c.model,models)):[];
  async function moveThreads(threads:Conversation[]){
    for(const thread of threads){
      const to=retirementFor(thread.model,models)!.successor;
      await local('conversation_model_set',{projectId:project.id,channelId:thread.id,expectedModel:thread.model,model:to});
    }
    await client.invalidateQueries({queryKey:['local-conversations',project.id]});
  }

  async function afterProject(){await client.invalidateQueries({queryKey:['local-projects']});}
  async function setAccount(account:Account){
    if(account.id===project.profile_id)return;
    const chosen=await modelForAccount(client,project,account,own?.provider);
    await saver.save('new','account',async()=>{
      await local('project_profile_set',{projectId:project.id,expectedProfileId:project.profile_id,profileId:account.id,expectedModel:project.model,model:chosen});
      await afterProject();await refresh();
    });
  }
  async function setModel(id:string,confirmed=false){
    if(!own||id===project.model)return;
    if(!confirmed&&asksBeforeFable(own.profile,id)){setAsking(id);return;}
    setAsking(null);
    await saver.save('new','model',async()=>{
      if(confirmed)await local('profile_fable_ack',{profileId:own.id});
      await local('project_model_set',{projectId:project.id,expectedModel:project.model,model:id});
      remember(project.id,own.id,id);
      await Promise.all([afterProject(),client.invalidateQueries({queryKey:['local-profiles']})]);await refresh();
    });
  }
  const noAccount=!own;
  return <>
    <SettingsSection id="new" title={t('New threads')} caption={saver.caption('new',t('Threads you have keep theirs'))}>
      <SettingRow name={t('Account')} line={own?t('{identity}. New threads here sign in with it.',{identity:own.identity}):undefined}
        why={noAccount?<span className="dv-inline-why"><StatusGlyph state="needs" accent/>{t('New threads can’t start until you pick an account.')}</span>:undefined}
        end={<AccountMenu accounts={accounts} current={own} disabled={saver.saving('new')} onPick={account=>void setAccount(account)} onAdd={onAddAccount}/>}/>
      <FieldError message={saver.error('account')}/>
      {own&&<div className="dv-set"><div><p className="dv-set-name">{t('Model')}</p><p className="dv-set-line">{t('From {provider}’s own list for {name}. Each thread can pick another from its model picker.',{provider:PROVIDER_NAMES[own.provider],name:own.name})}</p></div>
        <div className="dv-set-below">
          {leaving&&models&&<RetireNote retirement={leaving} catalog={models} busy={saver.saving('new')}
            stays={t('New threads here start on it until you change it.')} gone={t('New threads here can’t start until you change it.')}
            move={model=>t('Use {model} for new threads',{model})} onMove={id=>void setModel(id)}/>}
          {own.provider==='codex'&&catalog.loading&&!rows.length?<p className="dv-sec-note" role="status">{t('Loading {name}’s models…',{name:own.name})}</p>
            :own.provider==='codex'&&catalog.error&&!rows.length?<FieldError message={t('Couldn’t load {name}’s models: {error}',{name:own.name,error:catalog.error})} onRetry={()=>void catalog.retry()}/>
            :<ModelChoices rows={[...rows,...(!listed&&project.model?[{id:project.model,label:modelLabel(own.provider,project.model),line:t('A model ID you chose. Checked when a task starts.')}]:[])]} value={project.model}
              asking={asking} account={own} disabled={saver.saving('new')} onPick={id=>void setModel(id)} onConfirm={id=>void setModel(id,true)} onCancel={()=>setAsking(null)}/>}
          <details className="dv-disclose dv-more-models"><summary>{CHEV_R}{t('Other models')}</summary>
            <div className="dv-disclose-body">
              {own.provider==='claude'&&<ModelChoices rows={older.map(m=>({id:m.id,label:m.label,line:claudeLine(m.id,own.profile.plan),tag:isFable(m.id)?fableCreditLabel(own.profile.plan):undefined}))}
                value={project.model} asking={asking} account={own} disabled={saver.saving('new')} onPick={id=>void setModel(id)} onConfirm={id=>void setModel(id,true)} onCancel={()=>setAsking(null)}/>}
              <label className="dv-model-id"><span className="dv-set-line">{t('A model ID. Checked when a task starts. Dervo won’t substitute another model.')}</span>
                <TextField label={t('Model ID')} value={listed?'':project.model} placeholder={own.provider==='claude'?'claude-…':'gpt-…'}
                  onCommit={next=>{const id=next.trim();if(!id)return;if(id!==next)return t('Use a model ID with no spaces around it.');void setModel(id);}}/></label>
            </div>
          </details>
          <FieldError message={saver.error('model')}/>
        </div></div>}
      {own&&levels.length>0&&<SettingRow name={t('Thinking')} line={projectThinkingLine({project:effort,personal},resolved,project.model,own.provider,modelName)}
        end={effort?<button type="button" className="dv-btn dv-btn-quiet" disabled={saver.saving('new')}
          onClick={()=>void saver.save('new','effort',async()=>refresh(await local('coding_effort_set',{projectId:project.id,channelId:null,effort:null,level:'project'})))}>{t('Use my default')}</button>:undefined}
        below={<div className="dv-thinking"><ThinkingLevels size="regular" value={effort} light={levels.filter(l=>!HEAVY_EFFORTS.includes(l))} heavy={shown||(effort&&heavy.includes(effort))?heavy:[]}
            more={heavy.length>0&&!shown&&!(effort&&heavy.includes(effort))} disabled={!efforts||saver.saving('new')} onMore={()=>setMore(true)}
            onPick={level=>void saver.save('new','effort',async()=>refresh(await local('coding_effort_set',{projectId:project.id,channelId:null,effort:level,level:'project'})))}/>
          {shown&&!(effort&&heavy.includes(effort))&&<p className="dv-set-line">{heavyLine(heavy)}</p>}
          <FieldError message={saver.error('effort')}/></div>}/>}
      {own&&levels.length>0&&<SettingRow name={t('Your default')} line={personalThinkingLine(personal,resolved,project.model,own.provider,modelName,own.name)}
        below={<div className="dv-thinking"><ThinkingLevels size="regular" value={personal} light={levels.filter(l=>!HEAVY_EFFORTS.includes(l))} heavy={shownPersonal||(personal&&heavy.includes(personal))?heavy:[]}
            more={heavy.length>0&&!shownPersonal&&!(personal&&heavy.includes(personal))} disabled={!efforts||saver.saving('new')} onMore={()=>setMorePersonal(true)}
            onPick={level=>void saver.save('new','personal',async()=>refresh(await local('coding_effort_set',{projectId:project.id,channelId:null,effort:level,level:'personal'})))}/>
          <FieldError message={saver.error('personal')}/></div>}/>}
    </SettingsSection>
    {own&&<SettingsSection id="fail" title={t('Running low')} caption={saver.caption('fail')}>
      <SettingRow name={t('When {name} runs low or a run fails',{name:own.name})}
        line={other?t('Dervo offers your {name} account. It never moves a thread to your other {provider} account, because providers don’t allow switching accounts to get round limits.',{name:other.name,provider:PROVIDER_NAMES[own.provider]})
          :t('Add a {other} account in Connected accounts so Dervo has somewhere to go. It never moves a thread to your other {provider} account, because providers don’t allow switching accounts to get round limits.',{other:own.provider==='claude'?'ChatGPT':'Claude',provider:PROVIDER_NAMES[own.provider]})}
        below={<><Segmented label={t('If an account runs low or fails')} value={autoSwitch?'switch':'suggest'} disabled={!other||!permission.data||saver.saving('fail')}
          onChange={value=>void saver.save('fail','route',async()=>{await local('coding_route_set',{projectId:project.id,channelId:null,autoSwitch:value==='switch'});await refresh();})}
          options={[{value:'suggest',label:other?t('Suggest {provider}',{provider:PROVIDER_NAMES[other.provider]}):t('Suggest the other provider')},{value:'switch',label:other?t('Switch to {provider} by itself',{provider:PROVIDER_NAMES[other.provider]}):t('Switch to it by itself')}]}/>
          <FieldError message={saver.error('route')}/></>}/>
    </SettingsSection>}
    <SettingsSection id="also" title={t('Also')}>
      <SettingRow name={t('Auto')} disabled line={t('Picks the model and thinking for each message, on this account only. Coming in a later beta.')} end={<Tag>{t('Later')}</Tag>}/>
    </SettingsSection>
    {retiring.length>0&&own&&<SettingsSection id="leaving" title={t('Threads on a model leaving Codex')} caption={saver.caption('leaving',t('{count} threads',{count:retiring.length}))}>
      <div className="dv-sheet dv-other-threads">{retiring.map(thread=>{
        const out=retirementFor(thread.model,models)!,to=modelLabel('codex',out.successor,models);
        const offered=!models||models.some(m=>m.model===out.successor);
        return <div key={thread.id} className="dv-row">
          <StatusGlyph state="needs"/>
          <div><button type="button" className="dv-row-name dv-linkish-plain" onClick={()=>onOpenThread(thread.id)}>{thread.name}</button>
            <div className="dv-row-line">{modelLabel('codex',thread.model,models)} · {leavingLine(out)}</div>
            <FieldError message={saver.error(`move:${thread.id}`)}/></div>
          <div className="dv-row-end">{offered&&<button type="button" className="dv-btn dv-btn-quiet" disabled={saver.saving('leaving')}
            onClick={()=>void saver.save('leaving',`move:${thread.id}`,()=>moveThreads([thread]))}>{t('Move to {model}',{model:to})}</button>}</div>
        </div>;})}
      </div>
      {retiring.length>1&&<div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={saver.saving('leaving')}
        onClick={()=>void saver.save('leaving','move-all',()=>moveThreads(retiring.filter(c=>{const s=retirementFor(c.model,models)!.successor;return !models||models.some(m=>m.model===s);})))}>
        {t('Move all {count}',{count:retiring.length})}</button></div>}
      <FieldError message={saver.error('move-all')}/>
    </SettingsSection>}
    {elsewhere.length>0&&<SettingsSection id="others" title={t('Threads on another account')} caption={saver.caption('others',`${elsewhere.length} ${elsewhere.length===1?'thread':'threads'}`)}>
      <div className="dv-sheet dv-other-threads">{elsewhere.map(thread=>{
        const account=accounts.find(a=>a.id===thread.profile_id);
        return <div key={thread.id} className="dv-row">
          <StatusGlyph state="idle"/>
          <div><button type="button" className="dv-row-name dv-linkish-plain" onClick={()=>onOpenThread(thread.id)}>{thread.name}</button>
            <div className="dv-row-line">{account?.name??t('An account not on this Mac')} · {account?modelLabel(account.provider,thread.model):thread.model}</div>
            <FieldError message={saver.error(`thread:${thread.id}`)}/></div>
          <div className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" disabled={saver.saving('others')}
            onClick={()=>void saver.save('others',`thread:${thread.id}`,async()=>{
              await local('conversation_profile_set',{projectId:project.id,channelId:thread.id,expectedProfileId:thread.profile_id,profileId:project.profile_id,expectedModel:thread.model,model:project.model});
              await client.invalidateQueries({queryKey:['local-conversations',project.id]});
            })}>{t('Use project default')}</button></div>
        </div>;})}
      </div>
    </SettingsSection>}
  </>;
}

/** The account new threads use, picked by name from a menu grouped by provider with what's left on each. */
function AccountMenu({accounts,current,disabled,onPick,onAdd}:{accounts:Account[];current?:Account;disabled?:boolean;onPick:(account:Account)=>void;onAdd:()=>void}){
  return <DropdownMenu>
    <DropdownMenuTrigger asChild disabled={disabled}>
      <button type="button" className="dv-select" aria-label={t('Account for new threads')}>
        <span className="dv-select-value">{current?<>{current.name} <span className="dv-sub">{current.provider==='claude'?'Claude':'Codex'}{current.plan?` ${planLabel(current.plan)}`:''}</span></>:t('Not on this Mac')}</span>{CHEV}
      </button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="dv-acct-menu" aria-label={t('Account for new threads')}>
      {!accounts.length&&<p className="dv-acct-empty">{t('No accounts yet.')}</p>}
      {(['claude','codex'] as const).map(provider=>{const group=accounts.filter(a=>a.provider===provider);return group.length?<div key={provider} role="group" aria-label={provider==='claude'?'Claude':'Codex'}>
        <DropdownMenuLabel>{provider==='claude'?'Claude':'Codex'}</DropdownMenuLabel>
        {group.map(account=><DropdownMenuItem key={account.id} className="dv-acct-item" data-current={account.id===current?.id||undefined} onSelect={()=>onPick(account)}>
          <b>{account.name}</b><small>{accountCaption(account)}</small>
          {account.left!==null&&<span className="dv-pk-bar" aria-hidden="true"><i style={{width:`${account.left}%`}}/></span>}
          {account.id===current?.id&&TICK}
        </DropdownMenuItem>)}
      </div>:null;})}
      <DropdownMenuSeparator/>
      <DropdownMenuItem onSelect={onAdd}>{PLUS}{t('Add an account')}</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}

/** The models, one row each with its line; a radio group. Fable on a paid-credits plan opens in place and asks once. */
function ModelChoices({rows,value,asking,account,disabled,onPick,onConfirm,onCancel}:{rows:ModelRow[];value:string;asking:string|null;account:Account;disabled?:boolean;
  onPick:(id:string)=>void;onConfirm:(id:string)=>void;onCancel:()=>void}){
  const refs=useRef<(HTMLDivElement|null)[]>([]),confirm=useRef<HTMLButtonElement|null>(null);
  const asked=rows.findIndex(r=>r.id===asking);
  // The question takes focus when it opens (interactions.md: "Focus to Use Fable 5.1").
  useEffect(()=>{if(asked>=0)requestAnimationFrame(()=>confirm.current?.focus());},[asked]);
  function cancel(at:number){onCancel();refs.current[at]?.focus();}
  const current=rows.findIndex(r=>r.id===value&&!r.unavailable);
  const stop=current>=0?current:Math.max(0,rows.findIndex(r=>!r.unavailable));
  function keys(event:KeyboardEvent,at:number){
    if(event.target!==event.currentTarget)return;
    if(event.key===' '||event.key==='Enter'){event.preventDefault();if(!rows[at].unavailable&&!disabled)onPick(rows[at].id);return;}
    const next=nextIndex(event.key,at,rows.map(()=>false));
    if(next>=0){event.preventDefault();refs.current[next]?.focus();}
  }
  return <div className="dv-choices dv-compact" role="radiogroup" aria-label={t('Model for new threads')}>
    {rows.map((row,at)=>{const checked=row.id===value&&!row.unavailable,open=asking===row.id;const body:ReactNode=open?<div className="dv-c-body" onClick={e=>e.stopPropagation()}
      onKeyDown={e=>{e.stopPropagation();if(e.key==='Escape'){e.preventDefault();cancel(at);}}}>
      <div className="dv-confirm dv-project-confirm"><p className="dv-set-name">{t('Use {model} for new threads?',{model:row.label})}</p>
        <p className="dv-set-line">{account.profile.plan==='team'?TEAM_FABLE_NOTICE:t('Each message costs money on top of your {plan} plan.',{plan:account.plan||'Pro'})} {t('Dervo asks once for {name}.',{name:account.name})}</p>
        <div className="dv-actions"><button type="button" className="dv-btn dv-btn-primary dv-btn-sm" ref={confirm} disabled={disabled} onClick={()=>onConfirm(row.id)}>{t('Use {model}',{model:row.label})}</button>
          <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>cancel(at)}>{t('Cancel')}</button></div></div></div>:null;
      return <div key={row.id} ref={el=>{refs.current[at]=el;}} role="radio" aria-checked={checked} aria-disabled={row.unavailable||disabled?true:undefined} tabIndex={at===stop?0:-1}
        className={`dv-choice${open?' dv-open':''}`} onClick={()=>{if(!row.unavailable&&!disabled)onPick(row.id);}} onKeyDown={event=>keys(event,at)}>
        <span className="dv-dot" aria-hidden="true"/>
        <div><p className="dv-c-name">{row.label}{row.tag&&<Tag>{row.tag}</Tag>}</p><p className="dv-c-line">{row.line}</p></div>
        <span className="dv-c-end"/>
        {body}
      </div>;})}
  </div>;
}
