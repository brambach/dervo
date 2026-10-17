import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {existsSync,readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,cleanup,screen}=await import('@testing-library/react');
const {ConversationIndicator}=await import('./TurnControls.tsx');
const {useDervoBrand}=await import('./DervoBrand.tsx');
afterEach(cleanup);after(()=>dom.window.close());

test('observed work marks the status row active, while lost observation retains an explicit unknown label with a still row',()=>{
  const props={binding:{provider:'codex',identity:'synthetic@example.test',model:'test-model'},onSettings(){}};
  const view=render(React.createElement(ConversationIndicator,{...props,status:{state:'working',active:true,detail:'Reading a file.'}}));
  assert.equal(document.querySelectorAll('.dervo-activity[data-active]').length,1);
  assert.ok(screen.getByText('Codex is working'));
  view.rerender(React.createElement(ConversationIndicator,{...props,status:{state:'unknown',active:true,review_active:true,detail:'Delivery needs checking.'}}));
  assert.equal(document.querySelectorAll('.dervo-activity[data-active]').length,0);
  assert.ok(screen.getByText('Delivery unknown'));
  view.rerender(React.createElement(ConversationIndicator,{...props,error:true,status:{state:'working',active:true,detail:'Stale status.'}}));
  assert.equal(document.querySelectorAll('.dervo-activity[data-active]').length,0);
  assert.ok(screen.getByText('Status unavailable'));
  assert.equal(document.querySelector('svg').getAttribute('aria-hidden'),'true','status text supplies the accessible meaning');
});

test('workspace brand activation leaves stored appearance preferences intact and restores its root marker on unmount',()=>{
  localStorage.setItem('buzz-theme','existing-preference');
  function Workspace(){useDervoBrand();return null;}
  const view=render(React.createElement(Workspace));
  assert.equal(document.documentElement.hasAttribute('data-dervo-local'),true);
  view.unmount();
  assert.equal(document.documentElement.hasAttribute('data-dervo-local'),false);
  assert.equal(localStorage.getItem('buzz-theme'),'existing-preference');
});

test('the inherited theme reads the dv- palette: every HSL token is remapped, Manrope is gone, and the type is Instrument Sans at the token scale',()=>{
  const brand=readFileSync(new URL('./DervoBrand.css',import.meta.url),'utf8');
  assert.equal(brand.includes('Manrope'),false,'Manrope retired (Bryce, 18 Sep: one type system)');
  assert.equal(existsSync(new URL('./brand-assets/Manrope.woff2',import.meta.url)),false);
  assert.equal(existsSync(new URL('./brand-assets/Manrope-OFL.txt',import.meta.url)),false);
  assert.match(brand,/:root\[data-dervo-local\] body \{[^}]*font-family: var\(--dv-font-text\);[^}]*font-size: var\(--dv-fs-body\);[^}]*line-height: var\(--dv-lh-ui\);/);
  assert.match(brand,/:root\[data-dervo-local\] :is\(code, kbd, pre, samp, \.font-mono\) \{ font-family: var\(--dv-font-mono\); \}/);
  assert.match(brand,/--conversation-message-font-size: var\(--dv-fs-read\);/);
  for(const token of ['--background','--foreground','--card','--popover','--primary','--primary-foreground','--secondary','--muted','--muted-foreground','--accent','--destructive','--border','--input','--ring','--sidebar-background','--sidebar-foreground','--sidebar-primary','--sidebar-accent','--sidebar-border','--sidebar-ring'])
    assert.match(brand,new RegExp(`\\n  ${token}: var\\(--dv-hsl-[a-z0-9-]+\\) !important;`),token);
  // The old amber and red map onto the palette's own states: green runs, rust needs you.
  assert.match(brand,/--dervo-live: var\(--dv-hsl-live\);/);assert.match(brand,/--dervo-alert: var\(--dv-hsl-rust\);/);assert.match(brand,/--dervo-moss: var\(--dv-hsl-sage\);/);
  // No stray colour: every hex in the file is a dv- palette value listed as an HSL comment or the paper the retired gradient paints.
  const hexes=[...brand.matchAll(/#[0-9a-f]{6}\b/gi)].map(m=>m[0].toLowerCase());
  const tokens=readFileSync(new URL('./DervoTokens.css',import.meta.url),'utf8').toLowerCase();
  for(const hex of hexes)assert.ok(tokens.includes(hex),`${hex} is a token`);
  // The wash: the meadow, blurred and faded behind the sidebar, at the token strength; the main panel flat with a hairline.
  assert.match(brand,/\.dv-window::before \{[^}]*opacity: var\(--dv-wash\); filter: blur\(14px\) saturate\(\.8\);[^}]*studio-meadow\.webp'\) 28% center \/ auto 100% no-repeat;/);
  assert.match(brand,/\.dv-window > \.dv-main \{ background: var\(--dv-canvas\); box-shadow: -1px 0 0 var\(--dv-line\);/);
  assert.equal(brand.includes('data-buzz-content-surface'),false,'no floating card');
});

test('the window clips the wash instead of hiding it, so nothing can scroll the whole app up under the traffic lights',()=>{
  const brand=readFileSync(new URL('./DervoBrand.css',import.meta.url),'utf8');
  // The wash still reaches past the window, which is what gave overflow: hidden its 40px of scroll room.
  assert.match(brand,/\.dv-window::before \{[^}]*inset: -40px;/);
  const clipped=css=>[...css.matchAll(/:root\[data-dervo-local\] \.dv-window \{([^}]*)\}/g)].some(m=>/(^|;)\s*overflow: clip;/.test(m[1]));
  assert.ok(clipped(brand),'the window is overflow: clip');
  // Negative control: the rule as it was before, with the window left to the shell's overflow-hidden, isn't clipped.
  assert.equal(clipped(':root[data-dervo-local] .dv-window { --sidebar-width: 252px !important; }'),false);
});

test('the shared Button and Input mark themselves so the tokens can style every variant as pills and rings',()=>{
  const button=readFileSync(new URL('../shared/ui/button.tsx',import.meta.url),'utf8');
  assert.match(button,/data-dv-btn=\{variant \?\? "default"\}/);assert.match(button,/data-dv-size=\{size \?\? "default"\}/);
  for(const file of ['../shared/ui/input.tsx','../shared/ui/textarea.tsx'])assert.match(readFileSync(new URL(file,import.meta.url),'utf8'),/data-dv-input=""/,file);
  const brand=readFileSync(new URL('./DervoBrand.css',import.meta.url),'utf8');
  assert.match(brand,/\[data-dv-btn\] \{[^}]*border-radius: var\(--dv-r-pill\);/);
  // The primary pill carries the landing's lit edge (design report, 23 Sep, #5); a delete is ink, never rust (#2).
  assert.match(brand,/\[data-dv-btn="default"\] \{ background: var\(--dv-pine\); color: var\(--dv-pine-ink\); box-shadow: var\(--dv-btn-lit\); \}/);
  assert.match(brand,/\[data-dv-btn="destructive"\] \{ background: var\(--dv-ink-2\); color: var\(--dv-canvas\); \}/);
  assert.doesNotMatch(brand,/--destructive: var\(--dv-hsl-rust\)/,'the inherited destructive colour no longer borrows the one accent');
  assert.match(brand,/:is\(\[data-dv-btn="default"\], \[data-dv-btn="destructive"\]\):active:not\(:disabled\) \{ transform: translateY\(1px\) scale\(\.985\); box-shadow: var\(--dv-btn-pressed\);/,'a filled pill sinks a pixel when pressed, as the landing’s does');
  assert.match(brand,/\[data-dv-btn="default"\]:disabled \{ box-shadow: none; \}/,'and a disabled one has no lit edge');
  assert.match(brand,/\[data-dv-btn="outline"\] \{[^}]*box-shadow: inset 0 0 0 1px var\(--dv-line-strong\); \}/);
  assert.match(brand,/\[data-dv-btn="ghost"\] \{[^}]*color: var\(--dv-muted\); \}/);
  assert.match(brand,/\[data-dv-input\] \{[^}]*box-shadow: inset 0 0 0 1px var\(--dv-line-strong\);/);
});
