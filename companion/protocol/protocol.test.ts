import assert from 'node:assert/strict';
import {test} from 'node:test';
import {decode,encode,fromUtf8,utf8} from './b64.ts';
import {newKeyPair,open,seal,isEnvelope} from './crypto.ts';
import {offerLink,readOffer,type PairingOffer} from './pairing.ts';
import {readMacMessage,readPhoneMessage} from './messages.ts';
import {bucketOf,buildSnapshot,stopTargets,threadOf} from './home.ts';
import {readToRelay} from './wire.ts';

test('base64url round-trips every length and refuses near misses',()=>{
  for(let n=0;n<70;n++){
    const bytes=Uint8Array.from({length:n},(_,i)=>(i*37+n)&255);
    const text=encode(bytes);
    assert.match(text,/^[A-Za-z0-9_-]*$/);
    assert.deepEqual(decode(text),bytes);
    assert.equal(text,Buffer.from(bytes).toString('base64url'));
  }
  assert.equal(decode('ab+c'),null);
  assert.equal(decode('a'),null);
  assert.equal(decode('AB'),null,'non-zero leftover bits have one spelling only');
});

test('UTF-8 by hand matches the platform and refuses malformed bytes',()=>{
  for(const text of ['','plain','café','日本語','emoji 🙂 mixed','\u0000'])
    {assert.deepEqual(utf8(text),new TextEncoder().encode(text));assert.equal(fromUtf8(utf8(text)),text);}
  for(const bad of [[0xc0,0x80],[0xe0,0x80],[0xed,0xa0,0x80],[0xff],[0xf4,0x90,0x80,0x80],[0xe2,0x82]])
    assert.throws(()=>fromUtf8(Uint8Array.from(bad)));
});

test('a sealed message opens only for its recipient, from its sender, untampered',()=>{
  const mac=newKeyPair(),phone=newKeyPair(),stranger=newKeyPair();
  const env=seal({t:'hello'},phone,mac.publicKey,{keep:'q'});
  assert.ok(isEnvelope(env));
  assert.equal(env.keep,'q');
  assert.deepEqual(open(env,mac,phone.publicKey),{t:'hello'});
  assert.equal(open(env,stranger,phone.publicKey),null,'wrong recipient');
  assert.equal(open(env,mac,stranger.publicKey),null,'wrong sender');
  assert.equal(open({...env,from:stranger.publicKey},mac,stranger.publicKey),null,'forged sender');
  const c=decode(env.c)!;c[5]^=1;
  assert.equal(open({...env,c:encode(c)},mac,phone.publicKey),null,'tampered');
  assert.notEqual(seal({t:'hello'},phone,mac.publicKey).n,env.n,'fresh nonce every time');
  assert.equal(open({...env,n:'short'},mac,phone.publicKey),null);
});

test('a pairing link carries the offer and refuses expired, cleartext or malformed ones',()=>{
  const k=()=>encode(new Uint8Array(32).fill(7));
  const offer:PairingOffer={v:1,relay:'wss://relay.example',room:'abcdefghijklmnopqrstuvwx',key:k(),mac:k(),code:k(),name:'Bryce’s MacBook',expires:Date.now()+60_000};
  assert.deepEqual(readOffer(offerLink(offer)),offer);
  assert.equal(readOffer(offerLink({...offer,expires:Date.now()-1})),null);
  assert.equal(readOffer(offerLink({...offer,relay:'ws://relay.example'})),null,'cleartext off this device');
  assert.ok(readOffer(offerLink({...offer,relay:'ws://127.0.0.1:8787'})),'loopback for development');
  assert.equal(readOffer(offerLink({...offer,relay:'https://relay.example'})),null);
  assert.equal(readOffer(offerLink({...offer,mac:'short'})),null);
  assert.equal(readOffer(offerLink({...offer,room:'../x'})),null);
  assert.equal(readOffer('https://evil.example/pair'),null);
});

test('phone messages are read strictly',()=>{
  assert.deepEqual(readPhoneMessage({t:'hello'}),{t:'hello'});
  assert.equal(readPhoneMessage({t:'action',id:'a',at:1,action:{kind:'delete',projectId:'p',channelId:'c'}}),null,'only send and stop');
  assert.equal(readPhoneMessage({t:'action',id:'a',at:1,action:{kind:'send',projectId:'p',channelId:'c',text:'   '}}),null,'empty text');
  assert.equal(readPhoneMessage({t:'action',id:'a',at:1,action:{kind:'send',projectId:'p',channelId:'c',text:'x'.repeat(20001)}}),null);
  assert.deepEqual(readPhoneMessage({t:'action',id:'a',at:1,action:{kind:'stop',projectId:'p',channelId:'c',extra:1}}),{t:'action',id:'a',at:1,action:{kind:'stop',projectId:'p',channelId:'c'}},'extra fields dropped');
  assert.equal(readPhoneMessage({t:'run',command:'rm -rf /'}),null);
  assert.equal(readPhoneMessage(null),null);
});

test('mac messages are read strictly, and bad rows are dropped not trusted',()=>{
  const home={at:1,mac:'Mac',projects:[{id:'p',name:'dervo'}],rows:[{id:'c',projectId:'p',project:'dervo',name:'A',bucket:'working',line:'x',step:null,unread:0,at:1,question:null,stoppable:true},{id:'d',projectId:'p',bucket:'exploding'}]};
  const read=readMacMessage({t:'snapshot',home});
  assert.equal(read?.t,'snapshot');
  assert.equal(read?.t==='snapshot'&&read.home.rows.length,1);
  assert.equal(read?.t==='snapshot'&&read.home.awake,undefined,'a Mac that can’t say leaves it out');
  const awake=readMacMessage({t:'snapshot',home:{...home,awake:{enabled:true,holding:true,extra:1}}});
  assert.deepEqual(awake?.t==='snapshot'&&awake.home.awake,{enabled:true,holding:true},'whether the Mac is staying awake');
  const odd=readMacMessage({t:'snapshot',home:{...home,awake:{enabled:'yes',holding:true}}});
  assert.equal(odd?.t==='snapshot'&&odd.home.awake,undefined,'a malformed one is dropped, not trusted');
  assert.equal(readMacMessage({t:'ack',id:'a',state:'maybe',detail:''}),null);
});

test('buckets come from the host records, with lost sight beating everything',()=>{
  assert.equal(bucketOf({running:true},true),'unknown');
  assert.equal(bucketOf({running:true,lost:true},false),'unknown');
  assert.equal(bucketOf({running:true,pending_question:'Which one?'},false),'needs','a question waits on you even while it works');
  assert.equal(bucketOf({status:'running'},false),'working');
  assert.equal(bucketOf({unread:2},false),'finished');
  assert.equal(bucketOf(null,false),'idle');
  const home=buildSnapshot({mac:'Mac',now:5,projects:[{id:'p',name:null,cwd:'/Users/b/dervo/app'}],lostRuns:[{channelId:'lost',projectId:'p'}],conversations:{p:[
    {id:'idle',name:'Idle',project_id:'p',activity:{at:9}},
    {id:'run',name:'Run',project_id:'p',activity:{running:true,step:'Running the tests',at:3}},
    {id:'ask',name:'Ask',project_id:'p',activity:{status:'needs',why:'Approve the plan',at:1}},
    {id:'lost',name:'Lost',project_id:'p',activity:{running:true}},
  ]}});
  assert.deepEqual(home.rows.map(r=>[r.id,r.bucket,r.line]),[['ask','needs','Approve the plan'],['lost','unknown','Dervo lost sight of this run'],['run','working','Running the tests'],['idle','idle','']]);
  assert.equal(home.projects[0].name,'app');
  assert.equal(home.rows.find(r=>r.id==='run')?.stoppable,true);
  assert.equal(home.rows.find(r=>r.id==='lost')?.stoppable,false,'no Stop for a run Dervo can\'t see');
});

test('a thread reads your messages and the agents\' replies, oldest first, without plumbing',()=>{
  const thread=threadOf({projectId:'p',channelId:'c',name:'T',now:1,you:'local-user',
    history:[{id:'h1',content:'hi',pubkey:'local-user',created_at:10},{id:'h2',content:'hello',pubkey:'provider',created_at:11}],
    tasks:[{id:'t',state:'completed',messages:[{role:'user',text:'fix it',created_at:12},{role:'tool',text:'{...}',created_at:13},{role:'assistant',text:'Fixed.',created_at:14}]}]});
  assert.deepEqual(thread.messages.map(m=>[m.from,m.text]),[['you','hi'],['agent','hello'],['you','fix it'],['agent','Fixed.']]);
});

test('relay frames are read strictly',()=>{
  const key=encode(new Uint8Array(32));
  assert.ok(readToRelay(JSON.stringify({t:'auth',role:'mac',pk:key,key,proof:'abc'})));
  assert.equal(readToRelay(JSON.stringify({t:'auth',role:'mac',pk:key,key})),null,'no proof, no sign-in');
  assert.equal(readToRelay(JSON.stringify({t:'auth',role:'admin',pk:key,key})),null);
  assert.equal(readToRelay(JSON.stringify({t:'push',token:'https://evil'})),null);
  assert.ok(readToRelay(JSON.stringify({t:'push',token:'ExponentPushToken[abcdefghijklmn]'})));
  assert.equal(readToRelay('{'),null);
});

test('state spine records map to the same buckets, and quiet or stopped runs say so',async()=>{
  const {buildSnapshotFromStates}=await import('./home.ts');
  const home=buildSnapshotFromStates({mac:'Mac',now:1,projects:[{id:'p',name:'dervo app'}],threads:[
    {id:'a',projectId:'p',name:'Ask',state:'needs_you',why:'Pick one',at:5},
    {id:'q',projectId:'p',name:'Quiet',state:'quiet',step:'Running tests',at:4},
    {id:'s',projectId:'p',name:'Stopped',state:'stopped',at:3},
    {id:'f',projectId:'p',name:'Done',state:'finished',unread:1,summary:'44 tests pass',at:2},
    {id:'r',projectId:'p',name:'Read',state:'finished',unread:0,summary:'Old result',at:1},
    {id:'x',projectId:'p',name:'Archived',state:'working',archived:true},
    {id:'g',projectId:'p',state:'idle',gone:true},
    {id:'o',projectId:'other',name:'Other project',state:'working'},
  ]});
  assert.deepEqual(home.rows.map(r=>[r.id,r.bucket,r.line]),[['a','needs','Pick one'],['q','working','No word from the agent for over a minute'],['f','finished','44 tests pass'],['s','idle','Stopped. Edits kept.'],['r','idle','Old result']]);
});

test('Stop follows the spine\'s running flag when the record has it',async()=>{
  const {rowOfState}=await import('./home.ts');
  assert.equal(rowOfState({id:'a',projectId:'p',state:'needs_you',running:true,pending_question:'Which one?'},'x').stoppable,true);
  assert.equal(rowOfState({id:'b',projectId:'p',state:'needs_you',running:false,pending_question:'Merge it?'},'x').stoppable,false);
  assert.equal(rowOfState({id:'c',projectId:'p',state:'working'},'x').stoppable,true);
});

test('Home\'s chat reads your messages and Dervo\'s replies, with where each job went',async()=>{
  const {homeChatOf}=await import('./home.ts');
  const {readAction}=await import('./messages.ts');
  const chat=homeChatOf({projectId:'p',name:'dervo',now:1,entries:[
    {id:'1',kind:'you',text:'Fix the export',created_at:1},
    {id:'2',kind:'reply',state:'ready',text:'Starting it.',parts:[{name:'Export fix',channel_id:'c',state:'sent'},{name:'Skipped',state:'offered'}],created_at:2},
    {id:'3',kind:'you',text:'And the pricing copy',created_at:3},
    {id:'4',kind:'reply',state:'thinking',created_at:4},
    {id:'5',kind:'arrival',text:'ignored'},
  ]});
  assert.equal(chat.channelId,'home');
  assert.deepEqual(chat.messages.map(m=>[m.from,m.text,!!m.pending]),[
    ['you','Fix the export',false],['dervo','Starting it.\n\nSent to Export fix.',false],['you','And the pricing copy',false],['dervo','Thinking…',true]]);
  assert.ok(readAction({kind:'home',projectId:'p',channelId:'home',text:'hi'}));
  assert.equal(readAction({kind:'home',projectId:'p',channelId:'c1',text:'hi'}),null,'Home only');
  assert.equal(readAction({kind:'home',projectId:'p',channelId:'home',text:'x'.repeat(8001)}),null);
});

test('a long thread or a big Home fits one sealed message, newest kept',async()=>{
  const {fitThread,fitSnapshot}=await import('./home.ts');
  const {seal,newKeyPair}=await import('./crypto.ts');
  const messages=Array.from({length:60},(_,i)=>({id:String(i),from:'agent' as const,text:'y'.repeat(5000)+i,at:i}));
  const thread=fitThread({projectId:'p',channelId:'c',name:'Long',messages,at:1});
  assert.equal(thread.earlier,true);
  assert.equal(thread.messages.at(-1)!.id,'59');
  const a=newKeyPair(),b=newKeyPair();
  assert.doesNotThrow(()=>seal({t:'thread',thread},a,b.publicKey));
  const rows=Array.from({length:600},(_,i)=>({id:'r'+i,projectId:'p',project:'p',name:'Thread '+i,bucket:'idle' as const,line:'z'.repeat(400),step:null,unread:0,at:i,question:null,stoppable:false,quiet:false}));
  const home=fitSnapshot({at:1,mac:'Mac',projects:[],rows});
  assert.ok((home.hidden??0)>0);
  assert.equal(home.rows.length+home.hidden!,600);
  assert.doesNotThrow(()=>seal({t:'snapshot',home},a,b.publicKey));
});

test('one message too long for a sealed message keeps its end instead of showing nothing',async()=>{
  const {fitThread}=await import('./home.ts');
  const thread=fitThread({projectId:'p',channelId:'c',name:'Huge',at:1,messages:[{id:'a',from:'agent',text:'old',at:1},{id:'b',from:'agent',text:'q'.repeat(400*1024)+'THE END',at:2}]});
  assert.equal(thread.messages.length,1);
  assert.equal(thread.messages[0].id,'b');
  assert.ok(thread.messages[0].text.endsWith('THE END'));
  assert.ok(new TextEncoder().encode(JSON.stringify(thread)).length<=200*1024);
});

test('pairing digits match on both sides and change with the Mac, the phone and the code',async()=>{
  const {pairingDigits,newKeyPair,randomToken}=await import('./crypto.ts');
  const mac=newKeyPair().publicKey,phone=newKeyPair().publicKey,code=randomToken();
  const digits=pairingDigits(mac,phone,code);
  assert.match(digits,/^\d{6}$/);
  assert.equal(pairingDigits(mac,phone,code),digits);
  const others=[pairingDigits(newKeyPair().publicKey,phone,code),pairingDigits(mac,newKeyPair().publicKey,code),pairingDigits(mac,phone,randomToken())];
  assert.ok(others.filter(d=>d!==digits).length>=2,'binding to each input');
});


test('a Stop only stops runs that had started when it was tapped',()=>{
  const at=1_800_000_000;
  const tasks=[
    {id:'a',state:'working',messages:[{role:'user',text:'go',created_at:at-60}]},
    {id:'b',state:'working',messages:[{role:'user',text:'next',created_at:at+30}]},
    {id:'c',state:'working'},
    {id:'d',state:'stopped',messages:[{role:'user',text:'old',created_at:at-600}]},
    // Finished an hour ago, then a follow-up started a new run after the tap.
    {id:'e',state:'working',messages:[{role:'user',text:'first',created_at:at-3600},{role:'agent',text:'done',created_at:at-3500},{role:'user',text:'again',created_at:at+30}]},
  ];
  assert.deepEqual(stopTargets(tasks,at*1000),['a','c']);
  assert.deepEqual(stopTargets(tasks),['a','b','c','e']);
});
