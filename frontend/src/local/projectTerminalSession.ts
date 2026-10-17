import {local} from './api';
import {HIDDEN_CHARACTERS} from '@/shared/ui/markdown/replyActions';
import {t} from '@/i18n';

export type TerminalSession={sessionId:string;projectId:string;state:'running'|'exited'|'closed'|'closing';cwd:string;cols:number;rows:number;nextInputSeq:number;startCursor:number;endCursor:number;exitCode:number|null;pendingInputBytes:number;
 /** The command the shell is running now, by name, or null at the prompt. From the host's read of the pty's foreground group. */
 foreground?:string|null};
/** A command sent from a reply or Settings, shown above the output as a labelled block. Dervo can't see where its output
 * starts and ends in the shell, so a block says what was sent and when, and the header says whether something runs. */
export type TerminalRun={id:string;command:string;label:string;at:number;sessionId:string};
export type TerminalRead=TerminalSession&{dataBase64:string;nextCursor:number;dropped:number};
export type TerminalAck={sessionId:string;sequence:number;nextInputSeq:number;acceptedBytes:number;pendingInputBytes:number};
export type TerminalRequest=<T>(operation:string,args:Record<string,unknown>)=>Promise<T>;
type Action='load'|'start'|'read'|'input'|'input-limit'|'resize'|'close';
export type TerminalView={session:TerminalSession|null;busy:'load'|'start'|'close'|null;issue:{action:Action;message:string}|null;lostOutput:boolean;queuedBytes:number;runs:TerminalRun[]};
type Sink={write:(bytes:Uint8Array)=>Promise<void>;reset:()=>void};
type Input={bytes:Uint8Array;sequence?:number};
const INPUT_CHUNK=16*1024,INPUT_LIMIT=64*1024,RUNS_KEPT=6;
/** Whether the empty screen says what the terminal is: only before any shell, when there's no output under it. An ended
 * shell keeps its output, and a line centred over it printed on top of the last lines; the header's "Exited" and New
 * terminal already say it ended (design review, 23 Sep). */
export const terminalIdle=(view:TerminalView)=>!view.session&&!view.busy&&!view.issue;
export function terminalBytes(text:string,binary=false):Uint8Array{return binary?Uint8Array.from(text,character=>character.charCodeAt(0)&255):new TextEncoder().encode(text);}
export function terminalBase64(bytes:Uint8Array):string{return btoa(String.fromCharCode(...bytes));}
export function terminalOutput(data:string):Uint8Array{return Uint8Array.from(atob(data),character=>character.charCodeAt(0));}
export function terminalSize(cols:number,rows:number){return {cols:Math.max(2,Math.min(500,Math.floor(cols)||80)),rows:Math.max(1,Math.min(300,Math.floor(rows)||18))};}
const message=(error:unknown,fallback:string)=>error instanceof Error?error.message:typeof error==='string'?error:fallback;

/** One scoped shell connection. Hiding or replacing a view never closes it or replays uncertain input with a new ID. */
export class ProjectTerminalController{
 readonly projectId:string;
 snapshot:TerminalView={session:null,busy:'load',issue:null,lostOutput:false,queuedBytes:0,runs:[]};
 private listeners=new Set<(view:TerminalView)=>void>();
 private sink:Sink|null=null;
 private viewGeneration=0;
 private inputGeneration=0;
 private cursor=0;
 private readBusy=false;
 private writeBusy=false;
 private operationBusy=false;
 private openingId:string|null=null;
 private input:Input[]=[];
 private size={cols:80,rows:18};
 private resizeBusy=false;
 private resizeWanted=false;
 private request:TerminalRequest;
 private newId:()=>string;
 constructor(projectId:string,request:TerminalRequest=local,newId=()=>crypto.randomUUID()){this.projectId=projectId;this.request=request;this.newId=newId;}
 get attached(){return this.sink!==null;}
 get retain(){return this.operationBusy||this.writeBusy||this.input.length>0;}
 subscribe(listener:(view:TerminalView)=>void){this.listeners.add(listener);listener(this.snapshot);return()=>{this.listeners.delete(listener);};}
 private update(patch:Partial<TerminalView>){this.snapshot={...this.snapshot,...patch};for(const listener of this.listeners)listener(this.snapshot);}
 private problem(action:Action,error:unknown,fallback:string){this.update({issue:{action,message:message(error,fallback)}});}
 attach(sink:Sink){
  this.sink=sink;this.viewGeneration++;this.cursor=0;sink.reset();
  return()=>{if(this.sink===sink){this.sink=null;this.viewGeneration++;}};
 }
 private accept(session:TerminalSession){
  if(session.projectId!==this.projectId)throw Error(t('The terminal response belongs to another project.'));
  const previous=this.snapshot.session;
  if(previous?.sessionId!==session.sessionId){this.cursor=0;this.viewGeneration++;this.sink?.reset();this.update({lostOutput:false});}
  this.update({session:{...session,nextInputSeq:previous?.sessionId===session.sessionId?Math.max(previous.nextInputSeq,session.nextInputSeq):session.nextInputSeq}});
 }
 async load(){
  if(this.operationBusy)return;
  if(this.input.length||this.writeBusy)return;
  this.operationBusy=true;this.update({busy:'load',issue:null});
  try{
   const result=await this.request<{sessions:TerminalSession[]}>('terminal_list',{projectId:this.projectId});
   const session=result.sessions[0]??null;
   if(session)this.accept(session);else this.update({session:null});
  }catch(error){this.problem('load',error,t('The terminal couldn’t reconnect.'));}
  finally{this.operationBusy=false;this.update({busy:null});void this.flushResize();}
 }
 async start(){
  if(this.operationBusy||this.snapshot.session?.state==='running')return;
  this.operationBusy=true;this.openingId??=this.newId();this.inputGeneration++;this.input=[];
  this.update({busy:'start',issue:null,queuedBytes:0});
  try{
   const session=await this.request<TerminalSession>('terminal_open',{projectId:this.projectId,clientId:this.openingId,...this.size});
   this.accept(session);this.openingId=null;
  }catch(error){this.problem('start',error,t('The terminal couldn’t start. Try again to check the same request.'));}
  finally{this.operationBusy=false;this.update({busy:null});void this.flushResize();}
 }
 async read(){
  const session=this.snapshot.session,sink=this.sink;
  if(!session||!sink||this.readBusy||this.snapshot.busy||this.snapshot.issue?.action==='read'||(session.state!=='running'&&session.state!=='closing'&&this.cursor>=session.endCursor))return;
  const generation=this.viewGeneration,lifecycle=this.inputGeneration;
  this.readBusy=true;
  try{
   const response=await this.request<TerminalRead>('terminal_read',{projectId:this.projectId,sessionId:session.sessionId,cursor:this.cursor});
   if(generation!==this.viewGeneration||this.sink!==sink||this.snapshot.session?.sessionId!==session.sessionId)return;
   if(response.sessionId!==session.sessionId||response.projectId!==this.projectId)throw Error(t('The terminal output belongs to another session.'));
   if(response.dropped>0){sink.reset();this.update({lostOutput:true});}
   const bytes=terminalOutput(response.dataBase64);
   if(bytes.length)await sink.write(bytes);
   if(generation!==this.viewGeneration||this.sink!==sink||this.snapshot.session?.sessionId!==session.sessionId)return;
   this.cursor=response.nextCursor;
   // Output read before Close still belongs here, but its older running state doesn't reopen the shell.
   if(lifecycle===this.inputGeneration)this.accept(response);
  }catch(error){if(generation===this.viewGeneration&&!this.snapshot.issue)this.problem('read',error,t('Terminal output couldn’t load. Reconnect to continue.'));}
  finally{this.readBusy=false;}
 }
 enqueue(bytes:Uint8Array){
  if(!bytes.length)return;
  if(this.snapshot.session?.state!=='running'||this.snapshot.busy||this.snapshot.issue)return;
  const queued=this.input.reduce((total,item)=>total+item.bytes.length,0);
  if(queued+bytes.length>INPUT_LIMIT){this.problem('input-limit',null,t('That input wasn’t sent because it exceeds the 64 KB queue limit. Paste a smaller section after the current input finishes.'));return;}
  for(let offset=0;offset<bytes.length;offset+=INPUT_CHUNK)this.input.push({bytes:bytes.slice(offset,offset+INPUT_CHUNK)});
  this.update({queuedBytes:queued+bytes.length});void this.flushInput();
 }
 private async flushInput(){
  const session=this.snapshot.session,generation=this.inputGeneration;
  if(this.writeBusy||!session||session.state!=='running'||this.snapshot.busy||this.snapshot.issue)return;
  this.writeBusy=true;
  try{
   while(this.input.length&&generation===this.inputGeneration&&this.snapshot.session?.sessionId===session.sessionId&&this.snapshot.session.state==='running'){
    const item=this.input[0];item.sequence??=this.snapshot.session.nextInputSeq;
    const ack=await this.request<TerminalAck>('terminal_write',{projectId:this.projectId,sessionId:session.sessionId,sequence:item.sequence,dataBase64:terminalBase64(item.bytes)});
    if(generation!==this.inputGeneration||this.snapshot.session?.sessionId!==session.sessionId)return;
    if(ack.sessionId!==session.sessionId||ack.sequence!==item.sequence||ack.acceptedBytes!==item.bytes.length||ack.nextInputSeq!==item.sequence+1)throw Error(t('The terminal didn’t confirm this input. Retry checks the same input without sending it twice.'));
    this.input.shift();this.update({session:{...this.snapshot.session,nextInputSeq:ack.nextInputSeq,pendingInputBytes:ack.pendingInputBytes},queuedBytes:this.input.reduce((total,part)=>total+part.bytes.length,0)});
   }
  }catch(error){if(generation===this.inputGeneration)this.problem('input',error,t('Terminal input wasn’t confirmed. Retry checks the same input.'));}
  finally{this.writeBusy=false;}
 }
 resize(cols:number,rows:number){
  const size=terminalSize(cols,rows);if(size.cols===this.size.cols&&size.rows===this.size.rows)return;
  this.size=size;this.resizeWanted=true;void this.flushResize();
 }
 private async flushResize(){
  if(this.resizeBusy||!this.snapshot.session||this.snapshot.session.state!=='running'||this.snapshot.busy)return;
  this.resizeBusy=true;
  try{
   while(this.resizeWanted&&this.snapshot.session?.state==='running'){
    this.resizeWanted=false;const session:TerminalSession=this.snapshot.session,size={...this.size},generation=this.inputGeneration;
    const next=await this.request<TerminalSession>('terminal_resize',{projectId:this.projectId,sessionId:session.sessionId,...size});
    if(generation===this.inputGeneration&&this.snapshot.session?.sessionId===session.sessionId)this.accept(next);
   }
  }catch(error){if(!this.snapshot.issue)this.problem('resize',error,t('The terminal size couldn’t update.'));}
  finally{this.resizeBusy=false;}
 }
 async close(){
  const session=this.snapshot.session;if(!session||this.operationBusy)return;
  this.operationBusy=true;this.inputGeneration++;this.update({busy:'close',issue:null});
  try{
   const next=await this.request<TerminalSession>('terminal_close',{projectId:this.projectId,sessionId:session.sessionId});
   if(this.snapshot.session?.sessionId!==session.sessionId)return;
   this.accept(next);this.input=[];this.update({queuedBytes:0});
  }catch(error){this.problem('close',error,t('The terminal’s close wasn’t confirmed. Try again to check it.'));}
  finally{this.operationBusy=false;this.update({busy:null});}
 }
 /** Run a command from a reply or Settings: start the shell in the project folder if none is running, then type the
  * command and Return, exactly as if you had. Multi-line commands go line by line. Refused while input is uncertain. */
 async run(command:string,label:string){
  const text=command.replace(/\r\n?/g,'\n').trim();
  if(!text||text.length>4000)throw Error(t('That command is empty or too long to run from here.'));
  // What runs must be what you read: no terminal controls, and no invisible or reordering characters.
  if(HIDDEN_CHARACTERS.test(text))throw Error(t('That command has hidden characters, so Dervo won’t type it. Copy it and check it first.'));
  if(this.snapshot.issue)throw Error(t('The terminal needs attention first: {message}',{message:this.snapshot.issue.message}));
  if(this.snapshot.busy==='load')await this.load();
  const wasRunning=this.snapshot.session?.state==='running';
  if(!wasRunning)await this.start();
  // Read afresh after each await: start() and enqueue() replace the snapshot.
  const issue=()=>this.snapshot.issue as TerminalView['issue'];
  const session=this.snapshot.session;
  if(!session||session.state!=='running'||issue())throw Error(issue()?.message??t('The terminal didn’t start. Open it and try again.'));
  // A shell that was already open may be busy: a dev server, a y/N prompt, a Python session, an ssh login. Typing into
  // it would answer that, not run this (backend review, 23 Sep). Ask the host what's in the foreground now, and refuse.
  if(wasRunning){
   const fresh=(await this.request<{sessions:TerminalSession[]}>('terminal_list',{projectId:this.projectId})).sessions.find(item=>item.sessionId===session.sessionId);
   if(fresh?.foreground)throw Error(t('The terminal is busy with {foreground}. Stop it there or let it finish, then press Run again.',{foreground:fresh.foreground}));
  }
  this.enqueue(terminalBytes(text.split('\n').join('\r')+'\r'));
  const refused=issue();
  if(refused)throw Error(refused.message);
  const run:TerminalRun={id:this.newId(),command:text,label,at:Date.now(),sessionId:session.sessionId};
  this.update({runs:[...this.snapshot.runs,run].slice(-RUNS_KEPT)});
  return run;
 }
 dismissRuns(){this.update({runs:[]});}
 async retry(){
  const action=this.snapshot.issue?.action;this.update({issue:null});
  if(action==='load')await this.load();
  else if(action==='start')await this.start();
  else if(action==='close')await this.close();
  else if(action==='input')await this.flushInput();
  else if(action==='input-limit')await this.flushInput();
  else if(action==='resize'){this.resizeWanted=true;await this.flushResize();}
  else await this.read();
 }
}

// Small controllers keep unconfirmed input with its original project while views switch. The host owns the shells.
const controllers=new Map<string,ProjectTerminalController>();
export function projectTerminalController(projectId:string):ProjectTerminalController{
 const existing=controllers.get(projectId);if(existing)return existing;
 for(const [id,controller] of controllers){if(!controller.attached&&!controller.retain)controllers.delete(id);}
 const controller=new ProjectTerminalController(projectId);controllers.set(projectId,controller);return controller;
}
