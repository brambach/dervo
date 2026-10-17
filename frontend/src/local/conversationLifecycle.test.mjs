import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','Element','MutationObserver','Node','Event','CustomEvent','DocumentFragment','getComputedStyle'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.localStorage=dom.window.localStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.requestAnimationFrame=cb=>setTimeout(()=>cb(Date.now()),0);globalThis.cancelAnimationFrame=id=>clearTimeout(id);window.requestAnimationFrame=globalThis.requestAnimationFrame;window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
dom.window.HTMLElement.prototype.scrollIntoView=function(){};dom.window.HTMLElement.prototype.hasPointerCapture=()=>false;dom.window.HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {ArchivedConversations,forgetConversations,deleteConversation}=await import('./ConversationLifecycle.tsx');
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();});after(()=>dom.window.close());
function host(handler=()=>({})){const calls=[];window.__TAURI_INTERNALS__={invoke:async(command,{operation,args})=>{assert.equal(command,'local_request');calls.push({operation,args});return handler(operation,args);}};return calls;}
const frame=(Component,props)=>React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(Component,props)));
const design={id:'d',name:'Design notes',visibility:'shared',project_id:'p',profile_id:'a',model:'m',archived_at:100};

test('the archived list names each conversation and Restore sends the exact ids then refreshes',async()=>{
 let restored=0;const calls=host(op=>op==='conversations_archived'?[design,{...design,id:'x',name:'Mine',visibility:'private'}]:{});
 render(frame(ArchivedConversations,{projectId:'p',onRestored:async()=>{restored++;},onDelete(){}}));
 await screen.findByText(/Design notes/);
 assert.match(screen.getByTestId('archived-conversations').textContent,/Archived2/);
 fireEvent.click(screen.getByRole('button',{name:'Restore Design notes'}));
 await waitFor(()=>assert.equal(restored,1));
 assert.deepEqual(calls.filter(c=>c.operation==='conversation_restore').map(c=>c.args),[{projectId:'p',channelId:'d'}]);
});

test('an empty archive renders nothing, and an archived row deletes only after its in-menu confirm',async()=>{
 host(op=>op==='conversations_archived'?[]:{});
 const deleted=[];const onDelete=async c=>{deleted.push(c.id);};
 const view=render(frame(ArchivedConversations,{projectId:'p',onRestored:async()=>{},onDelete}));
 await new Promise(r=>setTimeout(r,20));
 assert.equal(screen.queryByTestId('archived-conversations'),null);
 host(op=>op==='conversations_archived'?[design]:{});
 view.rerender(frame(ArchivedConversations,{projectId:'p2',onRestored:async()=>{},onDelete}));
 fireEvent.keyDown(await screen.findByRole('button',{name:'Options for Design notes'}),{key:'Enter'});
 assert.deepEqual((await screen.findAllByRole('menuitem')).map(i=>i.textContent),['Restore','Delete…']);
 fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'}));
 const confirm=await screen.findByRole('alertdialog');
 assert.equal(deleted.length,0,'choosing Delete… only asks');
 assert.match(confirm.textContent,/^Delete Design notes\?/);
 assert.match(confirm.textContent,/can’t be undone/);assert.match(confirm.textContent,/Restore brings it back instead/);
 await waitFor(()=>assert.ok(document.activeElement===within(confirm).getByRole('menuitem',{name:'Cancel'}),'Cancel is first and focused'));
 fireEvent.click(within(confirm).getByRole('menuitem',{name:'Delete thread'}));
 await waitFor(()=>assert.deepEqual(deleted,['d']));
});

test('a refused delete keeps the confirm open with the host’s reason',async()=>{
 host(op=>op==='conversations_archived'?[design]:{});
 render(frame(ArchivedConversations,{projectId:'p',onRestored:async()=>{},onDelete:async()=>{throw Error('Finish or reconcile this conversation’s tasks first.');}}));
 fireEvent.keyDown(await screen.findByRole('button',{name:'Options for Design notes'}),{key:'Enter'});
 fireEvent.click(await screen.findByRole('menuitem',{name:'Delete…'}));
 fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('menuitem',{name:'Delete thread'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/reconcile/));
 assert.ok(screen.getByRole('alertdialog'));
});

test('the delete helper sends DELETE plus the exact id with the thread’s own project, and forgetting clears only its drafts',async()=>{
 localStorage.setItem('dervo-coding-pending:d','{}');localStorage.setItem('dervo-pending:d:1','x');localStorage.setItem('dervo-coding-pending:other','{}');
 const calls=host(()=>({id:'d',channelIds:['d']}));
 await deleteConversation({id:'d',project_id:'p'});
 assert.deepEqual(calls,[{operation:'conversation_delete',args:{projectId:'p',channelId:'d',confirmation:'DELETE d'}}]);
 forgetConversations(['d']);
 assert.equal(localStorage.getItem('dervo-coding-pending:d'),null);assert.equal(localStorage.getItem('dervo-pending:d:1'),null);
 assert.equal(localStorage.getItem('dervo-coding-pending:other'),'{}','other conversations keep their pending ids');
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/await deleteConversation\(conversation\);\s*forgetConversations\(\[conversation\.id\]\);/,'the thread menus delete through the helper that sends the token');
 // Beta.16 (b16/lifecycle's ask): the sidebar draws the Archived fold itself and gets its two handlers from the shell.
 assert.match(source,/onArchivedRestored=\{async conversation=>\{await conversations\.refetch\(\);setArrived\(conversation\.id\);\}\} onArchivedDelete=\{deleteThread\}/);
 assert.doesNotMatch(source,/DeleteConversationDialog|CreateConversationDialog/,'no typed-DELETE dialog and no create dialog');
});

test('A9: the Archived row turns its chevron and offers Restore as a word',async()=>{
 host(op=>op==='conversations_archived'?[design]:{});
 render(frame(ArchivedConversations,{projectId:'p',onRestored:async()=>{},onDelete(){}}));
 const restore=await screen.findByRole('button',{name:'Restore Design notes'});
 assert.equal(restore.textContent,'Restore');
 const summary=screen.getByTestId('archived-conversations').querySelector('summary');
 assert.ok(summary.querySelector('.dervo-archived-chevron'));
 assert.match(summary.textContent,/^Archived1$/);
});

test('behaviour review S1: Home’s rows and this project’s notifications open a thread through openThread, which brings an archived one back',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/onOpen=\{id=>void openThread\(id\)\} onOpenTask=\{navigate\.openTask\} onHandOff/);
 assert.match(source,/if\(target===projectId\)void openThread\(conversation\);else setChannel\(conversation\)/);
 assert.match(source,/await restoreConversation\(\{id,project_id:projectId\}\)/);
});
