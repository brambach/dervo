import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
for (const key of ['window','document','HTMLElement','MutationObserver','Node','Event']) globalThis[key] = dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = await import('react');
const {render,screen,fireEvent,waitFor,cleanup,act} = await import('@testing-library/react');
const {BetaUpdates} = await import('./BetaUpdates.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const base={available:true,context:'activation',current:{releaseId:'trial.1'},state:'unchecked',detail:'Check for a newer beta.',busy:false,canCheck:true,canDownload:false,received:0,savedPath:null,release:null};
const available={...base,state:'available',canDownload:true,release:{release_id:'trial.2',title:'A clearer workspace',notes:'Better conversations.',bytes:300000000,format:'zip'}};
function mount(handler) {
  const calls=[];
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});return handler(command,args);}};
  return {...render(React.createElement(BetaUpdates)),calls};
}

test('opening settings reads status without checking or downloading automatically',async()=>{
  const view=mount(()=>base);
  await waitFor(()=>assert.ok(screen.getByText('Dervo trial.1')));
  assert.deepEqual(view.calls,[{command:'local_request',args:{operation:'beta_update_status',args:{}}}]);
  assert.ok(screen.getByText('Check for a newer beta.'));
  assert.equal(screen.queryByText('Previous version'),null,'no project, so no previous version is read');
  assert.equal(screen.queryByRole('button',{name:'Download installer'}),null);
});

test('download invokes only native selection with the displayed release and activation',async()=>{
  const view=mount(()=>available);
  await waitFor(()=>assert.ok(screen.getByText('A clearer workspace')));
  fireEvent.click(screen.getByRole('button',{name:'Download installer'}));
  await waitFor(()=>assert.ok(view.calls.find(c=>c.command==='download_beta_update')));
  assert.deepEqual(view.calls.find(c=>c.command==='download_beta_update').args,{context:'activation',releaseId:'trial.2'});
  assert.ok(view.calls.every(c=>!JSON.stringify(c).includes('folder')&&!JSON.stringify(c).includes('url')));
});

test('cancelled folder choice never reports a saved installer',async()=>{
  let picked=false;
  mount((command)=>{if(command==='download_beta_update'){picked=true;return null;}return available;});
  await waitFor(()=>assert.ok(screen.getByRole('button',{name:'Download installer'})));
  fireEvent.click(screen.getByRole('button',{name:'Download installer'}));
  await waitFor(()=>assert.equal(picked,true));
  await act(async()=>{});
  assert.equal(screen.queryByText('Saved to'),null);
});

test('progress comes from received bytes and cancellation carries current context',async()=>{
  const view=mount(()=>({...available,state:'downloading',busy:true,canCheck:false,canDownload:false,received:150000000}));
  await waitFor(()=>assert.equal(screen.getByRole('progressbar').value,150000000));
  assert.equal(screen.getByRole('progressbar').max,300000000);
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
  await waitFor(()=>assert.ok(view.calls.find(c=>c.args?.operation==='beta_update_cancel')));
  assert.deepEqual(view.calls.find(c=>c.args?.operation==='beta_update_cancel').args.args,{context:'activation'});
});

test('an unknown check is an error rather than a latest-version claim',async()=>{
  mount(()=>{throw new Error('https://private.invalid/signed');});
  await waitFor(()=>assert.ok(screen.getByRole('alert')));
  assert.equal(screen.queryByText(/latest published/),null);
  assert.ok(!document.body.textContent.includes('private.invalid'));
});

test('personal builds keep installed release notes and hide beta update controls',async()=>{
  const view=mount(()=>({...base,available:false}));
  await act(async()=>{});
  assert.match(view.container.textContent,/Changelog/);
  assert.equal(screen.queryByRole('button',{name:'Check for updates'}),null);
  assert.match(view.container.textContent,/Mark each account as Work or Personal/);
});

test('a newer version found or downloaded is one click: Update and restart asks the native app and names nothing',async()=>{
  const ready={...base,state:'ready',detail:'0.1.0-prebeta.44 is ready.',update:{source:'local',releaseId:'0.1.0-prebeta.44'},canUpdate:true};
  const view=mount(command=>command==='install_update'?null:ready);
  await waitFor(()=>assert.ok(screen.getByTestId('update-ready')));
  assert.match(document.querySelector('.dv-open-card').textContent,/0\.1\.0-prebeta\.44 is ready.*opens prebeta\.44\. Trial\.1 is kept so you can go back/);
  fireEvent.click(screen.getByRole('button',{name:'Update and restart'}));
  await waitFor(()=>assert.ok(view.calls.find(c=>c.command==='install_update')));
  assert.deepEqual(view.calls.find(c=>c.command==='install_update').args??{},{},'no path, version or context from the renderer');
});

test('a refused update shows the host’s reason and can be tried again',async()=>{
  const ready={...base,state:'ready',update:{source:'download',releaseId:'trial.2'},canUpdate:true};
  mount(command=>{if(command==='install_update')throw 'Finish or reconcile the work in your projects before switching versions.';return ready;});
  await waitFor(()=>assert.match(document.querySelector('.dv-open-card').textContent,/in this app’s place/));
  fireEvent.click(screen.getByRole('button',{name:'Update and restart'}));
  await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Finish or reconcile the work/));
  assert.equal(screen.getByRole('button',{name:'Update and restart'}).disabled,false);
});

test('a published zip downloads into Dervo with Download update; the folder download stays as the alternative',async()=>{
  const view=mount(()=>({...available,canFetch:true}));
  await waitFor(()=>assert.ok(screen.getByRole('button',{name:'Download update'})));
  assert.ok(screen.getByRole('button',{name:'Save the installer instead'}),'the folder download stays, as the quiet choice');
  fireEvent.click(screen.getByRole('button',{name:'Download update'}));
  await waitFor(()=>assert.ok(view.calls.find(c=>c.args?.operation==='beta_update_fetch')));
  assert.deepEqual(view.calls.find(c=>c.args?.operation==='beta_update_fetch').args.args,{context:'activation',releaseId:'trial.2'});
});

test('a Download update that is running never asks for a folder',async()=>{
  // Review, 23 Sep: canFetch is false while a download runs, so the card fell through to the saved-installer copy.
  const fetching={...available,state:'downloading',busy:true,canCheck:false,canDownload:false,canFetch:false,received:1000000};
  mount(()=>({...fetching,mode:'fetch'}));
  await waitFor(()=>assert.ok(screen.getByRole('progressbar')));
  const card=document.querySelector('.dv-open-card').textContent;
  assert.match(card,/Download update gets it ready to switch to/);
  assert.doesNotMatch(card,/Choose a folder/);
  cleanup();
  mount(()=>({...fetching,state:'unpacking',mode:'fetch'}));
  await waitFor(()=>assert.ok(screen.getByText('Unpacking the new version…')));
  assert.doesNotMatch(document.querySelector('.dv-open-card').textContent,/Choose a folder/);
  cleanup();
  // The installer download says where it's going, and one that saved says the same.
  mount(()=>({...fetching,mode:'download'}));
  await waitFor(()=>assert.ok(screen.getByRole('progressbar')));
  assert.match(document.querySelector('.dv-open-card').textContent,/The installer goes to the folder you chose/);
  cleanup();
  // Negative control: with nothing running and no Download update on offer, the folder choice is the offer.
  mount(()=>available);
  await waitFor(()=>assert.ok(screen.getByRole('button',{name:'Download installer'})));
  assert.match(document.querySelector('.dv-open-card').textContent,/Choose a folder for the installer/);
});

test('the card line follows the running download, then the offer',async()=>{
  const {offerLine}=await import('./BetaUpdates.tsx');
  assert.match(offerLine({mode:'fetch',canFetch:false}),/^Download update gets it ready/);
  assert.match(offerLine({mode:'download',canFetch:true}),/^The installer goes to the folder you chose/);
  assert.match(offerLine({mode:null,canFetch:true}),/^Download update gets it ready/);
  assert.match(offerLine({canFetch:false}),/^Choose a folder/,'an older host with no mode keeps the old line');
});

test('Later folds a ready update into the version row, which keeps Update and restart', async()=>{
  const ready={...base,state:'ready',update:{source:'local',releaseId:'0.1.0-beta.9'},canUpdate:true};
  const view=mount(command=>command==='install_update'?null:ready);
  await waitFor(()=>assert.ok(screen.getByTestId('update-ready')));
  fireEvent.click(screen.getByRole('button',{name:'Later'}));
  assert.equal(screen.queryByTestId('update-ready'),null);
  assert.ok(screen.getByText('0.1.0-beta.9 is ready to install.'));
  fireEvent.click(screen.getByRole('button',{name:'Update and restart'}));
  await waitFor(()=>assert.ok(view.calls.find(c=>c.command==='install_update')));
});

test('signed out: Check for updates is disabled and the row says why, with the way to Account', async()=>{
  let opened=0;
  window.__TAURI_INTERNALS__={invoke:async()=>({...base,canCheck:false})};
  render(React.createElement(BetaUpdates,{onAccount:()=>{opened++;}}));
  await waitFor(()=>assert.ok(screen.getByText('Sign in with approved beta access to check for updates.')));
  assert.equal(screen.getByRole('button',{name:'Check for updates'}).disabled,true);
  fireEvent.click(screen.getByRole('button',{name:'Account'}));
  assert.equal(opened,1);
});

test('the previous version is offered with its project, and going back is confirmed in place first', async()=>{
  const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
  const calls=[];
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});
    if(args?.operation==='preview_status')return {rollback:{switchId:'sw1',version:'0.1.0-beta.7',detail:''}};
    if(command==='restore_previous_version')return null;
    return base;}};
  const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
  render(React.createElement(QueryClientProvider,{client},React.createElement(BetaUpdates,{projectId:'p1'})));
  fireEvent.click(await screen.findByRole('button',{name:'Go back to beta.7'}));
  assert.equal(calls.some(c=>c.command==='restore_previous_version'),false,'nothing happens before the confirm');
  assert.ok(screen.getByText(/Project files aren’t rolled back/));
  fireEvent.click(screen.getByRole('button',{name:'Back up and go back'}));
  await waitFor(()=>assert.ok(calls.find(c=>c.command==='restore_previous_version')));
  assert.deepEqual(calls.find(c=>c.command==='restore_previous_version').args,{switchId:'sw1'});
});

// Harrison, 25 Sep: an app that can't be replaced where it runs says how to fix it, and Update and restart stays off.
test('an app that can’t be replaced where it runs says so and doesn’t offer a restart that fails',async()=>{
  const line='Dervo can’t update itself from where it’s running. Quit Dervo, move Dervo Local.app into your Applications folder, open it from there, then update again.';
  const ready={...available,state:'ready',canUpdate:true,update:{source:'download',releaseId:'trial.2'},installProblem:{kind:'move',line}};
  const view=mount(()=>ready);
  await waitFor(()=>assert.equal(screen.getByTestId('update-blocked').textContent,line));
  assert.equal(screen.getByRole('button',{name:'Update and restart'}).disabled,true);
  fireEvent.click(screen.getByRole('button',{name:'Update and restart'}));
  await act(async()=>{});
  assert.equal(view.calls.find(c=>c.command==='install_update'),undefined);
  cleanup();
  mount(()=>({...base,installProblem:{kind:'permission',line:'Dervo can’t replace itself in this folder.'}}));
  await waitFor(()=>assert.ok(screen.getByText('Dervo trial.1')));
  assert.equal(screen.queryByTestId('update-blocked'),null,'the permission line waits until there’s an update');
});
