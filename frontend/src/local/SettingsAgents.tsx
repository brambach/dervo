import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Select,SettingRow,SettingsSection,Segmented,Switch} from './DervoControls';
import {accountsFrom,claudeDefaultModel,claudeModelsFor,modelLabel,type Account,type Profile} from './models';
import {useClaudeVersion} from './AddProject';
import {useCatalog} from './ModelPicker';
import {FieldError} from './SettingsBits';
import type {SettingsProject} from './projectSettingsData';
import type {UsageAccount} from './api';
import {agentDelete,agentSave,agentTemplates,agentsList,heartbeat,heartbeatSet,routineDelete,routineParse,routinePause,routineRunNow,routineSave,
  routines,type AgentScope,type AgentTemplate,type DervoAgent,type Gate,type RoutineProposal,type RoutineRun} from './agents';
import {t} from '@/i18n';

/** Agents and routines (agents-and-assistant.md): agents you @mention in any shared thread, each working in
 * its own thread on the account and model you pick; routines that run one of them on a schedule and say what they found
 * in Home; and the heartbeat, which says in Home when a thread newly needs you. */

const PLUS=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;
const failure=(error:unknown)=>error instanceof Error?error.message:String(error);
const GATE_LINES:Record<Gate,string>={always:t('Every time'),changed:t('Only when another thread moved'),needs_you:t('Only when a thread waits on you')};
const RUN_LINES:Record<RoutineRun['state'],string>={due:t('Due'),queued:t('Waiting for its thread to finish a run'),working:t('Working'),done:t('Done'),
  skipped:t('Skipped, nothing changed'),failed:t('Didn’t start'),stopped:t('Stopped'),unknown:t('Outcome unknown')};
const BEATS=['15','30','60','120'] as const;

export function AgentsPage({project,profiles,usage}:{project:SettingsProject;profiles:Profile[];usage?:UsageAccount[]}){
  const client=useQueryClient();
  const accounts=accountsFrom(profiles,usage,Date.now(),project.context??'personal');
  const agents=useQuery({queryKey:['dervo-agents',project.id],queryFn:()=>agentsList(project.id)});
  const templates=useQuery({queryKey:['agent-templates'],staleTime:Infinity,queryFn:agentTemplates});
  const [editing,setEditing]=useState<{agent?:DervoAgent;template?:AgentTemplate}|null>(null);
  const [choosing,setChoosing]=useState(false);
  const done=async()=>{setEditing(null);setChoosing(false);await Promise.all([agents.refetch(),client.invalidateQueries({queryKey:['local-conversations',project.id]})]);};
  const mine=agents.data??[];
  return <>
    <SettingsSection id="dervo-agents" title={t('Agents')} caption={mine.length?t('{length} in this project',{length:mine.length}):undefined}>
      {agents.isError&&<FieldError message={t('The agents couldn’t load.')} onRetry={()=>void agents.refetch()}/>}
      <p className="dv-sec-note">{t('Name one with @ in any shared thread. It works in its own thread and its answer comes back where you asked.')}</p>
      <div className="dv-sheet dv-agents">
        {mine.map(agent=>editing?.agent?.id===agent.id?<AgentForm key={agent.id} project={project} accounts={accounts} agent={agent} onDone={done}/>
          :<div key={agent.id} className="dv-row">
            <span className="dv-avatar" aria-hidden="true">@</span>
            <div><div className="dv-row-name">@{agent.handle} <span className="dv-row-line">{agent.name}</span></div>
              <div className="dv-row-line">{agent.scope==='read'?t('Reads only'):t('May change files')} · {agent.provider==='claude'?'Claude Code':'Codex'} · {t('{model} on {account}',{model:modelLabel(agent.provider,agent.model),account:accounts.find(a=>a.id===agent.profile_id)?.name??t('an account not on this Mac')})}</div>
              {agent.detail&&<p className="dv-why">{t('Can’t run: {why}',{why:agent.detail})}</p>}</div>
            <div className="dv-row-end">{agent.state==='ready'&&<span className="dv-state" data-state="done">{t('Ready')}</span>}
              <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setEditing({agent})}>{t('Edit')}</button></div>
          </div>)}
        {editing&&!editing.agent?<AgentForm project={project} accounts={accounts} template={editing.template} onDone={done}/>
          :choosing?<div className="dv-row dv-open" role="group" aria-label={t('Start from')}>
            <div className="dv-agent-fields"><p className="dv-set-name">{t('Start from')}</p>
              <div className="dv-actions">
                {(templates.data??[]).filter(tpl=>!mine.some(a=>a.handle===tpl.handle)).map(tpl=>
                  <button key={tpl.handle} type="button" className="dv-btn dv-btn-ghost dv-btn-sm" title={tpl.brief} onClick={()=>setEditing({template:tpl})}>{tpl.name}</button>)}
                <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>setEditing({})}>{t('Blank')}</button>
                <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setChoosing(false)}>{t('Cancel')}</button>
              </div></div></div>
          :<button type="button" className="dv-add-line dv-add-agent" onClick={()=>setChoosing(true)}><span>{PLUS}</span><span className="dv-al">{t('Add an agent')}</span><span className="dv-hint">{t('Verifier, Reviewer, Gardener or your own')}</span></button>}
      </div>
    </SettingsSection>
    <RoutinesSection project={project} agents={mine}/>
    <HeartbeatSection project={project} hasAgents={mine.length>0}/>
  </>;
}

function AgentForm({project,accounts,agent,template,onDone}:{project:SettingsProject;accounts:Account[];agent?:DervoAgent;template?:AgentTemplate;onDone:()=>Promise<unknown>}){
  const [handle,setHandle]=useState(agent?.handle??template?.handle??'');
  const [name,setName]=useState(agent?.name??template?.name??'');
  const [brief,setBrief]=useState(agent?.brief??template?.brief??'');
  const [skills,setSkills]=useState((agent?.skills??[]).join(', '));
  const [scope,setScope]=useState<AgentScope>(agent?.scope??template?.scope??'read');
  // A new agent starts on the project's own account and model, so a template saves with one click.
  const [profileId,setProfileId]=useState(agent?.profile_id??project.profile_id);
  const [model,setModel]=useState(agent?.model??project.model);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[deleting,setDeleting]=useState(false);
  const account=accounts.find(a=>a.id===profileId);
  const catalog=useCatalog(account?.profile,true);
  const claudeVersion=useClaudeVersion(accounts.some(a=>a.provider==='claude'));
  const claudeRows=claudeModelsFor(claudeVersion).map(m=>({value:m.id,label:m.label}));
  const models=account?.provider==='claude'?(!model||claudeRows.some(m=>m.value===model)?claudeRows:[...claudeRows,{value:model,label:modelLabel('claude',model)}])
    :(catalog.data?.models??[]).map(m=>({value:m.model,label:m.displayName??modelLabel('codex',m.model)}));
  const missing=!/^[a-z][a-z0-9-]{0,31}$/.test(handle)?t('A handle is lowercase letters, digits or hyphens, starting with a letter.'):!name.trim()?t('Add a name.')
    :!brief.trim()?t('Say what it’s for in the brief.'):!account?t('Pick an account.'):!model?t('Pick a model.'):'';
  async function save(){
    setBusy(true);setError('');
    try{await agentSave(project.id,{handle,name,brief,skills:skills.split(',').map(s=>s.trim()).filter(Boolean),profileId,model,scope},agent?.id);await onDone();}
    catch(e){setError(failure(e));}finally{setBusy(false);}
  }
  async function remove(){
    setBusy(true);setError('');
    try{await agentDelete(project.id,agent!.id);await onDone();}catch(e){setError(failure(e));}finally{setBusy(false);}
  }
  return <div className="dv-row dv-open dv-agent-card" role="group" aria-label={agent?t('Edit @{handle}',{handle:agent.handle}):t('Add an agent')}
    onKeyDown={event=>{if(event.key==='Escape'&&!(event.target instanceof HTMLElement&&event.target.closest('[role="menu"]'))){event.preventDefault();event.stopPropagation();void onDone();}}}>
    <span className="dv-avatar" aria-hidden="true">@</span>
    <div className="dv-agent-fields">
      <SettingRow name={t('Handle')} line={t('What you type after @.')} end={<input className="dv-input" aria-label={t('Agent handle')} value={handle} maxLength={32} onChange={e=>setHandle(e.target.value.toLowerCase())} autoFocus={!agent}/>}/>
      <SettingRow name={t('Name')} end={<input className="dv-input" aria-label={t('Agent name')} value={name} maxLength={60} onChange={e=>setName(e.target.value)}/>}/>
      <SettingRow name={t('What it may change')} line={scope==='read'?t('Reads the project and answers. Changes nothing.'):t('Works like any request in a thread here, under this project’s access.')}
        end={<Segmented label={t('What it may change')} value={scope} onChange={setScope} options={[{value:'read',label:t('Reads only')},{value:'change',label:t('May change files')}]}/>}/>
      <SettingRow name={t('Account')} end={<Select label={t('Agent account')} value={profileId} onChange={next=>{setProfileId(next);const a=accounts.find(x=>x.id===next);setModel(a?.provider==='claude'?claudeDefaultModel(claudeVersion):'');}}
        groups={[{options:accounts.map(a=>({value:a.id,label:a.name,sub:a.provider==='claude'?'Claude':'Codex'}))}]}/>}/>
      <SettingRow name={t('Model')} end={<Select label={t('Agent model')} value={model||null} placeholder={catalog.loading?t('Loading…'):t('Choose a model')} onChange={setModel} groups={[{options:models}]}/>}/>
      <label className="dv-instructions"><span className="dv-set-name">{t('Brief')}</span><span className="dv-set-line">{t('What it’s for and how it works. It never grants access. {length} of {limit}.',{length:brief.length,limit:4000})}</span>
        <textarea className="dv-input" rows={5} maxLength={4000} value={brief} onChange={e=>setBrief(e.target.value)}/></label>
      <SettingRow name={t('Skills')} line={t('Project skills it brings to every request, by name, separated by commas.')}
        end={<input className="dv-input" aria-label={t('Agent skills')} value={skills} onChange={e=>setSkills(e.target.value)} placeholder="verify-app"/>}/>
      {deleting?<div className="dv-confirm dv-project-confirm"><p className="dv-set-name">{t('Delete @{handle}? Its thread and everything it did are kept.',{handle:agent!.handle})}</p>
        <div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void remove()}>{t('Delete')}</button><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setDeleting(false)}>{t('Keep')}</button></div></div>
      :<div className="dv-actions">
        <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!!missing} aria-describedby={missing?'dv-dervo-agent-missing':undefined} onClick={()=>void save()}>{busy?t('Saving…'):t('Save agent')}</button>
        <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void onDone()}>{t('Cancel')}</button>
        {agent&&<button type="button" className="dv-btn dv-btn-quiet dv-push-right" disabled={busy} onClick={()=>setDeleting(true)}>{t('Delete…')}</button>}
      </div>}
      {missing&&(agent||handle||name||brief)&&<p id="dv-dervo-agent-missing" className="dv-set-line">{missing}</p>}
      <FieldError message={error}/>
    </div>
  </div>;
}

function RoutinesSection({project,agents}:{project:SettingsProject;agents:DervoAgent[]}){
  const list=useQuery({queryKey:['routines',project.id],refetchInterval:30000,queryFn:()=>routines(project.id)});
  const [said,setSaid]=useState(''),[proposal,setProposal]=useState<RoutineProposal|null>(null);
  const [gate,setGate]=useState<Gate>('changed'),[agentId,setAgentId]=useState<string|null>(null);
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[dropping,setDropping]=useState<string|null>(null);
  async function act(key:string,fn:()=>Promise<unknown>){setBusy(key);setError('');try{await fn();await list.refetch();}catch(e){setError(failure(e));}finally{setBusy('');}}
  async function read(){
    await act('read',async()=>{const found=await routineParse(project.id,said);setProposal(found);setGate(found.gate);setAgentId(found.agentId??null);});
  }
  const chosen=agentId??proposal?.agentId??null;
  const canSave=!!proposal?.schedule&&!!proposal.prompt&&!!chosen;
  return <SettingsSection id="routines" title={t('Routines')} caption={list.data?.length?t('{length} set up',{length:list.data.length}):undefined}>
    <p className="dv-sec-note">{agents.length?t('Say when and what, and name the agent. What it finds is said in Home. A routine never gets full access.'):t('Add an agent first. A routine runs as one of them.')}</p>
    {agents.length>0&&<div className="dv-sheet">
      <div className="dv-row dv-open"><div className="dv-agent-fields">
        <input className="dv-input" aria-label={t('When and what')} value={said} placeholder={t('Every weekday at 8, @reviewer look over what finished overnight')}
          onChange={e=>{setSaid(e.target.value);setProposal(null);}} onKeyDown={e=>{if(e.key==='Enter'&&said.trim())void read();}}/>
        {!proposal?<div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!said.trim()||busy==='read'} onClick={()=>void read()}>{t('Read it')}</button></div>
          :<>
            <SettingRow name={t('When')} line={proposal.when??t('Dervo couldn’t tell when. Try “every weekday at 8” or “every 2 hours”.')}/>
            <SettingRow name={t('Agent')} line={chosen?undefined:t('Name an agent, or pick the one it runs as.')} end={<Select label={t('Routine agent')} value={chosen} onChange={setAgentId} groups={[{options:agents.map(a=>({value:a.id,label:'@'+a.handle}))}]}/>}/>
            <SettingRow name={t('Asks')} line={proposal.prompt??t('Say what it should do.')}/>
            <SettingRow name={t('Runs')} end={<Segmented label={t('Runs')} value={gate} onChange={setGate} options={(['changed','needs_you','always'] as Gate[]).map(value=>({value,label:GATE_LINES[value]}))}/>}/>
            <div className="dv-actions">
              <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!canSave||busy==='save'}
                onClick={()=>void act('save',async()=>{await routineSave(project.id,{name:proposal.name??proposal.prompt!.slice(0,40),prompt:proposal.prompt!,agentId:chosen!,schedule:proposal.schedule!,gate});setSaid('');setProposal(null);})}>{t('Save routine')}</button>
              <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setProposal(null)}>{t('Cancel')}</button>
            </div></>}
      </div></div>
      {(list.data??[]).map(routine=><div key={routine.id} className="dv-row">
        <div><div className="dv-row-name">{routine.name}</div>
          <div className="dv-row-line">{routine.when} · {routine.agent?`@${routine.agent.handle}`:t('no agent')} · {GATE_LINES[routine.gate]}{routine.state==='paused'?` · ${t('Paused')}`:routine.state==='done'?` · ${t('Done')}`:''}</div>
          {routine.last_run&&<div className="dv-row-line">{t('Last run: {state}',{state:routine.last_run.detail?`${RUN_LINES[routine.last_run.state]}: ${routine.last_run.detail}`:RUN_LINES[routine.last_run.state]})}</div>}
          {routine.detail&&<p className="dv-why">{routine.detail}</p>}
          {dropping===routine.id&&<div className="dv-confirm dv-project-confirm"><p className="dv-set-name">{t('Delete the routine “{name}”? Runs it already made stay in their threads.',{name:routine.name})}</p>
            <div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void act(routine.id,async()=>{await routineDelete(project.id,routine.id);setDropping(null);})}>{t('Delete')}</button><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setDropping(null)}>{t('Keep')}</button></div></div>}</div>
        <div className="dv-row-end">
          {routine.state!=='done'&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void act(routine.id,()=>routinePause(project.id,routine.id,routine.state==='active'))}>{routine.state==='active'?t('Pause'):t('Resume')}</button>}
          <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy||!routine.agent} onClick={()=>void act(routine.id,()=>routineRunNow(project.id,routine.id))}>{t('Run now')}</button>
          <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>setDropping(routine.id)}>{t('Delete…')}</button>
        </div>
      </div>)}
    </div>}
    <FieldError message={error}/>
  </SettingsSection>;
}

function HeartbeatSection({project,hasAgents}:{project:SettingsProject;hasAgents:boolean}){
  const beat=useQuery({queryKey:['heartbeat',project.id],queryFn:()=>heartbeat(project.id)});
  const [error,setError]=useState('');
  async function set(enabled:boolean,minutes:number){setError('');try{await heartbeatSet(project.id,enabled,minutes);await beat.refetch();}catch(e){setError(failure(e));}}
  const b=beat.data;
  return <SettingsSection id="heartbeat" title={t('Heartbeat')}>
    {beat.isError&&<FieldError message={t('Dervo couldn’t read the heartbeat setting.')}/>}
    {beat.isError&&<div className="dv-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void beat.refetch()}>{t('Try again')}</button></div>}
    {b&&<>
      <SettingRow name={t('Tell me in Home when a thread needs me')} disabled={!hasAgents}
        line={hasAgents?t('Checks the records, never a model. Says nothing when nothing new needs you.'):t('Starts once this project has an agent.')}
        end={<Switch label={t('Heartbeat')} checked={b.enabled&&hasAgents} disabled={!hasAgents} onChange={next=>void set(next,b.minutes)}/>}/>
      {b.enabled&&hasAgents&&<SettingRow name={t('How often')} end={<Segmented label={t('How often')} value={String(b.minutes) as typeof BEATS[number]}
        onChange={value=>void set(true,Number(value))} options={BEATS.map(value=>({value,label:Number(value)<60?t('{n} min',{n:value}):t('{n} h',{n:Number(value)/60})}))}/>}/>}
    </>}
    <FieldError message={error}/>
  </SettingsSection>;
}
