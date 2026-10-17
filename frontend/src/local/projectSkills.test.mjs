import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLTextAreaElement','HTMLButtonElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
HTMLElement.prototype.scrollIntoView=function(){};HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {SkillsPage,proposalOrigin,skillMeta,skillName}=await import('./ProjectSkills.tsx');
const {queueItems}=await import('./KnowledgeQueue.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const version=(over={})=>({version:1,name:'add-a-bridge-operation',description:'Adding an operation the renderer calls.',body:'1. Variant.\n2. Test.',
 status:'approved',origin:'human',author:'local-user',created_at:1000,...over});
const skill=(over={})=>({id:'s1',name:'cut-a-beta',description:'Building a beta.',revision:2,current:version({name:'cut-a-beta',description:'Building a beta.'}),proposals:[],
 uses:3,last_used_at:null,exported:{at:1,version:1,path:'.claude/skills/cut-a-beta/SKILL.md'},archived_at:null,history:[],...over});
const listing=(skills,over={})=>({skills,archived:[],pending:skills.reduce((n,s)=>n+s.proposals.length,0),limits:{description:240,body:12000,skills:60},dirs:['.claude/skills','.agents/skills'],...over});

test('the words on a skill say who wrote it, what it has done and where it lives',()=>{
 assert.equal(proposalOrigin(version({origin:'agent',author:'claude',created_at:1000}),null,1000+1200),'Proposed by Claude Code · 20 min ago · new skill');
 assert.equal(proposalOrigin(version({origin:'imported',note:'Imported from .claude/skills/x/SKILL.md',created_at:1000}),version({version:2}),1000+60),
  'Imported from .claude/skills/x/SKILL.md · 1 min ago · would replace version 2');
 assert.equal(skillMeta(skill()),'used in 3 tasks · version 1 · written to .claude/skills');
 assert.equal(skillMeta(skill({uses:0,exported:null})),'not used yet · version 1');
 assert.equal(skillMeta(skill({uses:1,exported:null})),'used in 1 task · version 1');
 assert.equal(skillName('Add a Bridge Operation!'),'add-a-bridge-operation');
 assert.equal(skillName('  --cut__beta--  '),'cut-beta');
});

function host(initial){
 let current=initial;const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{
  calls.push([operation,args]);
  if(operation==='skills_list')return current;
  if(operation==='skills_review'){const s=current.skills.find(x=>x.id===args.skillId);const v=s.proposals.find(p=>p.version===args.version);
   current=listing(current.skills.map(x=>x!==s?x:{...s,revision:s.revision+1,proposals:s.proposals.filter(p=>p!==v),current:args.decision==='approve'?{...v,status:'approved'}:s.current}));return current;}
  if(operation==='skills_save'){current=listing([...current.skills,skill({id:'s9',name:args.name,description:args.description,revision:1,uses:0,exported:null,current:version({name:args.name,description:args.description,body:args.body})})]);return current;}
  if(operation==='skills_import_text'){current=listing([skill({current:null,proposals:[version({status:'proposed',origin:'imported'})]})]);return current;}
  if(operation==='skills_import')return {...current,imported:{found:2,added:1,skipped:1,refused:['.agents/skills/bad: A skill name is lowercase words']}};
  throw new Error('no fixture '+operation);
 }};
 return calls;
}
const mount=()=>render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
 React.createElement(SkillsPage,{projectId:'p',profileId:'me'})));

test('an imported file waits in Knowledge’s queue, not in How we work, which lists only skills in use',async()=>{
 const proposal=version({origin:'imported',author:'local-user',status:'proposed',note:'Imported from .agents/skills/add/SKILL.md'});
 const imported=version({version:2,origin:'imported',status:'proposed',note:'Imported from .claude/skills/cut-a-beta/SKILL.md',created_at:500});
 const data=listing([skill({proposals:[imported]}),skill({id:'s2',name:'add-a-bridge-operation',description:'Adding an operation the renderer calls.',current:null,proposals:[proposal],uses:0,exported:null})]);
 host(data);
 mount();
 await screen.findByText('Building a beta.');
 assert.equal(screen.queryByRole('article'),null,'no proposal card in the Skills section');
 assert.deepEqual([...document.querySelectorAll('[data-testid="skill-row"] .dv-row-name')].map(b=>b.textContent),['Building a beta.']);
 assert.equal(document.querySelector('[data-testid="skill-row"] .dv-row-line').textContent,'cut-a-beta · Written by you · used in 3 chats');
 const items=queueItems([],data,[]);
 assert.deepEqual(items.map(item=>item.key),['skill:s1:2','skill:s2:1'],'both wait, oldest first, the imported file included');
});

test('a new skill is saved under the SKILL.md name rule, and import says what it found',async()=>{
 const calls=host(listing([]));
 mount();
 await screen.findByText(/No skills yet/);
 fireEvent.click(screen.getByRole('button',{name:'New skill'}));
 fireEvent.change(screen.getByLabelText('Name'),{target:{value:'Cut a Beta'}});
 assert.match(document.querySelector('.dv-kn-hint').textContent,/Saved as cut-a-beta/);
 fireEvent.change(screen.getByLabelText('When it applies'),{target:{value:'Building and checking a beta.'}});
 fireEvent.change(screen.getByLabelText('Steps'),{target:{value:'1. Build.\n2. Verify.'}});
 fireEvent.click(screen.getByRole('button',{name:'Save skill'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='skills_save')));
 assert.deepEqual(calls.find(([op])=>op==='skills_save')[1],{projectId:'p',profileId:'me',skillId:null,expectedRevision:0,name:'cut-a-beta',description:'Building and checking a beta.',body:'1. Build.\n2. Verify.'});
 await screen.findByRole('button',{name:'Building and checking a beta.'});
 fireEvent.click(screen.getByRole('button',{name:'Import from project'}));
 await screen.findByText('1 skill is waiting for you above. 1 already here. 1 couldn’t be read.');
 assert.ok(screen.getByText(/Why 1 file wasn’t imported/));
});


test('a selected private skill is imported into the current scope without approving it',async()=>{
 const calls=host(listing([]));mount();
 await screen.findByRole('button',{name:'Import file'});
 const text='---\nname: personal-writing\ndescription: Drafting emails in my voice\n---\nUse concrete nouns.';
 fireEvent.change(screen.getByLabelText('Import skill file'),{target:{files:[{size:text.length,text:async()=>text}]}});
 await screen.findByText('The skill is waiting for review above. A file’s skill doesn’t reach a chat until you approve it.');
 assert.deepEqual(calls.find(([op])=>op==='skills_import_text'),['skills_import_text',{projectId:'p',profileId:'me',text}]);
 assert.equal(calls.some(([op])=>op==='skills_review'),false);
});

test('a skill an agent saved is in use at once, New since you last looked, says which chats got it, and Delete has Undo',async()=>{
 let current=listing([skill({id:'s3',name:'build-a-numbered-beta',description:'Build a numbered beta.',revision:4,uses:2,used_in:['plan','gone'],exported:null,
  current:version({name:'build-a-numbered-beta',description:'Build a numbered beta.',origin:'agent',author:'claude',created_at:2000,thread_id:'plan',task_id:'t1'})})]);
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='skills_list')return current;
  if(operation==='skills_archive'){const s=current.skills[0];current=listing([],{archived:[{...s,revision:s.revision+1,archived_at:3000}]});return current;}
  if(operation==='skills_restore'){const s=current.archived[0];current=listing([{...s,revision:s.revision+1,archived_at:null}]);return current;}
  throw new Error('no fixture '+operation);}};
 const opened=[];
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
  React.createElement(SkillsPage,{projectId:'p',profileId:'me',seenAt:1500,threadName:id=>id==='plan'?'Beta 15 plan':undefined,onOpenSource:(thread,task)=>opened.push([thread,task])})));
 const row=(await screen.findByText('Build a numbered beta.')).closest('[data-testid="skill-row"]');
 assert.equal(row.querySelector('.dv-row-line').textContent,'build-a-numbered-beta · Claude Code in Beta 15 plan · used in 2 chats');
 assert.equal(row.querySelector('.dv-kn-new')?.textContent,'New');
 fireEvent.click(screen.getByRole('button',{name:'Beta 15 plan'}));
 assert.deepEqual(opened,[['plan','t1']]);
 fireEvent.click(screen.getByRole('button',{name:'used in 2 chats'}));
 assert.match(row.textContent,/Went to Beta 15 plan, a chat you can’t open here\. Delete it if it misled one\./);
 fireEvent.click(screen.getByRole('button',{name:'Delete skill: build-a-numbered-beta'}));
 const gone=await screen.findByText('Deleted build-a-numbered-beta. Chats about it won’t get it.');
 assert.deepEqual(calls.find(([op])=>op==='skills_archive')[1],{projectId:'p',profileId:'me',skillId:'s3',expectedRevision:4});
 fireEvent.click(within(gone.closest('p')).getByRole('button',{name:'Undo'}));
 await screen.findByText('Build a numbered beta.');
 assert.deepEqual(calls.find(([op])=>op==='skills_restore')[1],{projectId:'p',profileId:'me',skillId:'s3',expectedRevision:5});
 // Negative control: before the page knows when you last looked, nothing is New, and your own skill never is.
 cleanup();
 render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(SkillsPage,{projectId:'p',profileId:'me'})));
 await screen.findByText('Build a numbered beta.');
 assert.equal(document.querySelector('.dv-kn-new'),null);
});
