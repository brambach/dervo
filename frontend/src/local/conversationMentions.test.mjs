import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
dom.window.Element.prototype.scrollIntoView=function(){};
const React=await import('react');
const {render,screen,cleanup}=await import('@testing-library/react');
const {mentionableConversations,conversationMentionNote,mentionSignature}=await import('./ConversationMentions.tsx');
const {selectChannelSuggestions}=await import('@/features/messages/lib/useChannelLinks');
const {detectPrefixQuery}=await import('@/shared/lib/detectPrefixQuery');
const {ChannelAutocomplete}=await import('@/features/messages/ui/ChannelAutocomplete');
after(()=>dom.window.close());
afterEach(cleanup);
const rows=[
 {id:'here',name:'Beta four polish',visibility:'shared',project_id:'p',profile_id:'a',model:'m'},
 {id:'plan',name:'Move my entire workspace from Claude into here',visibility:'shared',project_id:'p',profile_id:'a',model:'m',activity:{status:'running',running:true,step:'Reading the codebase',unread:0}},
 {id:'mine',name:'Private notes',visibility:'private',project_id:'p',profile_id:'a',model:'m'},
 {id:'ask',name:'Release checks',visibility:'shared',project_id:'p',profile_id:'a',model:'m',activity:{status:'needs',running:false,step:null,unread:1}},
];

test('the picker offers this project’s other conversations, and never the one you are in',()=>{
 const offered=mentionableConversations(rows,'here');
 assert.deepEqual(offered.map(channel=>channel.name),['Move my entire workspace from Claude into here','Private notes','Release checks']);
 for(const channel of offered){
  assert.equal(channel.channelType,'stream','a DM type would drop the row from the suggestion list');
  assert.equal(channel.archivedAt,null,'an archived channel is never suggestable');
 }
 assert.equal(offered.find(channel=>channel.id==='mine').visibility,'private');
 assert.deepEqual(mentionableConversations([{...rows[0],id:'blank',name:'  '}],'here'),[],'a conversation with no name has nothing to type');
});

test('each suggestion says what that conversation is doing, and a quiet shared one says nothing',()=>{
 assert.equal(conversationMentionNote(rows[1]),'Working now');
 assert.equal(conversationMentionNote(rows[3]),'Needs you');
 assert.equal(conversationMentionNote(rows[2]),'Private');
 assert.equal(conversationMentionNote(rows[0]),null);
 assert.equal(conversationMentionNote(undefined),null);
});

test('a task moving on does not rebuild the list under someone who is typing',()=>{
 const moved=rows.map(row=>row.activity?{...row,activity:{...row.activity,step:'Running the tests',unread:3}}:row);
 assert.equal(mentionSignature(moved,'here'),mentionSignature(rows,'here'));
 assert.notEqual(mentionSignature([...rows,{id:'new',name:'Another chat',visibility:'shared',project_id:'p',profile_id:'a',model:'m'}],'here'),mentionSignature(rows,'here'));
 assert.notEqual(mentionSignature(rows,'plan'),mentionSignature(rows,'here'),'the conversation you are in changes what is offered');
});

test('typing part of a long name keeps the query open and the matching conversation is offered',()=>{
 const offered=mentionableConversations(rows,'here');
 const known=offered.map(channel=>channel.name.toLowerCase());
 const typed='look in #move my entire workspace';
 const query=detectPrefixQuery('#',typed,typed.length,known);
 assert.equal(query.query,'move my entire workspace','a name with spaces keeps matching as you type');
 assert.equal(typed.slice(query.startIndex,query.startIndex+1),'#');
 assert.deepEqual(selectChannelSuggestions(offered,query.query).map(row=>row.id),['plan']);
 assert.deepEqual(selectChannelSuggestions(offered,'notes').map(row=>row.id),['mine'],'a private conversation of yours is offered too');
});

test('a Dervo suggestion shows what the conversation is doing instead of a channel type',()=>{
 const offered=mentionableConversations(rows,'here');
 const suggestions=selectChannelSuggestions(offered,'');
 const note=suggestion=>conversationMentionNote(rows.find(row=>row.id===suggestion.id));
 render(React.createElement(ChannelAutocomplete,{suggestions,selectedIndex:0,composerOwnsFocus:true,note,onSelect(){}}));
 assert.ok(screen.getByText('#Move my entire workspace from Claude into here'));
 assert.ok(screen.getByText('Working now'));
 assert.ok(screen.getByText('Private'));
 assert.equal(screen.queryByText('stream'),null,'Dervo conversations are not Buzz streams');
 cleanup();
 render(React.createElement(ChannelAutocomplete,{suggestions,selectedIndex:0,composerOwnsFocus:true,onSelect(){}}));
 assert.equal(screen.queryAllByText('stream').length,3,'without a note the shared composer keeps its badge');
});

test('the conversation composer is wired to the picker',()=>{
 const app=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(app,/<ConversationMentions conversations=\{conversations\.data\?\?\[\]\} currentId=\{channelId\}><div ref=\{composerRoot\}[\s\S]*?><LocalImageComposer onStop=\{running\?\(\)=>void stopHere\(\):undefined\} channelSuggestionNote=\{conversationMentionNote\}/);
 const composer=readFileSync(new URL('../features/messages/ui/MessageComposer.tsx',import.meta.url),'utf8');
 assert.match(composer,/channelSuggestionNote=\{channelSuggestionNote\}/,'the composer passes the note down to its # suggestions');
});
