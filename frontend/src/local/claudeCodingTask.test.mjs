import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {TaskStart}=await import('./CodingTasks.tsx');
after(()=>dom.window.close());
function mount(binding,calls){
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});if(operation==='task_create')return {id:'task',state:'needs_permission'};throw Error(operation);}};
 return render(React.createElement(ThemeProvider,null,React.createElement(TaskStart,{open:true,onOpenChange:()=>{},projectId:'p',channelId:'c',binding,refresh:async()=>{}})));
}
test('a Claude Code binding can save a coding task scope through the existing host operation',async()=>{
 const calls=[];
 const view=mount({provider:'claude',identity:'personal@example.test',model:'claude-fable-5-1',cwd:'/approved',visibility:'private'},calls);
 try{
  const save=view.getByRole('button',{name:'Save task scope'});
  assert.equal(save.disabled,true,'empty scope stays disabled');
  assert.equal(view.queryByText(/blocked/),null,'no stale blocked claim for Claude');
  fireEvent.change(view.getByLabelText('Development request'),{target:{value:'Improve the test'}});
  fireEvent.change(view.getByLabelText('Allowed files'),{target:{value:'test_sample.py'}});
  fireEvent.change(view.getByLabelText('Test file'),{target:{value:'test_sample.py'}});
  assert.equal(save.disabled,false);
  fireEvent.click(save);
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='task_create').length,1));
  assert.deepEqual(calls[0].args,{projectId:'p',channelId:'c',text:'Improve the test',files:['test_sample.py'],testFile:'test_sample.py'});
 }finally{cleanup();}
});
test('an unsupported provider binding cannot save a scope',()=>{
 const calls=[];
 const view=mount({provider:'other',identity:'x',model:'m',cwd:'/approved',visibility:'shared'},calls);
 try{
  fireEvent.change(view.getByLabelText('Development request'),{target:{value:'x'}});
  fireEvent.change(view.getByLabelText('Allowed files'),{target:{value:'a.py'}});
  fireEvent.change(view.getByLabelText('Test file'),{target:{value:'a.py'}});
  assert.equal(view.getByRole('button',{name:'Save task scope'}).disabled,true);
  assert.ok(view.getByText(/Codex or Claude Code account binding/));
  assert.equal(calls.length,0);
 }finally{cleanup();}
});
