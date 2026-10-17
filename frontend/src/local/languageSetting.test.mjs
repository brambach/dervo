import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent','FocusEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);window.requestAnimationFrame=globalThis.requestAnimationFrame;
globalThis.cancelAnimationFrame=id=>clearTimeout(id);window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.scrollIntoView=function(){};
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
const React=await import('react');
const {render,fireEvent,screen,cleanup,within}=await import('@testing-library/react');
const {LanguageSetting}=await import('./LanguageSetting.tsx');
const {SettingsFrame,SettingsNav}=await import('./DervoSettings.tsx');
const i18n=await import('@/i18n');
const h=React.createElement;
after(()=>dom.window.close());
afterEach(()=>{cleanup();window.localStorage.clear();i18n.saveLanguageChoice('system');i18n.useLanguageForTests('en');});

/** Language, on Preferences (beta.17, Bruno's report on beta.14): Match macOS by default, each language named in itself,
 * a pick that changes the language saves it and reloads the window, and search finds the row in either language. */
const pick=async name=>{
  fireEvent.keyDown(screen.getByRole('button',{name:'Language'}),{key:'Enter'});
  fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitemradio',{name}));
};

test('Language leads Preferences and starts on Match macOS, saying what that comes to', ()=>{
  render(h(SettingsFrame,{page:'preferences',projectName:'Example project'},h('section',{'aria-labelledby':'theme'},h('h2',{id:'theme'},'Theme'))));
  const titles=screen.getAllByRole('heading',{level:2}).map(heading=>heading.textContent);
  assert.deepEqual(titles.slice(0,2),['Language','Theme'],'first on the page, above Theme');
  assert.equal(screen.getByRole('button',{name:'Language'}).textContent,'Match macOS · English');
  cleanup();
  render(h(SettingsFrame,{page:'general',projectName:'Example project'},null));
  assert.equal(screen.queryByRole('button',{name:'Language'}),null,'only on Preferences');
});

test('choosing Portuguese saves it and reloads the window; choosing what’s already shown doesn’t', async ()=>{
  const reloads=[];
  render(h(LanguageSetting,{reload:()=>{reloads.push(i18n.languageChoice());}}));
  await pick('English');
  assert.equal(window.localStorage.getItem('dervo-language'),'en');
  assert.deepEqual(reloads,[],'English is what the window shows, so nothing reloads');
  await pick('Português (Brasil)');
  assert.equal(window.localStorage.getItem('dervo-language'),'pt-BR');
  assert.deepEqual(reloads,['pt-BR']);
  assert.equal(screen.getByRole('button',{name:'Language'}).disabled,true,'one switch at a time');
});

test('Match macOS clears the saved pick', async ()=>{
  window.localStorage.setItem('dervo-language','en');
  i18n.saveLanguageChoice('en');
  render(h(LanguageSetting,{reload:()=>{}}));
  await pick(/^Match macOS/);
  assert.equal(window.localStorage.getItem('dervo-language'),null);
});

test('search finds the Language row from English or Portuguese words, with or without accents', ()=>{
  render(h(SettingsNav,{page:'account',onPage(){},onBack(){},onFeedback(){},projectName:'Example project',admin:false}));
  const search=screen.getByRole('searchbox',{name:'Search settings'});
  for(const query of ['language','idioma','língua','mudar o idioma','portugues']){
    fireEvent.change(search,{target:{value:query}});
    assert.ok(screen.getByRole('button',{name:'Preferences',exact:true}),query);
    assert.equal(screen.getByRole('status').textContent,'1 result',query);
  }
});
