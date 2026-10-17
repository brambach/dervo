import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
for(const key of ['window','document','HTMLElement','Element','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
let reduced=true;
window.matchMedia=query=>({media:query,get matches(){return query==='(prefers-reduced-motion: reduce)'&&reduced;},addEventListener(){},removeEventListener(){}});
const {renderHook,act,cleanup}=await import('@testing-library/react');
const {useAnchoredScroll}=await import('./useAnchoredScroll.ts');
afterEach(()=>{cleanup();document.body.replaceChildren();reduced=true;});
after(()=>dom.window.close());

function fixture(virtual=false){
 const container=document.createElement('div'),content=document.createElement('div'),row=document.createElement('div');
 row.dataset.messageId='target';content.append(row);container.append(content);document.body.append(container);
 Object.defineProperties(container,{clientHeight:{value:300},scrollHeight:{value:1600}});
 container.getBoundingClientRect=()=>({top:0,bottom:300,height:300});
 row.getBoundingClientRect=()=>({top:60,bottom:100,height:40});
 const calls=[];
 container.scrollTo=options=>calls.push({kind:'container',...options});
 row.scrollIntoView=options=>calls.push({kind:'element',...options});
 const options={channelId:'fixture',isLoading:false,messages:[{id:'target'}],scrollContainerRef:{current:container},contentRef:{current:content},
  ...(virtual?{virtualizerOwnsPrependAnchoring:true,virtualScrollToMessage:(_id,options)=>{calls.push({kind:'virtual-target',...options});return true;},virtualScrollToBottom:behavior=>calls.push({kind:'virtual-bottom',behavior})}:{})};
 const hook=renderHook(()=>useAnchoredScroll(options));
 calls.length=0;
 return {hook,calls};
}

for(const virtual of [false,true])test(`message and bottom jumps respect current reduced motion, virtual=${virtual}`,()=>{
 const {hook,calls}=fixture(virtual);
 // No remount or rerender between changes: each jump must read the current preference.
 for(const [preference,expected] of [[true,'auto'],[false,'smooth'],[true,'auto']]){
  reduced=preference;calls.length=0;
  act(()=>{assert.equal(hook.result.current.scrollToMessage('target',{behavior:'smooth'}),true);hook.result.current.scrollToBottom('smooth');});
  assert.equal(calls.length,2);
  assert.deepEqual(calls.map(call=>call.behavior),[expected,expected]);
 }
});
