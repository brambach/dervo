import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event','KeyboardEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,cleanup}=await import('@testing-library/react');
const {Welcome,welcomeSeen,rememberWelcomeSeen}=await import('./Welcome.tsx');
const {MICROTASK_HEADLINE,MICROTASK_WELCOME}=await import('./Microtasks.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();localStorage.clear();});

test('one idea, one action: the tiny-task line, one sentence under it, and a single button that continues',()=>{
 let went=0;
 const view=render(React.createElement(Welcome,{onContinue:()=>went++}));
 assert.ok(view.getByRole('heading',{level:1,name:'Keep Dervo open beside you and fire off tiny tasks.'}));
 assert.equal(view.getAllByRole('button').length,1,'exactly one thing to press');
 assert.ok(document.querySelector('.dv-moment-mark.dv-enter .dv-pebble'),'the pebble settles in once');
 assert.equal(document.querySelector('.dv-btn-accent'),null,'nothing here needs you, so no accent');
 fireEvent.click(view.getByRole('button',{name:'Get started'}));
 assert.equal(went,1);
});

test('nobody is made to read it: Enter and Escape both continue, and shortcuts with a modifier are left alone',()=>{
 let went=0;
 render(React.createElement(Welcome,{onContinue:()=>went++}));
 fireEvent.keyDown(window,{key:'Escape'});
 assert.equal(went,1);
 fireEvent.keyDown(window,{key:'Enter'});
 assert.equal(went,2);
 fireEvent.keyDown(window,{key:'Enter',metaKey:true});
 assert.equal(went,2);
});

test('seen once is remembered on this Mac, beside the app’s other per-Mac memory',()=>{
 assert.equal(welcomeSeen(),false);
 rememberWelcomeSeen();
 assert.equal(welcomeSeen(),true);
 assert.equal(localStorage.getItem('dervo-welcomed'),'1');
});

test('the app wires it: the welcome comes before first run and holds the usage notice back while it shows',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/if\(firstRunMode==='first'&&!welcomed\)return <Welcome onContinue=\{\(\)=>\{rememberWelcomeSeen\(\);setWelcomed\(true\);\}\}\/>;\s*if\(firstRunMode==='first'\)return firstRun;/);
 assert.match(shell,/useUsageNotice\(\(\)=>settings\.go\('preferences'\),firstRunMode==='first'&&!welcomed\);/);
});

test('the copy keeps house style: no em dashes, nothing claiming first-to-market',()=>{
 const view=render(React.createElement(Welcome,{onContinue(){}}));
 const text=view.container.textContent;
 assert.equal(text.includes('—'),false);
 assert.doesNotMatch(text,/\b(first|only) (app|tool)\b/i);
});

test('the welcome sells the microtask pattern: the headline is the pattern, one sentence under it says what happens, and it stays one idea with one action',()=>{
 const view=render(React.createElement(Welcome,{onContinue(){}}));
 const lede=view.container.querySelector('.dv-lede');
 assert.equal(view.getByRole('heading',{level:1}).textContent,MICROTASK_HEADLINE);
 assert.equal(lede.textContent,MICROTASK_WELCOME);
 assert.match(lede.textContent,/^A failing test, a rename, a question about the code\./);
 assert.equal(view.container.querySelectorAll('p').length,1,'one sentence under the line, not a list of features');
 assert.equal(view.getAllByRole('button').length,1,'exactly one thing to press');
});
