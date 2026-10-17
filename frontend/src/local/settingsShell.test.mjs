import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
window.requestAnimationFrame=globalThis.requestAnimationFrame;
globalThis.cancelAnimationFrame=id=>clearTimeout(id);window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.scrollIntoView=function(){};
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within,act}=await import('@testing-library/react');
const {DervoSidebar}=await import('./DervoSidebar.tsx');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
// The project picker reads archived projects and each project's counts when it opens (surfaces build).
window.__TAURI_INTERNALS__={invoke:async(_,{operation})=>operation==='projects_activity'?{}:[]};
const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const threadMenu={workstreams:[],filedUnder:()=>null,cannotMove:()=>null,rename(){},move(){},archive(){},remove:async()=>{},stop:async()=>{}};
const {SettingsNav,SettingsFrame,useSettingsSurface,useSettingsEscape,SETTINGS_ENTRY,PROJECT_PAGES,YOU_PAGES,DERVO_PAGES}=await import('./DervoSettings.tsx');
const {TextField,Select}=await import('./DervoControls.tsx');
const h=React.createElement;
after(()=>dom.window.close());afterEach(cleanup);

const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
const frame=()=>new Promise(resolve=>setTimeout(resolve,5));

/** The app as ConnectedApp wires it: the sidebar's entry points open the Settings surface, which swaps the sidebar for
 * the settings nav and covers the main panel; Esc and the back link return. The source test below holds ConnectedApp
 * to these same calls. */
function App({admin=false,page:body}){
 const settings=useSettingsSurface();
 const open=settings.page!==null;
 useSettingsEscape(open,settings.close);
 const sidebar=h(DervoSidebar,{projects:[{id:'p',name:'dervo app',cwd:'/Users/bryce/dervo/app'}],projectId:'p',projectsLoading:false,projectsFailed:false,onRetryProjects(){},onSelectProject(){},onAddProject(){},
  ready:true,conversations:[],channelId:'',conversationActive:true,onSelectConversation(){},onAction:(action,trigger)=>{if(action==='settings')settings.open('project-picker',trigger);},onCreate(){},onJump(){},
  accounts:[],onUsage(){},onWorkspaceSettings:trigger=>settings.open('account-menu',trigger),onFeedback(){},canSignOut:false,onSignOut:async()=>{},threadMenu,onRename(){},onUndo(){},onConnectedAccounts(){},accountNote:'Private beta'});
 return h(QueryClientProvider,{client},h('div',null,
  h('aside',null,settings.page&&h(SettingsNav,{page:settings.page,onPage:settings.go,onBack:settings.close,onFeedback(){},projectName:'dervo app',admin}),h('div',{className:'dv-keep','data-away':open||undefined},sidebar)),
  h('main',null,settings.page&&h(SettingsFrame,{page:settings.page,projectName:'dervo app'},body??null),h('div',{className:'dv-keep','data-away':open||undefined},h('p',null,'Home')))));
}
const openMenu=trigger=>fireEvent.keyDown(trigger,{key:'Enter'});
const pageTitle=()=>screen.getByRole('heading',{level:1}).textContent;
const esc=(target=document.activeElement??document.body)=>fireEvent.keyDown(target,{key:'Escape'});

test('each entry point opens its own page: the project picker opens Account and model, the account menu opens Account',async()=>{
 render(h(App));
 const picker=screen.getByRole('button',{name:'Project dervo app'});
 openMenu(picker);fireEvent.click(await screen.findByRole('menuitem',{name:'Project settings'}));
 assert.equal(pageTitle(),'Account and model');
 const nav=screen.getByRole('navigation',{name:'Settings'});
 assert.equal(within(nav).getByRole('button',{current:'page'}).textContent,'Account and model');
 assert.equal(document.querySelector('[data-testid="dervo-sidebar"]').closest('[data-away]')!==null,true,'the app sidebar is swapped out, not shown beside the settings nav');
 await waitFor(()=>assert.ok(document.activeElement===screen.getByRole('heading',{level:1}),'the page title takes focus once the menu has closed, so a screen reader reads where you are'));
 fireEvent.click(within(nav).getByRole('button',{name:/Back to dervo app/}));
 await act(frame);
 assert.ok(!screen.queryByTestId('dervo-settings'));
 const me=screen.getByRole('button',{name:'Your account'});
 openMenu(me);fireEvent.click(await screen.findByRole('menuitem',{name:'Settings'}));
 assert.equal(pageTitle(),'Account');
 assert.equal(within(screen.getByRole('navigation',{name:'Settings'})).getByRole('button',{current:'page'}).textContent,'Account');
 // Every other way in, as ConnectedApp maps it.
 assert.deepEqual(SETTINGS_ENTRY,{'project-picker':'model','account-menu':'account','palette-project':'model','palette-settings':'account','usage-manage':'accounts',
  'account-notice':'account','first-run-account':'account','change-model':'model','review-setup':'checks','connection':'accounts'});
});

test('Esc returns to where you were, with focus back on what opened Settings',async()=>{
 render(h(App));
 const picker=screen.getByRole('button',{name:'Project dervo app'});
 openMenu(picker);fireEvent.click(await screen.findByRole('menuitem',{name:'Project settings'}));
 assert.ok(screen.getByTestId('dervo-settings'));
 esc();
 await act(frame);
 assert.ok(!screen.queryByTestId('dervo-settings'),'Esc left Settings');
 assert.ok(!screen.queryByRole('navigation',{name:'Settings'}));
 assert.ok(!document.querySelector('[data-testid="dervo-sidebar"]').closest('[data-away]'),'the app sidebar is back');
 await waitFor(()=>assert.ok(document.activeElement===picker,'focus returns to the project picker'));
});

test('Esc in a field leaves the field first; a field that put its value back keeps Settings open; an open menu takes Esc for itself',async()=>{
 function Page(){const [value,setValue]=React.useState('dervo app');return h('div',null,
  h('input',{'aria-label':'Plain field'}),
  h(TextField,{value,label:'Project name',onCommit:next=>{setValue(next);}}),
  h(Select,{value:'personal',label:'Account for new threads',onChange(){},groups:[{label:'Claude',options:[{value:'personal',label:'Personal'},{value:'work',label:'Acme'}]}]}));}
 render(h(App,{page:h(Page)}));
 openMenu(screen.getByRole('button',{name:'Project dervo app'}));fireEvent.click(await screen.findByRole('menuitem',{name:'Project settings'}));
 const plain=screen.getByRole('textbox',{name:'Plain field'});plain.focus();
 esc(plain);await act(frame);
 assert.ok(screen.getByTestId('dervo-settings'),'the first Esc only leaves the field');
 assert.ok(document.activeElement!==plain,'Esc left the field');
 const name=screen.getByRole('textbox',{name:'Project name'});name.focus();
 fireEvent.change(name,{target:{value:'dervo ap'}});
 esc(name);await act(frame);
 assert.equal(name.value,'dervo app','Esc put the saved value back');
 assert.ok(screen.getByTestId('dervo-settings'),'and Settings stayed');
 openMenu(screen.getByRole('button',{name:'Account for new threads'}));
 const item=await screen.findByRole('menuitemradio',{name:'Acme'});
 esc(item);await act(frame);
 assert.ok(screen.getByTestId('dervo-settings'),'Esc closed the menu, not Settings');
 esc(document.body);await act(frame);
 assert.ok(!screen.queryByTestId('dervo-settings'),'with nothing else open, Esc goes back');
});

test('Testers shows only for admins; the nav is one tab stop moved with arrows; Send feedback opens the dialog, not a page',()=>{
 const pages=[],feedback=[];
 const nav=admin=>h(SettingsNav,{page:'account',onPage:page=>pages.push(page),onBack(){},onFeedback:()=>feedback.push(1),projectName:'dervo app',admin});
 const view=render(nav(false));
 const labels=()=>[...screen.getByRole('navigation',{name:'Settings'}).querySelectorAll('.dv-sb-item')].map(row=>row.textContent);
 assert.equal(labels().includes('Testers'),false,'no Testers row for someone who can’t use it');
 assert.deepEqual(labels(),['Back to dervo appesc',...PROJECT_PAGES.map(n=>n.label),...YOU_PAGES.map(n=>n.label),'Connected accounts','Updates','Send feedback']);
 view.rerender(nav(true));
 assert.deepEqual(labels().slice(-4),['Connected accounts','Updates','Testers','Send feedback']);
 assert.deepEqual(DERVO_PAGES.map(n=>n.label),['Connected accounts','Updates','Testers']);
 const rows=[...screen.getByRole('navigation',{name:'Settings'}).querySelectorAll('button:not(.dv-sb-back)')];
 assert.equal(rows.filter(row=>row.tabIndex===0).length,1,'one tab stop for the rows');
 assert.equal(rows.find(row=>row.tabIndex===0).textContent,'Account','on the current page');
 rows.find(row=>row.textContent==='Account').focus();
 fireEvent.keyDown(document.activeElement,{key:'ArrowDown'});
 assert.equal(document.activeElement.textContent,'Preferences','arrows move focus');
 assert.deepEqual(pages,[],'moving alone opens nothing');
 fireEvent.click(document.activeElement);assert.deepEqual(pages,['preferences']);
 fireEvent.keyDown(document.activeElement,{key:'End'});
 assert.equal(document.activeElement.textContent,'Send feedback');
 fireEvent.click(document.activeElement);
 assert.deepEqual(feedback,[1]);assert.deepEqual(pages,['preferences'],'Send feedback isn’t a page');
});

test('search finds existing settings by everyday phrases and keeps their scope visible',()=>{
 const pages=[];
 render(h(SettingsNav,{page:'account',onPage:page=>pages.push(page),onBack(){},onFeedback(){},projectName:'Example project',admin:false}));
 const search=screen.getByRole('searchbox',{name:'Search settings'});
 for(const [query,label,scope] of [
  ['change thinking','Account and model','New threads in Example project'],
  ['stop switching accounts','Account and model','New threads in Example project'],
  ['permissions','Access','Project: Example project'],
  ['notifications','Preferences','This Mac'],
  ['reduce motion','Preferences','This Mac'],
  ['provider login','Connected accounts','This Mac'],
  ['updates','Updates','This Mac'],
 ]){
  fireEvent.change(search,{target:{value:query}});
  const result=screen.getByRole('button',{name:label,exact:true});
  assert.equal(result.getAttribute('aria-describedby')!==null,true,'result has a scope description');
  assert.equal(document.getElementById(result.getAttribute('aria-describedby')).textContent,scope);
  assert.equal(screen.getByRole('status').textContent,'1 result');
 }
 assert.deepEqual(pages,[],'typing never changes a page');
 fireEvent.click(screen.getByRole('button',{name:'Updates',exact:true}));
 assert.deepEqual(pages,['updates']);
});

test('search supports arrows, Enter and Escape without closing Settings while editing',async()=>{
 render(h(App));
 openMenu(screen.getByRole('button',{name:'Project dervo app'}));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Project settings'}));
 const search=screen.getByRole('searchbox',{name:'Search settings'});
 fireEvent.change(search,{target:{value:'sign in'}});search.focus();
 fireEvent.keyDown(search,{key:'ArrowDown'});
 assert.equal(document.activeElement.getAttribute('aria-label'),'Account');
 fireEvent.keyDown(document.activeElement,{key:'End'});
 assert.equal(document.activeElement.getAttribute('aria-label'),'Connected accounts');
 fireEvent.keyDown(document.activeElement,{key:'Escape'});
 assert.equal(document.activeElement,search,'Escape from a result returns to the cleared search');
 assert.equal(search.value,'');
 fireEvent.change(search,{target:{value:'notifications'}});
 fireEvent.keyDown(search,{key:'Enter',isComposing:true});
 assert.equal(pageTitle(),'Account and model','IME confirmation does not open a search result');
 fireEvent.keyDown(search,{key:'Enter'});
 assert.equal(pageTitle(),'Preferences');
 search.focus();esc(search);
 assert.equal(search.value,'','first Escape clears the search');
 assert.ok(screen.getByTestId('dervo-settings'));
 esc(search);
 assert.notEqual(document.activeElement,search,'next Escape leaves the field');
 assert.ok(screen.getByTestId('dervo-settings'));
 esc(document.body);await act(frame);
 assert.ok(!screen.queryByTestId('dervo-settings'),'Escape outside the field returns to the app');
});

test('search cannot reveal admin destinations and recovers its tab stop when access changes',()=>{
 const nav=admin=>h(SettingsNav,{page:'testers',onPage(){},onBack(){},onFeedback(){},projectName:'Example project',admin});
 const view=render(nav(true));
 const search=screen.getByRole('searchbox',{name:'Search settings'});
 fireEvent.change(search,{target:{value:'waitlist'}});
 assert.ok(screen.getByRole('button',{name:'Testers',exact:true}));
 view.rerender(nav(false));
 assert.ok(!screen.queryByRole('button',{name:'Testers',exact:true}));
 assert.equal(screen.getByRole('status').textContent,'No matching settings.');
 fireEvent.change(search,{target:{value:''}});
 const stops=[...screen.getByRole('navigation',{name:'Settings'}).querySelectorAll('button:not(.dv-sb-back)')].filter(row=>row.tabIndex===0);
 assert.equal(stops.length,1,'a hidden current page cannot strand keyboard navigation');
 assert.equal(stops[0].getAttribute('aria-label'),'Account and model');
});

test('search only offers project pages when a project exists, and explains where Knowledge is',()=>{
 const nav=projectName=>h(SettingsNav,{page:'account',onPage(){},onBack(){},onFeedback(){},projectName,admin:false});
 const view=render(nav(undefined));
 const search=screen.getByRole('searchbox',{name:'Search settings'});
 fireEvent.change(search,{target:{value:'thinking'}});
 assert.ok(!screen.queryByRole('button',{name:'Account and model',exact:true}));
 assert.equal(screen.getByRole('status').textContent,'No matching settings.');
 fireEvent.change(search,{target:{value:'memory'}});
 assert.match(screen.getByText(/Project memory, rules and skills/).textContent,/Choose a project first/);
 view.rerender(nav('Example project'));
 assert.match(screen.getByText(/Project memory, rules and skills/).textContent,/Go back to the project/);
 assert.ok(!screen.queryByRole('button',{name:'Knowledge'}),'there is no unwired search destination');
 fireEvent.change(search,{target:{value:'thinking'}});
 assert.ok(screen.getByRole('button',{name:'Account and model',exact:true}));
});

test('a project named Dervo does not duplicate app destinations or search results',()=>{
 render(h(SettingsNav,{page:'account',onPage(){},onBack(){},onFeedback(){},projectName:'Dervo',admin:true}));
 assert.equal(screen.getAllByRole('button',{name:'Send feedback',exact:true}).length,1);
 fireEvent.change(screen.getByRole('searchbox',{name:'Search settings'}),{target:{value:'feedback'}});
 assert.equal(screen.getAllByRole('button',{name:'Send feedback',exact:true}).length,1);
 assert.equal(screen.getByRole('status').textContent,'1 result');
});

test('page headers distinguish new-thread defaults, project settings and Mac preferences',()=>{
 const view=render(h(SettingsFrame,{page:'model',projectName:'Example project'},null));
 assert.ok(screen.getByText('New threads in Example project'));
 assert.match(screen.getByText(/What new threads/).textContent,/Any thread can switch/);
 view.rerender(h(SettingsFrame,{page:'access',projectName:'Example project',folder:'/tmp/example'},null));
 assert.ok(screen.getByText('Project: Example project'));
 assert.ok(screen.getByText('/tmp/example'));
 view.rerender(h(SettingsFrame,{page:'preferences',projectName:'Example project'},null));
 assert.ok(screen.getByText('This Mac'));
 assert.ok(screen.getByText('Reduce motion'));
 assert.match(screen.getByText(/Dervo follows your Mac/).textContent,/System Settings, then Accessibility, Display/);
});

test('ConnectedApp wires every entry point through the surface, hides Testers from non-admins, and keeps what you came from mounted',()=>{
 for(const call of [/onWorkspaceSettings=\{trigger=>settings\.open\('account-menu',trigger\)\}/,/if\(action==='settings'\)\{settings\.open\('project-picker',trigger\);return;\}/,
  /projectSettings:\(\)=>settings\.open\('palette-project',jumpTrigger\(\)\)/,/workspaceSettings:\(\)=>settings\.open\('palette-settings',jumpTrigger\(\)\)/,
  /onManage=\{\(\)=>\{setUsageOpen\(false\);settings\.open\('usage-manage',usageTrigger\.current\);\}\}/,/<DervoAccountNotice onOpen=\{trigger=>settings\.open\('account-notice',trigger\)\}\/>/,
/settings\.open\(page==='accounts'\?'usage-manage':page==='checks'\?'review-setup':'change-model'/,/useSettingsEscape\(settingsOpen,settings\.close\);/])
  assert.match(shell,call);
 assert.match(shell,/<SettingsNav page=\{settings\.page\}[^>]*onFeedback=\{\(\)=>openFeedback\(\)\}[^>]*admin=\{accountData\?\.membership\?\.is_admin===true\} testersCount=/);
 assert.match(shell,/\{settingsPage==='testers'&&accountData\?\.membership\?\.is_admin===true&&/);
 assert.match(shell,/<div className="dv-keep" data-away=\{settingsOpen\|\|undefined\}><DervoSidebar /);
 assert.match(shell,/<div className="dv-keep" data-away=\{settingsOpen\|\|undefined\}>\n\s*\{firstRunMode\?/);
 assert.doesNotMatch(shell,/Workspace settings|WorkspaceSettingsLayout|ProjectSettingsPage/,'the old dialog and page are gone');
 // Design sweep, first run (brand/): sign-in is first run's own step 1 now, so first run no longer opens Account.
 assert.doesNotMatch(shell,/first-run-account/);
 // Area 1's pages (settings/, 19 Sep): each page mounts its own screen, and the Usage dialog is retired for the popover.
 for(const mount of [/settingsPage==='account'&&<DervoAccount\/>/,/settingsPage==='preferences'&&<><ThemeTiles\/><NotificationSettings\/><KeepAwakeSetting\/><PhoneSettings\/><GlassWindow\/><\/>/,
  /settingsPage==='accounts'&&<ConnectedAccounts /,/settingsPage==='updates'&&<BetaUpdates projectId=/,/settingsPage==='testers'&&accountData\?\.membership\?\.is_admin===true&&<TestersPage\/>/,
  /<UsagePopover open=\{usageOpen\}/,/testersCount=\{testers\.data\?\.people\.filter\(person=>person\.status==='pending'\)\.length\|\|undefined\}/])
  assert.match(shell,mount);
 assert.doesNotMatch(shell,/UsageDialog/,'the Usage dialog is gone');
 const connected=readFileSync(new URL('./ConnectedAccounts.tsx',import.meta.url),'utf8');
 assert.match(connected,/<OpenRouterSettings\/>/);
 assert.match(connected,/function HelpBetweenAccounts/,'account linking and asking helpers moved here from Advanced');
});
