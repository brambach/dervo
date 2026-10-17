import type {Survey} from './onboarding';

/** A made-up Mac for the onboarding tests and design shots, in the shape onboarding_look returns. The people, companies
 * and folders are the mockup's inventions (onboarding-v2.html), none of them Bryce's. */
const NOW=Date.UTC(2026,8,24,9,41)/1000;
const DAY=86400;
const project=(key:string,name:string,path:string,conversations:number,recent:number,daysAgo:number,
 tools:('claude'|'codex')[],extra:Partial<Survey['projects'][number]>={}):Survey['projects'][number]=>({
 key,name,path,conversations,recent,last:NOW-daysAgo*DAY,first:NOW-90*DAY,
 tools,branch:'main',codex_project:false,ticked:recent>0&&conversations>=3,context:null,context_reason:null,
 reason:`${conversations} conversations${recent?`, ${recent} in the last 14 days`:''}, in ${tools.length===2?'both Claude Code and Codex':tools[0]==='codex'?'Codex':'Claude Code'}, on branch main.`,...extra});

const WORK={context:'work',context_reason:'guessed from ~/work'} as const;
const PERSONAL={context:'personal',context_reason:'guessed from ~/personal'} as const;

/** Work, Personal and one unsure (blog, ~/code with nothing to go on), as in the b16-work mocks. */
export const surveyFixture:Survey={
 surveyId:'survey-fixture',took:0.11,
 sources:[{tool:'claude',path:'~/.claude/projects',present:true},{tool:'codex',path:'~/.codex',present:true}],
 counts:{files:2318,folders:184,working:5,quieter:3,loose:14},
 projects:[
  project('k-billing','billing-api','~/work/acme/billing-api',44,12,1,['claude','codex'],WORK),
  project('k-checkout','checkout-web','~/code/checkout-web',17,5,2,['claude'],{context:'work',context_reason:'guessed from its remote, git.acme.io'}),
  project('k-exports','data-exports','~/work/acme/data-exports',6,3,3,['codex'],{...WORK,codex_project:true,reason:'6 conversations, 3 in the last 14 days, in Codex, on branch main. You made it a Codex project.'}),
  project('k-tidepool','tidepool','~/personal/tidepool',22,9,1,['claude','codex'],PERSONAL),
  project('k-recipes','recipe-box','~/personal/recipe-box',9,4,5,['claude'],PERSONAL),
  project('k-dotfiles','dotfiles','~/personal/dotfiles',4,0,40,['claude'],PERSONAL),
  project('k-blog','blog','~/code/blog',2,1,6,['claude']),
  project('k-scraper','scraper','~/code/scraper',3,0,70,['codex']),
 ],
 loose_ends:{key:null,name:'Loose ends',path:'~',ticked:false,context:null,context_reason:null,conversations:14,recent:2,last:NOW-4*DAY,first:NOW-80*DAY,
  tools:['claude'],branch:null,codex_project:false,reason:'14 conversations started in your home folder, not in a project. They stay where they are.'},
 set_aside:[
  {rule:'temp',count:126,label:'126 temporary folders',folders:[{path:'/private/tmp/build-3f9a',conversations:2,key:null}],more:125},
  {rule:'copy',count:412,label:'412 Codex copies of Claude Code sessions, counted once',folders:[{path:'~/code/tidepool',conversations:204,key:null}],more:0},
  {rule:'tool_run',count:96,label:'96 threads a tool ran',folders:[{path:'~/work/acme/billing-api',conversations:61,key:null}],more:0},
  {rule:'one_off',count:38,label:'38 one-offs',folders:[{path:'~/code/weekend-idea',conversations:1,key:'k-weekend',name:'weekend-idea'},{path:'~/Desktop/try-this',conversations:1,key:'k-try',name:'try-this'}],more:36},
  {rule:'gone',count:6,label:'6 folders that are gone',folders:[{path:'~/code/old-checkout',conversations:3,key:null}],more:5},
  {rule:'folded',count:4,label:'4 worktrees and subfolders, folded into their repo',folders:[{path:'~/work/acme/billing-api-wt/fix-tax',conversations:5,key:'k-fixtax',name:'fix-tax'}],more:3},
  {rule:'tool_folder',count:1,label:'1 tool’s own folder',folders:[{path:'~/.claude/mem/observer',conversations:880,key:'k-mem',name:'observer'}],more:0},
 ],
};

/** A Mac where every busy folder looks like work (a tester's dd and dd-portal-v2, here under ~/work). In beta.15.1 this
 * left Bring in disabled with nothing saying why; now the main button is Add a work account (suggest-work-only). */
export const workLookingFixture:Survey={
 surveyId:'survey-work-fixture',took:0.08,
 sources:[{tool:'claude',path:'~/.claude/projects',present:true},{tool:'codex',path:'~/.codex',present:true}],
 counts:{files:412,folders:37,working:2,quieter:1,loose:0},
 projects:[
  project('k-dd','dd','~/work/dd',31,11,1,['claude','codex'],WORK),
  project('k-portal','dd-portal-v2','~/work/dd-portal-v2',14,6,2,['claude'],WORK),
  project('k-infra','dd-infra','~/work/dd-infra',5,0,30,['codex'],WORK),
 ],
 loose_ends:null,
 set_aside:[{rule:'temp',count:22,label:'22 temporary folders',folders:[{path:'/private/tmp/dd-build',conversations:3,key:null}],more:21}],
};

/** Someone with only personal projects: one card, no Work group, no why (suggest-personal-only). */
export const personalFixture:Survey={...surveyFixture,surveyId:'survey-personal-fixture',counts:{...surveyFixture.counts,working:2},
 projects:surveyFixture.projects.filter(p=>p.context==='personal')};
