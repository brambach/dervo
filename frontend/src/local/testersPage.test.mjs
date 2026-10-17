import assert from 'node:assert/strict';
import {test,after,afterEach,beforeEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','FocusEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {TestersPage,endDate,changeLine}=await import('./TestersPage.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const h=React.createElement;

/** Testers, for admins only (settings/testers.html): asking, approved with end dates, revoked, and the changes. The host and
 * the server check the caller is an admin; this page only ever calls the three admin operations. */
const DAY=86400,now=new Date(2026,8,23,12,0).getTime()/1000;
// Render the shared records on their fixture day. Only Date is mocked.
beforeEach(t=>t.mock.timers.enable({apis:['Date'],now:now*1000}));
const person=(user_id,email,status,extra={})=>({user_id,email,status,expires_at:null,signed_up_at:now-DAY,updated_at:now-DAY,is_admin:false,is_you:false,...extra});
const listed=()=>({state:'ready',busy:null,detail:null,more:true,people:[
 person('u-new','jordan@example.com','pending',{signed_up_at:now-60}),person('u-old','maya@example.com','pending',{signed_up_at:now-3*DAY}),
 person('u-me','bryce@trydervo.com','approved',{is_admin:true,is_you:true}),person('u-sam','sam@example.com','approved'),person('u-kai','kai@example.com','revoked')],
 audit:[{id:3,changed_at:now-100,actor_id:'u-me',actor_email:'bryce@trydervo.com',subject_id:'u-sam',subject_email:'sam@example.com',previous_status:'pending',new_status:'approved',expires_at:null,by_you:true},
  {id:2,changed_at:now-200,actor_id:'u-other',actor_email:'alex@trydervo.com',subject_id:'u-kai',subject_email:'kai@example.com',previous_status:'approved',new_status:'revoked',expires_at:null,by_you:false}]});
function mount(){
 const calls=[];
 let state=listed();
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  if(command!=='local_request')return null;
  calls.push({operation:args.operation,args:args.args});
  if(args.operation==='dervo_testers_older')state={...state,audit:[...state.audit,{id:1,changed_at:now-DAY*5,actor_id:'u-me',actor_email:'bryce@trydervo.com',subject_id:'u-sam',subject_email:'sam@example.com',previous_status:null,new_status:'pending',expires_at:null,by_you:true}],more:false};
  return state;
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(h(QueryClientProvider,{client},h(TestersPage)));
 return calls;
}
const section=id=>document.getElementById(id).closest('section');
const rowOf=email=>[...document.querySelectorAll('.dv-set')].find(row=>row.querySelector('.dv-set-name')?.textContent===email);

test('a list the server cut at 500 says so, and a whole list says nothing',async()=>{
 // Security review, 19 Sep, finding 6: past 500 memberships the oldest sign-ups dropped off and the page looked complete.
 mount();
 await screen.findByText('maya@example.com');
 assert.equal(screen.queryByTestId('testers-cut'),null);
 cleanup();
 const people=Array.from({length:500},(_,n)=>person(`u-${n}`,`p${n}@example.com`,'approved'));
 window.__TAURI_INTERNALS__={invoke:async(command)=>command==='local_request'?{...listed(),people,cut:true}:null};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(h(QueryClientProvider,{client},h(TestersPage)));
 const note=await screen.findByTestId('testers-cut');
 assert.match(note.textContent,/Showing the newest 500 sign-ups\. Anyone who signed up before them isn’t listed here/);
});

test('people asking to join come oldest first, with Approve and Decline, and each calls the admin operation once',async()=>{
 const calls=mount();
 await screen.findByText('maya@example.com');
 assert.deepEqual([...section('dv-testers-asking').querySelectorAll('.dv-set-name')].map(n=>n.textContent),['maya@example.com','jordan@example.com']);
 assert.match(section('dv-testers-asking').textContent,/Signed up today, /);
 const maya=screen.getByText('maya@example.com').closest('.dv-set');
 fireEvent.click(within(maya).getByRole('button',{name:'Approve'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.operation==='dervo_tester_set')));
 assert.deepEqual(calls.find(c=>c.operation==='dervo_tester_set').args,{userId:'u-old',status:'approved',expiresAt:null});
 fireEvent.click(within(screen.getByText('jordan@example.com').closest('.dv-set')).getByRole('button',{name:'Decline'}));
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='dervo_tester_set').length,2));
 assert.deepEqual(calls.filter(c=>c.operation==='dervo_tester_set')[1].args,{userId:'u-new',status:'revoked',expiresAt:null});
 assert.ok(calls.every(c=>['dervo_testers','dervo_tester_set','dervo_testers_older'].includes(c.operation)),'only the admin operations');
});

test('your own row has no actions; an end date is chosen on the card, and Revoke asks first',async()=>{
 const calls=mount();
 await screen.findByText('Admin · no end date');
 const me=rowOf('bryce@trydervo.com');
 assert.equal(within(me).queryAllByRole('button').length,0,'you can’t revoke yourself here');
 assert.ok(within(me).getByText('Admin · no end date'));
 fireEvent.click(within(rowOf('sam@example.com')).getByRole('button',{name:'End date'}));
 const card=screen.getByRole('group',{name:'End date for sam@example.com'});
 assert.match(card.textContent,/No end date\. They keep access until you revoke it\./);
 fireEvent.click(within(card).getByRole('radio',{name:'Two weeks'}));
 assert.match(card.textContent,/Access ends on \d+ [A-Z][a-z]{2}\. Their projects stay on their Mac/);
 fireEvent.click(within(card).getByRole('button',{name:'Save end date'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.operation==='dervo_tester_set')));
 const sent=calls.find(c=>c.operation==='dervo_tester_set').args;
 assert.deepEqual([sent.userId,sent.status],['u-sam','approved']);
 assert.ok(Math.abs(sent.expiresAt-endDate('two_weeks',''))<2,'the end of the day two weeks from today');
 await waitFor(()=>assert.ok(!screen.queryByRole('group',{name:'End date for sam@example.com'})));
 fireEvent.click(within(rowOf('sam@example.com')).getByRole('button',{name:'Revoke'}));
 const confirm=screen.getByRole('group',{name:'Revoke sam@example.com?'});
 assert.match(confirm.textContent,/They keep their projects, and you can approve them again\./);
 assert.equal(calls.filter(c=>c.operation==='dervo_tester_set').length,1,'nothing is revoked before the confirm');
 fireEvent.click(within(confirm).getByRole('button',{name:'Revoke'}));
 await waitFor(()=>assert.deepEqual(calls.filter(c=>c.operation==='dervo_tester_set')[1].args,{userId:'u-sam',status:'revoked',expiresAt:null}));
});

test('the revoked list is behind its count, and the changes read as plain dated lines, older on request',async()=>{
 const calls=mount();
 await screen.findByText('1 revoked');
 const lines=[...document.querySelectorAll('.dv-log')].map(l=>l.textContent);
 assert.match(lines[0],/^Today, .*You approved sam@example\.com\. No end date\.$/);
 assert.match(lines[1],/alex@trydervo\.com revoked kai@example\.com\.$/);
 fireEvent.click(screen.getByRole('button',{name:'Show older'}));
 await waitFor(()=>assert.equal(document.querySelectorAll('.dv-log').length,3));
 assert.ok(calls.find(c=>c.operation==='dervo_testers_older'));
 assert.ok(!screen.queryByRole('button',{name:'Show older'}));
 assert.equal(endDate('none',''),null);
 assert.ok(Number.isNaN(endDate('pick','')),'Pick a date needs a date');
 const declined={id:9,changed_at:now,actor_id:'x',actor_email:null,subject_id:'y',subject_email:'a@b.test',previous_status:'pending',new_status:'revoked',expires_at:null,by_you:false};
 const text=node=>{const box=document.createElement('div');render(h('div',null,node),{container:box});return box.textContent;};
 assert.equal(text(changeLine(declined)),'An admin declined a@b.test.');
});

test('Testers is admin only: the page and its nav row exist only for admins, and the nav counts who is asking',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/settingsPage==='testers'&&accountData\?\.membership\?\.is_admin===true&&<TestersPage\/>/);
 assert.match(shell,/const testers=useQuery\(\{\.\.\.testersQuery,enabled:settingsOpen&&accountData\?\.membership\?\.is_admin===true\}\);/,'nothing is asked of the server for anyone else');
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['dervo_testers','dervo_testers_older','dervo_tester_set'])assert.ok(bridge.includes(`"${op}"`),op);
});
