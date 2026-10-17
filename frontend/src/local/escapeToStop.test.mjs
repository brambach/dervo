import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','Event','KeyboardEvent','MutationObserver'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,cleanup,act}=await import('@testing-library/react');
const {escapeStops,overlayOpen,stoppedLine,useEscapeToStop}=await import('./escapeToStop.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();document.body.innerHTML='';});

const idle={running:true,inRoom:false,paletteOpen:false,panelOpen:false,replying:false,overlayOpen:false};

test('Esc stops only a running task in a conversation with nothing open',()=>{
 assert.equal(escapeStops(idle),true);
 for(const [open,why] of [['paletteOpen','the palette closes first'],['panelOpen','a side panel closes first'],['replying','a reply is cancelled first'],['overlayOpen','a menu or dialog closes first']])
  assert.equal(escapeStops({...idle,[open]:true}),false,why);
 assert.equal(escapeStops({...idle,running:false}),false,'nothing running, nothing to stop');
 assert.equal(escapeStops({...idle,inRoom:true}),false,'never in the project room');
});

test('an open menu, dialog or list counts, a closed or hidden one does not',()=>{
 assert.equal(overlayOpen(),false);
 document.body.innerHTML='<div role="menu" data-state="open"></div>';
 assert.equal(overlayOpen(),true);
 document.body.innerHTML='<div role="dialog" data-state="closed"></div><div role="listbox" hidden></div><div role="menu" aria-hidden="true"></div>';
 assert.equal(overlayOpen(),false);
 document.body.innerHTML='<div data-radix-popper-content-wrapper><div></div></div>';
 assert.equal(overlayOpen(),true,'a popover counts');
});

test('the stopped line promises carrying on only when the next message resumes the session',()=>{
 assert.equal(stoppedLine('continues'),'Paused. Send a message or press Resume to carry on.');
 assert.equal(stoppedLine('fresh'),'Paused. Your next message starts a new session with a summary of this chat.');
 assert.equal(stoppedLine(undefined),'Paused. Your next message starts a new session with a summary of this chat.','unknown is not a promise');
});

test('pressing Esc stops through the same stop, once, and does nothing while a menu is open',async()=>{
 const stops=[];let state={running:true,inRoom:false,paletteOpen:false,panelOpen:false,replying:false};
 function Probe(){useEscapeToStop(()=>({...state,stop:()=>stops.push('stop')}));return null;}
 render(React.createElement(Probe));
 const press=(init={})=>{const event=new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true,...init});window.dispatchEvent(event);return event;};
 const event=press();
 assert.deepEqual(stops,['stop']);
 assert.equal(event.defaultPrevented,true);
 press({repeat:true});
 assert.deepEqual(stops,['stop'],'holding Esc is one stop, not many');
 document.body.innerHTML='<div role="menu" data-state="open"></div>';
 const menu=press();
 assert.deepEqual(stops,['stop'],'the open menu takes Esc');
 assert.equal(menu.defaultPrevented,false,'and it still gets to close');
 document.body.innerHTML='';
 state={...state,running:false};
 press();
 assert.deepEqual(stops,['stop'],'nothing running');
 const other=new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true});window.dispatchEvent(other);
 assert.deepEqual(stops,['stop']);
});

test('Escape in a worker question preserves the answer and never stops its running agent',()=>{
 const stops=[];
 function Probe(){
  useEscapeToStop(()=>({...idle,stop:()=>stops.push('stop')}));
  return React.createElement('section',{'data-worker-questions':''},
   React.createElement('textarea',{'aria-label':'Your answer',defaultValue:'Keep this draft'}),
   React.createElement('button',{type:'button'},'Skip question'));
 }
 const view=render(React.createElement(Probe));
 for(const target of [view.getByRole('textbox'),view.getByRole('button')]){
  const event=new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});
  target.dispatchEvent(event);
  assert.equal(event.defaultPrevented,false);
 }
 assert.deepEqual(stops,[]);
 assert.equal(view.getByRole('textbox').value,'Keep this draft');
 window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
 assert.deepEqual(stops,['stop'],'Escape outside the question keeps the existing stop shortcut');
});
