import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','Event','CustomEvent'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ChannelNavigationProvider}=await import('@/shared/context/ChannelNavigationContext.tsx');
const {TaskFanout,ThreadSourceLink}=await import('./TaskFanout.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const h=React.createElement;
const proposal=(id,extra={})=>({id,title:'Ticket '+id,purpose:'Investigate this ticket',request:'Read the comments. Draft a reply without sending it.',source_request:'Open separate threads.',state:'proposed',channelId:null,taskId:null,canStart:true,canDismiss:true,detail:null,read_only:true,identity:'fixture@example.test',model:'fixture-model',...extra});
function mount(rows,{handle,onOpenTask,onOpenChannel}={}){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,{operation,args})=>{
  calls.push({operation,args});if(operation==='task_thread_proposals')return rows.map(row=>({...row}));
  if(handle)return handle(operation,args,rows);
  const row=rows.find(row=>row.id===args.proposalId);
  if(operation==='task_thread_start')Object.assign(row,{state:'started',canStart:false,channelId:'child-'+row.id,taskId:'task-'+row.id});
  else if(operation==='task_thread_dismiss')Object.assign(row,{state:'dismissed',canStart:false});else throw Error(operation);
  return {...row};
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(h(QueryClientProvider,{client},h(ChannelNavigationProvider,{channels:[],onOpenTask,onOpenChannel},h(TaskFanout,{projectId:'p',channelId:'source',taskId:'parent'}))));
 return {calls,view,client};
}
test('proposals wait for a click, expose the full request and start once in exact source scope',async()=>{
 const {calls}=mount([proposal('one')]);const start=await screen.findByRole('button',{name:'Start thread'});
 assert.equal(calls.filter(c=>c.operation==='task_thread_start').length,0);
 // The request is open, never folded into a <details>: Start sends every word of it (security review H1, 23 Sep).
 assert.equal(document.querySelectorAll('details').length,0,'no folded request');assert.ok(screen.getByText('Read the comments. Draft a reply without sending it.'));
 fireEvent.click(start);fireEvent.click(start);await screen.findByText('Started');
 assert.deepEqual(calls.filter(c=>c.operation==='task_thread_start'),[{operation:'task_thread_start',args:{projectId:'p',channelId:'source',taskId:'parent',proposalId:'one'}}]);
});
test('a start that gives full access says so on the row and the batch, and only then',async()=>{
 mount([proposal('one',{fullAccess:true,read_only:false}),proposal('two',{fullAccess:true,read_only:false})]);
 assert.equal((await screen.findAllByRole('button',{name:'Start with full access'})).length,2);
 assert.ok(screen.getByRole('button',{name:'Start 2 threads with full access'}));
 assert.equal(screen.getAllByText(/· Full access/).length,2);
 assert.equal(screen.queryByRole('button',{name:'Start thread'}),null);cleanup();
 mount([proposal('three'),proposal('four',{fullAccess:false,read_only:false})]);
 assert.equal((await screen.findAllByRole('button',{name:'Start thread'})).length,2);
 assert.ok(screen.getByRole('button',{name:'Start 2 threads'}));
 assert.equal(screen.queryByText(/Full access/),null);
});
test('a batch preserves partial results and starts each proposal once',async()=>{
 const rows=[proposal('one'),proposal('two'),proposal('three')];
 const {calls}=mount(rows,{handle:async(op,args)=>{const row=rows.find(row=>row.id===args.proposalId);Object.assign(row,args.proposalId==='two'?{state:'failed',detail:'Account needs attention',channelId:'child-two'}:{state:'started',canStart:false,channelId:'child-'+row.id});return row;}});
 fireEvent.click(await screen.findByRole('button',{name:'Start 3 threads'}));await screen.findByText('Account needs attention');
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='task_thread_start').length,3));
 await waitFor(()=>assert.equal(screen.getAllByText('Started').length,2));
 assert.deepEqual(calls.filter(c=>c.operation==='task_thread_start').map(c=>c.args.proposalId),['one','two','three']);assert.ok(screen.getByRole('button',{name:'Check and retry'}));
});
test('leaving during a batch prevents later starts after the first response',async()=>{
 let finish;const {calls,view}=mount([proposal('one'),proposal('two')],{handle:()=>new Promise(resolve=>{finish=resolve;})});
 fireEvent.click(await screen.findByRole('button',{name:'Start 2 threads'}));await waitFor(()=>assert.equal(typeof finish,'function'));view.unmount();
 await act(async()=>{finish(proposal('one',{state:'started',canStart:false}));});assert.equal(calls.filter(c=>c.operation==='task_thread_start').length,1);
});
test('uncertain network response stops the rest of the batch',async()=>{
 const {calls}=mount([proposal('one'),proposal('two')],{handle:async()=>{throw Error('Connection ended before confirmation');}});
 fireEvent.click(await screen.findByRole('button',{name:'Start 2 threads'}));await screen.findByRole('alert');
 assert.match(screen.getByRole('alert').textContent,/before confirmation/);assert.equal(calls.filter(c=>c.operation==='task_thread_start').length,1);
 assert.equal(screen.getAllByRole('button',{name:'Start thread'}).length,2);
});
test('changed access cannot start, while a queued child opens without another start',async()=>{
 const opened=[];const {calls}=mount([proposal('one',{canStart:false,detail:'Project access changed. Ask for a fresh proposal.'}),proposal('two',{state:'queued',canStart:false,channelId:'child-two'})],{onOpenChannel:id=>opened.push(id)});
 await screen.findByText(/Project access changed/);assert.equal(screen.queryByRole('button',{name:'Start thread'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Open thread'}));await waitFor(()=>assert.deepEqual(opened,['child-two']));assert.equal(calls.filter(c=>c.operation==='task_thread_start').length,0);
});
test('child and source links use exact task identities',async()=>{
 const opened=[];mount([proposal('one',{state:'started',canStart:false,channelId:'child',taskId:'child-task'})],{onOpenTask:(...args)=>opened.push(args)});
 fireEvent.click(await screen.findByRole('button',{name:'Open thread'}));await waitFor(()=>assert.deepEqual(opened,[['child','child-task']]));cleanup();
 render(h(ChannelNavigationProvider,{channels:[],onOpenTask:(...args)=>opened.push(args)},h(ThreadSourceLink,{source:{channelId:'parent-thread',taskId:'parent-task',name:'Client tickets',purpose:'One ticket only'}})));
 fireEvent.click(screen.getByRole('button',{name:'Client tickets'}));assert.deepEqual(opened[1],['parent-thread','parent-task']);
});
