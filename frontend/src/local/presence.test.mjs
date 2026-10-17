import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
for (const key of ['window', 'document', 'HTMLElement', 'SVGElement', 'Node', 'Event', 'MutationObserver']) globalThis[key] = dom.window[key];
Object.defineProperty(globalThis, 'navigator', {configurable:true, value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let reduced = false;
const listeners = new Set();
window.matchMedia = () => ({get matches() {return reduced;}, addEventListener:(_, listener) => listeners.add(listener), removeEventListener:(_, listener) => listeners.delete(listener)});
const React = await import('react');
const {render, cleanup, act} = await import('@testing-library/react');
const {StepLine} = await import('./Presence.tsx');
const {DervoActivity} = await import('./DervoActivity.tsx');
afterEach(() => {cleanup(); reduced = false; listeners.clear();});
after(() => dom.window.close());

test('Home and thread activity keep their words when the motion preference changes', () => {
 const view = render(React.createElement(React.Fragment, null,
  React.createElement(StepLine, {text:'Thinking it through', waited:null}),
  React.createElement(DervoActivity, {label:'Reading project.ts', active:true}),
  React.createElement(StepLine, {text:'Starting a thread', waited:null, foot:true})));
 const words = [...view.container.querySelectorAll('.dv-presence-text')];
 assert.deepEqual(words.map(word => word.hasAttribute('data-shimmer')), [true, true, false]);
 const before = view.container.textContent;
 act(() => {reduced = true; for (const listener of listeners) listener();});
 assert.equal(view.container.textContent, before);
 assert.equal(view.container.querySelector('[data-shimmer]'), null);
 assert.deepEqual([...view.container.querySelectorAll('.dv-presence-text')], words, 'no remount or extra announcement for a motion preference');
 act(() => {reduced = false; for (const listener of listeners) listener();});
 assert.deepEqual(words.map(word => word.hasAttribute('data-shimmer')), [true, true, false]);
});

test('only observed neutral work shimmers; attention, failure, stop and completion stay still', () => {
 const view = render(React.createElement(DervoActivity, {label:'Working', active:true}));
 const words = view.container.querySelector('.dv-presence-text');
 assert.equal(words.dataset.shimmer, 'true');
 for (const [tone, label] of [['attention','Needs your answer'], ['error','Request failed'], ['cancelled','Stopped'], ['completed','Finished']]) {
  view.rerender(React.createElement(DervoActivity, {label, active:true, tone}));
  assert.equal(view.container.querySelector('.dv-presence-text'), words);
  assert.equal(words.textContent, label);
  assert.equal(view.container.querySelector('[data-shimmer]'), null);
  assert.equal(view.container.querySelector('[data-working]'), null);
 }
 view.rerender(React.createElement(DervoActivity, {label:'Idle', active:false}));
 assert.equal(view.container.querySelector('[data-shimmer]'), null);
 assert.equal(view.container.querySelectorAll('[role="status"]').length, 1, 'the shared words add no second live region');
});
