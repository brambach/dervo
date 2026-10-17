import assert from 'node:assert/strict';
import {test,after,afterEach,beforeEach,mock} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup,act}=await import('@testing-library/react');
const {useTaskGuideVisibility,expectsTask,NARROW_SETTLE_MS}=await import('./useTaskGuideVisibility.ts');
let width=1000,latest,observers=[];
const originalRect=HTMLElement.prototype.getBoundingClientRect;
HTMLElement.prototype.getBoundingClientRect=function(){return {...originalRect.call(this),width};};
class Observer{
 constructor(callback){this.callback=callback;this.disconnected=false;observers.push(this);}
 observe(target){this.target=target;}
 disconnect(){this.disconnected=true;}
 emit(value){this.callback([{target:this.target,contentRect:{width:value}}]);}
}
globalThis.ResizeObserver=Observer;
function Fixture({scope='p:c',hasTask=true,expectTask=false}){
 const guide=useTaskGuideVisibility({scope,hasTask,expectTask});latest=guide;
 return React.createElement('main',{ref:guide.mainRef},
  React.createElement('button',{ref:guide.triggerRef,onClick:guide.toggle},'Task guide'),
  React.createElement('input',{'aria-label':'Composer'}),
  guide.visible&&React.createElement('aside',{className:'dervo-task-guide'},
   React.createElement('button',{onClick:guide.close},'Hide task guide'),
   React.createElement('button',null,'Inspect task')));
}
function resize(value){width=value;act(()=>observers.at(-1).emit(value));}
function wait(ms){act(()=>mock.timers.tick(ms));}
beforeEach(()=>mock.timers.enable({apis:['setTimeout']}));
afterEach(()=>{cleanup();mock.timers.reset();observers=[];width=1000;globalThis.ResizeObserver=Observer;delete window.__dervoGuideTrace;});
after(()=>{HTMLElement.prototype.getBoundingClientRect=originalRect;dom.window.close();});

test('wide containers show a guide by default when a task arrives; narrow containers preserve the composer',()=>{
 for(const initialWidth of [1000,620,400]){
  width=initialWidth;
  const view=render(React.createElement(Fixture,{hasTask:false}));
  assert.equal(latest.visible,false);
  const composer=view.getByRole('textbox');composer.focus();
  view.rerender(React.createElement(Fixture,{hasTask:true}));
  assert.equal(latest.visible,initialWidth>620);
  assert.equal(document.activeElement,composer);
  cleanup();
 }
});

test('explicit narrow opening survives resizes on either side of the threshold and close restores focus',()=>{
 width=500;const view=render(React.createElement(Fixture));
 const trigger=view.getByRole('button',{name:'Task guide'});
 fireEvent.click(trigger);assert.equal(latest.visible,true);
 resize(600);assert.equal(latest.visible,true);
 resize(1000);assert.equal(latest.visible,true);
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,true);
 const hide=view.getByRole('button',{name:'Hide task guide'});hide.focus();fireEvent.click(hide);
 assert.equal(latest.visible,false);assert.equal(document.activeElement,trigger);
});

// Changed on purpose for beta.11 (#10, #11, DC3): the close waits for the width to settle, and an explicit open
// isn't forgotten by crossing the threshold any more.
test('crossing into narrow mode closes the guide only after the width settles, and returns focus only when it was inside the guide',()=>{
 const view=render(React.createElement(Fixture));
 const trigger=view.getByRole('button',{name:'Task guide'});
 const inspect=view.getByRole('button',{name:'Inspect task'});inspect.focus();
 resize(620);wait(100);assert.equal(latest.visible,true);assert.equal(document.activeElement,inspect);
 wait(NARROW_SETTLE_MS-100);assert.equal(latest.visible,false);assert.equal(document.activeElement,trigger);
 resize(621);assert.equal(latest.visible,true);
 // Negative control: a narrow reading that a wider one replaces within 100 ms never closes it or moves focus.
 view.getByRole('button',{name:'Inspect task'}).focus();const focused=document.activeElement;
 resize(620);wait(100);resize(700);wait(NARROW_SETTLE_MS*4);
 assert.equal(latest.visible,true);assert.equal(document.activeElement,focused);
 const composer=view.getByRole('textbox');composer.focus();
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,false);assert.equal(document.activeElement,composer);
});

test('a transient narrow reading during a resize does not forget an explicit open',()=>{
 width=500;const view=render(React.createElement(Fixture));
 fireEvent.click(view.getByRole('button',{name:'Task guide'}));assert.equal(latest.visible,true);
 resize(1000);resize(500);resize(1000);assert.equal(latest.visible,true);
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,true);
 resize(1000);resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,true);
 // Negative control: with no explicit open, the same settled narrow reading closes it.
 cleanup();width=500;render(React.createElement(Fixture));assert.equal(latest.visible,false);
 resize(1000);assert.equal(latest.visible,true);
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,false);
});

test('an explicit close while wide holds until a real narrow-then-wide cycle',()=>{
 const view=render(React.createElement(Fixture));
 fireEvent.click(view.getByRole('button',{name:'Task guide'}));assert.equal(latest.visible,false);
 resize(800);assert.equal(latest.visible,false);
 // Negative control: a narrow reading shorter than the settle time isn't a cycle, so the close holds.
 resize(500);wait(NARROW_SETTLE_MS-1);resize(1000);wait(NARROW_SETTLE_MS*4);assert.equal(latest.visible,false);
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,false);
 resize(1000);assert.equal(latest.visible,true);
});

test('the first frame measuring zero does not close a guide that then measures wide',()=>{
 width=0;render(React.createElement(Fixture));assert.equal(latest.visible,false);
 resize(1000);assert.equal(latest.visible,true);
 // Negative control: a zero first frame followed by a real narrow width stays closed.
 cleanup();observers=[];width=0;render(React.createElement(Fixture));
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,false);
});

test('no trace output unless the trace flag is set',()=>{
 const info=mock.method(console,'info',()=>{});
 try{
  const view=render(React.createElement(Fixture));
  resize(500);wait(NARROW_SETTLE_MS);resize(1000);fireEvent.click(view.getByRole('button',{name:'Task guide'}));
  view.rerender(React.createElement(Fixture,{hasTask:false}));
  assert.equal(info.mock.callCount(),0);
  // Negative control: the same kind of change with the flag set is traced.
  window.__dervoGuideTrace=true;
  view.rerender(React.createElement(Fixture,{hasTask:true}));
  assert.ok(info.mock.callCount()>=1);
  const [label,fields]=info.mock.calls.at(-1).arguments;
  assert.equal(label,'guide');
  assert.deepEqual(Object.keys(fields).sort(),['choice','hasTask','mode','scope','visible','width']);
  assert.equal(fields.width,1000);
 }finally{info.mock.restore();}
});

test('explicit choices belong to their scope and callbacks from an old conversation do nothing',()=>{
 const view=render(React.createElement(Fixture,{scope:'p:a'}));
 const old=latest;
 fireEvent.click(view.getByRole('button',{name:'Task guide'}));assert.equal(latest.visible,false);
 view.rerender(React.createElement(Fixture,{scope:'p:b'}));assert.equal(latest.visible,true);
 const composer=view.getByRole('textbox');composer.focus();
 act(()=>{old.close();old.toggle();});
 assert.equal(latest.visible,true);assert.equal(document.activeElement,composer);
 view.rerender(React.createElement(Fixture,{scope:'p:a'}));assert.equal(latest.visible,true);
 resize(500);wait(NARROW_SETTLE_MS);fireEvent.click(view.getByRole('button',{name:'Task guide'}));assert.equal(latest.visible,true);
 view.rerender(React.createElement(Fixture,{scope:'q:b'}));assert.equal(latest.visible,false);
});

test('no task means no guide, including captured actions, and resize cleanup ignores a late observer callback',()=>{
 const view=render(React.createElement(Fixture));const old=latest,observer=observers.at(-1);
 view.rerender(React.createElement(Fixture,{hasTask:false}));
 act(()=>{old.toggle();old.close();});assert.equal(latest.visible,false);
 resize(400);view.unmount();assert.equal(observer.disconnected,true);
 act(()=>observer.emit(400));wait(NARROW_SETTLE_MS*2);
});

test('window resize measures the container when ResizeObserver is unavailable',()=>{
 globalThis.ResizeObserver=undefined;width=500;
 const view=render(React.createElement(Fixture));assert.equal(latest.visible,false);
 width=1000;act(()=>window.dispatchEvent(new Event('resize')));assert.equal(latest.visible,true);
 width=600;act(()=>window.dispatchEvent(new Event('resize')));assert.equal(latest.visible,true);
 wait(NARROW_SETTLE_MS);assert.equal(latest.visible,false);
 view.unmount();width=1000;act(()=>window.dispatchEvent(new Event('resize')));
});

// Bryce, beta.11 previews: "Whenever I open a thread it does not default to open." ConnectedApp returns OpeningScreen
// on its first render, so <main> mounts a render later than the hook. The guide has to measure it then.
function LateMain({ready,scope='p:c',hasTask=true}){
 const guide=useTaskGuideVisibility({scope,hasTask});latest=guide;
 if(!ready)return React.createElement('div',null,'Opening');
 return React.createElement('main',{ref:guide.mainRef},
  React.createElement('button',{ref:guide.triggerRef,onClick:guide.toggle},'Task guide'),
  guide.visible&&React.createElement('aside',{className:'dervo-task-guide'},'Progress'));
}

test('a main area that mounts after the first render still opens the guide by default when wide',()=>{
 width=1000;
 const view=render(React.createElement(LateMain,{ready:false}));
 assert.equal(latest.visible,false);
 assert.equal(observers.length,0);
 view.rerender(React.createElement(LateMain,{ready:true}));
 assert.equal(latest.visible,true);
 assert.ok(view.container.querySelector('.dervo-task-guide'));
 // It's watched from then on: a settled narrow reading closes it.
 resize(500);wait(NARROW_SETTLE_MS);assert.equal(latest.visible,false);
});

test('negative control: a late main area that is narrow keeps the guide closed',()=>{
 width=500;
 const view=render(React.createElement(LateMain,{ready:false}));
 view.rerender(React.createElement(LateMain,{ready:true}));
 assert.equal(latest.visible,false);
 assert.equal(view.container.querySelector('.dervo-task-guide'),null);
 resize(1000);assert.equal(latest.visible,true);
});

// Beta.18: the column used to pop in once a thread's tasks loaded, squeezing the thread that had drawn full width.
test('while a thread that had a run loads, its column is held where the guide will open, and handed over once the task is in',()=>{
 width=1000;const view=render(React.createElement(Fixture,{hasTask:false,expectTask:true}));
 assert.equal(latest.reserved,true,'held while the tasks load');
 assert.equal(latest.visible,false);
 view.rerender(React.createElement(Fixture,{hasTask:true,expectTask:false}));
 assert.equal(latest.reserved,false);assert.equal(latest.visible,true,'the guide takes the place it held');
 cleanup();
 width=500;render(React.createElement(Fixture,{hasTask:false,expectTask:true}));
 assert.equal(latest.reserved,false,'narrow: Progress docks under the chat only when asked for, so nothing is held');
 cleanup();
 width=1000;render(React.createElement(Fixture,{hasTask:false,expectTask:false}));
 assert.equal(latest.reserved,false,'a thread with no sign of a run holds nothing');
});
test('a thread expects a task when its activity shows a run, and not when nothing has run in it',()=>{
 assert.equal(expectsTask(null),false);
 assert.equal(expectsTask({status:null,running:false,step:null,unread:0}),false);
 assert.equal(expectsTask({status:null,running:true}),true);
 assert.equal(expectsTask({status:'needs',running:false}),true);
 assert.equal(expectsTask({status:null,running:false,at:1700000000,ended:'ready_for_review'}),true);
 assert.equal(expectsTask({status:null,running:false,summary:'Approved and committed.'}),true);
});
