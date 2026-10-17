import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('',{pretendToBeVisual:true});
globalThis.window=dom.window;globalThis.document=dom.window.document;
const {watchConversationForeground,watchConversationPresence}=await import('./conversationForeground.ts');
after(()=>dom.window.close());
const settle=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};

test('native focus wins over stale visibility and a late initial focus query',async()=>{
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 let callback,resolveFocus,released=0;const observed=[];
 const stop=watchConversationForeground(value=>observed.push(value),()=>({
  onFocusChanged:async fn=>{callback=fn;return ()=>released++;},
  isFocused:()=>new Promise(resolve=>{resolveFocus=resolve;}),
 }));
 await settle();callback({payload:true});
 document.dispatchEvent(new dom.window.Event('visibilitychange'));
 assert.deepEqual(observed,[true]);
 resolveFocus(false);await settle();assert.deepEqual(observed,[true],'late initial observation cannot overwrite native focus');
 callback({payload:false});assert.deepEqual(observed,[true,false]);
 stop();callback({payload:true});window.dispatchEvent(new dom.window.Event('focus'));
 assert.equal(released,1);assert.deepEqual(observed,[true,false]);
});

test('listener registration completing after disposal releases the native listener',async()=>{
 let resolveListen,released=0,queries=0;const observed=[];
 const stop=watchConversationForeground(value=>observed.push(value),()=>({
  onFocusChanged:()=>new Promise(resolve=>{resolveListen=resolve;}),
  isFocused:async()=>{queries++;return true;},
 }));
 stop();resolveListen(()=>released++);await settle();
 assert.equal(released,1);assert.equal(queries,0);assert.deepEqual(observed,[]);
});

test('browser fallback supports focus and visibility and stops after cleanup',async()=>{
 const observed=[];
 const stop=watchConversationForeground(value=>observed.push(value),()=>{throw Error('No native window in browser preview');});
 window.dispatchEvent(new dom.window.Event('focus'));
 window.dispatchEvent(new dom.window.Event('blur'));
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 document.dispatchEvent(new dom.window.Event('visibilitychange'));
 assert.deepEqual(observed,[true,false,true]);
 stop();window.dispatchEvent(new dom.window.Event('focus'));assert.deepEqual(observed,[true,false,true]);
});

test('presence: blur with the document still visible is shown, not hidden, and changes and every focus are reported',async()=>{
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 const observed=[];
 const stop=watchConversationPresence(value=>observed.push(value),()=>{throw Error('No native window in browser preview');});
 window.dispatchEvent(new dom.window.Event('focus'));
 window.dispatchEvent(new dom.window.Event('blur'));
 window.dispatchEvent(new dom.window.Event('blur'));
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 document.dispatchEvent(new dom.window.Event('visibilitychange'));
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 document.dispatchEvent(new dom.window.Event('visibilitychange'));
 window.dispatchEvent(new dom.window.Event('blur'));
 window.dispatchEvent(new dom.window.Event('focus'));
 assert.deepEqual(observed,['focused','shown','hidden','focused','shown','focused'],'the second blur changes nothing, so it isn\'t reported');
 stop();window.dispatchEvent(new dom.window.Event('blur'));assert.deepEqual(observed,['focused','shown','hidden','focused','shown','focused']);
});

test('presence: native blur splits on visibility, and native focus wins over a stuck hidden document',async()=>{
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 let callback;const observed=[];
 const stop=watchConversationPresence(value=>observed.push(value),()=>({onFocusChanged:async fn=>{callback=fn;return ()=>{};},isFocused:async()=>true}));
 await settle();
 callback({payload:false});
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 document.dispatchEvent(new dom.window.Event('visibilitychange'));
 callback({payload:true});
 assert.deepEqual(observed,['focused','shown','hidden','focused']);
 stop();
});
