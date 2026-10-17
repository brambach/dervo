import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {CHECK_TEST_NAMES,local,type CodingPermission,type RunCheck,type CollabAgent,type CollabPolicy,type ExtraCheck,type UsageAccount} from './api';
import {agentLabel} from './collaboration';
import {ChoiceRows,Select,SettingRow,SettingsSection,Segmented,Switch,TextField,type Choice} from './DervoControls';
import {accountsFrom,effortLabel,effortsFor,modelLabel,type Profile} from './models';
import {MANUAL,currentChecks,currentWhere,grantArgs,useProjectPermission,useSaver,useSettingsRefresh,type SettingsProject} from './projectSettingsData';
import {Disclose,FieldError} from './SettingsBits';
import {formatList,t} from '@/i18n';

/** Checks and review (settings-checks.html), in the order they happen: 1. the check, 2. a second agent's review,
 * 3. your approval. Every control saves on change. The check lives in the same grant as Access, so a change here keeps
 * where agents can work exactly as it is. */

const LINES:Record<string,string>={
  [MANUAL]:t('No automated check. You try the change, then approve it.'),
  'python-unittest':t('Runs your tests before you see the result.'),
  'node-test':t('Runs your Node tests before you see the result.'),
  'typescript-check':t('Type-checks the project before you see the result.'),
  'rust-test':t('Runs your Rust tests before you see the result.'),
  'rust-check':t('Type-checks the Rust code before you see the result.'),
};
// #28: full access asks for approval whatever this switch says (conversation_coding.py sets acceptance 'full-access'), so
// with the switch off the row says why those tasks still wait.
export const FULL_ACCESS_ASKS=t('Full-access tasks still wait for your approval, because no checks run in Dervo for them.');
// With the project's own checks on, they do run, but the result still waits for you.
export const FULL_ACCESS_CHECKED=t('Full-access tasks still wait for your approval. Dervo runs this project’s checks first and shows what they found.');
// 16.2: with Commit results automatically on (Access), full-access results don't wait, whatever this switch says.
export const FULL_ACCESS_AUTO=t('Full-access results commit automatically here. That’s the Commit results automatically switch on Access.');
const RUST=['rust-check','rust-test'];
const PLUS=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;
const fileLabel=(profile:string)=>profile==='typescript-check'?t('Check configuration'):RUST.includes(profile)?t('Rust source file'):t('Test file');
const fileLine=(profile:string)=>profile==='typescript-check'?t('The configuration this check type-checks against. Each run gets a clean copy of the project.')
  :t('The root file this check runs. Each run gets a clean copy of the project.');

/** The files at the top of the project a check can run, by the host's own name rule for it. */
export function matching(permission:CodingPermission|undefined,profile:string):string[]{
  const rule=CHECK_TEST_NAMES[profile];
  if(!permission||!rule)return [];
  return [...new Set([...(permission.test_candidates??[]),...(permission.candidates??[]),...permission.files])].filter(name=>rule.test(name));
}

export function ChecksPage({project,profiles,usage,onAccess}:{project:SettingsProject;profiles:Profile[];usage?:UsageAccount[];onAccess:()=>void}){
  const permission=useProjectPermission(project.id);
  const saver=useSaver(),refresh=useSettingsRefresh(project.id);
  const data=permission.data;
  const checks=currentChecks(data);
  const [choosing,setChoosing]=useState<string|null>(null);
  const [toolchain,setToolchain]=useState('');
  const catalogue=(data?.check_profiles??[]).filter(p=>p.id!==MANUAL);
  const offered=catalogue.filter(p=>p.available&&(matching(data,p.id).length>0||p.id===checks.primary.profile));
  const rest=catalogue.filter(p=>!offered.includes(p));
  const where=currentWhere(data);
  const granted=!!data&&data.state!=='missing'&&data.state!=='unsupported';
  const primary=choosing??checks.primary.profile;

  async function saveChecks(next:{primary:{profile:string;test_file:string|null;rust_toolchain?:string|null;label:string};extra:ExtraCheck[]},timeout?:number){
    const view=await saver.save('chk','check',async()=>refresh(await local<CodingPermission>('coding_permission_grant',{projectId:project.id,channelId:null,...grantArgs(data,where,next,timeout)})));
    if(view!==undefined)setChoosing(null);
  }
  function choose(profile:string){
    saver.clear('check');
    if(profile===checks.primary.profile){setChoosing(null);return;}
    // i18n-ignore: the label is saved with the project's checks, in Dervo's own English
    if(profile===MANUAL){void saveChecks({primary:{profile:MANUAL,test_file:null,label:'Review changes myself'},extra:[]});return;}
    const files=matching(data,profile);
    if(files.length===1&&!RUST.includes(profile)){void saveChecks({primary:{profile,test_file:files[0],label:''},extra:checks.extra.filter(e=>e.checkProfile!==profile)});return;}
    setToolchain('');setChoosing(profile);
  }
  const extraLabel=(id:string)=>catalogue.find(p=>p.id===id)?.label??id;
  const addable=offered.filter(p=>p.id!==primary&&!RUST.includes(p.id)&&!checks.extra.some(e=>e.checkProfile===p.id));
  const cardFor=(profile:string)=>{
    const files=matching(data,profile);
    const pending=choosing===profile;
    const file=pending?'':checks.primary.test_file??'';
    const entry=catalogue.find(p=>p.id===profile);
    return <>
      {pending&&<p className="dv-set-line">{RUST.includes(profile)?t('Choose the installed toolchain folder and the root file to use this check.'):t('Pick the test file to use this check.')}</p>}
      {RUST.includes(profile)&&<SettingRow name={t('Toolchain folder')} line={t('The installed folder holding bin/rustc, outside this project. Dervo checks it when you save and never installs anything.')}
        below={<TextField className="dv-wide" label={t('Rust toolchain folder')} placeholder="/Users/you/.rustup/toolchains/stable-aarch64-apple-darwin" value={pending?toolchain:checks.primary.rust_toolchain??''}
          onCommit={next=>{if(pending){setToolchain(next);return;}void saveChecks({primary:{...checks.primary,rust_toolchain:next},extra:checks.extra});}}/>}/>}
      <SettingRow name={fileLabel(profile)} line={fileLine(profile)} end={<Select label={fileLabel(profile)} value={file||null} placeholder={t('Choose a file')} disabled={saver.saving('chk')}
        groups={[{options:files.map(f=>({value:f,label:f}))}]}
        onChange={next=>{if(RUST.includes(profile)&&pending&&!toolchain.trim()){saver.fail('check',t('Choose the toolchain folder first.'));return;}
          void saveChecks({primary:{profile,test_file:next,rust_toolchain:RUST.includes(profile)?(pending?toolchain:checks.primary.rust_toolchain):null,label:''},extra:checks.extra.filter(e=>e.checkProfile!==profile)});}}/>}/>
      {!pending&&<>
        <SettingRow name={t('Also allow')} line={t('For a project in two languages. You pick which check a message runs from its model picker.')}/>
        {checks.extra.map(extra=><div key={extra.checkProfile} className="dv-extra"><p><b>{extraLabel(extra.checkProfile)}</b><span className="dv-muted"> {t('on {file}',{file:extra.testFile})}</span></p>
          <button type="button" className="dv-btn dv-btn-quiet" disabled={saver.saving('chk')} onClick={()=>void saveChecks({primary:checks.primary,extra:checks.extra.filter(e=>e!==extra)})}>{t('Remove')}</button></div>)}
        {addable.length>0&&checks.extra.length<3&&<DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-quiet dv-add" disabled={saver.saving('chk')}>{PLUS}{t('Add a check')}</button></DropdownMenuTrigger>
          <DropdownMenuContent align="start">{addable.map(p=>matching(data,p.id).map(f=><DropdownMenuItem key={`${p.id}:${f}`} onSelect={()=>void saveChecks({primary:checks.primary,extra:[...checks.extra,{checkProfile:p.id,testFile:f}]})}>
            {p.label}<span className="dv-sub"> {t('on {file}',{file:f})}</span></DropdownMenuItem>))}</DropdownMenuContent></DropdownMenu>}
        <SettingRow name={t('Stop a check after')} line={t('5 to 120 seconds. You can also stop one while it runs.')}
          end={<span className="dv-secs"><TextField label={t('Seconds')} className="dv-secs-input" value={String(data?.check_timeout_seconds??5)}
            onCommit={next=>{const n=Number(next);if(!/^\d+$/.test(next)||n<5||n>120)return t('Use 5 to 120 seconds.');void saveChecks({primary:checks.primary,extra:checks.extra},n);}}/> {t('seconds')}</span>}/>
      </>}
      <FieldError message={saver.error('check')}/>
      {entry&&<Disclose summary={t('How this check runs')}><p className="dv-detail">{entry.detail}</p></Disclose>}
    </>;
  };
  const choices:Choice<string>[]=[
    {value:MANUAL,name:t('Review changes myself'),line:LINES[MANUAL]},
    ...offered.map(p=>{const files=matching(data,p.id);return {value:p.id,name:p.label,line:LINES[p.id]??p.detail,
      end:p.id===checks.primary.profile&&checks.primary.test_file?checks.primary.test_file:files.length===1?files[0]:files.length?t('{count} files',{count:files.length}):undefined,body:cardFor(p.id)};}),
  ];
  return <>
    {permission.isError&&<FieldError message={t('This project’s checks couldn’t load: {why}',{why:permission.error instanceof Error?permission.error.message:String(permission.error)})} onRetry={()=>void permission.refetch()}/>}
    <SettingsSection id="chk" title={t('1. Check the work')} caption={saver.caption('chk')}>
      {!data&&!permission.isError?<p className="dv-sec-note" role="status">{t('Loading this project’s checks…')}</p>
        :!granted?<><p className="dv-sec-note">{t('Agents only read and explain here, so there’s nothing to check. Pick Edit files on Access to choose a check.')}</p>
          <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-self-start" onClick={onAccess}>{t('Open Access')}</button></>
        :<>
          <ChoiceRows label={t('Check the work')} value={checks.primary.profile} lift={primary} open choices={choices} onChange={choose}/>
          {data.run_checks&&<RunChecksRows project={project} data={data} saver={saver} refresh={refresh}/>}
          {rest.length>0&&<Disclose summary={t('{count} more checks',{count:rest.length})} className="dv-more-checks"><p className="dv-detail">
            {formatList(rest.map(p=>p.label))}. {rest.every(p=>p.available)?t('None of them has a matching file at the top of this project.'):[...new Set(rest.filter(p=>!p.available).map(p=>p.detail))].join(' ')}</p></Disclose>}
        </>}
    </SettingsSection>
    <ReviewSection project={project} profiles={profiles} usage={usage}/>
    <SettingsSection id="apr" title={t('3. Your approval')} caption={saver.caption('apr')}>
      {data&&<SettingRow name={t('Wait for my approval')}
        line={checks.primary.profile===MANUAL&&granted?t('Each finished change waits for you to approve it or ask for changes.'):t('Each finished change waits for you to approve it or ask for changes. Off: a change that passes its check and review counts as done without you.')}
        why={checks.primary.profile===MANUAL&&granted?t('Always on while you review changes yourself.'):data.full_access?.auto_commit?FULL_ACCESS_AUTO:data.full_access?.enabled&&data.acceptance?.required===false?(data.run_checks?.commands.length?FULL_ACCESS_CHECKED:FULL_ACCESS_ASKS):undefined}
        end={<Switch label={t('Wait for my approval')} checked={checks.primary.profile===MANUAL&&granted?true:data.acceptance?.required??true} disabled={(checks.primary.profile===MANUAL&&granted)||!data.acceptance||saver.saving('apr')}
          onChange={required=>void saver.save('apr','approval',async()=>{await local('coding_acceptance_set',{projectId:project.id,channelId:null,required});await refresh();})}/>}/>}
      <FieldError message={saver.error('approval')}/>
    </SettingsSection>
  </>;
}

/** What a check runs, as you'd type it, with the folder it runs in. */
const commandLine=(check:RunCheck)=>`${check.cwd&&check.cwd!=='.'?`${check.cwd}: `:''}${check.argv.join(' ')}`;

/** The project's own checks after each full-access run (project_checks): one switch turns on everything its files
 * suggest, and each can be removed or added back. Before this nothing in the window turned them on (critic, 9 Oct). */
export function RunChecksRows({project,data,saver,refresh}:{project:SettingsProject;data:CodingPermission;saver:ReturnType<typeof useSaver>;refresh:ReturnType<typeof useSettingsRefresh>}){
  const saved=data.run_checks?.commands??[],offered=data.run_checks?.suggested??[];
  const on=saved.length>0;
  const full=!!data.full_access?.enabled;
  async function keep(names:string[]){
    saver.clear('runchecks');
    await saver.save('chk','runchecks',async()=>refresh(await local<CodingPermission>('coding_checks_set',{projectId:project.id,channelId:null,names})));
  }
  const line=!on&&!offered.length?t('Dervo didn’t find tests, type checks or lint scripts in this project’s files.')
    :full?t('After each full-access run that changes files, Dervo runs these and tells you which pass, which were already failing and which the run broke.')
    :t('These run after each full-access run that changes files. Turn on full access on Access to use them.');
  return <>
    <SettingRow name={t('Run this project’s checks')} line={line}
      end={<Switch label={t('Run this project’s checks')} checked={on} disabled={(!on&&!offered.length)||saver.saving('chk')}
        onChange={next=>void keep(next?offered.map(c=>c.name):[])}/>}/>
    {(on?saved:offered).map(check=><div key={check.name} className="dv-extra"><p><b>{check.name}</b><code className="dv-cmd"> {commandLine(check)}</code></p>
      {on&&<button type="button" className="dv-btn dv-btn-quiet" disabled={saver.saving('chk')} onClick={()=>void keep(saved.filter(c=>c!==check).map(c=>c.name))}>{t('Remove')}</button>}</div>)}
    {on&&offered.length>0&&<DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-quiet dv-add" disabled={saver.saving('chk')}>{PLUS}{t('Add a check')}</button></DropdownMenuTrigger>
      <DropdownMenuContent align="start">{offered.map(check=><DropdownMenuItem key={check.name} onSelect={()=>void keep([...saved.map(c=>c.name),check.name])}>
        {check.name}<span className="dv-sub"> {commandLine(check)}</span></DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu>}
    <FieldError message={saver.error('runchecks')}/>
  </>;
}

/** 2. Second review: another agent reads the finished work before you do. Turning it on with no reviewer yet sets one up
 * in place, on the project's own account and model. */
function ReviewSection({project,profiles,usage}:{project:SettingsProject;profiles:Profile[];usage?:UsageAccount[]}){
  const saver=useSaver(),refresh=useSettingsRefresh(project.id);
  const policy=useQuery({queryKey:['project-collab-policy',project.id],queryFn:()=>local<CollabPolicy>('collab_policy',{projectId:project.id,channelId:null})});
  const agents=useQuery({queryKey:['collab-agents'],queryFn:()=>local<CollabAgent[]>('collab_agents',{})});
  const [setup,setSetup]=useState(false),[setupEffort,setSetupEffort]=useState('low');
  const accounts=accountsFrom(profiles,usage,Date.now(),project.context??'personal');
  const own=accounts.find(a=>a.id===project.profile_id);
  const review=policy.data?.review;
  const reviewers=(agents.data??[]).filter(a=>a.role==='reviewer'&&a.project_id===project.id);
  const reviewer=reviewers.find(a=>a.name===review?.reviewer);
  const reviewerAccount=accounts.find(a=>a.id===reviewer?.profile_id);
  const on=!!review?.enabled;
  const levelsFor=(provider:string,model:string)=>effortsFor(provider,model).filter(l=>l!=='max');
  async function savePolicy(next:{enabled:boolean;reviewer:string|null;effort:string|null}){
    const p=policy.data;
    if(!p)return;
    await saver.save('rev','review',async()=>{
      const view=await local<CollabPolicy>('collab_policy_set',{projectId:project.id,channelId:null,mode:p.mode,agents:p.agents,channels:p.channels,
        maxParallel:p.limits.max_parallel,maxDepth:p.limits.max_depth,maxCalls:p.limits.max_calls,review:next});
      await policy.refetch();await refresh();return view;
    });
  }
  function toggle(next:boolean){
    saver.clear('review');
    if(!next){setSetup(false);void savePolicy({enabled:false,reviewer:null,effort:null});return;}
    const first=reviewers.find(a=>a.state==='ready')??reviewers[0];
    if(first){void savePolicy({enabled:true,reviewer:first.name,effort:first.provider==='claude'?'high':'medium'});return;}
    setSetup(true);
  }
  const who=reviewerAccount?.name??own?.name??t('your account');
  return <SettingsSection id="rev" title={t('2. Second review')} caption={saver.caption('rev')}>
    {policy.isError?<FieldError message={t('The review setting couldn’t load: {why}',{why:policy.error instanceof Error?policy.error.message:String(policy.error)})} onRetry={()=>void policy.refetch()}/>:<>
      <SettingRow name={t('Have another agent review each change')} line={t('Reviewer checks the finished work and can ask for one correction. It runs on {who}, so it uses {who}’s limits.',{who})}
        end={<Switch label={t('Second review')} checked={on||setup} disabled={!policy.data||saver.saving('rev')} onChange={toggle}/>}/>
      {setup&&!on&&own&&<div className="dv-rev"><div className="dv-confirm dv-project-confirm" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();setSetup(false);}}}>
        <p className="dv-set-line">{t('Reviewer uses {account} · {model} in its own read-only thread. Each review uses {account}’s limits.',{account:own.name,model:modelLabel(own.provider,project.model)})}</p>
        {levelsFor(own.provider,project.model).length>0&&<SettingRow name={t('Thinking')} end={<Segmented label={t('Reviewer thinking')} value={setupEffort} onChange={setSetupEffort}
          options={levelsFor(own.provider,project.model).map(l=>({value:l,label:effortLabel(l)}))}/>}/>}
        <div className="dv-actions"><button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={saver.saving('rev')}
          onClick={()=>void saver.save('rev','review',async()=>{await local('collab_reviewer_setup',{projectId:project.id,channelId:null,profileId:project.profile_id,model:project.model,effort:setupEffort});
            await Promise.all([policy.refetch(),agents.refetch()]);setSetup(false);await refresh();})}>{t('Set up reviewer')}</button>
          <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setSetup(false)}>{t('Cancel')}</button></div>
      </div></div>}
      {on&&<div className="dv-rev">
        <SettingRow name={t('Reviewer')} line={reviewer?t('{tool} in its own read-only thread.',{tool:reviewer.provider==='claude'?'Claude Code':'Codex'}):t('The saved reviewer isn’t on this project any more.')}
          end={<Select label={t('Reviewer')} value={review?.reviewer??null} disabled={saver.saving('rev')}
            groups={[{options:reviewers.map(a=>({value:a.name,label:agentLabel(a.name),sub:`${modelLabel(a.provider,a.model)} · ${accounts.find(x=>x.id===a.profile_id)?.name??t('another account')}`}))}]}
            onChange={name=>{const next=reviewers.find(a=>a.name===name);if(next)void savePolicy({enabled:true,reviewer:name,effort:next.provider==='claude'?'high':'medium'});}}/>}/>
        {reviewer&&levelsFor(reviewer.provider,reviewer.model).length>0&&<SettingRow name={t('Thinking')}
          end={<Segmented label={t('Reviewer thinking')} value={review?.effort??''} disabled={saver.saving('rev')} onChange={effort=>void savePolicy({enabled:true,reviewer:reviewer.name,effort})}
            options={levelsFor(reviewer.provider,reviewer.model).map(l=>({value:l,label:effortLabel(l)}))}/>}/>}
      </div>}
      <FieldError message={saver.error('review')}/>
    </>}
  </SettingsSection>;
}
