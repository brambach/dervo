import assert from 'node:assert/strict';
import {test} from 'node:test';
import {JSDOM} from 'jsdom';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {renderCachedMarkdown} from './nodeCache.ts';
const components={strong:({children})=>createElement('strong',{'data-dervo':'strong'},children),code:({children,className})=>createElement('code',{className},children),pre:({children})=>createElement('pre',{'data-dervo':'code'},children),a:({children,href})=>href?createElement('a',{href},children):createElement('span',null,children)};
const raw=(content,options={})=>renderToStaticMarkup(renderCachedMarkdown({content,components,variant:'stream-test',streamingCompatible:true,transient:true,...options}));
// Structure checks read through the per-word fade spans; the fade itself is checked on its own below.
const render=(content,options={})=>raw(content,options).replace(/<span data-sd-animate="true"[^>]*>([^<]*)<\/span>/g,'$1').replace(/ data-sd-animated="true"/g,'');
test('live reply words ease in one by one, and settled text renders without any fade (17 Sep: "creamy text flowing in")',()=>{
 const live=raw('The result is ready');
 assert.equal((live.match(/data-sd-animate="true"/g)??[]).length,4);
 assert.match(live,/--sd-duration:160ms/);  // the steady reply (#30, 97ac4c2): each word fades over 160 ms
 assert.doesNotMatch(raw('The result is ready',{transient:false}),/data-sd-animate/);
});
test('incomplete bold, inline code, fence, lists and links render without exposed control delimiters',()=>{
 assert.match(render('The result is **ready'),/<strong data-dervo="strong">ready<\/strong>/);
 assert.match(render('Open `src/search'),/<code>src\/search<\/code>/);
 assert.match(render('```ts\nconst value = 1;'),/<pre data-dervo="code">/);
 assert.match(render('- First item\n- Next'),/<li[^>]*>Next<\/li>/);
 const link=render('See [the result](https://example.test/res');
 assert.match(link,/<span>the result<\/span>/);assert.ok(!link.includes('href="https://example.test/res"'));
});
test('completed links retain the existing URL transform and raw HTML cannot execute',()=>{
 assert.match(render('[Result](https://example.test/result)',{transient:false}),/href="https:\/\/example.test\/result"/);
 for(const input of ['[bad](javascript:alert%281%29)','<script>alert(1)</script>','<img src=x onerror=alert(1)>']){
  const output=render(input);assert.ok(!output.includes('href="javascript:'));assert.ok(!output.includes('<script'));const dom=new JSDOM(output);assert.equal(dom.window.document.querySelector('[onerror]'),null);dom.window.close();
 }
});
test('message-level leading prefix appears once across multiple paragraphs',()=>{
 const output=render('First paragraph.\n\nSecond paragraph.\n\nThird paragraph.',{leadingInlineContent:true});
 assert.equal((output.match(/data-leading-inline-content/g)??[]).length,1);
});
