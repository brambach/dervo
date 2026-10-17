import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLTextAreaElement','HTMLButtonElement','HTMLDetailsElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {BugsList}=await import('./BugsList.tsx');
const {DervoFeedbackList}=await import('./DervoFeedbackList.tsx');
const {NextUp}=await import('./NextUp.tsx');
const {itemLine,reportLine,shippedLine,updatedLine,shortDay}=await import('./nextUpPlan.ts');
after(()=>dom.window.close());
afterEach(cleanup);

/* Next up (b16/lifecycle) replaces Bugs and ideas and Dervo reports. The host reads and edits NEXT-UP.md
   (backend/test_next_up.py); these check what the page shows and what it sends. */
const item=(key,title,extra={})=>({key,title,day:null,number:null,marks:[],thread:null,thread_id:null,thread_state:null,sent:[],notes:[],...extra});
const VIEW={exists:true,file:'NEXT-UP.md',revision:'a'.repeat(64),modified:Date.now()/1000-20*60,name:'dervo app',other:[],reports_here:true,reports_chosen:true,admin:true,
 sections:{
  next:[item('n1','Hand off after a messy stop',{day:'25 Sep',notes:['swarnim.eth on X · you said yes on 25 Sep']}),item('n2','Tester reports in admin Dervo',{day:'24 Sep'})],
  progress:[item('p1','Windows platform seam',{day:'Today',thread:'Windows: the seam and first build',thread_id:'t-win',thread_state:'running'})],
  undecided:[item('u1','Settings says thinking is High, but runs go out at Medium',{day:'25 Sep',marks:['You, with ⌘⇧B'],sent:[['Screen','Settings, Model'],['Build','0.1.0-beta.15.1']]}),
   item('u2','Model settings per person',{day:'24 Sep',marks:['You']})]},
 shipped:[{version:'0.1.0-beta.15.1',day:'25 Sep',items:[item('s1','Move to Applications before updating')]},
  {version:'0.1.0-beta.15',day:null,items:['Run buttons','The icon picker','Home’s agent picker','Replies that run'].map((t,i)=>item(`s${i+2}`,t))}]};
const REPORTS={state:'ready',unseen:1,project:'p1',reports:[
 {id:'71000000-0000-0000-0000-000000000001',category:'bug',message:'The update failed.',created_at:Date.parse('2026-09-25T12:00:00Z')/1000,tester:'Harrison',app_version:'0.1.0-beta.15',route:'settings',provider:null,seen:false,added:false,shipped_in:null},
 {id:'71000000-0000-0000-0000-000000000002',category:'confusing',message:'Where did my answer go?',created_at:Date.parse('2026-09-24T12:00:00Z')/1000,tester:null,app_version:'0.1.0-beta.15',route:'conversation',provider:null,seen:true,added:true,shipped_in:'0.1.0-beta.16'}]};

function host(view=VIEW,reports=REPORTS){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{
  calls.push([operation,args]);
  if(operation==='next_up_read')return view;
  if(operation==='tester_reports')return reports;
  if(['next_up_move','next_up_ship','next_up_add','tester_report_add'].includes(operation))return view;
  if(operation==='next_up_work')return {...view,thread:{id:'t-new',name:'Tester reports in admin Dervo'},text:'Work on this'};
  if(operation==='tester_report_mark')return {id:args.reportId,seen:true};
  if(operation==='project_file_open')return {opened:true};
  throw new Error(`no fake for ${operation}`);
 }};
 return calls;
}
const mount=(Component,props={})=>render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
 React.createElement(Component,{projectId:'p1',profileId:'cs',projectName:'dervo app',open:true,onClose(){},...props})));

test('closed, the page is nothing and asks the host for nothing',()=>{
 const calls=host();
 const {container}=mount(NextUp,{open:false});
 assert.equal(container.innerHTML,'');
 assert.equal(calls.length,0);
});

test('Bugs and ideas and Dervo reports now open Next up',async()=>{
 host();
 mount(BugsList);
 assert.ok(await screen.findByRole('heading',{level:1,name:'Next up'}));
 cleanup();
 mount(DervoFeedbackList);
 assert.ok(await screen.findByRole('heading',{level:1,name:'Next up'}));
 assert.equal(screen.queryByText('Bugs and ideas'),null);
});

test('the sections read in the design’s order, with counts, numbers, the thread and the folded versions',async()=>{
 host();
 const opened=[];
 mount(NextUp,{onOpenThread:id=>opened.push(id)});
 await screen.findByText('Hand off after a messy stop');
 const titles=screen.getAllByRole('heading',{level:2}).map(h=>h.textContent);
 assert.deepEqual(titles,['Next','In progress','Not decided','Tester reports','Shipped']);
 assert.ok(screen.getByText('NEXT-UP.md'));
 assert.ok(screen.getByText('Updated 20 min ago'));
 assert.ok(screen.getByText('swarnim.eth on X · you said yes on 25 Sep'));
 fireEvent.click(screen.getByRole('button',{name:'Windows: the seam and first build'}));
 assert.deepEqual(opened,['t-win']);
 assert.ok(screen.getByText('In 0.1.0-beta.15'));
 assert.ok(screen.getByText('4 items · Run buttons, the icon picker, home’s agent picker and more'));
 assert.ok(await screen.findByText('1 new'),'tester reports load after the plan');
});

test('Move to Next and Drop send the key and the revision the page read',async()=>{
 const calls=host();
 mount(NextUp);
 const row=(await screen.findByText('Model settings per person')).closest('.dv-row');
 fireEvent.click(within(row).getByRole('button',{name:'Move to Next'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='next_up_move')));
 assert.deepEqual(calls.find(([op])=>op==='next_up_move')[1],{projectId:'p1',expectedRevision:'a'.repeat(64),key:'u2',to:'next'});
 const sent=screen.getByText('Settings says thinking is High, but runs go out at Medium').closest('.dv-row');
 fireEvent.click(within(sent).getByRole('button',{name:'Sent with it'}));
 assert.ok(within(sent).getByText('Settings, Model'));
 fireEvent.click(within(sent).getByRole('button',{name:'Drop'}));
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='next_up_move'&&a.to==='drop'&&a.key==='u1')));
});

test('Shipped asks for the version, refuses a bad one, and sends a good one',async()=>{
 const calls=host();
 mount(NextUp);
 const row=(await screen.findByText('Hand off after a messy stop')).closest('.dv-row');
 fireEvent.click(within(row).getByRole('button',{name:'Edit'}));
 fireEvent.click(within(row).getByRole('button',{name:'Shipped…'}));
 const field=within(row).getByRole('textbox',{name:'Version Hand off after a messy stop shipped in'});
 fireEvent.change(field,{target:{value:'beta; rm'}});
 fireEvent.click(within(row).getByRole('button',{name:'Mark shipped'}));
 assert.ok(await screen.findByRole('alert'));
 assert.equal(calls.some(([op])=>op==='next_up_ship'),false);
 fireEvent.change(field,{target:{value:'0.1.0-beta.16'}});
 fireEvent.keyDown(field,{key:'Enter'});
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='next_up_ship')?.[1],{projectId:'p1',expectedRevision:'a'.repeat(64),key:'n1',version:'0.1.0-beta.16'}));
});

test('Start a thread hands the new thread and its request to the app',async()=>{
 host();
 const worked=[];
 mount(NextUp,{onWork:(id,text)=>worked.push([id,text])});
 const row=(await screen.findByText('Tester reports in admin Dervo')).closest('.dv-row');
 fireEvent.click(within(row).getByRole('button',{name:'Start a thread'}));
 await waitFor(()=>assert.deepEqual(worked,[['t-new','Work on this']]));
});

test('the add line puts a title under Not decided and clears',async()=>{
 const calls=host();
 mount(NextUp);
 const field=await screen.findByRole('textbox',{name:'Add to Not decided'});
 fireEvent.change(field,{target:{value:'Pin threads from ⌘K'}});
 fireEvent.keyDown(field,{key:'Enter'});
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='next_up_add')?.[1],{projectId:'p1',expectedRevision:'a'.repeat(64),title:'Pin threads from ⌘K',note:null}));
 await waitFor(()=>assert.equal(field.value,''));
});

test('tester reports: a first name, the build and the screen; Add to Next up and Mark seen on new ones only',async()=>{
 const calls=host();
 mount(NextUp);
 const fresh=(await screen.findByText('The update failed.')).closest('.dv-row');
 assert.ok(within(fresh).getByText('Harrison · 0.1.0-beta.15 · Settings · 25 Sep'));
 assert.ok(within(fresh).getByText('Bug'));
 const old=screen.getByText('Where did my answer go?').closest('.dv-row');
 assert.ok(within(old).getByText('A tester · 0.1.0-beta.15 · Thread · 24 Sep · shipped in 0.1.0-beta.16'));
 assert.equal(within(old).queryByRole('button',{name:'Add to Next up'}),null);
 fireEvent.click(within(fresh).getByRole('button',{name:'Mark seen'}));
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='tester_report_mark'&&a.seen===true)));
 fireEvent.click(within(fresh).getByRole('button',{name:'Add to Next up'}));
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='tester_report_add'&&a.reportId==='71000000-0000-0000-0000-000000000001')));
});

test('testers never see tester reports, and an admin elsewhere is offered this project once',async()=>{
 host({...VIEW,admin:false,reports_here:false});
 mount(NextUp);
 await screen.findByText('Hand off after a messy stop');
 assert.equal(screen.queryByText('Tester reports'),null);
 assert.equal(screen.queryByRole('button',{name:/Show them here/}),null);
 cleanup();
 const calls=host({...VIEW,reports_here:false,reports_chosen:false});
 mount(NextUp);
 fireEvent.click(await screen.findByRole('button',{name:'Show them here'}));
 await waitFor(()=>assert.ok(calls.some(([op,a])=>op==='next_up_reports_here'&&a.here===true)));
 assert.equal(calls.some(([op])=>op==='tester_reports'),false,'nothing is read before you choose');
});

test('no file yet: an empty page that says where the plan will live',async()=>{
 host({...VIEW,exists:false,revision:null,modified:null,sections:{next:[],progress:[],undecided:[]},shipped:[],admin:false});
 mount(NextUp);
 assert.ok(await screen.findByText('Nothing planned yet'));
 assert.equal(screen.queryByRole('button',{name:'Open the file'}),null);
 assert.ok(screen.getByRole('textbox',{name:'Add to Not decided'}));
});

test('the lines under each row',()=>{
 assert.equal(itemLine({marks:['You, with ⌘⇧B'],day:'25 Sep',notes:[]}),'You, with ⌘⇧B · 25 Sep');
 assert.equal(itemLine({marks:['Idea','Home, from your message'],day:null,notes:['Asked on Home']}),'Home, from your message · Asked on Home');
 assert.equal(reportLine({tester:null,app_version:null,route:'workspace',created_at:Date.parse('2026-09-23T12:00:00Z')/1000,added:true,shipped_in:null}),'A tester · Home · 23 Sep · added to Next up');
 assert.equal(shippedLine({version:'x',day:null,items:[item('a','Only one')]}),'1 item · Only one');
 assert.equal(shippedLine({version:'x',day:null,items:[item('a','One'),item('b','Two')]}),'2 items · One and two');
 const now=Date.parse('2026-09-25T10:00:00Z');
 assert.equal(updatedLine(now/1000-30,now),'Updated just now');
 assert.equal(updatedLine(now/1000-3*3600,now),'Updated 3 hours ago');
 assert.equal(updatedLine(now/1000-3*86400,now),`Updated ${shortDay(now/1000-3*86400)}`);
 assert.equal(updatedLine(null,now),'');
});
