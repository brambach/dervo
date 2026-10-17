import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.localStorage=dom.window.localStorage;
const React=await import('react');
const {render,fireEvent,cleanup,act,waitFor}=await import('@testing-library/react');
const {ProjectMoment,momentFrom,rememberWaiting,waitingOverviews,useOverviewOnApproval}=await import('./ProjectMoment.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();localStorage.clear();delete window.__TAURI_INTERNALS__;});

const waiting={id:'p9',channelId:'gen',name:'shop',access:'edit',overview:{channelId:'ov',state:'waiting',detail:'Your beta access is waiting for approval.'},overview_detail:'Your beta access is waiting for approval.'};

test('what first run returned is kept, not dropped: waiting, started, or refused with its reason',()=>{
 assert.deepEqual(momentFrom(waiting,'Claude Code'),{projectId:'p9',channelId:'ov',name:'shop',agent:'Claude Code',state:'waiting',accessDetail:undefined,overviewDetail:undefined,waitingFor:'beta'});
 assert.equal(momentFrom({...waiting,overview:{...waiting.overview,waiting_for:'plan'}},'Claude Code').waitingFor,'plan','a project made before choosing a plan waits for one');
 assert.equal(momentFrom({...waiting,overview:{channelId:'ov',state:'working'},overview_detail:undefined},'Codex').state,'running');
 const refused=momentFrom({...waiting,overview:{channelId:'ov',state:'blocked',detail:'Codex isn’t signed in.'},overview_detail:'Codex isn’t signed in.'},'Codex');
 assert.deepEqual([refused.state,refused.overviewDetail],['failed','Codex isn’t signed in.']);
 const noThread=momentFrom({...waiting,overview:null,overview_detail:'The model is gone.'},'Codex');
 assert.deepEqual([noThread.state,noThread.channelId],['failed','gen'],'with no overview thread the project opens on its own thread');
 assert.equal(momentFrom({...waiting,access_detail:'Git isn’t working.'},'Codex').accessDetail,'Git isn’t working.');
 // Full access that couldn't be turned on leaves editing with approval, and says so (setup's access choice, 9 Oct).
 assert.equal(momentFrom({...waiting,access:'edit',access_detail:'Full access needs git.'},'Codex').accessNow,'edit');
 assert.equal(momentFrom({...waiting,access:'read',access_detail:'Git isn’t working.'},'Codex').accessNow,'read');
 assert.equal(momentFrom({...waiting,access:'full',checks:[],checks_detail:'Tests isn’t one of this project’s checks.'},'Codex').checksDetail,'Tests isn’t one of this project’s checks.');
});

function Watcher({approved,events}){
 useOverviewOnApproval(approved,(projectId,channelId)=>events.push(['started',projectId,channelId]),(projectId,detail)=>events.push(['failed',projectId,detail]));
 return null;
}

test('a pending tester’s overview starts by itself when access turns approved, once, in its own thread',async()=>{
 const calls=[],events=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push([args.operation,args.args]);return {id:'req',channelId:'ov',state:'working'};}};
 rememberWaiting('p9');
 const view=render(React.createElement(Watcher,{approved:false,events}));
 await act(async()=>{});
 assert.deepEqual(calls,[],'nothing is sent while access is pending');
 view.rerender(React.createElement(Watcher,{approved:true,events}));
 await waitFor(()=>assert.equal(events.length,1));
 assert.deepEqual(calls,[['project_explore',{projectId:'p9',clientId:'first-run-p9'}]],'the same request ID first run used, so it can’t start twice');
 assert.deepEqual(events,[['started','p9','ov']]);
 assert.deepEqual(waitingOverviews(),[],'the wait is over');
 view.rerender(React.createElement(Watcher,{approved:false,events}));view.rerender(React.createElement(Watcher,{approved:true,events}));
 await act(async()=>{});
 assert.equal(calls.length,1,'approval flickering doesn’t start it again');
});

test('a start refused for another reason says why instead of waiting forever; one still refused on access keeps waiting',async()=>{
 const events=[];
 window.__TAURI_INTERNALS__={invoke:async()=>({id:'req',channelId:'ov',state:'blocked',error:'Codex isn’t signed in.'})};
 rememberWaiting('p9');
 const view=render(React.createElement(Watcher,{approved:true,events}));
 await waitFor(()=>assert.equal(events.length,1));
 assert.deepEqual(events,[['failed','p9','Codex isn’t signed in.']]);
 view.unmount();
 window.__TAURI_INTERNALS__={invoke:async()=>{throw new Error('Dervo beta access needs confirmation. Open Account in Settings.');}};
 rememberWaiting('p8');
 render(React.createElement(Watcher,{approved:true,events}));
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20));});
 assert.deepEqual(waitingOverviews(),['p8']);
 assert.equal(events.length,1);
 // Refused for want of a plan (supabase_account.plan_detail): it keeps waiting too.
 window.__TAURI_INTERNALS__={invoke:async()=>{throw new Error('Your free trial has ended. Choose a plan in Settings › Account to keep starting agent work.');}};
 rememberWaiting('p7');
 render(React.createElement(Watcher,{approved:true,events}));
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20));});
 assert.deepEqual(waitingOverviews().sort(),['p7','p8']);
 assert.equal(events.length,1);
});

test('the moment: ready with the mark, waiting with Check now, and a refusal with its reason and no mark above it',async()=>{
 const checks=[],settings=[];
 const moment={projectId:'p9',channelId:'ov',name:'shop',agent:'Claude Code',state:'waiting'};
 const props={checkedAt:Date.now(),onCheck:()=>checks.push(1),onRetry:async()=>{},onSettings:trigger=>settings.push(trigger)};
 const view=render(React.createElement(ProjectMoment,{...props,moment}));
 assert.ok(view.getByRole('heading',{name:'shop is set up.'}));
 assert.equal(view.getByRole('status').textContent,'Waiting for approval');
 assert.match(document.querySelector('.dv-pm-status').textContent,/Waiting for approval · checked just now/);
 assert.match(document.body.textContent,/as soon as your beta access is approved\. You don’t need to do anything else\./);
 fireEvent.click(view.getByRole('button',{name:'Check now'}));
 assert.deepEqual(checks,[1]);
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,state:'running',accessDetail:'Git isn’t working in this folder.'}}));
 assert.ok(view.getByRole('heading',{name:'shop is set up.'}));
 assert.ok(document.querySelector('.dv-moment-mark .dv-pebble'));
 assert.match(document.body.textContent,/Editing couldn’t be turned on: Git isn’t working in this folder\. It can read and explain for now\./);
 fireEvent.click(view.getByRole('button',{name:'Open Project settings'}));
 assert.equal(settings.length,1);
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,state:'running',accessNow:'edit',accessDetail:'Read the full-access warning and confirm it to turn this on.'}}));
 assert.match(document.body.textContent,/Full access isn’t on, so it can edit files with your approval for now\. Read the full-access warning and confirm it to turn this on\./);
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,state:'failed',overviewDetail:'Codex isn’t signed in.'}}));
 assert.equal(document.querySelector('.dv-moment-mark'),null,'the mark never sits above an error');
 assert.match(document.body.textContent,/The overview didn’t start: Codex isn’t signed in\./);
 assert.ok(view.getByRole('button',{name:'Try again'}));
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,waitingFor:'plan'}}));
 assert.match(document.querySelector('.dv-pm-status').textContent,/Waiting for a plan · checked just now/);
 assert.match(document.body.textContent,/as soon as you start a free trial or choose a plan in Settings › Account\./);
 assert.doesNotMatch(document.body.textContent,/beta access/);
});

test('the moment teaches the microtask pattern while it waits or runs, offers Home only once approved, and leaves an error alone',()=>{
 const homes=[];
 const props={checkedAt:Date.now(),onCheck(){},onRetry:async()=>{},onSettings(){},onHome:()=>homes.push(1)};
 const moment={projectId:'p9',channelId:'ov',name:'shop',agent:'Claude Code',state:'waiting'};
 const view=render(React.createElement(ProjectMoment,{...props,moment}));
 assert.ok(view.getByRole('heading',{level:3,name:'Keep Dervo open beside you'}));
 assert.match(document.body.textContent,/Once you’re approved, ask Dervo on Home when something small comes up/);
 assert.equal(view.queryByRole('button',{name:'Try one on Home'}),null,'Home can’t take a task before approval');
 assert.equal(view.getAllByRole('status').length,1,'the lesson adds no second status line to the wait');
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,state:'running'}}));
 assert.match(document.body.textContent,/When something small comes up, ask Dervo on Home and get back to what you were doing\./);
 assert.equal(document.querySelectorAll('.dv-mt-list li').length,3,'three examples of what tiny means');
 fireEvent.click(view.getByRole('button',{name:'Try one on Home'}));
 assert.equal(homes.length,1);
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,state:'failed',overviewDetail:'Codex isn’t signed in.'}}));
 assert.equal(view.queryByRole('heading',{name:'Keep Dervo open beside you'}),null,'an error leads alone');
 assert.equal(view.queryByRole('button',{name:'Try one on Home'}),null);
 assert.ok(view.getByRole('button',{name:'Try again'}));
 view.rerender(React.createElement(ProjectMoment,{...props,moment:{...moment,waitingFor:'plan'}}));
 assert.match(document.querySelector('.dv-pm-status').textContent,/Waiting for a plan · checked just now/);
 assert.match(document.body.textContent,/as soon as you start a free trial or choose a plan in Settings › Account\./);
 assert.doesNotMatch(document.body.textContent,/beta access/);
});

test('the app keeps the pending wait: first run’s result opens the overview thread, the composer says why it waits, and approval starts it',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/onDone=\{async\(created,agent\)=>\{const next=momentFrom\(created,agent\);if\(next\.state==='waiting'\)rememberWaiting\(created\.id\);setMoment\(next\);[^}]*setChannel\(next\.channelId\)/);
 assert.match(shell,/useOverviewOnApproval\(imageAccessReady&&accountData\?\.plan\?\.plan!=='none',/,'a project made without a plan waits for one');
 assert.match(shell,/<MessageTimeline[^>]*pinnedIntro=\{momentIntro\}/);
 assert.match(shell,/disabled=\{!binding\.data\|\|!!modelGone\|\|accessPending\}[^]*placeholder=\{accessPending\?'Agents can start once your access is approved'/);
 assert.match(shell,/if\(projects\.isPending\)return <OpeningScreen\/>;/,'one opening screen until projects load');
 assert.match(shell,/if\(firstRunMode==='first'\)return firstRun;/,'first run is a full window with no sidebar');
});

test('overview recap shortens only the generated request, including the host excerpt',async()=>{
 const {recapRows}=await import('./ThreadPickup.tsx');
 const {PROJECT_OVERVIEW_REQUEST,PROJECT_OVERVIEW_SUMMARY}=await import('./projectOverview.ts');
 for(const asked of [PROJECT_OVERVIEW_REQUEST,PROJECT_OVERVIEW_REQUEST.slice(0,127)+'…',PROJECT_OVERVIEW_REQUEST.slice(0,127)+'...']){
  assert.equal(recapRows({asked},true)[0].text,PROJECT_OVERVIEW_SUMMARY);
  assert.equal(recapRows({asked},false)[0].text,asked);
 }
 for(const asked of ['Explore this project…','Read my README.','Explore this project using a bounded folder listing, README and relevant source files. Then change it.'])assert.equal(recapRows({asked},true)[0].text,asked);
});
