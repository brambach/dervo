import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.requestAnimationFrame=cb=>setTimeout(()=>cb(Date.now()),0);globalThis.cancelAnimationFrame=id=>clearTimeout(id);window.requestAnimationFrame=globalThis.requestAnimationFrame;window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
const React=await import('react');
const {readFileSync}=await import('node:fs');
const {render,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThreadFolderChip,ThreadFolderCard,ThreadMerge,ThreadFolderSettings,chipLabel,reasonLine}=await import('./ThreadFolder.tsx');
after(()=>dom.window.close());afterEach(cleanup);

/** Beta.18, thread folders (direction B): the composer chip, the setup card, Merge and the Access list, against the host
 * operations in backend/thread_folders.py. */
const view=(extra={})=>({choice:'own',reason:'code',state:'chosen',flippable:true,branch:null,base:'main',base_commit:null,folder:null,
 project_folder:'/Users/someone/dervo/app',project_branch:'main',repo:true,steps:null,error:null,dependencies:null,install_error:null,merge:null,left:null,
 suggested_branch:'dervo/fix-login',branch_state:null,unknown:null,...extra});
const steps=(states)=>['space','branch','files','dependencies','ignored'].map((key,i)=>({key,state:states[i]??'later',line:`${key} line`}));

function mount(element,answer){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push([operation,args]);return answer(operation,args);}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(React.createElement(QueryClientProvider,{client},element));
 return calls;
}

test('the chip shows the pick before the first run and the branch after it',()=>{
 assert.deepEqual(chipLabel(view()),{text:'Own branch',mono:false});
 assert.deepEqual(chipLabel(view({choice:'project',reason:'explore'})),{text:'Project folder',mono:false});
 assert.deepEqual(chipLabel(view({state:'ready',branch:'dervo/fix-login',flippable:false})),{text:'dervo/fix-login',mono:true});
 assert.match(reasonLine('code'),/build or change/);
 assert.match(reasonLine(null),/safe side/);
 assert.match(reasonLine('explore'),/project folder/);
});

test('flipping the chip before the first send asks the host, with the draft read for the pick',async()=>{
 let current=view();
 const calls=mount(React.createElement(ThreadFolderChip,{projectId:'p',channelId:'c',draft:'Fix the login bug'}),(operation,args)=>{
  if(operation==='thread_folder_choose')current=view({choice:args.choice,reason:'you'});
  return current;
 });
 const chip=await screen.findByTestId('thread-folder-chip');
 assert.equal(chip.textContent,'Own branch');
 fireEvent.click(chip);
 const project=await screen.findByTestId('thread-folder-project');
 assert.match(document.body.textContent,/dervo\/fix-login/);
 assert.match(document.body.textContent,/~\/dervo\/app on main, shared with other threads, as today\./);
 assert.match(document.body.textContent,/Set at the first send\./);
 fireEvent.click(project);
 await waitFor(()=>assert.ok(calls.some(([op,args])=>op==='thread_folder_choose'&&args.choice==='project')));
 await waitFor(()=>assert.ok(calls.some(([op,args])=>op==='thread_folder'&&args.text==='Fix the login bug')));
});

test('after the first run the chip only tells, it never flips',async()=>{
 mount(React.createElement(ThreadFolderChip,{projectId:'p',channelId:'c'}),()=>view({state:'ready',branch:'dervo/fix-login',base_commit:'6ea332ed99',flippable:false,folder:'/w/f'}));
 const chip=await screen.findByTestId('thread-folder-chip');
 // After the first run the chip keeps only its icon; the branch is its accessible name (Bryce, 8 Oct: only what's needed).
 assert.equal(chip.textContent,'');
 assert.equal(chip.getAttribute('aria-label'),'dervo/fix-login');
 fireEvent.click(chip);
 await screen.findByText('From main at 6ea332e');
 assert.equal(screen.queryByTestId('thread-folder-project'),null);
 assert.match(document.body.textContent,/Fixed at the first run/);
});

test('no chip outside a git repository',async()=>{
 const calls=mount(React.createElement(ThreadFolderChip,{projectId:'p',channelId:'c'}),()=>view({repo:false,choice:'project',reason:'not_repo'}));
 await waitFor(()=>assert.ok(calls.length));
 assert.equal(screen.queryByTestId('thread-folder-chip'),null);
});

test('the setup card lists its steps and offers the project folder instead',async()=>{
 const calls=mount(React.createElement(ThreadFolderCard,{projectId:'p',channelId:'c'}),()=>view({state:'setting_up',branch:'dervo/fix-login',steps:steps(['done','done','running'])}));
 const card=await screen.findByTestId('thread-folder-card');
 assert.equal(card.dataset.state,'setting_up');
 assert.match(card.textContent,/Own branch from main/);
 assert.match(card.textContent,/Nothing has run yet\./);
 assert.equal(card.querySelectorAll('li[data-later]').length,2);
 fireEvent.click(screen.getByText('Run in the project folder instead'));
 await waitFor(()=>assert.ok(calls.some(([op,args])=>op==='thread_folder_choose'&&args.choice==='project')));
});

test('low space stops setup with nothing made, and Try again asks again',async()=>{
 const calls=mount(React.createElement(ThreadFolderCard,{projectId:'p',channelId:'c'}),()=>view({state:'failed',branch:'dervo/fix-login',
  steps:[{key:'space',state:'needs',line:'4.8 GB free. Dervo keeps 6.0 GB free, and this folder needs about 64 MB more.'},...steps([]).slice(1)],
  error:{key:'space',text:'Not enough free space'}}));
 const card=await screen.findByTestId('thread-folder-card');
 assert.match(card.textContent,/Couldn’t set up/);
 assert.match(card.textContent,/Nothing ran and nothing changed\./);
 assert.match(card.textContent,/Dervo keeps 6\.0 GB free/);
 assert.equal(card.querySelectorAll('li').length,1);  // steps that never started aren't listed
 fireEvent.click(screen.getByText('Try again'));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='thread_folder_retry')));
});

test('a different lockfile asks before installing and shows the exact command',async()=>{
 const calls=mount(React.createElement(ThreadFolderCard,{projectId:'p',channelId:'c'}),()=>view({state:'needs_install',branch:'dervo/fix-login',steps:steps(['done','done','done','needs']),
  dependencies:[{lockfile:'frontend/pnpm-lock.yaml',folder:'frontend/node_modules',command:['pnpm','install','--frozen-lockfile'],cwd:'frontend',state:'differs'}]}));
 const card=await screen.findByTestId('thread-folder-card');
 assert.match(card.textContent,/Install dependencies\?/);
 assert.match(card.textContent,/pnpm install --frozen-lockfile/);
 assert.equal(calls.filter(([op])=>op==='thread_folder_install').length,0);  // never by itself
 fireEvent.click(screen.getByText('Install'));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='thread_folder_install')));
});

test('the card stays away for project-folder threads and ready folders',async()=>{
 for(const shown of [view({choice:'project',reason:'explore'}),view({state:'ready',branch:'dervo/x',flippable:false})]){
  const calls=mount(React.createElement(ThreadFolderCard,{projectId:'p',channelId:'c'}),()=>shown);
  await waitFor(()=>assert.ok(calls.length));
  assert.equal(screen.queryByTestId('thread-folder-card'),null);
  cleanup();
 }
});

test('Merge is a click, says nothing is pushed, and is off on a clash',async()=>{
 const ready=view({state:'ready',branch:'dervo/fix-login',flippable:false,branch_state:{ahead:1,behind:0,clashes:[],checked:1}});
 const calls=mount(React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),()=>ready);
 const button=await screen.findByTestId('thread-merge-button');
 assert.match(document.body.textContent,/Committed to its branch\. 1 commit ahead of main, no clashes\./);
 assert.equal(button.textContent,'Merge into main');
 assert.match(button.title,/Nothing is pushed/);
 assert.equal(calls.filter(([op])=>op==='thread_folder_merge').length,0);
 fireEvent.click(button);
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='thread_folder_merge')));
 cleanup();
 mount(React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),()=>view({state:'ready',branch:'dervo/fix-login',flippable:false,branch_state:{ahead:2,behind:1,clashes:['backend/host.py'],checked:1}}));
 const off=await screen.findByTestId('thread-merge-button');
 assert.equal(off.disabled,true);
 assert.match(document.body.textContent,/Can’t merge yet\. main also changed backend\/host\.py\./);
});

test('a result and its Undo read as nothing to merge, with no Merge button',async()=>{
 mount(React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),()=>view({state:'ready',branch:'dervo/fix-login',flippable:false,branch_state:{ahead:2,behind:0,clashes:[],empty:true,checked:1}}));
 await waitFor(()=>assert.match(document.body.textContent,/Nothing to merge\. The branch’s changes were undone, so main would stay the same\./));
 assert.equal(screen.queryByTestId('thread-merge-button'),null);
 cleanup();
});

test('merged and unknown read plainly, and nothing shows before there is a commit',async()=>{
 mount(React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),()=>view({state:'merged',branch:'dervo/x',flippable:false,merge:{sha:'abcdef1234',base:'main',branch:'dervo/x',commits:1,at:1,where:null}}));
 await waitFor(()=>assert.match(document.body.textContent,/Merged into main\. Merge commit abcdef1\. Nothing was pushed, and the branch stays\./));
 cleanup();
 mount(React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),()=>view({state:'ready',branch:'dervo/x',flippable:false,unknown:'The thread’s folder is missing.'}));
 await waitFor(()=>assert.match(document.body.textContent,/Unknown folder\. The thread’s folder is missing\./));
 cleanup();
 const calls=mount(React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),()=>view({state:'ready',branch:'dervo/x',flippable:false,branch_state:{ahead:0,behind:0,clashes:[],checked:1}}));
 await waitFor(()=>assert.ok(calls.length));
 assert.equal(screen.queryByTestId('thread-merge'),null);
});

// Beta.18 review (7 Oct 2026): a run ending commits to the thread's branch, but the card only read the branch again on its
// 30 s timer (and kept a read for 8 s), so "Committed to its branch" and Merge came up to half a minute after the result.
test('Merge reads the branch again as soon as the latest result commits',async()=>{
 let ahead=0;const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push([operation,args]);
  return view({state:'ready',branch:'dervo/x',flippable:false,branch_state:{ahead,behind:0,clashes:[],checked:1}});}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const at=moved=>React.createElement(QueryClientProvider,{client},React.createElement(ThreadMerge,{projectId:'p',channelId:'c',moved}));
 const screenNow=render(at('working::'));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.equal(screen.queryByTestId('thread-merge'),null);
 ahead=1;
 screenNow.rerender(at('idle:abc1234:'));
 await waitFor(()=>assert.ok(screen.queryByTestId('thread-merge')),'Merge shows without waiting for the next timed read');
 assert.equal(calls.length,2);
 screenNow.rerender(at('idle:abc1234:'));
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(calls.length,2,'an unchanged result asks nothing more');
});

test('Access lists the ignored files to copy, and saves exactly what you tick',async()=>{
 let approved=['runtime/codex'];
 const calls=mount(React.createElement(ThreadFolderSettings,{projectId:'p'}),(operation,args)=>{
  if(operation==='thread_folder_ignored_set')approved=args.paths;
  return {approved,candidates:['runtime/codex','runtime/codex-code-mode-host'],root:'/Users/someone/Library/Application Support/Dervo/Worktrees'};
 });
 await screen.findByTestId('thread-folder-settings');
 assert.match(document.body.textContent,/~\/Library\/Application Support\/Dervo\/Worktrees/);
 const boxes=[...document.querySelectorAll('input[type="checkbox"]')];
 assert.deepEqual(boxes.map(box=>box.checked),[true,false]);
 fireEvent.click(boxes[1]);
 await waitFor(()=>assert.ok(calls.some(([op,args])=>op==='thread_folder_ignored_set'&&args.paths.join()==='runtime/codex,runtime/codex-code-mode-host')));
});

test('the bridge lists every thread folder operation, and the thread screen mounts the chip, the card and Merge',()=>{
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['thread_folder','thread_folder_choose','thread_folder_retry','thread_folder_install','thread_folder_skip_install','thread_folder_merge','thread_folder_reveal','thread_folder_settings','thread_folder_ignored_set'])
  assert.ok(bridge.includes(`=>"${op}"`),op);
 assert.ok(!/thread_folder_(squash|push|auto)/.test(bridge.replace(/\[.*must not round-trip.*\]|for name in \["thread_folder_squash[^\]]*\]/g,'')));
 const app=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(app,/<ThreadFolderChip key=/);
 assert.match(app,/<ThreadFolderCard key=/);
 assert.match(app,/task\.id===latestConversationTask&&<ThreadMerge /);
 const access=readFileSync(new URL('./SettingsAccess.tsx',import.meta.url),'utf8');
 assert.match(access,/!channelId&&threadFoldersShown\(\)&&<SettingsSection id="thread-branches"/);
});

/** Beta.18 on Windows (Bryce, 6 Oct 2026): threads always work in the project folder, so the chip, the card, Merge and
 * the Access list never show, and the host isn't asked. */
test('on Windows nothing about thread folders shows and the host is never asked',async()=>{
 Object.defineProperty(navigator,'platform',{configurable:true,value:'Win32'});
 try{
  for(const element of [React.createElement(ThreadFolderChip,{projectId:'p',channelId:'c',draft:'Fix the login bug'}),
   React.createElement(ThreadFolderCard,{projectId:'p',channelId:'c'}),React.createElement(ThreadMerge,{projectId:'p',channelId:'c'}),
   React.createElement(ThreadFolderSettings,{projectId:'p'})]){
   const calls=mount(element,()=>view({state:'setting_up',branch:'dervo/x',steps:steps(['done']),approved:[],candidates:['runtime/codex'],root:'/w'}));
   await new Promise(resolve=>setTimeout(resolve,50));
   assert.deepEqual(calls,[]);
   assert.equal(document.body.textContent,'');
   cleanup();
  }
 }finally{delete navigator.platform;}
 assert.notEqual(navigator.platform,'Win32');
});
