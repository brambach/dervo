import {useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {createAgentConversation,local,type CollabAgent,type CollabPolicy,type Conversation,type UsageAccount} from './api';
import {agentLabel as handleLabel} from './collaboration';

/** An agent by name, as its row says it: "Reviewer" for the one review setup made, otherwise the name you gave it. */
const agentLabel=(name:string)=>handleLabel(name).replace(/^@/,'');
import {Select,SettingRow,SettingsSection,Segmented,Switch} from './DervoControls';
import {accountsFrom,claudeDefaultModel,claudeModelsFor,modelLabel,type Account,type Profile} from './models';
import {useClaudeVersion} from './AddProject';
import {useCatalog} from './ModelPicker';
import {useSaver,type SettingsProject} from './projectSettingsData';
import {Disclose,FieldError} from './SettingsBits';
import {t,tr} from '@/i18n';

/** Helper agents (settings-helpers.html): the agents the coding agent here can ask, one row each with the reason one
 * can't run beside the fix, then when the coding agent may ask. Every switch saves; the agent card is the one form with
 * a Save, because its fields only make sense together. */

type Mode='off'|'mentions'|'automatic';
const PLUS=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;
const MODE_LINES:Record<Mode,(names:string,example:string)=>ReactNode>={
  off:()=>t('The coding agent works alone here.'),
  mentions:(_names,example)=>example?tr('Only when you name an agent with @ in your message, like {example}.',{example:<code>@{example}</code>}):t('Only when you name an agent with @ in your message.'),
  automatic:names=>names?t('Whenever it judges help is useful, from {names}.',{names}):t('Whenever it judges help is useful.'),
};
const LIMITS:{key:'maxParallel'|'maxDepth'|'maxCalls';field:'max_parallel'|'max_depth'|'max_calls';name:string;line:string;max:number}[]=[
  {key:'maxParallel',field:'max_parallel',name:t('Agents at once'),line:t('Counting the coding agent.'),max:3},
  {key:'maxDepth',field:'max_depth',name:t('Chain depth'),line:t('Whether an agent it asks may ask another.'),max:2},
  {key:'maxCalls',field:'max_calls',name:t('Asks per message'),line:t('Across every agent.'),max:6},
];

export function HelpersPage({project,profiles,usage,conversations,onAccounts}:{project:SettingsProject;profiles:Profile[];usage?:UsageAccount[];conversations:Conversation[];onAccounts:()=>void}){
  const client=useQueryClient();
  const saver=useSaver();
  const accounts=accountsFrom(profiles,usage,Date.now(),project.context??'personal');
  const policy=useQuery({queryKey:['project-collab-policy',project.id],queryFn:()=>local<CollabPolicy>('collab_policy',{projectId:project.id,channelId:null})});
  const agents=useQuery({queryKey:['collab-agents'],queryFn:()=>local<CollabAgent[]>('collab_agents',{})});
  const [editing,setEditing]=useState<string|null>(null);
  const mine=(agents.data??[]).filter(a=>a.project_id===project.id);
  const shared=conversations.filter(c=>c.visibility==='shared'&&!mine.some(a=>a.channel_id===c.id));
  const p=policy.data;
  const threadName=(id:string)=>conversations.find(c=>c.id===id)?.name??t('its own thread');
  async function setPolicy(change:Partial<{mode:Mode;agents:string[];channels:string[];maxParallel:number;maxDepth:number;maxCalls:number}>,key='policy'){
    if(!p)return;
    // Turning asking on names every shared thread when none was chosen yet, so it has somewhere to ask from.
    const mode=change.mode??p.mode;
    const channels=change.channels??(mode!=='off'&&!p.channels.length?shared.slice(0,32).map(c=>c.id):p.channels);
    await saver.save('ask',key,async()=>{
      await local('collab_policy_set',{projectId:project.id,channelId:null,mode,agents:change.agents??p.agents,channels,
        maxParallel:change.maxParallel??p.limits.max_parallel,maxDepth:change.maxDepth??p.limits.max_depth,maxCalls:change.maxCalls??p.limits.max_calls,review:{enabled:!!p.review?.enabled,reviewer:p.review?.reviewer??null,effort:p.review?.effort??null}});
      await policy.refetch();
    });
  }
  const reason=(name:string)=>p?.unavailable.find(u=>u.name===name)?.reason??mine.find(a=>a.name===name&&a.state!=='ready')?.detail;
  const unlinked=(text?:string)=>!!text&&/(not|n[’']t) linked/i.test(text);
  const names=mine.filter(a=>p?.agents.includes(a.name)).map(a=>`@${a.name}`).join(', ');
  return <>
    <SettingsSection id="agents" title={t('Agents')} caption={saver.caption('agents',mine.length?t('{length} in this project',{length:mine.length}):undefined)}>
      {agents.isError&&<FieldError message={t('The agents couldn’t load.')} onRetry={()=>void agents.refetch()}/>}
      <div className="dv-sheet dv-agents">
        {mine.map(agent=>{const account=accounts.find(a=>a.id===agent.profile_id);const why=reason(agent.name);
          return editing===agent.name?<AgentCard key={agent.name} project={project} accounts={accounts} conversations={conversations} agent={agent}
            onDone={async()=>{setEditing(null);await Promise.all([agents.refetch(),policy.refetch(),client.invalidateQueries({queryKey:['local-conversations',project.id]})]);}}/>
          :<div key={agent.name} className="dv-row">
            <span className="dv-avatar" aria-hidden="true">{agentLabel(agent.name).charAt(0).toUpperCase()}</span>
            <div><div className="dv-row-name">{agentLabel(agent.name)}</div>
              <div className="dv-row-line">{agent.role==='reviewer'?t('Reviews'):t('Works')} · {agent.provider==='claude'?'Claude Code':'Codex'} · {t('{model} on {account}',{model:modelLabel(agent.provider,agent.model),account:account?.name??t('an account not on this Mac')})} · {t('works in {thread}',{thread:threadName(agent.channel_id)})}</div>
              {why&&<p className="dv-why">{t('Can’t run: {why}',{why})}</p>}</div>
            <div className="dv-row-end">{agent.state==='ready'&&!why?<span className="dv-state" data-state="done">{t('Ready')}</span>:unlinked(why)?<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onAccounts}>{t('Link accounts')}</button>:null}
              <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setEditing(agent.name)}>{t('Edit')}</button></div>
          </div>;})}
        {editing===''?<AgentCard project={project} accounts={accounts} conversations={conversations} onDone={async()=>{setEditing(null);await Promise.all([agents.refetch(),policy.refetch(),client.invalidateQueries({queryKey:['local-conversations',project.id]})]);}}/>
          :<button type="button" className="dv-add-line dv-add-agent" onClick={()=>setEditing('')}><span>{PLUS}</span><span className="dv-al">{t('Add an agent')}</span><span className="dv-hint">{t('A reviewer or a worker')}</span></button>}
      </div>
    </SettingsSection>
    <SettingsSection id="ask" title={t('Asking for help')} caption={saver.caption('ask',t('Applies to new requests'))}>
      {policy.isError?<FieldError message={t('The asking policy couldn’t load.')} onRetry={()=>void policy.refetch()}/>:!p?<p className="dv-sec-note" role="status">{t('Loading…')}</p>:<>
        <SettingRow name={t('When the coding agent can ask')} line={MODE_LINES[p.mode](names,mine.find(a=>!a.name.startsWith('review-'))?.name??'')}
          below={<Segmented label={t('When the coding agent can ask')} value={p.mode} disabled={saver.saving('ask')} onChange={mode=>void setPolicy({mode})}
            options={[{value:'off',label:t('Never')},{value:'mentions',label:t('When I @mention one')},{value:'automatic',label:t('When it’s useful')}]}/>}/>
        {mine.map(agent=>{const why=reason(agent.name);const off=agent.state!=='ready'||!!why;
          return <SettingRow key={agent.name} name={agentLabel(agent.name)} disabled={off} line={off?(unlinked(why)?t('Can’t run until its account is linked'):t('Can’t run right now')):p.agents.includes(agent.name)?t('Can be asked'):t('Isn’t asked')}
            end={<Switch label={t('Allow {name}',{name:agentLabel(agent.name)})} checked={p.agents.includes(agent.name)&&!off} disabled={off||saver.saving('ask')}
              onChange={next=>void setPolicy({agents:next?[...p.agents,agent.name]:p.agents.filter(n=>n!==agent.name)})}/>}/>;})}
        <FieldError message={saver.error('policy')}/>
        <Disclose summary={t('Limits')}>
          {LIMITS.map(limit=><SettingRow key={limit.key} name={limit.name} line={limit.line}
            end={<Segmented label={limit.name} value={String(p.limits[limit.field])} disabled={saver.saving('ask')}
              onChange={value=>void setPolicy({[limit.key]:Number(value)})} options={Array.from({length:limit.max},(_,i)=>({value:String(i+1),label:String(i+1)}))}/>}/>)}
          {shared.length>0&&<><p className="dv-label dv-limits-label">{t('Threads that may ask')}</p>
            {shared.map(thread=><SettingRow key={thread.id} name={thread.name} end={<Switch label={t('{name} may ask',{name:thread.name})} checked={p.channels.includes(thread.id)} disabled={saver.saving('ask')}
              onChange={next=>void setPolicy({channels:next?[...p.channels,thread.id]:p.channels.filter(id=>id!==thread.id)})}/>}/>)}</>}
        </Disclose>
      </>}
    </SettingsSection>
  </>;
}

/** An agent, lifted onto the page's card while you add or edit it: name, role, account, thread, model, instructions. */
function AgentCard({project,accounts,conversations,agent,onDone}:{project:SettingsProject;accounts:Account[];conversations:Conversation[];agent?:CollabAgent;onDone:()=>Promise<unknown>}){
  const [name,setName]=useState(agent?.name??''),[role,setRole]=useState<'reviewer'|'worker'>(agent?.role??'reviewer');
  const [profileId,setProfileId]=useState(agent?.profile_id??project.profile_id);
  const [channelId,setChannelId]=useState<string>(agent?.channel_id??'new');
  const [model,setModel]=useState(agent?.model??project.model);
  const [instructions,setInstructions]=useState(agent?.instructions??'');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[deleting,setDeleting]=useState(false);
  const account=accounts.find(a=>a.id===profileId);
  const catalog=useCatalog(account?.profile,true);
  const threads=conversations.filter(c=>c.visibility==='shared'&&c.profile_id===profileId);
  const claudeVersion=useClaudeVersion(accounts.some(a=>a.provider==='claude'));
  // The model it starts on stays in the list even when the list doesn't name it (a project still on Opus 5), so the
  // Select never reads "Choose a model" while a model is set.
  const claudeRows=claudeModelsFor(claudeVersion).map(m=>({value:m.id,label:m.label}));
  const models=account?.provider==='claude'?(!model||claudeRows.some(m=>m.value===model)?claudeRows:[...claudeRows,{value:model,label:modelLabel('claude',model)}]):(catalog.data?.models??[]).map(m=>({value:m.model,label:m.displayName??modelLabel('codex',m.model)}));
  const missing=!name.trim()?t('Add a name to save.'):!/^[a-z0-9][a-z0-9-]{0,31}$/.test(name)?t('Use 1 to 32 lowercase letters, digits or hyphens.'):!account?t('Pick an account.'):channelId==='new'&&!model?t('Pick a model.'):'';
  async function save(){
    setBusy(true);setError('');
    try{
      const channel=channelId==='new'?(await createAgentConversation(project.id,profileId,model,name)).id:channelId;
      await local('collab_agent_save',{name,profileId,channelId:channel,projectId:project.id,role,instructions});
      await onDone();
    }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy(false);}
  }
  async function remove(){
    setBusy(true);setError('');
    try{await local('collab_agent_delete',{name:agent!.name});await onDone();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy(false);}
  }
  return <div className="dv-row dv-open dv-agent-card" role="group" aria-label={agent?t('Edit {name}',{name:agentLabel(agent.name)}):t('Add an agent')}
    onKeyDown={event=>{if(event.key==='Escape'&&!(event.target instanceof HTMLElement&&event.target.closest('[role="menu"]'))){event.preventDefault();event.stopPropagation();void onDone();}}}>
    <span className="dv-avatar" aria-hidden="true">{(name||'?').charAt(0).toUpperCase()}</span>
    <div className="dv-agent-fields">
      <SettingRow name={t('Name')} line={t('How you @mention it: lowercase letters, digits or hyphens.')} end={agent?<code>{agent.name}</code>:<input className="dv-input" aria-label={t('Agent name')} value={name} maxLength={32} onChange={e=>setName(e.target.value)} autoFocus/>}/>
      <SettingRow name={t('Role')} line={role==='reviewer'?t('Reads what it’s sent and says what’s wrong. Changes nothing.'):t('May change the files it’s sent and run the checks.')}
        end={<Segmented label={t('Role')} value={role} onChange={setRole} options={[{value:'reviewer',label:t('Reviewer')},{value:'worker',label:t('Worker')}]}/>}/>
      <SettingRow name={t('Account')} line={account&&account.id!==project.profile_id?t('{name} needs linking to {other} before it can be asked. Link them in Connected accounts.',{name:account.name,other:accounts.find(a=>a.id===project.profile_id)?.name??t('this project’s account')}):undefined}
        end={<Select label={t('Agent account')} value={profileId} onChange={next=>{setProfileId(next);setChannelId('new');const a=accounts.find(x=>x.id===next);setModel(a?.provider==='claude'?claudeDefaultModel(claudeVersion):'');}}
          groups={[{options:accounts.map(a=>({value:a.id,label:a.name,sub:a.provider==='claude'?'Claude':'Codex'}))}]}/>}/>
      <SettingRow name={t('Thread')} line={t('Where it works. Its own thread keeps its account and model.')}
        end={<Select label={t('Agent thread')} value={channelId} onChange={setChannelId} groups={[{options:[{value:'new',label:t('A new thread')},...threads.map(thread=>({value:thread.id,label:thread.name}))]}]}/>}/>
      {channelId==='new'?<SettingRow name={t('Model')} end={<Select label={t('Agent model')} value={model||null} placeholder={catalog.loading?t('Loading…'):t('Choose a model')} onChange={setModel} groups={[{options:models}]}/>}/>
        :<SettingRow name={t('Model')} line={t('{model}, the thread’s own.',{model:modelLabel(account?.provider??'claude',threads.find(thread=>thread.id===channelId)?.model??'')})}/>}
      <label className="dv-instructions"><span className="dv-set-name">{t('Instructions')}</span><span className="dv-set-line">{t('They never grant permissions. {length} of {limit}.',{length:instructions.length,limit:2000})}</span>
        <textarea className="dv-input" rows={4} maxLength={2000} value={instructions} onChange={e=>setInstructions(e.target.value)}/></label>
      {deleting?<div className="dv-confirm dv-project-confirm"><p className="dv-set-name">{t('Delete {name}? Its recorded requests are kept.',{name:agentLabel(agent!.name)})}</p>
        <div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void remove()}>{t('Delete')}</button><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setDeleting(false)}>{t('Keep')}</button></div></div>
      :<div className="dv-actions">
        <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!!missing} aria-describedby={missing?'dv-agent-missing':undefined} onClick={()=>void save()}>{busy?t('Saving…'):t('Save agent')}</button>
        <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void onDone()}>{t('Cancel')}</button>
        {agent&&<button type="button" className="dv-btn dv-btn-quiet dv-push-right" disabled={busy} onClick={()=>setDeleting(true)}>{t('Delete…')}</button>}
      </div>}
      {missing&&<p id="dv-agent-missing" className="dv-set-line">{missing}</p>}
      <FieldError message={error}/>
    </div>
  </div>;
}

