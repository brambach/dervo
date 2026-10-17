// 16.4 item 5: "Drop files to upload" stayed up after a drop. Dervo's composer wrapper (LocalImageComposer) takes the
// drop in its capture handler and stops it, so the form's onDrop and a bubbling window listener never ran.
import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,act,cleanup}=await import('@testing-library/react');
const {useMediaUpload}=await import('./useMediaUpload.ts');
afterEach(()=>cleanup());
after(()=>dom.window.close());

function fileEvent(type,files=[]){
  const event=new dom.window.Event(type,{bubbles:true,cancelable:true});
  Object.defineProperty(event,'dataTransfer',{value:{types:['Files'],files,items:[]}});
  return event;
}
/** The composer's form with the hook's handlers, inside a wrapper that handles drops itself the way
 * LocalImageComposer.tsx does (preventDefault and stopPropagation in onDropCapture, then its own upload). */
function Composer({state,wrapped,onWrapperDrop}){
  const media=useMediaUpload();
  state.current=media;
  const form=React.createElement('form',{'data-testid':'form',onDragEnter:media.handleDragEnter,onDragLeave:media.handleDragLeave,
    onDragOver:media.handleDragOver,onDrop:media.handleDrop},media.isDragOver?React.createElement('p',{'data-testid':'overlay'},'Drop files to upload'):null);
  if(!wrapped)return form;
  return React.createElement('div',{
    onDragOverCapture:event=>{if(event.dataTransfer.types.includes('Files')){event.preventDefault();event.stopPropagation();}},
    onDropCapture:event=>{const files=Array.from(event.dataTransfer.files);if(files.length){event.preventDefault();event.stopPropagation();onWrapperDrop(files);}},
  },form);
}
function mount(wrapped){
  const state={current:null},drops=[];
  const view=render(React.createElement(Composer,{state,wrapped,onWrapperDrop:files=>drops.push(files)}));
  return {...view,state,drops,form:view.getByTestId('form')};
}
const file=new dom.window.File(['x'],'shot.png',{type:'image/png'});

test('a drop the wrapper takes clears the overlay, as on a thread or Home',async()=>{
  const view=mount(true);
  await act(async()=>{view.form.dispatchEvent(fileEvent('dragenter'));});
  assert.equal(!!view.queryByTestId('overlay'),true,'the overlay shows while dragging');
  await act(async()=>{view.form.dispatchEvent(fileEvent('drop',[file]));});
  assert.equal(view.drops.length,1,'the wrapper still uploads the file once');
  assert.equal(!!view.queryByTestId('overlay'),false,'and the overlay is gone');
  assert.equal(view.state.current.isDragOver,false);
});
test('nested enters are all cleared by one drop, and a later drag shows the overlay again',async()=>{
  const view=mount(true);
  await act(async()=>{view.form.dispatchEvent(fileEvent('dragenter'));view.form.dispatchEvent(fileEvent('dragenter'));});
  await act(async()=>{view.form.dispatchEvent(fileEvent('drop',[file]));});
  assert.equal(!!view.queryByTestId('overlay'),false);
  await act(async()=>{view.form.dispatchEvent(fileEvent('dragenter'));});
  assert.equal(!!view.queryByTestId('overlay'),true,'the depth count started again from zero');
  await act(async()=>{view.form.dispatchEvent(fileEvent('dragleave'));});
  assert.equal(!!view.queryByTestId('overlay'),false);
});
test('a drag cancelled anywhere, even where a handler stops it, clears the overlay',async()=>{
  const view=mount(true);
  await act(async()=>{view.form.dispatchEvent(fileEvent('dragenter'));});
  const stopper=dom.window.document.createElement('div');dom.window.document.body.append(stopper);
  stopper.addEventListener('dragend',event=>event.stopPropagation());
  await act(async()=>{stopper.dispatchEvent(fileEvent('dragend'));});
  assert.equal(!!view.queryByTestId('overlay'),false);
  stopper.remove();
});
test('without the wrapper the form still takes the drop and clears the overlay',async()=>{
  const view=mount(false);
  await act(async()=>{view.form.dispatchEvent(fileEvent('dragenter'));});
  assert.equal(!!view.queryByTestId('overlay'),true);
  await act(async()=>{view.form.dispatchEvent(fileEvent('drop',[]));});
  assert.equal(!!view.queryByTestId('overlay'),false);
});
test('starting an upload clears a drag state left over from anything else',async()=>{
  // The upload itself goes to the native bridge; here it just never answers.
  window.__TAURI_INTERNALS__={invoke:()=>new Promise(()=>{}),transformCallback:()=>0};
  try{
    const view=mount(true);
    await act(async()=>{view.form.dispatchEvent(fileEvent('dragenter'));});
    assert.equal(!!view.queryByTestId('overlay'),true);
    await act(async()=>{await view.state.current.handlePaste({clipboardData:{items:[{kind:'string',getAsFile:()=>null}]},preventDefault(){}});});
    assert.equal(!!view.queryByTestId('overlay'),true,'pasting text starts no upload and changes nothing');
    await act(async()=>{await view.state.current.handlePaste({clipboardData:{items:[{kind:'file',getAsFile:()=>file}]},preventDefault(){}});});
    assert.equal(!!view.queryByTestId('overlay'),false,'a pasted file starts an upload and clears the overlay');
  }finally{delete window.__TAURI_INTERNALS__;}
});
