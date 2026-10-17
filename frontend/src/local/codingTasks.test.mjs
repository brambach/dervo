import assert from 'node:assert/strict';
import {test} from 'node:test';
import {existsSync,readFileSync} from 'node:fs';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ConversationIndicator} from './TurnControls.tsx';
test('task controls are wired into the channel without a second shell',async()=>{
 const url=new URL('./CodingTasks.tsx',import.meta.url);
 assert.ok(existsSync(url),'Coding task dialog is missing');
 const {TaskDisclosure,CODING_PROVIDERS}=await import('./CodingTasks.tsx');
 const html=renderToStaticMarkup(React.createElement(TaskDisclosure,{binding:{provider:'codex',identity:'expected@test',model:'model',cwd:'/approved',visibility:'private'}}));
 for(const value of ['expected@test','model','/approved','private','filesystem','existing files'])assert.ok(html.includes(value),value);
 assert.deepEqual([...CODING_PROVIDERS].sort(),['claude','codex']);
 const claude=renderToStaticMarkup(React.createElement(TaskDisclosure,{binding:{provider:'claude',identity:'personal@test',model:'claude-fable-5-1',cwd:'/approved',visibility:'private'}}));
 for(const value of ['claude-fable-5-1','Dervo host tools','never as success','external MCP servers'])assert.ok(claude.includes(value),value);
 assert.ok(!claude.includes('blocked'),'Claude disclosure must not claim coding is blocked');
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/TaskFooter/);
 // 19 Sep: the review bar dropped Run it when the thread screen moved to the design system; the footer must get it.
 assert.match(source,/<TaskFooter [^>]*onRunIt=\{/,'Run it reaches the review bar');
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['task_create','task_approve','task_detail','task_review','task_continue','task_stop','task_deny','tasks'])assert.ok(bridge.includes(`"${op}"`),op);
});
test('routine policy stays in details and actionable blockers stay visible',()=>{
 const binding={provider:'codex',identity:'account',model:'model'};
 const render=state=>renderToStaticMarkup(React.createElement(ConversationIndicator,{binding,status:{state,detail:'actionable detail'},onSettings:()=>{}}));
 assert.ok(!render('idle').includes('<p>'),'Idle row should stay compact');
 assert.ok(render('blocked').includes('actionable detail'));
});

test('the decision bar says the outcome once, with one clause about checks or the commit, and nothing while the owner works',async()=>{
 const {decisionLine,checksClause}=await import('./CodingTasks.tsx');
 const base={id:'t',origin:'conversation',binding:{provider:'claude',identity:'me',model:'m',cwd:'/p',visibility:'shared'},messages:[],tokens:[],runs:[{id:'r',state:'completed'}],check_profile:'python-unittest'};
 const review=decisionLine({...base,state:'ready_for_review',check_profile:'manual-review',acceptance:{required:true}});
 assert.deepEqual([review.glyph,review.accent,review.lead,review.clause,review.inspect],['needs',true,'Ready for review.','No checks ran in Dervo.','Inspect']);
 assert.equal(checksClause({...base,completion:{test_exit_status:0}}),'Python check passed.');
 assert.equal(checksClause({...base,completion:{test_exit_status:1}}),'Python check failed.');
 assert.equal(checksClause({...base,full_access:true,collaboration:{review:{phase:'passed'}}}),'No checks ran in Dervo. Review passed.');
 // 17.1 (#42): a skipped review says why on the bar, the host's first sentence, with an automatic reviewer called Reviewer.
 const gone='The reviewer was deleted, so no review was run. Turn on Second review in project settings to pick another.';
 assert.equal(checksClause({...base,full_access:true,collaboration:{review:{phase:'skipped',error:gone}}}),'No checks ran in Dervo. The reviewer was deleted, so no review was run.');
 const unlinked="@review-448cf4bed5333b39bf9133ae runs on Codex, which isn't linked to this conversation's Claude Code account, so no review was run. Link the two accounts in Settings.";
 assert.equal(checksClause({...base,completion:{test_exit_status:0},collaboration:{review:{phase:'skipped',error:unlinked}}}),"Python check passed. Reviewer runs on Codex, which isn't linked to this conversation's Claude Code account, so no review was run.");
 assert.equal(checksClause({...base,full_access:true,collaboration:{review:{phase:'failed',error:'The review packet record is missing'}}}),'No checks ran in Dervo.');
 // Critic, 9 Oct: a full-access run whose project checks ran says what they found, in the host's words.
 const checked=state=>({...base,full_access:true,runs:[{id:'r',state:'completed',checks:state}]});
 assert.equal(checksClause({...checked({state:'running'}),collaboration:{review:{phase:'passed'}}}),'Dervo is running your checks.');
 assert.equal(checksClause({...checked({state:'passed',summary:'Checks pass'}),collaboration:{review:{phase:'passed'}}}),'Checks pass. Review passed.');
 assert.equal(checksClause({...checked({state:'new_failures',summary:'1 new failure: test_send_order'}),collaboration:{review:{phase:'passed'}}}),'1 new failure: test_send_order.');
 assert.equal(checksClause(checked({state:'skipped',summary:'Checks didn’t run, because this thread can’t run commands'})),'Checks didn’t run, because this thread can’t run commands.');
 assert.equal(checksClause(checked(undefined)),'No checks ran in Dervo.');
 assert.equal(checksClause({...checked({state:'passed',summary:'Checks pass'}),check_profile:'manual-review'}),'Checks pass.','a manual-review grant still runs them in full access');
 // With approval off a skipped review keeps the result from completing on its own, and the bar says why there too.
 const waiting=decisionLine({...base,state:'ready_for_review',acceptance:{required:false},collaboration:{review:{phase:'skipped',error:gone}}});
 assert.deepEqual([waiting.lead,waiting.clause],['Needs attention.','The reviewer was deleted, so no review was run.']);
 assert.equal(decisionLine({...base,state:'ready_for_review',acceptance:{required:false},collaboration:{review:{phase:'failed',error:'x'}}}).clause,'Independent review ended failed.');
 assert.equal(decisionLine({...base,state:'working',runs:[{id:'r',state:'running'}]}),null);
 assert.equal(decisionLine({...base,state:'answered'}),null);
 // Beta.18 (record-b): an answer that met a hand commit, or wrote outside the project, keeps a quiet card for the notes.
 const moved=decisionLine({...base,state:'answered',moved_head:{branch:'mvp1',commits:[{sha:'1516d13b7a40',subject:'x'}]}});
 assert.deepEqual([moved.glyph,moved.lead,moved.accent],['done','Finished.',undefined]);
 assert.equal(decisionLine({...base,state:'answered',outside:{count:1,files:['/tmp/x.txt']}}).lead,'Finished.');
 assert.deepEqual(decisionLine({...base,state:'idle',review:'approved',commit:{sha:'3f9c2a17b0d4',branch:'mvp1'}}).lead,'Approved.');
 assert.equal(decisionLine({...base,state:'idle',review:'approved',commit:{sha:'3f9c2a17b0d4',branch:'mvp1'}}).clause,'Committed 3f9c2a1 on mvp1.');
 // 16.2: Commit results automatically says so, and an undone one reads as an ordinary approval again.
 assert.equal(decisionLine({...base,state:'idle',review:'approved',commit:{sha:'3f9c2a17b0d4',branch:'mvp1',how:'automatic'}}).lead,'Committed automatically.');
 assert.equal(decisionLine({...base,state:'idle',review:'approved',commit:{sha:'3f9c2a17b0d4',how:'automatic',reverted:{sha:'9e1b'}}}).lead,'Approved.');
 const failed=decisionLine({...base,state:'blocked',error:'The server is overloaded.',runs:[{id:'r',state:'failed'}]});
 assert.deepEqual([failed.glyph,failed.lead,failed.clause],['failed','Claude Code stopped before finishing.','The server is overloaded.']);
 assert.equal(decisionLine({...base,state:'blocked',error:'x',runs:[{id:'r',state:'failed'}],suggest:{reason:'x',kind:'overloaded',other:null,automatic:false,taken:null,error:null}}),null,'the handoff row is the home when a provider failed');
 assert.deepEqual(decisionLine({...base,state:'blocked',runs:[{id:'r',state:'interrupted'}]}).glyph,'stopped');
 assert.equal(decisionLine({...base,state:'ready_for_review',review:'rejected'}).lead,'Waiting for your feedback.');
 assert.equal(decisionLine({...base,state:'ready_for_review',acceptance:{required:false}}).lead,'Needs attention.');
 assert.equal(decisionLine({...base,state:'needs_permission'}).inspect,'Review the scope');
 assert.equal(decisionLine({...base,state:'disconnected',runs:[{id:'r',state:'uncertain'}]}).lead,'Outcome not confirmed.');
 // 16.4, Pause: your own pause is one quiet line that says how to carry on, and a quit that paused a run says so.
 const paused=decisionLine({...base,state:'blocked',runs:[{id:'r',state:'interrupted'}]});
 assert.deepEqual([paused.glyph,paused.lead,paused.clause],['stopped','Paused.','Its edits so far are in your project folder.']);
 const quit=decisionLine({...base,state:'disconnected',error:'Dervo quit mid-run and paused it before it could say how far it got. Check your folder, then send a message to carry on.',runs:[{id:'r',state:'uncertain'}]});
 assert.deepEqual([quit.glyph,quit.lead],['stopped','Paused when Dervo quit.']);
});

test('#27: the approve label promises a commit only when the host says the folder is a git repository',async()=>{
 const {approveLabel}=await import('./CodingTasks.tsx');
 assert.equal(approveLabel(true),'Approve and commit');
 assert.equal(approveLabel(true,'Approve result'),'Approve and commit');
 for(const git of [false,undefined]){
  assert.equal(approveLabel(git),'Approve');
  assert.equal(approveLabel(git,'Approve result'),'Approve result');
 }
 const source=readFileSync(new URL('./CodingTasks.tsx',import.meta.url),'utf8');
 assert.doesNotMatch(source,/'Approving…':'Approve'\}/,'no approve button hard-codes its label');
 assert.doesNotMatch(source,/>Approve result<\/Button>/,'no approve button hard-codes its label');
});

test('the task sheet says what approving does: it commits these files in a git repository, and never says "attempt" (UX review 23 Sep, M4)',async()=>{
 const {approvingLine}=await import('./CodingTasks.tsx');
 assert.equal(approvingLine(true,['a.py','b.py']),'Approving commits these 2 files.');
 assert.equal(approvingLine(true,['a.py']),'Approving commits this file.');
 assert.equal(approvingLine(true,['a.py','b.py'],'main'),'Approving commits these 2 files to main.','the branch when a record names it');
 // Outside git, or before the host has said, nothing is promised about a commit.
 for(const git of [false,undefined])assert.equal(approvingLine(git,['a.py']),'Approving records your decision.');
 assert.equal(approvingLine(true,[]),'Approving records your decision.','no files, nothing to commit');
 const source=readFileSync(new URL('./CodingTasks.tsx',import.meta.url),'utf8');
 assert.doesNotMatch(source,/decision on attempt|You approved attempt/);
 assert.doesNotMatch(source,/>Request correction</,'one name for sending work back');
});

test('after a handoff the sheet says what else approving commits, and what it leaves out, and sends the list’s id (23 Sep)',async()=>{
 const {approvingLine,carriedLine,leftOutLines,committedFiles,carriedArgs,CarriedNote}=await import('./CodingTasks.tsx');
 const carried={id:'c1',from_task_id:'t0',here:'Claude',to:'Codex',files:['greeting.py','notes.md','a.py','b.py'],
  flagged:[{path:'todo.md',why:'changed after the handoff, and not by either run'}]};
 assert.equal(carriedLine(carried),'Includes 4 files Claude changed before the handoff: greeting.py, notes.md, a.py and 1 more.');
 assert.equal(carriedLine({...carried,files:['notes.md']}),'Includes 1 file Claude changed before the handoff: notes.md.');
 assert.equal(carriedLine({...carried,files:[]}),null);
 assert.equal(carriedLine(null),null);
 assert.deepEqual(leftOutLines(carried),['Left out: todo.md, changed after the handoff, and not by either run.']);
 const files=committedFiles(['greeting.py'],carried);
 assert.deepEqual(files,['greeting.py','notes.md','a.py','b.py'],'Codex’s file once, then Claude’s');
 assert.equal(approvingLine(true,files),'Approving commits these 4 files.');
 assert.deepEqual(carriedArgs(carried),{carriedId:'c1'});
 assert.deepEqual(carriedArgs(undefined),{},'any other task sends what it always sent');
 const html=renderToStaticMarkup(React.createElement(CarriedNote,{carried}));
 assert.ok(html.includes('Includes 4 files Claude changed')&&html.includes('Left out: todo.md'));
 assert.equal(renderToStaticMarkup(React.createElement(CarriedNote,{carried:null})),'');
 assert.equal(html.includes('—'),false,'no em dash');
});

test('after Ask for changes, a plain message carries on the waiting task instead of starting a second one',async()=>{
 const {correctionTarget}=await import('./CodingTasks.tsx');
 const task=(id,extra={})=>({id,origin:'conversation',state:'ready_for_review',review:'rejected',messages:[],tokens:[],runs:[],...extra});
 assert.equal(correctionTarget([task('old',{state:'idle',review:'approved'}),task('t')])?.id,'t');
 assert.equal(correctionTarget([task('t')],true),undefined,'a message with images starts its own task');
 assert.equal(correctionTarget([task('t',{review:null})]),undefined,'only after Ask for changes');
 assert.equal(correctionTarget([task('t'),task('newer',{state:'idle',review:'approved'})]),undefined,'only the newest result');
 assert.equal(correctionTarget([task('t'),task('helper',{origin:'collaboration',state:'working'})])?.id,'t','a helper’s task isn’t yours');
});
