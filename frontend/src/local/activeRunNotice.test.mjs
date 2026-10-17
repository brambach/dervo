import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {ActiveRunNotice}=await import('./ActiveRunNotice.tsx');
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});after(()=>dom.window.close());
const active={task_id:'other-task',thread_id:'d',project_id:'q',origin:'conversation',agent:null,name:'Design notes'};
function host(handler=()=>({})){const calls=[];window.__TAURI_INTERNALS__={invoke:async(command,{operation,args})=>{assert.equal(command,'local_request');calls.push({operation,args});return handler(operation,args);}};return calls;}

test('names the thread holding the run in bold and offers to open it with its ids',()=>{
 host();const opened=[];
 render(React.createElement(ActiveRunNotice,{active,onOpen:(p,c)=>opened.push([p,c]),onDismiss(){},refresh:async()=>true}));
 const notice=screen.getByTestId('active-run-notice');
 assert.match(notice.textContent,/active in Design notes\. Wait for it to finish or stop it first\. Your message is kept\./);
 assert.equal(notice.querySelector('b').textContent,'Design notes','the name, not the host\'s #name');
 fireEvent.click(screen.getByRole('button',{name:'Open Design notes'}));
 assert.deepEqual(opened,[['q','d']]);
});

test('shows the host\'s own reason, and says the message is kept',()=>{
 host();
 const {container}=render(React.createElement(ActiveRunNotice,{active,message:'3 coding tasks are already running. Wait for one to finish or stop one first.',onOpen(){},onDismiss(){},refresh:async()=>true}));
 assert.equal(screen.getByTestId('active-run-notice').querySelector('.dv-what').textContent,'3 coding tasks are already running. Wait for one to finish or stop one first. Your message is kept.');
 assert.equal(container.querySelector('b'),null);
});

test('Stop it sends task_stop with the holder\'s own project and channel, refreshes, and the notice says it stopped',async()=>{
 let refreshed=0,dismissed=0;const calls=host();
 render(React.createElement(ActiveRunNotice,{active,onOpen(){},onDismiss:()=>{dismissed++;},refresh:async()=>{refreshed++;return true;}}));
 fireEvent.click(screen.getByRole('button',{name:'Pause it'}));
 await waitFor(()=>assert.match(screen.getByTestId('active-run-notice').textContent,/Paused\. Send your message when you’re ready\./));
 assert.deepEqual(calls,[{operation:'task_stop',args:{projectId:'q',channelId:'d',taskId:'other-task'}}]);
 assert.equal(refreshed,1);
 assert.equal(dismissed,0,'it waits for you rather than vanishing');
 assert.equal(screen.queryByRole('button',{name:'Pause it'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Dismiss'}));
 assert.equal(dismissed,1);
});

test('a hidden conversation is counted but not named, and offers no Open',()=>{
 host();
 render(React.createElement(ActiveRunNotice,{active:{...active,name:null},onOpen(){},onDismiss(){},refresh:async()=>true}));
 assert.match(screen.getByTestId('active-run-notice').textContent,/active in another conversation/);
 assert.equal(screen.queryByRole('button',{name:/^Open/}),null);
 assert.ok(screen.getByRole('button',{name:'Pause it'}));
});

test('a refused stop keeps the notice and shows the reason',async()=>{
 let dismissed=0;host(()=>{throw Error('Task identity changed');});
 render(React.createElement(ActiveRunNotice,{active,onOpen(){},onDismiss:()=>{dismissed++;},refresh:async()=>true}));
 fireEvent.click(screen.getByRole('button',{name:'Pause it'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Task identity changed/));
 assert.equal(dismissed,0);
 assert.equal(screen.getByRole('button',{name:'Pause it'}).disabled,false);
});
