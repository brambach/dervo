import {useState} from 'react';
import {Button} from '@/shared/ui/button';
import {local} from './api';
import {t} from '@/i18n';

export type Proposal={id:string;message_id:string;state:string;files:string[];test_file:string|null;reason:string|null;source:string;provider_proposal:string;task_id:string|null;text:string};

/** A drafted scope under the message it came from. Accepting creates a task that still needs execution approval. */
export function ScopeProposalCard({proposal,projectId,channelId,refresh,onAdjust}:{proposal:Proposal;projectId:string;channelId:string;refresh:()=>Promise<unknown>;onAdjust:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function act(operation:string){setBusy(true);setError('');try{await local(operation,{projectId,channelId,proposalId:proposal.id});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  if(proposal.state==='dismissed')return null;
  return <div className="mt-1 max-w-xl space-y-1 rounded-md border bg-muted/40 p-2 text-2xs text-muted-foreground" data-testid="scope-proposal">
    <p className="text-foreground">{proposal.state==='proposed'?t('Proposed coding scope'):proposal.state==='accepted'?t('Scope accepted'):t('Scope needs your input')}</p>
    {proposal.files.length>0&&<pre className="whitespace-pre-wrap break-all">{proposal.files.join('\n')}</pre>}
    {proposal.test_file&&<p>{t('Host test file: {file}',{file:proposal.test_file})}</p>}
    {proposal.reason&&<p role="alert">{proposal.reason}</p>}
    <p>{t('Drafted by Dervo from your message, without a model. Provider-drafted scope: {scope}. Nothing runs until you approve execution.',{scope:proposal.provider_proposal})}</p>
    {proposal.state==='proposed'&&<div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy} onClick={()=>void act('proposal_accept')}>{t('Accept scope')}</Button><Button size="sm" variant="outline" disabled={busy} onClick={onAdjust}>{t('Adjust manually')}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={()=>void act('proposal_dismiss')}>{t('Dismiss')}</Button></div>}
    {proposal.state==='needs_input'&&<div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={onAdjust}>{t('Enter scope manually')}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={()=>void act('proposal_dismiss')}>{t('Dismiss')}</Button></div>}
    {proposal.state==='accepted'&&<p>{t('Review the execution scope on the task below before anything starts.')}</p>}
    {error&&<p role="alert" className="text-destructive">{error}</p>}
  </div>;
}
