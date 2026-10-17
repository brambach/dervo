import {useState} from 'react';
import {Button} from '@/shared/ui/button';
import {Input} from '@/shared/ui/input';
import {local} from './api';
import {providerAuthor} from './providerLabel';
import {SignInToAdd} from './AccountSignIn';
import './ProjectSetup.css';
import {t} from '@/i18n';

/** Add a Claude Code or Codex account: sign in with a subscription (the provider's own browser flow starts at once and
 * Dervo shows who signed in before adding it, AccountSignIn.tsx, beta.18), or use an API key and pay per use. A key is checked once with the provider, then kept in the Mac's Keychain (Claude) or by Codex
 * inside its Dervo-owned folder. The key is sent to the host once and never shown, saved locally or repeated in an error. */
export type Method='subscription'|'api_key';
export const LIMITS=[20,50,100,null] as const;
const KEY_LABEL:Record<string,string>={claude:t('Anthropic API key'),codex:t('OpenAI API key')};
const KEY_WHERE:Record<string,string>={claude:'console.anthropic.com',codex:'platform.openai.com'};
const KEY_PREFIX:Record<string,string>={claude:'sk-ant-',codex:'sk-'};
const PLAN:Record<string,string>={claude:t('Uses your Claude Pro or Max plan. Sign-in finishes in Claude’s own window.'),codex:t('Uses your ChatGPT plan. Sign-in finishes in OpenAI’s own window.')};
const STORED:Record<string,string>={claude:t('The key is checked once with Anthropic, kept in your Mac’s Keychain, and passed only to Claude Code on this Mac.'),codex:t('The key is checked once with OpenAI, then Codex saves it in a file in its Dervo folder on this Mac. Anything running as you can read that file, full-access runs included.')};
/** What the monthly limit does, and doesn't (security review, 19 Sep, finding 2): Dervo shows spend against it and never
 * stops a run. Codex reports tokens but no cost, so a Codex key's limit isn't measured at all. */
export const LIMIT_NOTE:Record<string,string>={
  claude:t('Dervo shows this month’s spend against the limit, from Claude Code’s own cost estimate. The limit doesn’t stop runs. For a hard stop, set a spend limit in the Anthropic Console.'),
  codex:t('Codex doesn’t tell Dervo what a run costs, so the limit isn’t measured yet, and it doesn’t stop runs. For a hard stop, set a budget on the OpenAI platform.')};
export function limitLabel(limit:number|null){return limit===null?t('No limit'):`$${limit}`;}
/** Error text never repeats a pasted key, whatever the message contained. */
export function maskKey(error:unknown,key:string):string{const text=String(error),secret=key.trim();return secret.length>=8?text.split(secret).join('[key]'):text;}

export function AddAccountForm({defaultProvider='codex',onAdded,onCancel}:{defaultProvider?:string;onAdded:(profile:{id:string;provider:string;method:Method})=>Promise<unknown>;onCancel?:()=>void}){
  const [provider,setProvider]=useState(defaultProvider),[method,setMethod]=useState<Method>('subscription');
  const [key,setKey]=useState(''),[limit,setLimit]=useState<number|null>(50),[signing,setSigning]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(){
    if(busy)return;
    setBusy(true);setError('');
    try{
      if(method==='api_key'){
        const added=await local<{id:string}>('profile_key_add',{provider,key,...(limit===null?{}:{monthlyLimitUsd:limit})});
        setKey('');
        await onAdded({id:added.id,provider,method});
      }else setSigning(true);
    }catch(e){setError(maskKey(e,key));}
    finally{setBusy(false);}
  }
  const ready=method==='api_key'?key.trim().length>=8:true;
  if(signing)return <div className="dervo-setup-connect"><h3>{t('Connect an account')}</h3>
    <SignInToAdd provider={provider==='claude'?'claude':'codex'} category="personal" onCancel={()=>setSigning(false)} onAdded={added=>onAdded({id:added.id,provider,method:'subscription'})}/></div>;
  return <form className="dervo-setup-connect" onSubmit={e=>{e.preventDefault();void submit();}}>
    <h3>{t('Connect an account')}</h3>
    <div className="dervo-setup-provider-choice" role="group" aria-label={t('Provider')}>{['codex','claude'].map(p=><button type="button" key={p} disabled={busy} aria-pressed={provider===p} onClick={()=>{setProvider(p);setError('');}}>{providerAuthor(p)}</button>)}</div>
    <div className="dervo-setup-provider-choice dervo-setup-methods" role="group" aria-label={t('How to connect')}>
      <button type="button" disabled={busy} aria-pressed={method==='subscription'} onClick={()=>setMethod('subscription')}><strong>{t('Sign in with {provider}',{provider:provider==='claude'?'Claude':'ChatGPT'})}</strong><small>{PLAN[provider]}</small></button>
      <button type="button" disabled={busy} aria-pressed={method==='api_key'} onClick={()=>setMethod('api_key')}><strong>{t('Use an API key')}</strong><small>{t('Pay per use, billed to the key’s account. Get one at {site}.',{site:KEY_WHERE[provider]})}</small></button>
    </div>
    {method==='api_key'?<>
      <label>{KEY_LABEL[provider]}<Input aria-label={KEY_LABEL[provider]} type="password" autoComplete="off" spellCheck={false} placeholder={`${KEY_PREFIX[provider]}…`} required disabled={busy} value={key} onChange={e=>setKey(e.target.value)}/></label>
      <div className="dervo-setup-limit"><span id="dervo-limit-label">{t('Monthly spend limit in Dervo')}</span><div className="dervo-setup-provider-choice" role="group" aria-labelledby="dervo-limit-label">{LIMITS.map(value=><button type="button" key={String(value)} disabled={busy} aria-pressed={limit===value} onClick={()=>setLimit(value)}>{limitLabel(value)}</button>)}</div></div>
      <p>{STORED[provider]} {LIMIT_NOTE[provider]}</p>
    </>:<p>{t('Sign in with {provider}, and Dervo shows which account signed in before adding it. Dervo never sees your password.',{provider:providerAuthor(provider)})}</p>}
    <div className="dervo-setup-inline-actions"><Button type="submit" variant="outline" disabled={busy||!ready}>{busy?(method==='api_key'?t('Checking the key…'):t('Adding…')):method==='api_key'?t('Check and add key'):t('Sign in')}</Button>{onCancel&&<Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>{t('Cancel')}</Button>}</div>
    {error&&<p role="alert" className="dervo-setup-error">{error}</p>}
  </form>;
}
