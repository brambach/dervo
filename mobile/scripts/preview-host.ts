import type {HostCall} from '../../companion/mac/link.ts';

/** A stand-in Dervo host for rendering the phone app in a browser: the 8 Oct mockup's example threads, served through
 * the state spine's ``thread_states`` shape. Example content only, never shown as real activity. The real host path is
 * covered by companion/mac/link.test.ts. */
export function previewHost(){
  const now=Date.now()/1000;
  const min=(n:number)=>now-n*60;
  let premiumRunning=true;
  let ideasAnswered=false,boardDecided=false;
  const homeChat:{id:string;kind:string;text?:string;state?:string;created_at:number;parts?:{name:string;channel_id:string;state:string}[]}[]=[
    {id:'h1',kind:'you',text:'Make sending feel as fast as Claude Code, and redo Home after.',created_at:min(26)},
    {id:'h2',kind:'reply',state:'ready',text:'Two pieces. Speed first, then Home, so the redesign builds on the faster send.',created_at:min(25.5),
      parts:[{name:'Feel premium',channel_id:'premium',state:'sent'},{name:'Home redesign',channel_id:'redesign',state:'sent'}]},
  ];
  const threads=()=>[
    {id:'premium',projectId:'app',name:'Feel premium',state:premiumRunning?'working':'stopped',step:'Timing the send on your Mac',at:min(21)},
    {id:'redesign',projectId:'app',name:'Home redesign',state:'working',step:'Reading Home.tsx and the mockups',at:min(1)},
    {id:'ideas',projectId:'app',name:'Ideas storage',state:'needs_you',pending_question:'Should an archived idea keep its threads?',at:min(48)},
    {id:'board',projectId:'app',name:'Live board',state:'needs_you',why:'Ready for review. 44 tests pass, 3 files.',at:min(95)},
    {id:'picker',projectId:'app',name:'Connector picker',state:'unknown',at:min(30)},
    {id:'sweep',projectId:'app',name:'Design sweep',state:'stopped',summary:'Stopped at 3 of 7. Edits kept, not committed.',at:min(60*30)},
    {id:'beta',projectId:'site',name:'Beta plan screen',state:'finished',unread:1,summary:'Layout pass done. Screenshots attached.',at:min(70)},
    {id:'pricing',projectId:'site',name:'Pricing section',state:'idle',summary:'Waiting for the new copy',at:min(60*50)},
  ];
  const call:HostCall=async(operation:string,args:Record<string,unknown>={})=>{
    switch(operation){
      case 'status':return {actor:'local-user',lostRuns:[]} as never;
      case 'projects':return [{id:'app',name:'dervo app',profile_id:'me'},{id:'site',name:'trydervo.com',profile_id:'me'}] as never;
      case 'orchestrator_list':return {entries:args.projectId==='app'?homeChat:[],thinking:homeChat.some(e=>e.state==='thinking')} as never;
      case 'orchestrator_send':
        homeChat.push({id:'h'+(homeChat.length+1),kind:'you',text:String(args.text),created_at:Date.now()/1000});
        homeChat.push({id:'h'+(homeChat.length+1),kind:'reply',state:'thinking',created_at:Date.now()/1000});
        return {entries:homeChat,thinking:true} as never;
      case 'thread_states':return {version:1,threads:threads()} as never;
      case 'conversation_feed':return {snapshot:{history:[]}} as never;
      case 'tasks':
        if(args.channelId==='ideas')return [{id:'t2',state:'working',title:'Store ideas next to projects',messages:[],questions:ideasAnswered?[]:[{id:'q1',
          ask:'Should an archived idea keep its threads?',state:'pending',canAnswer:true,canSkip:true,options:[
            {id:'keep',label:'Keep them',detail:'Threads stay readable under the archived idea.'},
            {id:'archive',label:'Archive them too',detail:'They leave Home with the idea and come back if you restore it.'}]}]}] as never;
        if(args.channelId==='board')return [{id:'t3',state:boardDecided?'accepted':'ready_for_review',scope_version:1,title:'Live board shows every run\'s last step',
          review:boardDecided?'approved':null,check_profile:'tests',messages:[]}] as never;
        if(args.channelId!=='premium')return [] as never;
        return [{id:'t1',state:premiumRunning?'working':'stopped',messages:[
          {role:'user',text:'Make sending feel as fast as Claude Code. Time it on my Mac first.',created_at:min(24)},
          {role:'assistant',text:'Measured on your Mac: Dervo starts Claude Code within 2 s, so most of "starting" happens after launch. Full-access runs connect all 7 MCP servers before the first turn, about 10 s.',created_at:min(22)},
          {role:'user',text:'Keep one Claude Code alive per thread then.',created_at:min(21.5)},
          {role:'assistant',text:'On it. I\'m adding connector-ready and first-output times to the run record so the before and after can be proven.',created_at:min(21)},
        ]}] as never;
      case 'task_detail':return {task:{},evidence:{attempts:[{number:1,kind:'run',state:'verified',test:{exit_status:0},
        diff:{'src/board/Board.tsx':{},'src/board/step.ts':{},'src/board/step.test.ts':{}}}]},rebound:false,carried:null,retry:null} as never;
      case 'task_question_answer':ideasAnswered=true;return {delivery:'sent'} as never;
      case 'task_review':boardDecided=true;return {commit:{sha:'a1b2c3d'}} as never;
      case 'task_stop':premiumRunning=false;return {} as never;
      case 'coding_request':return {id:'r1',state:'queued'} as never;
      default:throw new Error('Unsupported operation or fields');
    }
  };
  return call;
}
