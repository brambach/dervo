import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
const {sendingRow}=await import('./CodingQueue.tsx');
const {mergeHarnessRows}=await import('./Harness.tsx');

test('a message you send shows at once as your own row, after everything already in the conversation (17 Sep: "slight delay then everything appears")',()=>{
 const now=Date.UTC(2026,8,17,12)/1000;
 const row=sendingRow('c','Fix the header',now*1000);
 assert.deepEqual([row.pubkey,row.content,row.state,row.tags],['local-user','Fix the header','sending',[['h','c']]]);
 const older={id:'t:0',content:'Earlier',pubkey:'provider',kind:9,sig:'',created_at:now+60,tags:[['h','c']],state:'task'};
 assert.deepEqual(mergeHarnessRows([older],[row]).map(r=>r.id),['t:0',row.id],'even a row stamped later by a skewed clock stays above it');
 // The shell adds the row before it waits on the host and removes it once the saved row has been fetched.
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 // The coding send is found first, then its own add before it and its own drop after it (the @agent send has its own pair).
 const send=source.indexOf('await codingRequest(target.id'),add=source.lastIndexOf('setSending(rows=>[...rows,pending])',send),drop=source.indexOf('finally{setSending(rows=>rows.filter(row=>row.id!==pending.id));}',send);
 assert.ok(add>0&&add<send&&send<drop,'added before the request, removed after the refresh');
});
