import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
import {advanceReceived, incomingRate, releaseRate, safeTextBoundary, MIN_SPREAD_MS, MAX_SPREAD_MS, INITIAL_STREAM_RATE} from './receivedText.ts';
const dom = new JSDOM('<!doctype html><html><body></body></html>');
// SVGElement is needed because motion-dom's supportsBrowserAnimation does an instanceof
// against it, and without it the whole file dies on a ReferenceError rather than failing a test.
for (const name of ['window','document','HTMLElement','SVGElement','MutationObserver','Node','Event']) globalThis[name] = dom.window[name];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let reduced = false, notifyMotion;
window.matchMedia = () => ({matches:reduced, addEventListener:(_, fn)=>{notifyMotion=fn;}, removeEventListener:()=>{notifyMotion=undefined;}});
const React = await import('react');
const {render, act, cleanup} = await import('@testing-library/react');
const {useReceivedText} = await import('./useReceivedText.ts');
const {ReceivedResponse} = await import('./ReceivedResponse.tsx');
afterEach(()=>{cleanup();reduced=false;});
after(()=>dom.window.close());
const wait = ms => act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
function Fixture({text,active=true,identity='a'}) {return React.createElement('output',null,useReceivedText(text,active,identity));}

test('pacing is finite and bounded by received content',()=>{
 assert.equal(incomingRate(128,10,0),128);
 assert.equal(advanceReceived(0,10,Infinity,50),6.4);
 assert.equal(advanceReceived(0,10,128,-5),0);
 assert.equal(advanceReceived(9,10,1000,50),10);
 assert.equal(advanceReceived(0,4000,4000/1.5,250),Math.min(4000,4000/1.5*0.25));
 assert.equal(safeTextBoundary('a😀b',2),1);
 assert.equal(safeTextBoundary('a😀b',3),3);
 assert.equal(safeTextBoundary('a\ud83d',2),1);
});
test('a burst is released over about as long as it took to arrive, never popped in whole (#30)',()=>{
 // 600 characters after a two-second pause: spread over the 1.5 s cap, so 50 ms shows about 20 characters, not 600.
 const burst=releaseRate(128,600,2000);
 assert.equal(burst,600*1000/MAX_SPREAD_MS);
 const after50=advanceReceived(0,600,burst,50);
 assert.ok(after50<40&&after50>10,`a burst starts flowing at once but stays a flow: ${after50}`);
 let cursor=0;for(let i=0;i<30;i++)cursor=advanceReceived(cursor,600,burst,50);
 assert.equal(cursor,600,'and the whole burst is on screen within its spread');
 // Negative control: the old rule cleared any backlog within 150 ms, which is the burst the owner saw.
 assert.ok(advanceReceived(0,600,600/0.15,50)>=199,'the pre-#30 drain rate would show a third of the burst in one frame');
 // Token streaming (a few characters every 40 ms) keeps its own pace: the floor and the observed rate, no burst term.
 assert.equal(releaseRate(128,5,40),Math.max(INITIAL_STREAM_RATE,5*1000/MIN_SPREAD_MS));
 assert.equal(releaseRate(900,5,40),900,'a fast steady stream is shown at its own rate');
 // A pause longer than the cap still spreads over the cap; a broken clock falls back to the cap.
 assert.equal(releaseRate(128,300,60_000),300*1000/MAX_SPREAD_MS);
 assert.equal(releaseRate(128,300,NaN),300*1000/MAX_SPREAD_MS);
 assert.equal(releaseRate(NaN,0,500),INITIAL_STREAM_RATE);
});
test('saved history is immediate and starts no interval',()=>{
 const original=globalThis.setInterval;let timers=0;
 globalThis.setInterval=(...args)=>{timers++;return original(...args);};
 try {const view=render(React.createElement(Fixture,{text:'Saved **answer**',active:false}));assert.equal(view.container.textContent,'Saved **answer**');assert.equal(timers,0);}
 finally {globalThis.setInterval=original;}
});
test('active text reveals received prefixes and immediately flushes on stop or lost observation',async()=>{
 const text='hello 😀 '.repeat(40),view=render(React.createElement(Fixture,{text:''}));
 view.rerender(React.createElement(Fixture,{text}));
 assert.equal(view.container.textContent,'');await wait(65);
 assert.ok(view.container.textContent.length>0&&view.container.textContent.length<text.length);
 assert.ok(text.startsWith(view.container.textContent));
 view.rerender(React.createElement(Fixture,{text,active:false}));
 assert.equal(view.container.textContent,text);
});
test('snapshot replacement and conversation change never flash previous response text',async()=>{
 const view=render(React.createElement(Fixture,{text:'previous response '.repeat(10)}));await wait(65);
 view.rerender(React.createElement(Fixture,{text:'new response',identity:'b'}));
 assert.equal(view.container.textContent,'new response');
 view.rerender(React.createElement(Fixture,{text:'replacement',identity:'b'}));assert.equal(view.container.textContent,'replacement');
});
test('changing reduced-motion preference flushes text and bypasses later smoothing',async()=>{
 const view=render(React.createElement(Fixture,{text:'response '.repeat(20)}));
 act(()=>{reduced=true;notifyMotion();});assert.equal(view.container.textContent,'response '.repeat(20));
 view.rerender(React.createElement(Fixture,{text:'next received chunk'}));assert.equal(view.container.textContent,'next received chunk');
});
test('pre-response label waits briefly and disappears on first received text',async()=>{
 const props={text:'',presentation:{identity:'run',label:'Thinking'},children:visible=>React.createElement('p',null,visible)};
 const view=render(React.createElement(ReceivedResponse,props));assert.equal(view.container.textContent,'');
 await wait(175);assert.equal(view.container.textContent,'Thinking');
 view.rerender(React.createElement(ReceivedResponse,{...props,text:'answer'}));assert.ok(!view.container.textContent.includes('Thinking'));
 view.rerender(React.createElement(ReceivedResponse,{...props,text:'answer',presentation:undefined}));assert.equal(view.container.textContent,'answer');
});
test('only a mounted live response retains the streaming parser; reopening history uses cached Markdown',()=>{
 const parsers=[];
 const children=(visible,transient,compatible)=>{parsers.push({transient,compatible});return React.createElement('p',null,visible);};
 const view=render(React.createElement(ReceivedResponse,{text:'received',presentation:{identity:'run',label:'Thinking'},children}));
 assert.equal(parsers.at(-1).compatible,true);
 view.rerender(React.createElement(ReceivedResponse,{text:'finished',children}));
 assert.deepEqual(parsers.at(-1),{transient:false,compatible:true});
 view.unmount();
 render(React.createElement(ReceivedResponse,{text:'finished',children}));
 assert.deepEqual(parsers.at(-1),{transient:false,compatible:false});
});
test('the live line has no smile since 16.2: the step changes in place and the line leaves when the run ends',async()=>{
 const props={text:'answer',presentation:{identity:'run',label:'Reading merged.md'},children:visible=>React.createElement('p',null,visible)};
 const view=render(React.createElement(ReceivedResponse,props));
 const line=()=>view.container.querySelector('.dervo-response-activity');
 assert.equal(view.container.querySelector('[data-testid="working-mark"]'),null,'the smile left the reply row (mock F)');
 const step=line().querySelector('.dv-live-step');
 assert.equal(step.textContent,'Reading merged.md');
 assert.equal(step.dataset.shimmer,'true');
 view.rerender(React.createElement(ReceivedResponse,{...props,presentation:{identity:'run',label:'Editing plan.md'}}));
 assert.equal(view.container.querySelectorAll('.dervo-response-activity').length,1,'one line that changes in place, not a stack');
 assert.equal(line().querySelector('.dv-live-step'),step,'a new observed step keeps the label mounted and its shimmer running');
 assert.equal(line().querySelector('.dv-live-step').textContent,'Editing plan.md');
 view.rerender(React.createElement(ReceivedResponse,{...props,presentation:undefined}));
 assert.equal(line(),null,'the line leaves with the run');
 assert.equal(view.container.querySelector('[data-shimmer]'),null,'settled answer prose never shimmers');
});

const trailRun=(over={})=>({key:'k',model:'Opus 5.5',startedAt:100,live:true,done:[{id:'a',text:'Read',subject:'a.ts'},{id:'b',text:'Searched for',subject:'coding_request'}],now:{id:'c',text:'Editing',subject:'ConnectedApp.tsx'},steps:2,helpers:0,latest:true,...over});
test('a run shows its trail at once, keeps one trail element when words arrive, then folds into the chip on its last row (16.2, mock F)',()=>{
 const children=visible=>React.createElement('p',null,visible);
 const view=render(React.createElement(ReceivedResponse,{text:'',presentation:{identity:'run',label:'Getting started'},run:trailRun(),children}));
 const trail=view.container.querySelector('[data-testid="reply-trail"]');
 assert.ok(trail,'no 160 ms wait for a run');
 assert.equal(trail.textContent,'Read a.ts›Searched for coding_request›EditingConnectedApp.tsx');
 assert.ok(trail.querySelector('.dv-trail-old'),'the older crumb is the fainter one');
 assert.equal(trail.querySelectorAll('.dv-trail-old').length,1);
 assert.equal(trail.querySelector('.dv-live-step').dataset.shimmer,'true');
 assert.equal(view.container.querySelector('[data-testid="working-mark"]'),null);
 view.rerender(React.createElement(ReceivedResponse,{text:'answer',presentation:{identity:'run',label:'Editing'},run:trailRun({key:'k2'}),children}));
 assert.equal(view.container.querySelector('[data-testid="reply-trail"]'),trail,'the same element under the words, so nothing restarts');
 assert.ok(view.container.querySelector('.dervo-response-activity > [data-testid="reply-trail"]'));
 const ended=trailRun({key:'k3',live:false,now:undefined,endedAt:113,steps:6,helpers:2});
 view.rerender(React.createElement(ReceivedResponse,{text:'answer',run:ended,children}));
 assert.equal(view.container.querySelector('[data-testid="reply-trail"]'),null);
 assert.equal(view.container.querySelector('[data-testid="reply-run-chip"]'),null,'only the run’s last row carries the chip');
 view.rerender(React.createElement(ReceivedResponse,{text:'answer',run:{...ended,tail:true},children}));
 assert.equal(view.container.querySelector('[data-testid="reply-run-chip"]').textContent,'6 steps · 2 helpers · 13s›');
 let opened=0;window.addEventListener('dervo-open-progress',()=>opened++);
 view.container.querySelector('[data-testid="reply-run-chip"]').click();
 assert.equal(opened,1,'the chip opens Progress');
 // Negative control: an older run of the task has no chip, since Progress shows only the latest.
 view.rerender(React.createElement(ReceivedResponse,{text:'answer',run:{...ended,tail:true,latest:false},children}));
 assert.equal(view.container.querySelector('[data-testid="reply-run-chip"]'),null);
});
test('while helpers work the live step has one dot each, sage once finished',()=>{
 const run=trailRun({now:{id:'h',text:'Waiting on 2 helpers',helpers:[true,false]}});
 const view=render(React.createElement(ReceivedResponse,{text:'',presentation:{identity:'run',label:'Thinking'},run,children:v=>v}));
 const dots=[...view.container.querySelectorAll('.dv-trail-dots i')];
 assert.deepEqual(dots.map(dot=>dot.dataset.done??''),['true','']);
});
