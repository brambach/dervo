import assert from 'node:assert/strict';
import {test} from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

// Beta.15 (Bryce, 23 Sep): the "another chat" note and the files Dervo couldn't record are parts of the result card,
// not a rust sentence with a file list in it.
const {decisionLine}=await import('./CodingTasks.tsx');
const {UnrecordedFiles,AlongsideNote}=await import('./ResultNotes.tsx');
const base={id:'t',origin:'conversation',binding:{provider:'claude',identity:'me',model:'m',cwd:'/p',visibility:'shared'},messages:[],tokens:[],runs:[{id:'r',state:'completed'}],
 check_profile:'manual-review',full_access:true,acceptance:{required:true,source:'full-access'},error:null};
const alongside={count:7,files:['PLAN-beta15-20260923.md','evidence/design-tests/landing-depth/PORT.md','evidence/design-tests/landing-depth/depth.css']};

test('another chat in the folder never makes a result need attention',()=>{
 const line=decisionLine({...base,state:'ready_for_review',alongside});
 assert.equal(line.lead,'Ready for review.');
 assert.doesNotMatch(line.clause,/Another chat|PLAN-beta15/);
 assert.equal(decisionLine({...base,state:'idle',review:'approved',alongside}).lead,'Approved.');
 const html=renderToStaticMarkup(React.createElement(AlongsideNote,{alongside}));
 assert.match(html,/Another chat worked in this folder too\. This result has only this task’s edits\./);
 assert.match(html,/Its 7 files/);
 assert.doesNotMatch(html,/PLAN-beta15/,'the files stay behind the toggle until you open it');
});

test('files Dervo couldn’t record are a short lead and a row each, with the fix said once',()=>{
 const unrecorded=[{path:'PLAN-beta15-20260923.md',why:'You had uncommitted changes in it before the run',count:1},{path:'dist/app.js',why:'A hidden, secret or generated file',count:1}];
 const blocked=decisionLine({...base,state:'blocked',unrecorded,error:'Dervo couldn’t record 2 files this run left (PLAN-beta15-20260923.md, dist/app.js), so there’s nothing to approve.'});
 assert.deepEqual([blocked.lead,blocked.clause],['Nothing to approve.','Dervo couldn’t record the 2 files this run left.']);
 assert.notEqual(blocked.lead,'Claude Code stopped before finishing.','it finished; what it left can’t be recorded');
 const partial=decisionLine({...base,state:'ready_for_review',unrecorded:[{path:'new065.py, new066.py and 3 more',why:'More files than one result holds (64)',count:5}]});
 assert.deepEqual([partial.lead,partial.clause],['Can’t approve yet.','Dervo couldn’t record 5 files from this run.']);
 const html=renderToStaticMarkup(React.createElement(UnrecordedFiles,{rows:unrecorded,blocked:true}));
 assert.match(html,/<code>PLAN-beta15-20260923\.md<\/code>/);
 assert.match(html,/You had uncommitted changes in it before the run/);
 assert.match(html,/Commit or remove them, then send the request again\./);
 assert.match(renderToStaticMarkup(React.createElement(UnrecordedFiles,{rows:unrecorded.slice(0,1),blocked:true})),/It’s in your project folder as the agent left it\. Commit or remove it,/);
 assert.doesNotMatch(html,/—/);
 const many=renderToStaticMarkup(React.createElement(UnrecordedFiles,{rows:Array.from({length:8},(_,i)=>({path:`f${i}.py`,why:'Deleted'})),blocked:false}));
 assert.match(many,/Show 3 more/);
 assert.doesNotMatch(many,/f7\.py/);
});

test('only a row for one file opens as a path; a row for several names them in words and has no menu (design review, 23 Sep)',async()=>{
 const {ReplyActionsContext}=await import('../shared/ui/markdown/replyActions.ts');
 const actions={commandsAllowed:false,run(){},preview(){},reveal:async()=>{},openDefault:async()=>{}};
 const rows=[{path:'dist/app.js',why:'A hidden, secret or generated file',count:1},{path:'new065.py, new066.py and 3 more',why:'More files than one result holds (64)',count:5}];
 const html=renderToStaticMarkup(React.createElement(ReplyActionsContext.Provider,{value:actions},React.createElement(UnrecordedFiles,{rows,blocked:false})));
 // The file manager is named for the computer the test runs on (platformWords.ts).
 assert.match(html,/aria-label="dist\/app\.js: open, show in (Finder|File Explorer|Files) or copy"/);
 assert.doesNotMatch(html,/new065\.py, new066\.py and 3 more: open/,'Show in Finder on "a.py, b.py and 3 more" can only fail');
 assert.match(html,/<code>new065\.py, new066\.py and 3 more<\/code>/);
});

test('a file an earlier result left unapproved is one quiet line that says approving commits it too (beta.15)',async()=>{
 const {InheritedNote}=await import('./ResultNotes.tsx');
 const html=renderToStaticMarkup(React.createElement(InheritedNote,{rows:[{path:'PLAN-beta15-update-20260924.md',task_id:'t-first'}]}));
 // Design review S7 (24 Sep): the old "One file here still held an earlier result nobody approved" was hard to parse.
 assert.match(html,/One of these files also has changes from an earlier result you didn’t approve\. Approving this commits those too\./);
 assert.doesNotMatch(html,/still held|nobody approved/);
 assert.match(html,/Which file/);
 assert.doesNotMatch(html,/PLAN-beta15-update/,'the file stays behind the toggle');
 assert.doesNotMatch(html,/—/);
 const two=renderToStaticMarkup(React.createElement(InheritedNote,{rows:[{path:'a.md',task_id:null},{path:'b.md',task_id:null}]}));
 assert.match(two,/2 of these files also have changes from an earlier result you didn’t approve\. Approving this commits them too\./);
 // It doesn't change what the result card leads with: a result that carried a file over is still just ready for review.
 const {decisionLine}=await import('./CodingTasks.tsx');
 assert.equal(decisionLine({...base,state:'ready_for_review',inherited:[{path:'a.md',task_id:'t'}]}).lead,'Ready for review.');
});

test('16.2: a file another thread’s waiting result holds names that thread and says to approve it first',async()=>{
 const {heldBy}=await import('./CodingTasks.tsx');
 const row={path:'PLAN.md',why:'Waiting in 16.2 Home panel’s result. Approve that one first',count:1,task_id:'t-home',thread_id:'home',thread:'16.2 Home panel'};
 const mine={path:'notes.md',why:'You had uncommitted changes in it before the run',count:1};
 assert.deepEqual(heldBy({unrecorded:[mine,row]}),row);
 assert.equal(heldBy({unrecorded:[mine]}),null,'your own edit has no thread to open');
 const blocked=decisionLine({...base,state:'blocked',unrecorded:[row]});
 assert.deepEqual([blocked.lead,blocked.clause],['Nothing to approve.','16.2 Home panel has a result waiting with this file. Approve that one first, then send this again.']);
 const partial=decisionLine({...base,state:'ready_for_review',unrecorded:[row,mine]});
 assert.deepEqual([partial.lead,partial.clause],['Can’t approve yet.','16.2 Home panel has a result waiting with one of these files. Approve that one first, then ask for a correction here.']);
 const unnamed=decisionLine({...base,state:'blocked',unrecorded:[{...row,thread:'another thread'}]});
 assert.match(unnamed.clause,/^Another thread has a result waiting/);
 assert.doesNotMatch(blocked.clause+partial.clause,/—/);
});
