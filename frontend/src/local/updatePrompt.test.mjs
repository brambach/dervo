import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
for (const key of ['window','document','HTMLElement','MutationObserver','Node','Event','KeyboardEvent']) globalThis[key] = dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = await import('react');
const {render,screen,fireEvent,waitFor,cleanup,act} = await import('@testing-library/react');
const {UpdatePrompt, promptView, shortVersion, mb} = await import('./UpdatePrompt.tsx');
const {resetUpdateNotice, pollDelay, QUIET_MS, BUSY_MS} = await import('./updateNotice.ts');
const {buildChanges, buildChangesRelease} = await import('./buildChanges.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();resetUpdateNotice();delete window.__TAURI_INTERNALS__;});

// The update prompt (beta.15, group 9), against the host's notice for a fake newer catalog row.
const status={context:'ctx',state:'available',detail:'A newer Dervo beta is ready to download.',busy:false,received:0,canFetch:true,canDownload:true,canUpdate:false,
  release:{release_id:'0.1.0-beta.16',bytes:300000000,format:'zip'},update:null};
const offer={prompt:{releaseId:'0.1.0-beta.16',title:'Beta 16',notes:['Pick the agent on Home','Icons in the Dock','Update prompt'],bytes:300000000},dot:true,updated:null,status};
const none={prompt:null,dot:false,updated:null,status:{...status,state:'current',release:null}};

function mount(handler, props={}) {
  const calls=[];
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});return handler(command,args);}};
  let opened=0;
  const view=render(React.createElement(UpdatePrompt,{onOpenUpdates:()=>{opened++;},...props}));
  return {...view,calls,opened:()=>opened};
}
const operations=calls=>calls.filter(c=>c.command==='local_request').map(c=>c.args.operation);

test('a newer build shows its version, what’s new, Update now and Later',async()=>{
  mount(()=>offer);
  await waitFor(()=>assert.ok(screen.getByRole('region',{name:'Dervo beta.16 is out'})));
  for (const line of offer.prompt.notes) assert.ok(screen.getByText(line));
  assert.ok(screen.getByText('300 MB'));
  assert.ok(screen.getByRole('button',{name:'Update now'}));
  assert.ok(screen.getByRole('button',{name:'Later'}));
});

test('nothing shows when you’re on the newest build, or when the check failed',async()=>{
  const view=mount(()=>none);
  await waitFor(()=>assert.deepEqual(operations(view.calls),['beta_update_notice']));
  assert.equal(screen.queryByTestId('update-prompt'),null);
  cleanup();resetUpdateNotice();
  const failed=mount(()=>{throw new Error('offline');});
  await waitFor(()=>assert.equal(failed.calls.length,1));
  assert.equal(screen.queryByTestId('update-prompt'),null,'a failed read says nothing on open');
});

test('Update now is Settings > Updates’ own download, with the displayed release and context',async()=>{
  let state=offer;
  const view=mount((command,args)=>{
    if(args?.operation==='beta_update_fetch'){state={...offer,status:{...status,state:'downloading',busy:true,canFetch:false,received:150000000}};return state.status;}
    return state;
  });
  await waitFor(()=>screen.getByRole('button',{name:'Update now'}));
  fireEvent.click(screen.getByRole('button',{name:'Update now'}));
  await waitFor(()=>assert.ok(screen.getByRole('progressbar',{name:'Update download'})));
  const fetch=view.calls.find(c=>c.args?.operation==='beta_update_fetch');
  assert.deepEqual(fetch.args.args,{context:'ctx',releaseId:'0.1.0-beta.16'});
  assert.equal(screen.getByRole('progressbar').value,150000000);
  assert.ok(view.calls.every(c=>c.command!=='install_update'),'nothing installs without a second click');
});

test('when the update is ready, Restart to update is the native Update and restart',async()=>{
  const ready={...offer,status:{...status,state:'ready',canUpdate:true,update:{source:'download',releaseId:'0.1.0-beta.16'}}};
  const view=mount(()=>ready);
  await waitFor(()=>screen.getByRole('button',{name:'Restart to update'}));
  fireEvent.click(screen.getByRole('button',{name:'Restart to update'}));
  await waitFor(()=>assert.ok(view.calls.find(c=>c.command==='install_update')));
});

test('a disk image build opens Settings > Updates, where the installer downloads',async()=>{
  const dmg={...offer,status:{...status,canFetch:false,release:{...status.release,format:'dmg'}}};
  const view=mount(()=>dmg);
  await waitFor(()=>screen.getByRole('button',{name:'Update now'}));
  fireEvent.click(screen.getByRole('button',{name:'Update now'}));
  assert.equal(view.opened(),1);
  assert.ok(!operations(view.calls).includes('beta_update_fetch'));
});

test('Later hides it for this build, and Escape does the same',async()=>{
  let state=offer;
  const view=mount((command,args)=>{
    if(args?.operation==='beta_update_later'){state={...offer,prompt:null};return state;}
    return state;
  });
  await waitFor(()=>screen.getByRole('button',{name:'Later'}));
  // Inside act, so the store's update settles before waiting (a bare waitFor here never returned).
  await act(async()=>{fireEvent.keyDown(screen.getByRole('region'),{key:'Escape'});});
  await waitFor(()=>assert.equal(screen.queryByTestId('update-prompt'),null));
  const later=view.calls.find(c=>c.args?.operation==='beta_update_later');
  assert.deepEqual(later.args.args,{releaseId:'0.1.0-beta.16'});
});

test('after an update the first open says which version you’re on and what changed, once',async()=>{
  let state={...none,updated:{releaseId:'0.1.0-beta.15',notes:['Pick an icon','Update prompt']}};
  const view=mount((command,args)=>{
    if(args?.operation==='beta_update_seen'){state=none;return state;}
    return state;
  });
  await waitFor(()=>screen.getByRole('region',{name:'You’re on beta.15'}));
  assert.ok(screen.getByText('Pick an icon'));
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'OK'}));});
  await waitFor(()=>assert.equal(screen.queryByTestId('update-prompt'),null));
  assert.deepEqual(view.calls.find(c=>c.args?.operation==='beta_update_seen').args.args,{releaseId:'0.1.0-beta.15'});
});

test('without recorded notes, "You’re on" uses this build’s own changes',async()=>{
  mount(()=>({...none,updated:{releaseId:buildChangesRelease,notes:[]}}));
  await waitFor(()=>screen.getByRole('region',{name:`You’re on ${shortVersion(buildChangesRelease)}`}));
  for (const line of buildChanges.slice(0,3)) assert.ok(screen.getByText(line));
  assert.equal(document.querySelectorAll('.dv-up-notes li').length,3);
});

test('without recorded notes, a version this build’s list wasn’t written for shows no list',async()=>{
  mount(()=>({...none,updated:{releaseId:'0.1.0-beta.17',notes:[]}}));
  await waitFor(()=>screen.getByRole('region',{name:'You’re on beta.17'}));
  assert.equal(document.querySelector('.dv-up-notes'),null,'no older notes under a newer version');
  assert.ok(screen.getByRole('button',{name:'OK'}));
  cleanup();resetUpdateNotice();
  mount(()=>({...none,updated:{releaseId:'0.1.0-beta.17',notes:['Recorded line']}}));
  await waitFor(()=>screen.getByText('Recorded line'),'recorded notes still show');
});

test('the title follows the download: downloading, ready and failed',async()=>{
  const at=(state,extra={})=>({...offer,status:{...status,state,...extra}});
  mount(()=>at('downloading',{busy:true,canFetch:false,received:41000000}));
  await waitFor(()=>screen.getByRole('region',{name:'Downloading beta.16'}));
  assert.ok(screen.getByText('41 MB of 300 MB'));
  cleanup();resetUpdateNotice();
  mount(()=>at('ready',{canUpdate:true,update:{source:'download',releaseId:'0.1.0-beta.16'}}));
  await waitFor(()=>screen.getByRole('region',{name:'Dervo beta.16 is ready'}));
  assert.doesNotMatch(screen.getByTestId('update-prompt').textContent,/Ready\./);
  cleanup();resetUpdateNotice();
  mount(()=>at('unpacking',{busy:true,canFetch:false}));
  await waitFor(()=>screen.getByRole('region',{name:'Unpacking beta.16'}));
});

test('a failed download offers Try again, which is the same download again',async()=>{
  const failed={...offer,status:{...status,state:'error',detail:'The download stopped at 41 MB. Check your connection and try again.'}};
  let state=failed;
  const view=mount((command,args)=>{
    if(args?.operation==='beta_update_fetch'){state={...offer,status:{...status,state:'downloading',busy:true,canFetch:false,received:1000000}};return state.status;}
    return state;
  });
  await waitFor(()=>screen.getByRole('region',{name:'Dervo beta.16 didn’t download'}));
  assert.ok(screen.getByRole('alert'));
  assert.ok(screen.getByRole('button',{name:'Open Updates'}));
  fireEvent.click(screen.getByRole('button',{name:'Try again'}));
  await waitFor(()=>screen.getByRole('region',{name:'Downloading beta.16'}));
  assert.deepEqual(view.calls.find(c=>c.args?.operation==='beta_update_fetch').args.args,{context:'ctx',releaseId:'0.1.0-beta.16'});
  cleanup();resetUpdateNotice();
  mount(()=>({...failed,status:{...failed.status,canFetch:false}}));
  await waitFor(()=>screen.getByRole('region',{name:'Dervo beta.16 didn’t download'}));
  assert.equal(screen.queryByRole('button',{name:'Try again'}),null,'a build that can’t download here sends you to Updates');
});

test('the card shows the icon you picked, and Cream when the native app can’t say',async()=>{
  const view=mount((command)=>command==='app_icon_get'?{icon:'forest',dock:true}:offer);
  await waitFor(()=>assert.equal(view.container.querySelector('.dv-up-mark').getAttribute('src'),'/app-icons/forest.png'));
  cleanup();resetUpdateNotice();
  const other=mount((command)=>{if(command==='app_icon_get')throw new Error('no native app');return offer;});
  await waitFor(()=>screen.getByRole('region',{name:'Dervo beta.16 is out'}));
  assert.equal(other.container.querySelector('.dv-up-mark').getAttribute('src'),'/app-icons/cream.png');
  const {markIcon}=await import('./UpdatePrompt.tsx');
  assert.deepEqual([markIcon({icon:'night'}),markIcon({icon:'../x'}),markIcon(offer),markIcon(null)],['night','cream','cream','cream']);
});

test('this build’s change list leads with beta.16’s thread questions, Work accounts and Continue',()=>{
  assert.ok(buildChanges.length>=3);
  const first=buildChanges.slice(0,3).join(' ');
  for (const topic of [/ask their questions/,/Work or Personal/,/Continue in Codex or Claude/]) assert.match(first,topic);
  assert.match(buildChanges.join(' '),/GPT-6 Sol/);
  assert.match(buildChanges.join(' '),/pull request/);
  assert.equal(buildChangesRelease,'0.1.0-beta.16');
  assert.doesNotMatch(buildChanges.join(' '),/move it to Applications/,'the beta.15.1 list is gone');
});

test('the card follows the host’s state',()=>{
  assert.equal(shortVersion('0.1.0-beta.16'),'beta.16');
  assert.equal(shortVersion('0.1.0-preview.20260924081135000'),'preview.20260924081135000');
  assert.deepEqual([mb(148000000),mb(147600000),mb(4200000),mb(0)],['148 MB','148 MB','4.2 MB','0.0 MB']);
  assert.deepEqual(promptView(none),{kind:'none'});
  assert.deepEqual(promptView(offer),{kind:'offer',releaseId:'0.1.0-beta.16'});
  assert.equal(promptView({...offer,status:{...status,state:'unpacking',busy:true}}).kind,'unpacking');
  assert.equal(promptView({...offer,status:{...status,state:'error'}}).kind,'failed');
  assert.equal(promptView({...offer,status:{...status,release:{...status.release,release_id:'0.1.0-beta.15'}}}).kind,'offer','another release on Settings doesn’t change the offer');
  assert.equal(promptView({...offer,updated:{releaseId:'0.1.0-beta.15',notes:[]}}).kind,'updated','“You’re on” comes first');
  assert.equal(pollDelay(offer),QUIET_MS);
  assert.equal(pollDelay({...offer,status:{...status,busy:true}}),BUSY_MS);
});

// Harrison, 25 Sep: beta.14 ran from where it was unzipped (macOS App Translocation), so Update and restart failed and
// Finder opened a recovery folder. The host now says so (installProblem) and the prompt tells you what to do first.
const move={kind:'move',line:'Dervo can’t update itself from where it’s running. Quit Dervo, move Dervo Local.app into your Applications folder, open it from there, then update again.'};
const permission={kind:'permission',line:'Dervo can’t replace itself in this folder with your account’s permissions. Download the new version from trydervo.com/beta and drag it into Applications.'};

test('an app running from Downloads is asked to move on open, and Not now hides it',async()=>{
  assert.deepEqual(promptView({...none,status:{...none.status,installProblem:move}}),{kind:'move'});
  assert.deepEqual(promptView({...none,status:{...none.status,installProblem:permission}}),{kind:'none'},'no nag for an app already in Applications');
  mount(()=>({...none,status:{...none.status,installProblem:move}}));
  await waitFor(()=>assert.ok(screen.getByRole('region',{name:'Move Dervo to Applications'})));
  assert.ok(screen.getByText(/drag Dervo Local.app into your Applications folder/));
  fireEvent.click(screen.getByRole('button',{name:'Not now'}));
  await waitFor(()=>assert.equal(screen.queryByTestId('update-prompt'),null));
});

test('a newer build that couldn’t replace this app says how to fix it instead of offering Update now',async()=>{
  const blocked={...offer,status:{...status,installProblem:move}};
  assert.deepEqual(promptView(blocked),{kind:'blocked',releaseId:'0.1.0-beta.16'});
  assert.equal(promptView({...blocked,status:{...blocked.status,state:'downloading',busy:true}}).kind,'downloading','a download already running keeps its progress');
  assert.equal(promptView({...blocked,updated:{releaseId:'0.1.0-beta.15',notes:[]}}).kind,'updated');
  const {calls}=mount(()=>blocked);
  await waitFor(()=>assert.ok(screen.getByRole('region',{name:'Dervo beta.16 is out'})));
  assert.ok(screen.getByText(move.line));
  assert.equal(screen.queryByRole('button',{name:'Update now'}),null);
  fireEvent.click(screen.getByRole('button',{name:'Later'}));
  await waitFor(()=>assert.ok(operations(calls).includes('beta_update_later')));
});
