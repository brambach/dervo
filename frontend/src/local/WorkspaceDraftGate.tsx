import {useEffect,useRef,useState,type ReactNode} from 'react';
import {getAllDraftEntries,restoreWorkspaceDrafts,subscribeToStore} from '@/features/messages/lib/useDrafts';
import {local} from './api';
import {WorkspaceDraftPersistence,registerWorkspaceDraftPersistence,type DraftSaveStatus} from './workspaceDraftPersistence';
import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import './Surfaces.css';
import {Button} from '@/shared/ui/button';
import {t} from '@/i18n';

export function WorkspaceDraftGate({children}:{children:ReactNode}){
  const [ready,setReady]=useState(false),[attempt,setAttempt]=useState(0);
  const [status,setStatus]=useState<DraftSaveStatus>({kind:'loading'});
  const controller=useRef<WorkspaceDraftPersistence|undefined>(undefined);
  const content=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    let alive=true,closing=false,unsubscribe=()=>{},unregister=()=>{},unlisten=()=>{};
    const persistence=new WorkspaceDraftPersistence(local,s=>{if(alive)setStatus(s);});
    controller.current=persistence;
    persistence.open(getAllDraftEntries()).then(async entries=>{
      if(!alive)return;
      restoreWorkspaceDrafts(entries);
      unsubscribe=subscribeToStore(()=>persistence.changed(getAllDraftEntries()));
      unregister=registerWorkspaceDraftPersistence(persistence);
      const stop=await listen('dervo-save-before-exit',async()=>{
        if(!alive||closing)return;
        closing=true;
        if(content.current)content.current.inert=true;
        try{await persistence.beforeExit();if(alive)await invoke('draft_exit');}
        catch{
          closing=false;if(content.current)content.current.inert=false;
          if(alive)setStatus({kind:'error',detail:t('Your drafts aren’t saved yet. Retry or copy your text before closing Dervo.')});
        }
      });
      if(!alive){stop();return;}
      unlisten=stop;
      await invoke('draft_guard_ready');
      if(!alive)return;
      setReady(true);
    }).catch(()=>{if(alive)setStatus({kind:'error',detail:t('Dervo couldn’t open your saved drafts. Retry to keep them with this workspace.')});});
    return()=>{alive=false;unsubscribe();unregister();unlisten();persistence.close();};
  },[attempt]);
  if(!ready)return <main className="grid min-h-screen place-content-center gap-4 p-8" aria-busy={status.kind!=='error'}>
    <h1 className="text-xl font-semibold">{status.kind==='error'?t('Your drafts need a moment'):t('Opening your saved drafts')}</h1>
    {status.kind==='error'&&<><p role="alert">{status.detail}</p><Button onClick={()=>{setStatus({kind:'loading'});setAttempt(v=>v+1);}}>{t('Try again')}</Button></>}
  </main>;
  return <><div ref={content} className="contents">{children}</div>{status.kind==='error'&&<aside className="dv-toast-fixed" role="alert">{/* A failure toast (surfaces kit): over the sidebar foot, never the composer, and it stays until the drafts save. */}<p>{status.detail}</p><span className="dv-acts"><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void controller.current?.retry()}>{t('Retry')}</button></span></aside>}</>;
}
