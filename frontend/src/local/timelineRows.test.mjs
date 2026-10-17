import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {orderRows,taskFooterKey,taskRows} from './timelineRows.ts';
const first={id:'first',messages:[{role:'user',text:'Fix greeting',created_at:100},{role:'assistant',text:'Blocked on model',created_at:300}]};
const second={id:'second',messages:[{role:'user',text:'Check greeting.py without changing files',created_at:200}]};
test('a task stays contiguous under its own request, so the newest task footer is last',()=>{
 const rows=orderRows([],[first,second],'c');
 assert.deepEqual(rows.map(r=>r.id),['first:0','first:1','second:0']);
 assert.equal(taskFooterKey(second),'second:0');
 assert.equal(taskFooterKey(first),'first:1');
 assert.equal(rows.at(-1).id,taskFooterKey(second),'the last row carries the newest task footer');
 for(const row of rows)assert.equal('sortKey' in row,false,'ordering key never reaches the timeline');
});
test('history rows interleave by time and task rows keep their pubkeys and tags',()=>{
 const history=[{id:'h1',content:'hi',pubkey:'local-user',kind:9,sig:'',created_at:150,tags:[],state:'queued'}];
 const rows=orderRows(history,[first,second],'chan');
 assert.deepEqual(rows.map(r=>r.id),['first:0','first:1','h1','second:0']);
 const task=taskRows([first],'chan');
 assert.deepEqual(task.map(r=>r.pubkey),['local-user','provider']);
 assert.deepEqual(task[0].tags,[['h','chan']]);
 assert.equal(task[1].created_at,300,'display time is the real message time');
});
test('ConnectedApp uses the shared ordering and footer key',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/orderRows\(history\.data\?\?\[\],tasks\.data\?\?\[\],channelId,current\?\.origin==='overview'\)/);
 assert.match(source,/footers\[taskFooterKey\(task\)\]/);
 assert.doesNotMatch(source,/\$\{task\.id\}:\$\{task\.messages\.length-1\}/);
});
test('recorded helper requests get a short Dervo presentation while exact messages stay untouched',()=>{
 const helper={id:'helper',origin:'collaboration',packet_id:'packet',agent:'reviewer',role:'reviewer',messages:[{role:'user',text:'Exact machine context with file snapshots',created_at:1},{role:'assistant',text:'The saved answer',created_at:2},{role:'user',text:'A real follow-up from you',created_at:3}]};
 const before=structuredClone(helper),rows=taskRows([helper],'review');
 assert.equal(rows[0].content,'Review requested from @reviewer.');
 assert.equal(rows[0].pubkey,'dervo-coordinator');
 assert.equal(rows[0].id,'helper:0');
 assert.deepEqual(rows.slice(1).map(row=>[row.content,row.pubkey]),[['The saved answer','provider'],['A real follow-up from you','local-user']]);
 assert.deepEqual(helper,before,'presentation never changes the host record');
 for(const patch of [{packet_id:undefined},{origin:'conversation'}])assert.equal(taskRows([{...helper,...patch}],'review')[0].content,helper.messages[0].text,'without a recorded helper binding the original text stays visible');
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/if\(pubkey==='dervo-coordinator'\)return 'Dervo'/);
 assert.match(source,/const requestKey=taskRowId\(task,0\),existing=footers\[requestKey\]/);
 assert.match(source,/<\/div>\{existing\}/,'one-message helpers keep their task status below the request');
});

test('host review corrections belong to Dervo; an identical human sentence stays You',()=>{
 const text='Independent review findings: fix the mutation';
 const task={id:'owner',messages:[{role:'user',text,created_at:1},{role:'user',source:'independent_review',text,created_at:2}]};
 const before=JSON.stringify(task);
 assert.deepEqual(taskRows([task],'c').map(r=>r.pubkey),['local-user','dervo-coordinator']);
 assert.equal(JSON.stringify(task),before,'rendering never rewrites saved history');
});

// An overview is a presentation of Dervo's generated request, never a changed provider prompt.
test('project setup uses a short Dervo request and keeps replies, follow-ups and saved instructions',async()=>{
 const {PROJECT_OVERVIEW_REQUEST,PROJECT_OVERVIEW_SUMMARY}=await import('./projectOverview.ts');
 const task={id:'setup',origin:'conversation',read_only:true,messages:[{role:'user',text:PROJECT_OVERVIEW_REQUEST,created_at:1},{role:'assistant',text:'The project does this.',created_at:2},{role:'user',text:'Explain the folder structure.',created_at:3}]};
 const before=structuredClone(task),rows=taskRows([task],'overview',true);
 assert.equal(rows[0].content,PROJECT_OVERVIEW_SUMMARY);
 assert.equal(rows[0].pubkey,'dervo-coordinator');
 assert.deepEqual(rows.slice(1).map(row=>[row.content,row.pubkey]),[['The project does this.','provider'],['Explain the folder structure.','local-user']]);
 assert.deepEqual(task,before);
 for(const [candidate,overview] of [[task,false],[{...task,read_only:false},true],[{...task,origin:'collaboration'},true]])assert.equal(taskRows([candidate],'c',overview)[0].content,PROJECT_OVERVIEW_REQUEST);
 const host=readFileSync(new URL('../../../backend/project_lifecycle.py',import.meta.url),'utf8');
 // The host keeps the words in OVERVIEW_PROMPT (0c66f33), so the run record can name them as Dervo's.
 const prompt=host.slice(host.indexOf('OVERVIEW_PROMPT = ('),host.indexOf('\n\n',host.indexOf('OVERVIEW_PROMPT = (')));
 assert.ok(prompt.length>0,'OVERVIEW_PROMPT is in project_lifecycle.py');
 assert.equal([...prompt.matchAll(/'([^']*)'/g)].map(match=>match[1]).join(''),PROJECT_OVERVIEW_REQUEST);
});

test('beta.16.1: an answer shows as your words, with the question it answered tagged as the asker’s',async()=>{
 const {taskRows}=await import('./timelineRows.ts');
 const {QUESTION_TAG}=await import('./workerQuestionState.ts');
 const ask='Should ideas keep their conversations when an idea is archived?';
 const q=(id,extra={})=>({id,taskId:'t1',runId:'r1',asker:'Claude',ask,options:[],state:'answered',answer:{kind:'text',text:'Keep them'},answerClientId:'c',delivery:'sent',canAnswer:false,canSkip:false,detail:null,createdAt:1,...extra});
 const text=(id,where='from this task',asked=ask)=>`Answer to question ${id} ${where}:\n${asked}\n\nMy answer:\nKeep them.\n\nAnd tell me when it's done.`;
 const tasks=[{id:'t1',origin:'conversation',questions:[q('q1'),q('q2',{carried:true})],messages:[{role:'user',text:'Store ideas',created_at:1},{role:'user',text:text('q1'),created_at:2}]},
  {id:'t2',origin:'conversation',questions:[],messages:[{role:'user',text:text('q2','asked before its run ended'),created_at:3},{role:'assistant',text:text('q1'),created_at:4}]},
  {id:'t3',origin:'conversation',questions:[],messages:[{role:'user',text:text('q9'),created_at:5},{role:'user',text:text('q1','from this task','A question Claude never asked'),created_at:6}]}];
 const rows=Object.fromEntries(taskRows(tasks,'c').map(row=>[row.id,row]));
 for(const id of ['t1:1','t2:0']){
  assert.equal(rows[id].content,'Keep them.\n\nAnd tell me when it\'s done.',`${id}: your words, no ID or header`);
  assert.deepEqual(rows[id].tags,[['h','c'],[QUESTION_TAG,'Claude',ask]]);
 }
 // The question's words come from its record in this thread, never from the text alone.
 assert.equal(rows['t2:1'].content,text('q1'),'an agent’s reply is never read as your answer');
 assert.equal(rows['t3:0'].content,text('q9'),'a question not on record here stays as written');
 assert.equal(rows['t3:1'].content,text('q1','from this task','A question Claude never asked'),'different wording stays as written');
 assert.deepEqual(rows['t3:1'].tags,[['h','c']]);
});

test('a thread Home started shows only your words as your message, never Dervo’s brief to the agent',async()=>{
 const {taskRows,yourWords}=await import('./timelineRows.ts');
 const brief="Home routed this request to this chat. Work only on the quoted focus within the user's request. Existing approval and tool permissions still apply.\n\n"
  +'Focus for this chat:\nFix signup test\n\nYour request:\nPlease fix the signup test\nand add a dark mode toggle\n\nPrevious request for context:\nearlier';
 assert.equal(yourWords(brief),'Please fix the signup test\nand add a dark mode toggle');
 assert.equal(yourWords(brief.replace(/\n\nPrevious request[\s\S]*$/,'')),'Please fix the signup test\nand add a dark mode toggle','the last part runs to the end');
 assert.equal(yourWords('fix the picker'),'fix the picker');
 const [mine,reply]=taskRows([{id:'t',origin:'conversation',messages:[{role:'user',text:brief,created_at:1},{role:'assistant',text:'Your request: done',created_at:2}]}],'c');
 assert.equal(mine.content,'Please fix the signup test\nand add a dark mode toggle');
 assert.equal(mine.pubkey,'local-user');
 assert.equal(reply.content,'Your request: done','the agent’s words are never cut');
});
