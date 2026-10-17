import {useEffect,useRef,useState} from 'react';
import {useQueries,useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {connectionStatusQuery} from './ConnectionPreflight';
import {Segmented} from './DervoControls';
import {StatusGlyph} from './StatusGlyph';
import {claudeDefaultModel} from './models';
import type {SideAccount} from './onboarding';
import {t} from '@/i18n';

/** Work accounts (Bryce, 26 Sep 2026: "make sure both sides work"; evidence/design-tests/b16-work). A work account is its
 * own Claude Code or Codex sign-in, in a folder of its own, and only ever runs projects under Work. Adding one uses the
 * same host operations as Settings (add_profile, connection_login, connection_login_status): the provider's own
 * sign-in, then the ordinary identity check, which says by name when the sign-in came back as your personal account. */

export type AccountProfile={id:string;provider:string;identity:string;category:string;method?:string;name?:string|null;plan?:string|null};
type Check={state:string;canSend:boolean;detail:string;otherSide?:string;signedInAs?:string};
export type Provider='claude'|'codex';
export const TOOL:Record<Provider,{cli:string;label:string;site:string}>={
 claude:{cli:'Claude Code',label:'Claude',site:'claude.ai'},
 codex:{cli:'Codex',label:'ChatGPT',site:'chatgpt.com'}};
import {planWords,SignInToAdd} from './AccountSignIn';
export {planWords};
const toolOf=(provider:string)=>TOOL[provider as Provider]??TOOL.claude;

/** The work side as it stands: a verified work account and the model its projects start on, or why there isn't one. */
export type WorkSide={state:'none'|'checking'|'ready'|'signed_out'|'problem';profile?:AccountProfile;model?:string;
 /** "Claude Code · you@acme.io", for the Work group's head. */
 who:string;line:string;prepare:()=>Promise<SideAccount>};

export function useWorkSide(profiles:AccountProfile[],claudeVersion?:string|null):WorkSide{
 const work=profiles.filter(profile=>profile.category==='work');
 const checks=useQueries({queries:work.map(profile=>({...connectionStatusQuery(profile.id),refetchInterval:(query:{state:{data?:Check}})=>query.state.data?.state==='checking'?600:false}))});
 const asked=useRef(new Set<string>());
 // A work account nobody has checked since Dervo opened is checked once: its sign-in and plan, never a model call.
 useEffect(()=>{work.forEach((profile,n)=>{const state=(checks[n]?.data as Check|undefined)?.state;
  if(state==='unchecked'&&!asked.current.has(profile.id)){asked.current.add(profile.id);void local('connection_check',{profileId:profile.id}).then(()=>checks[n]?.refetch()).catch(()=>{});}});},
  [work.map(p=>p.id).join(),checks.map(c=>(c.data as Check|undefined)?.state).join()]);
 const states=work.map((profile,n)=>({profile,check:checks[n]?.data as Check|undefined}));
 const ready=states.find(s=>s.check?.state==='identity_verified')?.profile;
 const codex=useQuery({queryKey:['codex-models',ready?.id??''],enabled:ready?.provider==='codex',
  queryFn:()=>local<{state:string;detail:string;models:{model:string;hidden?:boolean}[]}>('model_status',{profileId:ready!.id}),
  refetchInterval:query=>query.state.data?.state==='checking'?600:false});
 useEffect(()=>{if(ready?.provider==='codex'&&codex.data?.state==='unchecked')void local('model_check',{profileId:ready.id}).then(()=>codex.refetch()).catch(()=>{});},[codex.data?.state,ready?.id]);
 const model=ready?(ready.provider==='codex'?codex.data?.models.find(m=>!m.hidden)?.model:claudeDefaultModel(claudeVersion)):undefined;
 const shown=ready??states.find(s=>s.check?.state==='signed_out')?.profile??work[0];
 const who=shown?`${toolOf(shown.provider).cli} · ${shown.identity}`:'';
 const state:WorkSide['state']=!work.length?'none':ready?(model?'ready':'checking')
  :states.some(s=>!s.check||s.check.state==='checking'||s.check.state==='unchecked')?'checking'
  :states.some(s=>s.check?.state==='signed_out')?'signed_out':'problem';
 const line=state==='none'?t('No work account yet'):state==='checking'?t('Checking your work account'):state==='signed_out'?t('{who} is signed out',{who})
  :state==='problem'?t('Your work account needs signing in again'):who;
 return {state,profile:shown,model,who,line,prepare:async()=>{
  if(!ready||!model)throw new Error(t('Your work account isn’t ready yet.'));
  return {profileId:ready.id,model};
 }};
}

/** Adding a work account, in place (suggest-add-work and suggest-add-same): which tool, then the provider's own sign-in
 * in a folder of its own, then which account it is (beta.18: no email to type first; AccountSignIn.tsx). A sign-in that
 * comes back as your personal account is said by name and never added. */
export function AddWorkAccount({onChanged,onCancel,onVerified}:{profiles?:AccountProfile[];onChanged:()=>Promise<unknown>;onCancel:()=>void;
 onVerified:(profile:AccountProfile)=>void}){
 const client=useQueryClient();
 const [provider,setProvider]=useState<Provider>('claude'),[signing,setSigning]=useState(false);
 const go=useRef<HTMLButtonElement>(null);
 const tool=TOOL[provider];
 useEffect(()=>{if(!signing)requestAnimationFrame(()=>go.current?.focus());},[signing]);
 const head=<div className="dv-ob-add-head"><h4>{t('Add a work account')}</h4><span className="dv-caption">{t('Only ever runs projects under Work')}</span></div>;
 const which=<Segmented label={t('Which tool')} value={provider} disabled={signing} onChange={next=>setProvider(next)}
  options={[{value:'claude',label:'Claude Code'},{value:'codex',label:'Codex'}]}/>;
 return <div className="dv-ob-add" role="group" aria-label={t('Add a work account')}>
  {head}{which}
  {signing?<SignInToAdd key={provider} provider={provider} category="work" onCancel={()=>setSigning(false)} onAdded={async added=>{
    await client.invalidateQueries({queryKey:['connection-status',added.id]});await onChanged();
    onVerified({id:added.id,provider,identity:added.identity??'',category:'work',name:added.name??null,plan:added.plan??null});}}/>
   :<>
    <p className="dv-field-note">{t('The {label} account your employer gave you. {cli}’s own sign-in opens in your browser, in a folder of its own on this Mac, apart from your personal one. Dervo shows which account signed in before adding it.',{label:tool.label,cli:tool.cli})}</p>
    <div className="dv-card-actions"><span className="dv-caption">{t('Dervo never sees your password.')}</span>
     <button type="button" className="dv-btn dv-btn-quiet" onClick={onCancel}>{t('Cancel')}</button>
     <button ref={go} type="button" className="dv-btn dv-btn-primary" onClick={()=>setSigning(true)}>{t('Sign in with {label}',{label:tool.label})}</button></div>
   </>}
 </div>;
}

/** Signed in: which account it is and what it's for (suggest-work-added). */
export function WorkAdded({profile,runs,onChange}:{profile:AccountProfile;runs:string[];onChange:()=>void}){
 const what=runs.length?t('{names} and anything else under Work',{names:runs.slice(0,3).join(', ')}):t('anything under Work');
 return <div className="dv-ob-done" role="status"><StatusGlyph state="done"/>
  <div><p><b>{t('Work account added.')}</b> {[toolOf(profile.provider).cli,profile.identity,planWords(profile.plan)].filter(Boolean).join(' · ')}</p>
   <span className="dv-caption">{t('Runs {what}. It never runs a Personal project.',{what})}</span></div>
  <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={onChange}>{t('Change')}</button></div>;
}

/** The sign-in state of one account, checked once if nobody has since Dervo opened (a provider status read, never a
 * model call). '' checks nothing. */
export function useAccountCheck(profileId:string):string|undefined{
 const check=useQuery({...connectionStatusQuery(profileId),enabled:!!profileId,refetchInterval:query=>query.state.data?.state==='checking'?600:false});
 const asked=useRef(new Set<string>());
 useEffect(()=>{if(profileId&&check.data?.state==='unchecked'&&!asked.current.has(profileId)){asked.current.add(profileId);
  void local('connection_check',{profileId}).then(()=>check.refetch()).catch(()=>{});}},[profileId,check.data?.state]);
 return check.data?.state;
}

/** A Work thread whose work account is signed out, said where you'd type (thread-signed-out), with the two ways on. It
 * never offers a personal account instead. */
export function WorkSignedOut({name,profile,project,mismatch,onSignIn,onOther}:{name:string;profile:AccountProfile;project:string;mismatch:boolean;
 onSignIn:(trigger:HTMLElement)=>void;onOther:()=>void}){
 const tool=toolOf(profile.provider).label;
 return <div className="dv-work-out" role="status"><StatusGlyph state="needs" accent/><div>
  <p><b>{mismatch?t('{name}, your work {tool} account, is signed in as someone else.',{name,tool}):t('{name}, your work {tool} account, is signed out.',{name,tool})}</b> {t('Sign in as {identity} again to run {project}. Dervo won’t run it on a personal account instead.',{identity:profile.identity,project})}</p>
  <div className="dv-ob-acts"><button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={event=>onSignIn(event.currentTarget)}>{t('Sign in again')}</button>
   <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={onOther}>{t('Use another work account')}</button></div>
 </div></div>;
}
