import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','Event','KeyboardEvent','MutationObserver','CustomEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,screen,fireEvent,cleanup}=await import('@testing-library/react');
const {useEscapeToStop}=await import('./escapeToStop.ts');
const {useArchiveShortcut}=await import('./useArchiveShortcut.ts');
const {usePaletteShortcuts}=await import('./CommandPalette.tsx');
const {usePreviewShortcuts}=await import('./PreviewPane.tsx');
const {useSettingsEscape}=await import('./DervoSettings.tsx');
const {isProjectTerminalKey}=await import('./terminalKeys.ts');
afterEach(cleanup);after(()=>dom.window.close());
const h=React.createElement;

function Harness({calls,running=true}){
 const call=action=>()=>calls.push(action);
 useEscapeToStop(()=>({running,inRoom:false,paletteOpen:false,panelOpen:false,replying:false,stop:call('stop')}));
 useArchiveShortcut(true,call('archive'));
 usePaletteShortcuts({toggle:call('palette'),catchUp:call('home'),knowledge:call('knowledge'),ready:true});
 usePreviewShortcuts(true,{toggleColumn:call('column'),switchView:call('preview')});
 useSettingsEscape(true,call('close-settings'));
 return h('div',null,h('section',{'data-project-terminal':'','aria-label':'Terminal'},
  h('div',null,h('textarea',{'aria-label':'Terminal input'})),
  h('button',{onClick:call('terminal-action')},'Terminal action')),
  h('button',{'aria-label':'Outside control'},'Outside'));
}
function press(target,init){const event=new window.KeyboardEvent('keydown',{bubbles:true,cancelable:true,...init});target.dispatchEvent(event);return event;}

test('terminal Escape reaches its input without stopping the chat or closing Settings',()=>{
 const calls=[];render(h(Harness,{calls}));
 const input=screen.getByRole('textbox',{name:'Terminal input'});
 const event=press(input,{key:'Escape'});
 assert.deepEqual(calls,[],'the capture-phase stop listener must inspect the event target');
 assert.equal(event.defaultPrevented,false,'the shell can consume Escape itself');
 assert.equal(isProjectTerminalKey({target:input}),true);
 assert.equal(isProjectTerminalKey({target:screen.getByRole('button',{name:'Outside control'})}),false);
});

test('terminal modifier keys do not open app pages, archive threads or change the preview',()=>{
 const calls=[];render(h(Harness,{calls}));
 const input=screen.getByRole('textbox',{name:'Terminal input'});
 for(const modifier of ['ctrlKey','metaKey'])for(const init of [
  {key:'k'},{key:'1'},{key:'2'},{key:'\\',code:'Backslash'},
  {key:'p',code:'KeyP',altKey:true},{key:'A',shiftKey:true},
 ]){
  const event=press(input,{...init,[modifier]:true});
  assert.equal(event.defaultPrevented,false,`${modifier} ${init.key} stays available to the terminal`);
 }
 assert.deepEqual(calls,[]);
 fireEvent.click(screen.getByRole('button',{name:'Terminal action'}));
 assert.deepEqual(calls,['terminal-action'],'explicit terminal controls still work');
});

test('the same shortcuts still work outside the terminal',()=>{
 const calls=[];render(h(Harness,{calls}));
 const outside=screen.getByRole('button',{name:'Outside control'});
 for(const init of [
  {key:'k',metaKey:true},{key:'1',ctrlKey:true},{key:'2',metaKey:true},
  {key:'\\',code:'Backslash',ctrlKey:true},{key:'p',code:'KeyP',altKey:true,metaKey:true},
  {key:'A',ctrlKey:true,shiftKey:true},{key:'Escape'},
 ])assert.equal(press(outside,init).defaultPrevented,true);
 assert.deepEqual(calls,['palette','home','knowledge','column','preview','archive','stop']);
});

test('composition, repeats and previously handled keys never invoke app shortcuts',()=>{
 const calls=[];render(h(Harness,{calls}));
 const outside=screen.getByRole('button',{name:'Outside control'});
 const keys=[{key:'k',metaKey:true},{key:'\\',code:'Backslash',ctrlKey:true},{key:'A',ctrlKey:true,shiftKey:true},{key:'Escape'}];
 for(const init of keys){
  press(outside,{...init,isComposing:true});press(outside,{...init,repeat:true});
  const handled=new window.KeyboardEvent('keydown',{bubbles:true,cancelable:true,...init});handled.preventDefault();outside.dispatchEvent(handled);
 }
 assert.deepEqual(calls,[]);
});

test('Settings keeps terminal Escape but still closes from an outside control',()=>{
 const calls=[];render(h(Harness,{calls,running:false}));
 press(screen.getByRole('textbox',{name:'Terminal input'}),{key:'Escape'});
 assert.deepEqual(calls,[]);
 press(screen.getByRole('button',{name:'Outside control'}),{key:'Escape'});
 assert.deepEqual(calls,['close-settings']);
});
