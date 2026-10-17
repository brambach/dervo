import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','MutationObserver','KeyboardEvent','MouseEvent','PointerEvent','Event','CustomEvent','FocusEvent','DocumentFragment','NodeFilter'])if(dom.window[key])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,cleanup,screen,waitFor}=await import('@testing-library/react');
const {ReplyActionsContext}=await import('./replyActions.ts');
const {ExternalLinkAnchor}=await import('./ExternalLinkAnchor.tsx');
const {MarkdownCodeBlock}=await import('./CodeBlock.tsx');
const {TooltipProvider}=await import('@/shared/ui/tooltip');
const renderIn=node=>render(React.createElement(TooltipProvider,null,node));
after(()=>dom.window.close());
afterEach(()=>cleanup());

function actions(extra={}){
 const calls=[];
 return {calls,value:{commandsAllowed:false,run:async(command,label)=>{calls.push(['run',command,label]);},preview:target=>calls.push(['preview',target]),
  reveal:async path=>calls.push(['reveal',path]),openDefault:async path=>calls.push(['open',path]),...extra}};
}
const withActions=(value,node)=>React.createElement(ReplyActionsContext.Provider,{value},node);
const block=(code,language)=>React.createElement(MarkdownCodeBlock,{language},React.createElement('code',{className:language?`language-${language}`:undefined},code));

test('a command block gets Run only in a Dervo reply, and a quiet command runs on the click',async()=>{
 renderIn(block('git status\n','bash'));
 assert.equal(screen.queryByRole('button',{name:/Run in the project terminal/}),null,'no Run outside Dervo');
 cleanup();
 const {calls,value}=actions();
 renderIn(withActions(value,block('git status\n','bash')));
 fireEvent.click(screen.getByRole('button',{name:'Run in the project terminal: git status'}));
 await waitFor(()=>assert.deepEqual(calls,[['run','git status','From a reply']]));
 cleanup();
 renderIn(withActions(value,block('const x = 1;\n','ts')));
 assert.equal(screen.queryByRole('button',{name:/Run in the project terminal/}),null,'code is not a command');
});

test('a command that installs or changes files asks once, in plain words, unless agent commands are on',async()=>{
 const {calls,value}=actions();
 renderIn(withActions(value,block('npm install\n','bash')));
 fireEvent.click(screen.getByRole('button',{name:/Run in the project terminal/}));
 const ask=screen.getByRole('group',{name:'Run this command?'});
 assert.match(ask.textContent,/installs or updates software/);
 assert.deepEqual(calls,[],'nothing runs before you answer');
 fireEvent.keyDown(ask,{key:'Escape'});
 assert.equal(screen.queryByRole('group',{name:'Run this command?'}),null);
 // Design review, 23 Sep: the question leaves with the focused button in it, so focus goes back to Run, not the page.
 assert.equal(document.activeElement,screen.getByRole('button',{name:/Run in the project terminal/}));
 fireEvent.click(screen.getByRole('button',{name:/Run in the project terminal/}));
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 assert.equal(document.activeElement,screen.getByRole('button',{name:/Run in the project terminal/}),'and after Cancel');
 const {readFileSync}=await import('node:fs');
 const css=readFileSync(new URL('./ReplyChips.css',import.meta.url),'utf8');
 assert.match(css,/\.dv-reply-run \{[^}]*box-shadow: var\(--dv-btn-lit, none\);/,'a pine pill with the lit edge, like the others');
 assert.match(css,/\.dv-reply-run:active \{ transform: translateY\(1px\) scale\(\.985\); box-shadow: var\(--dv-btn-pressed, none\);/);
 fireEvent.click(screen.getByRole('button',{name:/Run in the project terminal/}));
 fireEvent.click(screen.getByRole('button',{name:'Run it'}));
 await waitFor(()=>assert.deepEqual(calls,[['run','npm install','From a reply']]));
 cleanup();
 const allowed=actions({commandsAllowed:true});
 renderIn(withActions(allowed.value,block('npm install\n','bash')));
 fireEvent.click(screen.getByRole('button',{name:/Run in the project terminal/}));
 await waitFor(()=>assert.deepEqual(allowed.calls,[['run','npm install','From a reply']]));
 assert.equal(screen.queryByRole('group',{name:'Run this command?'}),null,'the setting allows it');
});

test('Run on a Settings command row tells the terminal it came from Settings, not a reply (design review, 23 Sep)',async()=>{
 const {CopyCommand}=await import('../../../local/SettingsKit.tsx');
 const {calls,value}=actions();
 renderIn(withActions(value,React.createElement(CopyCommand,{command:'claude --version'})));
 fireEvent.click(screen.getByRole('button',{name:/Run in the project terminal/}));
 await waitFor(()=>assert.deepEqual(calls,[['run','claude --version','From Settings']]));
});

test('a local page link opens in Preview on click and gets Open; it never navigates the app',async()=>{
 const {calls,value}=actions();
 renderIn(withActions(value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'http://localhost:8840/mock.html',label:'mock',isLinearLink:false},'the mockup')));
 assert.equal(fireEvent.click(screen.getByRole('link',{name:'the mockup'})),false,'default navigation prevented');
 fireEvent.click(screen.getByRole('button',{name:'Open localhost:8840/mock.html in Preview'}));
 assert.deepEqual(calls,[['preview',{kind:'page',url:'http://localhost:8840/mock.html'}],['preview',{kind:'page',url:'http://localhost:8840/mock.html'}]]);
 cleanup();
 // A remote link keeps its old behaviour: no Open, no Preview.
 renderIn(withActions(value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'https://example.com:8840/',label:'x',isLinearLink:false},'remote')));
 assert.equal(screen.queryByRole('button',{name:/in Preview/}),null);
});

test('with no Preview column in the thread, the buttons say where things open instead (design review, 23 Sep)',async()=>{
 const {value}=actions({previewHere:false});
 renderIn(withActions(value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'http://localhost:8840/mock.html',label:'mock',isLinearLink:false},'the mockup')));
 assert.ok(screen.getByRole('button',{name:'Open localhost:8840/mock.html in your browser'}));
 assert.equal(screen.queryByRole('button',{name:/in Preview/}),null,'it doesn’t open in Preview here, so it doesn’t say so');
 cleanup();
 renderIn(withActions(value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'evidence/mock.html',label:'mock',isLinearLink:false},'mock.html')));
 fireEvent.keyDown(screen.getByRole('button',{name:'evidence/mock.html: open, show in Finder or copy'}),{key:'Enter'});
 const items=await waitFor(()=>{const found=screen.getAllByRole('menuitem').map(item=>item.textContent);if(!found.length)throw new Error('no menu');return found;});
 assert.deepEqual(items,['Show in Finder','Open with default app','Copy path']);
 cleanup();
 renderIn(withActions(actions().value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'evidence/mock.html',label:'mock',isLinearLink:false},'mock.html')));
 fireEvent.keyDown(screen.getByRole('button',{name:'evidence/mock.html: open, show in Finder or copy'}),{key:'Enter'});
 await waitFor(()=>assert.equal(screen.getAllByRole('menuitem')[0].textContent,'Open in Preview'),'with a Preview column, Preview leads');
});

// 16.2: Copy path went through a native command Dervo's host doesn't have, so it always said "Failed to copy to clipboard".
test('Copy path writes the path with the webview clipboard and says "Path copied"',async()=>{
 const {Toaster}=await import('sonner');
 const written=[];
 let refuse=false;
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{if(refuse)throw new Error('NotAllowedError');written.push(text);}}});
 const copyFrom=async()=>{
  renderIn(React.createElement(React.Fragment,null,React.createElement(Toaster),
   withActions(actions().value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'evidence/mock.html',label:'mock',isLinearLink:false},'mock.html'))));
  fireEvent.keyDown(screen.getByRole('button',{name:'evidence/mock.html: open, show in Finder or copy'}),{key:'Enter'});
  const item=await waitFor(()=>screen.getByRole('menuitem',{name:'Copy path'}));
  fireEvent.click(item);
 };
 try{
  await copyFrom();
  await waitFor(()=>assert.ok(screen.getByText('Path copied')));
  assert.deepEqual(written,['evidence/mock.html']);
  assert.equal(screen.queryByText('Failed to copy to clipboard'),null);
  cleanup();
  refuse=true;
  await copyFrom();
  await waitFor(()=>assert.ok(screen.getByText('Failed to copy to clipboard')),'a refused write still says so');
 }finally{delete navigator.clipboard;}
});

test('a file link becomes a path chip with Preview, Finder and the default app',()=>{
 const {value}=actions();
 renderIn(withActions(value,React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'evidence/mock.html',label:'mock',isLinearLink:false},'mock.html')));
 const chip=screen.getByRole('button',{name:'evidence/mock.html: open, show in Finder or copy'});
 assert.equal(chip.getAttribute('aria-haspopup'),'menu');
});
