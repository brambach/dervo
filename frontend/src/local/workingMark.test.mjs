import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','SVGElement','Element','Node','Event','MutationObserver'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),16);globalThis.cancelAnimationFrame=clearTimeout;
let reduced=false;
window.matchMedia=()=>({matches:reduced,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,cleanup,act}=await import('@testing-library/react');
const {WorkingMark,mouthPath,motionOf,SMILE,THINK,MORPH_MS}=await import('./WorkingMark.tsx');
const {DervoActivity}=await import('./DervoActivity.tsx');
const {readFileSync}=await import('node:fs');
afterEach(()=>{cleanup();reduced=false;});
after(()=>dom.window.close());
const wait=ms=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
const mark=view=>view.container.querySelector('[data-testid="working-mark"]');
const mouth=view=>mark(view).querySelector('.dv-work-mouth').getAttribute('d');

test('the mouth morphs between the flat line and the smile, on the mockup geometry (card C)',()=>{
 assert.equal(mouthPath(0),THINK);
 assert.equal(mouthPath(1),SMILE);
 assert.equal(mouthPath(.5),'M35 58.5Q48.5 71 63 57');
 assert.equal(mouthPath(-1),THINK,'clamped');
 assert.equal(mouthPath(2),SMILE,'clamped');
});

test('working: the flat line, eyes up; then the smile and one hop, once, only on the change inside a mount',async()=>{
 const view=render(React.createElement(WorkingMark,{working:true}));
 assert.equal(mark(view).dataset.working,'true');
 assert.equal(mark(view).dataset.done,undefined,'no hop while working');
 assert.equal(mouth(view),THINK);
 view.rerender(React.createElement(WorkingMark,{working:false}));
 assert.equal(mark(view).dataset.working,undefined);
 assert.equal(mark(view).dataset.done,'true','the hop plays when the work ends');
 await wait(MORPH_MS+120);
 assert.equal(mouth(view),SMILE,'and the mouth has become the smile');
 view.rerender(React.createElement(WorkingMark,{working:true}));
 assert.equal(mark(view).dataset.done,undefined,'working again clears the hop');
 // Negative control: a mark that mounts already done sits still, no hop.
 const still=render(React.createElement(WorkingMark,{working:false}));
 assert.equal(mark(still).dataset.done,undefined);
 assert.equal(mouth(still),SMILE);
});

test('reduced motion: states change at once, no hop and no morph',async()=>{
 reduced=true;
 const view=render(React.createElement(WorkingMark,{working:true}));
 assert.equal(mouth(view),THINK);
 view.rerender(React.createElement(WorkingMark,{working:false}));
 assert.equal(mark(view).dataset.done,undefined,'no hop');
 assert.equal(mouth(view),SMILE,'the smile at once');
 const css=readFileSync(new URL('./ChatMotion.css',import.meta.url),'utf8');
 const moving=css.slice(css.indexOf('prefers-reduced-motion: no-preference'));
 assert.ok(moving.includes('dv-work-think')&&moving.includes('dv-work-hop'),'drift and hop are inside the no-preference media block');
 assert.ok(!/^[^@]*dv-work-think[^@]*infinite/m.test(css.slice(0,css.indexOf('@media'))),'no drift outside it');
});

test('the status row shows the working smile where the moss spinner was, only while active',()=>{
 const active=render(React.createElement(DervoActivity,{label:'Claude Code is working',active:true}));
 assert.ok(active.container.querySelector('[data-testid="working-mark"][data-working]'));
 assert.equal(active.container.querySelector('[data-testid="moss"]'),null);
 const idle=render(React.createElement(DervoActivity,{label:'Idle',active:false}));
 assert.equal(idle.container.querySelector('[data-testid="working-mark"]'),null,'no smile when nothing runs');
 const done=render(React.createElement(DervoActivity,{label:'Finished',active:false,tone:'completed'}));
 assert.equal(done.container.querySelector('[data-testid="working-mark"]'),null,'a finished row keeps its check, not the smile');
});

test('the pebble moves to match the recorded step, and never claims more than its words (24 Sep)',()=>{
 for(const [step,motion] of [[null,'think'],['Thinking','think'],['Working','think'],['Reading Home.css','read'],['Listing files','read'],['Fetching trydervo.com','read'],
  ['Searching project files','search'],['Searching the web for orbs','search'],['Finding files','search'],['Looking through earlier chats','search'],
  ['Editing Home.css','write'],['Writing notes.md','write'],['Creating JobLive.tsx','write'],['Running npm test','run'],['Running the check','run'],
  ['Waiting for a slot','wait'],['Connecting connectors','wait'],['Asking a helper','think'],['Independent review in progress','think']])
  assert.equal(motionOf(step),motion,String(step));
 const {container,rerender}=render(React.createElement(WorkingMark,{working:true,step:'Reading Home.css'}));
 assert.equal(container.querySelector('svg').dataset.motion,'read');
 rerender(React.createElement(WorkingMark,{working:false,step:'Reading Home.css'}));
 assert.equal(container.querySelector('svg').hasAttribute('data-motion'),false,'a finished mark only smiles');
 const css=readFileSync(new URL('./ChatMotion.css',import.meta.url),'utf8');
 for(const motion of ['read','search','write','run','wait'])assert.match(css,new RegExp(`data-motion="${motion}"`),motion);
});
