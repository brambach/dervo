import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body><button id="line">Claude 64%</button></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent','FocusEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);window.requestAnimationFrame=globalThis.requestAnimationFrame;
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within}=await import('@testing-library/react');
const {UsagePopover,accountMeter,resetText}=await import('./UsagePopover.tsx');
after(()=>dom.window.close());afterEach(cleanup);
const h=React.createElement;

/** Usage as a popover beside the sidebar's usage lines (settings/usage-b.html), which replaced the Usage dialog. */
const at=(day,hour,minute)=>Math.floor(new Date(2026,8,day,hour,minute).getTime()/1000);
const now=new Date(2026,8,19,9,0).getTime();
const account=extra=>({profile_id:'a',provider:'claude',identity:'me@example.test',method:'subscription',name:null,monthly_limit_usd:null,runs_today:18,tokens_today:0,cost_today_usd:null,
 cost_month_usd:null,runs_month:40,resets_at:null,window_mins:10080,percent:36,low:false,basis:'rate_limits',rate_limits:{},...extra});

test('each account says what is left, the limit and when it resets, from what the provider reported',()=>{
 assert.equal(resetText(at(19,14,40),now),'2:40 PM','today: the time alone');
 assert.equal(resetText(at(23,22,0),now),'Wed 10:00 PM','later: the weekday and time');
 assert.equal(resetText(null,now),'');
 assert.deepEqual(Object.fromEntries(Object.entries(accountMeter(account({resets_at:at(23,22,0)}),now)).filter(([k])=>k!=='title')),
  {percent:64,value:'64% left',caption:'Weekly limit · resets Wed 10:00 PM'});
 assert.equal(accountMeter(account({window_mins:300,resets_at:at(19,14,40),percent:81,low:true}),now).caption,'5-hour limit · resets 2:40 PM · Low');
 const key=accountMeter(account({method:'api_key',basis:'spend_limit',monthly_limit_usd:50,cost_month_usd:12.5,percent:25,window_mins:null}),now);
 assert.deepEqual([key.percent,key.value,key.caption],[75,'$37.50 left','$12.50 of $50 this month']);
 const none=accountMeter(account({percent:null,basis:'runs'}),now);
 assert.equal(none.percent,null,'no bar when the provider has reported no allowance');
 assert.equal(none.caption,'18 runs today. Claude Code reports what’s left during its next run.');
 assert.match(accountMeter(account({cost_month_usd:6.1}),now).title,/40 runs this month, about \$6\.10 by the provider’s estimate\./);
});

test('the popover lists accounts by name under their provider and hands off to Connected accounts',async()=>{
 const anchor={current:{getBoundingClientRect:()=>new DOMRect(232,600,0,120)}};
 const line={current:document.getElementById('line')};
 let open=true,managed=0;
 const summary={at:0,accounts:[account({profile_id:'cs',name:'Personal',resets_at:at(23,22,0)}),account({profile_id:'cw',name:'Acme',percent:19,window_mins:300,runs_today:4}),
  account({profile_id:'cx',provider:'codex',name:null,rate_limits:{planType:'plus'},percent:41})]};
 const view=render(h(UsagePopover,{open,onOpenChange:next=>{open=next;view.rerender(h(UsagePopover,{open,onOpenChange(){},anchor,returnFocus:line,summary,failed:false,onRetry(){},onManage(){managed++;}}));},
  anchor,returnFocus:line,summary,failed:false,onRetry(){},onManage(){managed++;}}));
 const pop=await screen.findByTestId('usage-popover');
 assert.deepEqual(within(pop).getAllByRole('group').map(g=>g.getAttribute('aria-label')),['Claude','Codex']);
 assert.deepEqual([...pop.querySelectorAll('.dv-u b')].map(b=>b.textContent),['Personal','Acme','ChatGPT Plus'],'the same names as the sidebar and picker');
 assert.ok(within(pop).getByText('81% left'));
 assert.match(pop.textContent,/Weekly limit · resets .* · 18 runs today/);
 await waitFor(()=>assert.equal(document.activeElement,within(pop).getByRole('heading',{name:'Usage'})),'focus moves into the popover');
 fireEvent.click(within(pop).getByRole('button',{name:'Connected accounts'}));
 assert.equal(managed,1);
 fireEvent.keyDown(document.activeElement,{key:'Escape'});
 await waitFor(()=>assert.equal(open,false),'Esc closes it');
});

test('failure and empty say so plainly, with the way on',async()=>{
 const anchor={current:{getBoundingClientRect:()=>new DOMRect(232,600,0,120)}};
 let retried=0;
 render(h(UsagePopover,{open:true,onOpenChange(){},anchor,failed:true,onRetry(){retried++;},onManage(){}}));
 fireEvent.click(within(await screen.findByRole('alert')).getByRole('button',{name:'Try again'}));
 assert.equal(retried,1);
 cleanup();
 render(h(UsagePopover,{open:true,onOpenChange(){},anchor,failed:false,summary:{at:0,accounts:[]},onRetry(){},onManage(){}}));
 assert.ok(await screen.findByText('No accounts yet. Add one on Connected accounts.'));
});

test('usage shows the five-hour and weekly allowances together with separate resets',async()=>{
 const anchor={current:{getBoundingClientRect:()=>new DOMRect(232,600,0,120)}};
 render(h(UsagePopover,{open:true,onOpenChange(){},anchor,failed:false,onRetry(){},onManage(){},summary:{at:0,accounts:[account({
  windows:[{percent:90,window_mins:300,resets_at:at(23,14,0)},{percent:25,window_mins:10080,resets_at:at(25,10,0)}],
 })]}}));
 const pop=await screen.findByTestId('usage-popover');
 assert.ok(within(pop).getByText('10% left'));
 assert.ok(within(pop).getByText('75% left'));
 assert.match(pop.textContent,/5-hour limit/);
 assert.match(pop.textContent,/Weekly limit/);
 assert.equal(pop.querySelectorAll('.dv-usage-windows').length,1);
});

test('the Usage dialog is retired: every way in opens the popover beside the usage lines',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.doesNotMatch(shell,/UsageDialog/);
 assert.match(shell,/usage:\(\)=>openUsage\(jumpTrigger\(\)\)/,'⌘K');
 assert.match(shell,/onUsage=\{openUsage\} usageLifted=\{usageOpen\?usageLifted:null\}/,'the lines and the account menu');
 assert.match(shell,/if\(!lines\|\|!usage\.data\?\.accounts\.length\)\{settings\.open\('usage-manage',trigger\);return;\}/,'with nothing to show, Connected accounts instead');
 const sidebar=readFileSync(new URL('./DervoSidebar.tsx',import.meta.url),'utf8');
 // One line since app #13 (23 Sep): it lifts while the popover is open for any of its accounts.
 assert.match(sidebar,/const lifted=props\.accounts\.some\(account=>account\.profile_id===props\.usageLifted\)/);
 assert.match(sidebar,/aria-expanded=\{lifted\}/,'the line you clicked lifts, and says false when closed');
});
