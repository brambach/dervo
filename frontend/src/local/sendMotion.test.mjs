import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','Node','Event','KeyboardEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
let reduced=false;
window.matchMedia=()=>({matches:reduced,addEventListener(){},removeEventListener(){}});
const sendMotion=await import('./sendMotion.ts');
const {sendMotionPlan,glideOffset,nearBottom,armSendMotion,playSendMotion,armedSendMotion,ARM_TTL_MS,GLIDE_MAX_PX}=sendMotion;
after(()=>dom.window.close());
afterEach(()=>{reduced=false;document.body.innerHTML='';});
const box=(left,top,width,height)=>({left,top,width,height});

test('the bubble starts where the composer text was: the plan is the offset from its landing place, transform only',()=>{
 assert.deepEqual(sendMotionPlan(box(292,752,712,44),box(446,123,546,68),false),{dx:-154,dy:629});
 // Negative controls: nothing to travel from or to, or no distance, means the row simply appears in place.
 assert.equal(sendMotionPlan(null,box(446,123,546,68),false),null);
 assert.equal(sendMotionPlan(box(292,752,712,44),undefined,false),null);
 assert.equal(sendMotionPlan(box(292,752,0,0),box(446,123,546,68),false),null,'an unmeasured composer');
 assert.equal(sendMotionPlan(box(292,752,712,44),box(446,123,0,0),false),null,'a row that has no size yet');
 assert.equal(sendMotionPlan(box(292,752,712,44),box(446,-900,546,68),false),null,'a row above the viewport');
 assert.equal(sendMotionPlan(box(10,10,100,20),box(10.5,10.2,100,20),false),null,'already in place');
 assert.equal(sendMotionPlan(box(292,752,712,44),box(446,123,546,68),true),null,'reduced motion: instant');
});

test('a scroll that makes room for the row is glided only when the reader was at the bottom and it is a row or two',()=>{
 assert.equal(glideOffset(400,476,true),76);
 assert.equal(glideOffset(400,476,false),0,'a reader up in the history is not pulled');
 assert.equal(glideOffset(400,400,true),0,'no move, no glide');
 assert.equal(glideOffset(318,264,true),-54,'the floor moving up (the composer shrank) glides too, the other way');
 assert.equal(glideOffset(0,GLIDE_MAX_PX+1,true),0,'a far jump is another view');
 assert.equal(glideOffset(GLIDE_MAX_PX+1,0,true),0,'either way');
 assert.equal(glideOffset(0,NaN,true),0,'a missing measurement is no move');
 assert.equal(nearBottom(568,1200,600),true);
 assert.equal(nearBottom(500,1200,600),false);
});

test('arming measures the composer before the editor clears; playing uses it once and only within the send moment',()=>{
 document.body.innerHTML='<div id="c"><div data-testid="message-input-scroll"><textarea data-testid="message-input">words</textarea></div></div><div data-buzz-conversation-scroll><div id="inner"></div></div>';
 const composer=document.getElementById('c');
 const rect=(l,t,w,h)=>()=>({left:l,top:t,width:w,height:h,right:l+w,bottom:t+h,x:l,y:t});
 composer.querySelector('[data-testid="message-input"]').getBoundingClientRect=rect(292,700,712,96);
 composer.querySelector('[data-testid="message-input-scroll"]').getBoundingClientRect=rect(292,700,712,96);
 armSendMotion(composer,null);
 assert.deepEqual(armedSendMotion().text,{left:292,top:700,width:712,height:96});
 assert.equal(armedSendMotion().wrapHeight,96);
 // No row yet: the list draws it a frame later, so the measurement is held for the next frames, not spent.
 const frames=[];globalThis.requestAnimationFrame=callback=>{frames.push(callback);return frames.length;};
 assert.equal(playSendMotion(null,composer,null),false,'no row to land in yet: nothing travels');
 assert.ok(armedSendMotion(),'the measurement waits for the row');
 assert.equal(frames.length,1);
 // A frame later there is still no row: after a few frames it gives up and the measurement is spent.
 for(let i=0;i<8&&frames.length>i;i++)frames[i]();
 assert.equal(armedSendMotion(),null,'spent after the retry frames');
 // Reduced motion arms nothing.
 reduced=true;armSendMotion(composer,null);assert.equal(armedSendMotion(),null);
 reduced=false;
 // A stale measurement (older than the send moment) is not reused for a later row.
 armSendMotion(composer,null);
 const now=performance.now;performance.now=()=>now.call(performance)+ARM_TTL_MS+1;
 try{assert.equal(playSendMotion(document.getElementById('inner'),composer,null),false);}finally{performance.now=now;}
 assert.equal(armedSendMotion(),null);
 // No composer, no text box: arming is a no-op rather than a throw.
 armSendMotion(null,null);assert.equal(armedSendMotion(),null);
});

test('a delayed sent row never animates the previous message while waiting for its own ID',()=>{
 document.body.innerHTML='<div id="composer"><textarea data-testid="message-input">new words</textarea></div><div data-testid="message-row" data-message-id="old" data-pubkey="local-user"></div>';
 const composer=document.getElementById('composer');
 armSendMotion(composer,null);
 const frames=[];globalThis.requestAnimationFrame=callback=>{frames.push(callback);return frames.length;};
 assert.equal(playSendMotion('sending:new',composer,null),false);
 assert.ok(armedSendMotion(),'the old row must not consume the new row’s measurement');
 const row=document.createElement('div');row.dataset.testid='message-row';row.dataset.messageId='sending:new';document.body.append(row);
 frames.shift()();
 assert.equal(armedSendMotion(),null,'the matching row consumes its own measurement');
 armSendMotion(composer,null);
 playSendMotion('sending:other',composer,null);
 sendMotion.cancelSendMotion();
 for(const frame of frames)frame();
 assert.equal(armedSendMotion(),null,'a conversation switch cancels pending retries');
});

test('the row you sent keeps one identity until the host’s copy replaces it, and the two never show together',()=>{
 const {sentRowKeys,rememberSent,SENT_KEY_TTL_MS}=sendMotion;
 const keys=new Map();
 const row=(id,body,pubkey='local-user')=>({id,body,pubkey});
 rememberSent(keys,'Fix the header','sent:1',1000);
 // Sending row alone: it carries the key.
 assert.deepEqual(sentRowKeys([row('a:0','Earlier'),row('sending:1','Fix the header')],keys,1500).map(m=>[m.id,m.renderKey]),[['a:0',undefined],['sending:1','sent:1']]);
 // The saved copy has arrived while the sending row is still there: the copy takes the key and the sending row steps aside.
 assert.deepEqual(sentRowKeys([row('a:0','Earlier'),row('t:0','Fix the header'),row('sending:1','Fix the header')],keys,1500).map(m=>[m.id,m.renderKey]),[['a:0',undefined],['t:0','sent:1']]);
 // Negative controls: a reply with the same words, an old send, and two saved copies of the same words.
 assert.deepEqual(sentRowKeys([row('t:1','Fix the header','provider')],keys,1500).map(m=>m.renderKey),[undefined],'a reply is not yours');
 assert.deepEqual(sentRowKeys([row('t:0','Fix the header')],keys,1000+SENT_KEY_TTL_MS+1).map(m=>m.renderKey),[undefined],'a minute later the key is forgotten');
 assert.deepEqual(sentRowKeys([row('t:0','Fix the header'),row('u:0','Fix the header')],keys,1500).map(m=>m.renderKey),[undefined,'sent:1'],'only the newest copy takes it, so keys stay unique');
 assert.equal(sentRowKeys([row('t:0','x')],new Map()).length,1,'no sends: rows pass through');
 rememberSent(keys,'Later','sent:2',1000+SENT_KEY_TTL_MS+1);
 assert.equal(keys.has('Fix the header'),false,'stale entries are pruned as new ones arrive');
});

test('the saved row that takes a sending row’s place keeps its pictures until its task shows them (send pipeline, 9 Oct)',()=>{
 const {sentRowKeys,rememberSent,handedOverImages}=sendMotion;
 const keys=new Map(),sending=[{id:'sending:1',images:['img-1']}];
 rememberSent(keys,'Fix the header','sent:sending:1',1000);
 const rows=sentRowKeys([{id:'t:0',body:'Fix the header',pubkey:'local-user'},{id:'sending:1',body:'Fix the header',pubkey:'local-user'}],keys,1500);
 assert.deepEqual([...handedOverImages(rows,sending)],[['t:0',['img-1']]]);
 assert.equal(handedOverImages(rows,[]).size,0,'once the sending row is gone, the task carries them');
 assert.equal(handedOverImages([{id:'sending:1',renderKey:'sent:sending:1'}],sending).size,0,'the sending row keeps its own');
 assert.equal(handedOverImages(rows,[{id:'sending:1'}]).size,0,'a send without pictures carries none');
});
