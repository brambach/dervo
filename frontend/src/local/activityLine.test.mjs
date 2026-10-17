import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Node','Event','MouseEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {readFileSync}=await import('node:fs');
const {render,fireEvent,cleanup,screen}=await import('@testing-library/react');
const {ActivityLine,ActivityLines,foldLines,ACTIVITY_SHOWN}=await import('./ActivityLine.tsx');
after(()=>dom.window.close());afterEach(cleanup);

test('one line, at most one action, or the word it left behind',()=>{
 let undone=0;
 render(React.createElement(ActivityLine,{text:'Moved Connector picker to Connectors.',action:{label:'Undo',describe:'Undo: Moved Connector picker',onClick:()=>undone++}}));
 fireEvent.click(screen.getByRole('button',{name:'Undo: Moved Connector picker'}));
 assert.equal(undone,1);
 cleanup();
 render(React.createElement(ActivityLine,{text:'Renamed Shipping to Releases.',done:'Undone',action:null}));
 assert.equal(screen.queryByRole('button'),null,'a spent action is a word, not a button');
 assert.equal(document.querySelector('.dv-activity-done').textContent,'Undone');
 assert.equal(document.querySelector('.dv-activity').getAttribute('role'),null,'only a live line is announced');
});

test('more than four lines fold to three and a Show, never a feed',()=>{
 assert.equal(ACTIVITY_SHOWN,3);
 assert.deepEqual(foldLines([1,2,3,4],false),{shown:[1,2,3,4],hidden:0},'folding one line saves nothing');
 assert.deepEqual(foldLines([1,2,3,4,5,6],false),{shown:[1,2,3],hidden:3});
 assert.deepEqual(foldLines([1,2,3,4,5,6],true),{shown:[1,2,3,4,5,6],hidden:0});
 const items=Array.from({length:12},(_,i)=>({key:String(i),text:`Filed thread ${i} under Connectors.`}));
 render(React.createElement(ActivityLines,{items,more:'more changes'}));
 assert.equal(document.querySelectorAll('.dv-activity').length,4);
 assert.equal(screen.getByText('9 more changes').tagName,'SPAN');
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 assert.equal(document.querySelectorAll('.dv-activity').length,12);
 cleanup();
 render(React.createElement(ActivityLines,{items:[]}));
 assert.equal(document.querySelector('.dv-activity-list'),null,'nothing to say, nothing drawn');
});

test('the style is the muted token, no bars, no literal colours, and it enters once',()=>{
 const css=readFileSync(new URL('./ActivityLine.css',import.meta.url),'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
 assert.match(css,/\.dv-activity \{[^}]*color: var\(--dv-muted\);/);
 assert.doesNotMatch(css,/--dv-faint|#[0-9a-f]{3,6}\b|border-left|infinite/i);
 assert.match(css,/prefers-reduced-motion: no-preference/);
});
