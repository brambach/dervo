import './SettingsParts.css';
import {FullAccessSection} from './FullAccess';
import {SettingsSectionFrame} from './SettingsSectionFrame';
import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {invoke} from '@tauri-apps/api/core';
import {Button} from '@/shared/ui/button';
import {local,setResultApproval,grantPayload,CLAUDE_EFFORTS,CHECK_TEST_NAMES,PYTHON_CHECK,type CodingPermission,type ExtraCheck} from './api';
import {TaskDisclosure,CODING_PROVIDERS} from './CodingTasks';
import {t} from '@/i18n';
// i18n-ignore: example folder paths, the same in every language
const FOLDER_EXAMPLE='src\ntests';

type Binding={provider:string;identity:string;model:string;cwd:string;visibility:string};
function unique(values:string[]):string[]{return [...new Set(values)];}
/** What the Save button sends. Blank (empty or whitespace only) means unset: null, the provider default. A nonblank token is
 * sent exactly as typed, untrimmed, so a malformed value such as " high " reaches the host's strict rejection unchanged. */
/** B7: a folder chosen in the macOS panel, as the relative path the grant takes. Null when it isn't strictly inside the
 * project: the project folder itself, a sibling that only shares a name prefix, or anywhere else. */
export function folderInside(root:string,chosen:string):string|null{
  const base=root.replace(/\/+$/,'');
  if(!base||!chosen.startsWith(base+'/'))return null;
  const relative=chosen.slice(base.length+1).replace(/\/+$/,'');
  return relative&&!relative.split('/').some(part=>part===''||part==='.'||part==='..')?relative:null;
}
export function outgoingEffort(effort:string):string|null {return effort.trim()===''?null:effort;}
function matchingChecks(data:CodingPermission,check:string,extraFiles:string[]=[]){
 const rule=CHECK_TEST_NAMES[check];
 return unique([...(data.test_candidates??[]),...(data.candidates??[]),...data.files,...extraFiles]).filter(name=>rule?.test(name));
}
function initialCheck(data:CodingPermission){
 // Saved grants retain their check even if its files or runtime are now missing.
 if(data.check_profile)return data.check_profile;
 if(data.state!=='missing'||!data.check_profiles)return PYTHON_CHECK;
 return [PYTHON_CHECK,'node-test','typescript-check','rust-test','rust-check'].find(id=>
  (!data.check_profiles||data.check_profiles.some(entry=>entry.id===id&&entry.available))&&matchingChecks(data,id).length>0
 )??(data.check_profiles.some(entry=>entry.id==='manual-review'&&entry.available)?'manual-review':PYTHON_CHECK);
}
/** Project access and a supported check. Detection never grants access. */
export function CodingPermissionDialog({embedded=false,open,onOpenChange,projectId,channelId,binding,refresh}:{embedded?:boolean;open:boolean;onOpenChange:(v:boolean)=>void;projectId:string;channelId:string;binding:Binding;refresh:()=>Promise<unknown>}){
 const titleRef=useRef<HTMLHeadingElement>(null);
 const permission=useQuery({queryKey:['coding-permission',projectId,channelId],enabled:open,queryFn:()=>local<CodingPermission>('coding_permission',{projectId,channelId})});
 const [selected,setSelected]=useState<string[]>([]),[testFile,setTestFile]=useState(''),[extra,setExtra]=useState(''),[folders,setFolders]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[seeded,setSeeded]=useState('');
 // The check profile: one host-catalogued check, seeded from the saved grant (Python unittest for a grant made before profiles existed).
 const [projectAccess,setProjectAccess]=useState(false);
 const [revoking,setRevoking]=useState(false),[folderError,setFolderError]=useState('');
 async function chooseFolder(){
  setFolderError('');
  try{
   const chosen=await invoke<string|null>('choose_folder_in',{root:binding.cwd});
   if(!chosen)return;
   const relative=folderInside(binding.cwd,chosen);
   if(!relative){setFolderError(t('Choose a folder inside this project, not the project itself or a folder outside it.'));return;}
   setFolders(current=>{const lines=current.split('\n').map(line=>line.trim()).filter(Boolean);return lines.includes(relative)?current:[...lines,relative].join('\n');});
  }catch(failure){setFolderError(failure instanceof Error?failure.message:String(failure));}
 }
 const [check,setCheck]=useState(PYTHON_CHECK),[rustToolchain,setRustToolchain]=useState(''),[checkTimeout,setCheckTimeout]=useState('5');
 // Extra checks for a mixed project: each is another catalogued check with its own root file. A request picks one.
 const [extraChecks,setExtraChecks]=useState<ExtraCheck[]>([]);
 const timeoutValid=/^\d+$/.test(checkTimeout)&&Number(checkTimeout)>=5&&Number(checkTimeout)<=120;
 const data=permission.data;
 useEffect(()=>{
  const stamp=data?`${projectId}:${channelId}:${data.state}:${data.created_at??''}:${data.rust_toolchain??''}:${data.check_timeout_seconds??5}`:'';
  if(open&&data&&stamp!==seeded){setProjectAccess(data.project_access===true||(data.state==='missing'&&data.project_access_available===true));setSelected(data.files);setFolders((data.folders??[]).join('\n'));setCheck(initialCheck(data));setRustToolchain(data.rust_toolchain??'');setCheckTimeout(String(data.check_timeout_seconds??5));const matches=matchingChecks(data,initialCheck(data));setTestFile(data.test_file??(matches.length===1?matches[0]:''));setExtraChecks((data.checks??[]).slice(1).map(entry=>({checkProfile:entry.profile,testFile:entry.test_file??'',...(entry.rust_toolchain?{rustToolchain:entry.rust_toolchain}:{})})));setSeeded(stamp);}
 },[data,seeded,projectId,channelId,open]);
 useEffect(()=>{if(!open)setSeeded('');},[open]);
 const checkProfiles=data?.check_profiles??[];
 const checkEntry=checkProfiles.find(p=>p.id===check);
 const manual=check==='manual-review';
 // One central name rule per check (api.ts CHECK_TEST_NAMES); the host's test_pattern is the authority and decides.
 const testName=CHECK_TEST_NAMES[check];
 function chooseCheck(id:string){setCheck(id);const matches=data?matchingChecks(data,id,files):[];setTestFile(matches.includes(testFile)?testFile:matches.length===1?matches[0]:'');}
 // The owner effort for exactly this provider and model. Seeded from the host record alone, so a saved value is what the
 // host holds. The control stays disabled until that seed has arrived (effortSeed undefined means not yet loaded), an edit
 // marks it dirty so a background reload never overwrites what the user typed, and a successful save clears dirty so the
 // reloaded host value is shown. Closing the dialog drops the seed and any unsaved edit; reopening reseeds from the host.
 const [effort,setEffort]=useState(''),[effortDirty,setEffortDirty]=useState(false),[effortSeed,setEffortSeed]=useState<string|null|undefined>(undefined);
 useEffect(()=>{if(!open){setEffortSeed(undefined);setEffortDirty(false);}},[open]);
 useEffect(()=>{
  if(!data)return;
  const held=data.owner_effort??null;
  if(effortSeed===undefined||(!effortDirty&&held!==effortSeed)){setEffort(held??'');setEffortSeed(held);}
 },[data,effortSeed,effortDirty]);
 const effortReady=effortSeed!==undefined;
 function editEffort(value:string){setEffort(value);setEffortDirty(true);}
 // Result approval: a project policy, separate from this grant and from Independent review. The checkbox shows the host
 // value only; it stays disabled until loaded and saves at once, so a reopen shows exactly what the host holds.
 const approval=data?.acceptance;
 const [approvalBusy,setApprovalBusy]=useState(false),[approvalError,setApprovalError]=useState('');
 async function saveApproval(required:boolean){
  setApprovalBusy(true);setApprovalError('');
  try{await setResultApproval(projectId,channelId,required);await permission.refetch();await refresh();}
  catch(e){setApprovalError(String(e));}
  finally{setApprovalBusy(false);}
 }
 const candidates=unique([...(data?.files??[]),...(data?.candidates??[])]);
 const files=unique([...selected,...extra.split('\n').map(s=>s.trim()).filter(Boolean),...(testFile?[testFile]:[]),...extraChecks.map(c=>c.testFile).filter(Boolean)]);
 const extraOptions=checkProfiles.filter(p=>p.id!=='manual-review'&&p.id!==check&&p.available);
 const extraProblem=manual?'':extraChecks.find(c=>!c.checkProfile||!c.testFile||!CHECK_TEST_NAMES[c.checkProfile]?.test(c.testFile)||c.checkProfile===check||extraChecks.filter(o=>o.checkProfile===c.checkProfile).length>1);
 function editExtra(index:number,patch:Partial<ExtraCheck>){setExtraChecks(current=>current.map((c,i)=>i===index?{...c,...patch}:c));}
 const testOptions=data?matchingChecks(data,check,files):[];
 const supported=CODING_PROVIDERS.has(binding.provider);
 const checkFileLabel=check==='typescript-check'?t('check configuration'):['rust-check','rust-test'].includes(check)?t('Rust source file'):t('test file');
 const setupReason=permission.isError?t('Project access couldn’t be loaded. Use Try again above.'):!data?t('Loading project files…'):!supported?t('Coding needs a Codex or Claude Code account binding.'):
  checkEntry&&!checkEntry.available?t('Choose an available check.'):
  manual&&!checkEntry?.available?t('Manual review isn’t available in this host.'):
  !manual&&!testFile?(testOptions.length?t('Choose a {checkFileLabel} under Check the work.',{checkFileLabel}):
   t('No matching {checkFileLabel} was found in the project root for {check}. Choose another check, or add an existing root file under Additional files.',{checkFileLabel,check:checkEntry?.label??t('this check')})):
  !manual&&!testName?.test(testFile)?t('Choose a matching {checkFileLabel} for this check.',{checkFileLabel}):
  !projectAccess&&files.length>32?t('Choose no more than 32 individual files. Use folder access for larger scopes.'):
  !projectAccess&&!files.length&&!(manual&&folders.trim())?t('Choose the files Dervo can work with.'):
  !manual&&!timeoutValid?t('Set the maximum check time to 5 to 120 seconds.'):
  extraProblem?t('Give each extra check its own kind and a matching root file, or remove it.'):'';

 function toggle(name:string,on:boolean){setSelected(current=>on?unique([...current,name]):current.filter(f=>f!==name));}
 async function act(operation:string,args:Record<string,unknown>={}){
  setBusy(true);setError('');
  try{await local(operation,{projectId,channelId,...args});if(operation==='coding_effort_set')setEffortDirty(false);await permission.refetch();await refresh();if(operation==='coding_permission_grant')onOpenChange(false);}
  catch(e){setError(String(e));}
  finally{setBusy(false);}
 }
 // Full access and manual review ask whatever this setting says (conversation_coding.py sets acceptance 'full-access' and
 // 'manual-review'), so the off wording names the exception instead of reading as a contradiction.
 const ALWAYS_ASKS=t('Full-access tasks and changes you review yourself always wait for your approval, because no checks run in Dervo for them.');
 // The header states what this permission covers and, separately, what the host's result approval policy is. It never
 // promises a review the policy does not make: with approval off, a checked result counts as Completed without one.
 const approvalSummary=manual?t('No automated checks will run. Each changed result waits for your review, even when the project’s result approval setting is off.'):!approval?t('Result approval is a separate setting; its saved value is loading.')
  :approval.required?(approval.source==='legacy'?t('Result approval is on (kept from before this setting existed): each result waits for your approval or a correction request.'):t('Result approval is on: each result waits for your approval or a correction request.'))
  :t('Result approval is off: a checked result counts as Completed without your approval. {ALWAYS_ASKS}',{ALWAYS_ASKS});
 return <SettingsSectionFrame embedded={embedded} open={open} onOpenChange={onOpenChange} title={t('Project access')} description={t('Choose where Dervo can work and how it checks a result.')} titleRef={titleRef}>
  <p data-testid="coding-permission-summary" className="text-xs text-muted-foreground">{binding.cwd.split('/').filter(Boolean).pop()} · {binding.model}</p>
  <div className={embedded?"space-y-6 text-sm":"min-h-0 space-y-6 overflow-y-auto pr-2 text-sm"}>
   <p role="status" className="rounded-xl border bg-muted/20 p-3 leading-relaxed" data-testid="coding-permission-state">{data?.state==='granted'&&data.check_profile==='manual-review'?t('Coding is allowed. Changes wait for your review. No automated checks will run.'):data?.state==='granted'&&data.project_access?t('Coding is allowed in this project. Checks use {test_file}.',{test_file:data.test_file??''}):data?.state==='granted'?`${t('Allowed: {count} file(s)',{count:data.files.length})}${data.folders?.length?`, ${t('{count} folder(s)',{count:data.folders.length})}`:''}; ${t('tests run {test}',{test:data.test_file??''})}${data.check_profile&&data.check_profile!==PYTHON_CHECK?` (${data.check_profile})`:''}.`:data?.detail??(permission.error?t('Project access could not be loaded.'):t('Loading the project files.'))}</p>
   {permission.error&&<div role="alert" className="space-y-2 text-destructive"><p>{String(permission.error)}</p><Button variant="outline" onClick={()=>void permission.refetch()}>{t('Try again')}</Button></div>}
   <section className="space-y-4" aria-label={t('File access')}><h3 className="font-semibold">{t('Where Dervo can work')}</h3>
   {data?.project_access_available&&<>
    <div className="rounded-xl border bg-muted/20 p-4 space-y-2" data-testid="project-folder-access"><p className="font-medium">{projectAccess?t('This project folder'):t('Restricted access')}</p><p className="break-all text-xs text-muted-foreground">{binding.cwd}</p><p className="leading-relaxed">{projectAccess?t('Dervo can find, read, edit and create ordinary source files in this folder. You don’t need to pick each file.'):t('Your saved file and folder restrictions are still in place.')}</p>{!projectAccess&&<Button variant="outline" disabled={busy} onClick={()=>setProjectAccess(true)}>{t('Use project folder')}</Button>}</div>
    <div role="group" aria-label={t('How much of the project')} className="dervo-segmented"><button type="button" aria-pressed={projectAccess} disabled={busy} onClick={()=>setProjectAccess(true)}>{t('Whole folder')}</button><button type="button" aria-pressed={!projectAccess} disabled={busy} onClick={()=>setProjectAccess(false)}>{t('Selected files and folders')}</button></div>
   </>}
   {!projectAccess&&<>
   <fieldset className="max-h-48 space-y-2 overflow-auto rounded-xl border p-3" disabled={!data||busy}><legend className="px-1 text-xs text-muted-foreground">{t('Files coding may read or change')}</legend>
    {candidates.map(name=><label key={name} className="flex items-start gap-2 break-all"><input className="mt-0.5 shrink-0" type="checkbox" aria-label={t('Allow {name}',{name})} checked={selected.includes(name)} onChange={e=>toggle(name,e.target.checked)}/> {name}</label>)}
    {data&&candidates.length===0&&<p>{t('No usable files in the project root. Enter existing relative files below.')}</p>}
   </fieldset>
   <label className="block">{t('Additional files, one relative path per line')}<textarea aria-label={t('More files')} disabled={!data||busy} className="mt-1 w-full rounded border bg-background p-2" value={extra} onChange={e=>setExtra(e.target.value)}/></label>
   <label className="block">{t('Folders (optional)')}<p className="mt-1 text-xs text-muted-foreground">{t('Dervo can read, edit and create files inside these folders. Enter existing relative paths, one per line.')}</p><textarea aria-label={t('Granted folders')} disabled={!data||busy} className="mt-1 w-full rounded border bg-background p-2" placeholder={FOLDER_EXAMPLE} value={folders} onChange={e=>setFolders(e.target.value)}/></label>
   <div className="flex flex-wrap items-center gap-2"><Button type="button" size="sm" variant="outline" disabled={!data||busy} onClick={()=>void chooseFolder()}>{t('Choose a folder…')}</Button>{folderError&&<p role="alert" className="text-xs text-destructive">{folderError}</p>}</div>
   <details className="text-xs leading-relaxed text-muted-foreground"><summary className="cursor-pointer">{t('What folder access includes')}</summary><p className="mt-2" data-testid="creation-scope">{data?.creation_detail??t('A granted folder lets coding list, search, read, edit and create regular files there; each read or edited file is recorded in the task evidence. Dot names, secret-named paths, links and host data stay denied.')}</p></details></>}
   {projectAccess&&<p className="text-xs leading-relaxed text-muted-foreground">{t('Hidden files, credential paths, databases, links and generated folders are excluded. Only allow a project whose source you’re comfortable sharing with your chosen agent.')}</p>}</section>
   <section className="space-y-4 border-t pt-5" aria-label={t('Project checks')}><h3 className="font-semibold">{t('Check the work')}</h3>
   <p className="text-sm leading-relaxed text-muted-foreground">{t('Choose how you’ll review changes. You can start without a test suite.')}</p>
   <div className="grid gap-3 sm:grid-cols-2" role="group" aria-label={t('Ways to review changes')}>
    {checkProfiles.filter(p=>p.id==='manual-review'||p.id===check||matchingChecks(data!,p.id,files).length>0).map(p=><button key={p.id} type="button" aria-pressed={check===p.id} disabled={busy||!p.available} onClick={()=>chooseCheck(p.id)} className={`rounded-xl border p-4 text-left transition-colors motion-reduce:transition-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50 ${check===p.id?'border-primary bg-primary/10':'bg-background hover:bg-muted/40'}`}><span className="block font-medium">{p.label}</span><span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{p.id==='manual-review'?t('Try the changes yourself, then approve them. No automated checks.'):t('{length} matching check file(s) found. Runs before you review.',{length:matchingChecks(data!,p.id,files).length})}</span></button>)}
   </div>
   <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">{t('All available checks')}</summary>
   <label className="block">{t('Check')}<select aria-label={t('Check')} className="ml-3 rounded border bg-background p-2" value={check} disabled={!checkProfiles.length} onChange={e=>chooseCheck(e.target.value)}>{checkProfiles.length?checkProfiles.map(p=><option key={p.id} value={p.id} disabled={!p.available}>{p.label}</option>):<option value={PYTHON_CHECK}>{t('Python unittest')}</option>}</select></label></details>
   {['rust-check','rust-test'].includes(check)&&<label className="block space-y-2"><span>{t('Rust toolchain folder')}</span><input aria-label={t('Rust toolchain folder')} className="w-full rounded-lg border bg-background p-2" disabled={!data||busy} value={rustToolchain} onChange={e=>setRustToolchain(e.target.value)} placeholder={t('Absolute path to your installed toolchain')}/><span className="block text-xs leading-relaxed text-muted-foreground">{t('Choose the installed folder containing bin/rustc and lib, outside this project. Saving checks its files and remembers your choice for this project. Dervo doesn\'t download or install anything. Leave blank to use an administrator-configured toolchain.')}</span></label>}
   <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">{t('How this check runs')}</summary><p className="mt-2 leading-relaxed" data-testid="check-detail">{checkEntry?checkEntry.detail:t('One host check runs on the task snapshot; the host lists the available checks.')}</p></details>
   {!manual&&<><label className="block">{check==='typescript-check'?t('Check configuration'):['rust-check','rust-test'].includes(check)?t('Rust source file'):t('Test file')}<select aria-label={t('Test file')} disabled={!data||busy} className="ml-3 rounded border bg-background p-2" value={testFile} onChange={e=>setTestFile(e.target.value)}><option value="">{t('Choose the check file ({pattern})',{pattern:checkEntry?.test_pattern??'test_*.py'})}</option>{testOptions.map(f=><option key={f} value={f}>{f}</option>)}</select></label>
   
   <fieldset className="space-y-2 rounded-xl border p-3" data-testid="extra-checks"><legend className="px-1 text-xs text-muted-foreground">{t('Also allow these checks')}</legend>
   <p className="text-xs leading-relaxed text-muted-foreground">{t('For a project in more than one language. Each request runs one check; you pick which when you send it.')}</p>
   {extraChecks.map((entry,index)=><div key={index} className="flex flex-wrap items-center gap-2"><select aria-label={t('Extra check {number}',{number:index+1})} className="rounded border bg-background p-2" disabled={busy} value={entry.checkProfile} onChange={e=>editExtra(index,{checkProfile:e.target.value,testFile:(data?matchingChecks(data,e.target.value,files):[])[0]??''})}>{extraOptions.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select><select aria-label={t('Extra check {number} file',{number:index+1})} className="rounded border bg-background p-2" disabled={busy} value={entry.testFile} onChange={e=>editExtra(index,{testFile:e.target.value})}><option value="">{t('Choose its root file')}</option>{(data?matchingChecks(data,entry.checkProfile,files):[]).map(f=><option key={f} value={f}>{f}</option>)}</select>{['rust-check','rust-test'].includes(entry.checkProfile)&&<input aria-label={t('Extra check {number} toolchain',{number:index+1})} className="rounded border bg-background p-2" placeholder={t('Rust toolchain folder')} disabled={busy} value={entry.rustToolchain??''} onChange={e=>editExtra(index,{rustToolchain:e.target.value})}/>}<Button type="button" size="sm" variant="ghost" disabled={busy} onClick={()=>setExtraChecks(current=>current.filter((_,i)=>i!==index))}>{t('Remove')}</Button></div>)}
   <Button type="button" size="sm" variant="outline" disabled={busy||!extraOptions.length||extraChecks.length>=3} onClick={()=>{const next=extraOptions.find(p=>!extraChecks.some(c=>c.checkProfile===p.id))??extraOptions[0];setExtraChecks(current=>[...current,{checkProfile:next.id,testFile:(data?matchingChecks(data,next.id,files):[])[0]??''}]);}}>{t('Add another check')}</Button>
   </fieldset></>}
   {manual&&<p className="rounded-xl bg-muted/30 p-3 leading-relaxed" role="note">{t('Dervo saves the file changes and asks you to review them. An approval records your decision. It doesn’t mean tests passed.')}</p>}
   </section><section className="space-y-3 border-t pt-5" aria-label={t('Approving results')}><fieldset className="min-w-0 space-y-3" data-testid="result-approval" data-ready={approval?'true':'false'}><legend>{t('Result approval')}</legend>
    <label className="flex items-center gap-2"><input type="checkbox" aria-label={t('Require your approval of each result')} disabled={manual||!approval||approvalBusy} checked={manual||(approval?.required??false)} onChange={e=>void saveApproval(e.target.checked)}/> {t('Require your approval of each result')}</label>
    {(!approval||approvalBusy)&&<p role="status">{approval?t('Saving to the host.'):t('Loading the saved result approval setting.')}</p>}
    {approvalError&&<p role="alert" className="text-destructive">{approvalError}</p>}
   <p className="text-xs leading-relaxed text-muted-foreground" data-testid="approval-summary">{approvalSummary}</p></fieldset></section><details><summary className="cursor-pointer text-sm text-muted-foreground">{t('Advanced request settings')}</summary><section className="mt-4 grid gap-5 border-t pt-5 sm:grid-cols-2" aria-label={t('Request defaults')}>{!manual&&<label className="block space-y-2"><span>{t('Maximum check time')}</span><span className="flex items-center gap-2"><input aria-label={t('Maximum check time in seconds')} type="number" min={5} max={120} step={1} disabled={!data||busy} className="w-24 rounded-lg border bg-background p-2" value={checkTimeout} onChange={e=>setCheckTimeout(e.target.value)} aria-invalid={!timeoutValid}/> {t('seconds')}</span><span className="block text-xs leading-relaxed text-muted-foreground">{t('Choose 5 to 120 seconds for each check in new requests. Existing tasks keep their saved limit. You can use Stop while a check runs.')}</span></label>}
   <fieldset className="min-w-0 space-y-3" data-testid="owner-effort" data-ready={effortReady?'true':'false'}><legend>{t('Owner effort for {model}',{model:binding.model})}</legend>
    <div className="flex flex-wrap items-center gap-2">
     {binding.provider==='claude'
      ?<select aria-label={t('Owner effort')} className="w-full min-w-0 rounded-lg border bg-background p-2" disabled={!effortReady} value={effort} onChange={e=>editEffort(e.target.value)}><option value="">{t('Provider default')}</option>{CLAUDE_EFFORTS.map(level=><option key={level} value={level}>{level}</option>)}</select>
      :<input aria-label={t('Owner effort')} className="w-full min-w-0 rounded-lg border bg-background p-2" placeholder={t('Provider default')} disabled={!effortReady} value={effort} onChange={e=>editEffort(e.target.value)}/>}
     <Button variant="outline" disabled={busy||!supported||!effortReady} onClick={()=>void act('coding_effort_set',{effort:outgoingEffort(effort)})}>{t('Save effort')}</Button>
    </div>
    {!effortReady&&<p role="status">{t('Loading the saved owner effort.')}</p>}
   </fieldset>
   </section></details>
   {data?.state==='granted'&&data.project_access&&<FullAccessSection projectId={projectId} channelId={channelId} view={data.full_access} onChanged={async()=>{await permission.refetch();await refresh();}}/>}
   <p className="text-xs leading-relaxed text-muted-foreground">{data?.full_access?.enabled?(data.full_access.auto_commit?t('Full access is on: Claude Code runs commands as you in this folder, and Dervo commits finished results itself. Undo reverts one.'):t('Full access is on: Claude Code runs commands as you in this folder. Dervo commits nothing until you approve.')):projectAccess?t('Without full access, this stays inside the selected project: no shell, deployment, account changes or purchases.'):t('Not granted: the project root, files or folders outside these lists, a shell, other commands, deployment, accounts or spend.')}</p>
   <TaskDisclosure binding={binding} detailsTestId="coding-permission-details" summary={t('How this permission, the effort and result approval work')}>
    <p>{t('Checks run on isolated snapshots in confinement; a text answer never counts as a change. The check is chosen once here: the provider calls one run-tests tool and never picks an executable, arguments, a shell or an environment. A check whose runtime is missing is refused, not replaced. The Rust type check emits metadata only. It doesn\'t execute Rust tests. It requires a configured trusted compiler and writes only disposable metadata. The TypeScript check uses the project\'s own node_modules/typescript compiler and explicitly listed dependency inputs in a copied snapshot. Unlisted imports fail the check.')}</p>
    <p data-testid="folder-risk">{data?.folder_risk??t('Name rules do not prove the other files in a granted folder hold no secrets: the provider may read any regular file there. Grant only folders whose contents it may see.')}</p>
    <p data-testid="owner-effort-detail">{effortReady?data?.owner_effort_detail??'':t('Loading the saved owner effort.')} {binding.provider==='claude'?t('Claude Code takes only its documented --effort levels. The chosen level is forwarded as the exact --effort token; whether the installed Claude Code and this model honour it is not checked before content is sent.'):t('A Codex effort is a token the model catalog advertises; the host checks it at launch and refuses an unsupported one before sending content.')} {t('Blank means the provider default. The Independent reviewer keeps its own effort.')}</p>
    <p data-testid="result-approval-detail">{approval?`${approval.required?t('On'):t('Off')}${approval.source==='legacy'?` ${t('(kept from before this setting existed)')}`:''}. ${approval.detail}`:t('The saved result approval setting is still loading.')} {t('Changing this applies to new requests only: a pending result and every earlier approval record stay as they are. It is separate from Independent review.')}</p>
   </TaskDisclosure>
   {error&&<p role="alert" className="text-destructive">{error}</p>}
  </div><div className={embedded?"dervo-save-bar space-y-3":"shrink-0 space-y-3 border-t pt-4"}>{setupReason&&<p id="coding-setup-next" data-testid="coding-setup-next" role="status" className="text-sm leading-relaxed text-muted-foreground">{setupReason}</p>}<div className="flex flex-wrap gap-2"><Button aria-describedby={setupReason?'coding-setup-next':undefined} disabled={busy||!!setupReason} onClick={()=>void act('coding_permission_grant',grantPayload(files,testFile,folders.split('\n'),check,manual?'':rustToolchain,manual?5:Number(checkTimeout),projectAccess,extraChecks))}>{data?.state==='granted'?(projectAccess?t('Save project access'):t('Save file access')):t('Allow coding')}</Button>{data?.state==='granted'&&!revoking?<Button variant="outline" disabled={busy} onClick={()=>setRevoking(true)}>{t('Stop allowing coding')}</Button>:null}</div>{data?.state==='granted'&&revoking&&<div role="group" aria-label={t('Stop allowing coding')} className="flex flex-wrap items-center gap-2"><span>{t('Stop allowing coding in this project? New coding requests here will ask for permission again. Nothing already recorded changes.')}</span><Button variant="destructive" size="sm" disabled={busy} onClick={()=>{setRevoking(false);void act('coding_permission_revoke');}}>{t('Stop allowing')}</Button><Button variant="ghost" size="sm" disabled={busy} onClick={()=>setRevoking(false)}>{t('Keep allowing')}</Button></div>}</div>
   {!supported&&<p>{t('Coding needs a Codex or Claude Code account binding.')}</p>}
  </SettingsSectionFrame>;
}
