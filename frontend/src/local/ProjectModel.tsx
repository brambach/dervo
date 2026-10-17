import {useRef,useState} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {Input} from '@/shared/ui/input';
import {ClaudeModels} from './ClaudeModels';
import {CodexModels} from './CodexModels';
import {local} from './api';
import './ProjectSetup.css';
import {t} from '@/i18n';

/** The project's default model for new conversations, or, with ``channelId``, one conversation's own model for its
 * future requests. The account never changes here: a conversation keeps the profile it was created with, so the
 * provider is fixed and only that provider's models are offered. */
export function ProjectModel({projectId,channelId,profileId,provider,savedModel,bare=false}:{projectId:string;channelId?:string;profileId:string;provider:string;savedModel:string;bare?:boolean}){
  const client=useQueryClient();
  const [editing,setEditing]=useState(false),[model,setModel]=useState(savedModel);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const flight=useRef(false);
  const conversation=!!channelId;
  async function save(){
    if(flight.current)return;
    flight.current=true;setBusy(true);setError('');
    try{
      if(conversation){
        await local('conversation_model_set',{projectId,channelId,expectedModel:savedModel,model});
        await client.invalidateQueries({queryKey:['local-binding',projectId,channelId]});
        await client.invalidateQueries({queryKey:['local-conversations',projectId]});
      }else{
        await local('project_model_set',{projectId,expectedModel:savedModel,model});
        await client.invalidateQueries({queryKey:['local-projects']});
      }
      setEditing(false);
    }catch(e){setError(String(e));}
    finally{flight.current=false;setBusy(false);}
  }
  const heading=conversation?t('Model for this conversation'):t('Model for new conversations');
  const note=conversation
    ?t('Your next requests here use it. Tasks already recorded in this conversation keep the model they were approved with and can’t be continued after a change.')
    :t('Existing conversations and reviewers keep their current model. Start a new conversation to use your selection.');
  const saveLabel=conversation?t('Save conversation model'):t('Save default model');
  const changeLabel=conversation?t('Change conversation model'):t('Change default model');
  return <section className="space-y-3" aria-label={conversation?t('Conversation model'):t('Default model')}>
    {bare?<p className="text-xs text-muted-foreground">{note}</p>:<div><h3 className="font-semibold">{heading}</h3><p className="text-sm text-muted-foreground">{note}</p></div>}
    {editing?<>
      {provider==='claude'?<ClaudeModels model={model} onSelect={setModel} disabled={busy}/>:<CodexModels profileId={profileId} model={model} onSelect={setModel} cards disabled={busy}/>}
      <details className="dervo-setup-custom-model"><summary>{t('Use a different model ID')}</summary><Input aria-label={conversation?t('Custom conversation model ID'):t('Custom default model ID')} value={model} disabled={busy} onChange={e=>setModel(e.target.value)}/><p>{t('Checked when a task starts. Dervo won\'t substitute another model.')}</p></details>
      <div className="flex gap-2"><Button disabled={busy||!model.trim()||model!==model.trim()||model===savedModel} onClick={()=>void save()}>{busy?t('Saving…'):saveLabel}</Button><Button variant="ghost" disabled={busy} onClick={()=>{setEditing(false);setModel(savedModel);setError('');}}>{t('Cancel')}</Button></div>
    </>:<Button variant="outline" onClick={()=>{setModel(savedModel);setEditing(true);}}>{changeLabel}</Button>}
    {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
  </section>;
}
