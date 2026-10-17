import type {HistoryImport,HistoryLook,HistoryThread,HistoryWorkstream} from './historyImport';

/** The mockup's data for the one project (evidence/design-tests/onboarding/index.html, OB_TREE's "Dervo app" entry),
 * filled out to the shape in historyImport.ts. Thread names are plausible groupings picked by hand from Bryce's own
 * threads, not output from file_by_rules; the question is the mockup's. Times are relative to `now` so the shots read
 * as today. Used by the tests and by evidence/design-tests/onboarding/built/shoot.mjs; nothing in the app imports it. */

const MIN=60,HOUR=3600,DAY=86400;
let n=0;
const thread=(name:string,ago:number,open:boolean,sessions=1,source:'claude'|'codex'='claude'):HistoryThread=>({id:`hist-${++n}`,name,last_active:0-ago,open,source,sessions});

export function historyLookFixture(now:number):HistoryLook{
 return {folder:'/Users/bryce/dervo/app',conversations:25,sessions:41,sources:['claude','codex'],first:now-29*DAY,last:now-16*MIN};
}

export function historyFixture(now:number):HistoryImport{
 n=0;
 const workstreams:HistoryWorkstream[]=[
  {id:'ws-home',name:'Home and catch up',rule:'name overlap',threads:[
   thread('Catch up as a queue',6*HOUR+19*MIN,true,3),thread('Presence and the composer',11*HOUR,false,2),thread('Where you left off, from records',DAY+3*HOUR,false,4),
   thread('The pane folds on narrow',DAY+9*HOUR,false),thread('Arrival summary',2*DAY,false,2),thread('Push to GitHub from Home',2*DAY+5*HOUR,false,3),
   thread('Images in the Home composer',3*DAY,false),thread('Undo for filed threads',4*DAY,false),thread('Lost runs after a relaunch',6*DAY,false,2)]},
  {id:'ws-releases',name:'Releases and previews',rule:'name overlap',threads:[
   thread('Preview builds and the plan',DAY+2*HOUR+2*MIN,true,2),thread('Beta plan screen',5*DAY,false,3),thread('Release smoke on the built app',5*DAY+4*HOUR,false),
   thread('Notarise and ship beta.11',7*DAY,false,2,'codex'),thread('Update check on launch',9*DAY,false),thread('Changelog from commits',12*DAY,false)]},
  {id:'ws-evals',name:'Evals and smartness',rule:'topic',threads:[
   thread('Hard cases, 86/100',11*HOUR+26*MIN,false,4),thread('Jev on the filing rules',DAY+6*HOUR,false,2),thread('Naming from the first message',3*DAY+2*HOUR,false),
   thread('Closing question rule',4*DAY+7*HOUR,false),thread('Seams eval runner',8*DAY,false,3),thread('Score the tidy on real threads',10*DAY,false,2,'codex'),thread('Topic overlap threshold',15*DAY,false)]},
  {id:'ws-launch',name:'Launch and positioning',rule:'new workstream',threads:[
   thread('Landing reposition',DAY+14*HOUR,true,2),thread('Launch video v5',3*DAY,false,3),thread('X launch posts',4*DAY+3*HOUR,false)]},
 ];
 for(const ws of workstreams)for(const t of ws.threads)t.last_active+=now;
 const kept=workstreams.flatMap(ws=>ws.threads.map(t=>t.name));
 const noise=['dervo-claude-zwl14n2','dervo-claude-v4m7zori','one message, no reply','dervo-claude-frcr8beq','empty session','dervo-claude-9m12xi8h',
  'dervo-claude-ly1pyma5','one message, no reply','dervo-claude-mgryrq0','dervo-claude-lpgwwjh','empty session','dervo-claude-qpmgvm6p','dervo-claude-hp8mtuy',
  'dervo-claude-k2n8fq1','one message, no reply','dervo-claude-8xr4wm0'];
 // The stream as the reader saw it: 41 sessions, 25 kept and 16 set aside, with the noise between the kept ones.
 const read:HistoryImport['read']=[];
 let j=0;
 kept.forEach((name,index)=>{
  if(index%3===0&&j<noise.length){read.push({label:noise[j++],kept:false});read.push({label:noise[j++],kept:false});}
  read.push({label:name,kept:true});
 });
 return {folder:'/Users/bryce/dervo/app',counts:{conversations:25,sessions:41,working:workstreams.length},set_aside:16,read,workstreams,at:now,
  question:{thread_id:workstreams[1].threads[0].id,thread_name:'Preview builds and the plan',workstream:'Releases and previews',asked_at:now-DAY-2*HOUR-2*MIN,
   asked:'Keep preview builds out of the shipped and next lists.',did:'Split previews into their own list. A failed build no longer writes the beta plan.',
   question:'Should a preview build ever write the beta plan?',
   choices:['No. Previews stay out of the plan entirely.','Yes, but mark them as previews in the list.','Ask me each time.']}};
}
