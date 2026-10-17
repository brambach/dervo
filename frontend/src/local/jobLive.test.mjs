import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','MutationObserver','Event','CustomEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,cleanup}=await import('@testing-library/react');
const {liveJob,ranFor,LiveJobLine}=await import('./JobLive.tsx');
const {StatusGlyph,arrivalOf}=await import('./StatusGlyph.tsx');
after(()=>dom.window.close());
afterEach(()=>cleanup());

const SENT=1_800_000_000;
const activity=(extra={})=>({status:null,running:false,step:null,unread:0,since:null,at:null,summary:null,...extra});

test('a job Home started shows its live step and clock while it runs, then the record’s summary (chat-first report #1)',()=>{
 assert.equal(liveJob({state:'pending'},activity({running:true,step:'Reading Home.css'}),SENT),null,'an unsent job keeps Home’s own words');
 assert.equal(liveJob({state:'sent'},null,SENT),null,'no activity yet: nothing is guessed');
 assert.deepEqual(liveJob({state:'sent'},activity({running:true,step:'Reading Home.css',since:SENT+5,next:'Add the dark mode toggle'}),SENT),
  {kind:'running',step:'Reading Home.css',since:SENT+5,next:'Add the dark mode toggle'});
 assert.equal(liveJob({state:'sent'},activity({running:true,step:null}),SENT).step,'Working','a run between steps says Working, never Started');
 assert.deepEqual(liveJob({state:'sent'},activity({summary:'Finished. 2 files changed. No checks ran.',at:SENT+600}),SENT),
  {kind:'ended',summary:'Finished. 2 files changed. No checks ran.'});
 assert.equal(liveJob({state:'sent'},activity({summary:'Finished an older run.',at:SENT-60}),SENT),null,'a summary from before this job belongs to an earlier run');
});

test('the clock reads to the minute',()=>{
 assert.equal(ranFor(12),'under a minute');
 assert.equal(ranFor(59.9),'under a minute');
 assert.equal(ranFor(60),'1 min');
 assert.equal(ranFor(22*60+40),'22 min');
 assert.equal(ranFor(3600),'1 h');
 assert.equal(ranFor(3600+5*60),'1 h 5 min');
 assert.equal(ranFor(-5),'under a minute','a clock a little ahead of the host never goes negative');
});

test('the line puts the step and clock first and Home’s own words after',()=>{
 const since=Math.floor(Date.now()/1000)-4*60-10;
 const {container,rerender}=render(React.createElement(LiveJobLine,{live:{kind:'running',step:'Reading Home.css',since,next:'Add the toggle'},rest:'in Website'}));
 const small=container.querySelector('small');
 assert.equal(small.textContent,'Reading Home.css · 4 min · in Website');
 assert.equal(small.dataset.liveJob,'running');
 assert.equal(small.title,'Next: Add the toggle','the agent’s next step is there on hover, not a second line');
 rerender(React.createElement(LiveJobLine,{live:{kind:'running',step:'Working',since:null,next:null}}));
 assert.equal(container.querySelector('small').textContent,'Working','no start time, no clock');
 rerender(React.createElement(LiveJobLine,{live:{kind:'ended',summary:'Finished. 2 files changed. No checks ran.'}}));
 assert.equal(container.querySelector('small').textContent,'Finished. 2 files changed. No checks ran.');
});

test('the step is what gives way: the clock and the workstream stay whole at any width (design review, 23 Sep)',async()=>{
 const since=Math.floor(Date.now()/1000)-4*60-10;
 const {container,rerender}=render(React.createElement(LiveJobLine,{live:{kind:'running',step:'Reading frontend/src/local/Home.css and the landing depth lab',since,next:null},rest:'in Website'}));
 assert.equal(container.querySelector('.dv-job-step').textContent.trim(),'Reading frontend/src/local/Home.css and the landing depth lab');
 assert.equal(container.querySelector('.dv-job-when').textContent,'· 4 min · in Website');
 rerender(React.createElement(LiveJobLine,{live:{kind:'running',step:'Working',since:null,next:null},rest:'in Website'}));
 assert.equal(container.querySelector('small').textContent,'Working · in Website','no clock: the workstream still follows');
 const {readFileSync}=await import('node:fs');
 const css=readFileSync(new URL('./Home.css',import.meta.url),'utf8');
 assert.match(css,/small\[data-live-job="running"\] \{ display: flex;/);
 assert.match(css,/\.dv-job-step \{ min-width: 0; overflow: hidden; text-overflow: ellipsis; \}/);
 assert.match(css,/\.dv-job-when \{ flex: none; \}/);
 assert.match(css,/\.dv-job-text:has\(> small\) > b \{ max-width: 60%; \}/,'a long title leaves room for the line');
});

test('a glyph moves only when its state changes on screen, and a finished run draws its check (design report #3)',()=>{
 assert.equal(arrivalOf('running','running'),null);
 assert.equal(arrivalOf('running','done'),'done');
 assert.equal(arrivalOf('idle','running'),'swap');
 assert.equal(arrivalOf('running','failed'),'swap');
 const {container,rerender}=render(React.createElement(StatusGlyph,{state:'running'}));
 const glyph=()=>container.querySelector('.dv-st');
 assert.equal(glyph().hasAttribute('data-arrive'),false,'appearing is not a state change');
 const before=glyph().querySelector('svg');
 rerender(React.createElement(StatusGlyph,{state:'running'}));
 assert.equal(glyph().hasAttribute('data-arrive'),false);
 assert.equal(glyph().querySelector('svg'),before,'the same state keeps the same drawing');
 rerender(React.createElement(StatusGlyph,{state:'done'}));
 assert.equal(glyph().dataset.arrive,'done');
 assert.equal(glyph().dataset.state,'done');
 assert.notEqual(glyph().querySelector('svg'),before,'a new state is a new drawing, so its entrance runs from the start');
 rerender(React.createElement(StatusGlyph,{state:'failed'}));
 assert.equal(glyph().dataset.arrive,'swap');
});
