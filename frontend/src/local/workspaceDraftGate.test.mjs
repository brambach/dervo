import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost'});
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.HTMLElement=dom.window.HTMLElement;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');const {render,cleanup,waitFor,fireEvent}=await import('@testing-library/react');
const {WorkspaceDraftGate}=await import('./WorkspaceDraftGate.tsx');
const drafts=await import('@/features/messages/lib/useDrafts');
const key='dervo:project:channel';
const draft=content=>({content,channelId:'channel',selectionStart:0,selectionEnd:0,createdAt:'today',updatedAt:'today',pendingImeta:[],spoileredAttachmentUrls:[],status:'active'});
function setup(call){
 localStorage.clear();drafts.clearAllDrafts();drafts.initDraftStore('local-user','dervo-local');
 const callbacks=new Map(),events=new Map(),calls=[];let id=0;
 window.__TAURI_INTERNALS__={transformCallback:fn=>{callbacks.set(++id,fn);return id;},invoke:async(command,args)=>{
  calls.push(command);
  if(command==='plugin:event|listen'){events.set(args.event,args.handler);return args.handler;}
  if(command==='plugin:event|unlisten')return;
  if(command==='draft_guard_ready'||command==='draft_exit')return;
  if(command==='local_request')return call(args.operation,args.args);
  throw Error(command);
 }};
 window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
 return {calls,exit:()=>callbacks.get(events.get('dervo-save-before-exit'))({payload:null})};
}
function Editor(){return React.createElement('textarea',{'aria-label':'Draft editor',defaultValue:drafts.loadDraftEntry(key)?.content??''});}
after(()=>{cleanup();dom.window.close();});

test('restores before mounting editor and freezes edits until quit save is acknowledged',async()=>{
 let open,save;const native=setup(async op=>{
  if(op==='drafts_list')return new Promise(resolve=>open=resolve);
  return new Promise(resolve=>save=resolve);
 });
 const ui=render(React.createElement(WorkspaceDraftGate,null,React.createElement(Editor)));
 assert.equal(ui.queryByLabelText('Draft editor'),null);
 await React.act(async()=>open({entries:[{key,projectId:'project',channelId:'channel',revision:1,draft:draft('restored')}]}));
 assert.equal(ui.getByLabelText('Draft editor').value,'restored');assert.ok(native.calls.includes('draft_guard_ready'));
 await React.act(async()=>{drafts.saveDraftEntry(key,draft('latest'));void native.exit();});
 assert.equal(ui.getByLabelText('Draft editor').parentElement.inert,true);
 assert.equal(native.calls.includes('draft_exit'),false);
 await React.act(async()=>save({revision:2}));
 await waitFor(()=>assert.ok(native.calls.includes('draft_exit')));
 cleanup();
});

test('refused legacy text remains editable and failed quit leaves retry available',async()=>{
 let value=null,revision=0;
 const native=setup(async(op,args)=>{
  if(op==='drafts_list')return {entries:[{key,projectId:'project',channelId:'channel',revision,draft:value}]};
  if(args.draft?.content.length>5)throw Error('too large');
  value=args.draft;return {revision:++revision};
 });
 drafts.saveDraftEntry(key,draft('legacy oversized text'));
 const ui=render(React.createElement(WorkspaceDraftGate,null,React.createElement(Editor)));
 await waitFor(()=>assert.equal(ui.getByLabelText('Draft editor').value,'legacy oversized text'));
 assert.ok(ui.getByRole('alert'));
 await React.act(async()=>native.exit());
 assert.equal(ui.getByLabelText('Draft editor').parentElement.inert,false);
 assert.equal(native.calls.includes('draft_exit'),false);
 await React.act(async()=>drafts.saveDraftEntry(key,draft('short')));
 await React.act(async()=>fireEvent.click(ui.getByRole('button',{name:'Retry'})));
 await waitFor(()=>assert.equal(ui.queryByRole('alert'),null));assert.equal(value.content,'short');
 await React.act(async()=>native.exit());assert.ok(native.calls.includes('draft_exit'));
 cleanup();
});
