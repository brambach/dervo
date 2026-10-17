import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
globalThis.window=dom.window;globalThis.document=dom.window.document;for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);dom.window.HTMLElement.prototype.scrollIntoView=()=>{};dom.window.HTMLElement.prototype.hasPointerCapture=()=>false;dom.window.HTMLElement.prototype.releasePointerCapture=()=>{};
const React=await import('react');
const {render,fireEvent,cleanup,waitFor,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ModelPicker,effortsFor,modelLabel,CLAUDE_CHOICES}=await import('./ModelPicker.tsx');
const models=await import('./models.ts');
after(()=>{cleanup();dom.window.close();});

// Bryce's three accounts, by the names he gave them on Connected accounts.
const PROFILES=[
 {id:'cs',provider:'claude',identity:'bryce@trydervo.com',category:'personal',method:'subscription',plan:'pro',fable_confirmed:false,name:'Personal'},
 {id:'cd',provider:'claude',identity:'bryce@acme.com',category:'personal',method:'subscription',plan:'team',fable_confirmed:false,name:'Acme'},
 {id:'cx',provider:'codex',identity:'bryce@trydervo.com',category:'personal',method:'subscription',plan:null,fable_confirmed:false},
 {id:'org',provider:'codex',identity:'team',category:'organisation'},
];
const USAGE=[{profile_id:'cs',provider:'claude',identity:'bryce@trydervo.com',method:'subscription',percent:36,basis:'rate_limits',resets_at:null,rate_limits:{}},
 {profile_id:'cx',provider:'codex',identity:'bryce@trydervo.com',method:'subscription',percent:41,basis:'rate_limits',resets_at:null,rate_limits:{planType:'plus'}}];
const CATALOG={state:'catalog',detail:'',models:[
 {model:'gpt-5.6-sol',displayName:'GPT-5.6 Sol',description:'Deep reasoning for complex work.',supportedReasoningEfforts:[{reasoningEffort:'low'},{reasoningEffort:'high'},{reasoningEffort:'ultra'}],defaultReasoningEffort:'high'},
 {model:'gpt-new-one',displayName:'GPT New One',description:'A model Dervo has no line for yet.',supportedReasoningEfforts:[]}]};

// 16.4: the thread's own level, the defaults it falls back to and the one a run uses (coding_permission's efforts).
const OWN_HIGH={thread:'high',project:null,personal:null,effective:'high',source:'thread'};
function mount(extra={},fail,efforts=OWN_HIGH){
 const calls=[];let changed=0;
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);if(operation==='model_status')return CATALOG;if(fail?.[operation])throw new Error(fail[operation]);
  if(operation==='coding_permission')return {efforts:typeof efforts==='function'?efforts():efforts};return {};}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const props={projectId:'p',channelId:'c',profiles:PROFILES,usage:USAGE,binding:{profile_id:'cs',provider:'claude',model:'claude-opus-5'},project:{profile_id:'cs',model:'claude-opus-5'},
  effort:'high',onChanged:async()=>{changed++;},...extra};
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(ModelPicker,props)));
 const open=async()=>{const trigger=view.getByTestId('model-picker');await act(async()=>{fireEvent.pointerDown(trigger,{button:0,ctrlKey:false});fireEvent.click(trigger);});await waitFor(()=>assert.ok(document.querySelector('.dv-picker')));};
 const writes=()=>calls.filter(([op])=>!['model_status','model_check','claude_version','coding_permission'].includes(op));
 return {view,calls,writes,open,changed:()=>changed};
}
const accounts=()=>[...document.querySelectorAll('.dv-pk-acct')];
const account=name=>accounts().find(row=>row.querySelector('b').textContent===name);
const rows=()=>[...document.querySelectorAll('.dv-pk-model')];
const row=label=>rows().find(r=>r.querySelector('b').firstChild.textContent===label);
const click=async el=>{await act(async()=>{fireEvent.click(el);});};

test('every personal account shows by name, grouped under Claude and Codex, both Claude accounts included',async()=>{
 const {open}=mount();
 try{
  await open();
  assert.deepEqual([...document.querySelectorAll('.dv-pk-group')].map(g=>[g.getAttribute('aria-label'),[...g.querySelectorAll('.dv-pk-acct b')].map(b=>b.textContent)]),
   [['Claude',['Personal','Acme']],['Codex',['ChatGPT Plus']]],'the second Claude account is reachable; the organisation account is not offered');
  // Beta.18, compact: a small figure on the row, the whole caption for screen readers and the row's title, and no bar.
  const said=name=>account(name).querySelector('small .sr-only').textContent,figure=name=>account(name).querySelector('.dv-pk-fig').textContent;
  assert.deepEqual([said('Personal'),figure('Personal')],['Pro · 64% left','64%']);
  assert.deepEqual([said('ChatGPT Plus'),figure('ChatGPT Plus')],['Plus · 59% left','59%'],'without a name, a ChatGPT login is called by its plan');
  assert.deepEqual([said('Acme'),figure('Acme')],['Team · No usage yet',''],'nothing reported, no figure');
  assert.equal(account('Personal').title,'bryce@trydervo.com · Pro · 64% left');
  assert.equal(document.querySelector('.dv-pk-bar'),null,'no full-width bars');
  assert.ok(document.querySelector('.dv-picker').classList.contains('dv-thread-picker'));
  assert.equal(account('Personal').getAttribute('aria-current'),'true');
  assert.equal(document.querySelector('.dv-pk-head .dv-caption').textContent,'Claude Pro');
 }finally{cleanup();}
 // Two unnamed Claude sign-ins would both read "Personal": each shows its sign-in instead.
 const unnamed=PROFILES.map(p=>({...p,name:undefined}));
 const second=mount({profiles:unnamed,usage:[]});
 try{await second.open();assert.deepEqual(accounts().map(a=>a.querySelector('b').textContent),['bryce@trydervo.com','bryce@acme.com','ChatGPT']);}finally{cleanup();}
});

test('the chip names the account when two accounts share a provider, and not otherwise',async()=>{
 const {view}=mount();
 try{
  const chip=view.getByTestId('model-picker');
  assert.equal(chip.querySelector('.dv-pk-acct-name').textContent,'Personal');
  await waitFor(()=>assert.equal(chip.querySelector('.dv-pk-model-name').textContent,'Opus 5 · High'));
  assert.equal(chip.getAttribute('aria-label'),'Account and model: Personal, Claude Opus 5 · High');
 }finally{cleanup();}
 const one=mount({profiles:PROFILES.filter(p=>p.id!=='cd')});
 try{assert.equal(one.view.getByTestId('model-picker').querySelector('.dv-pk-acct-name'),null);}finally{cleanup();}
});

test('pointing at another account says what moving does and saves nothing; a pick there moves this thread only',async()=>{
 const {writes,open,changed}=mount();
 try{
  await open();
  await click(account('Acme'));
  assert.equal(document.querySelector('.dv-pk-head .dv-title').textContent,'Acme');
  assert.match(document.querySelector('.dv-pk-note').textContent,/^Picking a model here moves this thread to Acme\. Its next message starts a new session there/);
  assert.match(document.querySelector('.dv-pk-foot:not(.dv-pk-effort-foot)').textContent,/This thread is on Personal\. New threads use Personal\./);
  assert.equal(document.querySelector('[aria-label="Thinking"]'),null,'thinking belongs to the thread’s own model');
  assert.equal(rows().some(r=>r.getAttribute('aria-selected')==='true'),false,'nothing is ticked on another account');
  assert.deepEqual(writes(),[],'pointing at an account saves nothing');
  await click(row('Sonnet 5.5'));
  await waitFor(()=>assert.equal(changed(),1));
  assert.deepEqual(writes()[0],['conversation_profile_set',{projectId:'p',channelId:'c',expectedProfileId:'cs',profileId:'cd',expectedModel:'claude-opus-5',model:'claude-sonnet-5-5'}]);
  assert.ok(!writes().some(([op])=>op.startsWith('project_')),'the project’s default never changes from the picker');
  assert.equal(document.querySelector('.dv-picker'),null,'the picker closes');
 }finally{cleanup();}
});

test('a pick on the thread’s own account changes only the model, so the session carries on',async()=>{
 const {writes,open}=mount();
 try{
  await open();
  assert.match(document.querySelector('.dv-pk-foot:not(.dv-pk-effort-foot)').textContent,/This thread only\. New threads use Personal · Opus 5\./);
  await click(row('Opus 5'));
  assert.deepEqual(writes(),[],'the model already in use sends nothing');
  await click(row('Sonnet 5.5'));
  await waitFor(()=>assert.equal(writes().length,1));
  assert.deepEqual(writes()[0],['conversation_model_set',{projectId:'p',channelId:'c',expectedModel:'claude-opus-5',model:'claude-sonnet-5-5'}]);
 }finally{cleanup();}
});

test('Fable on Pro says Paid credits once and asks once per account; Cancel sends nothing; a yes is remembered for that account',async()=>{
 const {writes,open}=mount();
 try{
  await open();
  const fable=row('Fable 5.1');
  assert.equal(fable.querySelector('.dv-tag').textContent,'Paid credits');
  assert.doesNotMatch(fable.querySelector('small').textContent,/credit|money/i,'the tag says it; the line doesn’t repeat it');
  assert.equal(row('Opus 5').querySelector('.dv-tag'),null);
  await click(fable);
  const ask=document.querySelector('[aria-label="Paid credits"]');
  assert.ok(ask,'it asks before anything is saved');
  assert.match(ask.textContent,/Use Fable 5\.1 in this thread\?Each message here will cost money on top of your Pro plan\. Dervo asks once for Personal\./);
  assert.deepEqual(writes(),[]);
  await click([...ask.querySelectorAll('button')].find(b=>b.textContent==='Cancel'));
  assert.equal(document.querySelector('[aria-label="Paid credits"]'),null);
  assert.deepEqual(writes(),[]);
  await click(row('Fable 5.1'));
  await click([...document.querySelectorAll('[aria-label="Paid credits"] button')].find(b=>b.textContent==='Use Fable 5.1'));
  await waitFor(()=>assert.equal(writes().length,2));
  assert.deepEqual(writes().map(([op,args])=>[op,args.profileId??args.model]),[['profile_fable_ack','cs'],['conversation_model_set','claude-fable-5-1']]);
 }finally{cleanup();}
 // Negative: once confirmed for Personal it switches at once; on Max there's no tag and no question.
 for(const change of [{fable_confirmed:true},{plan:'max'}]){
  const {writes:w,open:o}=mount({profiles:PROFILES.map(p=>p.id==='cs'?{...p,...change}:p)});
  try{
   await o();
   if(change.plan==='max'){assert.equal(row('Fable 5.1').querySelector('.dv-tag'),null);assert.match(row('Fable 5.1').querySelector('small').textContent,/Uses weekly limits faster/);}
   else assert.equal(row('Fable 5.1').querySelector('.dv-tag').textContent,'Paid credits','the tag stays after the yes');
   await click(row('Fable 5.1'));
   assert.equal(document.querySelector('[aria-label="Paid credits"]'),null);
   await waitFor(()=>assert.deepEqual(w().map(([op])=>op),['conversation_model_set']));
  }finally{cleanup();}
 }
 assert.equal(models.asksBeforeFable({provider:'claude',plan:'team',fable_confirmed:false},'claude-fable-5-1'),true,'Team is asked too: auth status can’t tell a standard seat from a premium one');
 assert.equal(models.asksBeforeFable({provider:'claude',plan:'pro',fable_confirmed:false},'claude-opus-5'),false);
});

test('Team Fable keeps seat uncertainty explicit, including after acknowledgement and for an older model',async()=>{
 for(const confirmed of [false,true]){
  const {open,writes}=mount({profiles:PROFILES.map(p=>p.id==='cd'?{...p,fable_confirmed:confirmed}:p),
   binding:{profile_id:'cd',provider:'claude',model:'claude-fable-5'},project:{profile_id:'cd',model:'claude-opus-5'}});
  try{
   await open();
   for(const label of ['Fable 5.1','Fable 5'])assert.equal(row(label).querySelector('.dv-tag').textContent,'Credits may apply');
   assert.ok(![...document.querySelectorAll('.dv-tag')].some(tag=>tag.textContent==='Paid credits'));
   await click(row('Fable 5.1'));
   if(!confirmed){
    const ask=document.querySelector('.dv-pk-confirm');
    assert.match(ask.textContent,/Premium seats include Fable for up to 50% of your weekly usage limits/);
    assert.match(ask.textContent,/Standard seats use paid credits from the first message/);
    assert.match(ask.textContent,/Dervo can’t detect your seat type/);
    assert.match(ask.textContent,/Continuing beyond the included Fable limit uses credits/);
    assert.deepEqual(writes(),[]);
    await click([...ask.querySelectorAll('button')].find(b=>b.textContent==='Cancel'));
    assert.deepEqual(writes(),[]);
   }else await waitFor(()=>assert.deepEqual(writes().map(([op])=>op),['conversation_model_set']));
  }finally{cleanup();}
 }
});

test('a model the account no longer offers is muted with its reason, marks the chip, and can’t be picked',async()=>{
 const binding={profile_id:'cx',provider:'codex',model:'gpt-5.3-codex'};
 const {view,writes,open}=mount({binding,effort:'medium'});
 try{
  await waitFor(()=>assert.ok(view.getByTestId('model-picker').querySelector('.dv-st')));
  const chip=view.getByTestId('model-picker');
  assert.match(chip.getAttribute('aria-label'),/This model isn’t offered any more/);
  assert.equal(chip.querySelector('.dv-pk-model-name').textContent,'GPT-5.3 Codex','no thinking level on a model that can’t run');
  await open();
  const gone=rows()[0];
  assert.equal(gone.getAttribute('aria-disabled'),'true');
  assert.equal(gone.getAttribute('aria-selected'),'false','not ticked');
  assert.equal(gone.querySelector('small').textContent,'ChatGPT Plus doesn’t offer it any more, so messages here can’t start. Pick another model.');
  await click(gone);
  assert.deepEqual(writes(),[]);
  // One honest line each: Dervo's line where it has one, otherwise Codex's own description (A8).
  assert.equal(row('GPT-5.6 Sol').querySelector('small').textContent,'Previous generation of Sol. Available while GPT-6 rolls out.');
  assert.equal(row('GPT-5.6 Sol').title,'Deep reasoning for complex work.');
  assert.equal(row('GPT New One').querySelector('small').textContent,'A model Dervo has no line for yet.');
  await click(row('GPT-5.6 Sol'));
  await waitFor(()=>assert.deepEqual(writes()[0],['conversation_model_set',{projectId:'p',channelId:'c',expectedModel:'gpt-5.3-codex',model:'gpt-5.6-sol'}]));
 }finally{cleanup();}
 // Negative: a model the catalog lists carries no mark.
 const listed=mount({binding:{...binding,model:'gpt-5.6-sol'}});
 try{await new Promise(r=>setTimeout(r,30));assert.equal(listed.view.getByTestId('model-picker').querySelector('.dv-st'),null);}finally{cleanup();}
});

test('a busy thread or an agent’s thread keeps its account and model, and says why',async()=>{
 const {writes,open}=mount({busy:'Wait for this thread’s task to finish before switching.'});
 try{
  await open();
  assert.equal(document.querySelector('.dv-pk-note').textContent,'Wait for this thread’s task to finish before switching.');
  assert.equal(row('Sonnet 5.5').getAttribute('aria-disabled'),'true');
  assert.equal(account('Acme').getAttribute('aria-disabled'),'true');
  await click(row('Sonnet 5.5'));
  assert.deepEqual(writes(),[]);
  assert.ok(document.querySelector('[aria-label="Thinking"]'),'thinking still works');
 }finally{cleanup();}
 const agent=mount({agent:'review-a05020d2dcdf76484cb1448f'});
 try{await agent.open();assert.equal(document.querySelector('.dv-pk-note').textContent,'This thread belongs to Reviewer, which keeps its account and model.');}finally{cleanup();}
 // Negative: a thread that isn't busy can switch.
 const free=mount();
 try{await free.open();assert.equal(row('Sonnet 5.5').getAttribute('aria-disabled'),null);}finally{cleanup();}
});

test('thinking: one line per level, a lone heavy level in the row, none for Haiku, and saves per model',async()=>{
 const {writes,open}=mount();
 try{
  await open();
  const levels=()=>[...document.querySelectorAll('[aria-label="Thinking"] [role="radio"]')].map(b=>b.textContent);
  // Harry, 27 Sep: one heavy level (Max) shows in the row. A More button in its place took the same room.
  assert.deepEqual(levels(),['Default','Low','Medium','High','Extra high','Max'],'a lone Max shows without More');
  assert.equal(document.querySelector('.dv-pk-more'),null,'no More button for one level');
  assert.equal(document.querySelector('.dv-pk-sec .dv-pk-line').textContent,'High: the best balance of quality and speed.');
  await click([...document.querySelectorAll('[aria-label="Thinking"] [role="radio"]')].find(b=>b.textContent==='Low'));
  await waitFor(()=>assert.deepEqual(writes()[0],['coding_effort_set',{projectId:'p',channelId:'c',effort:'low',level:'thread'}]));
  assert.ok(document.querySelector('.dv-picker'),'the picker stays open for thinking');
 }finally{cleanup();}
 const haiku=mount({binding:{profile_id:'cs',provider:'claude',model:'claude-haiku-4-5'},effort:null});
 try{await haiku.open();assert.equal(document.querySelector('[aria-label="Thinking"]'),null);}finally{cleanup();}
 assert.deepEqual(effortsFor('codex','gpt-5.6-sol',CATALOG.models),['low','high','ultra']);
 assert.equal(models.effortLabel('ultra'),'Ultra','Codex’s ultra no longer reads in lower case');
 assert.deepEqual(effortsFor('claude','claude-opus-5'),['low','medium','high','xhigh','max']);
 assert.ok(CLAUDE_CHOICES.some(([id,label])=>id==='claude-haiku-4-5'&&label==='Haiku 4.5'),'Haiku 4.5 is offered');
 assert.ok(!CLAUDE_CHOICES.some(([id])=>id==='best'),'the best alias is never offered');
 assert.equal(modelLabel('codex','gpt-5.5-codex'),'GPT-5.5 Codex');
});

test('a refusal shows under the list and the thread stays where it was',async()=>{
 const {open,changed}=mount({},{conversation_model_set:'Finish or stop this conversation’s coding task before changing its model.'});
 try{
  await open();
  await click(row('Sonnet 5.5'));
  await waitFor(()=>assert.match(document.querySelector('.dv-pk-error').textContent,/Finish or stop this conversation’s coding task/));
  assert.equal(changed(),0);
  assert.ok(document.querySelector('.dv-picker'),'the picker stays open with the reason');
 }finally{cleanup();}
});

test('Opus 5.5 and Sonnet 5.5 lead their choices, Opus 5 and Sonnet 5 move under Other models, and all keep their names',()=>{
  assert.deepEqual(models.CLAUDE_MODELS.map(m=>m.id),['claude-fable-5-1','claude-opus-5-5','claude-sonnet-5-5','claude-haiku-4-5']);
  assert.deepEqual(models.CLAUDE_OLDER.map(m=>m.id),['claude-opus-5','claude-sonnet-5','claude-fable-5']);
  assert.equal(modelLabel('claude','claude-sonnet-5-5'),'Sonnet 5.5');
  assert.equal(modelLabel('claude','claude-sonnet-5'),'Sonnet 5');
  assert.match(models.claudeLine('claude-sonnet-5-5'),/Needs Claude Code 2\.1\.284/);
  assert.equal(modelLabel('claude','claude-opus-5-5'),'Opus 5.5');
  assert.equal(modelLabel('claude','claude-opus-5'),'Opus 5');
  assert.ok(CLAUDE_CHOICES.some(([id])=>id==='claude-opus-5'),'a thread on Opus 5 still has a name');
  assert.equal(models.claudeLine('claude-opus-5-5','max'),'Complex coding and long agent work. Moderate speed. Needs Claude Code 2.1.280 or later.');
  assert.equal(models.claudeLine('claude-opus-5','pro'),'The earlier Opus. Moderate speed. Has its own Opus limit.');
});

test('Opus 5.5 is the default only on a Claude Code that can run it',()=>{
  const {claudeDefaultModel,claudeVersionAtLeast}=models;
  assert.equal(claudeDefaultModel('2.1.280'),'claude-opus-5-5');
  assert.equal(claudeDefaultModel('2.1.281 (Claude Code)'),'claude-opus-5-5');
  assert.equal(claudeDefaultModel('2.2.0'),'claude-opus-5-5');
  assert.equal(claudeDefaultModel('2.1.279'),'claude-opus-5');
  assert.equal(claudeDefaultModel('2.1.278'),'claude-opus-5');
  assert.equal(claudeDefaultModel(null),'claude-opus-5','an unknown version gets the Opus every supported version runs');
  assert.equal(claudeDefaultModel('not a version'),'claude-opus-5');
  assert.equal(claudeVersionAtLeast('2.1.300','2.1.280'),true);
  assert.equal(claudeVersionAtLeast('2.0.999','2.1.280'),false);
  assert.equal(claudeVersionAtLeast('3.0.0','2.1.280'),true);
});

// 16.4 item 12 (Harrison, report #18, 25 Sep): "Default is Medium, the level Anthropic set for Opus 5.5" was a guess from the
// docs. Default sends no --effort and Claude Code doesn't say which level it then uses, so Dervo names none.
test('Default thinking on Claude names no level Dervo can’t see',()=>{
  for(const model of ['claude-opus-5-5','claude-opus-5','claude-sonnet-5','claude-haiku-4-5'])assert.equal(models.defaultEffort('claude',model),undefined,model);
  assert.deepEqual(effortsFor('claude','claude-opus-5-5'),['low','medium','high','xhigh','max']);
  assert.equal(models.thinkingLine(null,models.defaultEffort('claude','claude-opus-5-5'),'claude-opus-5-5','claude'),
    'Claude Code’s own default for Opus 5.5. Dervo can’t see which level that is, and a level set in Claude Code itself doesn’t apply here.');
  assert.equal(models.thinkingLine('medium',undefined,'claude-opus-5-5','claude'),'Medium: saves limits on routine work, with some quality lost.','a chosen level keeps its own line');
});

test('Default thinking on Claude never reads as your own default, and a level you picked is named as yours',()=>{
  const line=models.thinkingLine(null,models.defaultEffort('claude','claude-opus-5-5'),'claude-opus-5-5','claude');
  assert.doesNotMatch(line,/Medium|High/,'no level is guessed');
  assert.match(line,/Claude Code itself/,'says a level set in Claude Code doesn’t reach Dervo');
  assert.equal(models.thinkingLine('xhigh',undefined,'claude-opus-5-5','claude'),'Extra high: deeper reasoning for long coding tasks. Uses more limits.','a level picked in Dervo is what runs');
  // Without the provider, a Claude model ID still gets the Claude wording.
  assert.equal(models.thinkingLine(null,undefined,'claude-opus-5-5'),line);
  // Codex's default comes from its own catalog, so its line is unchanged.
  assert.equal(models.thinkingLine(null,'high','gpt-5.5','codex'),'Default is High: the best balance of quality and speed.');
});

const footer=()=>document.querySelector('[data-testid="thinking-source"]');
const footButtons=()=>[...(footer()?.querySelectorAll('button')??[])].map(b=>b.textContent);
test('the footer names where Thinking comes from, and the way back shows only for a thread’s own level',async()=>{
  for(const [efforts,source,buttons] of [
    [{thread:'high',project:'low',personal:null,effective:'high',source:'thread'},'Thinking: This thread · High',['Back to default','Use for new threads in this project']],
    [{thread:'high',project:null,personal:'low',effective:'high',source:'thread'},'Thinking: This thread · High',['Back to default','Use for all new threads']],
    [{thread:null,project:'xhigh',personal:'low',effective:'xhigh',source:'project'},'Thinking: This project’s default · Extra high',[]],
    [{thread:null,project:null,personal:'low',effective:'low',source:'personal'},'Thinking: Your default · Low',[]],
    [{thread:null,project:null,personal:null,effective:null,source:null},'Thinking: Claude Code’s own default',[]],
  ]){
    const {open}=mount({},undefined,efforts);
    try{
      await open();
      await waitFor(()=>assert.equal(footer()?.querySelector('span').textContent,source));
      assert.deepEqual(footButtons(),buttons);
      const checked=document.querySelector('[aria-label="Thinking"] [aria-checked="true"]').textContent;
      assert.equal(checked,efforts.thread?models.effortLabel(efforts.thread):'Default','the levels show the thread’s own choice');
      if(!efforts.thread)assert.equal(document.querySelector('.dv-pk-sec .dv-pk-line').textContent,
        efforts.project?'Default here is Extra high, this project’s default.':efforts.personal?'Default here is Low, your default.'
        :'Claude Code’s own default for Opus 5. Dervo can’t see which level that is, and a level set in Claude Code itself doesn’t apply here.');
    }finally{cleanup();}
  }
});

test('Back to default and Use for new threads write the right level, with no dialog',async()=>{
  const efforts={thread:'xhigh',project:null,personal:'low',effective:'xhigh',source:'thread'};
  const {open,writes}=mount({},undefined,()=>efforts);
  try{
    await open();
    await waitFor(()=>assert.deepEqual(footButtons(),['Back to default','Use for all new threads']));
    await click([...footer().querySelectorAll('button')].find(b=>b.textContent==='Use for all new threads'));
    await waitFor(()=>assert.deepEqual(writes()[0],['coding_effort_set',{projectId:'p',channelId:'c',effort:'xhigh',level:'promote'}]));
    assert.equal(document.querySelector('[role="alertdialog"]'),null);
    assert.ok(document.querySelector('.dv-picker'),'the picker stays open');
    Object.assign(efforts,{thread:'xhigh'});
    await click([...footer().querySelectorAll('button')].find(b=>b.textContent==='Back to default'));
    await waitFor(()=>assert.deepEqual(writes()[1],['coding_effort_set',{projectId:'p',channelId:'c',effort:null,level:'thread'}]));
  }finally{cleanup();}
});

test('the chip names a thinking level only when the thread’s differs from its default',async()=>{
  for(const [efforts,name] of [
    [{thread:'high',project:'low',personal:null,effective:'high',source:'thread'},'Opus 5 · High'],
    [{thread:'low',project:'low',personal:null,effective:'low',source:'thread'},'Opus 5'],
    [{thread:null,project:'low',personal:null,effective:'low',source:'project'},'Opus 5'],
    [{thread:null,project:null,personal:'xhigh',effective:'xhigh',source:'personal'},'Opus 5'],
  ]){
    const {view,calls}=mount({},undefined,efforts);
    try{
      await waitFor(()=>assert.ok(calls.some(([op])=>op==='coding_permission')));
      await new Promise(r=>setTimeout(r,20));
      assert.equal(view.getByTestId('model-picker').querySelector('.dv-pk-model-name').textContent,name);
    }finally{cleanup();}
  }
  assert.equal(models.chipEffort(undefined),null);
  assert.equal(models.promoteLabel({project:'low'}),'Use for new threads in this project');
  assert.equal(models.promoteLabel({project:null}),'Use for all new threads');
});

test('GPT-6.1 Sol, GPT-6 Sol and Luna get Dervo’s own lines, and GPT-5.6 reads as the previous generation',()=>{
  assert.equal(models.codexLine({model:'gpt-6.1-sol'}),'Complex coding and everyday work, close to Astra. About as many messages as GPT-6 Sol.');
  assert.equal(models.codexLine({model:'gpt-6-sol'}),'Complex coding and agent work. OpenAI’s replacement for GPT-5.5.');
  assert.equal(models.codexLine({model:'gpt-6-luna'}),'Focused, high-volume tasks. About twenty times GPT-6 Sol’s messages.');
  assert.equal(models.codexLine({model:'gpt-6-astra'}),'Hardest end-to-end work. Slower. About a third of GPT-6 Sol’s messages.');
  assert.match(models.codexLine({model:'gpt-5.6-terra'}),/Previous generation/);
  assert.equal(models.codexLine({model:'gpt-7-nova',description:'From the catalog'}),'From the catalog','an unknown model still reads the catalog’s own words');
  assert.equal(modelLabel('codex','gpt-6-sol'),'GPT-6 Sol');
  assert.equal(modelLabel('codex','gpt-6.1-sol'),'GPT-6.1 Sol');
  assert.equal(modelLabel('claude','claude-haiku-4-5'),'Haiku 4.5');
});

// GPT-5.5 leaves ChatGPT and Codex on 14 Oct 2026 (learn.chatgpt.com/docs/models), with GPT-6 Sol named in its place.
const before=new Date(2026,9,13,12).getTime(),on=new Date(2026,9,14,0,1).getTime();
test('GPT-5.5 says when it leaves Codex, before and after the day, and an upgrade pointer alone isn’t a retirement',()=>{
  // The day is in the window's own date format: "14 October" in Australia, "October 14" in the US.
  assert.match(models.codexLine({model:'gpt-5.5'},before),/^Previous generation\. Leaves Codex on (14 October|October 14)\.$/);
  assert.match(models.codexLine({model:'gpt-5.5'},on),/^Previous generation\. Left Codex on (14 October|October 14)\.$/);
  assert.deepEqual(models.retirementFor('gpt-5.5'),{model:'gpt-5.5',successor:'gpt-6-sol',at:new Date(2026,9,14).getTime()});
  // The catalog's own pointer and date win over Dervo's.
  const said=models.retirementFor('gpt-5.5',[{model:'gpt-5.5',upgradeInfo:{model:'gpt-6.1-sol',retirementAt:Date.UTC(2026,9,20)/1000}}]);
  assert.equal(said.successor,'gpt-6.1-sol');assert.equal(said.at,Date.UTC(2026,9,20));
  // GPT-5.6 points at GPT-6 Sol with no date and stays offered: no notice.
  assert.equal(models.retirementFor('gpt-5.6-sol',[{model:'gpt-5.6-sol',upgradeInfo:{model:'gpt-6-sol'}}]),null);
  assert.equal(models.retirementFor('gpt-6-sol'),null);
});

test('a thread on GPT-5.5 is marked on the chip, says so in the picker, and moves to GPT-6 Sol only on a click',async()=>{
  const binding={profile_id:'cx',provider:'codex',model:'gpt-5.5'};
  const catalog={state:'catalog',detail:'',models:[{model:'gpt-6-sol',displayName:'GPT-6-Sol',supportedReasoningEfforts:[{reasoningEffort:'low'}]},
    {model:'gpt-5.5',displayName:'GPT-5.5',upgradeInfo:{model:'gpt-6-sol'},supportedReasoningEfforts:[{reasoningEffort:'low'}]}]};
  const original=CATALOG.models;CATALOG.models=catalog.models;
  const {view,writes,open}=mount({binding,effort:'low'});
  try{
    const chip=view.getByTestId('model-picker');
    await waitFor(()=>assert.ok(chip.querySelector('.dv-st')),'a quiet mark on the chip');
    assert.match(chip.getAttribute('aria-label'),/(Leaves|Left) Codex on (14 October|October 14)/);
    await open();
    const note=document.querySelector('.dv-pk-retire');
    assert.match(note.textContent,/GPT-5\.5 (leaves|left) Codex on (14 October|October 14)\./);
    assert.deepEqual(writes(),[],'nothing moves by itself');
    await click([...note.querySelectorAll('button')].find(b=>b.textContent==='Move this thread to GPT-6-Sol'));
    await waitFor(()=>assert.deepEqual(writes()[0],['conversation_model_set',{projectId:'p',channelId:'c',expectedModel:'gpt-5.5',model:'gpt-6-sol'}]));
  }finally{cleanup();CATALOG.models=original;}
  // Without GPT-6 Sol in the account's catalog there's no button, only the words.
  CATALOG.models=catalog.models.filter(m=>m.model!=='gpt-6-sol');
  const bare=mount({binding,effort:'low'});
  try{await waitFor(()=>assert.ok(bare.view.getByTestId('model-picker').querySelector('.dv-st')));await bare.open();await waitFor(()=>assert.ok(document.querySelector('.dv-pk-retire')));
    assert.equal(document.querySelector('.dv-pk-retire button'),null);
    assert.match(document.querySelector('.dv-pk-retire').textContent,/doesn’t offer GPT-6 Sol/);}finally{cleanup();CATALOG.models=original;}
});

test('an older Claude Code sees Opus 5 where Opus 5.5 would be, and Other models never repeats it',()=>{
  const ids=list=>list.map(m=>m.id);
  assert.deepEqual(ids(models.claudeModelsFor('2.1.278')),['claude-fable-5-1','claude-opus-5','claude-sonnet-5','claude-haiku-4-5']);
  assert.deepEqual(ids(models.claudeOlderFor('2.1.278')),['claude-fable-5']);
  assert.deepEqual(ids(models.claudeModelsFor('2.1.280')),['claude-fable-5-1','claude-opus-5-5','claude-sonnet-5','claude-haiku-4-5'],'Sonnet 5.5 needs 2.1.284');
  assert.deepEqual(ids(models.claudeOlderFor('2.1.280')),['claude-opus-5','claude-fable-5']);
  assert.deepEqual(ids(models.claudeModelsFor('2.1.284')),['claude-fable-5-1','claude-opus-5-5','claude-sonnet-5-5','claude-haiku-4-5']);
  assert.deepEqual(ids(models.claudeOlderFor('2.1.284')),['claude-opus-5','claude-sonnet-5','claude-fable-5']);
  assert.deepEqual(ids(models.claudeModelsFor(null)),ids(models.CLAUDE_MODELS),'an unknown version keeps Opus 5.5 and Sonnet 5.5; only a version the backend read is refused');
});

test('the thread picker offers the Opus this Mac’s Claude Code can run',async()=>{
  for(const [version,expected] of [['2.1.278',['Fable 5.1','Opus 5','Sonnet 5','Haiku 4.5']],['2.1.280',['Fable 5.1','Opus 5.5','Sonnet 5','Haiku 4.5','Opus 5']],[undefined,['Fable 5.1','Opus 5.5','Sonnet 5.5','Haiku 4.5','Opus 5']]]){
    const {open}=mount();
    const invoke=window.__TAURI_INTERNALS__.invoke;
    window.__TAURI_INTERNALS__.invoke=async(command,payload)=>payload?.operation==='claude_version'?{state:'ok',version:version??null}:invoke(command,payload);
    try{
      await open();
      await waitFor(()=>assert.deepEqual(rows().map(r=>r.querySelector('b').firstChild.textContent),expected));
    }finally{cleanup();}
  }
});

test('a Work project lists only work accounts, says why, and offers to add a missing one',async()=>{
 // b16-work picker-work: the project's side decides which accounts show. Personal ones never run a Work project.
 const ACME={id:'cw',provider:'claude',identity:'you@acme.test',category:'work',method:'subscription',plan:'team',fable_confirmed:false,name:'Acme'};
 const settings=[];
 const {open}=mount({profiles:[...PROFILES,ACME],binding:{profile_id:'cw',provider:'claude',model:'claude-opus-5'},project:{profile_id:'cw',model:'claude-opus-5',context:'work'},
  onSettings:page=>settings.push(page)});
 try{
  assert.match(document.querySelector('.dv-pk-acct-name').textContent,/Acme/);
  assert.ok(document.querySelector('.dv-pk-acct-name .dv-work-name svg'),'the chip carries the briefcase');
  await open();
  assert.deepEqual([...document.querySelectorAll('.dv-pk-group')].map(g=>[g.getAttribute('aria-label'),[...g.querySelectorAll('.dv-pk-acct b')].map(b=>b.textContent)]),
   [['Claude · Work',['Acme']],['Codex · Work',[]]]);
  assert.equal(document.querySelector('.dv-pk-side-note').textContent,'This is a Work project, so only work accounts show. Your personal ones never run it.');
  assert.match(document.querySelector('.dv-pk-add').textContent,/^No work Codex account/);
  await click(document.querySelector('.dv-pk-add button'));
  assert.deepEqual(settings,['accounts']);
 }finally{cleanup();}
 // A Personal project, as before: no work account and no note.
 const personal=mount({profiles:[...PROFILES,ACME]});
 try{
  await personal.open();
  assert.equal(accounts().some(a=>a.querySelector('b').textContent==='Acme'),false);
  assert.equal(document.querySelector('.dv-pk-side-note'),null);
 }finally{cleanup();}
});

test('one model line at a time: the chosen one, or the one you point at (beta.18 compact picker)',async()=>{
 const {open}=mount();
 try{
  await open();
  const line=()=>document.querySelector('.dv-pk-desc')?.textContent;
  await waitFor(()=>assert.equal(line(),models.claudeLine('claude-opus-5',undefined)||row('Opus 5')?.querySelector('small').textContent));
  assert.equal(document.querySelectorAll('.dv-pk-desc').length,1);
  fireEvent.pointerEnter(row('Sonnet 5.5'));
  assert.equal(line(),row('Sonnet 5.5').querySelector('small').textContent,'pointing shows that model’s line');
  fireEvent.pointerLeave(row('Sonnet 5.5'));
  assert.equal(line(),row('Opus 5').querySelector('small').textContent,'and back to the chosen one');
  // Each row still carries its own line for screen readers.
  assert.ok(rows().every(r=>r.querySelector('small')?.textContent));
 }finally{cleanup();}
});

test('a thread whose account was removed says Pick an account, and moves only when a model is picked on one of yours',async()=>{
 const {view,open,writes}=mount({binding:{profile_id:'gone',provider:'claude',model:'claude-opus-5'}});
 try{
  const chip=view.getByTestId('model-picker');
  assert.equal(chip.querySelector('.dv-pk-model-name').textContent,'Pick an account');
  assert.ok(chip.querySelector('.dv-st'),'marked on the chip');
  assert.match(chip.getAttribute('aria-label'),/isn’t connected any more\. Pick an account\./);
  await open();
  assert.match(document.querySelector('.dv-pk-note').textContent,/^This thread’s account isn’t connected any more, so nothing runs here until you pick one\. Picking a model moves it to Personal/);
  assert.equal(rows().some(r=>r.getAttribute('aria-selected')==='true'),false,'nothing is ticked');
  assert.match(document.querySelector('.dv-pk-foot').textContent,/This thread needs an account\./);
  assert.deepEqual(writes(),[],'nothing moves by itself');
  await click(row('Sonnet 5.5'));
  await waitFor(()=>assert.deepEqual(writes()[0],['conversation_profile_set',{projectId:'p',channelId:'c',expectedProfileId:'gone',profileId:'cs',expectedModel:'claude-opus-5',model:'claude-sonnet-5-5'}]));
 }finally{cleanup();}
});
