import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
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
const {WaitlistInvites,matchesSearch,resultLine,invitationLeft,BATCH}=await import('./WaitlistInvites.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const h=React.createElement;

/** The waitlist on the Testers page: pick people, read exactly who gets an email, send, and see each result. The website
 * and the account server decide who's an admin and what's a duplicate; this page only ever calls the two waitlist operations. */
const DAY=86400,now=Math.floor(Date.now()/1000);
const person=(email,status='waiting',extra={})=>({email,signed_up_at:now-5*DAY,status,invited_at:null,expires_at:null,delivery:null,...extra});
const preview={from:'Bryce <bryce@notify.trydervo.com>',reply_to:'bryce@trydervo.com',subject:'Your Dervo beta invitation',text:'Hey,\n\nGive Dervo a try. Sign in with [invited email].'};
const listed=()=>({email_preview:preview,state:'ready',detail:null,more:false,results:[],results_id:0,people:[
 person('ada@example.com'),person('bo@example.com'),person('cy@example.com'),
 person('dee@example.com','invited',{invited_at:now-DAY,expires_at:now+13*DAY,delivery:'sent'}),
 person('eli@example.com','invited',{invited_at:now-DAY,expires_at:now+13*DAY,delivery:'failed'}),
 person('flo@example.com','accepted'),person('gus@example.com','revoked')]});
function mount(start=listed()){
 const calls=[];
 let state=start;
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  if(command!=='local_request')return null;
  calls.push({operation:args.operation,args:args.args});
  if(args.operation==='dervo_waitlist_invite'){
   const {action,emails}=args.args;
   if(action==='add'){state={...state,results_id:state.results_id+1,results:emails.map(email=>({email,result:'added',expires_at:null})),people:[...state.people,...emails.map(email=>person(email,'waiting',{source:'admin'}))]};return state;}
   state={...state,results_id:state.results_id+1,
    results:emails.map(email=>email==='bo@example.com'?{email,result:'send_failed',expires_at:null}:{email,result:action==='revoke'?'revoked':'sent',expires_at:action==='revoke'?null:now+14*DAY}),
    people:state.people.map(p=>emails.includes(p.email)?{...p,status:action==='revoke'?'revoked':'invited',delivery:p.email==='bo@example.com'?'failed':'sent',invited_at:now,expires_at:now+14*DAY}:p)};
  }
  return state;
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(h(QueryClientProvider,{client},h(WaitlistInvites)));
 return calls;
}
const rowOf=email=>[...document.querySelectorAll('.dv-set')].find(row=>row.querySelector('.dv-set-name')?.textContent.trim()===email);
const sends=calls=>calls.filter(c=>c.operation==='dervo_waitlist_invite');

test('waiting people show their email and signup day, and nothing is sent until the exact list is confirmed',async()=>{
 const calls=mount();
 await screen.findByLabelText('Select ada@example.com');
 assert.match(document.getElementById('dv-waitlist').closest('section').textContent,/3 waiting/);
 const ada=screen.getByLabelText('Select ada@example.com').closest('.dv-waitlist-row');
 assert.match(ada.textContent,/ada@example\.com.*Website · /,'a compact row: the email, then where it came from and the day');
 assert.match(ada.querySelector('.dv-waitlist-meta').getAttribute('title'),/Joined the website waitlist /,'the full sentence stays one hover away');
 assert.equal(screen.getByRole('button',{name:'Invite selected'}).disabled,true,'nothing selected yet');
 fireEvent.click(screen.getByLabelText('Select ada@example.com'));
 fireEvent.click(screen.getByLabelText('Select bo@example.com'));
 fireEvent.click(screen.getByRole('button',{name:'Invite selected'}));
 const card=screen.getByRole('group',{name:'Invite 2 people?'});
 assert.deepEqual([...within(card).getByTestId('waitlist-confirm-list').querySelectorAll('li')].map(li=>li.textContent),['ada@example.com','bo@example.com']);
 assert.match(card.textContent,/tied to that address/);
 assert.equal(sends(calls).length,0,'opening the confirmation sends nothing');
 fireEvent.click(within(card).getByRole('button',{name:'Cancel'}));
 assert.equal(sends(calls).length,0);
 assert.equal(screen.getByLabelText('Select ada@example.com').checked,true,'cancelling keeps the selection');

 fireEvent.click(screen.getByRole('button',{name:'Invite selected'}));
 fireEvent.click(screen.getByRole('button',{name:'Send 2 invitations'}));
 const results=await screen.findByTestId('waitlist-results');
 assert.deepEqual(sends(calls).map(c=>c.args),[{action:'invite',emails:['ada@example.com','bo@example.com']}]);
 assert.match(within(results).getByText('ada@example.com').closest('.dv-set').textContent,/Invitation sent\. It expires .*Done/);
 assert.match(within(results).getByText('bo@example.com').closest('.dv-set').textContent,/Access is recorded.*delivery wasn’t confirmed.*same invitation.*Check/);
 fireEvent.click(screen.getByRole('button',{name:'Done'}));
 assert.equal(screen.queryByTestId('waitlist-results'),null);
 assert.ok(calls.every(c=>['dervo_waitlist','dervo_waitlist_invite'].includes(c.operation)),'only the waitlist operations');
});

test('an invited person has no checkbox: a second email takes Resend and its own confirmation',async()=>{
 const calls=mount();
 await screen.findByLabelText('Select ada@example.com');
 assert.equal(screen.queryByLabelText('Select dee@example.com'),null);
 assert.match(rowOf('dee@example.com').textContent,/Invited .* · expires /);
 assert.equal(within(rowOf('dee@example.com')).queryByRole('button',{name:'Retry email'}),null,'a delivered email has nothing to retry');
 fireEvent.click(within(rowOf('dee@example.com')).getByRole('button',{name:'Resend'}));
 assert.equal(sends(calls).length,0);
 fireEvent.click(within(screen.getByRole('group',{name:'Email dee@example.com again?'})).getByRole('button',{name:'Resend'}));
 await waitFor(()=>assert.deepEqual(sends(calls).map(c=>c.args),[{action:'resend',emails:['dee@example.com']}]));
});

test('an email that didn’t send can be sent again with the same invitation, and an invitation can be withdrawn',async()=>{
 const calls=mount();
 await screen.findByLabelText('Select ada@example.com');
 assert.match(rowOf('eli@example.com').textContent,/Email delivery isn’t confirmed/);
 fireEvent.click(within(rowOf('eli@example.com')).getByRole('button',{name:'Retry email'}));
 const retry=screen.getByRole('group',{name:'Retry the invitation email?'});
 assert.match(retry.textContent,/original email and invitation expiry/);
 assert.equal(within(retry).queryByText('Read the invitation email'),null,'current template is not passed off as the original email');
 assert.equal(sends(calls).length,0);
 fireEvent.click(within(retry).getByRole('button',{name:'Retry email'}));
 await waitFor(()=>assert.deepEqual(sends(calls).map(c=>c.args),[{action:'invite',emails:['eli@example.com']}]));
 fireEvent.click(await screen.findByRole('button',{name:'Done'}));
 fireEvent.click(within(rowOf('dee@example.com')).getByRole('button',{name:'Withdraw'}));
 fireEvent.click(within(screen.getByRole('group',{name:'Withdraw the invitation for dee@example.com?'})).getByRole('button',{name:'Withdraw'}));
 await waitFor(()=>assert.deepEqual(sends(calls).at(-1).args,{action:'revoke',emails:['dee@example.com']}));
});

test('a failed email whose invitation is too old to retry still has Resend, behind its own confirmation',async()=>{
 const calls=mount();
 await screen.findByLabelText('Select ada@example.com');
 fireEvent.click(within(rowOf('eli@example.com')).getByRole('button',{name:'Resend'}));
 assert.equal(sends(calls).length,0);
 const card=screen.getByRole('group',{name:'Email eli@example.com again?'});
 assert.match(card.textContent,/earlier email may already have arrived/);
 fireEvent.click(within(card).getByRole('button',{name:'Resend'}));
 await waitFor(()=>assert.deepEqual(sends(calls).map(c=>c.args),[{action:'resend',emails:['eli@example.com']}]));
});

test('a batch stops at ten, and a list that fails to load says so',async()=>{
 const many={...listed(),people:Array.from({length:BATCH+2},(_,n)=>person(`p${n}@example.com`))};
 mount(many);
 await screen.findByLabelText('Select p0@example.com');
 for(let n=0;n<BATCH;n++)fireEvent.click(screen.getByLabelText(`Select p${n}@example.com`));
 assert.equal(screen.getByLabelText(`Select p${BATCH}@example.com`).disabled,true);
 assert.equal(screen.getByLabelText('Select p0@example.com').disabled,false,'a chosen person can still be unticked');
 cleanup();
 mount({...listed(),state:'error',detail:'Dervo couldn’t load the waitlist. Check your connection, then try again.',people:[]});
 assert.match((await screen.findByRole('alert')).textContent,/couldn’t load the waitlist/);
 const section=document.getElementById('dv-waitlist').closest('section');
 assert.doesNotMatch(section.textContent,/Nobody is waiting|0 waiting/,'a failed load isn’t an empty waitlist');
});

test('an empty list only says nobody is waiting once it has loaded, and Refresh asks the website again',async()=>{
 mount({...listed(),state:'loading',people:[]});
 assert.match((await screen.findByRole('status')).textContent,/Loading the waitlist/);
 assert.doesNotMatch(document.getElementById('dv-waitlist').closest('section').textContent,/Nobody is waiting|0 waiting/);
 cleanup();
 const calls=mount({...listed(),people:[]});
 await screen.findByText(/Nobody is waiting/);
 fireEvent.click(screen.getByRole('button',{name:'Refresh'}));
 await waitFor(()=>assert.deepEqual(calls.at(-1),{operation:'dervo_waitlist',args:{reload:true}}));
 cleanup();
 mount();
 await screen.findByLabelText('Select ada@example.com');
 assert.ok(screen.getByRole('button',{name:'Refresh'}),'new signups can be fetched without leaving the page');
});

test('every result reads as a sentence, and the bridge allows exactly these operations',()=>{
 for(const result of ['added','already_listed','delivery_review','sent','revoked','already_invited','already_accepted','already_approved','member_revoked','needs_resend','not_on_waitlist','rate_limited',
  'send_budget_reached','send_failed','grant_failed','not_admin','failed'])assert.match(resultLine({email:'a@example.com',result,expires_at:null}),/\.$/,result);
 assert.match(resultLine({email:'a@example.com',result:'already_invited',expires_at:null}),/nothing was sent\. Use Resend/);
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['dervo_waitlist','dervo_waitlist_invite'])assert.ok(bridge.includes(`"${op}"`),op);
});


test('adding a direct address sends only add, then a separate selection and confirmation invites it',async()=>{
 const calls=mount();
 const input=await screen.findByRole('textbox',{name:'Email address'});
 fireEvent.change(input,{target:{value:'NEW@Example.com'}});
 fireEvent.submit(input.closest('form'));
 await screen.findByLabelText('Select new@example.com');
 assert.deepEqual(sends(calls).map(c=>c.args),[{action:'add',emails:['new@example.com']}]);
 assert.match(screen.getByTestId('waitlist-results').textContent,/No email was sent and access wasn’t approved/);
 fireEvent.click(screen.getByRole('button',{name:'Done'}));
 const added=screen.getByLabelText('Select new@example.com').closest('.dv-waitlist-row');
 assert.match(added.textContent,/Added · /);assert.match(added.querySelector('.dv-waitlist-meta').getAttribute('title'),/Added by an admin/);
 fireEvent.click(screen.getByLabelText('Select new@example.com'));
 fireEvent.click(screen.getByRole('button',{name:'Invite selected'}));
 const card=screen.getByRole('group',{name:'Invite 1 person?'});
 assert.match(card.textContent,/Bryce <bryce@notify.trydervo.com>/);
 assert.match(card.textContent,/Replies to: bryce@trydervo.com/);
 assert.match(card.textContent,/Sign in with \[invited email\]/);
 assert.equal(sends(calls).length,1,'reviewing the email still sends nothing');
 fireEvent.click(within(card).getByRole('button',{name:'Send 1 invitation'}));
 await waitFor(()=>assert.deepEqual(sends(calls).at(-1).args,{action:'invite',emails:['new@example.com']}));
});

test('without a server email preview adding remains available but sending is disabled',async()=>{
 const calls=mount({...listed(),email_preview:null});
 await screen.findByLabelText('Select ada@example.com');
 fireEvent.click(screen.getByLabelText('Select ada@example.com'));
 assert.equal(screen.getByRole('button',{name:'Invite selected'}).disabled,true);
 assert.equal(within(rowOf('eli@example.com')).getByRole('button',{name:'Retry email'}).disabled,true);
 const input=screen.getByRole('textbox',{name:'Email address'});
 fireEvent.change(input,{target:{value:'new@example.com'}});
 assert.equal(screen.getByRole('button',{name:'Add address'}).disabled,false);
 assert.equal(sends(calls).length,0);
});

test('one search narrows every group by email, keeps what you picked, and Escape clears it',async()=>{
 const calls=mount();
 const box=await screen.findByRole('searchbox',{name:'Search the waitlist'});
 assert.equal(box.getAttribute('placeholder'),'Search 7 addresses');
 fireEvent.click(screen.getByLabelText('Select ada@example.com'));
 fireEvent.change(box,{target:{value:'BO'}});
 assert.deepEqual([...document.querySelectorAll('.dv-waitlist-email')].map(e=>e.textContent),['bo@example.com'],'case doesn’t matter');
 assert.equal(screen.getByRole('status').textContent,'1 match');
 assert.match(document.body.textContent,/1 selected, 1 hidden by the search\./,'ada stays picked while hidden');
 fireEvent.change(box,{target:{value:'ee@'}});
 assert.equal(document.querySelector('.dv-waitlist'),null);
 assert.match(document.body.textContent,/Nobody waiting matches “ee@”\./);
 assert.match(document.body.textContent,/1 invited, not signed in yet/,'the invited group narrows too');
 assert.ok(rowOf('dee@example.com'));assert.equal(rowOf('eli@example.com'),undefined);
 fireEvent.change(box,{target:{value:'gus'}});
 assert.equal(rowOf('gus@example.com').closest('details').open,true,'a folded group opens while it holds a match');
 fireEvent.keyDown(box,{key:'Escape'});
 assert.equal(box.value,'');
 assert.equal(document.querySelectorAll('.dv-waitlist-email').length,3);
 assert.equal(screen.getByLabelText('Select ada@example.com').checked,true);
 assert.equal(sends(calls).length,0,'searching sends nothing');
 assert.equal(matchesSearch({email:'Ada@Example.com'},'  ada@ '),true);
 assert.equal(matchesSearch({email:'ada@example.com'},''),true);
 assert.equal(matchesSearch({email:'ada@example.com'},'bo'),false);
});

test('the invitation card opens under Invite selected, and a retry opens in place of its own row',async()=>{
 mount();
 fireEvent.click(await screen.findByLabelText('Select ada@example.com'));
 const button=screen.getByRole('button',{name:'Invite selected'});
 fireEvent.click(button);
 const card=screen.getByRole('group',{name:'Invite 1 person?'});
 const list=document.querySelector('.dv-waitlist');
 assert.ok(list.compareDocumentPosition(card)&Node.DOCUMENT_POSITION_FOLLOWING,'below the list, next to the button, so nothing needs scrolling up');
 assert.ok(button.compareDocumentPosition(card)&Node.DOCUMENT_POSITION_FOLLOWING);
 fireEvent.click(within(card).getByRole('button',{name:'Cancel'}));
 fireEvent.click(within(rowOf('eli@example.com')).getByRole('button',{name:'Retry email'}));
 const retry=screen.getByRole('group',{name:'Retry the invitation email?'});
 assert.equal(rowOf('eli@example.com'),undefined,'the card takes the row’s place');
 assert.ok(retry.closest('details'),'inside the invited group, where Retry was pressed');
});

test('invitationLeft says what is left of an invitation, in the words the row uses',()=>{
 const at=Date.UTC(2026,8,30,12,0)/1000,ms=at*1000;
 assert.equal(invitationLeft(null,ms),null);
 const open=invitationLeft(at+13*DAY,ms);
 assert.equal(open.state,'open');assert.equal(open.flag,undefined);assert.match(open.words,/^expires \d+ [A-Z][a-z]{2}$/);
 assert.equal(invitationLeft(at+7*DAY+1,ms).state,'open','a week and a second is not yet soon');
 const week=invitationLeft(at+7*DAY,ms);
 assert.equal(week.state,'soon');assert.equal(week.flag,'Expires in 7 days');assert.match(week.words,/^expires \d+ [A-Z][a-z]{2}, in 7 days$/);
 assert.equal(invitationLeft(at+2*DAY+1,ms).flag,'Expires in 3 days','part of a day counts as a day');
 assert.equal(invitationLeft(at+5*3600,ms).flag,'Expires within a day');
 assert.match(invitationLeft(at+5*3600,ms).words,/^expires (today|\d+ [A-Z][a-z]{2})$/);
 const gone=invitationLeft(at-DAY,ms);
 assert.equal(gone.state,'expired');assert.equal(gone.flag,'Expired');assert.match(gone.words,/^expired \d+ [A-Z][a-z]{2}$/);
 assert.equal(invitationLeft(at,ms).state,'expired','the second it runs out');
});

test('an invitation about to lapse, or already gone, is flagged and listed first, and Resend says what it does',async()=>{
 const calls=mount({...listed(),people:[
  person('later@example.com','invited',{invited_at:now-DAY,expires_at:now+13*DAY,delivery:'sent'}),
  person('gone@example.com','invited',{invited_at:now-15*DAY,expires_at:now-DAY,delivery:'sent'}),
  person('near@example.com','invited',{invited_at:now-11*DAY,expires_at:now+3*DAY-60,delivery:'sent'})]});
 await screen.findByText('near@example.com');
 const order=[...document.querySelectorAll('.dv-set-name')].map(n=>n.textContent).filter(t=>/@example\.com$/.test(t));
 assert.deepEqual(order,['gone@example.com','near@example.com','later@example.com'],'the lapsed one, then the one about to, then the rest');
 assert.match(rowOf('near@example.com').textContent,/Invited .* · expires .*, in 3 days.*Expires in 3 days/);
 assert.match(rowOf('gone@example.com').textContent,/Invited .* · expired .*Expired/);
 assert.doesNotMatch(rowOf('later@example.com').textContent,/Expires in|Expired/,'a fortnight out needs no flag');
 assert.match(document.body.textContent,/3 invited, not signed in yet · 1 expires within a week, 1 expired/);
 assert.match(document.body.textContent,/can still sign in and wait under Asking to join/);
 fireEvent.click(within(rowOf('gone@example.com')).getByRole('button',{name:'Resend'}));
 const card=screen.getByRole('group',{name:'Email gone@example.com again?'});
 assert.match(card.textContent,/Their invitation has run out/);
 assert.doesNotMatch(card.textContent,/earlier email may already have arrived/);
 assert.equal(sends(calls).length,0,'asking sends nothing');
 fireEvent.click(within(card).getByRole('button',{name:'Cancel'}));
 fireEvent.click(within(rowOf('near@example.com')).getByRole('button',{name:'Resend'}));
 assert.match(screen.getByRole('group',{name:'Email near@example.com again?'}).textContent,/earlier email may already have arrived/);
});

test('with no invitation close to running out, the invited list carries no warning',async()=>{
 mount();
 await screen.findByLabelText('Select ada@example.com');
 const text=document.body.textContent;
 assert.match(text,/2 invited, not signed in yet(?! ·)/);
 assert.doesNotMatch(text,/can still sign in and wait|Expires in|Expired/);
});

