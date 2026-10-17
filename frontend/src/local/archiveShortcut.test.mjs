import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup}=await import('@testing-library/react');
const {useArchiveShortcut}=await import('./useArchiveShortcut.ts');
afterEach(cleanup);after(()=>dom.window.close());
function Harness({enabled=true,onArchive}){useArchiveShortcut(enabled,onArchive);return React.createElement('input');}
test('archive chord fires once and ignores plain letters, repeats and composition',()=>{
 let count=0;render(React.createElement(Harness,{onArchive:()=>count++}));
 fireEvent.keyDown(window,{key:'A',metaKey:true,shiftKey:true});assert.equal(count,1);
 for(const props of [{key:'a'},{key:'a',metaKey:true},{key:'a',metaKey:true,shiftKey:true,repeat:true},{key:'a',metaKey:true,shiftKey:true,isComposing:true}])fireEvent.keyDown(window,props);
 assert.equal(count,1);
});
test('disabled and modal surfaces prevent archiving',()=>{
 let count=0;const view=render(React.createElement(Harness,{enabled:false,onArchive:()=>count++}));
 fireEvent.keyDown(window,{key:'A',metaKey:true,shiftKey:true});assert.equal(count,0);
 view.rerender(React.createElement(Harness,{onArchive:()=>count++}));
 for(const role of ['dialog','alertdialog','menu']){
  const blocker=document.createElement('div');blocker.setAttribute('role',role);document.body.append(blocker);
  fireEvent.keyDown(window,{key:'A',metaKey:true,shiftKey:true});assert.equal(count,0);blocker.remove();
 }
 fireEvent.keyDown(window,{key:'A',ctrlKey:true,shiftKey:true});assert.equal(count,1);
});
