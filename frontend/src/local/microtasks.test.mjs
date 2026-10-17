import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup}=await import('@testing-library/react');
const {MicrotaskTip,MICROTASK_EXAMPLES,MICROTASK_HEADLINE,MICROTASK_WELCOME}=await import('./Microtasks.tsx');
after(()=>dom.window.close());
afterEach(()=>cleanup());

const source=name=>readFileSync(new URL(name,import.meta.url),'utf8');

test('the lesson says keep Dervo open, ask on Home, and shows three examples of what tiny means',()=>{
 const view=render(React.createElement(MicrotaskTip,{}));
 assert.ok(view.getByRole('heading',{level:3,name:'Keep Dervo open beside you'}));
 assert.match(view.container.textContent,/When something small comes up, ask Dervo on Home and get back to what you were doing\. Dervo finds the right thread or starts a new one, and shows you when each task is done or needs you\./);
 assert.match(view.container.textContent,/Tiny tasks look like this:/);
 assert.deepEqual([...view.container.querySelectorAll('.dv-mt-list li')].map(li=>li.textContent),MICROTASK_EXAMPLES.map(text=>`“${text}”`));
 assert.equal(MICROTASK_EXAMPLES.length,3);
 assert.equal(view.queryAllByRole('button').length,0,'no way in unless the shell gives one');
 assert.equal(document.querySelector('[data-accent]'),null,'nothing here needs the tester, so no accent');
});

test('before approval it says when Home can take a task and offers no button; after it, one button goes to Home',()=>{
 const view=render(React.createElement(MicrotaskTip,{approved:false,onHome(){}}));
 assert.match(view.container.textContent,/Once you’re approved, ask Dervo on Home when something small comes up and get back to what you were doing\./);
 assert.equal(view.queryAllByRole('button').length,0,'Home can’t take a task before approval, so nothing invites one');
 cleanup();
 let went=0;
 const ready=render(React.createElement(MicrotaskTip,{onHome:()=>went++}));
 assert.doesNotMatch(ready.container.textContent,/Once you’re approved/);
 fireEvent.click(ready.getByRole('button',{name:'Try one on Home'}));
 assert.equal(went,1);
});

test('the examples are plain text, not rows that look like work in progress',()=>{
 const view=render(React.createElement(MicrotaskTip,{onHome(){}}));
 assert.equal(view.container.querySelectorAll('.dv-st,[data-state],[role="status"],[role="progressbar"]').length,0,'no status glyphs, no live regions');
 assert.equal(view.container.querySelectorAll('.dv-mt-list button, .dv-mt-list a').length,0,'nothing in the list looks pressable');
});

test('the copy keeps house style: no em dashes, no exclamation marks, and it never asks the reader to count agents',()=>{
 const view=render(React.createElement(MicrotaskTip,{onHome(){}}));
 for(const text of [view.container.textContent,MICROTASK_HEADLINE,MICROTASK_WELCOME,...MICROTASK_EXAMPLES]){
  assert.equal(text.includes('—'),false);
  assert.equal(text.includes('!'),false);
 }
 for(const text of [`${MICROTASK_HEADLINE} ${MICROTASK_WELCOME}`]){
  assert.doesNotMatch(text,/\b\d+ agents\b/i);
  assert.doesNotMatch(text,/\b(first|only) (app|tool)\b/i);
 }
});

test('the welcome headline is the pattern in the founder’s own words, and the sentence under it keeps what Dervo does for you',()=>{
 assert.equal(MICROTASK_HEADLINE,'Keep Dervo open beside you and fire off tiny tasks.');
 assert.match(MICROTASK_WELCOME,/^A failing test, a rename, a question about the code\./);
 assert.match(MICROTASK_WELCOME,/Your Claude Code or Codex does each one/);
 assert.match(MICROTASK_WELCOME,/Dervo tells you what finished and what needs you\.$/);
});

test('the three screens use the one file: the welcome, the project-created moment and Home’s empty state',()=>{
 assert.match(source('./Welcome.tsx'),/import \{MICROTASK_HEADLINE,MICROTASK_WELCOME\} from '\.\/Microtasks';/);
 assert.match(source('./Welcome.tsx'),/<h1 className="dv-display-hero dv-wm-in">\{t\(MICROTASK_HEADLINE\)\}<\/h1>/);
 assert.match(source('./Welcome.tsx'),/<p className="dv-lede dv-wm-in">\{t\(MICROTASK_WELCOME\)\}<\/p>/);
 assert.match(source('./ProjectMoment.tsx'),/\{state!=='failed'&&<MicrotaskTip approved=\{state!=='waiting'\} onHome=\{onHome\}\/>\}/,'shown while it runs or waits, never above an error');
 assert.match(source('./ConnectedApp.tsx'),/<ProjectMoment [^]{0,500}?onHome=\{openOrchestrator\}/,'Try one on Home is the sidebar’s Home');
 assert.match(source('./Home.tsx'),/t\('Start with something tiny, like “\{example\}” Ask below, then carry on\.[^']*',\{example:t\(MICROTASK_EXAMPLES\[0\]\)\}\)/);
 assert.doesNotMatch(source('./Home.tsx'),/Ask Dervo what to do first/,'the old open-ended prompt is gone');
});
