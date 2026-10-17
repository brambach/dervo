import assert from 'node:assert/strict';
import {test} from 'node:test';
import {WorkspaceDraftPersistence} from './workspaceDraftPersistence.ts';

const key='dervo:project:channel';
const draft=content=>({content,channelId:'channel',selectionStart:0,selectionEnd:0,createdAt:'today',updatedAt:'today',pendingImeta:[],spoileredAttachmentUrls:[],status:'active'});
const row=(revision=0,value=null)=>({key,projectId:'project',channelId:'channel',revision,draft:value});
function fixture(initial=row()){
 let current=structuredClone(initial);const saves=[],states=[];
 const call=async(op,args)=>{
  if(op==='drafts_list')return {entries:[structuredClone(current)]};
  saves.push(structuredClone(args));
  assert.equal(args.revision,current.revision);
  current={...current,revision:current.revision+1,draft:structuredClone(args.draft)};
  return {revision:current.revision};
 };
 return {call,saves,states,controller:new WorkspaceDraftPersistence(call,s=>states.push(s)),read:()=>current};
}

test('host drafts win over stale browser values and tombstones prevent resurrection',async()=>{
 const f=fixture(row(2,draft('host')));
 assert.deepEqual(await f.controller.open([{key,draft:draft('old browser')}]),[{key,draft:draft('host')}]);
 assert.equal(f.saves.length,0);f.controller.close();
 const cleared=fixture(row(3));assert.deepEqual(await cleared.controller.open([{key,draft:draft('sent')}]),[]);
 assert.equal(cleared.saves.length,0);cleared.controller.close();
});

test('imports legacy drafts only for conversations in this workspace',async()=>{
 const f=fixture();
 assert.deepEqual(await f.controller.open([{key,draft:draft('keep')},{key:'dervo:other:private',draft:draft('private')}]),[{key,draft:draft('keep')}]);
 assert.equal(f.saves.length,1);assert.equal(f.read().draft.content,'keep');f.controller.close();
});

test('coalesces typing and persists deliberate clearing',async()=>{
 const f=fixture();await f.controller.open([]);
 f.controller.changed([{key,draft:draft('a')}]);f.controller.changed([{key,draft:draft('abc')}]);
 await f.controller.flush();assert.equal(f.saves.length,1);assert.equal(f.read().draft.content,'abc');
 f.controller.changed([]);await f.controller.flush();assert.equal(f.read().draft,null);f.controller.close();
});

test('edits during a save are committed after its acknowledgement',async()=>{
 const f=fixture();let release,first=true;
 const c=new WorkspaceDraftPersistence(async(op,args)=>{
  if(op==='draft_save'&&first){first=false;await new Promise(r=>release=r);}
  return f.call(op,args);
 },()=>{});
 await c.open([]);c.changed([{key,draft:draft('first')}]);const pending=c.flush();
 c.changed([{key,draft:draft('latest')}]);release();await pending;
 assert.deepEqual(f.saves.map(s=>s.draft.content),['first','latest']);assert.equal(f.read().revision,2);c.close();
});

test('uncertain acknowledgement is reconciled before writing newer text',async()=>{
 const f=fixture();let lost=true,mutation;const attempts=[];
 const c=new WorkspaceDraftPersistence(async(op,args)=>{
  if(op==='drafts_list')return f.call(op,args);
  attempts.push(structuredClone(args));
  if(lost){lost=false;mutation=args.mutationId;await f.call(op,args);throw Error('lost acknowledgement');}
  if(args.mutationId===mutation)return {revision:1};
  return f.call(op,args);
 },s=>f.states.push(s));
 await c.open([]);c.changed([{key,draft:draft('original')}]);await c.flush();
 assert.equal(f.states.at(-1).kind,'error');
 c.changed([{key,draft:draft('new text')}]);await c.flush();assert.equal(attempts.length,1);
 await c.retry();assert.equal(attempts.length,2);assert.equal(attempts[1].revision,1);assert.equal(attempts[1].draft.content,'new text');assert.equal(f.read().draft.content,'new text');c.close();
});

test('a refused legacy draft stays editable and shortening it recovers the queue',async()=>{
 const f=fixture();const c=new WorkspaceDraftPersistence(async(op,args)=>{
  if(op==='draft_save'&&args.draft?.content.length>5)throw Error('too large');
  return f.call(op,args);
 },s=>f.states.push(s));
 assert.deepEqual(await c.open([{key,draft:draft('oversized legacy')}]),[{key,draft:draft('oversized legacy')}]);
 assert.equal(f.states.at(-1).kind,'error');
 c.changed([{key,draft:draft('short')}]);await c.retry();
 assert.equal(f.read().draft.content,'short');assert.equal(f.states.at(-1).kind,'saved');c.close();
});

test('deletion during a non-empty save reconciles and unblocks later work',async()=>{
 let present=true,release;const states=[];
 const c=new WorkspaceDraftPersistence(async(op)=>{
  if(op==='drafts_list')return {entries:present?[row()]:[]};
  await new Promise(r=>release=r);throw Error('removed');
 },s=>states.push(s));
 await c.open([]);c.changed([{key,draft:draft('pending')}]);const saving=c.flush();
 present=false;c.changed([]);release();await saving;assert.equal(states.at(-1).kind,'error');
 await c.retry();assert.equal(states.at(-1).kind,'saved');c.close();
});

test('exit drains a debounce and refuses to proceed after a failed save',async()=>{
 const f=fixture();await f.controller.open([]);f.controller.changed([{key,draft:draft('last edit')}]);
 await f.controller.beforeExit();assert.equal(f.read().draft.content,'last edit');f.controller.close();
 const c=new WorkspaceDraftPersistence(async(op)=>{if(op==='drafts_list')return {entries:[row()]};throw Error('disk');},()=>{});
 await c.open([]);c.changed([{key,draft:draft('keep')}]);await assert.rejects(c.beforeExit());c.close();
});

test('save failure never claims saved and retry remains explicit',async()=>{
 const states=[];let calls=0;
 const c=new WorkspaceDraftPersistence(async(op)=>{if(op==='drafts_list')return {entries:[row()]};calls++;throw Error('disk');},s=>states.push(s));
 await c.open([]);c.changed([{key,draft:draft('keep')}]);await c.flush();
 assert.equal(states.at(-1).kind,'error');await c.flush();assert.equal(calls,1);
 await c.retry();assert.equal(calls,2);c.close();
});

test('new conversation can save after startup and deletion reconciles a removed row',async()=>{
 let exists=false,revision=0;const states=[];
 const c=new WorkspaceDraftPersistence(async(op,args)=>{
  if(op==='drafts_list')return {entries:exists?[row(revision,draft('new'))]:[]};
  if(!exists)throw Error('removed');revision++;return {revision};
 },s=>states.push(s));
 await c.open([]);exists=true;c.changed([{key,draft:draft('new')}]);await c.flush();
 assert.equal(revision,1);exists=false;c.changed([]);await c.flush();assert.equal(states.at(-1).kind,'saved');c.close();
});
