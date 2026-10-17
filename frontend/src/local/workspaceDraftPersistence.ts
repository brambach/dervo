import type {DraftState} from '@/features/messages/lib/useDrafts';
import {t} from '@/i18n';

type Entry={key:string;projectId:string;channelId:string;revision:number;draft:DraftState|null};
type Draft={key:string;draft:DraftState};
type Call=<T>(operation:string,args?:Record<string,unknown>)=>Promise<T>;
type Save={projectId:string;channelId:string;revision:number;draft:DraftState|null;mutationId:string};
export type DraftSaveStatus={kind:'loading'|'saving'|'saved'|'error';detail?:string};

/** One writer per workspace. An uncertain save retries its original mutation first. */
export class WorkspaceDraftPersistence {
  private known=new Map<string,Entry>();
  private desired=new Map<string,DraftState>();
  private pending:{key:string;save:Save}|undefined;
  private running:Promise<void>|undefined;
  private timer:ReturnType<typeof setTimeout>|undefined;
  private failed=false;
  private closed=false;
  private call:Call;
  private status:(value:DraftSaveStatus)=>void;
  constructor(call:Call,status:(value:DraftSaveStatus)=>void){this.call=call;this.status=status;}
  async open(browser:Draft[]):Promise<Draft[]> {
    const result=await this.call<{entries:Entry[]}>('drafts_list');
    if(this.closed)return [];
    this.known=new Map(result.entries.map(e=>[e.key,e]));
    const old=new Map(browser.map(e=>[e.key,e.draft]));
    for(const e of result.entries){
      const draft=e.draft??(e.revision===0?old.get(e.key):null);
      if(draft)this.desired.set(e.key,draft);
    }
    await this.flush();
    // A refused legacy draft must remain editable so it can be shortened or copied.
    return [...this.desired].map(([key,draft])=>({key,draft}));
  }
  changed(drafts:Draft[]){
    if(this.closed)return;
    this.desired=new Map(drafts.filter(e=>e.key.startsWith('dervo:')).map(e=>[e.key,structuredClone(e.draft)]));
    if(this.timer)clearTimeout(this.timer);
    if(!this.failed)this.status({kind:'saving'});
    this.timer=setTimeout(()=>{this.timer=undefined;void this.flush();},200);
  }
  async retry(){
    if(this.pending){
      try{
        const latest=await this.call<{entries:Entry[]}>('drafts_list');
        const current=this.pending,entry=latest.entries.find(e=>e.key===current.key);
        if(!entry&&!this.desired.has(current.key)){
          this.known.delete(current.key);this.pending=undefined;
        }else if(entry&&(entry.revision===current.save.revision||
          (entry.revision===current.save.revision+1&&JSON.stringify(entry.draft)===JSON.stringify(current.save.draft)))){
          // A current read proves whether this save committed. Only then may newer text replace it.
          this.known.set(current.key,entry);this.pending=undefined;
        }
      }catch{return;}
    }
    this.failed=false;await this.flush();
  }
  async beforeExit(){await this.flush();if(this.failed)throw new Error(t('Save your drafts before closing Dervo.'));}
  async flush():Promise<void>{
    if(this.timer){clearTimeout(this.timer);this.timer=undefined;}
    if(this.running)return this.running;
    if(this.closed||this.failed)return;
    this.running=this.drain().finally(()=>{this.running=undefined;});
    return this.running;
  }
  private async drain(){
    while(!this.closed){
      if(!this.pending){
        const keys=new Set([...this.known.keys(),...this.desired.keys()]);
        for(const key of keys){
          const prior=this.known.get(key),draft=this.desired.get(key)??null;
          if(JSON.stringify(prior?.draft??null)===JSON.stringify(draft))continue;
          const parts=/^dervo:([^:]+):([^:]+)$/.exec(key);
          if(!parts){this.failed=true;this.status({kind:'error',detail:t('This draft couldn’t be saved. Copy your text before closing Dervo.')});return;}
          this.pending={key,save:{projectId:parts[1],channelId:parts[2],revision:prior?.revision??0,draft,mutationId:crypto.randomUUID()}};
          break;
        }
      }
      if(!this.pending){this.status({kind:'saved'});return;}
      const current=this.pending;
      this.status({kind:'saving'});
      try{
        const result=await this.call<{revision:number}>('draft_save',current.save);
        if(!Number.isSafeInteger(result.revision)||result.revision!==current.save.revision+1)throw new Error(t('Invalid draft acknowledgement'));
        this.known.set(current.key,{key:current.key,...current.save,revision:result.revision});
        this.pending=undefined;
      }catch{
        // Project deletion can remove a saved draft before its local removal reaches us.
        if(current.save.draft===null){
          try{
            const latest=await this.call<{entries:Entry[]}>('drafts_list');
            if(!latest.entries.some(e=>e.key===current.key)){
              this.known.delete(current.key);this.pending=undefined;continue;
            }
          }catch{/* Keep the uncertain operation available for an explicit retry. */}
        }
        this.failed=true;
        this.status({kind:'error',detail:t('Your latest draft changes aren’t saved yet. Retry or copy your text before closing Dervo.')});
        return;
      }
    }
  }
  close(){this.closed=true;if(this.timer)clearTimeout(this.timer);}
}

let active:WorkspaceDraftPersistence|undefined;
export function registerWorkspaceDraftPersistence(controller:WorkspaceDraftPersistence){
  active=controller;
  return()=>{if(active===controller)active=undefined;};
}
export async function flushWorkspaceDrafts(){await active?.beforeExit();}
