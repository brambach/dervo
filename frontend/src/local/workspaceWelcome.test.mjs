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
const {WorkspaceWelcome}=await import('./WorkspaceWelcome.tsx');
after(()=>dom.window.close());
afterEach(cleanup);
const projects=[{id:'p1',name:'dervo app',cwd:'/Users/bryce/dervo/app'},{id:'p2',name:null,cwd:'/Users/bryce/fieldnotes'}];
const base={loading:false,failed:false,projects,onOpen(){},onAdd(){},onRetry(){}};

test('with projects saved and none open, they are listed right here to open, with Add a project',()=>{
 const opened=[];let trigger;
 const view=render(React.createElement(WorkspaceWelcome,{...base,onOpen:id=>opened.push(id),onAdd:t=>trigger=t}));
 assert.ok(view.getByRole('heading',{name:'Pick up where you left off'}));
 const rows=view.getAllByRole('listitem').map(item=>item.textContent);
 assert.deepEqual(rows,['dervo app~/dervo/app','fieldnotes~/fieldnotes']);
 // Design review 23 Sep: no empty glyph slot, so the names start on the heading's edge (the row's first cell is the name).
 for(const row of document.querySelectorAll('.dv-welcome-list .dv-row'))assert.ok(row.firstElementChild.classList.contains('dv-welcome-row'));
 fireEvent.click(view.getByRole('button',{name:/fieldnotes/}));
 assert.deepEqual(opened,['p2']);
 // Arrow keys move between the rows.
 const first=view.getByRole('button',{name:/dervo app/});first.focus();
 fireEvent.keyDown(first,{key:'ArrowDown'});
 assert.equal(document.activeElement.textContent.startsWith('fieldnotes'),true);
 const add=view.getByRole('button',{name:'Add a project'});fireEvent.click(add);
 assert.equal(trigger,add);
 assert.ok(document.querySelector('.dv-moment-mark .dv-pebble'),'a brand moment: the pebble, not the retired yellow smile');
});

test('projects that didn’t load lead with the error and one way to fix it, with no mark above it',()=>{
 let retries=0;
 const view=render(React.createElement(WorkspaceWelcome,{...base,failed:true,detail:'The local store didn’t answer.',onRetry:()=>retries++}));
 assert.ok(view.getByRole('heading',{name:'Your projects didn’t load'}));
 assert.equal(document.querySelector('.dv-moment-mark'),null);
 assert.equal(document.querySelector('details pre').textContent,'The local store didn’t answer.');
 fireEvent.click(view.getByRole('button',{name:'Try again'}));
 assert.equal(retries,1);
});

test('a project’s threads loading is quiet: the one opening screen is the startup one',()=>{
 const view=render(React.createElement(WorkspaceWelcome,{...base,loading:true}));
 assert.equal(view.getByRole('status').textContent,'Opening your project');
 assert.equal(view.queryByRole('heading'),null);
});

test('the two branches nobody could reach are gone, and so is the yellow smile',()=>{
 const source=readFileSync(new URL('./WorkspaceWelcome.tsx',import.meta.url),'utf8');
 for(const gone of ['Start a conversation','Set up your first project','Your project is ready','hasSelectedProject','DervoCharacter'])assert.equal(source.includes(gone),false,gone);
 const brand=readFileSync(new URL('./DervoBrand.tsx',import.meta.url),'utf8');
 assert.equal(brand.includes('DervoCharacter'),false,'the yellow character retires everywhere');
 assert.equal(brand.includes('#edba54'),false);
});
