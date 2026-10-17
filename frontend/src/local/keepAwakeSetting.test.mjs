import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
globalThis.window=dom.window;globalThis.document=dom.window.document;for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup,waitFor}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {KeepAwakeSetting}=await import('./KeepAwakeSetting.tsx');
after(()=>{cleanup();dom.window.close();});
afterEach(()=>cleanup());

// Bryce, 9 Oct: the Mac has to stay on for the phone to make sense. Dervo keeps it awake while agents run, on unless
// you turn it off, and says that closing the lid still sleeps it.
function mount(state){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='keep_awake_set'){state={...state,enabled:args.enabled,holding:args.enabled&&state.holding};return state;}
  if(operation==='keep_awake_status')return state;throw Error(operation);}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(KeepAwakeSetting)));
 return {view,calls};
}
const line='Keeping your Mac awake while these run. Closing the lid still puts it to sleep.';

test('it’s on, says so while it holds, and turning it off asks the host at once',async()=>{
 const {view,calls}=mount({enabled:true,holding:true,supported:true,line});
 const toggle=await waitFor(()=>{const s=view.getByRole('switch');assert.equal(s.getAttribute('aria-checked'),'true');assert.equal(s.disabled,false);return s;});
 assert.match(view.getByTestId('keep-awake-setting').textContent,/Keeping it awake now\. Closing the lid still puts it to sleep\./);
 fireEvent.click(toggle);
 await waitFor(()=>assert.equal(view.getByRole('switch').getAttribute('aria-checked'),'false'));
 assert.deepEqual(calls.filter(([op])=>op==='keep_awake_set'),[['keep_awake_set',{enabled:false}]]);
 assert.match(view.getByTestId('keep-awake-setting').textContent,/Only while an agent is running\. Closing the lid still puts it to sleep\./);
});

test('a computer that can’t hold it shows the switch off and disabled, and says why',async()=>{
 const {view}=mount({enabled:true,holding:false,supported:false,line});
 await waitFor(()=>assert.match(view.getByTestId('keep-awake-setting').textContent,/Not available on this Mac\./));
 const toggle=view.getByRole('switch');
 assert.equal(toggle.getAttribute('aria-checked'),'false');
 assert.equal(toggle.disabled,true);
});
