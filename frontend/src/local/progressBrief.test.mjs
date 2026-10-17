import assert from 'node:assert/strict';
import {test} from 'node:test';
const {progressBrief,progressNow,progressSteps,progressPlan,progressRemembered,progressContinuity,QUIET_SECONDS}=await import('./progressBrief.ts');

const base={id:'t',state:'working',origin:'conversation',files:[],messages:[{role:'user',text:'Fix the upload retry',created_at:1}],tokens:[],runs:[{id:'run',state:'running'}],binding:{provider:'claude',model:'m',cwd:'/p'},check_profile:'python-unittest'};
const tok=(tool,path,extra={})=>({run_id:'run',tool,state:'delivered',success:true,path,...extra});

test('Now is the running tool call and its file, and says so when nothing new has been recorded for a while',()=>{
 assert.deepEqual(progressNow({...base,tokens:[tok('dervo_write_file','src/upload.py',{state:'executing',started_at:100})]},110),{text:'Editing',file:'upload.py',since:100,quiet:false,repeat:undefined});
 assert.equal(progressNow({...base,tokens:[tok('dervo_run_tests',null,{state:'running',started_at:100})]},110).text,'Running the Python check');
 assert.equal(progressNow(base,10).text,'Claude Code is reading your request');
 const between={...base,tokens:[tok('dervo_read_file','a.py',{ended_at:1000})]};
 assert.deepEqual(progressNow(between,1000+QUIET_SECONDS-1),{text:'Claude Code is deciding the next step',since:1000,quiet:false});
 assert.equal(progressNow(between,1000+QUIET_SECONDS).quiet,true,'past the quiet limit it stops implying motion');
 // The third edit to the same file is called out; two aren't.
 const edits=n=>({...base,tokens:[...Array(n-1)].map(()=>tok('dervo_write_file','src/upload.py')).concat([tok('dervo_write_file','src/upload.py',{state:'executing'})])});
 assert.equal(progressNow(edits(3),0).repeat,'3rd edit to this file');
 assert.equal(progressNow(edits(2),0).repeat,undefined);
 assert.equal(progressNow({...base,state:'completed'},0),undefined,'a settled task has no Now');
});

test('why and next are the current run’s report only, and the title falls back to your request',()=>{
 const guide={run_id:'run',goal:'Retry uploads',why:'Uploads fail on a slow network',next:'Run the Python check',step:'s',test_steps:'',limits:'l'};
 const brief=progressBrief({...base,runs:[{id:'run',state:'running',guide}]},0);
 assert.deepEqual([brief.title,brief.titleSource,brief.why,brief.next],['Retry uploads','agent',{text:'Uploads fail on a slow network',source:'agent'},{text:'Run the Python check',source:'agent'}]);
 const old=progressBrief({...base,runs:[{id:'old',state:'failed',guide:{...guide,run_id:'old'}},{id:'run',state:'running',guide:{...guide,run_id:'old'}}]},0);
 assert.deepEqual([old.title,old.titleSource,old.why,old.next],['Fix the upload retry','request',{text:'Fix the upload retry',source:'request'},undefined]);
 const settled=progressBrief({...base,state:'completed',runs:[{id:'run',state:'completed',guide}]},0);
 assert.deepEqual([settled.now,settled.why,settled.next],[undefined,undefined,undefined],'once settled, Progress shows what happened instead');
});

test('the result is at most two honest lines: changed and checked',()=>{
 const completion={run_id:'run',changed_files:['src/upload.py','tests/test_upload.py','README.md'],test_exit_status:0,review_phase:'passed'};
 const reads=['a.py','b.py','c.py','a.py'].map(f=>tok('dervo_read_file',`src/${f}`));
 assert.deepEqual(progressSteps({...base,state:'completed',completion,tokens:reads}).map(s=>`${s.label}: ${s.text}`),
  ['Changed: upload.py, test_upload.py and 1 more','Checked: Python check passed, and independent review passed']);
 assert.deepEqual(progressSteps({...base,state:'answered',tokens:reads,runs:[{id:'run',state:'completed',usage:{total_tokens:15706,cost_usd:.19}}]}),[],'an answer has no result lines: no files read, no tokens');
 assert.deepEqual(progressSteps({...base,state:'completed',completion:{...completion,test_exit_status:1,review_phase:null}}).map(s=>[s.text,s.state]),
  [['upload.py, test_upload.py and 1 more','done'],['Python check failed','problem']],'a failed check is never a pass');
 // A stopped task says what it edited before stopping and that nothing checked it.
 assert.deepEqual(progressSteps({...base,state:'blocked',runs:[{id:'run',state:'failed'}],tokens:[tok('dervo_write_file','src/upload.py')]}).map(s=>s.text),['upload.py, before it stopped','Not checked before it stopped']);
 // Negative: a completion from an earlier run, a failed read and another run's tokens don't count.
 assert.deepEqual(progressSteps({...base,state:'completed',completion:{...completion,run_id:'old'},tokens:[tok('dervo_read_file','x.py',{success:false}),{...tok('dervo_read_file','y.py'),run_id:'old'}]}).map(s=>s.text),[],'nothing it can stand behind, so nothing is said');
 assert.deepEqual(progressSteps({...base,state:'ready_for_review',check_profile:'manual-review',tokens:[tok('dervo_write_file','a.py')]}).map(s=>s.text),['a.py','No automated checks ran'],'waiting for review isn’t described as stopped');
});

test('review, unconfirmed outcomes and unfinished checks are never overstated (17 Sep review)',()=>{
 const completion={run_id:'run',changed_files:['a.py'],test_exit_status:0,review_phase:'passed'};
 // An earlier run's passing review says nothing about this run.
 const later={...base,state:'blocked',runs:[{id:'old',state:'completed'},{id:'run',state:'failed'}],completion:{...completion,run_id:'old'},collaboration:{review:{phase:'passed'}},tokens:[tok('dervo_run_tests',null,{success:false})]};
 assert.deepEqual(progressSteps(later).map(s=>s.text),['No files changed','Python check didn’t finish'],'no borrowed review pass, and an errored check call isn’t a failing test');
 // Disconnected isn't stopped: the outcome isn't known.
 const lost={...base,state:'disconnected',runs:[{id:'run',state:'uncertain'}],tokens:[tok('dervo_write_file','a.py')]};
 assert.deepEqual(progressSteps(lost).map(s=>[s.text,s.state]),[['a.py','done'],['Outcome not confirmed','problem']]);
 assert.equal(progressNow({...base,tokens:[tok('dervo_run_tests',null,{state:'queued'})]},0).text,'Waiting to start: running the Python check');
});

test('the plan is the current run’s reported steps: done, the one it is on, and what comes next',()=>{
 const guide={run_id:'run',goal:'Retry uploads',step:'Writing the retry',why:'w',next:'Run the Python check',test_steps:'',limits:'l'};
 const run={id:'run',state:'running',guide,guide_steps:[{step:'Reading the upload code',at:1},{step:'Writing the retry',at:2}]};
 assert.deepEqual(progressPlan({...base,runs:[run]}),[{text:'Reading the upload code',state:'done'},{text:'Writing the retry',state:'now'},{text:'Run the Python check',state:'next'}]);
 assert.deepEqual(progressPlan({...base,state:'completed',runs:[{...run,state:'completed'}]}).map(s=>s.state),['done','done'],'settled: every step is done and nothing is next');
 assert.deepEqual(progressPlan({...base,runs:[{id:'old',state:'failed',guide:{...guide,run_id:'old'},guide_steps:run.guide_steps},{id:'run',state:'running'}]}),[],'an earlier run’s plan is not this run’s');
 assert.deepEqual(progressPlan({...base,runs:[{id:'run',state:'running',guide}]}),[{text:'Writing the retry',state:'now'},{text:'Run the Python check',state:'next'}],'a guide saved before step history still shows its step');
});

test('memory saved this run and a break in context are shown; a continued session says nothing',()=>{
 const run={id:'run',state:'completed',remembered:[{action:'add',text:'Uploads retry three times.'},{action:'remove',text:'',entry_id:'x'}]};
 assert.deepEqual(progressRemembered({...base,runs:[run]}),[{text:'Uploads retry three times.'}]);
 assert.deepEqual(progressRemembered({...base,runs:[{id:'old',remembered:run.remembered},{id:'run',state:'running'}]}),[]);
 assert.equal(progressContinuity({...base,continuity:{state:'continues',reason:null}}),undefined);
 assert.equal(progressContinuity({...base,continuity:{state:'first',reason:null}}),undefined);
 assert.equal(progressContinuity({...base,continuity:{state:'fresh',reason:'Switched to another account.'}}),'New session. Switched to another account.');
});

test('a note dropped to make room rides on the save it made room for, and a stray drop is its own line (beta.11, MEM1)',()=>{
 const run={id:'run',state:'completed',remembered:[
  {action:'dropped',entry_id:'old1',text:'The header was blue.',for_entry_id:'new1'},
  {action:'add',entry_id:'new1',text:'Uploads retry three times.'},
  {action:'replace',entry_id:'keep',text:'Builds run from backend.'},
 ]};
 assert.deepEqual(progressRemembered({...base,runs:[run]}),[
  {text:'Uploads retry three times.',dropped:{id:'old1',text:'The header was blue.'}},
  {text:'Builds run from backend.'},
 ]);
 // Negative control: a drop whose save isn't in the record, or a second drop for the same save, isn't lost.
 const stray={id:'run',state:'completed',remembered:[
  {action:'dropped',entry_id:'gone',text:'Old fact.',for_entry_id:'trimmed'},
  {action:'dropped',entry_id:'old1',text:'First.',for_entry_id:'new1'},
  {action:'dropped',entry_id:'old2',text:'Second.',for_entry_id:'new1'},
  {action:'add',entry_id:'new1',text:'New fact.'},
 ]};
 assert.deepEqual(progressRemembered({...base,runs:[stray]}),[
  {text:'New fact.',dropped:{id:'old1',text:'First.'}},
  {dropped:{id:'gone',text:'Old fact.'}},
  {dropped:{id:'old2',text:'Second.'}},
 ]);
 // A save the full memory refused is still said.
 assert.deepEqual(progressRemembered({...base,runs:[{id:'run',remembered:[{action:'refused',entry_id:'',text:'Tried this.'}]}]}),[{text:'Tried this.',refused:true}]);
 // A save held back for a secret is one line with no words from it; an unknown kind reads as a note, and the record
 // never carries text. Control: a withheld record doesn't swallow the ordinary save beside it.
 assert.deepEqual(progressRemembered({...base,runs:[{id:'run',remembered:[{action:'withheld',what:'lesson'},{action:'withheld',what:'skill'},{action:'withheld',what:'odd'},{action:'add',entry_id:'n1',text:'Keys live in .env.local.'}]}]}),
  [{text:'',withheld:'lesson'},{text:'',withheld:'skill'},{text:'',withheld:'note'},{text:'Keys live in .env.local.'}]);
});

test('a command Claude Code described in words is the step itself, and a bare command stays a command (17 Sep)',async()=>{
 const {nativeStep}=await import('./nativeSteps.ts');
 assert.deepEqual(nativeStep({tool:'native:Bash',command:'Rebuild the preview and take screenshots',prose:true}),{present:'Rebuild the preview and take screenshots',past:'Rebuild the preview and take screenshots',file:false,prose:true});
 assert.deepEqual(nativeStep({tool:'native:Bash',command:'npm run build'}),{present:'Running',past:'Ran',subject:'npm run build',file:false});
});

test('a note the agent forgot, a duplicate, Dervo’s own note and a lesson are each their own line (beta.11, fix-memory)',()=>{
 const run={id:'run',state:'completed',remembered:[
  {action:'remove',entry_id:'old1',text:'The header was blue.'},
  {action:'duplicate',entry_id:'n1',text:'Tests run one file at a time.'},
  {action:'noted',entry_id:'dv1',text:'Dervo checks changes here with its Python unittest check; it last passed on 19 Sep.'},
  {action:'lesson',entry_id:'l1',text:'Run the frontend tests one file at a time.'},
 ]};
 assert.deepEqual(progressRemembered({...base,runs:[run]}),[
  {text:'The header was blue.',removed:{id:'old1'}},
  {text:'Tests run one file at a time.',duplicate:true},
  {text:'Dervo checks changes here with its Python unittest check; it last passed on 19 Sep.',noted:true},
  {text:'Run the frontend tests one file at a time.',lesson:true},
 ]);
 // Negative control: a removal with no words (an old record) and an unknown action say nothing.
 assert.deepEqual(progressRemembered({...base,runs:[{id:'run',remembered:[{action:'remove',entry_id:'x',text:''},{action:'renamed',text:'Something.'}]}]}),[]);
});

test('a correction that took an older note\'s place carries the older words and its id for Undo (critical review 20 Sep, #1)',()=>{
 const run={id:'run',state:'completed',remembered:[
  {action:'supersede',entry_id:'new1',text:'The web tests need Node 22.',superseded_id:'old1',previous:'The web tests need Node 20.'},
  {action:'supersede',entry_id:'new2',text:'The API uses port 9090.'}]};
 assert.deepEqual(progressRemembered({...base,runs:[run]}),[
  {id:'new1',text:'The web tests need Node 22.',superseded:{id:'old1',text:'The web tests need Node 20.'}},
  {id:'new2',text:'The API uses port 9090.'}]);
});
