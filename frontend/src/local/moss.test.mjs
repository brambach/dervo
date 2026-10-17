import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,cleanup}=await import('@testing-library/react');
const {Moss}=await import('./Moss.tsx');
const {DervoActivity}=await import('./DervoActivity.tsx');
afterEach(cleanup);after(()=>dom.window.close());

test('the moss squircle is one decorative svg with a track and a travelling dash, sized by its caller',()=>{
 const {container}=render(React.createElement(Moss,{size:18}));
 const mark=container.querySelector('.dervo-moss');
 assert.equal(mark.getAttribute('aria-hidden'),'true');
 assert.equal(mark.style.width,'18px');
 const paths=[...mark.querySelectorAll('path')].map(p=>p.getAttribute('class'));
 assert.deepEqual(paths,['dervo-moss-track','dervo-moss-car']);
 assert.equal(mark.querySelector('svg').getAttribute('width'),'18');
 assert.ok(mark.querySelector('path').getAttribute('d').startsWith('M0.5,25C'));
});

test('an active activity row shows the working smile (#31, where the squircle was); a settled or toned row shows its still mark or icon',()=>{
 const live=render(React.createElement(DervoActivity,{label:'Editing app.ts',active:true}));
 assert.ok(live.container.querySelector('.dervo-activity[data-active] [data-testid="working-mark"][data-working]'));
 assert.equal(live.container.querySelector('.dervo-moss'),null,'the squircle left the status row on 19 Sep');
 assert.equal(live.container.querySelector('.dervo-activity-dot'),null);
 cleanup();
 const still=render(React.createElement(DervoActivity,{label:'Waiting',active:false}));
 assert.ok(still.container.querySelector('.dervo-activity-dot'));
 assert.equal(still.container.querySelector('[data-testid="working-mark"]'),null);
 cleanup();
 const done=render(React.createElement(DervoActivity,{label:'Done',active:true,tone:'completed'}));
 assert.equal(done.container.querySelector('[data-testid="working-mark"]'),null,'a completed row is not running, whatever active says');
});

test('the squircle keeps a still dash under reduced motion and the old pulse is gone',()=>{
 const css=readFileSync(new URL('./Moss.css',import.meta.url),'utf8');
 assert.match(css,/prefers-reduced-motion: no-preference[\s\S]*dervo-moss-travel/,'motion only when the user allows it');
 assert.match(css,/\.dervo-moss-car \{[^}]*stroke-dashoffset:-6/,'a still dash shows outside the animation');
 const activity=readFileSync(new URL('./DervoActivity.css',import.meta.url),'utf8');
 assert.doesNotMatch(activity,/animate-pulse|dervo-activity-pulse/);
 const tasks=readFileSync(new URL('./CodingTasks.tsx',import.meta.url),'utf8');
 assert.doesNotMatch(tasks,/animate-pulse/,'the task footer uses the squircle, not a pulsing dot');
});
