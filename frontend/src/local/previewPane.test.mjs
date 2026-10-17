import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLButtonElement','HTMLIFrameElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
// The pane measures its stage; give it the 526 wide column it has at 1052.
Object.defineProperty(window.HTMLElement.prototype,'clientWidth',{configurable:true,get(){return 478;}});
Object.defineProperty(window.HTMLElement.prototype,'clientHeight',{configurable:true,get(){return 700;}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {PreviewPane,previewFiles,localPageUrl,previewSrc,servablePath,splitPath,FoldSidebarWhile,usePreviewShortcuts,PAGE_WIDTH}=await import('./PreviewPane.tsx');
const {SidebarProvider,useSidebar}=await import('@/shared/ui/sidebar');
const {readFileSync,readdirSync}=await import('node:fs');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const write=(path,extra={})=>({tool:'native:Write',state:'delivered',path,run_id:'r1',ended_at:100,...extra});
const task=(tokens,changed=[])=>({id:'t1',state:'completed',tokens,runs:[{id:'r1'}],messages:[],completion:changed.length?{changed_files:changed,at:50,run_id:'r1'}:undefined});
function host(){
 const calls=[];
 window.__TAURI_INTERNALS__={transformCallback:()=>1,invoke:async(command,args)=>{calls.push({command,args});
  if(command==='preview_grant')return {grant:'ab12',base:'dervo-preview://ab12/'};
  if(command==='preview_open_outside')return null;
  throw new Error(command);}};
 return calls;
}
const wrap=node=>React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}})},node);

test('Preview lists the pages and images a thread wrote, newest first, and counts writes for reloading',()=>{
 const files=previewFiles([task([write('evidence/bugs/index.html'),write('evidence/bugs/index.html',{ended_at:300}),write('evidence/shots/list.png',{ended_at:200})],['evidence/bugs/index.html','src/BugsList.tsx'])]);
 assert.deepEqual(files.map(f=>[f.path,f.kind,f.writes]),[['evidence/bugs/index.html','page',2],['evidence/shots/list.png','image',1]]);
 // Negative controls: a failed write, a write still running, a dotfile, an absolute path, traversal, a run-folder
 // screenshot, and files Preview can't show are all left out.
 const none=previewFiles([task([write('a.html',{success:false}),write('b.html',{state:'running'}),write('.env.html'),write('/etc/x.html'),write('../up.html'),
  write('evidence/.git/x.html'),write('screenshot shot.png'),write('notes.txt'),{tool:'native:Read',state:'delivered',path:'read.html'}],['.hidden/x.png','x.json'])]);
 assert.deepEqual(none,[]);
 // Beta.15: Markdown and PDFs are Preview's too.
 assert.deepEqual(previewFiles([task([write('notes.md'),write('out/report.pdf',{ended_at:120})])]).map(f=>[f.path,f.kind]),[['out/report.pdf','pdf'],['notes.md','markdown']]);
});

test('only project-relative paths without dot names are servable',()=>{
 for(const ok of ['index.html','evidence/a b/shot.png'])assert.ok(servablePath(ok),ok);
 for(const bad of ['','/abs.html','../x.html','a/../x.html','a/./x.html','.env','a/.git/config','a\\b.html','a//b.html',null,42])assert.equal(servablePath(bad),false,String(bad));
 assert.deepEqual(splitPath('evidence/design-tests/bugs-list/index.html'),{dir:'evidence/design-tests/bugs-list/',name:'index.html'});
});

test('a local page is localhost or 127.0.0.1 with a port, never anything remote',()=>{
 assert.equal(localPageUrl('5173'),'http://localhost:5173/');
 assert.equal(localPageUrl(' localhost:8818/bugs '),'http://localhost:8818/bugs');
 assert.equal(localPageUrl('http://127.0.0.1:3000/?x=1'),'http://127.0.0.1:3000/?x=1');
 for(const bad of ['example.com','http://example.com:80','https://localhost:5173','localhost','http://localhost/','localhost:0','localhost:70000',
  'http://user@localhost:5173','localhost:5173@evil.com','http://localhost.evil.com:80','0.0.0.0:3000','[::1]:3000','file:///etc/passwd',
  'javascript:alert(1)','127.0.0.2:3000','localhost:5173/a b','dervo-preview://ab12/index.html'])assert.equal(localPageUrl(bad),null,bad);
});

test('a project file is framed from the preview scheme, encoded part by part',()=>{
 assert.equal(previewSrc('dervo-preview://ab12/','evidence/a b/#1.html',3),'dervo-preview://ab12/evidence/a%20b/%231.html?v=3');
 assert.ok(!previewSrc('dervo-preview://ab12/','x.html').startsWith(window.location.origin),'never the app origin');
});

test('the mockup frame is sandboxed without allow-same-origin, from the grant, and scaled to the column',async()=>{
 const calls=host();
 const added=[];const original=window.addEventListener;
 window.addEventListener=function(type,...rest){added.push(type);return original.call(this,type,...rest);};
 try{
  render(wrap(React.createElement(PreviewPane,{projectId:'p1',tasks:[task([write('evidence/bugs/index.html'),write('evidence/shots/a.png'),write('evidence/shots/b.png')])],onProgress(){},onClose(){}})));
  const frame=await screen.findByTestId('preview-frame');
  assert.equal(frame.getAttribute('sandbox'),'allow-scripts');
  assert.ok(!frame.getAttribute('sandbox').includes('allow-same-origin'));
  assert.equal(frame.getAttribute('src'),'dervo-preview://ab12/evidence/bugs/index.html?v=1');
  assert.equal(frame.getAttribute('referrerpolicy'),'no-referrer');
  assert.equal(frame.style.width,`${PAGE_WIDTH}px`);
  assert.match(frame.style.transform,/scale\(0\.45/);
  assert.deepEqual(calls.find(c=>c.command==='preview_grant').args,{projectId:'p1'});
  assert.ok(screen.getByRole('tab',{name:'Screenshots 2'}));
  assert.ok(screen.getByText('Offline · reloads when the agent writes it'));
  // Negative control: nothing in the pane listens for messages a framed page could post.
  assert.ok(!added.includes('message'));
 }finally{window.addEventListener=original;}
});

test('a new write reloads the frame; open in browser passes the grant and path, never a URL',async()=>{
 const calls=host();
 const props={projectId:'p1',onProgress(){},onClose(){}};
 const {rerender}=render(wrap(React.createElement(PreviewPane,{...props,tasks:[task([write('m/index.html')])]})));
 const first=await screen.findByTestId('preview-frame');
 rerender(wrap(React.createElement(PreviewPane,{...props,tasks:[task([write('m/index.html'),write('m/index.html',{ended_at:200})])]})));
 const second=await screen.findByTestId('preview-frame');
 assert.notEqual(first,second,'a new frame for the new write');
 assert.equal(second.getAttribute('src'),'dervo-preview://ab12/m/index.html?v=2');
 fireEvent.click(screen.getByRole('button',{name:'Open in your browser'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.command==='preview_open_outside')));
 assert.deepEqual(calls.find(c=>c.command==='preview_open_outside').args,{grant:'ab12',path:'m/index.html'});
});

test('a local page opens only once you type one on this Mac, sandboxed the same way',async()=>{
 host();
 render(wrap(React.createElement(PreviewPane,{projectId:'p1',tasks:[],onProgress(){},onClose(){}})));
 fireEvent.click(screen.getByRole('tab',{name:'Local page'}));
 const input=screen.getByLabelText('Open a page running on this Mac');
 // Negative control: a remote address is refused and nothing is framed.
 fireEvent.change(input,{target:{value:'https://example.com'}});
 fireEvent.click(screen.getByRole('button',{name:'Open'}));
 assert.ok(screen.getByRole('alert').textContent.includes('localhost or 127.0.0.1'));
 assert.equal(screen.queryByTestId('preview-frame'),null);
 fireEvent.change(input,{target:{value:'localhost:5173'}});
 fireEvent.click(screen.getByRole('button',{name:'Open'}));
 const frame=await screen.findByTestId('preview-frame');
 assert.equal(frame.getAttribute('src'),'http://localhost:5173/');
 assert.equal(frame.getAttribute('sandbox'),'allow-scripts allow-forms');
});

test('with nothing written, Preview says so and frames nothing',async()=>{
 host();
 render(wrap(React.createElement(PreviewPane,{projectId:'p1',tasks:[task([write('src/a.ts')])],onProgress(){},onClose(){}})));
 assert.equal(screen.queryByRole('tab',{name:'Mockup'}),null);
 assert.equal(screen.queryByRole('tab',{name:/Screenshots/}),null);
 assert.ok(screen.getByLabelText('Open a page running on this Mac'),'it opens on Local page');
 assert.equal(screen.queryByTestId('preview-frame'),null);
});

test('esc and the switch go back to Progress; the close button closes the column',async()=>{
 host();
 let back=0,closed=0;
 render(wrap(React.createElement(PreviewPane,{projectId:'p1',tasks:[task([write('m/index.html')])],onProgress(){back++;},onClose(){closed++;}})));
 fireEvent.keyDown(screen.getByTestId('preview-pane'),{key:'Escape'});
 assert.equal(back,1);
 fireEvent.keyDown(screen.getByTestId('preview-pane'),{key:'Enter'});
 assert.equal(back,1,'negative control: another key does nothing');
 fireEvent.click(screen.getByRole('radio',{name:'Progress'}));
 assert.equal(back,2);
 fireEvent.click(screen.getByRole('radio',{name:'Preview'}));
 assert.equal(back,2,'choosing Preview while in Preview stays');
 fireEvent.click(screen.getByRole('button',{name:'Close the column'}));
 assert.equal(closed,1);
});

test('⌘\\ toggles the column and ⌥⌘P switches, only while a thread is open',()=>{
 let toggled=0,switched=0;
 function Probe({active}){usePreviewShortcuts(active,{toggleColumn:()=>toggled++,switchView:()=>switched++});return null;}
 const {rerender}=render(React.createElement(Probe,{active:true}));
 fireEvent.keyDown(window,{key:'\\',code:'Backslash',metaKey:true});
 fireEvent.keyDown(window,{key:'π',code:'KeyP',metaKey:true,altKey:true});
 assert.deepEqual([toggled,switched],[1,1]);
 // Negative controls: no modifier, shift, ⌘P without ⌥, and an inactive thread.
 fireEvent.keyDown(window,{key:'\\',code:'Backslash'});
 fireEvent.keyDown(window,{key:'P',code:'KeyP',metaKey:true,altKey:true,shiftKey:true});
 fireEvent.keyDown(window,{key:'p',code:'KeyP',metaKey:true});
 rerender(React.createElement(Probe,{active:false}));
 fireEvent.keyDown(window,{key:'\\',code:'Backslash',metaKey:true});
 assert.deepEqual([toggled,switched],[1,1]);
});

test('at 1052 opening Preview folds the sidebar and closing restores it; at 1512 it stays',async()=>{
 let state;
 function Read(){state=useSidebar().open;return null;}
 const tree=(active,width)=>React.createElement(SidebarProvider,null,React.createElement(FoldSidebarWhile,{active,width:()=>width}),React.createElement(Read));
 const {rerender}=render(tree(false,1052));
 assert.equal(state,true);
 rerender(tree(true,1052));
 await waitFor(()=>assert.equal(state,false));
 rerender(tree(false,1052));
 await waitFor(()=>assert.equal(state,true));
 cleanup();
 // Negative control: a wide window keeps the sidebar.
 const wide=render(tree(false,1512));
 wide.rerender(tree(true,1512));
 await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(state,true);
});

test('no frame anywhere in the app is given allow-same-origin',()=>{
 const dir=new URL('.',import.meta.url);
 for(const name of readdirSync(dir).filter(n=>/\.tsx?$/.test(n))){
  const source=readFileSync(new URL(name,dir),'utf8');
  assert.ok(!source.includes('allow-same-origin')||name==='PreviewPane.tsx','only the explaining comment names it: '+name);
 }
 const pane=readFileSync(new URL('PreviewPane.tsx',dir),'utf8');
 assert.ok(!/sandbox=\{?["'][^"']*allow-same-origin/.test(pane));
 assert.ok(!/allow-top-navigation|allow-popups|allow-modals/.test(pane));
});

test('esc in Preview never stops the run: the shell counts Preview as an open panel for Esc',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 const call=shell.slice(shell.indexOf('useEscapeToStop(()=>'),shell.indexOf('useEscapeToStop(()=>')+400);
 assert.match(call,/panelOpen:[^,]*\|\|previewShown/);
 // Negative control: the same rule with Preview left out would stop a running task on Esc.
 return import('./escapeToStop.ts').then(({escapeStops})=>{
  const base={running:true,inRoom:false,paletteOpen:false,replying:false,overlayOpen:false};
  assert.equal(escapeStops({...base,panelOpen:true}),false);
  assert.equal(escapeStops({...base,panelOpen:false}),true);
 });
});

// Beta.15, group 1: a reply's local page opens with one click, shell-made files come from git, a reply's path opens here.
const {agentPages,threadStart}=await import('./PreviewPane.tsx');
function hostWith(answers){
 const calls=[];
 window.__TAURI_INTERNALS__={transformCallback:()=>1,invoke:async(command,args)=>{calls.push({command,args});
  if(command==='preview_grant')return {grant:'ab12',base:'dervo-preview://ab12/'};
  if(command==='local_request'&&args.operation in answers)return answers[args.operation](args.args);
  throw new Error(command==='local_request'?args.operation:command);}};
 return calls;
}
const served=(extra={})=>({...task([{tool:'native:Bash',state:'delivered',command:'python3 -m http.server 8840 --directory evidence',started_at:90,run_id:'r1'}]),
 messages:[{role:'user',text:'Make a mockup and serve it',created_at:50},{role:'assistant',text:'Serving it at http://localhost:8840/mock.html.',created_at:120}],...extra});

test('local pages an agent started are offered with Open, and nothing opens until you click',async()=>{
 assert.deepEqual(agentPages([served()]),['http://localhost:8840/mock.html','http://localhost:8840/']);
 assert.deepEqual(agentPages([task([{tool:'native:Bash',state:'delivered',command:'curl https://example.com:8443/x'}])]),[],'never a remote address');
 // Design review, 23 Sep: a bare -p is a port only for something that serves.
 const bash=command=>task([{tool:'native:Bash',state:'delivered',command}]);
 for(const command of ['mkdir -p 2025/reports','ssh -p 22 build@host','psql -p 5432 dervo','cp -p 1234 backup'])
  assert.deepEqual(agentPages([bash(command)]),[],command);
 assert.deepEqual(agentPages([bash('npx serve -p 3000 dist')]),['http://localhost:3000/']);
 assert.deepEqual(agentPages([bash('docker run -p 8080:80 site')]),['http://localhost:8080/']);
 assert.deepEqual(agentPages([bash('npm run dev -- --port 5174')]),['http://localhost:5174/']);
 hostWith({preview_changes:()=>({files:[]})});
 const props={projectId:'p1',tasks:[served()],onProgress(){},onClose(){}};
 const {rerender}=render(wrap(React.createElement(PreviewPane,props)));
 assert.equal(screen.queryByTestId('preview-frame'),null,'nothing opened by itself');
 const started=screen.getByRole('group',{name:/Pages this thread/});
 assert.match(started.textContent,/localhost:8840\/mock\.html/);
 fireEvent.click(started.querySelectorAll('button')[0]);
 let frame=await screen.findByTestId('preview-frame');
 assert.equal(frame.getAttribute('src'),'http://localhost:8840/mock.html');
 assert.equal(frame.getAttribute('sandbox'),'allow-scripts allow-forms');
 // A reply's Open: one click, the page it named.
 rerender(wrap(React.createElement(PreviewPane,{...props,request:{target:{kind:'page',url:'http://127.0.0.1:5173/'},at:2}})));
 await waitFor(()=>assert.equal(screen.getByTestId('preview-frame').getAttribute('src'),'http://127.0.0.1:5173/'));
});

test('a reply’s request is shown once and handed back, and the shell keeps it to the thread it came from (design review, 23 Sep)',async()=>{
 hostWith({preview_changes:()=>({files:[]})});
 let handed=0;
 render(wrap(React.createElement(PreviewPane,{projectId:'p1',tasks:[served()],onProgress(){},onClose(){},onRequestHandled:()=>{handed++;},request:{target:{kind:'page',url:'http://localhost:8840/'},at:5}})));
 await waitFor(()=>assert.equal(screen.getByTestId('preview-frame').getAttribute('src'),'http://localhost:8840/'));
 assert.equal(handed,1,'handed back once, so reopening Preview later doesn’t replay it');
 const {readFileSync}=await import('node:fs');
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/request=\{previewRequest\?\.scope===guideScope\?previewRequest:null\} onRequestHandled=\{\(\)=>setPreviewRequest\(null\)\}/,'another thread’s Preview never gets it');
 assert.match(shell,/setPreviewRequest\(\{target,at:Date\.now\(\),scope:guideScope\}\)/);
});

test('files made with shell commands come from git status since the thread began, and reload when a run ends',async()=>{
 assert.equal(threadStart([served()]),50);
 assert.equal(threadStart([task([])]),null);
 const calls=hostWith({preview_changes:()=>({files:[{path:'evidence/shots/home.png',at:130},{path:'evidence/mock.html',at:125}]})});
 const props={projectId:'p1',onProgress(){},onClose(){}};
 const {rerender}=render(wrap(React.createElement(PreviewPane,{...props,tasks:[served()]})));
 const frame=await screen.findByTestId('preview-frame');
 assert.equal(frame.getAttribute('src'),'dervo-preview://ab12/evidence/mock.html');
 assert.ok(screen.getByRole('tab',{name:/Screenshots/}));
 const reads=()=>calls.filter(c=>c.command==='local_request'&&c.args.operation==='preview_changes');
 assert.deepEqual(reads()[0].args.args,{projectId:'p1',since:50});
 rerender(wrap(React.createElement(PreviewPane,{...props,tasks:[served({state:'ready_for_review',runs:[{id:'r1'},{id:'r2'}]})]})));
 await waitFor(()=>assert.equal(reads().length,2,'read again after the run'));
});

test('a path from a reply opens in its tab; Markdown renders as text without HTML; other files say why not',async()=>{
 const calls=hostWith({preview_changes:()=>({files:[]}),project_file_text:()=>({path:'docs/notes.md',text:'# Plan\n\n<script>alert(1)</script>\n\n![x](http://evil.test/x.png) [link](http://evil.test)',truncated:false})});
 const props={projectId:'p1',tasks:[served()],onProgress(){},onClose(){}};
 const {rerender}=render(wrap(React.createElement(PreviewPane,{...props,request:{target:{kind:'file',path:'./docs/notes.md'},at:1}})));
 const doc=await screen.findByTestId('preview-markdown');
 assert.equal(doc.querySelector('h1').textContent,'Plan');
 assert.equal(doc.querySelector('script'),null);
 assert.equal(doc.querySelector('img'),null);
 assert.equal(doc.querySelector('a'),null,'links stay text');
 assert.equal(screen.getByRole('tab',{name:/Docs/}).getAttribute('aria-selected'),'true');
 assert.deepEqual(calls.find(c=>c.args?.operation==='project_file_text').args.args,{projectId:'p1',path:'docs/notes.md'});
 rerender(wrap(React.createElement(PreviewPane,{...props,request:{target:{kind:'file',path:'../outside.html'},at:2}})));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/inside this project/));
 rerender(wrap(React.createElement(PreviewPane,{...props,request:{target:{kind:'file',path:'out/report.pdf'},at:3}})));
 await waitFor(()=>assert.equal(screen.getByTestId('preview-frame').getAttribute('src'),'dervo-preview://ab12/out/report.pdf'));
 assert.equal(screen.getByTestId('preview-frame').getAttribute('sandbox'),'allow-scripts','a PDF gets the mockup sandbox');
});
