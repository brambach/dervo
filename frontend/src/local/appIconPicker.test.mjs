import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
for (const key of ['window','document','HTMLElement','MutationObserver','Node','Event','KeyboardEvent']) globalThis[key] = dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = await import('react');
const {render,screen,fireEvent,waitFor,cleanup} = await import('@testing-library/react');
const {AppIconPicker, iconImage, whereLine} = await import('./AppIconPicker.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

// Settings > Preferences > App icon (beta.15, group 7).
const NAMES=['Cream','Forest','Meadow','Peek','Crew','Terminal','Pixel','Night','Clay','Obsidian'];
const icons=founder=>NAMES.map(name=>({id:name.toLowerCase(),name,founder:name==='Obsidian',available:name!=='Obsidian'||founder}));
const founderStatus={founder:'founder',detail:'You were here before launch, so Obsidian is yours for good, on any Mac you sign in on.',icons:icons(true)};
const laterStatus={founder:'not_founder',detail:'Obsidian is for testers who joined before the public launch.',icons:icons(false)};

function mount(status, chosen={icon:'cream',dock:true}, set) {
  const calls=[];
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
    calls.push({command,args});
    if(command==='local_request')return typeof status==='function'?status():status;
    if(command==='app_icon_get')return chosen;
    if(command==='app_icon_set')return set?set(args):{icon:args.icon,dock:true};
    throw new Error('unexpected '+command);
  }};
  return {...render(React.createElement(AppIconPicker)),calls};
}

test('all ten show, the saved one is checked, and each has its picture',async()=>{
  mount(founderStatus,{icon:'forest',dock:true});
  await waitFor(()=>assert.equal(screen.getAllByRole('radio').length,10));
  assert.equal(screen.getByRole('radio',{name:'Forest'}).getAttribute('aria-checked'),'true');
  assert.equal(screen.getByRole('radio',{name:'Cream'}).getAttribute('aria-checked'),'false');
  assert.equal(screen.getByRole('radio',{name:'Clay'}).querySelector('img').getAttribute('src'),'/app-icons/clay.png');
  assert.ok(screen.getByText('Founder'));
  assert.ok(screen.getByText(founderStatus.detail));
  assert.ok(screen.getByText(whereLine(true)));
});

test('choosing asks the native app, which checks with the host, and nothing else',async()=>{
  const view=mount(founderStatus);
  await waitFor(()=>screen.getByRole('radio',{name:'Obsidian'}));
  fireEvent.click(screen.getByRole('radio',{name:'Obsidian'}));
  await waitFor(()=>assert.equal(screen.getByRole('radio',{name:'Obsidian'}).getAttribute('aria-checked'),'true'));
  assert.deepEqual(view.calls.find(c=>c.command==='app_icon_set').args,{icon:'obsidian'});
  assert.deepEqual(view.calls.filter(c=>c.command==='local_request').map(c=>c.args.operation),['app_icon_status'],'the renderer never asks for the founder check itself');
});

test('a tester after launch sees Obsidian greyed with the reason, and it can’t be chosen',async()=>{
  const view=mount(laterStatus);
  await waitFor(()=>screen.getByRole('radio',{name:'Obsidian'}));
  const obsidian=screen.getByRole('radio',{name:'Obsidian'});
  assert.equal(obsidian.getAttribute('aria-disabled'),'true');
  assert.ok(screen.getByText(laterStatus.detail));
  fireEvent.click(obsidian);
  assert.equal(view.calls.filter(c=>c.command==='app_icon_set').length,0);
});

test('arrow keys move and choose, skipping an icon this account can’t use',async()=>{
  const view=mount(laterStatus,{icon:'clay',dock:true});
  await waitFor(()=>assert.equal(screen.getByRole('radio',{name:'Clay'}).getAttribute('aria-checked'),'true'));
  fireEvent.keyDown(screen.getByRole('radio',{name:'Clay'}),{key:'ArrowRight'});
  await waitFor(()=>assert.ok(view.calls.find(c=>c.command==='app_icon_set')));
  assert.deepEqual(view.calls.find(c=>c.command==='app_icon_set').args,{icon:'cream'},'Obsidian is skipped and it wraps to Cream');
});

test('a refused choice keeps the old one and says why',async()=>{
  mount(founderStatus,{icon:'cream',dock:true},()=>{throw 'Obsidian is for testers who joined before the public launch.';});
  await waitFor(()=>screen.getByRole('radio',{name:'Obsidian'}));
  fireEvent.click(screen.getByRole('radio',{name:'Obsidian'}));
  await waitFor(()=>assert.ok(screen.getByRole('alert')));
  assert.match(screen.getByRole('alert').textContent,/before the public launch/);
  assert.equal(screen.getByRole('radio',{name:'Cream'}).getAttribute('aria-checked'),'true');
});

test('while the founder check runs it reads again, then shows the answer',async()=>{
  let reads=0;
  mount(()=>++reads===1?{...laterStatus,founder:'checking',detail:'Checking whether Obsidian is yours…'}:founderStatus);
  await waitFor(()=>assert.ok(screen.getByText('Checking whether Obsidian is yours…')));
  await waitFor(()=>assert.ok(screen.getByText(founderStatus.detail)),{timeout:2000});
  assert.equal(screen.getByRole('radio',{name:'Obsidian'}).getAttribute('aria-disabled'),null);
});

test('a build without the Dock tile plug-in says the choice shows only while Dervo is open',async()=>{
  mount(founderStatus,{icon:'cream',dock:false});
  await waitFor(()=>assert.ok(screen.getByText(whereLine(false))));
  assert.equal(iconImage('night'),'/app-icons/night.png');
  assert.match(whereLine(true),/Finder, Spotlight and Launchpad keep the original/);
});
