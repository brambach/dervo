import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {messageAvatar,providerMark,dervoMark} from './messageAvatars.ts';

const photo='data:image/png;base64,iVBORw0KGgo=';

test('your own rows carry the account photo when one is loaded, and nothing otherwise',()=>{
 assert.equal(messageAvatar('local-user',{photo,provider:'claude'}),photo);
 assert.equal(messageAvatar('local-user',{provider:'claude'}),null,'no photo means the initials fallback, as before');
});

test('provider, helper and Dervo rows get marks that are inline SVG data URLs, one per provider',()=>{
 const claude=messageAvatar('provider',{photo,provider:'claude'});
 const codex=messageAvatar('provider',{photo,provider:'codex'});
 assert.equal(claude,providerMark('claude'));assert.equal(codex,providerMark('codex'));
 assert.notEqual(claude,codex);
 for(const url of [claude,codex,providerMark('unknown'),dervoMark()]){
  assert.ok(url.startsWith('data:image/svg+xml;utf8,'),url.slice(0,40));
  assert.ok(decodeURIComponent(url).includes('<svg xmlns="http://www.w3.org/2000/svg"'));
 }
 assert.equal(messageAvatar('helper:codex',{photo,provider:'claude'}),codex,'a helper row shows its own provider, not the conversation\'s');
 assert.equal(messageAvatar('dervo-coordinator',{photo,provider:'claude'}),dervoMark());
 assert.notEqual(messageAvatar('provider',{photo,provider:'claude'}),photo,'a provider row never shows your photo');
});

test('the marks are Dervo\'s own drawings, not a trademarked logo asset',()=>{
 const source=readFileSync(new URL('./messageAvatars.ts',import.meta.url),'utf8');
 assert.doesNotMatch(source,/\.(png|jpg|jpeg|webp)/i,'no bundled raster logo');
 assert.doesNotMatch(source,/anthropic|openai/i,'no vendor asset name');
});

test('ConnectedApp builds every timeline row through messageAvatar with the account photo and the bound provider',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/avatarUrl:messageAvatar\(r\.pubkey,avatars\)/);
 assert.match(source,/\{photo:photoImage\(accountData\),provider:binding\.data\?\.provider\}/);
});

// dd #23: an open menu marked the inherited row's avatar cell aria-hidden and a gutter rule then moved the Claude mark.
// Dervo's own row (9 Oct) has no avatar menu and no time gutter: the mark is a plain picture and a follow-on row drops
// its byline, so neither can drift.
test('the thread row\'s mark is a plain picture beside the name, and a follow-on row has no byline to move',()=>{
 const row=readFileSync(new URL('./DvMessageRow.tsx',import.meta.url),'utf8');
 const css=readFileSync(new URL('./DervoComponents.css',import.meta.url),'utf8');
 assert.match(row,/<img className="dv-msg-mark" data-testid="message-avatar"/);
 assert.doesNotMatch(row,/<button[^>]*message-avatar|UserProfilePopover/,'nothing to open on the mark');
 assert.match(row,/const showHead=!own&&!continuation;/,'a follow-on row and your own row have no byline');
 assert.doesNotMatch(css,/>\s*\[aria-hidden="true"\]:first-child[^{]*\{\s*position:\s*absolute/,'the old gutter rule is gone');
});
