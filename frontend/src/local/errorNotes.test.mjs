import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html data-dervo-local><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
globalThis.window=dom.window;globalThis.document=dom.window.document;for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
const copied=[];
Object.defineProperty(dom.window.navigator,'clipboard',{configurable:true,value:{writeText:async text=>{copied.push(text);}}});
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {toast,Toaster}=await import('sonner');
const notes=await import('./ErrorNotes.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();notes.forgetNotes();copied.length=0;toast.dismiss();});

test('an error toast has words you can select, Copy and Close, and never swipes away (group 4, 23 Sep)',async()=>{
 notes.installErrorToasts();
 render(React.createElement(Toaster,{}));
 notes.setOpenThread('c1');
 await act(async()=>{toast.error('Paste failed: the clipboard is empty',{description:'Copy the text again, then paste.'});});
 const shown=await waitFor(()=>screen.getByTestId('error-toast'));
 assert.equal(shown.querySelector('.dv-err-text').textContent,'Paste failed: the clipboard is empty');
 assert.equal(shown.querySelector('.dv-err-detail').textContent,'Copy the text again, then paste.');
 assert.equal(shown.closest('[data-sonner-toast]').getAttribute('data-dismissible'),'false','no swipe, so text can be selected');
 await act(async()=>{fireEvent.click([...shown.querySelectorAll('button')].find(b=>b.textContent==='Copy'));});
 await waitFor(()=>assert.deepEqual(copied,['Paste failed: the clipboard is empty\nCopy the text again, then paste.']));
 assert.deepEqual(notes.threadNotes('c1'),[],'not in the thread as well while the toast shows it (8 Oct)');
 await act(async()=>{fireEvent.click([...shown.querySelectorAll('button')].find(b=>b.textContent==='Close'));});
 await waitFor(()=>assert.equal(shown.closest('[data-sonner-toast]')?.getAttribute('data-removed'),'true'),'Close dismisses it');
 assert.deepEqual(notes.threadNotes('c1'),[],'closed means seen, so it isn’t kept');
});

test('an error toast that times out unread moves into the thread it showed in',async()=>{
 const toastCalls=[],custom=toast.custom;
 toast.custom=(make,options)=>{toastCalls.push(options);return custom(make,options);};
 try{
 render(React.createElement(Toaster,{}));
 notes.setOpenThread('c1');
 await act(async()=>{notes.showErrorToast('The host stopped answering.');});
 await waitFor(()=>screen.getByTestId('error-toast'));
 assert.deepEqual(notes.threadNotes('c1'),[]);
 notes.setOpenThread('c2');
 const call=toastCalls.at(-1);
 await act(async()=>{call.onAutoClose();});
 assert.deepEqual(notes.threadNotes('c1').map(note=>note.text),['The host stopped answering.'],'the thread it showed in, not the one open now');
 assert.deepEqual(notes.threadNotes('c2'),[]);
 }finally{toast.custom=custom;}
});

test('outside Dervo’s own app a toast.error is sonner’s own',async()=>{
 notes.installErrorToasts();
 document.documentElement.removeAttribute('data-dervo-local');
 try{
  render(React.createElement(Toaster,{}));
  await act(async()=>{toast.error('Plain error');});
  await waitFor(()=>assert.ok(screen.getByText('Plain error')));
  assert.equal(screen.queryByTestId('error-toast'),null);
  assert.deepEqual(notes.threadNotes(null),[]);
 }finally{document.documentElement.setAttribute('data-dervo-local','');}
});

test('a thread keeps its own errors, once each, with Copy and Dismiss',async()=>{
 notes.noteError(new Error('The host stopped answering.'),{channelId:'c1',now:1000});
 notes.noteError('The host stopped answering.',{channelId:'c1',now:20_000});
 notes.noteError('Another thread’s error',{channelId:'c2',now:21_000});
 notes.noteError('Shown in the alert line already',{channelId:'c1',now:22_000});
 assert.deepEqual(notes.threadNotes('c1').map(note=>[note.text,note.at]),[['The host stopped answering.',20_000],['Shown in the alert line already',22_000]],'the same words within a minute are one');
 render(React.createElement(notes.ThreadErrors,{channelId:'c1',except:'Shown in the alert line already'}));
 const rows=[...document.querySelectorAll('.dv-thread-error')];
 assert.deepEqual(rows.map(row=>row.querySelector('.dv-err-text').textContent),['The host stopped answering.'],'never twice on screen');
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Copy: The host stopped answering.'}));});
 await waitFor(()=>assert.deepEqual(copied,['The host stopped answering.']));
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Dismiss: The host stopped answering.'}));});
 assert.equal(document.querySelectorAll('.dv-thread-error').length,0);
 assert.deepEqual(notes.threadNotes('c2').map(note=>note.text),['Another thread’s error']);
});

test('a kept error is ink on a sunk line, never rust, which only means "needs you" (design report #2)',async()=>{
 const {readFileSync}=await import('node:fs');
 const css=readFileSync(new URL('./ErrorNotes.css',import.meta.url),'utf8');
 assert.match(css,/\.dv-thread-error \{[^}]*background: var\(--dv-sunk\);/);
 assert.doesNotMatch(css,/var\(--dv-rust/);
});

test('only the last twenty errors are kept',()=>{
 for(let n=0;n<25;n++)notes.noteError(`Error ${n}`,{channelId:'c1',now:n*100_000});
 assert.equal(notes.threadNotes('c1').length,notes.NOTES_KEPT);
 assert.equal(notes.threadNotes('c1')[0].text,'Error 5');
});
