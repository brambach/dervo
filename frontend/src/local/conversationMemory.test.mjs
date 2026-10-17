import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const wrap=element=>React.createElement(ThemeProvider,null,element);
const {render,fireEvent,waitFor,cleanup,act}=await import('@testing-library/react');
const {ConversationMemory}=await import('./ConversationMemory.tsx');
after(()=>dom.window.close());
const props={projectId:'project',channelId:'channel',request:{id:'one',state:'completed',knowledge:{count:1,omitted:0}}};
const snapshot={project_id:'project',captured_at:'2026-09-13T01:00:00Z',omitted:0,rules:[{rule_id:'rule',version_id:'version',text:'Use three bullets. <img src="https://example.test/private">',provenance:'An approved project decision'}]};
function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(args);}};return calls;}

test('guidance is fetched only on request, rendered inertly, and returns focus on close',async()=>{
 const calls=host(()=>({id:'one',knowledge_snapshot:snapshot}));
 try{
  const view=render(wrap(React.createElement(ConversationMemory,props)));
  assert.equal(calls.length,0);
  const trigger=view.getByRole('button',{name:'Project memory · 1 rule'});trigger.focus();fireEvent.click(trigger);
  await view.findByText(snapshot.rules[0].text);
  assert.deepEqual(calls,[{operation:'harness_status',args:{projectId:'project',channelId:'channel',requestId:'one'}}]);
  assert.equal(view.queryByRole('img'),null);
  assert.ok(view.getByText(/doesn't prove the agent followed it/));
  fireEvent.keyDown(view.getByRole('dialog'),{key:'Escape',code:'Escape'});
  await waitFor(()=>assert.equal(view.queryByRole('dialog'),null));
  await waitFor(()=>assert.equal(document.activeElement,trigger));
  fireEvent.click(trigger);await view.findByText(snapshot.rules[0].text);
  assert.equal(calls.length,2,'reopening checks access again');
 }finally{cleanup();}
});

test('late context from another request never appears and unknown records stay unknown',async()=>{
 let resolveFirst;const calls=host(args=>args.requestId==='one'?new Promise(resolve=>{resolveFirst=resolve;}):({id:'two',knowledge_snapshot:null}));
 try{
  const view=render(wrap(React.createElement(ConversationMemory,props)));fireEvent.click(view.getByRole('button'));
  await waitFor(()=>assert.equal(calls.length,1));
  view.rerender(wrap(React.createElement(ConversationMemory,{...props,request:{...props.request,id:'two'}})));
  await view.findByText('No guidance record was saved for this earlier request.');
  await act(async()=>resolveFirst({id:'one',knowledge_snapshot:snapshot}));
  assert.equal(view.queryByText(snapshot.rules[0].text),null);
 }finally{cleanup();}
});

test('failed or mismatched reads offer retry without displaying stale guidance',async()=>{
 let tries=0;host(()=>++tries===1?({id:'one',knowledge_snapshot:{...snapshot,project_id:'other'}}):({id:'one',knowledge_snapshot:{...snapshot,rules:[],omitted:2}}));
 try{
  const view=render(wrap(React.createElement(ConversationMemory,props)));fireEvent.click(view.getByRole('button'));
  await view.findByRole('alert');assert.equal(view.queryByText(snapshot.rules[0].text),null);
  fireEvent.click(view.getByRole('button',{name:'Try again'}));
  await view.findByText('No project rules included in this request');
  assert.ok(view.getByText('2 omitted by source checks or context limits.'));
 }finally{cleanup();}
});
