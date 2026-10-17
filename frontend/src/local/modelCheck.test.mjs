import assert from 'node:assert/strict';
import {test} from 'node:test';
const {checkClaude,checkCodex,checkDervo,claudeDocs,codexListed,report}=await import('./modelCheck.ts');

// #46 (Bryce, 3 Oct 2026): "check in each beta release if new models exist". frontend/scripts/check-models.mjs runs
// these comparisons on the live pages; here they run on copies of what those pages said on 5 Oct 2026.

// Codex 0.160.0's codex-rs/models-manager/models.json, the fields the check reads (snake_case, as the file has them).
const CODEX_0160=[
 {slug:'gpt-6-astra',visibility:'list',minimal_client_version:'0.153.0',upgrade:null},
 {slug:'gpt-6.1-sol',visibility:'list',minimal_client_version:'0.153.0',upgrade:null},
 {slug:'gpt-6-sol',visibility:'list',minimal_client_version:'0.155.0',upgrade:null},
 {slug:'gpt-6-luna',visibility:'list',minimal_client_version:'0.155.0',upgrade:null},
 {slug:'gpt-5.6-sol',visibility:'list',minimal_client_version:'0.144.0',upgrade:{model:'gpt-6-sol',retirement_at:null}},
 {slug:'gpt-5.6-terra',visibility:'list',minimal_client_version:'0.144.0',upgrade:{model:'gpt-6-sol',retirement_at:null}},
 {slug:'gpt-5.6-luna',visibility:'list',minimal_client_version:'0.144.0',upgrade:{model:'gpt-6-luna',retirement_at:null}},
 {slug:'gpt-daybreak-blue-latest',visibility:'hide',minimal_client_version:'0.142.2',upgrade:null},
 {slug:'gpt-5.5',visibility:'list',minimal_client_version:'0.124.0',upgrade:{model:'gpt-6-sol',retirement_at:null}},
 {slug:'codex-auto-review',visibility:'hide',minimal_client_version:'0.98.0',upgrade:null}];
// The overview's alias row and legacy line, and the model configuration page's version sentences, as published.
const OVERVIEW=['| Claude API alias | `claude-fable-5-1` | `claude-opus-5-5` | `claude-sonnet-5-5` | `claude-haiku-4-5` |','',
 'Legacy models (still available): [Claude Fable 5](https://platform.claude.com/docs/en/models/fable-5/overview), [Claude Opus 5](https://platform.claude.com/docs/en/models/opus-5/overview), [Claude Opus 4.8](https://platform.claude.com/docs/en/models/opus-4-8/overview), [Claude Sonnet 5](https://platform.claude.com/docs/en/models/sonnet-5/overview).'].join('\n');
const MODEL_CONFIG='  Sonnet 5.5 requires Claude Code v2.1.284 or later, and Opus 5.5 requires v2.1.280 or later.\n  Fable 5.1 requires Claude Code v2.1.257 or later.';
const BACKEND_FLOORS={'claude-opus-5-5':'2.1.280','claude-sonnet-5-5':'2.1.284'};
const loud=findings=>findings.filter(f=>f.level!=='info');

test('Dervo agrees with itself: every Claude model it offers has a name, a line and the words Home hears',()=>{
 assert.deepEqual(checkDervo(),[]);
});

test('both catalog shapes read the same, hidden models left out',()=>{
 const listed=codexListed({models:CODEX_0160});
 assert.deepEqual(listed.map(m=>m.model),['gpt-6-astra','gpt-6.1-sol','gpt-6-sol','gpt-6-luna','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna','gpt-5.5']);
 assert.equal(listed.find(m=>m.model==='gpt-5.5').upgradeTo,'gpt-6-sol');
 // model/list's own shape, as Dervo's model_status passes it on.
 const live=codexListed({state:'catalog',models:[{model:'gpt-6-sol',hidden:false},{model:'gpt-x',hidden:true},{model:'gpt-5.5',hidden:false,upgradeInfo:{model:'gpt-6-sol',retirementAt:Date.UTC(2026,9,14)/1000}}]});
 assert.deepEqual(live,[{model:'gpt-6-sol',minClient:undefined,upgradeTo:undefined,retiresOn:undefined},{model:'gpt-5.5',minClient:undefined,upgradeTo:'gpt-6-sol',retiresOn:'2026-10-14'}]);
});

test('today’s Codex catalog needs nothing from Dervo',()=>{
 assert.deepEqual(loud(checkCodex(codexListed(CODEX_0160),{pinned:'0.156.1',homeModel:'gpt-6-sol',today:'2026-10-05'})),[]);
});

test('a new Codex model with no line is flagged, and one that needs a newer Codex says which',()=>{
 const found=checkCodex(codexListed([...CODEX_0160,{slug:'gpt-7-nova',visibility:'list',minimal_client_version:'0.150.0'},{slug:'gpt-7-sun',visibility:'list',minimal_client_version:'0.161.0'}]),{pinned:'0.156.1',homeModel:'gpt-6-sol',today:'2026-10-05'});
 assert.match(found.find(f=>f.model==='gpt-7-nova'&&f.level==='warn').says,/no Dervo line/);
 assert.ok(found.some(f=>f.model==='gpt-7-nova'&&f.level==='info'&&/Home can’t hear it/.test(f.says)));
 assert.match(found.find(f=>f.model==='gpt-7-sun').says,/needs Codex 0\.161\.0; Dervo bundles 0\.156\.1/);
 assert.equal(found.filter(f=>f.model==='gpt-7-sun').length,1,'nothing else is asked of a model this Codex can’t get');
});

test('losing GPT-6 Sol is an error three ways: the starter, Home’s model and GPT-5.5’s replacement',()=>{
 const found=checkCodex(codexListed(CODEX_0160.filter(m=>m.slug!=='gpt-6-sol')),{homeModel:'gpt-6-sol',today:'2026-10-05'});
 assert.deepEqual(found.filter(f=>f.level==='error').map(f=>f.says.split(' ')[0]),['named','models.ts','backend/home_codex.py']);
});

test('a retirement Codex announces that Dervo doesn’t know is flagged, and a past one is only noted',()=>{
 const announced=checkCodex(codexListed([...CODEX_0160.filter(m=>m.slug!=='gpt-5.6-terra'),{slug:'gpt-5.6-terra',visibility:'list',upgrade:{model:'gpt-6-sol',retirement_at:'2026-11-30T19:00:00Z'}}]),{homeModel:'gpt-6-sol',today:'2026-10-05'});
 assert.match(announced.find(f=>f.model==='gpt-5.6-terra').says,/retires on 2026-11-30 for gpt-6-sol\. Add it to models\.ts CODEX_RETIREMENTS/);
 // After 14 Oct the catalog drops GPT-5.5. Its line and retirement stay, for threads still on it.
 const after=checkCodex(codexListed(CODEX_0160.filter(m=>m.slug!=='gpt-5.5')),{homeModel:'gpt-6-sol',today:'2026-10-15'});
 assert.deepEqual(loud(after),[]);
 assert.match(after.find(f=>f.model==='gpt-5.5').says,/retired on 2026-10-14/);
});

test('Anthropic’s pages read as current models, legacy models and Claude Code floors, and agree with Dervo today',()=>{
 const docs=claudeDocs(OVERVIEW,MODEL_CONFIG);
 assert.deepEqual(docs.current,['claude-fable-5-1','claude-opus-5-5','claude-sonnet-5-5','claude-haiku-4-5']);
 assert.deepEqual(docs.legacy,['claude-fable-5','claude-opus-5','claude-opus-4-8','claude-sonnet-5']);
 assert.deepEqual(docs.floors,{'claude-sonnet-5-5':'2.1.284','claude-opus-5-5':'2.1.280','claude-fable-5-1':'2.1.257'});
 assert.deepEqual(checkClaude(docs,{backendFloors:BACKEND_FLOORS,floor:'2.1.271'}),[],'Fable 5.1’s 2.1.257 is under Dervo’s own floor');
});

test('a new Claude model, one gone legacy and a changed floor are each flagged',()=>{
 const docs=claudeDocs(OVERVIEW.replace('`claude-sonnet-5-5` |','`claude-sonnet-6` |')+', [Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)',
  MODEL_CONFIG.replace('v2.1.280','v2.1.281'));
 const found=checkClaude(docs,{backendFloors:BACKEND_FLOORS,floor:'2.1.271'});
 assert.match(found.find(f=>f.model==='claude-sonnet-6').says,/a current Claude model Dervo doesn’t offer/);
 assert.equal(found.find(f=>f.model==='claude-sonnet-5-5').level,'warn');
 assert.match(found.find(f=>f.model==='claude-sonnet-5-5').says,/now a legacy model/);
 assert.equal(found.filter(f=>f.model==='claude-opus-5-5'&&f.level==='error').length,2,'models.ts and claude_version.py both');
 assert.match(report(found).split('\n')[0],/^ERROR/);
 // A page Dervo can't read is said, not passed.
 assert.equal(checkClaude(claudeDocs('',''))[0].level,'error');
});
