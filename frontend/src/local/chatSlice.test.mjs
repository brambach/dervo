import assert from 'node:assert/strict';
import {test} from 'node:test';
import {queueLocal,registerConversations} from './api.ts';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import * as controls from './TurnControls.tsx';
import {readFileSync} from 'node:fs';

test('native bridge accepts only the explicit scoped status operation',()=>{
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 assert.match(bridge,/Self::ConversationStatus=>"conversation_status"/);
});
test('scoped indicator names bound account and permissions without claiming readiness',()=>{
 assert.equal(typeof controls.ConversationIndicator,'function');
 const binding={provider:'codex',identity:'expected@example.test',model:'exact-model'};
 const render=(status,error)=>renderToStaticMarkup(React.createElement(controls.ConversationIndicator,{binding,status,error,onSettings:()=>{}}));
 // Bryce, 17 Sep: a new conversation showed a lone "Checks before sending". Before anything is sent, the bar says nothing.
 assert.equal(render({state:'unchecked',active:false,detail:'Checked on send.'}),'');
 const idle=render({state:'idle',active:false,detail:'Checked on send.'});
 for(const text of ['Codex','expected@example.test','exact-model','Chat only'])assert.ok(idle.includes(text),text);
 const coding=render({state:'working',active:true,coding:{state:'granted'},harness:{state:'available',helper:'codex',detail:'Coordination only stale tooltip'}});
 assert.ok(coding.includes('Project coding'));
 assert.ok(!coding.includes('Coordination only stale tooltip'));
 assert.ok(render({state:'working',active:true,detail:'Receiving a chat response.'}).includes('Codex is working'));
 assert.ok(render({state:'idle',active:false,detail:'Finished.'},true).includes('Status unavailable'));
 assert.ok(!render({state:'working',active:true,detail:'Receiving.'},true).includes('Codex is working'));
});

function setup(handler){
 const storage=new Map();
 globalThis.localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 globalThis.window={__TAURI_INTERNALS__:{invoke:(_command,{operation,args})=>handler(operation,args)}};
 registerConversations([{id:'chat',project_id:'project'}]);
 return storage;
}
const event={id:'message',content:'Draft',state:'queued'};
for(const state of ['uncertain','failed','cancelled','interrupted','queued']) {
 test(`${state} retains draft request without replay`,async()=>{
  let starts=0;
  const storage=setup(async op=>{
   if(op==='queue')return {id:'message',state};
   if(op==='turn_status')return {active:false,state,error:'Not delivered'};
   if(op==='start_turn'){starts++;return {active:false,state,error:'Not delivered'};}
   if(op==='history')return [event];
   throw new Error(op);
  });
  await assert.rejects(queueLocal('chat','Draft'));
  const request=storage.get('dervo-pending:chat:root');
  assert.ok(request);
  await assert.rejects(queueLocal('chat','Draft'));
  assert.equal(storage.get('dervo-pending:chat:root'),request);
  assert.equal(starts,state==='queued'?2:0);
 });
}

test('delivery becoming uncertain during history read retains the draft',async()=>{
 const storage=setup(async op=>{
  if(op==='queue')return {id:'message'};
  if(op==='turn_status')return {active:true,state:'running'};
  if(op==='history')return [{...event,state:'uncertain'}];
  throw new Error(op);
 });
 await assert.rejects(queueLocal('chat','Draft'),/uncertain/);
 assert.ok(storage.get('dervo-pending:chat:root'));
});
for(const lost of ['queue','start_turn','history']) {
 test(`lost ${lost} acknowledgement retries the same ID without repeating delivery`,async()=>{
  let failed=false,starts=0,state='queued';const ids=[];
  const storage=setup(async(op,args)=>{
   if(op==='queue')ids.push(args.clientId);
   if(op==='start_turn'){starts++;state='completed';}
   if(op===lost&&!failed){failed=true;throw new Error('Lost acknowledgement');}
   if(op==='queue')return {id:'message',state};
   if(op==='turn_status'||op==='start_turn')return {active:false,state};
   if(op==='history')return [{...event,state}];
   throw new Error(op);
  });
  await assert.rejects(queueLocal('chat','Draft'),/Lost acknowledgement/);
  assert.ok(storage.get('dervo-pending:chat:root'));
  await queueLocal('chat','Draft');
  assert.equal(new Set(ids).size,1);
  assert.equal(starts,1);
  assert.equal(storage.size,0);
 });
}
test('transport sending waits for provider acknowledgement before resolving',async()=>{
 let polls=0;
 setup(async op=>{
  if(op==='queue')return {id:'message'};
  if(op==='turn_status')return {active:true,state:++polls===1?'sending':'running'};
  if(op==='history')return [{...event,state:'running'}];
  throw new Error(op);
 });
 await queueLocal('chat','Draft');
 assert.equal(polls,2);
});
test('ordinary chat starts only its new message and waits past asynchronous preflight',async()=>{
 const calls=[];let polls=0;
 setup(async(op,args)=>{calls.push([op,args]);
  if(op==='queue')return {id:'message'};
  if(op==='start_turn')return {active:true,state:'queued'};
  if(op==='turn_status')return ++polls===1?{active:false,state:'queued'}:polls===2?{active:true,state:'queued'}:{active:true,state:'running'};
  if(op==='history')return [{...event,state:'running'}];
  throw new Error(op);
 });
 await queueLocal('chat','Draft');
 assert.equal(calls.filter(([op])=>op==='start_turn').length,1);
 assert.ok(polls>=2,'An accepted preflight is not delivery');
 assert.deepEqual(calls.find(([op])=>op==='start_turn')[1],{projectId:'project',channelId:'chat',messageId:'message'});
});
