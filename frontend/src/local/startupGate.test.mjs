import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event','FocusEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
const React=await import('react');
const {render,fireEvent,cleanup,act,waitFor}=await import('@testing-library/react');
const {StartupGate}=await import('./StartupGate.tsx');
const {openingClock,OPENING_DELAY}=await import('./BrandMoment.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();openingClock.now=()=>performance.now();});
const ready={ready:true,kind:'ready',detail:''};
const broken={ready:false,kind:'runtime',detail:'Dervo’s own Python at /Applications/Dervo Local.app/Contents/Resources/runtime/python/bin/python3.13 does not exist. This copy of Dervo is incomplete: download it again and replace it.'};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function app(){return render(React.createElement(StartupGate,null,React.createElement('p',null,'Workspace mounted')));}

test('a broken copy of Dervo’s own Python is a failed start with its reason, never an install page',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async command=>{calls.push(command);return broken;}};
 const view=app();
 await act(async()=>{});
 assert.ok(view.getByRole('heading',{name:'Dervo couldn’t open your workspace'}));
 assert.ok(view.getByText(/missing part of itself\. Download Dervo again and replace this copy\./));
 assert.ok(document.querySelector('details[open] pre').textContent.includes('runtime/python/bin/python3.13'));
 assert.equal(view.queryByText(/Python 3\.13 download|Dervo needs Python/),null,'testers are never sent to python.org');
 assert.equal(view.queryByText('Workspace mounted'),null);
 // No focus re-check: that was for coming back from an installer.
 await act(async()=>{window.dispatchEvent(new window.FocusEvent('focus'));});
 assert.deepEqual(calls,['startup_status']);
});

test('a ready startup mounts once, with nothing drawn over it',async()=>{
 const calls=[];let mounted=0;
 window.__TAURI_INTERNALS__={invoke:async command=>{calls.push(command);return ready;}};
 function Child(){React.useEffect(()=>{mounted++;},[]);return React.createElement('p',null,'Project');}
 const view=render(React.createElement(StartupGate,null,React.createElement(Child)));
 await act(async()=>{});
 assert.ok(view.getByText('Project'));assert.equal(mounted,1);assert.deepEqual(calls,['startup_status']);
 assert.equal(document.querySelector('[data-testid="dv-opening"]'),null);
});

test('the opening screen waits 600ms: blank paper first, then the mark and one caption',async()=>{
 const start=performance.now();
 openingClock.now=()=>performance.now()-start;
 window.__TAURI_INTERNALS__={invoke:()=>new Promise(()=>{})};
 app();
 await act(async()=>{await sleep(OPENING_DELAY-250);});
 assert.equal(!!document.querySelector('[data-testid="dv-opening"]'),false,'a quick start never flashes a loading screen');
 await act(async()=>{await sleep(400);});
 const opening=document.querySelector('[data-testid="dv-opening"]');
 assert.ok(opening,'a slow start shows one opening screen');
 assert.equal(opening.querySelector('[role="status"]').textContent,'Opening your workspace');
 assert.ok(opening.querySelector('.dv-attending .dv-pebble'),'the pebble rocks while the work is real');
});

test('switching to a new version is an opening, never the failure page, and the gate opens when it finishes',async()=>{
 openingClock.now=()=>OPENING_DELAY;
 const calls=[];let status={ready:false,kind:'switching',detail:'Backing up your workspace and opening the new version.'};
 window.__TAURI_INTERNALS__={invoke:async command=>{calls.push(command);return status;}};
 const view=app();
 await act(async()=>{});
 assert.equal(document.querySelector('[role="status"]')?.textContent,'Opening the new version');
 assert.equal(!!view.queryByText('Dervo couldn’t open your workspace'),false,'switching is not a failure');
 assert.equal(!!view.queryByRole('button',{name:'Try again'}),false,'nothing to press while it switches');
 status=ready;
 await waitFor(()=>assert.ok(view.getByText('Workspace mounted')),{timeout:2500});
 assert.ok(calls.filter(c=>c==='startup_status').length>=2,'it asks again rather than giving up');
});

test('a failed startup leads with the error, copies the details and tries again',async()=>{
 const calls=[],copied=[];
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{copied.push(text);}}});
 window.__TAURI_INTERNALS__={invoke:async command=>{calls.push(command);if(command==='startup_status')return {ready:false,kind:'host',detail:'Permission denied (os error 13)'};throw Error('Retry unavailable');}};
 const view=app();
 await act(async()=>{});
 assert.ok(view.getByRole('heading',{name:'Dervo couldn’t open your workspace'}));
 assert.equal(document.querySelector('.dv-moment-mark'),null,'no mark above an error');
 assert.equal(view.queryByRole('button',{name:'Open Python 3.13 download'}),null);
 assert.ok(document.querySelector('details[open] pre').textContent.includes('Permission denied'));
 // UX review, 23 Sep, M3: someone to write to, next to Copy details, when the app that holds Send feedback didn't start.
 const actions=view.getByRole('button',{name:'Copy details'}).parentElement;
 assert.match(actions.textContent,/Email bryce@trydervo\.com/);
 assert.match(view.getByText(/if it happens twice/).textContent,/email the details below to Bryce\./);
 await act(async()=>fireEvent.click(view.getByRole('button',{name:'Copy details'})));
 // Copy reads the app version through a dynamic import first, so the write lands a moment after the click.
 await waitFor(()=>assert.match(copied[0]??'',/Dervo couldn’t open your workspace \(host\)\.[^]*Permission denied/));
 await waitFor(()=>assert.ok(view.getByRole('button',{name:'Copied'})));
 await act(async()=>fireEvent.click(view.getByRole('button',{name:'Try again'})));
 assert.match(document.querySelector('details pre').textContent,/Retry unavailable\nChecked at/,'a new failure replaces the details, with the time');
 assert.equal(view.queryByText('Workspace mounted'),null);
 assert.deepEqual(calls.filter(c=>c.startsWith('startup')),['startup_status','startup_retry']);
});

test('a locked workspace names the existing Dervo copy and keeps retry available',async()=>{
 window.__TAURI_INTERNALS__={invoke:async()=>({ready:false,kind:'already-open',detail:'Another Dervo host owns this database'})};
 const view=app();await act(async()=>{});
 assert.ok(view.getByRole('heading',{name:'Dervo is already open'}));
 assert.equal(view.queryByText(/bryce@trydervo\.com/),null,'nothing is broken here, so no one to email');
 assert.ok(view.getByText(/quit the other copy, then try again here/));
 assert.ok(view.getByRole('button',{name:'Try again'}));assert.equal(view.queryByText('Workspace mounted'),null);
});
