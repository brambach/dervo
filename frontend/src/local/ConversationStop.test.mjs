import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {ConversationStop,conversationStopTargets}=await import('./ConversationStop.tsx');
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});after(()=>dom.window.close());
const defaults={projectId:'p',channelId:'c',activity:{state:'working',active:true,detail:'Fixture',active_turn_ids:['raw']},tasks:[],requests:[],refresh:async()=>true};
function host(handler=()=>({})){const calls=[];window.__TAURI_INTERNALS__={invoke:async(command,{operation,args})=>{assert.equal(command,'local_request');calls.push({operation,args});return handler(operation,args);}};return calls;}
const component=props=>React.createElement(ConversationStop,{...defaults,...props});

test('rendering active or settled records never sends cancellation',()=>{
 const calls=host();const view=render(component({}));assert.ok(screen.getByRole('button',{name:'Pause work in this conversation'}));assert.equal(calls.length,0);
 view.rerender(component({activity:{state:'unknown',active:false,detail:'Delivery unknown'},tasks:[{id:'ended',state:'completed'}],requests:[{id:'ended-helper',state:'completed'}]}));
 assert.equal(screen.queryByRole('button'),null);assert.equal(calls.length,0);
});

test('targets include recorded active turns, active coding review and coordination only',()=>{
 assert.deepEqual(conversationStopTargets(defaults.activity,[{id:'working',state:'working'},{id:'review',state:'ready_for_review',collaboration:{review:{phase:'final'}}},{id:'done',state:'completed'},{id:'unknown',state:'blocked'}],[{id:'queued',state:'queued'},{id:'running',state:'running'},{id:'stopping',state:'stopping'},{id:'finished',state:'completed'},{id:'unknown',state:'unknown'}]),[
  {operation:'stop_turn',args:{messageId:'raw'}},{operation:'task_stop',args:{taskId:'working'}},{operation:'task_stop',args:{taskId:'review'}},{operation:'harness_cancel',args:{requestId:'queued'}},{operation:'harness_cancel',args:{requestId:'running'}},{operation:'harness_cancel',args:{requestId:'stopping'}}]);
});

test('Stop all continues after one failure, keeps exact scope and retains errors after targets settle',async()=>{
 let refreshed=0;const calls=host(operation=>{if(operation==='stop_turn')throw Error('Raw stop refused');return {};});
 const props={tasks:[{id:'task',state:'working'}],requests:[{id:'helper',state:'running'}],refresh:async()=>{refreshed++;throw Error('Refresh unavailable');}};
 const view=render(component(props));fireEvent.click(screen.getByRole('button',{name:'Pause all work in this conversation'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Refresh unavailable/));assert.match(screen.getByRole('alert').textContent,/Raw stop refused/);assert.equal(refreshed,1);
 assert.deepEqual(calls,[{operation:'stop_turn',args:{projectId:'p',channelId:'c',messageId:'raw'}},{operation:'task_stop',args:{projectId:'p',channelId:'c',taskId:'task'}},{operation:'harness_cancel',args:{projectId:'p',channelId:'c',requestId:'helper'}}]);
 view.rerender(component({...props,activity:undefined,tasks:[],requests:[]}));assert.equal(screen.queryByRole('button'),null);assert.match(screen.getByRole('alert').textContent,/Raw stop refused/);
});

test('pending stop disables repeated clicks and permits explicit retry after failure',async()=>{
 let release;const calls=host(()=>new Promise((_resolve,reject)=>{release=()=>reject(Error('Stop not confirmed'));}));render(component({}));
 const button=screen.getByRole('button',{name:'Pause work in this conversation'});fireEvent.click(button);assert.equal(button.disabled,true);fireEvent.click(button);assert.equal(calls.length,1);
 await act(async()=>release());await waitFor(()=>assert.equal(button.disabled,false));assert.match(screen.getByRole('alert').textContent,/Stop not confirmed/);
 fireEvent.click(button);assert.equal(calls.length,2);await act(async()=>release());
});

test('late old-scope stop completion cannot cancel new work or publish an error in its keyed control',async()=>{
 let release,oldRefresh=0,newRefresh=0;const calls=host((_op,args)=>args.messageId==='raw'?new Promise((_resolve,reject)=>{release=()=>reject(Error('Old conversation failure'));}):{});
 const old={...defaults,tasks:[{id:'old-task',state:'working'}],refresh:async()=>{oldRefresh++;return false;}};
 const view=render(React.createElement(ConversationStop,{...old,key:'p:c'}));fireEvent.click(screen.getByRole('button',{name:'Pause all work in this conversation'}));
 view.rerender(React.createElement(ConversationStop,{...defaults,key:'q:new',projectId:'q',channelId:'new',activity:{...defaults.activity,active_turn_ids:['new-raw']},refresh:async()=>{newRefresh++;return true;}}));
 await act(async()=>release());await waitFor(()=>assert.equal(oldRefresh,1));assert.equal(newRefresh,0);assert.equal(screen.queryByRole('alert'),null);
 assert.deepEqual(calls,[{operation:'stop_turn',args:{projectId:'p',channelId:'c',messageId:'raw'}},{operation:'task_stop',args:{projectId:'p',channelId:'c',taskId:'old-task'}}]);
 assert.equal(screen.getByRole('button',{name:'Pause work in this conversation'}).disabled,false);
});

test('a task’s own Stop is enough: the floating Stop shows only for other work',()=>{
 host();
 const task={id:'t',state:'working',tokens:[],runs:[],messages:[]};
 const view=render(component({tasks:[task],tasksStoppedElsewhere:true,activity:{active_turn_ids:[]}}));
 assert.equal(screen.queryByRole('button',{name:'Pause work in this conversation'}),null);
 view.rerender(component({tasks:[task],tasksStoppedElsewhere:true,activity:{active_turn_ids:['m1']}}));
 assert.ok(screen.getByRole('button',{name:'Pause all work in this conversation'}),'a chat turn still needs it');
});
