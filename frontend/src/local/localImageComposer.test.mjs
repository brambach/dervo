import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','CustomEvent','Event','localStorage','getComputedStyle','Image','HTMLCanvasElement','Text','ShadowRoot','DOMParser'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.self=window;
window.HTMLCanvasElement.prototype.getContext=()=>null;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);globalThis.cancelAnimationFrame=clearTimeout;
window.requestAnimationFrame=globalThis.requestAnimationFrame;window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
globalThis.ClipboardEvent??=class extends window.Event{};
const React=await import('react');
const {render,screen,fireEvent,cleanup,waitFor}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {CommunitiesProvider}=await import('../features/communities/useCommunities.tsx');
const {ThemeProvider}=await import('../shared/theme/ThemeProvider.tsx');
const {TooltipProvider}=await import('../shared/ui/tooltip.tsx');
const {EmojiBurstProvider}=await import('../shared/ui/EmojiBurstProvider.tsx');
const {PoofBurstProvider}=await import('../shared/ui/PoofBurstProvider.tsx');
const {createRootRoute,createRoute,createRouter,createMemoryHistory,RouterProvider}=await import('@tanstack/react-router');
const {LocalImageComposer,SentImages}=await import('./LocalImageComposer.tsx');
after(async()=>{await new Promise(resolve=>setTimeout(resolve,50));dom.window.close();});afterEach(()=>{cleanup();localStorage.clear();});

test('sent pictures show as thumbnails in one .dervo-sent-images row the thread stylesheet can hang under your request (#13)',async(t)=>{
 window.__TAURI_INTERNALS__={invoke:async()=>({name:'a.png',dataUrl:'data:image/png;base64,fixture'})};
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 let tree=React.createElement(SentImages,{projectId:'p',channelId:'c',ids:['one','two']});
 for(const Provider of [TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 const row=screen.getByTestId('sent-images');
 assert.ok(row.classList.contains('dervo-sent-images'));
 await waitFor(()=>assert.deepEqual([...row.querySelectorAll('button')].map(b=>b.getAttribute('aria-label')),['Open a.png','Open a.png']));
 assert.equal(row.querySelectorAll('button img').length,2,'the pictures themselves, as the Claude app shows them');
 assert.equal(row.querySelector('button').closest('.dervo-sent-images'),row,'the buttons are the row’s own children, so the row’s alignment places them');
 view.unmount();
});

for(const interrupted of [false,true])test(`account recovery loads saved attachments without sending, interrupted read: ${interrupted}`,async(t)=>{
 let setAccess,reads=0,sends=0,finishRead;
 const row={id:'saved-image',name:'Saved.png',state:'ready',size:20,received:20,width:20,height:20,dataUrl:'data:image/png;base64,fixture'};
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{
  if(payload.operation==='image_drafts'){
   reads++;
   if(interrupted&&reads===1)return await new Promise(resolve=>{finishRead=resolve;});
   return {images:[row]};
  }
  if(payload.operation==='image_get'||payload.operation==='image_thumb')return row;
  return [];
 }};
 function Trial(){const [accessReady,set]=React.useState(false);setAccess=set;return React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',channelName:'Saved draft',accessReady,onSend:async()=>sends++});}
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await screen.findByText(/Attachments are waiting for Dervo account access/);
 assert.equal(reads,0);assert.equal(screen.getByLabelText('Attach files').disabled,true);
 await React.act(async()=>setAccess(true));
 if(interrupted){
  await waitFor(()=>assert.equal(typeof finishRead,'function'));
  await React.act(async()=>setAccess(false));
  await React.act(async()=>setAccess(true));
  await React.act(async()=>finishRead({images:[row]}));
 }
 await screen.findByAltText('Saved.png');
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 assert.equal(reads,interrupted?2:1);assert.equal(sends,0);
 await React.act(async()=>setAccess(false));
 assert.equal(screen.getByLabelText('Attach files').disabled,true);
 assert.equal(screen.getByLabelText('Remove Saved.png').disabled,true);
 assert.equal(sends,0);view.unmount();client.clear();
});

test('a failed draft lookup stays visible and retries without sending a message',async(t)=>{
 let fail=true,sends=0;
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{
  if(payload.operation==='image_drafts'){if(fail)throw Error('Unavailable');return {images:[]};}
  return [];
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 t.after(()=>client.clear());
 const root=createRootRoute();
 const route=createRoute({getParentRoute:()=>root,path:'/',component:()=>React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',channelName:'Screenshots',onSend:async()=>sends++})});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});
 await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await screen.findByRole('alert');
 fail=false;fireEvent.click(screen.getByRole('button',{name:'Try again'}));
 await waitFor(()=>{assert.equal(!!screen.queryByRole('alert'),false);assert.equal(screen.getByLabelText('Attach files').disabled,false);});
 assert.equal(sends,0);
 view.unmount();client.clear();
});


// Polish pass 2: opening a thread while the host was on a long request got "Attached files didn’t load." and a locked composer.
test('a busy answer to the draft lookup is asked again by itself, quietly, and anything else still shows at once',{timeout:20000},async(t)=>{
 let turnedAway=2,reads=0;
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{
  if(payload.operation==='image_drafts'){reads++;if(turnedAway-->0)throw Error('Dervo is busy with thread folder, so this wasn’t done. Try again in a moment.');return {images:[]};}
  return [];
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 t.after(()=>client.clear());
 const root=createRootRoute();
 const route=createRoute({getParentRoute:()=>root,path:'/',component:()=>React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',channelName:'Screenshots',onSend:async()=>{}})});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});
 await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await waitFor(()=>assert.equal(reads,1));
 assert.equal(screen.queryByRole('alert'),null,'no failure line for a busy answer');
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false),{timeout:8000});
 assert.equal(reads,3,'asked again twice, then it loaded');
 assert.equal(screen.queryByRole('alert'),null);
 view.unmount();client.clear();
});

test('an active send stays quiet but an unconfirmed result keeps image recovery visible',async(t)=>{
 const pending={projectId:'p',channelId:'c',text:'Read this image',images:['image-1']};
 localStorage.setItem('dervo-coding-pending:c',JSON.stringify(pending));
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{
  if(payload.operation==='image_drafts')return {images:[]};
  if(payload.operation==='image_get'||payload.operation==='image_thumb')return {id:'image-1',name:'Screenshot.png',state:'ready',size:20,received:20,width:20,height:20,dataUrl:'data:image/png;base64,fixture'};
  return [];
 }};
 let settle,sends=0;
 function Harness(){
  const [sending,setSending]=React.useState(true);settle=()=>setSending(false);
  return React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',channelName:'Screenshots',isSending:sending,onSend:async()=>sends++});
 }
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Harness});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await screen.findByAltText('Screenshot.png');
 assert.equal(!!screen.queryByText(/previous send hasn’t been confirmed/),false);
 await React.act(async()=>settle());
 await screen.findByText(/previous send hasn’t been confirmed/);
 assert.equal(screen.getByLabelText('Remove Screenshot.png').disabled,true);
 assert.deepEqual(JSON.parse(localStorage.getItem('dervo-coding-pending:c')),pending);
 assert.equal(sends,0);view.unmount();client.clear();
});

for(const movedElsewhere of [false,true])test(`new conversation focuses when ready, other input selected: ${movedElsewhere}`,async(t)=>{
 let resolveDrafts,setDisabled;
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>payload.operation==='image_drafts'?await new Promise(resolve=>{resolveDrafts=resolve;}):[]};
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 function Trial(){const [disabled,change]=React.useState(true);setDisabled=change;return React.createElement(React.Fragment,null,
  React.createElement('input',{'aria-label':'Other work'}),
  React.createElement(LocalImageComposer,{projectId:'p',channelId:'new-conversation',channelName:'New conversation',disabled,focusWhenReady:true,onSend:async()=>{}}));}
 const root=createRootRoute();const route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await waitFor(()=>assert.equal(typeof resolveDrafts,'function'));
 const other=screen.getByRole('textbox',{name:'Other work'});
 if(movedElsewhere)other.focus();
 await React.act(async()=>{setDisabled(false);resolveDrafts({images:[]});});
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 await new Promise(resolve=>setTimeout(resolve,30));
 const editor=view.container.querySelector('[data-testid="message-input"]');assert.ok(editor);
 assert.equal(document.activeElement,movedElsewhere?other:editor);
 other.focus();await React.act(async()=>setDisabled(true));await React.act(async()=>setDisabled(false));
 await new Promise(resolve=>setTimeout(resolve,30));assert.equal(document.activeElement,other);
 view.unmount();client.clear();
});

test('a draft handed over from the project room sends itself once, and only after the images have loaded',async(t)=>{
 // 18 Sep: the composer's auto-submit fires on its first tick, but this composer refuses any send until its image
 // drafts have loaded, so a message started from the room was refused every time. It must wait, then send once.
 const {persistDraftEntry}=await import('../features/messages/lib/useDrafts.ts');
 // The editor measures the caret when a draft is restored and sent; jsdom has no layout, so it gets empty boxes.
 const rects=()=>Object.assign([],{item:()=>null});
 for(const proto of [window.Element.prototype,window.Range?.prototype].filter(Boolean)){
  proto.getClientRects??=rects;proto.getBoundingClientRect??=()=>({x:0,y:0,width:0,height:0,top:0,left:0,right:0,bottom:0});
 }
 globalThis.Range??=window.Range;
 document.elementFromPoint??=()=>null;
 let finishRead,completed=0;
 const sent=[];
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{
  if(payload.operation==='image_drafts')return await new Promise(resolve=>{finishRead=resolve;});
  return [];
 }};
 persistDraftEntry('dervo:p:c','Rank exact titles first','c',[],[]);
 function Trial(){return React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',channelName:'search index',draftKey:'dervo:p:c',
  autoSubmitDraftKey:'dervo:p:c',onAutoSubmitComplete:()=>completed++,onSend:async text=>{sent.push(text);}});}
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await waitFor(()=>assert.equal(typeof finishRead,'function'));
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.deepEqual(sent,[],'nothing is sent while the images are still loading');
 await React.act(async()=>finishRead({images:[]}));
 await waitFor(()=>assert.equal(sent.length,1));
 assert.match(sent[0],/Rank exact titles first/);
 assert.equal(completed,1);
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.equal(sent.length,1,'and exactly once');
 view.unmount();client.clear();
});

for(const available of [false,true])test(`typing # offers this project's other threads and puts the name in as text, available: ${available}`,async(t)=>{
 const {ConversationMentions}=await import('./ConversationMentions.tsx');
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>payload.operation==='image_drafts'?{images:[]}:[]};
 const conversations=available?[{id:'other',name:'Release notes',visibility:'shared'}]:[];
 function Trial(){return React.createElement(ConversationMentions,{conversations,currentId:'c'},
  React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',onSend:async()=>{}}));}
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 const box=view.container.querySelector('[data-testid="message-input"]');
 box.focus();fireEvent.change(box,{target:{value:'look at #'}});box.setSelectionRange(9,9);fireEvent.select(box);
 if(!available){assert.equal(screen.queryByRole('listbox'),null,'nothing to offer, so no list');view.unmount();return;}
 const choice=await screen.findByRole('option',{name:'#Release notes'});
 assert.equal(box.getAttribute('aria-expanded'),'true');
 fireEvent.mouseDown(choice);
 await waitFor(()=>assert.equal(box.value,'look at #Release notes '));
 assert.equal(screen.queryByRole('listbox'),null);
 view.unmount();
});

test('the box keeps Enter, headings, drafts and ↑ the way the Claude app does (9 Oct review)',async(t)=>{
 const {ConversationMentions}=await import('./ConversationMentions.tsx');
 const {loadDraftEntry}=await import('../features/messages/lib/useDrafts.ts');
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>payload.operation==='image_drafts'?{images:[]}:[]};
 const sent=[];
 function Trial(){return React.createElement(ConversationMentions,{conversations:[{id:'other',name:'Release notes',visibility:'shared'}],currentId:'c'},
  React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',draftKey:'dervo:p:c',recall:'Run the tests again',onSend:async text=>{sent.push(text);}}));}
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 const box=view.container.querySelector('[data-testid="message-input"]');
 box.focus();
 // Enter in an empty box leaves no blank line behind, and nothing is sent.
 const enter=new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
 box.dispatchEvent(enter);
 assert.equal(enter.defaultPrevented,true,'no newline goes in');
 assert.deepEqual(sent,[]);
 // ↑ in the empty box brings your last message back.
 fireEvent.keyDown(box,{key:'ArrowUp'});
 await waitFor(()=>assert.equal(box.value,'Run the tests again'));
 // ...but not with a modifier held.
 fireEvent.change(box,{target:{value:''}});
 fireEvent.keyDown(box,{key:'ArrowUp',shiftKey:true});
 assert.equal(box.value,'');
 // "# " starts a markdown heading, so no list of threads opens over it.
 fireEvent.change(box,{target:{value:'# Plan'}});box.setSelectionRange(6,6);fireEvent.select(box);
 assert.equal(screen.queryByRole('listbox'),null);
 // Every edit is saved at once, so quitting straight after a keystroke keeps it.
 assert.equal(loadDraftEntry('dervo:p:c')?.content,'# Plan');
 view.unmount();
});

for(const command of [false,'/feedback','/bug'])test(`sending removes attachments immediately and restores them on failure; feedback command: ${command}`,async(t)=>{
 const {persistDraftEntry}=await import('../features/messages/lib/useDrafts.ts');
 const {isFeedbackCommand,feedbackCommandKind,takeFeedbackCommand}=await import('./feedbackCommand.ts');
 const rects=()=>Object.assign([],{item:()=>null});
 for(const proto of [window.Element.prototype,window.Range?.prototype].filter(Boolean)){
  proto.getClientRects??=rects;proto.getBoundingClientRect??=()=>({x:0,y:0,width:0,height:0,top:0,left:0,right:0,bottom:0});
 }
 globalThis.Range??=window.Range;document.elementFromPoint??=()=>null;
 const row={id:'image-1',name:'Saved.png',state:'ready',size:20,received:20,width:20,height:20,dataUrl:'data:image/png;base64,fixture'};
 const operations=[];let rejectSend,sends=0,feedback=0;
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{operations.push(payload.operation);return payload.operation==='image_drafts'?{images:[row]}:['image_get','image_thumb'].includes(payload.operation)?row:[];}};
 const text=command||'Fix this screenshot';persistDraftEntry('dervo:p:c',text,'c',[],[]);
 function Trial(){return React.createElement(LocalImageComposer,{projectId:'p',channelId:'c',channelName:'Screenshots',draftKey:'dervo:p:c',
  onLocalCommand:value=>{if(!feedbackCommandKind(value))return false;feedback++;return true;},
  onSend:async(value,_mentions,_media,images)=>{sends++;assert.equal(value,text);assert.deepEqual(images,['image-1']);await new Promise((_,reject)=>{rejectSend=reject;});}});}
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await screen.findByAltText('Saved.png');await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 fireEvent.click(screen.getByTestId('send-message'));
 if(command){await waitFor(()=>assert.equal(feedback,1));assert.equal(sends,0);assert.ok(screen.getByAltText('Saved.png'));assert.equal(document.querySelector('[data-testid="message-input"]').value,text);
  // Home asks isFeedbackCommand, then calls onFeedback with nothing: the shell takes the kind it matched, once.
  assert.equal(isFeedbackCommand(`  ${command.toUpperCase()} `),true);assert.equal(takeFeedbackCommand(),command.slice(1));assert.equal(takeFeedbackCommand(),'feedback');
  assert.equal(isFeedbackCommand('/bug the sidebar flickers'),false,'a command is the whole message');assert.equal(feedbackCommandKind('/bugs'),null);}
 else{
  await waitFor(()=>assert.equal(sends,1));assert.equal(screen.queryByAltText('Saved.png'),null);
  await React.act(async()=>rejectSend(Error('Host unavailable')));
  await screen.findByAltText('Saved.png');await waitFor(()=>assert.equal(document.querySelector('[data-testid="message-input"]').value,text));
 }
 assert.equal(operations.includes('image_discard'),false);view.unmount();client.clear();
});

async function pasteTrial(t,{draft='Keep this draft',rows=[]}={}){
 const rects=()=>Object.assign([],{item:()=>null});
 for(const proto of [window.Element.prototype,window.Range?.prototype].filter(Boolean)){
  proto.getClientRects??=rects;proto.getBoundingClientRect??=()=>({x:0,y:0,width:0,height:0,top:0,left:0,right:0,bottom:0});
 }
 globalThis.Range??=window.Range;document.elementFromPoint??=()=>null;
 const {persistDraftEntry}=await import('../features/messages/lib/useDrafts.ts');
 const saved=new Map(rows.map(row=>[row.id,row])),chunks=new Map(),calls=[],cancelled=new Set();
 let fail=false,sends=0,setConfig,beginGate;
 window.__TAURI_INTERNALS__={invoke:async(_,payload)=>{
  const {operation,args}=payload;calls.push({operation,args});
  if(operation==='image_drafts')return {images:[...saved.values()].filter(row=>!row.channelId||row.channelId===args.channelId)};
  if(operation==='image_get'||operation==='image_thumb')return {...saved.get(args.id),textPreview:'Saved text'};
  if(operation==='image_begin'){
   if(beginGate)await beginGate;
   if(fail)throw Error('Upload unavailable');
   const id=args.clientId;
   if(cancelled.has(id))throw Error('This upload was cancelled.');
   if(!saved.has(id))saved.set(id,{id,name:args.name,state:'uploading',size:args.size,received:0,width:null,height:null,channelId:args.channelId});
   return saved.get(id);
  }
  if(operation==='image_chunk'){
   const bytes=Buffer.from(args.data,'base64');chunks.set(args.id,Buffer.concat([chunks.get(args.id)??Buffer.alloc(0),bytes]));
   const row={...saved.get(args.id),received:args.offset+bytes.length};saved.set(args.id,row);return row;
  }
  if(operation==='image_finish'){const row={...saved.get(args.id),state:'ready'};saved.set(args.id,row);return row;}
  if(operation==='image_discard'||operation==='image_cancel'){const id=args.id??args.clientId;saved.delete(id);cancelled.add(id);return {id};}
  return [];
 }};
 persistDraftEntry('paste:p:c',draft,'c',[],[]);
 function Trial(){const [config,change]=React.useState({channelId:'c',show:true,accessReady:true,disabled:false});setConfig=change;
  return config.show?React.createElement(LocalImageComposer,{projectId:'p',channelName:'Paste test',...config,draftKey:`paste:p:${config.channelId}`,onSend:async()=>sends++}):null;}
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});t.after(()=>client.clear());
 const root=createRootRoute(),route=createRoute({getParentRoute:()=>root,path:'/',component:Trial});
 const router=createRouter({routeTree:root.addChildren([route]),history:createMemoryHistory({initialEntries:['/']})});await router.load();
 let tree=React.createElement(RouterProvider,{router});
 for(const Provider of [CommunitiesProvider,PoofBurstProvider,EmojiBurstProvider,TooltipProvider,ThemeProvider])tree=React.createElement(Provider,null,tree);
 const view=render(React.createElement(QueryClientProvider,{client},tree));
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 const update=async patch=>React.act(async()=>setConfig(current=>({...current,...patch})));
 const paste=text=>fireEvent.paste(view.container.querySelector('[data-testid="message-input"]'),{clipboardData:{files:[],items:[],types:['text/plain'],getData:type=>type==='text/plain'?text:''}});
 return {view,paste,update,calls,chunks,saved,fail:value=>{fail=value;},holdBegin:()=>{let release;beginGate=new Promise(resolve=>{release=resolve;});return ()=>{beginGate=null;release();};},sends:()=>sends,editor:()=>view.container.querySelector('[data-testid="message-input"]')};
}

const longPaste='\ufeff  α\t😀\r\n'.repeat(600);
test('long text attaches as a file at once, as the Claude app does, keeps the draft, and uploads exact UTF-8 bytes',async(t)=>{
 const h=await pasteTrial(t);
 h.paste(longPaste);
 await screen.findByLabelText('Remove Pasted text.txt');
 assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false,'no question card');
 assert.equal(h.calls.filter(call=>call.operation==='image_begin').length,1);
 assert.deepEqual([...h.chunks.values()][0],Buffer.from(longPaste,'utf8'));
 assert.equal(h.editor().value,'Keep this draft');assert.equal(h.sends(),0);
 await h.update({show:false});await h.update({show:true});
 await screen.findByLabelText('Remove Pasted text.txt');assert.equal(h.editor().value,'Keep this draft');
 fireEvent.click(screen.getByLabelText('Remove Pasted text.txt'));
 await waitFor(()=>assert.equal(!!screen.queryByLabelText('Remove Pasted text.txt'),false));
 assert.equal(h.sends(),0);
});

test('pending text survives remount and thread changes; paste-in-message preserves the captured selection',async(t)=>{
 const h=await pasteTrial(t,{draft:'before AFTER'});
 // "AFTER" selected when the paste comes in: Paste in message later puts the words there.
 const editor=h.editor();editor.focus();editor.setSelectionRange(7,12);
 await React.act(async()=>{await new Promise(resolve=>setTimeout(resolve,30));});
 // The card only shows once the automatic attach couldn't finish: fail it, then cancel the upload.
 h.fail(true);h.paste('z'.repeat(4000));
 fireEvent.click(await screen.findByRole('button',{name:'Cancel file upload'}));h.fail(false);
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'Paste in message'}).disabled,false));
 assert.equal(new Set(h.calls.filter(call=>call.operation==='image_begin').map(call=>call.args.clientId)).size,1,'a cancelled paste is not uploaded again by itself');
 await h.update({channelId:'other'});assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false);
 await h.update({channelId:'c'});await screen.findByRole('region',{name:'Pasted text'});
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 fireEvent.click(screen.getByRole('button',{name:'Paste in message'}));
 await waitFor(()=>assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false));
 assert.equal(h.editor().value,'before '+'z'.repeat(4000));assert.equal(h.sends(),0);
});

test('failed upload retains the choice and retries without sending; access and disabled state block attachment changes',async(t)=>{
 const h=await pasteTrial(t);
 // Without account access a long paste stays in the message: nothing uploads.
 await h.update({accessReady:false});h.paste('w'.repeat(4000));
 await waitFor(()=>assert.ok(h.editor().value.includes('w'.repeat(4000))));
 assert.equal(h.calls.some(call=>call.operation==='image_begin'),false,'nothing uploads without account access');
 await h.update({accessReady:true});await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 h.fail(true);h.paste('q'.repeat(4000));
 await screen.findByRole('button',{name:'Retry file upload'});assert.ok(screen.getByRole('region',{name:'Pasted text'}));assert.ok(h.editor().value.startsWith('Keep this draft'));
 h.fail(false);fireEvent.click(screen.getByRole('button',{name:'Retry file upload'}));
 await waitFor(()=>assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false));assert.ok(screen.getByLabelText('Remove Pasted text.txt'));assert.equal(h.sends(),0);
});

test('at the four-file limit a long paste stays in the message, and over-limit text stays in the message',async(t)=>{
 const rows=Array.from({length:4},(_,i)=>({id:`file${i}`,name:`file${i}.txt`,state:'ready',size:2,received:2,width:null,height:null}));
 // Four files already: the text goes in the message, with no card holding the composer (reviewer, 8 Oct).
 const h=await pasteTrial(t,{rows});h.paste('x'.repeat(4000));
 await waitFor(()=>assert.ok(h.editor().value.includes('x'.repeat(4000))));
 assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false);
 assert.equal(h.calls.some(call=>call.operation==='image_begin'),false);
 h.paste('short paste');assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false);assert.ok(h.editor().value.includes('short paste'));
 h.paste('😀'.repeat(16000));await screen.findByText(/more than 60 KB/);assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false);assert.ok(h.editor().value.includes('😀'.repeat(16000)));assert.equal(h.sends(),0);
});

test('cancelled pasted-text uploads use a fresh identity when attached again',async(t)=>{
 const h=await pasteTrial(t);h.fail(true);h.paste('x'.repeat(4000));
 await screen.findByRole('button',{name:'Cancel file upload'});
 const old=h.calls.find(call=>call.operation==='image_begin').args.clientId;
 fireEvent.click(screen.getByRole('button',{name:'Cancel file upload'}));
 await waitFor(()=>assert.equal(!!screen.queryByRole('button',{name:'Cancel file upload'}),false));
 h.fail(false);
 assert.equal(new Set(h.calls.filter(call=>call.operation==='image_begin').map(call=>call.args.clientId)).size,1,'Cancel never starts another upload by itself');
 fireEvent.click(screen.getByRole('button',{name:'Attach pasted text'}));
 await screen.findByLabelText('Remove Pasted text.txt');
 assert.notEqual(h.calls.filter(call=>call.operation==='image_begin').at(-1).args.clientId,old);
 assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false);assert.equal(h.sends(),0);
});

test('a late upload response cannot restore a paste discarded after remount',async(t)=>{
 const h=await pasteTrial(t);const release=h.holdBegin();h.paste('r'.repeat(4000));
 await waitFor(()=>assert.ok(h.calls.some(call=>call.operation==='image_begin')));
 await h.update({show:false});await h.update({show:true});
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'Discard paste'}).disabled,false));
 fireEvent.click(screen.getByRole('button',{name:'Discard paste'}));
 await React.act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
 await h.update({show:false});await h.update({show:true});
 await waitFor(()=>assert.equal(screen.getByLabelText('Attach files').disabled,false));
 assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false);
 assert.equal([...Array(localStorage.length)].map((_,i)=>localStorage.key(i)).some(key=>key.startsWith('dervo-pasted-text:')),false);
 assert.equal(h.editor().value,'Keep this draft');assert.equal(h.sends(),0);
});

test('storage failure keeps the paste recoverable and a second paste cannot replace it',async(t)=>{
 const h=await pasteTrial(t);h.fail(true);h.paste('a'.repeat(4000));
 fireEvent.click(await screen.findByRole('button',{name:'Cancel file upload'}));h.fail(false);
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'Paste in message'}).disabled,false));
 h.paste('b'.repeat(4000));
 await screen.findByText(/Choose what to do with the current paste first/);
 const remove=window.Storage.prototype.removeItem;
 window.Storage.prototype.removeItem=function(key){if(key.startsWith('dervo-pasted-text:'))throw Error('Storage unavailable');return remove.call(this,key);};
 try{
  fireEvent.click(screen.getByRole('button',{name:'Paste in message'}));await screen.findByText(/The text couldn’t be pasted/);
  assert.equal(h.editor().value,'Keep this draft');assert.ok(screen.getByRole('region',{name:'Pasted text'}));
  fireEvent.click(screen.getByRole('button',{name:'Discard paste'}));await screen.findByText(/The paste couldn’t be discarded/);
 }finally{window.Storage.prototype.removeItem=remove;}
 fireEvent.click(screen.getByRole('button',{name:'Paste in message'}));
 await waitFor(()=>assert.equal(!!screen.queryByRole('region',{name:'Pasted text'}),false));
 assert.equal(h.editor().value,'Keep this draft'+'a'.repeat(4000));assert.equal(h.sends(),0);
});

const dropFiles=(view,files)=>fireEvent.drop(view.container.firstChild,{dataTransfer:{files,types:['Files']}});
test('the send button stays ready while a file uploads, and a send waits for the upload and then goes (send pipeline, 9 Oct)',async(t)=>{
 const h=await pasteTrial(t);
 const release=h.holdBegin();
 dropFiles(h.view,[new File(['hello'],'notes.txt',{type:'text/plain'})]);
 await screen.findByLabelText('Remove notes.txt');
 const button=screen.getByTestId('send-message');
 assert.equal(button.getAttribute('aria-label'),'Send message','no spinner while it uploads');assert.equal(button.disabled,false);
 fireEvent.click(button);
 await screen.findByText('Sends when your attachments finish uploading.');
 assert.equal(h.sends(),0,'not before the upload finishes');
 release();
 await waitFor(()=>assert.equal(h.sends(),1));
 assert.equal(screen.queryByText('Sends when your attachments finish uploading.'),null);
 assert.equal(screen.queryByRole('alert'),null);
});

test('removing a tile mid-upload cancels it on the host, and the file behind it still uploads',async(t)=>{
 const h=await pasteTrial(t);
 const release=h.holdBegin();
 dropFiles(h.view,[new File(['first'],'first.txt',{type:'text/plain'}),new File(['second'],'second.txt',{type:'text/plain'})]);
 const remove=await screen.findByLabelText('Remove first.txt');
 assert.equal(remove.disabled,false,'a tile can go while it uploads');
 fireEvent.click(remove);
 await waitFor(()=>assert.equal(screen.queryByLabelText('Remove first.txt'),null));
 const cancel=h.calls.find(call=>call.operation==='image_cancel');
 assert.ok(cancel,'the host records the cancel');
 release();
 await waitFor(()=>assert.equal(h.calls.filter(call=>call.operation==='image_finish').length,1));
 assert.ok(screen.getByLabelText('Remove second.txt'));assert.equal(screen.queryByLabelText('Remove first.txt'),null,'the cancelled tile never comes back');
 assert.equal(screen.queryByRole('alert'),null,'a cancel is not a failure');
 assert.equal(h.calls.filter(call=>call.operation==='image_chunk'&&call.args.id===cancel.args.clientId).length,0,'nothing of it is sent after the cancel');
 assert.equal(h.sends(),0);
});
