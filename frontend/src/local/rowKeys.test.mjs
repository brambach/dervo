import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','Event','KeyboardEvent','MutationObserver'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,cleanup,fireEvent}=await import('@testing-library/react');
const {rowKeyDelta,rowTargets,onRowKeys}=await import('./rowKeys.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();document.body.innerHTML='';});

const h=React.createElement;
const keys=(key,extra={})=>({key,metaKey:false,ctrlKey:false,altKey:false,defaultPrevented:false,...extra});
// Rows shaped like the full Catch up: a lifted row whose first button is its action, plain rows that are one button, a
// row whose only button is disabled, and a box you type in.
function screen({menu=false}={}){
 return render(h('section',{onKeyDown:onRowKeys,'aria-label':'Catch up'},
  h('h1',{tabIndex:-1},'Catch up'),
  h('div',{role:'list'},
   h('article',{role:'listitem'},h('div',null,'Ideas storage'),h('button',null,'Answer'),h('button',null,'Dismiss')),
   h('div',{role:'listitem'},h('button',null,'Search index'))),
  h('div',{role:'list'},
   h('div',{role:'listitem'},h('button',{disabled:true},'Busy')),
   h('div',{role:'listitem'},h('button',null,'Live board')),
   h('input',{'aria-label':'Filter'})),
  menu?h('div',{role:'menu'},'Approve'):null));
}
const focused=()=>document.activeElement?.textContent;

test('Down and j go on, Up and k go back, and a modifier or a composing key does nothing',()=>{
 assert.equal(rowKeyDelta(keys('ArrowDown')),1);
 assert.equal(rowKeyDelta(keys('j')),1);
 assert.equal(rowKeyDelta(keys('ArrowUp')),-1);
 assert.equal(rowKeyDelta(keys('k')),-1);
 for(const extra of [{metaKey:true},{ctrlKey:true},{altKey:true},{defaultPrevented:true},{isComposing:true}])assert.equal(rowKeyDelta(keys('ArrowDown',extra)),0,JSON.stringify(extra));
 assert.equal(rowKeyDelta(keys('Enter')),0);
});

test('each row offers its first enabled button, and a row without one is skipped',()=>{
 screen();
 assert.deepEqual(rowTargets(document).map(button=>button.textContent),['Answer','Search index','Live board']);
});

test('from the heading Down goes to the first row, then row to row, wrapping at both ends',()=>{
 screen();
 document.querySelector('h1').focus();
 fireEvent.keyDown(document.activeElement,{key:'ArrowDown'});assert.equal(focused(),'Answer');
 fireEvent.keyDown(document.activeElement,{key:'ArrowDown'});assert.equal(focused(),'Search index');
 fireEvent.keyDown(document.activeElement,{key:'j'});assert.equal(focused(),'Live board','the disabled row is passed over');
 fireEvent.keyDown(document.activeElement,{key:'ArrowDown'});assert.equal(focused(),'Answer','past the last row is the first');
 fireEvent.keyDown(document.activeElement,{key:'k'});assert.equal(focused(),'Live board','before the first row is the last');
 document.querySelector('h1').focus();
 fireEvent.keyDown(document.activeElement,{key:'ArrowUp'});assert.equal(focused(),'Live board','Up from outside the rows is the last');
});

test('from a second button in a row, Down still goes to the next row',()=>{
 screen();
 document.querySelector('article button:nth-of-type(2)').focus();
 assert.equal(focused(),'Dismiss');
 assert.equal(fireEvent.keyDown(document.activeElement,{key:'ArrowDown'}),false,'the key was taken');
 assert.equal(focused(),'Search index');
});

test('a box you type in, an open menu and a modifier keep their keys',()=>{
 screen();
 const box=document.querySelector('input');box.focus();
 assert.equal(fireEvent.keyDown(box,{key:'ArrowDown'}),true);assert.equal(document.activeElement,box);
 assert.equal(fireEvent.keyDown(box,{key:'j'}),true);assert.equal(document.activeElement,box);
 const answer=[...document.querySelectorAll('button')].find(button=>button.textContent==='Answer');answer.focus();
 assert.equal(fireEvent.keyDown(answer,{key:'ArrowDown',metaKey:true}),true);assert.equal(focused(),'Answer');
 cleanup();
 screen({menu:true});
 const first=[...document.querySelectorAll('button')].find(button=>button.textContent==='Answer');first.focus();
 assert.equal(fireEvent.keyDown(first,{key:'ArrowDown'}),true,'the menu has the arrows');assert.equal(focused(),'Answer');
});
