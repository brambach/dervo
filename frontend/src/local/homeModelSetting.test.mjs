import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
globalThis.window=dom.window;globalThis.document=dom.window.document;for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
const React=await import('react');
const {render,fireEvent,cleanup,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {HomeModelSetting,homeModelRows,HOME_DEFAULT_LINE}=await import('./HomeModelSetting.tsx');
after(()=>{cleanup();dom.window.close();});
afterEach(()=>cleanup());

// Bryce, 2 Oct (b17/home): Home's own reply model is the person's pick per project and provider, with a thinking level.
const CATALOG={state:'catalog',detail:'',models:[
 {model:'gpt-6-sol',displayName:'GPT-6-Sol',description:'Workhorse model for coding and everyday work.',supportedReasoningEfforts:[{reasoningEffort:'low'},{reasoningEffort:'medium'},{reasoningEffort:'high'}],defaultReasoningEffort:'medium'},
 {model:'gpt-6-luna',displayName:'GPT-6-Luna',description:'Fast and affordable model for easier tasks.',supportedReasoningEfforts:[{reasoningEffort:'low'},{reasoningEffort:'medium'}],defaultReasoningEffort:'medium'},
 {model:'gpt-secret',displayName:'Hidden one',hidden:true}]};
const CODEX_ACCOUNT={id:'cx',provider:'codex',name:'ChatGPT',identity:'me@example.test',work:false,plan:'Plus',profile:{id:'cx',provider:'codex',identity:'me@example.test',category:'personal',method:'subscription',plan:null}};
const CLAUDE_ACCOUNT={id:'cs',provider:'claude',name:'Personal',identity:'me@example.test',work:false,plan:'Pro',profile:{id:'cs',provider:'claude',identity:'me@example.test',category:'personal',method:'subscription',plan:'pro'}};

function mount(home,account){
 const calls=[],saved=[];let backs=0;
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);if(operation==='model_status')return CATALOG;
  if(operation==='orchestrator_home_model')return {entries:[],home:{...home,chosen:args.model!==null,model_id:args.model??home.model_id,effort:args.effort,model:args.label??home.model}};return {};}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(HomeModelSetting,{home,account,claudeVersion:'2.1.290',projectId:'p',profileId:'cx',
  onSaved:listing=>saved.push(listing),onBack:()=>{backs++;}})));
 return {view,calls,saved,backs:()=>backs};
}
const sent=calls=>calls.filter(([op])=>op==='orchestrator_home_model').map(([,a])=>a);

test('the rows are Dervo’s default first, then the account’s models, hidden ones left out',()=>{
 const codex=homeModelRows('codex',null,CATALOG.models);
 assert.deepEqual(codex.map(r=>[r.id,r.label]),[[null,'Dervo’s default'],['gpt-6-sol','GPT-6-Sol'],['gpt-6-luna','GPT-6-Luna']]);
 assert.equal(codex[0].line,HOME_DEFAULT_LINE.codex);
 const claude=homeModelRows('claude','2.1.290');
 assert.equal(claude[0].id,null);
 assert.ok(claude.some(r=>r.id==='claude-opus-5-5')&&claude.some(r=>r.id==='claude-sonnet-5-5'),'the Claude list the thread picker shows');
});

test('on a Codex project the default is ticked, and picking Luna saves it with its name for this project',async()=>{
 const {view,calls,saved}=mount({provider:'codex',profile_id:'cx',model:'GPT-6-Sol',model_id:'gpt-6-sol',chosen:false,effort:null},CODEX_ACCOUNT);
 const list=await view.findByRole('listbox',{name:'Home’s model'});
 const options=within(list).getAllByRole('option');
 assert.deepEqual(options.map(o=>o.querySelector('b').textContent),['Dervo’s default','GPT-6-Sol','GPT-6-Luna']);
 assert.equal(options[0].getAttribute('aria-selected'),'true','nothing picked: Dervo’s default');
 assert.ok(view.getByText(/on ChatGPT\. A lighter model answers sooner/));
 fireEvent.click(options[2]);
 await waitFor(()=>assert.equal(saved.length,1));
 assert.deepEqual(sent(calls),[{projectId:'p',profileId:'cx',provider:'codex',model:'gpt-6-luna',effort:null,label:'GPT-6-Luna'}]);
});

test('Thinking saves a level for the model that runs, and a model that offers the level keeps it',async()=>{
 const {view,calls}=mount({provider:'codex',profile_id:'cx',model:'GPT-6-Sol',model_id:'gpt-6-sol',chosen:true,effort:'low'},CODEX_ACCOUNT);
 const group=await view.findByRole('radiogroup',{name:'Thinking'});
 assert.deepEqual(within(group).getAllByRole('radio').map(r=>r.textContent),['Default','Low','Medium','High']);
 fireEvent.click(within(group).getByRole('radio',{name:'High'}));
 await waitFor(()=>assert.equal(sent(calls).length,1));
 assert.deepEqual(sent(calls)[0],{projectId:'p',profileId:'cx',provider:'codex',model:'gpt-6-sol',effort:'high',label:'GPT-6-Sol'});
 // Luna offers Low too, so the level goes with it. Back to Dervo's default clears both.
 fireEvent.click(view.getByRole('option',{name:/GPT-6-Luna/}));
 await waitFor(()=>assert.equal(sent(calls).length,2));
 assert.equal(sent(calls)[1].effort,'low');
 fireEvent.click(view.getByRole('option',{name:/Dervo’s default/}));
 await waitFor(()=>assert.equal(sent(calls).length,3));
 assert.deepEqual(sent(calls)[2],{projectId:'p',profileId:'cx',provider:'codex',model:null,effort:null});
});

test('on a Claude project the Claude list shows and a pick goes out as provider claude; Back goes back',async()=>{
 const {view,calls,backs}=mount({provider:'claude',profile_id:'cs',model:'Sonnet',model_id:null,chosen:false,effort:null},CLAUDE_ACCOUNT);
 const list=view.getByRole('listbox',{name:'Home’s model'});
 assert.equal(within(list).getAllByRole('option')[0].getAttribute('aria-selected'),'true');
 assert.equal(calls.filter(([op])=>op==='model_status').length,0,'no Codex catalog is read for Claude');
 fireEvent.click(within(list).getByRole('option',{name:/Opus 5\.5/}));
 await waitFor(()=>assert.equal(sent(calls).length,1));
 assert.equal(sent(calls)[0].provider,'claude');
 assert.equal(sent(calls)[0].model,'claude-opus-5-5');
 fireEvent.click(view.getByRole('button',{name:'Back'}));
 assert.equal(backs(),1);
});

test('a save the host refuses says why and keeps the screen',async()=>{
 const {view}=mount({provider:'codex',profile_id:'cx',model:'GPT-6-Sol',model_id:'gpt-6-sol',chosen:false,effort:null},CODEX_ACCOUNT);
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{if(operation==='model_status')return CATALOG;if(operation==='orchestrator_home_model')throw new Error('Choose a model with a valid model ID.');return {};}};
 fireEvent.click(await view.findByRole('option',{name:/GPT-6-Luna/}));
 assert.equal((await view.findByRole('alert')).textContent,'Choose a model with a valid model ID.');
 assert.ok(view.getByRole('listbox',{name:'Home’s model'}));
});

test('Home saved on GPT-5.5 says it leaves Codex and moves to GPT-6 Sol on one click, keeping a level GPT-6 Sol offers',async()=>{
 const leaving={model:'gpt-5.5',displayName:'GPT-5.5',description:'Legacy coding model.',upgradeInfo:{model:'gpt-6-sol'},supportedReasoningEfforts:[{reasoningEffort:'low'},{reasoningEffort:'medium'}],defaultReasoningEffort:'medium'};
 CATALOG.models.push(leaving);
 try{
  const {view,calls}=mount({provider:'codex',profile_id:'cx',model:'GPT-5.5',model_id:'gpt-5.5',chosen:true,effort:'low'},CODEX_ACCOUNT);
  const note=await waitFor(()=>{const found=document.querySelector('.dv-pk-retire');assert.ok(found);return found;});
  assert.match(note.textContent,/GPT-5\.5 (leaves|left) Codex on (14 October|October 14)\./);
  assert.match(within(view.getByRole('listbox',{name:'Home’s model'})).getByRole('option',{name:/^GPT-5\.5/}).textContent,/Previous generation\. (Leaves|Left) Codex/);
  assert.equal(sent(calls).length,0,'nothing moves by itself');
  fireEvent.click(within(note).getByRole('button',{name:'Move Home to GPT-6-Sol'}));
  await waitFor(()=>assert.equal(sent(calls).length,1));
  assert.deepEqual(sent(calls)[0],{projectId:'p',profileId:'cx',provider:'codex',model:'gpt-6-sol',effort:'low',label:'GPT-6-Sol'});
 }finally{CATALOG.models.pop();cleanup();}
 // Home on Dervo's default (GPT-6 Sol) or a model that isn't leaving shows no notice.
 const {view}=mount({provider:'codex',profile_id:'cx',model:'GPT-6-Sol',model_id:'gpt-6-sol',chosen:true,effort:null},CODEX_ACCOUNT);
 await view.findByRole('listbox',{name:'Home’s model'});
 assert.equal(document.querySelector('.dv-pk-retire'),null);
});
