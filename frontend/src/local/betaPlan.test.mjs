import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event'])if(dom.window[key])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(Date.now()),0);
globalThis.cancelAnimationFrame=handle=>clearTimeout(handle);
const React=await import('react');
const {render,screen,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {BetaPlan,counters,releaseRail,day}=await import('./BetaPlan.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const view={
 running:{id:'0.1.0-beta.3'},source:'/Users/someone/dervo/app',file:'runtime/dervo-beta-plan.json',
 next:{version:'0.1.0-beta.4',headline:'Chats you can point at.',
  planned:[{title:'Pick a conversation with #',detail:'In the composer.',state:'building'},
           {title:'Polish the coding experience',detail:null,state:'planned'}],
  landed:[{sha:'0917862',date:'2026-09-18',title:'Mark as stopped'},{sha:'6e98e5c',date:'2026-09-18',title:'Archiving a conversation'}],
  total:7,uncommitted:11},
 shipped:[{version:'0.1.0-beta.3',built:'2026-09-18',headline:'Tasks stopped failing at once.',commit:'dcb3a65',
           features:[{sha:'dcb3a65',date:'2026-09-18',title:'Every task died at once'}],total:1},
          {version:'0.1.0-beta.2',built:'2026-09-17',headline:'First run sets Dervo up.',commit:'4f78cfd',features:[],total:4}],
};
function mount(handler=()=>view){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});return handler(command,args);}};
 return {...render(React.createElement(BetaPlan,{open:true,onClose(){}})),calls};
}

test('the rail lists the next version first and marks the beta you’re running',()=>{
 const rail=releaseRail(view);
 assert.deepEqual(rail.map(entry=>entry.version),['0.1.0-beta.4','0.1.0-beta.3','0.1.0-beta.2']);
 assert.equal(rail[0].when,'Next, not built yet');
 assert.equal(rail[0].running,false,'the version that isn’t built yet can’t be the one running');
 assert.equal(rail[1].running,true);
 assert.match(rail[1].when,/running now$/);
 assert.equal(rail[2].running,false);
});

test('every counter is derived from what the host read, and uncommitted work is called out',()=>{
 const rows=counters(view);
 assert.deepEqual(rows.map(row=>[row.value,row.label]),[
  ['2','features planned'],['1','still to start'],['7','commits since beta.3'],['11','uncommitted files']]);
 assert.equal(rows[3].warn,true);
 assert.equal(counters({...view,next:{...view.next,uncommitted:0}})[3].warn,false,'a clean tree is not a warning');
});

test('without Dervo’s own checkout the git counters read as unknown rather than zero',()=>{
 const rows=counters({...view,source:null,next:{...view.next,landed:[],total:0,uncommitted:null}});
 assert.deepEqual([rows[2].value,rows[3].value],['–','–']);
});

test('the screen shows the next release: its planned features with states, and the commits that landed',async()=>{
 const mounted=mount();
 await waitFor(()=>assert.ok(screen.getByRole('heading',{name:'0.1.0-beta.4'})));
 assert.deepEqual(mounted.calls,[{command:'local_request',args:{operation:'beta_plan',args:{}}}]);
 assert.equal(screen.getAllByText('Chats you can point at.').length,2,'the headline reads in the rail and over the release');
 assert.ok(screen.getByText('Building'));
 assert.ok(screen.getByText('Planned'));
 assert.ok(screen.getByText('Pick a conversation with #'));
 assert.ok(screen.getByText('0917862'));
 assert.ok(screen.getByText('5 more, 11 uncommitted files that aren’t in any release yet'));
 assert.ok(screen.getByText(/Read with git from \/Users\/someone\/dervo\/app/));
 assert.ok(screen.getByText(/runtime\/dervo-beta-plan\.json/),'the hand-written part names its own file');
});

test('choosing a shipped release reads the commits that were in it, not a typed feature list',async()=>{
 mount();
 await waitFor(()=>assert.ok(screen.getByRole('heading',{name:'0.1.0-beta.4'})));
 fireEvent.click(screen.getByRole('button',{name:/0\.1\.0-beta\.3/}));
 await waitFor(()=>assert.ok(screen.getByRole('heading',{name:'0.1.0-beta.3'})));
 assert.ok(screen.getByText('Every task died at once'));
 assert.equal(screen.queryByText('still to start'),null,'the planned counters belong to the version being planned');
 assert.ok(screen.getByText(`Shipped · ${day('2026-09-18')} · running now`));
});

test('a plan the host refuses is reported, and can be asked for again',async()=>{
 let attempts=0;
 const mounted=mount(()=>{attempts++;if(attempts===1)throw new Error('This build doesn’t carry a beta plan.');return view;});
 await waitFor(()=>assert.ok(screen.getByRole('alert')));
 assert.match(screen.getByRole('alert').textContent,/doesn’t carry a beta plan/);
 fireEvent.click(screen.getByRole('button',{name:'Try again'}));
 await waitFor(()=>assert.ok(screen.getByRole('heading',{name:'0.1.0-beta.4'})));
 assert.equal(mounted.calls.length,2);
});

test('the screen is reached from the sidebar and asks the host for the plan through the bridge',async()=>{
 const {readFileSync}=await import('node:fs');
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/<BetaPlan open=\{releasesOpen\}/);
 // Offered only to the Dervo team: the shell passes the handler through the same gate the host enforces.
 assert.match(shell,/onReleases=\{releasesAllowed\(accountData\)\?/);
 const sidebar=readFileSync(new URL('./DervoSidebar.tsx',import.meta.url),'utf8');
 assert.ok(sidebar.includes('{props.onReleases&&<DropdownMenuItem onSelect={()=>{opening.current=true;props.onReleases?.();}}><Package size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t(\'Releases\')}</span></DropdownMenuItem>}'),'the item exists only when the shell offers it');
 assert.ok(readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8').includes('"beta_plan"'));
});

test('a date the host didn’t record stays out of the line',()=>{
 assert.equal(day(null),'');
 assert.equal(day('sometime'),'');
 assert.equal(releaseRail({...view,shipped:[{...view.shipped[0],built:null}]})[1].when,'running now');
});

test('the page is the Dervo team’s: an admin or a build without sign-in sees it, a tester does not',async()=>{
 const {releasesAllowed}=await import('./BetaPlan.tsx');
 assert.equal(releasesAllowed({state:'signed_in',membership:{is_admin:true}}),true);
 assert.equal(releasesAllowed({state:'unavailable'}),true,'a source checkout has no Dervo sign-in');
 assert.equal(releasesAllowed({state:'signed_in',membership:{is_admin:false}}),false);
 assert.equal(releasesAllowed({state:'signed_out',membership:null}),false);
 assert.equal(releasesAllowed(undefined),false,'unknown until the account status has loaded');
});
