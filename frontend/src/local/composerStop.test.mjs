import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','Event','MutationObserver','SVGElement'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,cleanup,fireEvent,screen}=await import('@testing-library/react');
const {ComposerSendButton}=await import('@/features/messages/ui/ComposerAddressControls.tsx');
afterEach(cleanup);after(()=>dom.window.close());

test('while an agent works and nothing is typed, Send is Stop; typed words bring Send back (#30)',()=>{
 let stops=0;
 const view=render(React.createElement(ComposerSendButton,{isSending:false,sendDisabled:true,onStop:()=>{stops++;}}));
 const stop=screen.getByRole('button',{name:'Pause'});
 assert.equal(stop.getAttribute('type'),'button','Stop never submits the form');
 fireEvent.click(stop);assert.equal(stops,1);
 view.rerender(React.createElement(ComposerSendButton,{isSending:false,sendDisabled:false,onStop:()=>{stops++;}}));
 const send=screen.getByRole('button',{name:'Send message'});
 assert.equal(send.getAttribute('type'),'submit');
 assert.equal(screen.queryByRole('button',{name:'Pause'}),null);
 // Negative controls: with nothing running the empty composer shows a disabled Send, and a send in flight keeps Sending.
 view.rerender(React.createElement(ComposerSendButton,{isSending:false,sendDisabled:true}));
 assert.equal(screen.getByRole('button',{name:'Send message'}).disabled,true);
 view.rerender(React.createElement(ComposerSendButton,{isSending:true,sendDisabled:true,onStop:()=>{stops++;}}));
 assert.ok(screen.getByRole('button',{name:'Sending'}));
 assert.equal(stops,1);
});

test('a running thread shows one Stop: the panel’s steps aside while the composer’s is there (design review 23 Sep)',async()=>{
 const {readFileSync}=await import('node:fs');
 const css=readFileSync(new URL('./DervoComponents.css',import.meta.url),'utf8');
 assert.match(css,/\.dervo-conversation-workspace:has\(\[data-testid="stop-run"\]\) \.dv-panel-stop \{ display: none; \}/);
 const guide=readFileSync(new URL('./TaskGuide.tsx',import.meta.url),'utf8');
 assert.match(guide,/className="dv-btn dv-btn-ghost dv-btn-sm dv-panel-stop"/,'the rule names the panel’s own Stop');
 const button=readFileSync(new URL('../features/messages/ui/ComposerAddressControls.tsx',import.meta.url),'utf8');
 assert.match(button,/data-testid="stop-run"/,'and the composer’s');
});
