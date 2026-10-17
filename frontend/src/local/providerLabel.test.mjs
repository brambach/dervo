import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {providerAuthor} from './providerLabel.ts';
test('provider rows are labelled from the bound provider with an honest unknown fallback',()=>{
 assert.equal(providerAuthor('codex'),'Codex');
 assert.equal(providerAuthor('claude'),'Claude Code');
 assert.equal(providerAuthor(undefined),'Provider (unknown)');
 assert.equal(providerAuthor(null),'Provider (unknown)');
 assert.equal(providerAuthor(''),'Provider (unknown)');
 assert.equal(providerAuthor('other'),'Provider (other)');
});
test('the timeline no longer hardcodes Codex as the provider author',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.doesNotMatch(source,/'provider'\?'Codex'/);
 assert.match(source,/providerAuthor\(binding\.data\?\.provider\)/);
 assert.match(source,/timeline\(rows:LocalEvent\[\],providerLabel:string,avatars:AvatarSources\)/);
});
