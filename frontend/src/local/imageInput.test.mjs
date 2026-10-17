import assert from 'node:assert/strict';
import {test,afterEach} from 'node:test';
import {imageSize,uploadImage,prepareAttachment,thumbnailOf,tilePreview} from './imageInput.ts';
const scope={projectId:'project-a',channelId:'conversation-a'};
afterEach(()=>{delete globalThis.window;delete globalThis.createImageBitmap;delete globalThis.document;});
function host(handler){globalThis.window={__TAURI_INTERNALS__:{invoke:async(_,payload)=>handler(payload.operation,payload.args)}};}

test('resizing preserves proportions without enlarging a small screenshot',()=>{
 assert.deepEqual(imageSize(4000,2000),{width:2048,height:1024});
 assert.deepEqual(imageSize(100,50),{width:100,height:50});
 for(const size of [[0,10],[14000,10],[8000,8000],[NaN,12]])assert.throws(()=>imageSize(...size));
});

test('upload retries lost acknowledgements with the same scope, id and exact chunk bytes',async()=>{
 const bytes=Uint8Array.from({length:100_000},(_,i)=>i%251),calls=[],progress=[];
 let stored=new Uint8Array(),lost=true;
 host((operation,args)=>{
  calls.push({operation,args});
  assert.equal(args.projectId,scope.projectId);assert.equal(args.channelId,scope.channelId);
  if(operation==='image_begin')return {id:'image-a',state:'uploading',received:0};
  if(operation==='image_chunk'){
   const incoming=Uint8Array.from(atob(args.data),c=>c.charCodeAt(0));
   if(args.offset===stored.length)stored=Uint8Array.from([...stored,...incoming]);
   else assert.deepEqual(stored.slice(args.offset,args.offset+incoming.length),incoming);
   if(lost){lost=false;throw Error('Acknowledgement lost');}
   return {id:'image-a',state:'uploading',received:stored.length};
  }
  assert.equal(operation,'image_finish');return {id:'image-a',state:'ready',received:bytes.length};
 });
 const result=await uploadImage(scope,'Screenshot.png',bytes,'stable-client',()=>true,row=>progress.push(row));
 assert.equal(result.state,'ready');assert.deepEqual(stored,bytes);
 assert.deepEqual(calls[1],calls[2]);assert.equal(calls[0].args.clientId,'stable-client');
 assert.match(calls[0].args.sha256,/^[a-f0-9]{64}$/);
 assert(calls.every(call=>JSON.stringify(call).length<131072));
 assert(calls.every(call=>call.operation.startsWith('image_')));
 assert.equal(progress.at(-1).state,'ready');
});

test('a resumed upload starts at the saved offset and a completed retry sends no bytes again',async()=>{
 const calls=[];
 host((operation,args)=>{calls.push({operation,args});return operation==='image_begin'?{id:'a',state:'uploading',received:10}:{id:'a',state:operation==='image_finish'?'ready':'uploading',received:20};});
 await uploadImage(scope,'a.png',new Uint8Array(20),'same',()=>true,()=>{});
 assert.equal(calls[1].args.offset,10);
 host((operation)=>{assert.equal(operation,'image_begin');return {id:'a',state:'ready',received:20};});
 assert.equal((await uploadImage(scope,'a.png',new Uint8Array(20),'same',()=>true,()=>{})).state,'ready');
});

test('leaving the conversation stops further writes, including error retries',async()=>{
 let active=true,calls=0;
 host(()=>{calls++;active=false;throw Error('Interrupted');});
 await assert.rejects(uploadImage(scope,'a.png',new Uint8Array(20),'same',()=>active,()=>{}));
 assert.equal(calls,1);
});

test('an unconfirmed coding send restores its exact image ids and reuses the request id',async()=>{
 const {pendingImageRequest}=await import('./imageInput.ts');
 const {codingRequest,registerConversations}=await import('./api.ts');
 const storage=new Map();globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
 registerConversations([{id:scope.channelId,project_id:scope.projectId}]);
 const calls=[];
 host((operation,args)=>{calls.push({operation,args});if(calls.length===1)throw Error('Acknowledgement lost');return {id:'saved-request',taskId:'saved-task',state:'working'};});
 await assert.rejects(codingRequest(scope.channelId,'Explain this',undefined,['pinned-image']));
 const pending=pendingImageRequest(scope);
 assert.deepEqual(pending,{text:'Explain this',images:['pinned-image']});
 await codingRequest(scope.channelId,pending.text,undefined,pending.images);
 assert.equal(calls.length,2);assert.equal(calls[0].args.clientId,calls[1].args.clientId);
 assert.equal(pendingImageRequest(scope),null);
 delete globalThis.localStorage;
});


test('document preparation keeps CSV bytes and refuses unsupported or oversized input',async()=>{
 const file=new File(['name,total\nBryce,42\n'],'sales.csv',{type:'text/csv'});
 assert.equal(new TextDecoder().decode(await prepareAttachment(file)),'name,total\nBryce,42\n');
 await assert.rejects(()=>prepareAttachment(new File(['binary'],'app.exe')),/Attach an image/);
 await assert.rejects(()=>prepareAttachment(new File(['x'.repeat(2*1024*1024+1)],'large.csv')),/2 MB/);
});

test('a tile hands the host its small picture, then reads the small one back',async()=>{
 const calls=[];
 host((operation,args)=>{calls.push([operation,args.id,args.data]);return operation==='image_thumb_put'?{saved:true}:{id:args.id,name:'Shot.png',dataUrl:'data:image/png;base64,small',thumbnail:true};});
 const preview=await tilePreview(scope,{id:'image-a',width:400,height:300},'c21hbGw=');
 assert.deepEqual(calls,[['image_thumb_put','image-a','c21hbGw='],['image_thumb','image-a',undefined]]);
 assert.equal(preview.thumbnail,true);
});

test('a refused preview still shows the picture, and a document reads its text',async()=>{
 const calls=[];
 host((operation,args)=>{calls.push(operation);if(operation==='image_thumb_put')throw Error('This preview doesn’t match the image.');return {id:args.id,name:'x',dataUrl:'data:image/png;base64,full',thumbnail:false};});
 assert.equal((await tilePreview(scope,{id:'image-a',width:400,height:300},'bad')).dataUrl,'data:image/png;base64,full');
 assert.deepEqual(calls,['image_thumb_put','image_thumb']);
 calls.length=0;
 await tilePreview(scope,{id:'doc-a',width:null,height:null});
 assert.deepEqual(calls,['image_get'],'no picture to put, and a document has none to read');
});

test('a thumbnail is 192px on its short side and never past 640 on its long one',async()=>{
 const drawn=[];
 globalThis.createImageBitmap=async file=>({...file.size_,close(){}});
 globalThis.document={createElement:()=>{const canvas={width:0,height:0,getContext:()=>({drawImage:()=>drawn.push([canvas.width,canvas.height])}),toBlob:done=>done(new Blob([Uint8Array.of(137,80,78,71)]))};return canvas;}};
 const picture=(width,height,type='image/png')=>Object.assign(new Blob([],{type}),{size_:{width,height}});
 assert.equal(await thumbnailOf(picture(4000,3000)),btoa(String.fromCharCode(137,80,78,71)));
 await thumbnailOf(picture(100,50));await thumbnailOf(picture(4000,100));
 assert.deepEqual(drawn,[[256,192],[100,50],[640,16]]);
 assert.equal(await thumbnailOf(picture(10,10,'text/csv')),null,'a document has no thumbnail');
 globalThis.createImageBitmap=async()=>{throw Error('decode failed');};
 assert.equal(await thumbnailOf(picture(10,10)),null,'a picture that can’t decode just has none');
});
