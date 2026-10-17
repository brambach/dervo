import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,act}=await import('@testing-library/react');
const {Switch,Segmented,ChoiceRows,TextField,nextIndex}=await import('./DervoControls.tsx');
const h=React.createElement;
after(()=>dom.window.close());afterEach(cleanup);

function Held({kind,initial,onChange=()=>{},...props}){
 const [value,setValue]=React.useState(initial);
 const change=next=>{setValue(next);onChange(next);};
 return kind==='switch'?h(Switch,{...props,checked:value,onChange:change}):kind==='seg'?h(Segmented,{...props,value,onChange:change}):h(ChoiceRows,{...props,value,onChange:change});
}

test('a switch is a real switch: it says its state and flips on click',()=>{
 const seen=[];
 render(h(Held,{kind:'switch',initial:false,label:'A thread needs you',onChange:next=>seen.push(next)}));
 const toggle=screen.getByRole('switch',{name:'A thread needs you'});
 assert.equal(toggle.getAttribute('aria-checked'),'false');
 fireEvent.click(toggle);
 assert.equal(toggle.getAttribute('aria-checked'),'true');assert.deepEqual(seen,[true]);
 cleanup();
 render(h(Switch,{checked:false,disabled:true,onChange:()=>seen.push('no'),label:'Glass window'}));
 fireEvent.click(screen.getByRole('switch'));assert.deepEqual(seen,[true],'a disabled switch does nothing');
});

test('a segmented control is one tab stop; arrows move and choose, skipping what’s disabled, and Home and End jump',()=>{
 const seen=[];
 render(h(Held,{kind:'seg',initial:'medium',label:'Thinking',onChange:next=>seen.push(next),options:[{value:'low',label:'Low'},{value:'medium',label:'Medium'},{value:'high',label:'High',disabled:true},{value:'max',label:'Max'}]}));
 const radios=screen.getAllByRole('radio');
 assert.deepEqual(radios.map(r=>r.tabIndex),[-1,0,-1,-1]);
 radios[1].focus();
 fireEvent.keyDown(radios[1],{key:'ArrowRight'});
 assert.deepEqual(seen,['max'],'High is disabled, so the arrow lands on Max');
 assert.ok(document.activeElement===radios[3]);
 fireEvent.keyDown(document.activeElement,{key:'ArrowRight'});
 assert.equal(seen.at(-1),'low','it wraps');
 fireEvent.keyDown(document.activeElement,{key:'End'});
 assert.equal(seen.at(-1),'max');
 assert.equal(screen.getByRole('radio',{name:'Max'}).getAttribute('aria-checked'),'true');
 assert.equal(nextIndex('Enter',0,[false,false]),-1,'other keys are left alone');
});

test('choice rows: arrows move focus without choosing, Space or Return chooses, a disabled row stays readable and can’t be chosen, the chosen row lifts open',()=>{
 const seen=[];
 render(h(Held,{kind:'choices',initial:'edit',label:'What agents can do',open:true,onChange:next=>seen.push(next),choices:[
  {value:'read',name:'Read and explain',line:'Nothing changes.'},
  {value:'edit',name:'Edit files',line:'You review every change.',body:h('p',null,'Checks run first.')},
  {value:'full',name:'Full access',line:'Commands run as you.',disabled:true,end:'Needs Claude Code 2.1.271'}]}));
 const rows=screen.getAllByRole('radio');
 assert.deepEqual(rows.map(r=>r.tabIndex),[-1,0,-1]);
 assert.ok(rows[1].classList.contains('dv-open'),'the chosen row with a body is the page’s one lifted card');
 assert.match(rows[1].textContent,/Checks run first\./);
 rows[1].focus();
 fireEvent.keyDown(rows[1],{key:'ArrowDown'});
 assert.ok(document.activeElement===rows[2]);assert.deepEqual(seen,[],'moving alone changes nothing');
 fireEvent.keyDown(rows[2],{key:' '});
 assert.deepEqual(seen,[],'a disabled row can’t be chosen');
 assert.equal(rows[2].getAttribute('aria-disabled'),'true');assert.match(rows[2].textContent,/Needs Claude Code 2\.1\.271/);
 fireEvent.keyDown(rows[2],{key:'ArrowDown'});
 assert.ok(document.activeElement===rows[0]);
 fireEvent.keyDown(rows[0],{key:'Enter'});
 assert.deepEqual(seen,['read']);
 fireEvent.click(screen.getByRole('radio',{name:/Edit files/}));
 assert.deepEqual(seen,['read','edit']);
});

test('a text field saves on Return or when you leave it, puts the saved value back on Esc, and shows an error under itself',async()=>{
 const saved=[];
 render(h(TextField,{value:'dervo app',label:'Name',onCommit:next=>{saved.push(next);return next.trim()?undefined:'A project needs a name.';}}));
 const field=screen.getByRole('textbox',{name:'Name'});
 fireEvent.change(field,{target:{value:'dervo'}});
 await act(async()=>{fireEvent.keyDown(field,{key:'Enter'});});
 assert.deepEqual(saved,['dervo']);
 fireEvent.change(field,{target:{value:'dervo site'}});
 const esc=new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});
 field.dispatchEvent(esc);
 await act(async()=>{});
 assert.equal(field.value,'dervo','Esc put back the last saved value');
 assert.equal(esc.defaultPrevented,true,'and kept the Esc for itself');
 const idle=new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});
 field.dispatchEvent(idle);
 assert.equal(idle.defaultPrevented,false,'with nothing to put back, Esc is left for the page');
 fireEvent.change(field,{target:{value:'  '}});
 await act(async()=>{fireEvent.blur(field);});
 assert.deepEqual(saved,['dervo','  ']);
 assert.equal(screen.getByRole('alert').textContent,'A project needs a name.');
 assert.equal(field.getAttribute('aria-invalid'),'true');
});
