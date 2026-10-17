import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','MouseEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
const React=await import('react');
const {render,fireEvent,cleanup,screen}=await import('@testing-library/react');
const {default:remarkChannelLinks,plausibleUnknownName}=await import('../shared/lib/remarkChannelLinks.ts');
const {MarkdownChannelReference}=await import('../shared/ui/markdown/ChannelDeepLink.tsx');
const {MarkdownRuntimeContext}=await import('../shared/ui/markdown/runtimeContext.ts');
const {mentionedConversations,filingSuggestion,FilingPrompt,dismissKey}=await import('./ConversationFiling.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();localStorage.clear();});

/** Run the plugin over one paragraph and return what it made: [kind, text] for each piece. */
function pieces(text,options){
 const tree={type:'root',children:[{type:'paragraph',children:[{type:'text',value:text}]}]};
 remarkChannelLinks(options)(tree);
 return tree.children[0].children.map(node=>[node.type==='channel-link'?'pill':'text',node.value]).filter(([,value])=>value);
}

test('in Dervo, known conversations match longest first and a plausible unknown name is marked; nothing else is',()=>{
 const names=['api','api tests','search index'];
 const found=pieces('Check #api tests and #search index, then #api. Not #nothing-here? Colour #fff, #L42, issue #123, C# and a.b/#frag.',{channelNames:names,matchUnknown:true})
  .filter(([kind])=>kind==='pill').map(([,value])=>value);
 assert.deepEqual(found,['#api tests','#search index','#api','#nothing-here']);
 assert.equal(plausibleUnknownName('fff'),false,'a colour');
 assert.equal(plausibleUnknownName('cafe'),false,'a four-digit hex colour too');
 assert.equal(plausibleUnknownName('L42'),false,'a line reference');
 assert.equal(plausibleUnknownName('engineering'),true);
 // Buzz's own mode is unchanged: known names only, no unknown marking.
 assert.deepEqual(pieces('#api tests and #nothing-here',{channelNames:names}).filter(([k])=>k==='pill').map(([,v])=>v),['#api tests']);
});

const channel=(id,name)=>({id,name,channelType:'stream',visibility:'open',description:'',topic:null,purpose:null,memberCount:1,memberPubkeys:[],
 lastMessageAt:null,archivedAt:null,participants:[],participantPubkeys:[],isMember:true,ttlSeconds:null,ttlDeadline:null});
function pill(name,runtime){
 return render(React.createElement(MarkdownRuntimeContext.Provider,{value:{channels:[channel('c6','search index')],onOpenChannel(){},onOpenEntityLink(){},onOpenMessageLink(){},relayOrigin:null,localChannels:true,...runtime}},
  React.createElement(MarkdownChannelReference,{interactive:true},'#'+name)));
}

test('a known #name is a neutral pill that opens the conversation in place; an unknown one says there is none',()=>{
 const opened=[];
 pill('search index',{onOpenChannel:id=>opened.push(id)});
 const button=screen.getByRole('button',{name:'Open conversation search index'});
 assert.equal(button.className,'dervo-mention-pill');
 assert.equal(button.textContent,'#search index');
 fireEvent.click(button);
 assert.deepEqual(opened,['c6']);
 cleanup();
 pill('nothing-here');
 const unknown=document.querySelector('.dervo-mention-pill');
 assert.equal(unknown.tagName,'SPAN');
 assert.ok(unknown.hasAttribute('data-unknown'));
 assert.equal(unknown.getAttribute('title'),'No conversation called #nothing-here in this project');
});

const conversation=(id,name,visibility='shared')=>({id,name,visibility,project_id:'p',profile_id:'a',model:'m'});
const people=[conversation('me','Room composer'),conversation('eng','engineering'),conversation('api','api'),conversation('apit','api tests'),conversation('mine','scratch','private')];
const ideas={ideas:[{id:'i-eng',name:'Engineering',conversations:[{id:'eng',name:'engineering'},{id:'apit',name:'api tests'}]},
 {id:'i-api',name:'API',conversations:[{id:'api',name:'api'}]}],archived:[],unassigned:[],states:[],limits:{name:60,summary:2000},detail:''};

test('after a send that names a conversation in an idea, this conversation is offered that idea, and only then',()=>{
 assert.deepEqual(mentionedConversations('look in #api tests, then #engineering',people,'me').map(c=>c.id).sort(),['apit','eng'],'#api is not also matched inside #api tests');
 const offer=(text,over={})=>filingSuggestion({text,channelId:'me',visibility:'shared',conversations:people,listing:ideas,isDismissed:()=>false,...over});
 assert.deepEqual(offer('look in #engineering, will this interfere?'),{ideaId:'i-eng',ideaName:'Engineering'});
 assert.deepEqual(offer('compare #engineering with #api tests'),{ideaId:'i-eng',ideaName:'Engineering'},'two conversations, one idea');
 assert.equal(offer('compare #engineering with #api'),null,'more than one idea is mentioned');
 assert.equal(offer('no mention here'),null);
 assert.equal(offer('#scratch please'),null,'a conversation in no idea offers nothing');
 const inIt={...ideas,ideas:[{...ideas.ideas[0],conversations:[...ideas.ideas[0].conversations,{id:'me',name:'Room composer'}]},ideas.ideas[1]]};
 assert.equal(offer('look in #engineering',{listing:inIt}),null,'already there');
 assert.equal(offer('look in #engineering',{visibility:'private'}),null,'never in a private conversation');
 assert.equal(offer('look in #engineering',{isDismissed:(c,i)=>c==='me'&&i==='i-eng'}),null,'not after Not now for that idea here');
 assert.equal(offer('look in #engineering',{listing:null}),null,'ideas not loaded yet');
 // The real Not now memory is per conversation and per idea.
 localStorage.setItem(dismissKey('me','i-eng'),'1');
 assert.equal(filingSuggestion({text:'#engineering',channelId:'me',visibility:'shared',conversations:people,listing:ideas}),null);
 assert.deepEqual(filingSuggestion({text:'#engineering',channelId:'other',visibility:'shared',conversations:[...people,conversation('other','Other')],listing:ideas}),{ideaId:'i-eng',ideaName:'Engineering'});
});

test('the offer is one quiet line with File and Not now',()=>{
 const calls=[];
 render(React.createElement(FilingPrompt,{suggestion:{ideaId:'i-eng',ideaName:'Engineering'},busy:false,onFile:()=>calls.push('file'),onNotNow:()=>calls.push('not now')}));
 assert.equal(screen.getByRole('group').textContent.startsWith('File this conversation under Engineering?'),true);
 fireEvent.click(screen.getByRole('button',{name:'File'}));
 fireEvent.click(screen.getByRole('button',{name:'Not now'}));
 assert.deepEqual(calls,['file','not now']);
});
